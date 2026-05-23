"""
Metric computation for AI scorer eval results.

Each run produces a set of CaseResult objects; this module aggregates them
into EvalMetrics and performs regression comparisons against a baseline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    case_id: str
    model: str
    expected_score: Optional[int]
    expected_score_available: Optional[bool]
    actual_score: Optional[int]
    actual_score_available: Optional[bool]
    error: Optional[str] = None  # set when model call threw an exception
    latency_ms: Optional[float] = None  # wall-clock time for the model call


@dataclass
class EvalMetrics:
    total: int
    errored: int
    # Fraction of cases where (actual_score, actual_score_available) exactly
    # matches (expected_score, expected_score_available).
    exact_accuracy: float
    # N/A prediction quality
    na_precision: float
    na_recall: float
    na_f1: float
    # MAE only over cases where expected_score_available=True AND no error
    mean_abs_error: float
    # Count of actual score predictions (keys: "0".."5", "na", "error")
    score_distribution: dict = field(default_factory=dict)
    # Per-case latency statistics (ms); None when no timing data is available
    mean_latency_ms: Optional[float] = None
    p50_latency_ms: Optional[float] = None
    p95_latency_ms: Optional[float] = None
    total_latency_ms: Optional[float] = None


@dataclass
class RegressionResult:
    passed: bool
    reasons: list = field(default_factory=list)
    exact_accuracy_drop: float = 0.0
    na_f1_drop: float = 0.0
    mean_abs_error_increase: float = 0.0


# ---------------------------------------------------------------------------
# Agreed thresholds (from session decisions)
# ---------------------------------------------------------------------------
EXACT_ACCURACY_DROP_THRESHOLD = 0.03
NA_F1_DROP_THRESHOLD = 0.05
MAE_INCREASE_THRESHOLD = 0.20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _percentile(values: list, p: float) -> float:
    """Linear-interpolation percentile over a non-empty list of floats."""
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# ---------------------------------------------------------------------------
# Core metric computation
# ---------------------------------------------------------------------------

def compute_metrics(results: list) -> EvalMetrics:
    """Compute EvalMetrics from a list of CaseResult."""
    total = len(results)
    if total == 0:
        return EvalMetrics(
            total=0, errored=0, exact_accuracy=0.0,
            na_precision=0.0, na_recall=0.0, na_f1=0.0,
            mean_abs_error=0.0,
        )

    errored = sum(1 for r in results if r.error is not None)

    # --- Exact accuracy ---
    exact_matches = 0
    for r in results:
        if r.error is not None:
            continue
        if r.actual_score_available == r.expected_score_available:
            if not r.expected_score_available:
                # Both N/A → match
                exact_matches += 1
            elif r.actual_score == r.expected_score:
                exact_matches += 1

    exact_accuracy = exact_matches / total

    # --- N/A precision / recall / F1 ---
    # Positive class = N/A (score_available=False)
    true_positives = 0   # predicted N/A, expected N/A
    false_positives = 0  # predicted N/A, expected scored
    false_negatives = 0  # predicted scored, expected N/A

    for r in results:
        if r.error is not None:
            # Treat model errors as predicted N/A for conservative measurement
            predicted_na = True
        else:
            predicted_na = not r.actual_score_available

        expected_na = not r.expected_score_available if r.expected_score_available is not None else False

        if predicted_na and expected_na:
            true_positives += 1
        elif predicted_na and not expected_na:
            false_positives += 1
        elif not predicted_na and expected_na:
            false_negatives += 1

    na_precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives) > 0 else 0.0
    )
    na_recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives) > 0 else 0.0
    )
    na_f1 = (
        2 * na_precision * na_recall / (na_precision + na_recall)
        if (na_precision + na_recall) > 0 else 0.0
    )

    # --- MAE ---
    mae_errors = []
    for r in results:
        if (
            r.error is None
            and r.expected_score_available
            and r.actual_score_available
            and r.expected_score is not None
            and r.actual_score is not None
        ):
            mae_errors.append(abs(r.expected_score - r.actual_score))

    mean_abs_error = sum(mae_errors) / len(mae_errors) if mae_errors else 0.0

    # --- Score distribution ---
    dist: dict = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "na": 0, "error": 0}
    for r in results:
        if r.error is not None:
            dist["error"] += 1
        elif not r.actual_score_available:
            dist["na"] += 1
        elif r.actual_score is not None and 0 <= r.actual_score <= 5:
            dist[str(r.actual_score)] += 1
        else:
            dist["error"] += 1

    # --- Latency stats ---
    latencies = [
        r.latency_ms for r in results
        if r.latency_ms is not None and r.error is None
    ]
    if latencies:
        mean_latency_ms = sum(latencies) / len(latencies)
        p50_latency_ms = _percentile(latencies, 50)
        p95_latency_ms = _percentile(latencies, 95)
        total_latency_ms = sum(latencies)
    else:
        mean_latency_ms = p50_latency_ms = p95_latency_ms = total_latency_ms = None

    return EvalMetrics(
        total=total,
        errored=errored,
        exact_accuracy=exact_accuracy,
        na_precision=na_precision,
        na_recall=na_recall,
        na_f1=na_f1,
        mean_abs_error=mean_abs_error,
        score_distribution=dist,
        mean_latency_ms=mean_latency_ms,
        p50_latency_ms=p50_latency_ms,
        p95_latency_ms=p95_latency_ms,
        total_latency_ms=total_latency_ms,
    )


# ---------------------------------------------------------------------------
# Regression check
# ---------------------------------------------------------------------------

def check_regression(
    baseline: EvalMetrics,
    candidate: EvalMetrics,
    exact_accuracy_drop_threshold: float = EXACT_ACCURACY_DROP_THRESHOLD,
    na_f1_drop_threshold: float = NA_F1_DROP_THRESHOLD,
    mae_increase_threshold: float = MAE_INCREASE_THRESHOLD,
) -> RegressionResult:
    """Compare candidate vs baseline metrics and return a RegressionResult."""
    reasons = []

    exact_drop = baseline.exact_accuracy - candidate.exact_accuracy
    na_f1_drop = baseline.na_f1 - candidate.na_f1
    mae_increase = candidate.mean_abs_error - baseline.mean_abs_error

    if exact_drop > exact_accuracy_drop_threshold:
        reasons.append(
            f"exact_accuracy dropped {exact_drop:.3f} "
            f"(threshold={exact_accuracy_drop_threshold}): "
            f"baseline={baseline.exact_accuracy:.3f} "
            f"candidate={candidate.exact_accuracy:.3f}"
        )
    if na_f1_drop > na_f1_drop_threshold:
        reasons.append(
            f"na_f1 dropped {na_f1_drop:.3f} "
            f"(threshold={na_f1_drop_threshold}): "
            f"baseline={baseline.na_f1:.3f} candidate={candidate.na_f1:.3f}"
        )
    if mae_increase > mae_increase_threshold:
        reasons.append(
            f"mean_abs_error increased {mae_increase:.3f} "
            f"(threshold={mae_increase_threshold}): "
            f"baseline={baseline.mean_abs_error:.3f} "
            f"candidate={candidate.mean_abs_error:.3f}"
        )

    return RegressionResult(
        passed=len(reasons) == 0,
        reasons=reasons,
        exact_accuracy_drop=exact_drop,
        na_f1_drop=na_f1_drop,
        mean_abs_error_increase=mae_increase,
    )
