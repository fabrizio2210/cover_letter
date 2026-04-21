from __future__ import annotations

import argparse
import logging
import time
from typing import cast

import redis

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.crawler_ats_job_extraction_workflow import run_crawler_ats_job_extraction
from src.python.web_crawler.db import get_database
from src.python.web_crawler.progress import publish_progress, utc_timestamp
from src.python.web_crawler.workflow_messages import parse_workflow_dispatch

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_WORKFLOW_ID = "crawler_ats_job_extraction"


def _connect_redis(config: CrawlerConfig) -> redis.Redis:
    client = redis.Redis(host=config.redis_host, port=config.redis_port, socket_connect_timeout=5, decode_responses=True)
    client.ping()
    return client


def worker_main(config: CrawlerConfig) -> None:
    redis_client: redis.Redis | None = None

    while True:
        try:
            if redis_client is None:
                redis_client = _connect_redis(config)
                logger.info("connected to redis at %s:%s", config.redis_host, config.redis_port)

            queue_item = cast(
                tuple[str, str] | None,
                redis_client.blpop([config.crawler_ats_job_extraction_queue_name], timeout=0),
            )
            if not queue_item:
                continue

            _, raw_payload = queue_item
            try:
                message = parse_workflow_dispatch(raw_payload)
            except Exception as exc:
                logger.warning("invalid workflow dispatch payload: %s", exc)
                continue

            run_id = message.run_id.strip()
            workflow_run_id = message.workflow_run_id.strip()
            identity_id = message.identity_id.strip()

            if not run_id or not identity_id:
                logger.warning("dispatch message missing run_id or identity_id: %s", raw_payload)
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

            try:
                database = get_database(config)
                run_crawler_ats_job_extraction(database, config, identity_id=identity_id)
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
                    message="ATS job extraction completed",
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=workflow_run_id,
                )
            except Exception as exc:
                logger.exception("crawler_ats_job_extraction failed for identity %s: %s", identity_id, exc)
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
                    message="ATS job extraction failed",
                    reason="run_failed",
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=workflow_run_id,
                )
        except Exception as exc:
            logger.warning("worker loop error: %s", exc)
            redis_client = None
            time.sleep(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the crawler_ats_job_extraction workflow worker")
    parser.add_argument("--worker", action="store_true", required=True, help="Run as a long-lived Redis dispatch queue worker")
    return parser


def main() -> None:
    build_parser().parse_args()
    config = CrawlerConfig.from_env()
    worker_main(config)


if __name__ == "__main__":
    main()
