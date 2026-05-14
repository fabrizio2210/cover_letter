"""
Report: generate machine-readable and human-readable artifacts from eval runs.

Produces three files in the output directory:
  summary.json    — top-level metrics, regression result
  per_case.json   — per-case predictions for both models
  report.md       — human-readable markdown report
"""
from __future__ import annotations

import dataclasses
import json
import os
from typing import Optional


def _metrics_to_dict(m) -> dict:
    return dataclasses.asdict(m)


def _regression_to_dict(r) -> dict:
    return dataclasses.asdict(r)


def write_summary(
    output_dir: str,
    baseline_model: str,
    candidate_model: str,
    fixture_path: str,
    fixture_count: int,
    run_at: str,
    baseline_metrics,
    candidate_metrics,
    regression,
) -> str:
    """Write summary.json; returns its path."""
    data = {
        "baseline_model": baseline_model,
        "candidate_model": candidate_model,
        "fixture_path": fixture_path,
        "fixture_count": fixture_count,
        "run_at": run_at,
        "baseline_metrics": _metrics_to_dict(baseline_metrics),
        "candidate_metrics": _metrics_to_dict(candidate_metrics),
        "regression": _regression_to_dict(regression),
    }
    path = os.path.join(output_dir, "summary.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def write_per_case(
    output_dir: str,
    cases,
    baseline_results: list,
    candidate_results: list,
) -> str:
    """Write per_case.json; returns its path."""
    baseline_map = {r.case_id: r for r in baseline_results}
    candidate_map = {r.case_id: r for r in candidate_results}

    rows = []
    for case in cases:
        b = baseline_map.get(case.case_id)
        c = candidate_map.get(case.case_id)
        rows.append({
            "case_id": case.case_id,
            "preference_key": case.preference_key,
            "tags": case.tags,
            "expected_score": case.expected_score,
            "expected_score_available": case.expected_score_available,
            "baseline": {
                "actual_score": b.actual_score if b else None,
                "actual_score_available": b.actual_score_available if b else None,
                "error": b.error if b else "missing",
            },
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


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _improvement_marker(delta: float, higher_is_better: bool = True) -> str:
    """Return an arrow marker for metric change."""
    if abs(delta) < 1e-4:
        return "→ (unchanged)"
    if higher_is_better:
        return f"↑ +{delta:.3f}" if delta > 0 else f"↓ {delta:.3f}"
    return f"↓ {delta:.3f}" if delta > 0 else f"↑ {abs(delta):.3f}"


def write_report(
    output_dir: str,
    baseline_model: str,
    candidate_model: str,
    fixture_path: str,
    fixture_count: int,
    run_at: str,
    baseline_metrics,
    candidate_metrics,
    regression,
    cases,
    baseline_results: list,
    candidate_results: list,
) -> str:
    """Write report.md; returns its path."""
    bm = baseline_metrics
    cm = candidate_metrics

    lines = [
        "# AI Scorer Eval Report",
        "",
        f"**Run at:** {run_at}",
        f"**Fixtures:** {fixture_path} ({fixture_count} cases)",
        f"**Baseline model:** `{baseline_model}`",
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
        "## Metrics Comparison",
        "",
        "| Metric | Baseline | Candidate | Delta |",
        "|---|---|---|---|",
        f"| Exact accuracy | {_fmt_pct(bm.exact_accuracy)} | {_fmt_pct(cm.exact_accuracy)} | {_improvement_marker(cm.exact_accuracy - bm.exact_accuracy)} |",
        f"| N/A precision | {_fmt_pct(bm.na_precision)} | {_fmt_pct(cm.na_precision)} | {_improvement_marker(cm.na_precision - bm.na_precision)} |",
        f"| N/A recall | {_fmt_pct(bm.na_recall)} | {_fmt_pct(cm.na_recall)} | {_improvement_marker(cm.na_recall - bm.na_recall)} |",
        f"| N/A F1 | {_fmt_pct(bm.na_f1)} | {_fmt_pct(cm.na_f1)} | {_improvement_marker(cm.na_f1 - bm.na_f1)} |",
        f"| MAE (scored only) | {bm.mean_abs_error:.3f} | {cm.mean_abs_error:.3f} | {_improvement_marker(bm.mean_abs_error - cm.mean_abs_error)} |",
        f"| Errors | {bm.errored}/{bm.total} | {cm.errored}/{cm.total} | |",
        "",
        "## Score Distribution",
        "",
        "| Score | Baseline | Candidate |",
        "|---|---|---|",
    ]
    for bucket in ["1", "2", "3", "4", "5", "na", "error"]:
        b_count = bm.score_distribution.get(bucket, 0)
        c_count = cm.score_distribution.get(bucket, 0)
        lines.append(f"| {bucket} | {b_count} | {c_count} |")

    lines.append("")

    # Top regressions (cases where candidate is worse)
    baseline_map = {r.case_id: r for r in baseline_results}
    candidate_map = {r.case_id: r for r in candidate_results}

    regressions = []
    improvements = []
    for case in cases:
        b = baseline_map.get(case.case_id)
        c = candidate_map.get(case.case_id)
        if not b or not c:
            continue
        b_correct = (
            b.error is None
            and b.actual_score_available == case.expected_score_available
            and (not case.expected_score_available or b.actual_score == case.expected_score)
        )
        c_correct = (
            c.error is None
            and c.actual_score_available == case.expected_score_available
            and (not case.expected_score_available or c.actual_score == case.expected_score)
        )
        if b_correct and not c_correct:
            regressions.append((case, b, c))
        elif not b_correct and c_correct:
            improvements.append((case, b, c))

    if regressions:
        lines += ["## Top Regressions (baseline correct, candidate wrong)", ""]
        for case, b, c in regressions[:10]:
            lines.append(
                f"- `{case.case_id[:8]}` pref={case.preference_key!r} "
                f"expected={'N/A' if not case.expected_score_available else case.expected_score} "
                f"baseline={'N/A' if not b.actual_score_available else b.actual_score} "
                f"candidate={'N/A' if not c.actual_score_available else c.actual_score}"
            )
        lines.append("")

    if improvements:
        lines += ["## Top Improvements (baseline wrong, candidate correct)", ""]
        for case, b, c in improvements[:10]:
            lines.append(
                f"- `{case.case_id[:8]}` pref={case.preference_key!r} "
                f"expected={'N/A' if not case.expected_score_available else case.expected_score} "
                f"baseline={'N/A' if not b.actual_score_available else b.actual_score} "
                f"candidate={'N/A' if not c.actual_score_available else c.actual_score}"
            )
        lines.append("")

    content = "\n".join(lines)
    path = os.path.join(output_dir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
