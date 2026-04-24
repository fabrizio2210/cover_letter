from __future__ import annotations

import argparse
import logging
import time
import uuid

import redis

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.db import get_database
from src.python.web_crawler.enrichment_retiring_jobs.workflow import _WORKFLOW_ID, run_enrichment_retiring_jobs
from src.python.web_crawler.progress import publish_progress, utc_timestamp

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
                    "enrichment_retiring_jobs connected to redis at %s:%s",
                    config.redis_host,
                    config.redis_port,
                )

            # Block until a trigger message arrives or the interval elapses.
            # When the timeout expires redis returns None, which triggers the
            # periodic retirement run.
            redis_client.blpop(
                [config.crawler_enrichment_retiring_jobs_queue_name],
                timeout=config.crawler_enrichment_retiring_jobs_interval_seconds,
            )

            # run_id identifies the parent run; workflow_run_id identifies this
            # specific workflow execution attempt within that run.
            run_id = _new_workflow_run_id()
            workflow_run_id = _new_workflow_run_id()
            identity_id = "system"

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
                result = run_enrichment_retiring_jobs(database, config)
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
                    message=(
                        f"retiring jobs completed: {result.updated_count} marked closed, "
                        f"{result.deleted_count} deleted"
                    ),
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=workflow_run_id,
                )
            except Exception as exc:
                logger.exception("enrichment_retiring_jobs run failed: %s", exc)
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
                    message="retiring jobs failed",
                    reason="run_failed",
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=workflow_run_id,
                )
        except Exception as exc:
            logger.warning("enrichment_retiring_jobs worker loop error: %s", exc)
            redis_client = None
            time.sleep(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the enrichment_retiring_jobs workflow worker"
    )
    parser.add_argument(
        "--worker",
        action="store_true",
        required=True,
        help="Run as a long-lived periodic worker",
    )
    return parser


def main() -> None:
    build_parser().parse_args()
    config = CrawlerConfig.from_env()
    worker_main(config)


if __name__ == "__main__":
    main()
