from __future__ import annotations

import argparse
import json
import logging
import time
from typing import cast
import uuid

import redis

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.progress import publish_progress, utc_timestamp
from src.python.web_crawler.workflow_messages import crawl_trigger_to_dict, parse_crawl_trigger, workflow_dispatch_to_json

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _new_workflow_run_id() -> str:
    return uuid.uuid4().hex


def _connect_redis(config: CrawlerConfig) -> redis.Redis:
    client = redis.Redis(host=config.redis_host, port=config.redis_port, socket_connect_timeout=5, decode_responses=True)
    client.ping()
    return client


def _dispatch_workflow(
    redis_client: redis.Redis,
    config: CrawlerConfig,
    *,
    run_id: str,
    identity_id: str,
    workflow_id: str,
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
    redis_client.rpush(config.crawler_workflow_dispatch_queue_name, workflow_dispatch_to_json(dispatch))
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
            )
            logger.info(
                "dispatched crawler_company_discovery run_id=%s workflow_run_id=%s identity_id=%s",
                run_id,
                workflow_run_id,
                identity_id,
            )

        except Exception as exc:
            logger.warning("worker loop error: %s", exc)
            redis_client = None
            time.sleep(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the web crawler orchestrator")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--identity-id", help="MongoDB ObjectId of the identity to crawl for")
    mode_group.add_argument("--worker", action="store_true", help="Run as a long-lived Redis queue worker")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = CrawlerConfig.from_env()
    if args.worker:
        worker_main(config)
        return

    redis_client = _connect_redis(config)
    run_id = uuid.uuid4().hex
    workflow_run_id = _dispatch_workflow(
        redis_client,
        config,
        run_id=run_id,
        identity_id=args.identity_id,
        workflow_id="crawler_company_discovery",
    )
    print(json.dumps({
        "run_id": run_id,
        "workflow_run_id": workflow_run_id,
        "workflow_id": "crawler_company_discovery",
        "dispatch_queue": config.crawler_workflow_dispatch_queue_name,
    }))


if __name__ == "__main__":
    main()
