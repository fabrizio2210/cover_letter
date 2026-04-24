from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import WorkflowResult

logger = logging.getLogger(__name__)

_WORKFLOW_ID = "enrichment_retiring_jobs"
_CLOSED_AFTER_DAYS = 60


def _now_timestamp() -> dict:
    return {"seconds": int(time.time()), "nanos": 0}


def _seconds_60_days_ago() -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=_CLOSED_AFTER_DAYS)).timestamp())


def run_enrichment_retiring_jobs(
    database,
    config: CrawlerConfig,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> WorkflowResult:
    """Check all open jobs for 404 source URLs and remove jobs closed for over 60 days.

    Phase A — Mark closed: probe each open job's source_url; set ``is_open=False``
    and ``closed_at`` when the server responds with HTTP 404.

    Phase B — Remove expired: delete job documents where ``is_open=False`` and
    ``closed_at.seconds`` is older than 60 days.
    """
    jobs_collection = database["jobs"]
    result = WorkflowResult()

    # ===== PHASE A: Mark jobs as closed when source_url returns 404 =====
    open_jobs = list(
        jobs_collection.find(
            {"source_url": {"$exists": True, "$ne": ""}, "is_open": {"$ne": False}}
        )
    )

    total_open = len(open_jobs)
    checked = 0

    logger.info("enrichment_retiring_jobs: Phase A — checking %d open jobs for availability", total_open)

    if progress_callback:
        progress_callback(0, max(total_open, 1), f"Checking {total_open} open jobs for availability")

    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})

    try:
        for job in open_jobs:
            source_url = job.get("source_url", "")
            job_id = str(job.get("_id", ""))
            checked += 1

            try:
                response = session.head(
                    source_url,
                    timeout=config.http_timeout_seconds,
                    allow_redirects=True,
                )
                if response.status_code == 404:
                    jobs_collection.update_one(
                        {"_id": job["_id"]},
                        {"$set": {"is_open": False, "closed_at": _now_timestamp()}},
                    )
                    result.updated_count += 1
                    logger.debug(
                        "enrichment_retiring_jobs: marked job %s as closed (404 on %s)",
                        job_id,
                        source_url,
                    )
            except Exception as exc:
                logger.debug(
                    "enrichment_retiring_jobs: failed to check job %s url %s: %s",
                    job_id,
                    source_url,
                    exc,
                )
                result.failed_count += 1

            if progress_callback:
                progress_callback(
                    checked,
                    max(total_open, 1),
                    f"Checked {checked}/{total_open} jobs",
                )
    finally:
        session.close()

    logger.info(
        "enrichment_retiring_jobs: Phase A complete — %d jobs marked closed out of %d checked",
        result.updated_count,
        checked,
    )

    # ===== PHASE B: Remove jobs that have been closed for more than 60 days =====
    cutoff_seconds = _seconds_60_days_ago()

    delete_result = jobs_collection.delete_many(
        {
            "is_open": False,
            "closed_at.seconds": {"$lt": cutoff_seconds},
        }
    )
    result.deleted_count = delete_result.deleted_count

    logger.info(
        "enrichment_retiring_jobs: Phase B complete — %d expired jobs deleted",
        result.deleted_count,
    )

    return result
