"""
Unified CLI for AI scorer evals.

Subcommands:
  extract   — Extract candidate fixture stubs from MongoDB
  label     — Propose labels using a live Ollama model
  eval      — Run eval: candidate model vs baseline on canonical fixtures

Usage (from repo root):
    python -m src.python.ai_scorer.evals.cli extract [options]
    python -m src.python.ai_scorer.evals.cli label   [options]
    python -m src.python.ai_scorer.evals.cli eval    [options]
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# Subcommand: extract
# ---------------------------------------------------------------------------

def _cmd_extract(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.evals.extractor import main as extract_main
    extract_main([
        "--mongo-uri", args.mongo_uri,
        "--global-db", args.global_db,
        "--output", args.output,
        "--preferences", args.preferences,
        "--limit", str(args.limit),
    ])
    return 0


# ---------------------------------------------------------------------------
# Subcommand: label
# ---------------------------------------------------------------------------

def _cmd_label(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.evals.labeler import main as label_main
    label_main([
        "--ollama-host", args.ollama_host,
        "--model", args.model,
        "--input", args.input,
        "--output", args.output,
    ])
    return 0


# ---------------------------------------------------------------------------
# Subcommand: eval
# ---------------------------------------------------------------------------

def _cmd_eval(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.evals.metrics import compute_metrics, check_regression
    from src.python.ai_scorer.evals.report import write_per_case, write_report, write_summary
    from src.python.ai_scorer.evals.runner import run_eval
    from src.python.ai_scorer.evals.schema import load_fixtures, validate_fixtures

    # Load and validate canonical fixtures
    cases = load_fixtures(args.fixtures)
    errors = validate_fixtures(cases)
    if errors:
        print("[eval] ERROR: fixture validation failed:")
        for e in errors:
            print(f"  {e}")
        return 2

    print(f"[eval] Loaded {len(cases)} canonical cases from {args.fixtures}")
    print(f"[eval] Baseline model : {args.baseline}")
    print(f"[eval] Candidate model: {args.candidate}")
    print(f"[eval] Ollama host    : {args.ollama_host}")

    # --- Baseline run ---
    print(f"\n[eval] Running baseline ({args.baseline}) ...")
    baseline_results = run_eval(
        cases=cases,
        ollama_host=args.ollama_host,
        model_name=args.baseline,
        verbose=args.verbose,
    )

    # --- Candidate run ---
    print(f"\n[eval] Running candidate ({args.candidate}) ...")
    candidate_results = run_eval(
        cases=cases,
        ollama_host=args.ollama_host,
        model_name=args.candidate,
        verbose=args.verbose,
    )

    # --- Metrics ---
    baseline_metrics = compute_metrics(baseline_results)
    candidate_metrics = compute_metrics(candidate_results)
    regression = check_regression(baseline_metrics, candidate_metrics)

    # --- Artifacts ---
    run_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    os.makedirs(args.output_dir, exist_ok=True)

    summary_path = write_summary(
        output_dir=args.output_dir,
        baseline_model=args.baseline,
        candidate_model=args.candidate,
        fixture_path=args.fixtures,
        fixture_count=len(cases),
        run_at=run_at,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        regression=regression,
    )
    per_case_path = write_per_case(
        output_dir=args.output_dir,
        cases=cases,
        baseline_results=baseline_results,
        candidate_results=candidate_results,
    )
    report_path = write_report(
        output_dir=args.output_dir,
        baseline_model=args.baseline,
        candidate_model=args.candidate,
        fixture_path=args.fixtures,
        fixture_count=len(cases),
        run_at=run_at,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        regression=regression,
        cases=cases,
        baseline_results=baseline_results,
        candidate_results=candidate_results,
    )

    print(f"\n[eval] Artifacts written to {args.output_dir}:")
    print(f"  {summary_path}")
    print(f"  {per_case_path}")
    print(f"  {report_path}")

    # Print inline summary
    bm = baseline_metrics
    cm = candidate_metrics
    print("\n[eval] Metrics summary:")
    print(f"  exact_accuracy   : baseline={bm.exact_accuracy:.3f}  candidate={cm.exact_accuracy:.3f}")
    print(f"  na_f1            : baseline={bm.na_f1:.3f}  candidate={cm.na_f1:.3f}")
    print(f"  mean_abs_error   : baseline={bm.mean_abs_error:.3f}  candidate={cm.mean_abs_error:.3f}")

    if regression.passed:
        print("\n[eval] Regression gate: PASSED")
        return 0
    else:
        print("\n[eval] Regression gate: FAILED")
        for reason in regression.reasons:
            print(f"  - {reason}")
        return 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _default_preferences_path() -> str:
    return os.path.join(os.path.dirname(__file__), "data", "eval_preferences.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.python.ai_scorer.evals.cli",
        description="AI scorer evaluation tools",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # --- extract ---
    p_extract = sub.add_parser("extract", help="Extract candidate fixtures from MongoDB")
    p_extract.add_argument(
        "--mongo-uri",
        default=os.environ.get("MONGO_HOST", "mongodb://localhost:27017/"),
    )
    p_extract.add_argument(
        "--global-db",
        default=os.environ.get("DB_NAME", "cover_letter_global"),
    )
    p_extract.add_argument(
        "--output",
        default="src/python/ai_scorer/evals/data/proposed/candidates.json",
    )
    p_extract.add_argument(
        "--preferences",
        default=_default_preferences_path(),
    )
    p_extract.add_argument("--limit", type=int, default=50)

    # --- label ---
    p_label = sub.add_parser("label", help="Propose labels using a live Ollama model")
    p_label.add_argument(
        "--ollama-host",
        default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    )
    p_label.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b"),
    )
    p_label.add_argument(
        "--input",
        default="src/python/ai_scorer/evals/data/proposed/candidates.json",
    )
    p_label.add_argument(
        "--output",
        default="src/python/ai_scorer/evals/data/proposed/labeled.json",
    )

    # --- eval ---
    p_eval = sub.add_parser("eval", help="Run eval: candidate model vs baseline")
    p_eval.add_argument(
        "--ollama-host",
        default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    )
    p_eval.add_argument(
        "--baseline",
        default=os.environ.get("EVAL_BASELINE_MODEL", "qwen2.5:1.5b"),
        help="Baseline model name (default: EVAL_BASELINE_MODEL env or qwen2.5:1.5b)",
    )
    p_eval.add_argument(
        "--candidate",
        default=os.environ.get("EVAL_CANDIDATE_MODEL", "qwen2.5:1.5b"),
        help="Candidate model name (default: EVAL_CANDIDATE_MODEL env or qwen2.5:1.5b)",
    )
    p_eval.add_argument(
        "--fixtures",
        default="src/python/ai_scorer/evals/data/canonical/v1.json",
        help="Path to canonical fixture file",
    )
    p_eval.add_argument(
        "--output-dir",
        default="eval-results",
        help="Directory for output artifacts (created if missing)",
    )
    p_eval.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-case scoring progress",
    )

    return parser


def main(argv: list = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "extract": _cmd_extract,
        "label": _cmd_label,
        "eval": _cmd_eval,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
