from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, cast

from google.protobuf.timestamp_pb2 import Timestamp
import redis

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.db import get_database
from src.python.web_crawler.workflow1 import run_workflow1
from src.python.web_crawler.workflow2 import estimate_workflow2_url_checks, run_workflow2
from src.python.web_crawler.workflow3 import estimate_workflow3_job_checks, run_workflow3

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_PHASE_MESSAGES = {
    "queued": "Waiting for worker pickup",
    "workflow1_company_discovery": "Collecting company candidates",
    "workflow2_ats_enrichment": "Resolving ATS providers and slugs",
    "workflow3_ats_job_extraction": "Fetching ATS job postings",
    "finalizing": "Finalizing crawl run",
}


def _utc_timestamp() -> Timestamp:
    now = datetime.now(timezone.utc)
    timestamp = Timestamp()
    timestamp.FromDatetime(now)
    return timestamp


def _timestamp_to_wire_dict(value: Timestamp | None) -> dict[str, int] | None:
    if value is None:
        return None
    return {"seconds": int(value.seconds), "nanos": int(value.nanos)}


def _timestamp_from_wire_value(value: Any) -> Timestamp | None:
    if value is None:
        return None

    if isinstance(value, dict):
        seconds = int(value.get("seconds", 0))
        nanos = int(value.get("nanos", 0))
        timestamp = Timestamp(seconds=seconds, nanos=nanos)
        return timestamp

    if isinstance(value, str) and value.strip():
        timestamp = Timestamp()
        timestamp.FromJsonString(value)
        return timestamp

    return None


def _crawl_progress_to_wire_dict(payload: common_pb2.CrawlProgress) -> dict[str, Any]:
    return {
        "run_id": payload.run_id,
        "identity_id": payload.identity_id,
        "status": payload.status,
        "phase": payload.phase,
        "message": payload.message,
        "estimated_total": payload.estimated_total,
        "completed": payload.completed,
        "percent": payload.percent,
        "started_at": _timestamp_to_wire_dict(payload.started_at if payload.HasField("started_at") else None),
        "updated_at": _timestamp_to_wire_dict(payload.updated_at if payload.HasField("updated_at") else None),
        "finished_at": _timestamp_to_wire_dict(payload.finished_at if payload.HasField("finished_at") else None),
        "reason": payload.reason,
    }


def _crawl_trigger_payload_from_wire_json(raw_payload: str) -> common_pb2.CrawlTriggerQueuePayload:
    parsed = json.loads(raw_payload)
    if not isinstance(parsed, dict):
        raise ValueError("queue payload must be a JSON object")

    payload = common_pb2.CrawlTriggerQueuePayload(
        run_id=str(parsed.get("run_id") or "").strip(),
        identity_id=str(parsed.get("identity_id") or "").strip(),
    )

    requested_at = _timestamp_from_wire_value(parsed.get("requested_at"))
    if requested_at is not None:
        payload.requested_at.CopyFrom(requested_at)

    return payload


def _crawl_trigger_payload_to_wire_dict(payload: common_pb2.CrawlTriggerQueuePayload) -> dict[str, Any]:
    return {
        "run_id": payload.run_id,
        "identity_id": payload.identity_id,
        "requested_at": _timestamp_to_wire_dict(payload.requested_at if payload.HasField("requested_at") else None),
    }


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
    phase: str,
    estimated_total: int,
    completed: int,
    started_at: Timestamp | None,
    finished_at: Timestamp | None = None,
    message: str | None = None,
    reason: str = "",
) -> None:
    if redis_client is None:
        return

    percent = 0
    if estimated_total > 0:
        percent = max(0, min(100, int((completed / estimated_total) * 100)))

    payload = common_pb2.CrawlProgress(
        run_id=run_id,
        identity_id=identity_id,
        status=status,
        phase=phase,
        message=message or _PHASE_MESSAGES.get(phase, ""),
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
            json.dumps(_crawl_progress_to_wire_dict(payload)),
        )
    except Exception as exc:
        logger.warning("failed to publish crawl progress for run %s: %s", run_id, exc)


def _run_single_crawl(config: CrawlerConfig, identity_id: str, run_id: str, redis_client: redis.Redis | None) -> dict[str, dict]:
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
        phase="workflow1_company_discovery",
        estimated_total=estimated_total,
        completed=completed_units,
        started_at=started_at,
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
        phase="workflow2_ats_enrichment",
        estimated_total=estimated_total,
        completed=completed_units,
        started_at=started_at,
        message=f"Preparing ATS enrichment for {len(workflow1_result.company_ids)} discovered companies",
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
            phase="workflow2_ats_enrichment",
            estimated_total=estimated_total,
            completed=completed_units + max(workflow_completed, 0),
            started_at=started_at,
            message=message,
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
        phase="workflow3_ats_job_extraction",
        estimated_total=estimated_total,
        completed=completed_units,
        started_at=started_at,
        message=f"Preparing job extraction for {len(workflow2_result.company_ids)} ATS-enriched companies",
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
            phase="workflow3_ats_job_extraction",
            estimated_total=estimated_total,
            completed=completed_units + max(workflow_completed, 0),
            started_at=started_at,
            message=message,
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
        phase="finalizing",
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
        phase="finalizing",
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
                payload = _crawl_trigger_payload_from_wire_json(raw_payload)
            except Exception as exc:
                logger.warning("invalid crawler trigger payload: %s", exc)
                continue

            identity_id = payload.identity_id.strip()
            run_id = payload.run_id.strip()
            if not identity_id or not run_id:
                logger.warning(
                    "crawler trigger payload missing required keys: %s",
                    _crawl_trigger_payload_to_wire_dict(payload),
                )
                continue

            _publish_progress(
                redis_client,
                config,
                run_id=run_id,
                identity_id=identity_id,
                status="queued",
                phase="queued",
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
                    phase="finalizing",
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