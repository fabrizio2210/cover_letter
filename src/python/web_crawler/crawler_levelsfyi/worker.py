from __future__ import annotations

import argparse
import logging
import time
import uuid
from typing import cast

import redis

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.crawler_levelsfyi.workflow import (
    _WORKFLOW_ID,
    _emit_enrichment_events,
    run_crawler_levelsfyi,
)
from src.python.web_crawler.db import get_database, get_user_database
from src.python.web_crawler.progress import publish_progress, utc_timestamp
from src.python.web_crawler.workflow_counters import increment_discovered_jobs_counter
from src.python.web_crawler.workflow_messages import parse_workflow_dispatch

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


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


def worker_main(config: CrawlerConfig) -> None:
    redis_client: redis.Redis | None = None

    while True:
        try:
            if redis_client is None:
                redis_client = _connect_redis(config)
                logger.info(
                    "crawler_levelsfyi connected to redis at %s:%s",
                    config.redis_host,
                    config.redis_port,
                )

            queue_item = cast(
                tuple[str, str] | None,
                redis_client.blpop([config.crawler_levelsfyi_queue_name], timeout=0),
            )
            if not queue_item:
                continue

            _, raw_payload = queue_item
            try:
                message = parse_workflow_dispatch(raw_payload)
            except Exception as exc:
                logger.warning("crawler_levelsfyi: invalid workflow dispatch payload: %s", exc)
                continue

            run_id = message.run_id.strip()
            workflow_run_id = message.workflow_run_id.strip()
            identity_id = message.identity_id.strip()
            user_id = message.user_id.strip()

            if not identity_id or not user_id:
                logger.warning(
                    "crawler_levelsfyi: dispatch message missing identity_id or user_id: %s", raw_payload
                )
                continue

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
                workflow_run_id=workflow_run_id,
            )

            def _progress_callback(completed: int, estimated_total: int, message: str) -> None:
                publish_progress(
                    redis_client,
                    config,
                    run_id=run_id,
                    identity_id=identity_id,
                    status="running",
                    workflow=_WORKFLOW_ID,
                    estimated_total=max(estimated_total, 1),
                    completed=completed,
                    started_at=started_at,
                    message=message,
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=workflow_run_id,
                )

            try:
                database = get_database(config)
                user_database = get_user_database(config, user_id)
                crawl_result = run_crawler_levelsfyi(
                    database,
                    config,
                    identity_id,
                    progress_callback=_progress_callback,
                    identity_database=user_database,
                )

                increment_discovered_jobs_counter(
                    config,
                    workflow_id=_WORKFLOW_ID,
                    delta=crawl_result.inserted_count + crawl_result.updated_count,
                )

                _emit_enrichment_events(
                    redis_client,
                    config,
                    run_id=run_id,
                    workflow_run_id=workflow_run_id,
                    identity_id=identity_id,
                    company_ids=crawl_result.new_company_ids,
                )

                finished_at = utc_timestamp()
                publish_progress(
                    redis_client,
                    config,
                    run_id=run_id,
                    identity_id=identity_id,
                    status="completed",
                    workflow=_WORKFLOW_ID,
                    estimated_total=max(crawl_result.discovered_count, 1),
                    completed=crawl_result.inserted_count + crawl_result.updated_count,
                    started_at=started_at,
                    finished_at=finished_at,
                    message=(
                        f"Levels.fyi crawl completed: "
                        f"{crawl_result.inserted_count} inserted, "
                        f"{crawl_result.updated_count} updated, "
                        f"{crawl_result.skipped_count} skipped"
                    ),
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=workflow_run_id,
                )
                logger.info(
                    "crawler_levelsfyi completed run_id=%s workflow_run_id=%s identity_id=%s "
                    "inserted=%d updated=%d skipped=%d new_companies=%d",
                    run_id,
                    workflow_run_id,
                    identity_id,
                    crawl_result.inserted_count,
                    crawl_result.updated_count,
                    crawl_result.skipped_count,
                    len(crawl_result.new_company_ids),
                )
            except Exception as exc:
                logger.exception(
                    "crawler_levelsfyi failed for identity %s: %s", identity_id, exc
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
                    message="Levels.fyi crawl failed",
                    reason="run_failed",
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=workflow_run_id,
                )

        except Exception as exc:
            logger.warning("crawler_levelsfyi worker loop error: %s", exc)
            redis_client = None
            time.sleep(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the crawler_levelsfyi workflow worker"
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
