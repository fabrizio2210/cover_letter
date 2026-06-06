"""
Report: generate machine-readable and human-readable artifacts from eval runs.

Produces three files in the output directory:
  summary.json    — fixture metadata, candidate metrics, regression result
  per_case.json   — per-case predictions vs golden expected
  report.md       — human-readable markdown report
"""
from __future__ import annotations

import dataclasses
import json
import os
from typing import Optional


def _metrics_to_dict(m) -> dict:
    return dataclasses.asdict(m)


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _golden_score_distribution(cases) -> dict:
    """Build score distribution from golden fixture cases."""
    dist = {str(i): 0 for i in range(6)}
    dist["na"] = 0
    dist["error"] = 0

    for case in cases:
        if case.expected_score_available and case.expected_score is not None:
            bucket = str(case.expected_score)
            if bucket in dist:
                dist[bucket] += 1
            else:
                dist["error"] += 1
        else:
            dist["na"] += 1

    return dist


def write_summary(
    output_dir: str,
    fixture_source: str,
    fixture_model: str,
    fixture_count: int,
    cases,
    candidate_model: str,
    run_at: str,
    candidate_metrics,
    regression,
    reference_metrics_dict: Optional[dict] = None,
) -> str:
    """Write summary.json; returns its path."""
    cm_dict = _metrics_to_dict(candidate_metrics)

    timing: dict = {}
    if candidate_metrics.mean_latency_ms is not None:
        timing["candidate"] = {
            "mean_ms": round(candidate_metrics.mean_latency_ms, 1),
            "p50_ms": round(candidate_metrics.p50_latency_ms, 1),
            "p95_ms": round(candidate_metrics.p95_latency_ms, 1),
            "total_ms": round(candidate_metrics.total_latency_ms, 1),
        }
    if reference_metrics_dict:
        baseline_mean = reference_metrics_dict.get("mean_latency_ms")
        if baseline_mean is not None:
            timing["baseline"] = {
                "mean_ms": round(baseline_mean, 1),
                "p50_ms": round(reference_metrics_dict.get("p50_latency_ms", 0.0), 1),
                "p95_ms": round(reference_metrics_dict.get("p95_latency_ms", 0.0), 1),
                "total_ms": round(reference_metrics_dict.get("total_latency_ms", 0.0), 1),
            }

    data = {
        "fixture_source": fixture_source,
        "fixture_model": fixture_model,
        "fixture_count": fixture_count,
        "candidate_model": candidate_model,
        "run_at": run_at,
        "candidate_metrics": cm_dict,
        "golden_metrics": {
            "score_distribution": _golden_score_distribution(cases),
        },
        "timing": timing,
        "regression": {
            "passed": regression.passed,
            "reasons": regression.reasons,
            "exact_accuracy": candidate_metrics.exact_accuracy,
            "na_f1": candidate_metrics.na_f1,
            "mean_abs_error": candidate_metrics.mean_abs_error,
        },
    }
    path = os.path.join(output_dir, "summary.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def write_per_case(
    output_dir: str,
    cases,
    candidate_results: list,
) -> str:
    """Write per_case.json; returns its path."""
    candidate_map = {r.case_id: r for r in candidate_results}

    rows = []
    for case in cases:
        c = candidate_map.get(case.case_id)
        rows.append({
            "case_id": case.case_id,
            "preference_key": case.preference_key,
            "tags": case.tags,
            "expected_score": case.expected_score,
            "expected_score_available": case.expected_score_available,
            "candidate": {
                "actual_score": c.actual_score if c else None,
                "actual_score_available": c.actual_score_available if c else None,
                "error": c.error if c else "missing",
            },
        })

    path = os.path.join(output_dir, "per_case.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    return path


def write_report(
    output_dir: str,
    fixture_source: str,
    fixture_model: str,
    fixture_count: int,
    candidate_model: str,
    run_at: str,
    candidate_metrics,
    regression,
    cases,
    candidate_results: list,
    reference_metrics_dict: Optional[dict] = None,
) -> str:
    """Write report.md; returns its path."""
    cm = candidate_metrics

    lines = [
        "# AI Scorer Eval Report",
        "",
        f"**Run at:** {run_at}",
        f"**Fixtures:** {fixture_source} ({fixture_count} cases)",
        f"**Fixture model (golden):** `{fixture_model}`",
        f"**Candidate model:** `{candidate_model}`",
        "",
        "## Regression Gate",
        "",
    ]

    if regression.passed:
        lines.append("**PASSED** — candidate meets all regression thresholds.")
    else:
        lines.append("**FAILED** — candidate violated one or more thresholds:")
        for reason in regression.reasons:
            lines.append(f"- {reason}")
    lines.append("")

    lines += [
        "## Candidate Metrics vs Golden Set",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Exact accuracy | {_fmt_pct(cm.exact_accuracy)} |",
        f"| N/A precision | {_fmt_pct(cm.na_precision)} |",
        f"| N/A recall | {_fmt_pct(cm.na_recall)} |",
        f"| N/A F1 | {_fmt_pct(cm.na_f1)} |",
        f"| MAE (scored only) | {cm.mean_abs_error:.3f} |",
        f"| Errors | {cm.errored}/{cm.total} |",
        "",
        "## Score Distribution",
        "",
        "| Score | Candidate | Golden |",
        "|---|---|---|",
    ]
    golden_dist = _golden_score_distribution(cases)
    for bucket in ["0", "1", "2", "3", "4", "5", "na", "error"]:
        c_count = cm.score_distribution.get(bucket, 0)
        g_count = golden_dist.get(bucket, 0)
        lines.append(f"| {bucket} | {c_count} | {g_count} |")

    lines.append("")

    # --- Timing section ---
    cm_has_timing = cm.mean_latency_ms is not None
    baseline_mean = reference_metrics_dict.get("mean_latency_ms") if reference_metrics_dict else None
    baseline_has_timing = baseline_mean is not None

    if cm_has_timing:
        def _fmt_ms(v):
            return f"{v:,.1f} ms" if v is not None else "N/A"

        if baseline_has_timing:
            lines += [
                "## Timing",
                "",
                "| Metric | Candidate | Baseline (golden) | Delta |",
                "|---|---|---|---|",
            ]
            for label, c_val, b_key in [
                ("Mean", cm.mean_latency_ms, "mean_latency_ms"),
                ("P50",  cm.p50_latency_ms,  "p50_latency_ms"),
                ("P95",  cm.p95_latency_ms,  "p95_latency_ms"),
                ("Total", cm.total_latency_ms, "total_latency_ms"),
            ]:
                b_val = reference_metrics_dict.get(b_key)
                delta_str = (
                    f"+{c_val - b_val:,.1f} ms" if c_val - b_val >= 0
                    else f"{c_val - b_val:,.1f} ms"
                ) if b_val is not None else "N/A"
                lines.append(
                    f"| {label} | {_fmt_ms(c_val)} | {_fmt_ms(b_val)} | {delta_str} |"
                )
        else:
            lines += [
                "## Timing",
                "",
                "| Metric | Candidate |",
                "|---|---|",
                f"| Mean  | {_fmt_ms(cm.mean_latency_ms)} |",
                f"| P50   | {_fmt_ms(cm.p50_latency_ms)} |",
                f"| P95   | {_fmt_ms(cm.p95_latency_ms)} |",
                f"| Total | {_fmt_ms(cm.total_latency_ms)} |",
            ]
        lines.append("")
    candidate_map = {r.case_id: r for r in candidate_results}

    misses = []
    for case in cases:
        c = candidate_map.get(case.case_id)
        if not c:
            continue
        c_correct = (
            c.error is None
            and c.actual_score_available == case.expected_score_available
            and (not case.expected_score_available or c.actual_score == case.expected_score)
        )
        if not c_correct:
            misses.append((case, c))

    if misses:
        lines += ["## Mismatches (candidate differs from golden)", ""]
        for case, c in misses[:20]:
            lines.append(
                f"- `{case.case_id[:8]}` pref={case.preference_key!r} "
                f"expected={'N/A' if not case.expected_score_available else case.expected_score} "
                f"candidate={'ERROR' if c.error else ('N/A' if not c.actual_score_available else c.actual_score)}"
            )
        lines.append("")

    content = "\n".join(lines)
    path = os.path.join(output_dir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
