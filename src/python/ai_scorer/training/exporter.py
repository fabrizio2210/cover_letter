from __future__ import annotations

import argparse
import json
import os

from src.python.ai_scorer.training.dataset_split import (
    DEFAULT_SPLIT_MANIFEST,
    load_split_manifest,
    validate_current_golden,
    write_split_manifest,
)
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
            "job_fingerprint": case.job_fingerprint,
            "fingerprint_basis": case.fingerprint_basis,
            "preference_key": case.preference_key,
            "preference_guidance": case.preference_guidance,
            "snippet_count": len(case.relevant_snippets),
        },
    }


def _write_jsonl(records: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def export_jsonl_splits(
    cases: list[TrainingCase],
    output_dir: str,
    split_manifest: dict,
    strip_system: bool = False,
) -> dict:
    labeled = _labeled_cases_only(cases)
    if not labeled:
        raise ValueError("No labeled cases available for export")

    golden_errors = validate_current_golden(split_manifest)
    if golden_errors:
        raise ValueError("Stale golden metadata: " + "; ".join(golden_errors))

    train_fingerprints = set(split_manifest["train_fingerprints"])
    val_fingerprints = set(split_manifest["val_fingerprints"])
    excluded_fingerprints = set(split_manifest["promotion_exclusion_fingerprints"])
    known_fingerprints = train_fingerprints | val_fingerprints | excluded_fingerprints
    unknown = sorted({case.job_fingerprint for case in labeled} - known_fingerprints)
    if unknown:
        raise ValueError(f"Labeled cases contain {len(unknown)} fingerprints absent from the split manifest")

    train_cases = [case for case in labeled if case.job_fingerprint in train_fingerprints]
    val_cases = [case for case in labeled if case.job_fingerprint in val_fingerprints]
    excluded_cases = [case for case in labeled if case.job_fingerprint in excluded_fingerprints]
    if not train_cases or not val_cases:
        raise ValueError("Split manifest must select non-empty train and validation cases")

    os.makedirs(output_dir, exist_ok=True)

    train_path = os.path.join(output_dir, "train.jsonl")
    val_path = os.path.join(output_dir, "val.jsonl")

    _write_jsonl([_to_chat_record(case, strip_system=strip_system) for case in train_cases], train_path)
    _write_jsonl([_to_chat_record(case, strip_system=strip_system) for case in val_cases], val_path)

    stale_test_path = os.path.join(output_dir, "test.jsonl")
    if os.path.isfile(stale_test_path):
        os.remove(stale_test_path)

    exported_total = len(train_cases) + len(val_cases)

    summary = {
        "total_label_inventory": len(labeled),
        "total_exported": exported_total,
        "excluded": len(excluded_cases),
        "train": len(train_cases),
        "val": len(val_cases),
        "train_job_fingerprints": sorted({case.job_fingerprint for case in train_cases}),
        "val_job_fingerprints": sorted({case.job_fingerprint for case in val_cases}),
        "excluded_job_fingerprints": sorted({case.job_fingerprint for case in excluded_cases}),
        "train_fingerprint_count": len(train_fingerprints),
        "val_fingerprint_count": len(val_fingerprints),
        "seed": split_manifest["seed"],
        "requested_val_ratio": split_manifest["requested_val_ratio"],
        "actual_train_ratio": len(train_cases) / exported_total,
        "actual_val_ratio": len(val_cases) / exported_total,
        "split_unit": "job_fingerprint",
        "fingerprint_bases": split_manifest["fingerprint_bases"],
        "strip_system_prompt": strip_system,
    }
    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    output_manifest_path = os.path.join(output_dir, "split-manifest.json")
    write_split_manifest(split_manifest, output_manifest_path)

    return {
        "train": train_path,
        "val": val_path,
        "summary": summary_path,
        "split_manifest": output_manifest_path,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export labeled training cases to chat JSONL")
    parser.add_argument("--input", default="src/python/ai_scorer/training/data/proposed/labeled.json")
    parser.add_argument("--output-dir", default="src/python/ai_scorer/training/data/export")
    parser.add_argument("--split-manifest", default=DEFAULT_SPLIT_MANIFEST)
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
        split_manifest=load_split_manifest(args.split_manifest),
        strip_system=args.strip_system_prompt,
    )
    print("[training.export] wrote files:")
    for name, path in paths.items():
        print(f"  - {name}: {path}")


if __name__ == "__main__":
    main()
