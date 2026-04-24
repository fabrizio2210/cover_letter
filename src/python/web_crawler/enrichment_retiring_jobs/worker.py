from __future__ import annotations

import argparse
import logging
import time
import uuid
from typing import cast

import redis

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.db import get_database
from src.python.web_crawler.enrichment_retiring_jobs.workflow import _WORKFLOW_ID, run_enrichment_retiring_jobs
from src.python.web_crawler.progress import publish_progress, utc_timestamp
from src.python.web_crawler.workflow_messages import parse_job_retire_event

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

            queue_item = cast(
                tuple[str, str] | None,
                redis_client.blpop(
                    [config.crawler_enrichment_retiring_jobs_queue_name], timeout=0
                ),
            )
            if not queue_item:
                continue

            _, raw_payload = queue_item
            try:
                event = parse_job_retire_event(raw_payload)
            except Exception as exc:
                logger.warning("enrichment_retiring_jobs: invalid job retire event payload: %s", exc)
                continue

            run_id = event["run_id"]
            workflow_run_id = event["workflow_run_id"]
            identity_id = event["identity_id"]
            job_id = event["job_id"]

            if not job_id:
                logger.warning(
                    "enrichment_retiring_jobs: event missing job_id: %s", raw_payload
                )
                continue

            # Generate a new workflow_run_id for this retirement attempt if the
            # caller did not supply one.
            if not workflow_run_id:
                workflow_run_id = _new_workflow_run_id()
            if not run_id:
                run_id = workflow_run_id
            if not identity_id:
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
                result = run_enrichment_retiring_jobs(database, config, job_id)
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
                        f"retiring job {job_id}: updated={result.updated_count}, "
                        f"deleted={result.deleted_count}"
                    ),
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=workflow_run_id,
                )
            except Exception as exc:
                logger.exception(
                    "enrichment_retiring_jobs failed for job %s: %s", job_id, exc
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
                    message=f"retiring job {job_id} failed",
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
        help="Run as a long-lived Redis dispatch queue worker",
    )
    return parser


def main() -> None:
    build_parser().parse_args()
    config = CrawlerConfig.from_env()
    worker_main(config)


if __name__ == "__main__":
    main()
