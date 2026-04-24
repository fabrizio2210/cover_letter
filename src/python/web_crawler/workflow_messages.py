from __future__ import annotations

import json
from typing import Any

from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.timestamp_pb2 import Timestamp

from src.python.ai_querier import common_pb2


def _timestamp_from_wire_value(value: Any) -> Timestamp | None:
    if value is None:
        return None

    if isinstance(value, dict):
        seconds = int(value.get("seconds", 0))
        nanos = int(value.get("nanos", 0))
        return Timestamp(seconds=seconds, nanos=nanos)

    if isinstance(value, str) and value.strip():
        timestamp = Timestamp()
        timestamp.FromJsonString(value)
        return timestamp

    return None


def _timestamp_to_wire_dict(value: Timestamp | None) -> dict[str, int] | None:
    if value is None:
        return None
    return {"seconds": int(value.seconds), "nanos": int(value.nanos)}


def parse_crawl_trigger(raw_payload: str) -> common_pb2.CrawlTriggerQueuePayload:
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


def crawl_trigger_to_dict(payload: common_pb2.CrawlTriggerQueuePayload) -> dict[str, Any]:
    return {
        "run_id": payload.run_id,
        "identity_id": payload.identity_id,
        "requested_at": _timestamp_to_wire_dict(payload.requested_at if payload.HasField("requested_at") else None),
    }


def crawl_progress_to_dict(payload: common_pb2.CrawlProgress) -> dict[str, Any]:
    return {
        "run_id": payload.run_id,
        "workflow_run_id": payload.workflow_run_id,
        "workflow_id": payload.workflow_id,
        "identity_id": payload.identity_id,
        "status": payload.status,
        "workflow": payload.workflow,
        "message": payload.message,
        "estimated_total": payload.estimated_total,
        "completed": payload.completed,
        "percent": payload.percent,
        "started_at": _timestamp_to_wire_dict(payload.started_at if payload.HasField("started_at") else None),
        "updated_at": _timestamp_to_wire_dict(payload.updated_at if payload.HasField("updated_at") else None),
        "finished_at": _timestamp_to_wire_dict(payload.finished_at if payload.HasField("finished_at") else None),
        "reason": payload.reason,
    }


def workflow_dispatch_to_json(payload: common_pb2.WorkflowDispatchMessage) -> str:
    wire = MessageToDict(payload, preserving_proto_field_name=True)
    return json.dumps(wire)


def parse_workflow_dispatch(raw_payload: str) -> common_pb2.WorkflowDispatchMessage:
    parsed = json.loads(raw_payload)
    if not isinstance(parsed, dict):
        raise ValueError("workflow dispatch payload must be a JSON object")
    message = common_pb2.WorkflowDispatchMessage()
    ParseDict(parsed, message)
    return message


def company_discovery_event_to_json(payload: common_pb2.CompanyDiscoveryEvent) -> str:
    wire = MessageToDict(payload, preserving_proto_field_name=True)
    return json.dumps(wire)


def parse_company_discovery_event(raw_payload: str) -> common_pb2.CompanyDiscoveryEvent:
    parsed = json.loads(raw_payload)
    if not isinstance(parsed, dict):
        raise ValueError("company discovery event payload must be a JSON object")
    message = common_pb2.CompanyDiscoveryEvent()
    ParseDict(parsed, message)
    return message


def ats_job_trigger_event_to_json(payload: common_pb2.AtsJobTriggerEvent) -> str:
    wire = MessageToDict(payload, preserving_proto_field_name=True)
    return json.dumps(wire)


def job_retire_event_to_json(payload: common_pb2.JobRetireEvent) -> str:
    wire = MessageToDict(payload, preserving_proto_field_name=True)
    return json.dumps(wire)


def parse_job_retire_event(raw_payload: str) -> common_pb2.JobRetireEvent:
    """Parse a job retire event from a raw JSON queue payload."""
    parsed = json.loads(raw_payload)
    if not isinstance(parsed, dict):
        raise ValueError("job retire event payload must be a JSON object")
    message = common_pb2.JobRetireEvent()
    ParseDict(parsed, message)
    return message
