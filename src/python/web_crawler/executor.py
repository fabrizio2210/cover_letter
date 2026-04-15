"""
Parallel execution infrastructure for web crawler workflows.

Provides thread-safe session management and worker functions for concurrent
HTTP requests using ThreadPoolExecutor.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

import requests
from bson import ObjectId

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.sources.ats_detector import (
    ATSRequestFailure,
    detect_ats_provider,
)
from src.python.web_crawler.sources.ats_slug_resolver import (
    resolve_direct_slug,
    resolve_slug_via_search_dorking,
)

logger = logging.getLogger(__name__)


@dataclass
class ATSWorkerTask:
    """Input data for a single ATS detection worker task."""

    company_id: str
    company_object_id: ObjectId
    company_name: str
    candidate_urls: list[str]
    company_index: int
    total_companies: int


@dataclass
class ATSWorkerResult:
    """Output result from a single ATS detection worker task."""

    company_id: str
    company_object_id: ObjectId
    company_name: str
    company_index: int
    success: bool
    provider: str | None = None
    slug: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    error_url: str | None = None


class ThreadSafeSessionPool:
    """
    Manages thread-local requests.Session instances.

    Each thread gets its own session to avoid synchronization overhead and
    allow independent connection pooling per worker thread.
    """

    def __init__(self, user_agent: str):
        """
        Initialize the session pool.

        Args:
            user_agent: User-Agent string for HTTP requests.
        """
        self._thread_local = threading.local()
        self._user_agent = user_agent
        self._lock = threading.Lock()
        self._session_count = 0

    def get_session(self) -> requests.Session:
        """
        Get the thread-local session, creating one if needed.

        Returns:
            A requests.Session instance for the current thread.
        """
        if not hasattr(self._thread_local, "session"):
            with self._lock:
                self._session_count += 1
                session_id = self._session_count

            session = requests.Session()
            session.headers.update({"User-Agent": self._user_agent})
            self._thread_local.session = session
            logger.debug("Created session %d for thread %s", session_id, threading.current_thread().name)

        return self._thread_local.session

    def close_session(self) -> None:
        """Close the thread-local session if it exists."""
        if hasattr(self._thread_local, "session"):
            self._thread_local.session.close()
            logger.debug("Closed session for thread %s", threading.current_thread().name)

    def close_all(self) -> None:
        """Close all sessions (called from main thread only)."""
        # Thread-local data is not accessible from other threads, so we can't
        # explicitly close all sessions. Rely on garbage collection.
        logger.debug("ThreadSafeSessionPool shutdown complete (GC will clean up %d worker sessions)", self._session_count)


def _detect_ats_worker(
    task: ATSWorkerTask,
    config: CrawlerConfig,
    session_pool: ThreadSafeSessionPool,
) -> ATSWorkerResult:
    """
    Worker function for parallel ATS detection.

    Runs in a thread pool worker and handles ATS provider detection +
    slug resolution for a single company.

    Args:
        task: Input task data for the company.
        config: Crawler configuration.
        session_pool: Thread-safe session provider.

    Returns:
        ATSWorkerResult with detection outcome (success/failure) and details.
    """
    try:
        session = session_pool.get_session()

        logger.debug(
            "Worker: ATS detection for company %d/%d: %s",
            task.company_index,
            task.total_companies,
            task.company_name or task.company_id,
        )

        # Detect ATS provider from candidate URLs
        detection = detect_ats_provider(task.candidate_urls, config, session=session)
        if detection is None:
            logger.debug("Worker: No ATS provider detected for company %s", task.company_id)
            return ATSWorkerResult(
                company_id=task.company_id,
                company_object_id=task.company_object_id,
                company_name=task.company_name,
                company_index=task.company_index,
                success=False,
                error_type="no_ats_provider",
                error_message="No ATS provider detected",
            )

        logger.debug(
            "Worker: ATS provider detected for company %s: provider=%s board_url=%s",
            task.company_id,
            detection.provider,
            detection.board_url,
        )

        # Attempt direct slug resolution
        slug = resolve_direct_slug(detection.provider, config, board_url=detection.board_url, session=session)
        logger.debug(
            "Worker: Direct slug resolution for company %s (provider=%s): %s",
            task.company_id,
            detection.provider,
            slug or "not found",
        )

        # If direct resolution failed, try search/dorking (without company name in this context)
        # Note: In the actual workflow2, SERP fallback uses company_name and checks prior attempts.
        # For now, we only do direct slug resolution in the worker. SERP fallback logic stays in main thread.
        if slug is None:
            logger.debug(
                "Worker: Direct slug resolution failed for company %s provider=%s. "
                "SERP fallback will be handled by main thread.",
                task.company_id,
                detection.provider,
            )
            return ATSWorkerResult(
                company_id=task.company_id,
                company_object_id=task.company_object_id,
                company_name=task.company_name,
                company_index=task.company_index,
                success=False,
                provider=detection.provider,
                error_type="slug_not_resolved_direct",
                error_message=f"Direct slug resolution failed for provider {detection.provider}",
            )

        logger.debug("Worker: ATS detection complete for company %s: provider=%s slug=%s", task.company_id, detection.provider, slug)
        return ATSWorkerResult(
            company_id=task.company_id,
            company_object_id=task.company_object_id,
            company_name=task.company_name,
            company_index=task.company_index,
            success=True,
            provider=detection.provider,
            slug=slug,
        )

    except ATSRequestFailure as exc:
        logger.debug(
            "Worker: Terminal ATS failure for company %s: failure_type=%s url=%s message=%s",
            task.company_id,
            exc.failure_type,
            exc.url,
            exc.message,
        )
        return ATSWorkerResult(
            company_id=task.company_id,
            company_object_id=task.company_object_id,
            company_name=task.company_name,
            company_index=task.company_index,
            success=False,
            error_type=f"ats_request_failure:{exc.failure_type}",
            error_message=str(exc),
            error_url=exc.url,
        )

    except Exception as exc:
        logger.exception(
            "Worker: Unexpected error during ATS detection for company %s",
            task.company_id,
        )
        return ATSWorkerResult(
            company_id=task.company_id,
            company_object_id=task.company_object_id,
            company_name=task.company_name,
            company_index=task.company_index,
            success=False,
            error_type="unexpected_error",
            error_message=str(exc),
        )
