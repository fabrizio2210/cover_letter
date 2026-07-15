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

from src.python.ai_scorer.job_fingerprint import fingerprint_basis, validate_fingerprint

SCHEMA_VERSION = "2"
FIXTURE_FORMAT_VERSION = "2"
EXTRACTOR_VERSION = "1"


@dataclass
class FixtureMeta:
    """Metadata stored at the top of a canonical fixture file (v2 format).

    fixture_model   — the model name used when labeling / validating these cases.
    reference_metrics — EvalMetrics-compatible dict computed from that model run;
                        used as the regression baseline so no second model run is
                        needed at eval time.
    """
    fixture_model: str
    reference_metrics: dict  # keys mirror EvalMetrics fields
    format_version: str = FIXTURE_FORMAT_VERSION


@dataclass
class Provenance:
    source_db: str
    extracted_at: str  # ISO-8601
    extractor_version: str = EXTRACTOR_VERSION


@dataclass
class EvalCase:
    """One (job, preference) evaluation case.

    expected_score and expected_score_available are None in candidate/proposed
    files (not yet labeled).  In canonical files both must be set.
    """

    case_id: str
    job_fingerprint: str
    fingerprint_basis: str
    title: str
    description: str   # normalize_description_markdown() already applied
    location: str
    preference_key: str
    preference_guidance: str
    # None = not yet labeled (candidate); int 0..5 or None (N/A) in canonical
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
    fingerprint_error = validate_fingerprint(case.job_fingerprint)
    if fingerprint_error:
        errors.append(fingerprint_error)
    elif case.fingerprint_basis != fingerprint_basis(case.job_fingerprint):
        errors.append("fingerprint_basis must match job_fingerprint")
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
        elif case.expected_score not in range(0, 6):
            errors.append(f"expected_score must be 0..5, got {case.expected_score!r}")
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
    provenance = (
        Provenance(
            source_db=prov_raw.get("source_db", ""),
            extracted_at=prov_raw.get("extracted_at", ""),
            extractor_version=prov_raw.get("extractor_version", EXTRACTOR_VERSION),
        )
        if prov_raw
        else None
    )
    return EvalCase(
        case_id=item["case_id"],
        job_fingerprint=item.get("job_fingerprint", ""),
        fingerprint_basis=item.get("fingerprint_basis", ""),
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


def _cases_list_from_raw(raw, path: str) -> list:
    """Extract the cases list from either v1 (bare array) or v2 (object) format."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and "cases" in raw:
        return raw["cases"]
    raise ValueError(f"Unexpected fixture format in {path}: expected array or {{meta, cases}} object")


def load_fixtures(path: str) -> list:
    """Load and return a list of EvalCase from a JSON fixture file.

    Supports both v1 (bare JSON array) and v2 ({"meta": ..., "cases": [...]}) formats.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return [_case_from_dict(item) for item in _cases_list_from_raw(raw, path)]


def load_fixture_meta(path: str) -> Optional[FixtureMeta]:
    """Return FixtureMeta from a v2 fixture file, or None for v1 (bare array) files."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "meta" in raw:
        m = raw["meta"]
        return FixtureMeta(
            fixture_model=m.get("fixture_model", ""),
            reference_metrics=m.get("reference_metrics", {}),
            format_version=m.get("format_version", FIXTURE_FORMAT_VERSION),
        )
    return None


def dump_fixtures(cases: list, path: str, meta: Optional[FixtureMeta] = None) -> None:
    """Write a list of EvalCase to a JSON fixture file.

    If *meta* is provided the file is written in v2 format ({"meta": ..., "cases": [...]}).
    Otherwise the legacy v1 bare-array format is used (for proposed/labeled intermediates).
    """
    if meta is not None:
        data: object = {
            "meta": asdict(meta),
            "cases": [asdict(c) for c in cases],
        }
    else:
        data = [asdict(c) for c in cases]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
