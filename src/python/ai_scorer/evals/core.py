from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LABEL_NA = "N/A"


@dataclass
class EvalThresholds:
    exact_accuracy_drop: float = 0.03
    na_f1_drop: float = 0.05
    mean_abs_error_increase: float = 0.20


class FixtureValidationError(ValueError):
    pass


def _is_valid_score(score: Any) -> bool:
    return isinstance(score, int) and 1 <= score <= 5


def _required_path(dct: dict[str, Any], path: str) -> Any:
    current: Any = dct
    for chunk in path.split("."):
        if not isinstance(current, dict) or chunk not in current:
            raise FixtureValidationError(f"Missing required field: {path}")
        current = current[chunk]
    return current


def validate_case(case: dict[str, Any]) -> None:
    case_id = _required_path(case, "case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise FixtureValidationError("case_id must be a non-empty string")

    title = _required_path(case, "job.title")
    description = _required_path(case, "job.description")
    location = _required_path(case, "job.location")
    preference_key = _required_path(case, "preference.key")
    preference_guidance = _required_path(case, "preference.guidance")
    expected_available = _required_path(case, "expected.score_available")
    expected_score = _required_path(case, "expected.score")

    if not isinstance(title, str):
        raise FixtureValidationError("job.title must be a string")
    if not isinstance(description, str):
        raise FixtureValidationError("job.description must be a string")
    if not isinstance(location, str):
        raise FixtureValidationError("job.location must be a string")
    if not isinstance(preference_key, str) or not preference_key.strip():
        raise FixtureValidationError("preference.key must be a non-empty string")
    if not isinstance(preference_guidance, str) or not preference_guidance.strip():
        raise FixtureValidationError("preference.guidance must be a non-empty string")
    if not isinstance(expected_available, bool):
        raise FixtureValidationError("expected.score_available must be a boolean")

    if expected_available:
        if not _is_valid_score(expected_score):
            raise FixtureValidationError("expected.score must be integer in range 1..5 when score_available=true")
    else:
        if expected_score is not None:
            raise FixtureValidationError("expected.score must be null when score_available=false")

    tags = case.get("tags", [])
    if tags is not None:
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise FixtureValidationError("tags must be a list of strings")


def load_and_validate_cases(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise FixtureValidationError("fixtures root must be a list")

    seen_ids: set[str] = set()
    for case in data:
        if not isinstance(case, dict):
            raise FixtureValidationError("each fixture case must be an object")
        validate_case(case)
        case_id = str(case["case_id"])
        if case_id in seen_ids:
            raise FixtureValidationError(f"duplicate case_id: {case_id}")
        seen_ids.add(case_id)

    return data


def to_label(score_available: bool, score: int | None) -> str:
    if not score_available:
        return LABEL_NA
    if score is None:
        return LABEL_NA
    return str(score)


def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if total == 0:
        raise ValueError("rows cannot be empty")

    exact_matches = 0
    na_tp = 0
    na_fp = 0
    na_fn = 0

    mae_values: list[float] = []
    labels = ["1", "2", "3", "4", "5", LABEL_NA]
    confusion = {expected: {predicted: 0 for predicted in labels} for expected in labels}

    for row in rows:
        expected_available = bool(row["expected_score_available"])
        predicted_available = bool(row["predicted_score_available"])
        expected_score = row.get("expected_score")
        predicted_score = row.get("predicted_score")

        expected_label = to_label(expected_available, expected_score)
        predicted_label = to_label(predicted_available, predicted_score)
        confusion[expected_label][predicted_label] += 1

        if expected_available == predicted_available:
            if not expected_available:
                exact_matches += 1
            elif expected_score == predicted_score:
                exact_matches += 1

        expected_is_na = not expected_available
        predicted_is_na = not predicted_available
        if expected_is_na and predicted_is_na:
            na_tp += 1
        elif not expected_is_na and predicted_is_na:
            na_fp += 1
        elif expected_is_na and not predicted_is_na:
            na_fn += 1

        if expected_available:
            if predicted_available and isinstance(expected_score, int) and isinstance(predicted_score, int):
                mae_values.append(abs(expected_score - predicted_score))
            else:
                # Missing a score where one is expected is a strong regression.
                mae_values.append(5.0)

    na_precision = na_tp / (na_tp + na_fp) if (na_tp + na_fp) > 0 else 0.0
    na_recall = na_tp / (na_tp + na_fn) if (na_tp + na_fn) > 0 else 0.0
    na_f1 = 0.0
    if (na_precision + na_recall) > 0:
        na_f1 = (2.0 * na_precision * na_recall) / (na_precision + na_recall)

    mean_abs_error = sum(mae_values) / len(mae_values) if mae_values else 0.0

    return {
        "count": total,
        "exact_accuracy": exact_matches / total,
        "na": {
            "precision": na_precision,
            "recall": na_recall,
            "f1": na_f1,
            "tp": na_tp,
            "fp": na_fp,
            "fn": na_fn,
        },
        "mean_abs_error": mean_abs_error,
        "confusion": confusion,
    }


def compare_candidate_vs_baseline(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    thresholds: EvalThresholds,
) -> dict[str, Any]:
    exact_accuracy_drop = float(baseline_metrics["exact_accuracy"]) - float(candidate_metrics["exact_accuracy"])
    na_f1_drop = float(baseline_metrics["na"]["f1"]) - float(candidate_metrics["na"]["f1"])
    mean_abs_error_increase = float(candidate_metrics["mean_abs_error"]) - float(
        baseline_metrics["mean_abs_error"]
    )

    checks = {
        "exact_accuracy_drop": {
            "value": exact_accuracy_drop,
            "threshold": thresholds.exact_accuracy_drop,
            "passed": exact_accuracy_drop <= thresholds.exact_accuracy_drop,
        },
        "na_f1_drop": {
            "value": na_f1_drop,
            "threshold": thresholds.na_f1_drop,
            "passed": na_f1_drop <= thresholds.na_f1_drop,
        },
        "mean_abs_error_increase": {
            "value": mean_abs_error_increase,
            "threshold": thresholds.mean_abs_error_increase,
            "passed": mean_abs_error_increase <= thresholds.mean_abs_error_increase,
        },
    }

    overall_passed = all(check["passed"] for check in checks.values())

    return {
        "overall_passed": overall_passed,
        "checks": checks,
    }
