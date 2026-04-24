from __future__ import annotations

import logging

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.db import get_database

logger = logging.getLogger(__name__)

STATS_COLLECTION = "stats"
WORKFLOW_COUNTERS_DOC_ID = "crawler_workflow_cumulative_jobs"
WORKFLOW_COUNTERS_FIELD = "discovered_jobs_by_workflow"


def increment_discovered_jobs_counter(
    config: CrawlerConfig,
    *,
    workflow_id: str,
    delta: int,
) -> None:
    """Increment the cumulative discovered-jobs counter for one workflow.

    The counter is persisted under a single stats document so values survive
    API restarts and can be displayed as all-time cumulative totals.
    """
    if not workflow_id or delta <= 0:
        return

    try:
        database = get_database(config)
        stats_collection = database[STATS_COLLECTION]
        stats_collection.update_one(
            {"_id": WORKFLOW_COUNTERS_DOC_ID},
            {
                "$inc": {f"{WORKFLOW_COUNTERS_FIELD}.{workflow_id}": int(delta)},
            },
            upsert=True,
        )
    except Exception as exc:
        logger.warning(
            "failed to increment cumulative discovered jobs counter workflow=%s delta=%s: %s",
            workflow_id,
            delta,
            exc,
        )
