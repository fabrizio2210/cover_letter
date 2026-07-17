from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections import Counter
from copy import deepcopy
from dataclasses import asdict

from src.python.ai_scorer.job_fingerprint import (
    DESCRIPTION_BASIS,
    fingerprint_basis,
    partition_fingerprints,
    stable_json_hash,
)
from src.python.ai_scorer.training.dataset_split import (
    DEFAULT_SPLIT_MANIFEST,
    file_sha256,
    load_split_manifest,
    validate_current_golden,
    validate_split_manifest,
)
from src.python.ai_scorer.training.preferences import default_preferences_path, load_preferences
from src.python.ai_scorer.training.schema import TrainingCase, load_cases, validate_cases


DEFAULT_CANDIDATES = "src/python/ai_scorer/training/data/proposed/candidates.json"
DEFAULT_LABELED = "src/python/ai_scorer/training/data/proposed/labeled.json"
DEFAULT_EXPANSION_CANDIDATES = (
    "src/python/ai_scorer/training/data/proposed/high-score-candidates.json"
)
DEFAULT_MERGED_LABELED = "src/python/ai_scorer/training/data/proposed/merged-labeled.json"
DEFAULT_REPORT = "src/python/ai_scorer/training/data/proposed/labeled-expansion-merge-plan.json"
DEFAULT_RECEIPT = "src/python/ai_scorer/training/data/proposed/labeled-expansion-merge-receipt.json"


def _case_payload_without_labels(case: TrainingCase) -> dict:
    payload = asdict(case)
    payload.pop("label_score", None)
    payload.pop("label_available", None)
    return payload


def _case_map(cases: list[TrainingCase], name: str) -> dict[str, TrainingCase]:
    result = {case.case_id: case for case in cases}
    if len(result) != len(cases):
        raise ValueError(f"{name} contains duplicate case IDs")
    return result


def _validate_inventory(cases: list[TrainingCase], name: str, *, labeled: bool) -> None:
    errors = validate_cases(cases)
    if labeled:
        unlabeled = sum(case.label_available is None for case in cases)
        if unlabeled:
            errors.append(f"{name} contains {unlabeled} unlabeled cases")
    if errors:
        raise ValueError(f"Invalid {name}: " + "; ".join(errors))


def _validate_alignment(
    candidates: list[TrainingCase],
    labeled: list[TrainingCase],
    name: str,
) -> None:
    candidate_by_id = _case_map(candidates, f"{name} candidates")
    labeled_by_id = _case_map(labeled, f"{name} labels")
    if set(candidate_by_id) != set(labeled_by_id):
        raise ValueError(f"{name} candidate and labeled case IDs differ")
    mismatches = [
        case_id
        for case_id in candidate_by_id
        if _case_payload_without_labels(candidate_by_id[case_id])
        != _case_payload_without_labels(labeled_by_id[case_id])
    ]
    if mismatches:
        raise ValueError(f"{name} candidate and labeled payloads differ for {len(mismatches)} cases")


def _label(case: TrainingCase) -> str:
    if case.label_available is False:
        return "N/A"
    if case.label_available is True:
        return str(case.label_score)
    return "UNLABELED"


def _distribution(cases: list[TrainingCase]) -> dict[str, int]:
    counts = Counter(_label(case) for case in cases)
    return {
        label: counts.get(label, 0)
        for label in ("0", "1", "2", "3", "4", "5", "N/A", "UNLABELED")
        if counts.get(label, 0)
    }


def build_expansion_plan(
    base_candidates: list[TrainingCase],
    base_labeled: list[TrainingCase],
    expansion_candidates: list[TrainingCase],
    merged_labeled: list[TrainingCase],
    split_manifest: dict,
    preferences: list[dict],
    *,
    teacher_model: str,
    inputs: dict | None = None,
) -> tuple[dict, list[TrainingCase], dict]:
    _validate_inventory(base_candidates, "base candidate inventory", labeled=False)
    _validate_inventory(base_labeled, "base labeled inventory", labeled=True)
    _validate_inventory(expansion_candidates, "expansion candidate inventory", labeled=False)
    _validate_inventory(merged_labeled, "merged labeled inventory", labeled=True)
    _validate_alignment(base_candidates, base_labeled, "base")

    golden_errors = validate_current_golden(split_manifest)
    if golden_errors:
        raise ValueError("Golden fixture validation failed: " + "; ".join(golden_errors))
    if stable_json_hash(preferences) != split_manifest.get("preference_set_hash"):
        raise ValueError("Seed preference set does not match the split manifest")

    base_ids = {case.case_id for case in base_candidates}
    expansion_ids = {case.case_id for case in expansion_candidates}
    if base_ids & expansion_ids:
        raise ValueError("Base and expansion candidates overlap by case ID")
    merged_by_id = _case_map(merged_labeled, "merged labeled inventory")
    if set(merged_by_id) != base_ids | expansion_ids:
        raise ValueError("Merged labels are not exactly the base plus expansion case IDs")

    base_labeled_by_id = _case_map(base_labeled, "base labeled inventory")
    changed_base_labels: list[str] = []
    for case_id, before in base_labeled_by_id.items():
        after = merged_by_id[case_id]
        if _case_payload_without_labels(before) != _case_payload_without_labels(after):
            raise ValueError(f"Merged output changed the base payload for case {case_id}")
        if (before.label_score, before.label_available) != (
            after.label_score,
            after.label_available,
        ):
            changed_base_labels.append(case_id)
    if changed_base_labels:
        raise ValueError(f"Merged output changed {len(changed_base_labels)} paid base labels")

    expansion_labeled = [merged_by_id[case.case_id] for case in expansion_candidates]
    _validate_alignment(expansion_candidates, expansion_labeled, "expansion")

    preference_by_key = {str(item["key"]): str(item["guidance"]) for item in preferences}
    bad_preferences = [
        case.case_id
        for case in expansion_candidates
        if preference_by_key.get(case.preference_key) != case.preference_guidance
    ]
    if bad_preferences:
        raise ValueError(f"Expansion contains {len(bad_preferences)} non-seed preferences")

    known = (
        set(split_manifest["train_fingerprints"])
        | set(split_manifest["val_fingerprints"])
        | set(split_manifest["promotion_exclusion_fingerprints"])
    )
    new_fingerprints = {case.job_fingerprint for case in expansion_candidates}
    overlap = new_fingerprints & known
    if overlap:
        raise ValueError(f"Expansion contains {len(overlap)} already-known job fingerprints")
    non_description = [
        fingerprint
        for fingerprint in new_fingerprints
        if fingerprint_basis(fingerprint) != DESCRIPTION_BASIS
    ]
    if non_description:
        raise ValueError("Expansion jobs must all use full-description fingerprints")

    new_train, new_val = partition_fingerprints(
        new_fingerprints,
        seed=int(split_manifest["seed"]),
        val_ratio=float(split_manifest["requested_val_ratio"]),
    )
    updated_manifest = deepcopy(split_manifest)
    updated_manifest["train_fingerprints"] = sorted(
        set(split_manifest["train_fingerprints"]) | set(new_train)
    )
    updated_manifest["val_fingerprints"] = sorted(
        set(split_manifest["val_fingerprints"]) | set(new_val)
    )
    assigned = set(updated_manifest["train_fingerprints"]) | set(
        updated_manifest["val_fingerprints"]
    )
    updated_manifest["fingerprint_bases"] = sorted(
        {fingerprint_basis(fingerprint) for fingerprint in assigned}
    )
    updated_manifest["native_description_fingerprints"] = sorted(
        set(split_manifest.get("native_description_fingerprints", [])) | new_fingerprints
    )
    manifest_errors = validate_split_manifest(updated_manifest)
    if manifest_errors:
        raise ValueError("Expanded split manifest is invalid: " + "; ".join(manifest_errors))

    combined_candidates = base_candidates + expansion_candidates
    _validate_alignment(combined_candidates, merged_labeled, "combined")

    train_set = set(updated_manifest["train_fingerprints"])
    val_set = set(updated_manifest["val_fingerprints"])
    excluded_set = set(updated_manifest["promotion_exclusion_fingerprints"])
    exported_train = [case for case in merged_labeled if case.job_fingerprint in train_set]
    exported_val = [case for case in merged_labeled if case.job_fingerprint in val_set]
    excluded_cases = [case for case in merged_labeled if case.job_fingerprint in excluded_set]

    by_preference: dict[str, dict] = {}
    for preference_key in sorted(preference_by_key):
        cases = [case for case in expansion_labeled if case.preference_key == preference_key]
        by_preference[preference_key] = {
            "case_count": len(cases),
            "job_count": len({case.job_fingerprint for case in cases}),
            "label_distribution": _distribution(cases),
        }

    report = {
        "report_schema_version": "1",
        "dry_run": True,
        "teacher_model": teacher_model,
        "labeling_provenance_complete": teacher_model != "unrecorded",
        "inputs": inputs or {},
        "summary": {
            "base_candidate_count": len(base_candidates),
            "base_labeled_count": len(base_labeled),
            "expansion_case_count": len(expansion_candidates),
            "merged_labeled_count": len(merged_labeled),
            "preserved_base_label_count": len(base_labeled),
            "changed_base_label_count": 0,
            "new_job_fingerprint_count": len(new_fingerprints),
            "new_train_fingerprint_count": len(new_train),
            "new_val_fingerprint_count": len(new_val),
            "combined_train_fingerprint_count": len(train_set),
            "combined_val_fingerprint_count": len(val_set),
            "expected_train_case_count": len(exported_train),
            "expected_val_case_count": len(exported_val),
            "expected_excluded_case_count": len(excluded_cases),
            "expansion_label_distribution": _distribution(expansion_labeled),
            "combined_label_distribution": _distribution(merged_labeled),
        },
        "expansion_by_preference": by_preference,
        "new_train_fingerprints": new_train,
        "new_val_fingerprints": new_val,
    }
    return report, combined_candidates, updated_manifest


def _write_json(path: str, value: object) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _stage_json(target: str, value: object) -> str:
    directory = os.path.dirname(os.path.abspath(target))
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json.tmp",
        prefix="labeled-expansion-",
        dir=directory,
        delete=False,
    )
    try:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        return handle.name
    finally:
        handle.close()


def _replace_atomically(staged: dict[str, str]) -> None:
    backups: dict[str, str] = {}
    try:
        for target in staged:
            handle = tempfile.NamedTemporaryFile(
                suffix=".backup",
                prefix="labeled-expansion-",
                dir=os.path.dirname(os.path.abspath(target)),
                delete=False,
            )
            handle.close()
            shutil.copy2(target, handle.name)
            backups[target] = handle.name
        try:
            for target, staged_path in staged.items():
                os.replace(staged_path, target)
        except Exception:
            for target, backup_path in backups.items():
                shutil.copy2(backup_path, target)
            raise
    finally:
        for path in list(staged.values()) + list(backups.values()):
            if os.path.exists(path):
                os.unlink(path)


def merge_labeled_expansion(
    *,
    candidates_path: str,
    labeled_path: str,
    expansion_candidates_path: str,
    merged_labeled_path: str,
    split_manifest_path: str,
    preferences_path: str,
    report_out: str,
    receipt_out: str,
    teacher_model: str,
    apply: bool,
) -> dict:
    paths = {
        "base_candidates": candidates_path,
        "base_labeled": labeled_path,
        "expansion_candidates": expansion_candidates_path,
        "merged_labeled": merged_labeled_path,
        "split_manifest": split_manifest_path,
        "preferences": preferences_path,
    }
    inputs = {
        name: {"path": path, "sha256": file_sha256(path)}
        for name, path in paths.items()
    }
    base_candidates = load_cases(candidates_path)
    base_labeled = load_cases(labeled_path)
    expansion_candidates = load_cases(expansion_candidates_path)
    merged_labeled = load_cases(merged_labeled_path)
    manifest = load_split_manifest(split_manifest_path)
    preferences = load_preferences(preferences_path)
    report, combined_candidates, updated_manifest = build_expansion_plan(
        base_candidates,
        base_labeled,
        expansion_candidates,
        merged_labeled,
        manifest,
        preferences,
        teacher_model=teacher_model,
        inputs=inputs,
    )
    _write_json(report_out, report)
    if not apply:
        return report

    before = {
        "candidates_sha256": file_sha256(candidates_path),
        "labeled_sha256": file_sha256(labeled_path),
        "split_manifest_sha256": file_sha256(split_manifest_path),
    }
    candidate_stage = _stage_json(candidates_path, [asdict(case) for case in combined_candidates])
    labeled_stage = _stage_json(labeled_path, [asdict(case) for case in merged_labeled])
    manifest_stage = _stage_json(split_manifest_path, updated_manifest)
    staged = {
        candidates_path: candidate_stage,
        labeled_path: labeled_stage,
        split_manifest_path: manifest_stage,
    }
    try:
        staged_candidates = load_cases(candidate_stage)
        staged_labeled = load_cases(labeled_stage)
        _validate_inventory(staged_candidates, "staged candidate inventory", labeled=False)
        _validate_inventory(staged_labeled, "staged labeled inventory", labeled=True)
        _validate_alignment(staged_candidates, staged_labeled, "staged combined")
        with open(manifest_stage, "r", encoding="utf-8") as handle:
            staged_manifest = json.load(handle)
        manifest_errors = validate_split_manifest(staged_manifest)
        if manifest_errors:
            raise ValueError("Staged manifest is invalid: " + "; ".join(manifest_errors))
        _replace_atomically(staged)
    except Exception:
        for path in staged.values():
            if os.path.exists(path):
                os.unlink(path)
        raise

    receipt = {
        "receipt_schema_version": "1",
        "source_report": {"path": report_out, "sha256": file_sha256(report_out)},
        "teacher_model": teacher_model,
        "labeling_provenance_complete": teacher_model != "unrecorded",
        "summary": report["summary"],
        "before": before,
        "after": {
            "candidates_sha256": file_sha256(candidates_path),
            "labeled_sha256": file_sha256(labeled_path),
            "split_manifest_sha256": file_sha256(split_manifest_path),
        },
    }
    _write_json(receipt_out, receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and merge a labeled full-description expansion"
    )
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES)
    parser.add_argument("--labeled", default=DEFAULT_LABELED)
    parser.add_argument("--expansion-candidates", default=DEFAULT_EXPANSION_CANDIDATES)
    parser.add_argument("--merged-labeled", default=DEFAULT_MERGED_LABELED)
    parser.add_argument("--split-manifest", default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--preferences", default=default_preferences_path())
    parser.add_argument("--report-out", default=DEFAULT_REPORT)
    parser.add_argument("--receipt-out", default=DEFAULT_RECEIPT)
    parser.add_argument("--teacher-model", default="unrecorded")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    result = merge_labeled_expansion(
        candidates_path=args.candidates,
        labeled_path=args.labeled,
        expansion_candidates_path=args.expansion_candidates,
        merged_labeled_path=args.merged_labeled,
        split_manifest_path=args.split_manifest,
        preferences_path=args.preferences,
        report_out=args.report_out,
        receipt_out=args.receipt_out,
        teacher_model=args.teacher_model,
        apply=args.apply,
    )
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    if args.apply:
        print(f"[training.merge-labeled-expansion] receipt -> {args.receipt_out}")
    else:
        print(f"[training.merge-labeled-expansion] dry-run report -> {args.report_out}")
        print("[training.merge-labeled-expansion] no maintained artifacts were modified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
