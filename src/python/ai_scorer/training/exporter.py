from __future__ import annotations

import argparse
import json
import os
import random

from src.python.ai_scorer.training.schema import TrainingCase, load_cases, validate_cases


def _labeled_cases_only(cases: list[TrainingCase]) -> list[TrainingCase]:
    output: list[TrainingCase] = []
    for case in cases:
        if case.label_available is None:
            continue
        output.append(case)
    return output


def _to_chat_record(case: TrainingCase, strip_system: bool = False) -> dict:
    assistant = "N/A" if case.label_available is False else str(case.label_score)
    messages = []
    if not strip_system:
        messages.append({"role": "system", "content": case.system_prompt})
    messages.extend([
        {"role": "user", "content": case.user_prompt},
        {"role": "assistant", "content": assistant},
    ])
    return {
        "messages": messages,
        "meta": {
            "case_id": case.case_id,
            "source_job_id": case.source_job_id,
            "preference_key": case.preference_key,
            "preference_guidance": case.preference_guidance,
            "snippet_count": len(case.relevant_snippets),
        },
    }


def _split_cases(
    cases: list[TrainingCase],
    seed: int,
    val_ratio: float,
) -> tuple[list[TrainingCase], list[TrainingCase]]:
    if val_ratio <= 0 or val_ratio >= 1:
        raise ValueError("val_ratio must satisfy: 0 < val_ratio < 1")

    source_job_ids = sorted({case.source_job_id for case in cases})
    if len(source_job_ids) < 2:
        raise ValueError("At least two distinct source_job_id values are required for train/val splitting")

    random.Random(seed).shuffle(source_job_ids)
    n_val_jobs = round(len(source_job_ids) * val_ratio)
    n_val_jobs = max(1, min(len(source_job_ids) - 1, n_val_jobs))
    val_job_ids = set(source_job_ids[:n_val_jobs])

    train = [case for case in cases if case.source_job_id not in val_job_ids]
    val = [case for case in cases if case.source_job_id in val_job_ids]
    return train, val


def _write_jsonl(records: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def export_jsonl_splits(
    cases: list[TrainingCase],
    output_dir: str,
    seed: int,
    val_ratio: float,
    strip_system: bool = False,
) -> dict:
    labeled = _labeled_cases_only(cases)
    if not labeled:
        raise ValueError("No labeled cases available for export")

    train_cases, val_cases = _split_cases(
        labeled,
        seed=seed,
        val_ratio=val_ratio,
    )

    os.makedirs(output_dir, exist_ok=True)

    train_path = os.path.join(output_dir, "train.jsonl")
    val_path = os.path.join(output_dir, "val.jsonl")

    _write_jsonl([_to_chat_record(case, strip_system=strip_system) for case in train_cases], train_path)
    _write_jsonl([_to_chat_record(case, strip_system=strip_system) for case in val_cases], val_path)

    stale_test_path = os.path.join(output_dir, "test.jsonl")
    if os.path.isfile(stale_test_path):
        os.remove(stale_test_path)

    total = len(labeled)
    train_job_count = len({case.source_job_id for case in train_cases})
    val_job_count = len({case.source_job_id for case in val_cases})

    summary = {
        "total_labeled": len(labeled),
        "train": len(train_cases),
        "val": len(val_cases),
        "train_source_jobs": train_job_count,
        "val_source_jobs": val_job_count,
        "seed": seed,
        "requested_val_ratio": val_ratio,
        "actual_train_ratio": len(train_cases) / total,
        "actual_val_ratio": len(val_cases) / total,
        "split_unit": "source_job_id",
        "strip_system_prompt": strip_system,
    }
    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    return {
        "train": train_path,
        "val": val_path,
        "summary": summary_path,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export labeled training cases to chat JSONL")
    parser.add_argument("--input", default="src/python/ai_scorer/training/data/proposed/labeled.json")
    parser.add_argument("--output-dir", default="src/python/ai_scorer/training/data/export")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--strip-system-prompt", action="store_true", help="Remove system prompt from messages (embed at fine-tuning instead)")
    args = parser.parse_args(argv)

    cases = load_cases(args.input)
    errors = validate_cases(cases)
    if errors:
        print("[training.export] ERROR: input validation failed")
        for err in errors:
            print(f"  - {err}")
        raise SystemExit(2)

    paths = export_jsonl_splits(
        cases,
        output_dir=args.output_dir,
        seed=args.seed,
        val_ratio=args.val_ratio,
        strip_system=args.strip_system_prompt,
    )
    print("[training.export] wrote files:")
    for name, path in paths.items():
        print(f"  - {name}: {path}")


if __name__ == "__main__":
    main()
