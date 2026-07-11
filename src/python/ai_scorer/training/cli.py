"""Unified CLI for training-dataset runtime.

Subcommands:
  generate-preferences  — Generate or refresh fixed 10-preference seed set
  extract               — Build prompt-ready training cases from MongoDB
  label                 — Label cases with Gemini
  export                — Export labeled cases into chat JSONL splits

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "generate-preferences": _cmd_generate_preferences,
        "extract": _cmd_extract,
        "label": _cmd_label,
        "export": _cmd_export,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
