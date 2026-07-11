"""Unified CLI for training-dataset runtime.

Subcommands:
  generate-preferences  — Generate or refresh fixed 10-preference seed set
  extract               — Build prompt-ready training cases from MongoDB
  label                 — Label cases with Gemini
  export                — Export labeled cases into chat JSONL splits
    preflight             — Validate exported JSONL dataset integrity
    detect-runtime        — Detect CUDA/CPU fine-tuning runtime path
    train                 — Launch fine-tuning run with manifests/checkpoints
    merge                 — Merge adapter into full HF weights
    package               — Convert to GGUF and package Ollama model
    eval-gate             — Run scorer eval gate for promotion

Usage:
  python -m src.python.ai_scorer.training.cli generate-preferences
  python -m src.python.ai_scorer.training.cli extract
  python -m src.python.ai_scorer.training.cli label
  python -m src.python.ai_scorer.training.cli export
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.python.ai_scorer.training.preferences import (
    default_preferences_path,
    ensure_seed_preferences,
    generate_random_preferences,
    save_preferences,
)


def _resolve_dataset_dir(profile: str, override: str) -> str:
    if override:
        return override
    if profile == "keep-system":
        return "src/python/ai_scorer/training/data/export"
    return "src/python/ai_scorer/training/data/export/no-system-prompt"


def _cmd_generate_preferences(args: argparse.Namespace) -> int:
    if args.overwrite or not os.path.exists(args.output):
        preferences = generate_random_preferences(count=args.count, seed=args.seed)
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        save_preferences(preferences, args.output)
    else:
        preferences = ensure_seed_preferences(args.output, count=args.count, seed=args.seed)

    print(f"[training.preferences] generated={len(preferences)} path={args.output}")
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.training.extractor import main as extract_main

    extract_main(
        [
            "--mongo-uri",
            args.mongo_uri,
            "--global-db",
            args.global_db,
            "--output",
            args.output,
            "--preferences",
            args.preferences,
            "--limit",
            str(args.limit),
            "--seed-count",
            str(args.seed_count),
            "--seed",
            str(args.seed),
        ]
    )
    return 0


def _cmd_label(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.training.labeler import main as label_main

    label_main(
        [
            "--input",
            args.input,
            "--output",
            args.output,
            "--model",
            args.model,
        ]
    )
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.training.exporter import main as export_main

    export_args = [
        "--input",
        args.input,
        "--output-dir",
        args.output_dir,
        "--seed",
        str(args.seed),
        "--train-ratio",
        str(args.train_ratio),
        "--val-ratio",
        str(args.val_ratio),
    ]
    if args.strip_system_prompt:
        export_args.append("--strip-system-prompt")
    export_main(export_args)
    return 0


def _cmd_preflight(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.training.fine_tune_preflight import main as preflight_main

    dataset_dir = _resolve_dataset_dir(args.dataset_profile, args.dataset_dir)
    return preflight_main([
        "--dataset-dir",
        dataset_dir,
        "--splits",
        args.splits,
        "--report-out",
        args.report_out,
    ])


def _cmd_detect_runtime(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.training.fine_tune_runtime import main as runtime_main

    runtime_args = []
    if args.output:
        runtime_args.extend(["--output", args.output])
    return runtime_main(runtime_args)


def _cmd_train(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.training.fine_tune_train import main as train_main

    train_args = [
        "--base-model-ollama",
        args.base_model_ollama,
        "--base-model-hf",
        args.base_model_hf,
        "--dataset-profile",
        args.dataset_profile,
        "--dataset-dir",
        args.dataset_dir,
        "--output-root",
        args.output_root,
        "--seed",
        str(args.seed),
        "--max-seq-length",
        str(args.max_seq_length),
        "--per-device-batch-size",
        str(args.per_device_batch_size),
        "--gradient-accumulation-steps",
        str(args.gradient_accumulation_steps),
        "--learning-rate",
        str(args.learning_rate),
        "--max-steps",
        str(args.max_steps),
        "--num-train-epochs",
        str(args.num_train_epochs),
    ]
    if args.run_id:
        train_args.extend(["--run-id", args.run_id])
    if args.resume_from_checkpoint:
        train_args.extend(["--resume-from-checkpoint", args.resume_from_checkpoint])
    if args.smoke_run:
        train_args.append("--smoke-run")
    return train_main(train_args)


def _cmd_merge(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.training.fine_tune_merge import main as merge_main

    merge_args = [
        "--base-model-hf",
        args.base_model_hf,
        "--run-dir",
        args.run_dir,
    ]
    if args.adapter_dir:
        merge_args.extend(["--adapter-dir", args.adapter_dir])
    if args.merged_dir:
        merge_args.extend(["--merged-dir", args.merged_dir])
    return merge_main(merge_args)


def _cmd_package(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.training.fine_tune_package import main as package_main

    package_args = [
        "--run-dir",
        args.run_dir,
        "--ollama-tag",
        args.ollama_tag,
        "--quant",
        args.quant,
        "--system-prompt",
        args.system_prompt,
    ]
    if args.merged_dir:
        package_args.extend(["--merged-dir", args.merged_dir])
    if args.output_dir:
        package_args.extend(["--output-dir", args.output_dir])
    if args.convert_script:
        package_args.extend(["--convert-script", args.convert_script])
    if args.skip_ollama_create:
        package_args.append("--skip-ollama-create")
    return package_main(package_args)


def _cmd_eval_gate(args: argparse.Namespace) -> int:
    from src.python.ai_scorer.training.fine_tune_eval_gate import main as eval_gate_main

    gate_args = [
        "--candidate-model",
        args.candidate_model,
        "--eval-script",
        args.eval_script,
    ]
    if args.run_dir:
        gate_args.extend(["--run-dir", args.run_dir])
    if args.fixtures:
        gate_args.extend(["--fixtures", args.fixtures])
    return eval_gate_main(gate_args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.python.ai_scorer.training.cli",
        description="Training dataset runtime for ai_scorer",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pref = sub.add_parser("generate-preferences", help="Generate fixed training preference seed set")
    p_pref.add_argument("--output", default=default_preferences_path())
    p_pref.add_argument("--count", type=int, default=10)
    p_pref.add_argument("--seed", type=int, default=1337)
    p_pref.add_argument("--overwrite", action="store_true")

    p_extract = sub.add_parser("extract", help="Extract prompt-ready training cases from MongoDB")
    p_extract.add_argument("--mongo-uri", default=os.environ.get("MONGO_HOST", "mongodb://localhost:27017/"))
    p_extract.add_argument("--global-db", default=os.environ.get("DB_NAME", "cover_letter_global"))
    p_extract.add_argument("--output", default="src/python/ai_scorer/training/data/proposed/candidates.json")
    p_extract.add_argument("--preferences", default=default_preferences_path())
    p_extract.add_argument("--limit", type=int, default=50)
    p_extract.add_argument("--seed-count", type=int, default=10)
    p_extract.add_argument("--seed", type=int, default=1337)

    p_label = sub.add_parser("label", help="Label extracted cases with Gemini")
    p_label.add_argument("--input", default="src/python/ai_scorer/training/data/proposed/candidates.json")
    p_label.add_argument("--output", default="src/python/ai_scorer/training/data/proposed/labeled.json")
    p_label.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"))

    p_export = sub.add_parser("export", help="Export labeled cases into chat JSONL splits")
    p_export.add_argument("--input", default="src/python/ai_scorer/training/data/proposed/labeled.json")
    p_export.add_argument("--output-dir", default="src/python/ai_scorer/training/data/export")
    p_export.add_argument("--seed", type=int, default=42)
    p_export.add_argument("--train-ratio", type=float, default=0.8)
    p_export.add_argument("--val-ratio", type=float, default=0.1)
    p_export.add_argument("--strip-system-prompt", action="store_true", help="Remove system prompt from messages (embed at fine-tuning instead)")

    p_preflight = sub.add_parser("preflight", help="Validate exported JSONL dataset splits")
    p_preflight.add_argument("--dataset-profile", choices=["keep-system", "no-system"], default="keep-system")
    p_preflight.add_argument("--dataset-dir", default="")
    p_preflight.add_argument("--splits", default="train,val,test")
    p_preflight.add_argument("--report-out", default="")

    p_runtime = sub.add_parser("detect-runtime", help="Detect CUDA/CPU fine-tuning runtime path")
    p_runtime.add_argument("--output", default="")

    p_train = sub.add_parser("train", help="Launch fine-tuning run with manifests")
    p_train.add_argument("--base-model-ollama", default="qwen2.5:1.5b")
    p_train.add_argument("--base-model-hf", default="Qwen/Qwen2.5-1.5B-Instruct")
    p_train.add_argument("--dataset-profile", choices=["keep-system", "no-system"], default="keep-system")
    p_train.add_argument("--dataset-dir", default="")
    p_train.add_argument("--output-root", default="src/python/ai_scorer/training/artifacts")
    p_train.add_argument("--run-id", default="")
    p_train.add_argument("--seed", type=int, default=42)
    p_train.add_argument("--max-seq-length", type=int, default=1024)
    p_train.add_argument("--per-device-batch-size", type=int, default=1)
    p_train.add_argument("--gradient-accumulation-steps", type=int, default=8)
    p_train.add_argument("--learning-rate", type=float, default=2e-4)
    p_train.add_argument("--max-steps", type=int, default=200)
    p_train.add_argument("--num-train-epochs", type=int, default=1)
    p_train.add_argument("--resume-from-checkpoint", default="")
    p_train.add_argument("--smoke-run", action="store_true")

    p_merge = sub.add_parser("merge", help="Merge LoRA adapters into full HF weights")
    p_merge.add_argument("--base-model-hf", default="Qwen/Qwen2.5-1.5B-Instruct")
    p_merge.add_argument("--run-dir", required=True)
    p_merge.add_argument("--adapter-dir", default="")
    p_merge.add_argument("--merged-dir", default="")

    p_package = sub.add_parser("package", help="Convert merged model to GGUF and package for Ollama")
    p_package.add_argument("--run-dir", required=True)
    p_package.add_argument("--merged-dir", default="")
    p_package.add_argument("--output-dir", default="")
    p_package.add_argument("--convert-script", default="")
    p_package.add_argument("--quant", default="q4_k_m")
    p_package.add_argument("--ollama-tag", required=True)
    p_package.add_argument("--system-prompt", default="You are an AI scorer. Return only a score from 0 to 5 or N/A.")
    p_package.add_argument("--skip-ollama-create", action="store_true")

    p_gate = sub.add_parser("eval-gate", help="Run existing scorer eval as promotion gate")
    p_gate.add_argument("--candidate-model", required=True)
    p_gate.add_argument("--run-dir", default="")
    p_gate.add_argument("--eval-script", default="scripts/eval-scorer.sh")
    p_gate.add_argument("--fixtures", default="")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "generate-preferences": _cmd_generate_preferences,
        "extract": _cmd_extract,
        "label": _cmd_label,
        "export": _cmd_export,
        "preflight": _cmd_preflight,
        "detect-runtime": _cmd_detect_runtime,
        "train": _cmd_train,
        "merge": _cmd_merge,
        "package": _cmd_package,
        "eval-gate": _cmd_eval_gate,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
