from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import requests
from bson import ObjectId
from bson.errors import InvalidId

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import WorkflowResult

logger = logging.getLogger(__name__)

_WORKFLOW_ID = "enrichment_retiring_jobs"
_CLOSED_AFTER_DAYS = 60


def _now_timestamp() -> dict:
    return {"seconds": int(time.time()), "nanos": 0}


def _seconds_60_days_ago() -> int:
    return int((datetime.now(timezone.utc) - timedelta(days=_CLOSED_AFTER_DAYS)).timestamp())


def _to_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def run_enrichment_retiring_jobs(
    database,
    config: CrawlerConfig,
    job_id: str,
) -> WorkflowResult:
    """Check a single job's source_url and retire it if necessary.

    Phase A — Mark closed: if the job is open, probe its source_url; set
    ``is_open=False`` and ``closed_at`` when the server responds with HTTP 404.

    Phase B — Remove expired: if the job is closed and ``closed_at`` is older
    than 60 days, delete the job document.
    """
    result = WorkflowResult()

    job_oid = _to_object_id(job_id)
    if job_oid is None:
        logger.warning("enrichment_retiring_jobs: invalid job_id %r, skipping", job_id)
        result.failed_count += 1
        return result

    jobs_collection = database["job-descriptions"]
    job = jobs_collection.find_one({"_id": job_oid})
    if job is None:
        logger.debug("enrichment_retiring_jobs: job %s not found, skipping", job_id)
        result.skipped_count += 1
        return result

    # ===== PHASE A: Mark job as closed when source_url returns 404 =====
    if job.get("is_open") is not False:
        source_url = job.get("source_url", "")
        session = requests.Session()
        session.headers.update({"User-Agent": config.user_agent})
        try:
            response = session.head(
                source_url,
                timeout=config.http_timeout_seconds,
                allow_redirects=True,
            )
            if response.status_code == 404:
                closed_at = _now_timestamp()
                jobs_collection.update_one(
                    {"_id": job_oid},
                    {"$set": {"is_open": False, "closed_at": closed_at}},
                )
                job["is_open"] = False
                job["closed_at"] = closed_at
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
            return result
        finally:
            session.close()

    # ===== PHASE B: Delete job if it has been closed for more than 60 days =====
    if job.get("is_open") is False:
        closed_at = job.get("closed_at") or {}
        closed_seconds = closed_at.get("seconds", 0) if isinstance(closed_at, dict) else 0
        if closed_seconds < _seconds_60_days_ago():
            jobs_collection.delete_one({"_id": job_oid})
            result.deleted_count += 1
            logger.debug("enrichment_retiring_jobs: deleted expired job %s", job_id)

    return result
