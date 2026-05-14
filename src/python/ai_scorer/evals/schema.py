"""Golden fixture schema for AI scorer evals.

A fixture file is a JSON array of EvalCase objects.
Canonical fixtures live in data/canonical/ and are committed to the repo.
Candidate/proposed fixtures live in data/proposed/ and are gitignored.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from typing import Optional

SCHEMA_VERSION = "1"
EXTRACTOR_VERSION = "1"


@dataclass
class Provenance:
    source_db: str
    source_job_id: str
    extracted_at: str  # ISO-8601
    extractor_version: str = EXTRACTOR_VERSION


@dataclass
class EvalCase:
    """One (job, preference) evaluation case.

    expected_score and expected_score_available are None in candidate/proposed
    files (not yet labeled).  In canonical files both must be set.
    """

    case_id: str
    title: str
    description: str   # normalize_description_markdown() already applied
    location: str
    preference_key: str
    preference_guidance: str
    # None = not yet labeled (candidate); int 1..5 or None (N/A) in canonical
    expected_score: Optional[int]
    expected_score_available: Optional[bool]
    rationale: str
    tags: list
    schema_version: str = SCHEMA_VERSION
    provenance: Optional[Provenance] = None


def new_case_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_case(case: EvalCase) -> list:
    """Return list of error strings (empty = valid canonical case)."""
    errors = []
    if not case.case_id:
        errors.append("case_id is required")
    if not case.preference_key:
        errors.append("preference_key is required")
    if not case.preference_guidance:
        errors.append("preference_guidance is required")
    if case.expected_score_available is None:
        errors.append(
            "expected_score_available must be True or False "
            "(None means not yet labeled — only valid in candidate/proposed files)"
        )
    elif case.expected_score_available:
        if case.expected_score is None:
            errors.append("expected_score must be set when expected_score_available=True")
        elif case.expected_score not in range(1, 6):
            errors.append(f"expected_score must be 1..5, got {case.expected_score!r}")
    else:
        if case.expected_score is not None:
            errors.append(
                "expected_score must be None when expected_score_available=False (N/A case)"
            )
    return errors


def validate_fixtures(cases: list) -> list:
    """Validate all cases; returns combined list of error strings."""
    errors = []
    seen_ids: set = set()
    for i, case in enumerate(cases):
        for e in validate_case(case):
            errors.append(f"case[{i}] {case.case_id!r}: {e}")
        if case.case_id in seen_ids:
            errors.append(f"case[{i}]: duplicate case_id {case.case_id!r}")
        seen_ids.add(case.case_id)
    return errors


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _case_from_dict(item: dict) -> EvalCase:
    prov_raw = item.get("provenance")
    provenance = Provenance(**prov_raw) if prov_raw else None
    return EvalCase(
        case_id=item["case_id"],
        title=item.get("title", ""),
        description=item.get("description", ""),
        location=item.get("location", ""),
        preference_key=item["preference_key"],
        preference_guidance=item["preference_guidance"],
        expected_score=item.get("expected_score"),
        expected_score_available=item.get("expected_score_available"),
        rationale=item.get("rationale", ""),
        tags=item.get("tags", []),
        schema_version=item.get("schema_version", SCHEMA_VERSION),
        provenance=provenance,
    )


def load_fixtures(path: str) -> list:
    """Load and return a list of EvalCase from a JSON fixture file."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError(f"Expected JSON array in {path}, got {type(raw).__name__}")
    return [_case_from_dict(item) for item in raw]


def dump_fixtures(cases: list, path: str) -> None:
    """Write a list of EvalCase to a JSON fixture file."""
    data = [asdict(c) for c in cases]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
