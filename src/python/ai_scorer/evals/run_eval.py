from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ollama import Client

from src.python.ai_scorer.ai_scorer import score_preference
from src.python.ai_scorer.evals.core import (
    EvalThresholds,
    compare_candidate_vs_baseline,
    compute_metrics,
    load_and_validate_cases,
)


def run_model(
    *,
    client: Client,
    model_name: str,
    cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for case in cases:
        expected = case["expected"]
        result = score_preference(
            ollama_client=client,
            model_name=model_name,
            test_mode=False,
            job_id=case["case_id"],
            preference={
                "key": case["preference"]["key"],
                "guidance": case["preference"]["guidance"],
                "weight": 1,
                "enabled": True,
            },
            job_doc=case["job"],
            company_doc={},
            identity_doc={},
        )

        rows.append(
            {
                "case_id": case["case_id"],
                "expected_score_available": bool(expected["score_available"]),
                "expected_score": expected["score"],
                "predicted_score_available": bool(result["score_available"]),
                "predicted_score": result["score"],
                "raw": result,
            }
        )

    metrics = compute_metrics(rows)
    return rows, metrics


def build_report(
    *,
    baseline_model: str,
    candidate_model: str,
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    decision: dict[str, Any],
) -> str:
    lines = [
        "# AI Scorer Golden Eval Report",
        "",
        f"- Baseline model: {baseline_model}",
        f"- Candidate model: {candidate_model}",
        f"- Overall passed: {decision['overall_passed']}",
        "",
        "## Metrics",
        "",
        f"- Baseline exact accuracy: {baseline_metrics['exact_accuracy']:.4f}",
        f"- Candidate exact accuracy: {candidate_metrics['exact_accuracy']:.4f}",
        f"- Baseline N/A F1: {baseline_metrics['na']['f1']:.4f}",
        f"- Candidate N/A F1: {candidate_metrics['na']['f1']:.4f}",
        f"- Baseline MAE: {baseline_metrics['mean_abs_error']:.4f}",
        f"- Candidate MAE: {candidate_metrics['mean_abs_error']:.4f}",
        "",
        "## Regression Checks",
        "",
    ]

    for name, value in decision["checks"].items():
        lines.append(
            f"- {name}: value={value['value']:.4f}, threshold={value['threshold']:.4f}, passed={value['passed']}"
        )

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ai_scorer golden evaluation")
    parser.add_argument("--fixtures", required=True, help="Path to canonical golden fixtures JSON")
    parser.add_argument("--baseline-model", required=True, help="Baseline model name")
    parser.add_argument("--candidate-model", required=True, help="Candidate model name")
    parser.add_argument("--ollama-host", default="http://localhost:11434", help="Ollama host URL")
    parser.add_argument("--out-dir", required=True, help="Output directory for eval artifacts")
    parser.add_argument("--exact-accuracy-drop", type=float, default=0.03)
    parser.add_argument("--na-f1-drop", type=float, default=0.05)
    parser.add_argument("--mean-abs-error-increase", type=float, default=0.20)

    args = parser.parse_args()

    cases = load_and_validate_cases(args.fixtures)
    thresholds = EvalThresholds(
        exact_accuracy_drop=args.exact_accuracy_drop,
        na_f1_drop=args.na_f1_drop,
        mean_abs_error_increase=args.mean_abs_error_increase,
    )

    client = Client(host=args.ollama_host)

    baseline_rows, baseline_metrics = run_model(client=client, model_name=args.baseline_model, cases=cases)
    candidate_rows, candidate_metrics = run_model(client=client, model_name=args.candidate_model, cases=cases)
    decision = compare_candidate_vs_baseline(baseline_metrics, candidate_metrics, thresholds)

    combined_rows = []
    baseline_by_id = {row["case_id"]: row for row in baseline_rows}
    candidate_by_id = {row["case_id"]: row for row in candidate_rows}
    for case in cases:
        case_id = case["case_id"]
        combined_rows.append(
            {
                "case_id": case_id,
                "expected": baseline_by_id[case_id]["expected_score"],
                "expected_score_available": baseline_by_id[case_id]["expected_score_available"],
                "baseline_prediction": baseline_by_id[case_id]["predicted_score"],
                "baseline_score_available": baseline_by_id[case_id]["predicted_score_available"],
                "candidate_prediction": candidate_by_id[case_id]["predicted_score"],
                "candidate_score_available": candidate_by_id[case_id]["predicted_score_available"],
            }
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "baseline_model": args.baseline_model,
        "candidate_model": args.candidate_model,
        "thresholds": {
            "exact_accuracy_drop": thresholds.exact_accuracy_drop,
            "na_f1_drop": thresholds.na_f1_drop,
            "mean_abs_error_increase": thresholds.mean_abs_error_increase,
        },
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "decision": decision,
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
    (out_dir / "case_diffs.json").write_text(json.dumps(combined_rows, indent=2, ensure_ascii=True), encoding="utf-8")
    (out_dir / "report.md").write_text(
        build_report(
            baseline_model=args.baseline_model,
            candidate_model=args.candidate_model,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            decision=decision,
        ),
        encoding="utf-8",
    )

    print(json.dumps({"status": "ok", "overall_passed": decision["overall_passed"], "out_dir": str(out_dir)}))
    return 0 if decision["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
