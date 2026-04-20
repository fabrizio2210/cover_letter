from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
import time
from typing import cast
import uuid

from google.protobuf.timestamp_pb2 import Timestamp
import redis

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.db import get_database
from src.python.web_crawler.workflow_messages import crawl_progress_to_dict, crawl_trigger_to_dict, parse_crawl_trigger, workflow_dispatch_to_json
from src.python.web_crawler.workflow1 import run_workflow1
from src.python.web_crawler.workflow2 import estimate_workflow2_url_checks, run_workflow2
from src.python.web_crawler.workflow3 import estimate_workflow3_job_checks, run_workflow3

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_WORKFLOW_MESSAGES = {
    "queued": "Waiting for worker pickup",
    "crawler_company_discovery": "Collecting company candidates",
    "enrichment_ats_enrichment": "Resolving ATS providers and slugs",
    "crawler_ats_job_extraction": "Fetching ATS job postings",
    "crawler_4dayweek": "Collecting 4dayweek jobs",
    "finalizing": "Finalizing crawl run",
}


def _utc_timestamp() -> Timestamp:
    now = datetime.now(timezone.utc)
    timestamp = Timestamp()
    timestamp.FromDatetime(now)
    return timestamp


def _new_workflow_run_id() -> str:
    return uuid.uuid4().hex


def _enqueue_workflow_dispatch(
    redis_client: redis.Redis | None,
    config: CrawlerConfig,
    payload: common_pb2.WorkflowDispatchMessage,
) -> None:
    if redis_client is None:
        return
    redis_client.rpush(config.crawler_workflow_dispatch_queue_name, workflow_dispatch_to_json(payload))


def _dispatch_initial_workflows(
    redis_client: redis.Redis | None,
    config: CrawlerConfig,
    *,
    run_id: str,
    identity_id: str,
) -> list[str]:
    workflow_ids = ["crawler_company_discovery", "crawler_ats_job_extraction", "crawler_4dayweek"]
    workflow_run_ids: list[str] = []
    
    for workflow_id in workflow_ids:
        workflow_run_id = _new_workflow_run_id()
        workflow_run_ids.append(workflow_run_id)
        dispatch = common_pb2.WorkflowDispatchMessage(
            run_id=run_id,
            workflow_run_id=workflow_run_id,
            workflow_id=workflow_id,
            identity_id=identity_id,
            trigger_kind="public_crawl",
            attempt=1,
        )
        dispatch.dispatched_at.CopyFrom(_utc_timestamp())
        _enqueue_workflow_dispatch(redis_client, config, dispatch)

    return workflow_run_ids


def _connect_redis(config: CrawlerConfig) -> redis.Redis:
    client = redis.Redis(host=config.redis_host, port=config.redis_port, socket_connect_timeout=5, decode_responses=True)
    client.ping()
    return client


def _publish_progress(
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
    payload.updated_at.CopyFrom(_utc_timestamp())
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


def _run_single_crawl(config: CrawlerConfig, identity_id: str, run_id: str, redis_client: redis.Redis | None) -> dict[str, dict]:
    if config.enable_workflow_dispatch_mode and redis_client is not None:
        workflow_run_ids = _dispatch_initial_workflows(
            redis_client,
            config,
            run_id=run_id,
            identity_id=identity_id,
        )
        return {
            "dispatch": {
                "workflow_run_ids": workflow_run_ids,
                "workflow_dispatch_queue_name": config.crawler_workflow_dispatch_queue_name,
            }
        }

    database = get_database(config)
    started_at = _utc_timestamp()
    workflow1_units = 1
    workflow2_units = 1
    workflow3_units = 1
    finalizing_units = 1
    estimated_total = workflow1_units + workflow2_units + workflow3_units + finalizing_units
    completed_units = 0

    _publish_progress(
        redis_client,
        config,
        run_id=run_id,
        identity_id=identity_id,
        status="running",
        workflow="crawler_company_discovery",
        estimated_total=estimated_total,
        completed=completed_units,
        started_at=started_at,
        workflow_id="crawler_company_discovery",
        workflow_run_id=f"{run_id}:crawler_company_discovery:inline",
    )
    workflow1_result = run_workflow1(database, config, identity_id)
    completed_units += workflow1_units

    workflow2_units = estimate_workflow2_url_checks(len(workflow1_result.company_ids))
    estimated_total = workflow1_units + workflow2_units + workflow3_units + finalizing_units

    _publish_progress(
        redis_client,
        config,
        run_id=run_id,
        identity_id=identity_id,
        status="running",
        workflow="enrichment_ats_enrichment",
        estimated_total=estimated_total,
        completed=completed_units,
        started_at=started_at,
        message=f"Preparing ATS enrichment for {len(workflow1_result.company_ids)} discovered companies",
        workflow_id="enrichment_ats_enrichment",
        workflow_run_id=f"{run_id}:enrichment_ats_enrichment:inline",
    )

    def _publish_workflow2_progress(workflow_completed: int, workflow_estimated: int, message: str) -> None:
        nonlocal workflow2_units, estimated_total
        if workflow_estimated != workflow2_units:
            workflow2_units = max(workflow_estimated, 1)
            estimated_total = workflow1_units + workflow2_units + workflow3_units + finalizing_units

        _publish_progress(
            redis_client,
            config,
            run_id=run_id,
            identity_id=identity_id,
            status="running",
            workflow="enrichment_ats_enrichment",
            estimated_total=estimated_total,
            completed=completed_units + max(workflow_completed, 0),
            started_at=started_at,
            message=message,
            workflow_id="enrichment_ats_enrichment",
            workflow_run_id=f"{run_id}:enrichment_ats_enrichment:inline",
        )

    workflow2_result = run_workflow2(
        database,
        config,
        workflow1_result.company_ids,
        progress_callback=_publish_workflow2_progress,
    )
    completed_units += workflow2_units

    workflow3_units = estimate_workflow3_job_checks(len(workflow2_result.company_ids))
    estimated_total = workflow1_units + workflow2_units + workflow3_units + finalizing_units

    _publish_progress(
        redis_client,
        config,
        run_id=run_id,
        identity_id=identity_id,
        status="running",
        workflow="crawler_ats_job_extraction",
        estimated_total=estimated_total,
        completed=completed_units,
        started_at=started_at,
        message=f"Preparing job extraction for {len(workflow2_result.company_ids)} ATS-enriched companies",
        workflow_id="crawler_ats_job_extraction",
        workflow_run_id=f"{run_id}:crawler_ats_job_extraction:inline",
    )

    def _publish_workflow3_progress(workflow_completed: int, workflow_estimated: int, message: str) -> None:
        nonlocal workflow3_units, estimated_total
        if workflow_estimated != workflow3_units:
            workflow3_units = max(workflow_estimated, 1)
            estimated_total = workflow1_units + workflow2_units + workflow3_units + finalizing_units

        _publish_progress(
            redis_client,
            config,
            run_id=run_id,
            identity_id=identity_id,
            status="running",
            workflow="crawler_ats_job_extraction",
            estimated_total=estimated_total,
            completed=completed_units + max(workflow_completed, 0),
            started_at=started_at,
            message=message,
            workflow_id="crawler_ats_job_extraction",
            workflow_run_id=f"{run_id}:crawler_ats_job_extraction:inline",
        )

    workflow3_result = run_workflow3(
        database,
        config,
        workflow2_result.company_ids,
        identity_id,
        progress_callback=_publish_workflow3_progress,
    )
    completed_units += workflow3_units

    _publish_progress(
        redis_client,
        config,
        run_id=run_id,
        identity_id=identity_id,
        status="running",
        workflow="finalizing",
        estimated_total=estimated_total,
        completed=completed_units,
        started_at=started_at,
    )
    completed_units += finalizing_units

    finished_at = _utc_timestamp()
    _publish_progress(
        redis_client,
        config,
        run_id=run_id,
        identity_id=identity_id,
        status="completed",
        workflow="finalizing",
        estimated_total=estimated_total,
        completed=completed_units,
        started_at=started_at,
        finished_at=finished_at,
        message="Crawl completed",
    )

    return {
        "workflow1": asdict(workflow1_result),
        "workflow2": asdict(workflow2_result),
        "workflow3": asdict(workflow3_result),
    }


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

            _publish_progress(
                redis_client,
                config,
                run_id=run_id,
                identity_id=identity_id,
                status="queued",
                workflow="queued",
                estimated_total=4,
                completed=0,
                started_at=None,
                message="Worker picked up queued crawl request",
            )

            try:
                _run_single_crawl(config, identity_id, run_id, redis_client)
            except Exception as exc:
                logger.exception("crawl run failed for identity %s: %s", identity_id, exc)
                finished_at = _utc_timestamp()
                _publish_progress(
                    redis_client,
                    config,
                    run_id=run_id,
                    identity_id=identity_id,
                    status="failed",
                    workflow="finalizing",
                    estimated_total=4,
                    completed=4,
                    started_at=finished_at,
                    finished_at=finished_at,
                    message="Crawl failed",
                    reason="run_failed",
                )
        except Exception as exc:
            logger.warning("worker loop error: %s", exc)
            redis_client = None
            time.sleep(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the web crawler workflows")
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--identity-id", help="MongoDB ObjectId of the identity to crawl for")
    mode_group.add_argument("--worker", action="store_true", help="Run as a long-lived Redis queue worker")
    parser.add_argument(
        "--force-serp-retry",
        action="store_true",
        help="Bypass prior SERP-attempt checks in workflow2 and retry slug search fallback",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = CrawlerConfig.from_env()
    if args.force_serp_retry:
        config.force_serp_retry_on_prior_attempt = True
    if args.worker:
        worker_main(config)
        return

    result = _run_single_crawl(config, args.identity_id, run_id="manual-cli-run", redis_client=None)
    print(json.dumps(result))


if __name__ == "__main__":
    main()