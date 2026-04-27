from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from google.protobuf.timestamp_pb2 import Timestamp
import redis

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.workflow_messages import crawl_progress_to_dict

logger = logging.getLogger(__name__)

_WORKFLOW_MESSAGES = {
    "queued": "Waiting for worker pickup",
    "crawler_ycombinator": "Collecting Y Combinator company candidates",
    "crawler_hackernews": "Collecting Hacker News company candidates",
    "enrichment_ats_enrichment": "Resolving ATS providers and slugs",
    "crawler_ats_job_extraction": "Fetching ATS job postings",
    "crawler_4dayweek": "Collecting 4dayweek jobs",
    "crawler_levelsfyi": "Collecting Levels.fyi jobs",
    "enrichment_retiring_jobs": "Retiring stale job postings",
    "finalizing": "Finalizing crawl run",
}


def utc_timestamp() -> Timestamp:
    now = datetime.now(timezone.utc)
    timestamp = Timestamp()
    timestamp.FromDatetime(now)
    return timestamp


def publish_progress(
    redis_client: redis.Redis | None,
    config: CrawlerConfig,
    *,
    run_id: str,
    identity_id: str,
    status: str,
    workflow: str,
    estimated_total: int,
    completed: int,
    started_at: Timestamp | None,
    finished_at: Timestamp | None = None,
    message: str | None = None,
    reason: str = "",
    workflow_id: str = "",
    workflow_run_id: str = "",
) -> None:
    if redis_client is None:
        return

    percent = 0
    if estimated_total > 0:
        percent = max(0, min(100, int((completed / estimated_total) * 100)))

    payload = common_pb2.CrawlProgress(
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        workflow_id=workflow_id,
        identity_id=identity_id,
        status=status,
        workflow=workflow,
        message=message or _WORKFLOW_MESSAGES.get(workflow, ""),
        estimated_total=estimated_total,
        completed=completed,
        percent=percent,
        reason=reason,
    )
    payload.updated_at.CopyFrom(utc_timestamp())
    if started_at is not None:
        payload.started_at.CopyFrom(started_at)
    if finished_at is not None:
        payload.finished_at.CopyFrom(finished_at)

    try:
        redis_client.publish(
            config.crawler_progress_channel_name,
            json.dumps(crawl_progress_to_dict(payload)),
        )
    except Exception as exc:
        logger.warning("failed to publish crawl progress for run %s: %s", run_id, exc)
