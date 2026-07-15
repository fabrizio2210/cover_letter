from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field

from src.python.ai_scorer.training.dataset_split import (
    load_split_manifest,
    validate_current_golden,
)
from src.python.ai_scorer.job_fingerprint import fingerprint_basis, validate_fingerprint

_ALLOWED_ASSISTANT_LABELS = {"N/A", "0", "1", "2", "3", "4", "5"}


@dataclass
class SplitReport:
    split: str
    path: str
    count: int = 0
    critical_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PreflightReport:
    dataset_dir: str
    total_records: int
    split_reports: list[SplitReport]
    duplicate_case_ids: list[str]
    overlapping_job_fingerprints: list[str]
    promotion_overlap_fingerprints: list[str]
    manifest_errors: list[str]
    critical_error_count: int


def _read_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}: line {idx} invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}: line {idx} must be a JSON object")
            rows.append(row)
    return rows


def _validate_messages(messages: list[dict], where: str) -> list[str]:
    errors: list[str] = []
    if not messages:
        return [f"{where}: messages is empty"]

    roles = [m.get("role") for m in messages]
    if roles[-1] != "assistant":
        errors.append(f"{where}: last message role must be assistant")

    if roles[0] not in {"system", "user"}:
        errors.append(f"{where}: first message role must be system or user")

    if roles[0] == "system":
        if len(roles) < 3:
            errors.append(f"{where}: system-first messages must include user and assistant")
        elif roles[1] != "user" or roles[-1] != "assistant":
            errors.append(f"{where}: expected role order system -> user -> assistant")
    else:
        if len(roles) < 2:
            errors.append(f"{where}: user-first messages must include assistant")
        elif roles[1] != "assistant" and (len(roles) < 3 or roles[1] != "user"):
            errors.append(f"{where}: unsupported role sequence")

    for idx, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")
        if role not in {"system", "user", "assistant"}:
            errors.append(f"{where}: message[{idx}] invalid role {role!r}")
        if not isinstance(content, str) or content.strip() == "":
            errors.append(f"{where}: message[{idx}] has empty content")

    assistant = messages[-1].get("content")
    if isinstance(assistant, str) and assistant not in _ALLOWED_ASSISTANT_LABELS:
        errors.append(f"{where}: assistant label must be 0..5 or N/A")

    return errors


def run_preflight(
    dataset_dir: str,
    splits: list[str],
    split_manifest_path: str = "",
) -> PreflightReport:
    split_reports: list[SplitReport] = []
    all_case_ids: dict[str, int] = {}
    fingerprint_splits: dict[str, set[str]] = {}
    total = 0
    manifest_errors: list[str] = []
    manifest_path = split_manifest_path or os.path.join(dataset_dir, "split-manifest.json")
    try:
        manifest = load_split_manifest(manifest_path)
        manifest_errors.extend(validate_current_golden(manifest))
    except (OSError, ValueError) as exc:
        manifest = None
        manifest_errors.append(str(exc))

    for split in splits:
        path = os.path.join(dataset_dir, f"{split}.jsonl")
        report = SplitReport(split=split, path=path)
        if not os.path.isfile(path):
            report.critical_errors.append("split file missing")
            split_reports.append(report)
            continue

        rows = _read_jsonl(path)
        report.count = len(rows)
        total += len(rows)
        if not rows:
            report.critical_errors.append("split file is empty")

        for idx, row in enumerate(rows):
            where = f"{split}[{idx}]"
            messages = row.get("messages")
            if not isinstance(messages, list):
                report.critical_errors.append(f"{where}: messages must be an array")
                continue

            report.critical_errors.extend(_validate_messages(messages, where))

            meta = row.get("meta")
            if not isinstance(meta, dict):
                report.critical_errors.append(f"{where}: meta must be an object")
                continue

            case_id = meta.get("case_id")
            if not isinstance(case_id, str) or not case_id.strip():
                report.critical_errors.append(f"{where}: meta.case_id must be a non-empty string")
            else:
                all_case_ids[case_id] = all_case_ids.get(case_id, 0) + 1

            job_fingerprint = meta.get("job_fingerprint")
            fingerprint_error = validate_fingerprint(job_fingerprint)
            if fingerprint_error:
                report.critical_errors.append(f"{where}: {fingerprint_error}")
            else:
                meta_basis = meta.get("fingerprint_basis")
                if meta_basis != fingerprint_basis(job_fingerprint):
                    report.critical_errors.append(
                        f"{where}: meta.fingerprint_basis must match meta.job_fingerprint"
                    )
                fingerprint_splits.setdefault(job_fingerprint, set()).add(split)
                if manifest is not None:
                    expected = set(manifest.get(f"{split}_fingerprints", []))
                    if job_fingerprint not in expected:
                        report.critical_errors.append(
                            f"{where}: job fingerprint is not assigned to {split} in split manifest"
                        )

        split_reports.append(report)

    duplicate_case_ids = sorted([case_id for case_id, count in all_case_ids.items() if count > 1])
    if duplicate_case_ids:
        for report in split_reports:
            report.critical_errors.append(f"duplicate case ids detected across splits: {len(duplicate_case_ids)}")

    overlapping_job_fingerprints = sorted(
        job_fingerprint
        for job_fingerprint, observed_splits in fingerprint_splits.items()
        if len(observed_splits) > 1
    )
    if overlapping_job_fingerprints:
        for report in split_reports:
            report.critical_errors.append(
                f"job fingerprints detected across splits: {len(overlapping_job_fingerprints)}"
            )

    promotion_overlap_fingerprints: list[str] = []
    if manifest is not None:
        observed = set(fingerprint_splits)
        excluded = set(manifest["promotion_exclusion_fingerprints"])
        promotion_overlap_fingerprints = sorted(observed & excluded)
        if promotion_overlap_fingerprints:
            for report in split_reports:
                report.critical_errors.append(
                    f"promotion-excluded fingerprints detected in dataset: {len(promotion_overlap_fingerprints)}"
                )
        for report in split_reports:
            expected = set(manifest.get(f"{report.split}_fingerprints", []))
            observed_in_split = {
                fingerprint
                for fingerprint, observed_splits in fingerprint_splits.items()
                if report.split in observed_splits
            }
            missing = expected - observed_in_split
            if missing:
                report.critical_errors.append(
                    f"split is missing {len(missing)} fingerprints assigned by the manifest"
                )

    if manifest_errors:
        if split_reports:
            split_reports[0].critical_errors.extend(
                f"split manifest: {error}" for error in manifest_errors
            )
        else:
            split_reports.append(
                SplitReport(
                    split="manifest",
                    path=manifest_path,
                    critical_errors=[f"split manifest: {error}" for error in manifest_errors],
                )
            )

    critical_error_count = sum(len(report.critical_errors) for report in split_reports)

    return PreflightReport(
        dataset_dir=dataset_dir,
        total_records=total,
        split_reports=split_reports,
        duplicate_case_ids=duplicate_case_ids,
        overlapping_job_fingerprints=overlapping_job_fingerprints,
        promotion_overlap_fingerprints=promotion_overlap_fingerprints,
        manifest_errors=manifest_errors,
        critical_error_count=critical_error_count,
    )


def _default_output(dataset_dir: str) -> str:
    return os.path.join(dataset_dir, "preflight-report.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate training JSONL exports before fine-tuning")
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--splits", default="train,val", help="Comma-separated split names")
    parser.add_argument("--split-manifest", default="")
    parser.add_argument("--report-out", default="")
    args = parser.parse_args(argv)

    splits = [item.strip() for item in args.splits.split(",") if item.strip()]
    report = run_preflight(args.dataset_dir, splits, args.split_manifest)

    report_path = args.report_out or _default_output(args.dataset_dir)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "dataset_dir": report.dataset_dir,
                "total_records": report.total_records,
                "split_reports": [asdict(item) for item in report.split_reports],
                "duplicate_case_ids": report.duplicate_case_ids,
                "overlapping_job_fingerprints": report.overlapping_job_fingerprints,
                "promotion_overlap_fingerprints": report.promotion_overlap_fingerprints,
                "manifest_errors": report.manifest_errors,
                "critical_error_count": report.critical_error_count,
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )
        handle.write("\n")

    print(f"[training.preflight] report={report_path}")
    print(f"[training.preflight] total_records={report.total_records}")
    print(f"[training.preflight] critical_errors={report.critical_error_count}")

    return 0 if report.critical_error_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
