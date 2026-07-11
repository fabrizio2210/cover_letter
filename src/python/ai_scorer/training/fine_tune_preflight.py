from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field

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


def run_preflight(dataset_dir: str, splits: list[str]) -> PreflightReport:
    split_reports: list[SplitReport] = []
    all_case_ids: dict[str, int] = {}
    total = 0

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
                continue
            all_case_ids[case_id] = all_case_ids.get(case_id, 0) + 1

        split_reports.append(report)

    duplicate_case_ids = sorted([case_id for case_id, count in all_case_ids.items() if count > 1])
    if duplicate_case_ids:
        for report in split_reports:
            report.critical_errors.append(f"duplicate case ids detected across splits: {len(duplicate_case_ids)}")

    critical_error_count = len(duplicate_case_ids)
    critical_error_count += sum(len(report.critical_errors) for report in split_reports)

    return PreflightReport(
        dataset_dir=dataset_dir,
        total_records=total,
        split_reports=split_reports,
        duplicate_case_ids=duplicate_case_ids,
        critical_error_count=critical_error_count,
    )


def _default_output(dataset_dir: str) -> str:
    return os.path.join(dataset_dir, "preflight-report.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate training JSONL exports before fine-tuning")
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--splits", default="train,val,test", help="Comma-separated split names")
    parser.add_argument("--report-out", default="")
    args = parser.parse_args(argv)

    splits = [item.strip() for item in args.splits.split(",") if item.strip()]
    report = run_preflight(args.dataset_dir, splits)

    report_path = args.report_out or _default_output(args.dataset_dir)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "dataset_dir": report.dataset_dir,
                "total_records": report.total_records,
                "split_reports": [asdict(item) for item in report.split_reports],
                "duplicate_case_ids": report.duplicate_case_ids,
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
