from __future__ import annotations

import argparse
import logging
import time
from typing import cast
import uuid

import redis

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.db import get_database
from src.python.web_crawler.progress import publish_progress, utc_timestamp
from src.python.web_crawler.workflow_messages import (
    company_discovery_event_to_json,
    crawl_trigger_to_dict,
    parse_crawl_trigger,
    workflow_dispatch_to_json,
)

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _new_workflow_run_id() -> str:
    return uuid.uuid4().hex


def _connect_redis(config: CrawlerConfig) -> redis.Redis:
    client = redis.Redis(host=config.redis_host, port=config.redis_port, socket_connect_timeout=5, decode_responses=True)
    client.ping()
    return client


def _query_companies_needing_enrichment(database) -> list[str]:
    """Return hex _id strings for companies that still need ATS enrichment."""
    companies = database["companies"]
    cursor = companies.find(
        {
            "$and": [
                {"enrichment_ats_enrichment_terminal_failure": {"$exists": False}},
                {
                    "$or": [
                        {"ats_provider": {"$in": [None, ""]}},
                        {"ats_provider": {"$exists": False}},
                        {"ats_slug": {"$in": [None, ""]}},
                        {"ats_slug": {"$exists": False}},
                    ]
                },
            ]
        },
        {"_id": 1},
    )
    return [str(doc["_id"]) for doc in cursor]


def _fan_out_enrichment_events(
    redis_client: redis.Redis,
    config: CrawlerConfig,
    database,
    *,
    run_id: str,
    identity_id: str,
) -> int:
    """Push one CompanyDiscoveryEvent per unenriched company to the enrichment queue."""
    company_ids = _query_companies_needing_enrichment(database)
    workflow_run_id = _new_workflow_run_id()
    emitted = 0
    for company_id in company_ids:
        try:
            event = common_pb2.CompanyDiscoveryEvent(
                run_id=run_id,
                workflow_run_id=workflow_run_id,
                workflow_id="dispatcher",
                identity_id=identity_id,
                company_id=company_id,
                reason="no_ats_slug",
            )
            event.emitted_at.CopyFrom(utc_timestamp())
            redis_client.rpush(
                config.crawler_enrichment_ats_enrichment_queue_name,
                company_discovery_event_to_json(event),
            )
            emitted += 1
        except Exception as exc:
            logger.warning("failed to emit enrichment event for company %s: %s", company_id, exc)
    logger.info(
        "fanned out %d enrichment events run_id=%s identity_id=%s",
        emitted,
        run_id,
        identity_id,
    )
    return emitted


def _dispatch_workflow(
    redis_client: redis.Redis,
    config: CrawlerConfig,
    *,
    run_id: str,
    identity_id: str,
    workflow_id: str,
    queue_name: str,
) -> str:
    workflow_run_id = _new_workflow_run_id()
    dispatch = common_pb2.WorkflowDispatchMessage(
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        workflow_id=workflow_id,
        identity_id=identity_id,
        trigger_kind="public_crawl",
        attempt=1,
    )
    dispatch.dispatched_at.CopyFrom(utc_timestamp())
    redis_client.rpush(queue_name, workflow_dispatch_to_json(dispatch))
    return workflow_run_id


def worker_main(config: CrawlerConfig) -> None:
    redis_client: redis.Redis | None = None

    while True:
        try:
            if redis_client is None:
                redis_client = _connect_redis(config)
                logger.info("connected to redis at %s:%s", config.redis_host, config.redis_port)

            queue_item = cast(
                tuple[str, str] | None,
                redis_client.blpop([config.crawler_trigger_queue_name], timeout=0),
            )
            if not queue_item:
                continue

            _, raw_payload = queue_item
            try:
                payload = parse_crawl_trigger(raw_payload)
            except Exception as exc:
                logger.warning("invalid crawler trigger payload: %s", exc)
                continue

            identity_id = payload.identity_id.strip()
            run_id = payload.run_id.strip()
            if not identity_id or not run_id:
                logger.warning(
                    "crawler trigger payload missing required keys: %s",
                    crawl_trigger_to_dict(payload),
                )
                continue

            publish_progress(
                redis_client,
                config,
                run_id=run_id,
                identity_id=identity_id,
                status="queued",
                workflow="queued",
                estimated_total=1,
                completed=0,
                started_at=None,
                message="Worker picked up queued crawl request",
            )

            workflow_run_id = _dispatch_workflow(
                redis_client,
                config,
                run_id=run_id,
                identity_id=identity_id,
                workflow_id="crawler_company_discovery",
                queue_name=config.crawler_company_discovery_queue_name,
            )
            logger.info(
                "dispatched crawler_company_discovery run_id=%s workflow_run_id=%s identity_id=%s",
                run_id,
                workflow_run_id,
                identity_id,
            )

            ats_workflow_run_id = _dispatch_workflow(
                redis_client,
                config,
                run_id=run_id,
                identity_id=identity_id,
                workflow_id="crawler_ats_job_extraction",
                queue_name=config.crawler_ats_job_extraction_queue_name,
            )
            logger.info(
                "dispatched crawler_ats_job_extraction run_id=%s workflow_run_id=%s identity_id=%s",
                run_id,
                ats_workflow_run_id,
                identity_id,
            )

            levelsfyi_workflow_run_id = _dispatch_workflow(
                redis_client,
                config,
                run_id=run_id,
                identity_id=identity_id,
                workflow_id="crawler_levelsfyi",
                queue_name=config.crawler_levelsfyi_queue_name,
            )
            logger.info(
                "dispatched crawler_levelsfyi run_id=%s workflow_run_id=%s identity_id=%s",
                run_id,
                levelsfyi_workflow_run_id,
                identity_id,
            )

            fourdayweek_workflow_run_id = _dispatch_workflow(
                redis_client,
                config,
                run_id=run_id,
                identity_id=identity_id,
                workflow_id="crawler_4dayweek",
                queue_name=config.crawler_4dayweek_queue_name,
            )
            logger.info(
                "dispatched crawler_4dayweek run_id=%s workflow_run_id=%s identity_id=%s",
                run_id,
                fourdayweek_workflow_run_id,
                identity_id,
            )

            database = get_database(config)
            _fan_out_enrichment_events(
                redis_client,
                config,
                database,
                run_id=run_id,
                identity_id=identity_id,
            )

        except Exception as exc:
            logger.warning("worker loop error: %s", exc)
            redis_client = None
            time.sleep(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the web crawler orchestrator")
    parser.add_argument("--worker", action="store_true", required=True, help="Run as a long-lived Redis queue worker")
    return parser


def main() -> None:
    build_parser().parse_args()
    config = CrawlerConfig.from_env()
    worker_main(config)


if __name__ == "__main__":
    main()
