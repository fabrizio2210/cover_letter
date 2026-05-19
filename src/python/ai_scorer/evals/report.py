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


def write_summary(
    output_dir: str,
    fixture_source: str,
    fixture_model: str,
    fixture_count: int,
    candidate_model: str,
    run_at: str,
    candidate_metrics,
    regression,
) -> str:
    """Write summary.json; returns its path."""
    data = {
        "fixture_source": fixture_source,
        "fixture_model": fixture_model,
        "fixture_count": fixture_count,
        "candidate_model": candidate_model,
        "run_at": run_at,
        "candidate_metrics": _metrics_to_dict(candidate_metrics),
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
        "| Score | Candidate |",
        "|---|---|",
    ]
    for bucket in ["1", "2", "3", "4", "5", "na", "error"]:
        c_count = cm.score_distribution.get(bucket, 0)
        lines.append(f"| {bucket} | {c_count} |")

    lines.append("")

    # Cases where candidate missed the golden label
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
