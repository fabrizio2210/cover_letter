from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from typing import Optional

SCHEMA_VERSION = "1"


@dataclass
class TrainingCase:
    case_id: str
    source_job_id: str
    title: str
    location: str
    preference_key: str
    preference_guidance: str
    relevant_snippets: list[str]
    system_prompt: str
    user_prompt: str
    label_score: Optional[int] = None
    label_available: Optional[bool] = None
    schema_version: str = SCHEMA_VERSION


def new_case_id() -> str:
    return str(uuid.uuid4())


def validate_case(case: TrainingCase) -> list[str]:
    errors: list[str] = []
    if not case.case_id:
        errors.append("case_id is required")
    if not case.source_job_id:
        errors.append("source_job_id is required")
    if not case.preference_key:
        errors.append("preference_key is required")
    if not case.preference_guidance:
        errors.append("preference_guidance is required")
    if not case.system_prompt:
        errors.append("system_prompt is required")
    if not case.user_prompt:
        errors.append("user_prompt is required")

    if case.label_available is True:
        if case.label_score is None or case.label_score not in range(0, 6):
            errors.append("label_score must be 0..5 when label_available=true")
    if case.label_available is False and case.label_score is not None:
        errors.append("label_score must be null when label_available=false")

    return errors


def validate_cases(cases: list[TrainingCase]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for idx, case in enumerate(cases):
        for err in validate_case(case):
            errors.append(f"case[{idx}] {case.case_id!r}: {err}")
        if case.case_id in seen_ids:
            errors.append(f"case[{idx}] duplicate case_id {case.case_id!r}")
        seen_ids.add(case.case_id)
    return errors


def _case_from_dict(item: dict) -> TrainingCase:
    return TrainingCase(
        case_id=item["case_id"],
        source_job_id=item.get("source_job_id", ""),
        title=item.get("title", ""),
        location=item.get("location", ""),
        preference_key=item.get("preference_key", ""),
        preference_guidance=item.get("preference_guidance", ""),
        relevant_snippets=list(item.get("relevant_snippets", [])),
        system_prompt=item.get("system_prompt", ""),
        user_prompt=item.get("user_prompt", ""),
        label_score=item.get("label_score"),
        label_available=item.get("label_available"),
        schema_version=item.get("schema_version", SCHEMA_VERSION),
    )


def load_cases(path: str) -> list[TrainingCase]:
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        raise ValueError(f"Expected array in {path}")
    return [_case_from_dict(item) for item in raw]


def dump_cases(cases: list[TrainingCase], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump([asdict(case) for case in cases], handle, indent=2, ensure_ascii=False)
