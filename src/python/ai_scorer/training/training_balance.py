from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass


SAMPLING_MODES = ("job-preference-balanced", "all")
BALANCED_MODE = "job-preference-balanced"
LABEL_ORDER = ("0", "1", "2", "3", "4", "5", "N/A")


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assistant_label(row: dict) -> str:
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Training row must contain messages")
    assistant = messages[-1]
    if assistant.get("role") != "assistant":
        raise ValueError("Training row must end with an assistant message")
    label = assistant.get("content")
    if label not in LABEL_ORDER:
        raise ValueError(f"Unsupported assistant label: {label!r}")
    return str(label)


def _input_signature(row: dict) -> str:
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError("Training row must contain an input and assistant response")
    return _stable_hash(messages[:-1])


def _case_id(row: dict) -> str:
    meta = row.get("meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("case_id"), str):
        raise ValueError("Training row must contain meta.case_id")
    return meta["case_id"]


def _balance_group_key(row: dict) -> tuple[str, str]:
    meta = row.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("Training row must contain meta")
    fingerprint = meta.get("job_fingerprint")
    preference = meta.get("preference_key")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("Training row must contain meta.job_fingerprint")
    if not isinstance(preference, str) or not preference:
        raise ValueError("Training row must contain meta.preference_key")
    return fingerprint, preference


def _ordered_distribution(labels: list[str]) -> dict[str, int]:
    counts = Counter(labels)
    return {label: counts.get(label, 0) for label in LABEL_ORDER}


@dataclass
class BalancedTrainingPlan:
    rows: list[dict]
    groups: list[list[int]]
    group_keys: list[tuple[str, str]]
    seed: int
    samples_per_group: int
    report: dict

    def epoch_indices(self, epoch: int) -> list[int]:
        if epoch < 0:
            raise ValueError("epoch cannot be negative")
        selected: list[tuple[tuple[str, str], list[int]]] = []
        for key, indices in zip(self.group_keys, self.groups):
            ordered = sorted(
                indices,
                key=lambda index: _stable_hash([self.seed, key, _case_id(self.rows[index])]),
            )
            count = min(self.samples_per_group, len(ordered))
            start = (epoch * self.samples_per_group) % len(ordered)
            chosen = [ordered[(start + offset) % len(ordered)] for offset in range(count)]
            selected.append((key, chosen))

        rng = random.Random(self.seed + epoch)
        rng.shuffle(selected)
        return [index for _, indices in selected for index in indices]

    def epoch_label_distribution(self, epoch: int) -> dict[str, int]:
        return _ordered_distribution([_assistant_label(self.rows[index]) for index in self.epoch_indices(epoch)])


def build_balanced_training_plan(
    rows: list[dict],
    *,
    seed: int,
    samples_per_group: int = 1,
) -> BalancedTrainingPlan:
    if samples_per_group <= 0:
        raise ValueError("samples_per_group must be greater than zero")

    by_input: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_input[_input_signature(row)].append(row)

    resolved_rows: list[dict] = []
    conflict_details: list[dict] = []
    duplicate_group_count = 0
    conflicting_group_count = 0
    unresolved_tie_count = 0
    for signature in sorted(by_input):
        candidates = by_input[signature]
        labels = Counter(_assistant_label(row) for row in candidates)
        if len(candidates) > 1:
            duplicate_group_count += 1
        if len(labels) > 1:
            conflicting_group_count += 1

        ranked = labels.most_common()
        has_tie = len(ranked) > 1 and ranked[0][1] == ranked[1][1]
        if has_tie:
            unresolved_tie_count += 1
            selected = None
        else:
            majority_label = ranked[0][0]
            selected = min(
                (row for row in candidates if _assistant_label(row) == majority_label),
                key=_case_id,
            )
            resolved_rows.append(selected)

        if len(labels) > 1:
            conflict_details.append(
                {
                    "input_sha256": signature,
                    "case_ids": sorted(_case_id(row) for row in candidates),
                    "label_counts": {label: labels.get(label, 0) for label in LABEL_ORDER},
                    "resolution": "excluded_tie" if selected is None else "majority_label",
                    "selected_case_id": _case_id(selected) if selected is not None else None,
                    "selected_label": _assistant_label(selected) if selected is not None else None,
                }
            )

    resolved_rows.sort(key=_case_id)
    grouped_indices: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(resolved_rows):
        grouped_indices[_balance_group_key(row)].append(index)
    group_keys = sorted(grouped_indices)
    groups = [grouped_indices[key] for key in group_keys]

    source_by_fingerprint = Counter(_balance_group_key(row)[0] for row in rows)
    resolved_by_fingerprint = Counter(_balance_group_key(row)[0] for row in resolved_rows)
    effective_by_fingerprint: Counter[str] = Counter()
    for key, group in zip(group_keys, groups):
        effective_by_fingerprint[key[0]] += min(samples_per_group, len(group))
    group_sizes = [len(group) for group in groups]
    effective_records = sum(min(samples_per_group, size) for size in group_sizes)
    report = {
        "mode": BALANCED_MODE,
        "seed": seed,
        "samples_per_job_preference": samples_per_group,
        "source_record_count": len(rows),
        "resolved_record_count": len(resolved_rows),
        "rows_removed_by_exact_input_resolution": len(rows) - len(resolved_rows),
        "duplicate_exact_input_group_count": duplicate_group_count,
        "conflicting_exact_input_group_count": conflicting_group_count,
        "unresolved_tie_group_count": unresolved_tie_count,
        "balance_group_count": len(groups),
        "effective_records_per_epoch": effective_records,
        "max_alternatives_per_group": max(group_sizes, default=0),
        "epochs_to_cover_all_alternatives": max(
            (math.ceil(size / samples_per_group) for size in group_sizes),
            default=0,
        ),
        "source_label_distribution": _ordered_distribution([_assistant_label(row) for row in rows]),
        "resolved_label_distribution": _ordered_distribution(
            [_assistant_label(row) for row in resolved_rows]
        ),
        "fingerprint_contributions": [
            {
                "job_fingerprint": fingerprint,
                "source_records": source_by_fingerprint[fingerprint],
                "resolved_records": resolved_by_fingerprint[fingerprint],
                "effective_records_per_epoch": effective_by_fingerprint[fingerprint],
            }
            for fingerprint in sorted(source_by_fingerprint)
        ],
        "conflicts": conflict_details,
    }
    plan = BalancedTrainingPlan(
        rows=resolved_rows,
        groups=groups,
        group_keys=group_keys,
        seed=seed,
        samples_per_group=samples_per_group,
        report=report,
    )
    report["epoch_zero_label_distribution"] = plan.epoch_label_distribution(0)
    return plan


class RotatingGroupSampler:
    def __init__(self, plan: BalancedTrainingPlan):
        self.plan = plan
        self.epoch = 0

    def __iter__(self):
        indices = self.plan.epoch_indices(self.epoch)
        self.epoch += 1
        return iter(indices)

    def __len__(self) -> int:
        return int(self.plan.report["effective_records_per_epoch"])

    def set_epoch(self, epoch: int) -> None:
        self.epoch = int(epoch)


def audit_training_balance(
    rows: list[dict],
    *,
    seed: int,
    samples_per_group: int,
    preview_epochs: int,
) -> dict:
    plan = build_balanced_training_plan(
        rows,
        seed=seed,
        samples_per_group=samples_per_group,
    )
    if preview_epochs <= 0:
        preview_epochs = max(1, int(plan.report["epochs_to_cover_all_alternatives"]))
    aggregate = Counter()
    epochs = []
    for epoch in range(preview_epochs):
        distribution = plan.epoch_label_distribution(epoch)
        aggregate.update(distribution)
        epochs.append({"epoch": epoch, "label_distribution": distribution})
    report = dict(plan.report)
    report["preview_epoch_count"] = preview_epochs
    report["preview_aggregate_label_distribution"] = {
        label: aggregate.get(label, 0) for label in LABEL_ORDER
    }
    report["preview_epochs"] = epochs
    return report


def _read_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit job/preference-balanced training exposure")
    parser.add_argument("--train-jsonl", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--samples-per-job-preference", type=int, default=1)
    parser.add_argument(
        "--preview-epochs",
        type=int,
        default=0,
        help="Epochs to preview; defaults to one complete alternative-coverage window",
    )
    args = parser.parse_args(argv)

    report = audit_training_balance(
        _read_jsonl(args.train_jsonl),
        seed=args.seed,
        samples_per_group=args.samples_per_job_preference,
        preview_epochs=args.preview_epochs,
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(json.dumps({
        "source_record_count": report["source_record_count"],
        "resolved_record_count": report["resolved_record_count"],
        "balance_group_count": report["balance_group_count"],
        "effective_records_per_epoch": report["effective_records_per_epoch"],
        "conflicting_exact_input_group_count": report["conflicting_exact_input_group_count"],
        "unresolved_tie_group_count": report["unresolved_tie_group_count"],
    }, indent=2))
    print(f"[training.balance-audit] report -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
