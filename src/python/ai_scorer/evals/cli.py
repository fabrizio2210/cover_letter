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
import dataclasses
import datetime
import os
import sys

from src.python.ai_scorer.evals.profile import (
    PRODUCTION_PROFILE_NAME,
    PRODUCTION_REFERENCE_MODEL,
    apply_profile,
    build_run_configuration,
    fixture_fingerprint,
    metrics_fingerprint,
    reference_configuration_mismatches,
)


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name, "") or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes"}

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
    apply_profile(args.profile)
    run_configuration = build_run_configuration(args.profile, args.candidate)
    default_profile_environment: dict[str, str] = {}
    apply_profile(args.profile, default_profile_environment)
    default_profile_configuration = build_run_configuration(
        args.profile,
        args.candidate,
        default_profile_environment,
    )

    if args.refresh_reference and (
        run_configuration["pipeline_fingerprint"]
        != default_profile_configuration["pipeline_fingerprint"]
    ):
        print("[eval] ERROR: reference refresh requires the unmodified production profile")
        return 2
    if args.refresh_reference and args.candidate != PRODUCTION_REFERENCE_MODEL:
        print(
            "[eval] ERROR: reference refresh requires the configured reference model: "
            f"{PRODUCTION_REFERENCE_MODEL}"
        )
        return 2

    from src.python.ai_scorer.evals.metrics import (
        EvalMetrics,
        RegressionResult,
        check_regression,
        compute_metrics,
    )
    from src.python.ai_scorer.evals.report import write_per_case, write_report, write_summary
    from src.python.ai_scorer.evals.runner import run_eval
    from src.python.ai_scorer.evals.schema import (
        load_fixture_meta,
        load_fixtures,
        update_fixture_reference,
        validate_fixtures,
    )

    # Load and validate canonical fixtures and reference metadata.
    try:
        cases = load_fixtures(args.fixtures)
        fixture_meta = load_fixture_meta(args.fixtures)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        print(f"[eval] ERROR: failed to load fixtures: {exc}")
        return 2
    errors = validate_fixtures(cases)
    if errors:
        print("[eval] ERROR: fixture validation failed:")
        for e in errors:
            print(f"  {e}")
        return 2
    current_fixture_fingerprint = fixture_fingerprint(cases)
    run_configuration["fixture_fingerprint"] = current_fixture_fingerprint

    # Load fixture metadata (v2 format) to get fixture_model and reference_metrics
    fixture_model = fixture_meta.fixture_model if fixture_meta else "(unknown)"
    reference_metrics_dict = fixture_meta.reference_metrics if fixture_meta else {}
    reference_run = fixture_meta.reference_run if fixture_meta else {}
    configuration_mismatches = reference_configuration_mismatches(
        reference_run,
        run_configuration,
        current_fixture_fingerprint,
        reference_metrics_dict,
    )
    configuration_matches = not configuration_mismatches

    if (
        not args.refresh_reference
        and not configuration_matches
        and not args.allow_profile_mismatch
    ):
        print("[eval] ERROR: stored reference pipeline does not match this run")
        print(
            "[eval] Stored fingerprint: "
            f"{reference_run.get('pipeline_fingerprint', '(missing)')}"
        )
        print(
            "[eval] Current fingerprint: "
            f"{run_configuration['pipeline_fingerprint']}"
        )
        for mismatch in configuration_mismatches:
            print(f"[eval]   {mismatch}")
        print(
            "[eval] Refresh the configured production reference, or use "
            "--allow-profile-mismatch for an ungated experiment."
        )
        return 2

    print(f"[eval] Loaded {len(cases)} canonical cases from {args.fixtures}")
    print(f"[eval] Fixture model (golden): {fixture_model}")
    print(f"[eval] Candidate model       : {args.candidate}")
    print(f"[eval] Ollama host           : {args.ollama_host}")
    print(f"[eval] Evaluation profile    : {args.profile}")
    print(
        "[eval] Pipeline fingerprint : "
        f"{run_configuration['pipeline_fingerprint']}"
    )
    print(f"[eval] Fixture fingerprint  : {current_fixture_fingerprint}")
    for key, value in sorted(run_configuration["pipeline"].items()):
        print(f"[eval]   {key}={value}")

    # --- Candidate run ---
    print(f"\n[eval] Running candidate ({args.candidate}) against golden set ...")
    candidate_results = run_eval(
        cases=cases,
        ollama_host=args.ollama_host,
        model_name=args.candidate,
        verbose=args.verbose,
    )

    candidate_metrics = compute_metrics(candidate_results)

    run_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    reference_refreshed = False
    gate_status = "evaluated"

    if args.refresh_reference and candidate_metrics.errored:
        print(
            "[eval] ERROR: refusing to refresh reference metrics after "
            f"{candidate_metrics.errored} errored cases"
        )
        return 2

    if args.refresh_reference:
        reference_metrics_dict = dataclasses.asdict(candidate_metrics)
        reference_run = {
            "profile": run_configuration["profile"],
            "profile_version": run_configuration["profile_version"],
            "pipeline": run_configuration["pipeline"],
            "implementation": run_configuration["implementation"],
            "pipeline_fingerprint": run_configuration["pipeline_fingerprint"],
            "fixture_fingerprint": current_fixture_fingerprint,
            "metrics_fingerprint": metrics_fingerprint(reference_metrics_dict),
            "model": args.candidate,
            "run_at": run_at,
        }
        update_fixture_reference(
            args.fixtures,
            reference_metrics_dict,
            reference_run,
        )
        reference_refreshed = True
        gate_status = "refreshed"
        configuration_matches = True

    # Build reference EvalMetrics from stored fixture metadata (no second model run).
    reference_metrics = EvalMetrics(
        total=reference_metrics_dict.get("total", 0),
        errored=reference_metrics_dict.get("errored", 0),
        exact_accuracy=reference_metrics_dict.get("exact_accuracy", 0.0),
        na_precision=reference_metrics_dict.get("na_precision", 0.0),
        na_recall=reference_metrics_dict.get("na_recall", 0.0),
        na_f1=reference_metrics_dict.get("na_f1", 0.0),
        mean_abs_error=reference_metrics_dict.get("mean_abs_error", 0.0),
        score_distribution=reference_metrics_dict.get("score_distribution", {}),
        mean_latency_ms=reference_metrics_dict.get("mean_latency_ms"),
        p50_latency_ms=reference_metrics_dict.get("p50_latency_ms"),
        p95_latency_ms=reference_metrics_dict.get("p95_latency_ms"),
        total_latency_ms=reference_metrics_dict.get("total_latency_ms"),
    )

    if not configuration_matches and args.allow_profile_mismatch and not args.refresh_reference:
        gate_status = "skipped"
        regression = RegressionResult(
            passed=False,
            reasons=[
                "Stored and current pipeline fingerprints differ; metrics are "
                "exploratory and were not evaluated by the regression gate."
            ],
        )
    else:
        regression = check_regression(reference_metrics, candidate_metrics)

    # --- Artifacts ---
    os.makedirs(args.output_dir, exist_ok=True)

    summary_path = write_summary(
        output_dir=args.output_dir,
        fixture_source=args.fixtures,
        fixture_model=fixture_model,
        fixture_count=len(cases),
        cases=cases,
        candidate_model=args.candidate,
        run_at=run_at,
        candidate_metrics=candidate_metrics,
        regression=regression,
        reference_metrics_dict=reference_metrics_dict,
        run_configuration=run_configuration,
        reference_run=reference_run,
        gate_status=gate_status,
        reference_refreshed=reference_refreshed,
    )
    per_case_path = write_per_case(
        output_dir=args.output_dir,
        cases=cases,
        candidate_results=candidate_results,
    )
    report_path = write_report(
        output_dir=args.output_dir,
        fixture_source=args.fixtures,
        fixture_model=fixture_model,
        fixture_count=len(cases),
        candidate_model=args.candidate,
        run_at=run_at,
        candidate_metrics=candidate_metrics,
        regression=regression,
        cases=cases,
        candidate_results=candidate_results,
        reference_metrics_dict=reference_metrics_dict,
        run_configuration=run_configuration,
        reference_run=reference_run,
        gate_status=gate_status,
        reference_refreshed=reference_refreshed,
    )

    print(f"\n[eval] Artifacts written to {args.output_dir}:")
    print(f"  {summary_path}")
    print(f"  {per_case_path}")
    print(f"  {report_path}")

    cm = candidate_metrics
    print("\n[eval] Metrics summary (vs golden set):")
    print(f"  exact_accuracy : {cm.exact_accuracy:.3f}")
    print(f"  na_f1          : {cm.na_f1:.3f}")
    print(f"  mean_abs_error : {cm.mean_abs_error:.3f}")

    if reference_refreshed:
        print("\n[eval] Reference metrics: REFRESHED")
        return 0
    if gate_status == "skipped":
        print("\n[eval] Regression gate: SKIPPED (configuration mismatch)")
        return 2
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
    p_eval = sub.add_parser("eval", help="Run eval: candidate model vs golden fixtures")
    p_eval.add_argument(
        "--ollama-host",
        default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    )
    p_eval.add_argument(
        "--candidate",
        default=os.environ.get("EVAL_CANDIDATE_MODEL", PRODUCTION_REFERENCE_MODEL),
        help=(
            "Candidate model name "
            "(default: EVAL_CANDIDATE_MODEL or stored reference model)"
        ),
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
    p_eval.add_argument(
        "--profile",
        choices=[PRODUCTION_PROFILE_NAME],
        default=os.environ.get("EVAL_PROFILE", PRODUCTION_PROFILE_NAME),
        help="Scoring pipeline profile (default: production)",
    )
    p_eval.add_argument(
        "--allow-profile-mismatch",
        action="store_true",
        default=_env_flag("EVAL_ALLOW_PROFILE_MISMATCH"),
        help="Run an ungated experiment when pipeline and reference differ",
    )
    p_eval.add_argument(
        "--refresh-reference",
        action="store_true",
        default=_env_flag("EVAL_REFRESH_REFERENCE"),
        help="Replace stored reference metrics with this candidate run",
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
