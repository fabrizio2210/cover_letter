
from __future__ import annotations

import argparse
import logging
import time
import uuid
from typing import cast

import redis
from bson import ObjectId

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.db import get_database, get_user_database
from src.python.web_crawler.models import WorkflowResult
from src.python.web_crawler.progress import publish_progress, utc_timestamp
from src.python.web_crawler.enrichment_ats_enrichment.workflow import run_enrichment_ats_enrichment
from src.python.web_crawler.workflow_messages import (
    parse_company_discovery_event,
    workflow_dispatch_to_json,
)

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_WORKFLOW_ID = "enrichment_ats_enrichment"


def _new_workflow_run_id() -> str:
    return uuid.uuid4().hex


def _connect_redis(config: CrawlerConfig) -> redis.Redis:
    client = redis.Redis(
        host=config.redis_host,
        port=config.redis_port,
        socket_connect_timeout=5,
        decode_responses=True,
    )
    client.ping()
    return client


def _dispatch_ats_job_extraction(
    redis_client: redis.Redis,
    config: CrawlerConfig,
    *,
    run_id: str,
    identity_id: str,
    user_id: str,
    company_id: str,
    ats_provider: str,
    ats_slug: str,
    producer_workflow_run_id: str,
) -> None:
    """Dispatch a WorkflowDispatchMessage to the ATS extraction queue."""
    extraction_workflow_run_id = _new_workflow_run_id()
    dispatch = common_pb2.WorkflowDispatchMessage(
        run_id=run_id,
        workflow_run_id=extraction_workflow_run_id,
        workflow_id="crawler_ats_job_extraction",
        identity_id=identity_id,
        user_id=user_id,
        company_id=company_id,
        ats_provider=ats_provider,
        ats_slug=ats_slug,
        trigger_kind="company_enriched",
        attempt=1,
    )
    dispatch.dispatched_at.CopyFrom(utc_timestamp())
    redis_client.rpush(
        config.crawler_ats_job_extraction_queue_name,
        workflow_dispatch_to_json(dispatch),
    )
    logger.debug(
        "dispatched crawler_ats_job_extraction run_id=%s workflow_run_id=%s company=%s",
        run_id,
        extraction_workflow_run_id,
        company_id,
    )


def _run_enrichment_for_event(
    redis_client: redis.Redis,
    config: CrawlerConfig,
    *,
    run_id: str,
    workflow_run_id: str,
    identity_id: str,
    user_id: str,
    company_id: str,
) -> WorkflowResult:
    """Run ATS enrichment for a single company and dispatch extraction on success."""
    database = get_database(config)
    result = run_enrichment_ats_enrichment(database, config, company_ids=[company_id])

    # If enrichment succeeded, look up the resolved slug and dispatch extraction.
    if result.enriched_count > 0:
        companies_collection = database["companies"]
        try:
            doc = companies_collection.find_one(
                {"_id": ObjectId(company_id)},
                {"ats_provider": 1, "ats_slug": 1},
            )
            if doc and doc.get("ats_provider") and doc.get("ats_slug"):
                _dispatch_ats_job_extraction(
                    redis_client,
                    config,
                    run_id=run_id,
                    identity_id=identity_id,
                    user_id=user_id,
                    company_id=company_id,
                    ats_provider=doc["ats_provider"],
                    ats_slug=doc["ats_slug"],
                    producer_workflow_run_id=workflow_run_id,
                )
        except Exception as exc:
            logger.warning(
                "failed to dispatch ats_job_extraction for company %s: %s",
                company_id,
                exc,
            )

    return result


def worker_main(config: CrawlerConfig) -> None:
    redis_client: redis.Redis | None = None

    while True:
        try:
            if redis_client is None:
                redis_client = _connect_redis(config)
                logger.info(
                    "enrichment_ats_enrichment connected to redis at %s:%s",
                    config.redis_host,
                    config.redis_port,
                )

            queue_item = cast(
                tuple[str, str] | None,
                redis_client.blpop(
                    [config.crawler_enrichment_ats_enrichment_queue_name], timeout=0
                ),
            )
            if not queue_item:
                continue

            _, raw_payload = queue_item
            try:
                event = parse_company_discovery_event(raw_payload)
            except Exception as exc:
                logger.warning("invalid company discovery event payload: %s", exc)
                continue

            run_id = event.run_id.strip()
            workflow_run_id = event.workflow_run_id.strip()
            identity_id = event.identity_id.strip()
            company_id = event.company_id.strip()
            user_id = event.user_id.strip()

            if not identity_id or not company_id or not user_id:
                logger.warning(
                    "company discovery event missing identity_id, company_id, or user_id: %s",
                    raw_payload,
                )
                continue

            # Generate a new workflow_run_id for this enrichment attempt.
            enrichment_workflow_run_id = _new_workflow_run_id()

            started_at = utc_timestamp()
            publish_progress(
                redis_client,
                config,
                run_id=run_id,
                identity_id=identity_id,
                status="running",
                workflow=_WORKFLOW_ID,
                estimated_total=1,
                completed=0,
                started_at=started_at,
                workflow_id=_WORKFLOW_ID,
                workflow_run_id=enrichment_workflow_run_id,
            )

            try:
                _run_enrichment_for_event(
                    redis_client,
                    config,
                    run_id=run_id,
                    workflow_run_id=enrichment_workflow_run_id,
                    identity_id=identity_id,
                    user_id=user_id,
                    company_id=company_id,
                )
                finished_at = utc_timestamp()
                publish_progress(
                    redis_client,
                    config,
                    run_id=run_id,
                    identity_id=identity_id,
                    status="completed",
                    workflow=_WORKFLOW_ID,
                    estimated_total=1,
                    completed=1,
                    started_at=started_at,
                    finished_at=finished_at,
                    message="ATS enrichment completed",
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=enrichment_workflow_run_id,
                )
            except Exception as exc:
                logger.exception(
                    "enrichment_ats_enrichment failed for company %s: %s",
                    company_id,
                    exc,
                )
                finished_at = utc_timestamp()
                publish_progress(
                    redis_client,
                    config,
                    run_id=run_id,
                    identity_id=identity_id,
                    status="failed",
                    workflow=_WORKFLOW_ID,
                    estimated_total=1,
                    completed=0,
                    started_at=started_at,
                    finished_at=finished_at,
                    message="ATS enrichment failed",
                    reason="run_failed",
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=enrichment_workflow_run_id,
                )
        except Exception as exc:
            logger.warning("enrichment worker loop error: %s", exc)
            redis_client = None
            time.sleep(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the enrichment_ats_enrichment workflow worker"
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        required=True,
        help="Run as a long-lived Redis dispatch queue worker",
    )
    return parser


def main() -> None:
    build_parser().parse_args()
    config = CrawlerConfig.from_env()
    worker_main(config)


if __name__ == "__main__":
    main()
