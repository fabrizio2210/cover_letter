from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable


LABEL_BALANCED_MODE = "label-preference-balanced"
JOB_PREFERENCE_BALANCED_MODE = "job-preference-balanced"
BALANCED_MODE = LABEL_BALANCED_MODE
SAMPLING_MODES = (LABEL_BALANCED_MODE, JOB_PREFERENCE_BALANCED_MODE, "all")
LABEL_ORDER = ("0", "1", "2", "3", "4", "5", "N/A")
NUMERIC_LABELS = LABEL_ORDER[:6]
DEFAULT_NA_SHARE = 0.05


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


def _ordered_shares(distribution: dict[str, int]) -> dict[str, float]:
    total = sum(distribution.values())
    return {
        label: round(distribution.get(label, 0) / total, 6) if total else 0.0
        for label in LABEL_ORDER
    }


def _resolve_exact_inputs(rows: list[dict]) -> tuple[list[dict], dict]:
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
    return resolved_rows, {
        "duplicate_exact_input_group_count": duplicate_group_count,
        "conflicting_exact_input_group_count": conflicting_group_count,
        "unresolved_tie_group_count": unresolved_tie_count,
        "conflicts": conflict_details,
    }


Slot = tuple[str, int]


def _assign_distinct_groups(
    label_groups: dict[str, list[int]],
    quotas: dict[str, int],
    candidate_order: Callable[[str, int], list[int]],
) -> dict[Slot, int] | None:
    slots = [
        (label, ordinal)
        for label in LABEL_ORDER
        for ordinal in range(quotas.get(label, 0))
    ]
    label_position = {label: index for index, label in enumerate(LABEL_ORDER)}
    slots.sort(
        key=lambda slot: (
            len(label_groups.get(slot[0], [])) / max(1, quotas.get(slot[0], 0)),
            label_position[slot[0]],
            slot[1],
        )
    )
    group_to_slot: dict[int, Slot] = {}
    slot_to_group: dict[Slot, int] = {}

    def augment(slot: Slot, seen_groups: set[int]) -> bool:
        label, ordinal = slot
        for group_index in candidate_order(label, ordinal):
            if group_index in seen_groups:
                continue
            seen_groups.add(group_index)
            incumbent = group_to_slot.get(group_index)
            if incumbent is None or augment(incumbent, seen_groups):
                group_to_slot[group_index] = slot
                slot_to_group[slot] = group_index
                return True
        return False

    for slot in slots:
        if not augment(slot, set()):
            return None
    return slot_to_group


def _feasible_quotas(label_groups: dict[str, list[int]], quotas: dict[str, int]) -> bool:
    assignment = _assign_distinct_groups(
        label_groups,
        quotas,
        lambda label, _ordinal: list(label_groups.get(label, [])),
    )
    return assignment is not None


def _derive_label_quotas(
    label_groups: dict[str, list[int]],
    *,
    samples_per_label: int,
    na_share: float,
) -> tuple[dict[str, int], dict]:
    if samples_per_label < 0:
        raise ValueError("samples_per_label cannot be negative")
    if na_share < 0 or na_share >= 1:
        raise ValueError("na_share must satisfy: 0 <= na_share < 1")

    missing = [label for label in NUMERIC_LABELS if not label_groups.get(label)]
    if missing:
        raise ValueError(
            "Label-balanced sampling requires every numeric label 0..5; missing: "
            + ", ".join(missing)
        )

    capacity = min(len(label_groups[label]) for label in NUMERIC_LABELS)
    automatic_capacity = capacity - 1 if capacity > 1 else capacity
    requested = samples_per_label or automatic_capacity
    if samples_per_label and samples_per_label > capacity:
        raise ValueError(
            f"samples_per_label={samples_per_label} exceeds the smallest numeric-label "
            f"group capacity ({capacity})"
        )

    numeric_quota = requested
    while numeric_quota > 0:
        numeric = {label: numeric_quota for label in NUMERIC_LABELS}
        if _feasible_quotas(label_groups, numeric):
            break
        numeric_quota -= 1
    if numeric_quota <= 0:
        raise ValueError("Could not construct a job/preference-distinct numeric-label schedule")
    if samples_per_label and numeric_quota != samples_per_label:
        raise ValueError(
            f"samples_per_label={samples_per_label} is not jointly feasible across job/preference groups"
        )

    numeric_total = numeric_quota * len(NUMERIC_LABELS)
    requested_na = (
        round(numeric_total * na_share / (1 - na_share))
        if na_share > 0
        else 0
    )
    na_capacity = len(label_groups.get("N/A", []))
    automatic_na_capacity = na_capacity - 1 if na_capacity > 1 else na_capacity
    na_quota = min(requested_na, automatic_na_capacity)
    quotas = {label: numeric_quota for label in NUMERIC_LABELS}
    quotas["N/A"] = na_quota
    while na_quota > 0 and not _feasible_quotas(label_groups, quotas):
        na_quota -= 1
        quotas["N/A"] = na_quota

    return quotas, {
        "requested_samples_per_label": samples_per_label or "auto",
        "numeric_group_capacity_floor": capacity,
        "automatic_rotation_slack_groups": capacity - automatic_capacity,
        "requested_na_share": na_share,
        "requested_na_records_per_epoch": requested_na,
        "na_group_capacity": na_capacity,
        "automatic_na_rotation_slack_groups": na_capacity - automatic_na_capacity,
    }


@dataclass
class BalancedTrainingPlan:
    rows: list[dict]
    groups: list[list[int]]
    group_keys: list[tuple[str, str]]
    seed: int
    samples_per_group: int
    mode: str
    report: dict
    label_quotas: dict[str, int] = field(default_factory=dict)
    label_groups: dict[str, list[int]] = field(default_factory=dict)
    group_label_indices: dict[tuple[int, str], list[int]] = field(default_factory=dict)
    _epoch_cache: dict[int, list[int]] = field(default_factory=dict, init=False, repr=False)
    _unit_selection_counts: Counter = field(default_factory=Counter, init=False, repr=False)
    _preference_selection_counts: Counter = field(default_factory=Counter, init=False, repr=False)
    _next_uncached_epoch: int = field(default=0, init=False, repr=False)

    def _job_preference_epoch_indices(self, epoch: int) -> list[int]:
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

    def _min_cost_group_assignment(self, epoch: int) -> list[tuple[str, int]]:
        source = 0
        next_node = 1
        label_nodes: dict[str, int] = {}
        preference_nodes: dict[tuple[str, str], int] = {}
        group_nodes: dict[int, int] = {}
        for label in LABEL_ORDER:
            if self.label_quotas.get(label, 0):
                label_nodes[label] = next_node
                next_node += 1
        for label in label_nodes:
            preferences = sorted(
                {self.group_keys[group_index][1] for group_index in self.label_groups[label]}
            )
            for preference in preferences:
                preference_nodes[(label, preference)] = next_node
                next_node += 1
        for group_index in range(len(self.groups)):
            group_nodes[group_index] = next_node
            next_node += 1
        sink = next_node
        graph: list[list[dict]] = [[] for _ in range(sink + 1)]

        def add_edge(
            start: int,
            end: int,
            capacity: int,
            cost: int,
            assignment: tuple[str, int] | None = None,
        ) -> None:
            forward = {
                "to": end,
                "reverse": len(graph[end]),
                "capacity": capacity,
                "cost": cost,
                "assignment": assignment,
                "original_capacity": capacity,
            }
            reverse = {
                "to": start,
                "reverse": len(graph[start]),
                "capacity": 0,
                "cost": -cost,
                "assignment": None,
                "original_capacity": 0,
            }
            graph[start].append(forward)
            graph[end].append(reverse)

        preference_weight = 100_000
        unit_weight = 1_000_000_000
        for label, label_node in label_nodes.items():
            quota = self.label_quotas[label]
            add_edge(source, label_node, quota, 0)
            preferences = sorted(
                preference
                for candidate_label, preference in preference_nodes
                if candidate_label == label
            )
            for preference in preferences:
                preference_node = preference_nodes[(label, preference)]
                prior = self._preference_selection_counts[(label, preference)]
                for ordinal in range(quota):
                    add_edge(
                        label_node,
                        preference_node,
                        1,
                        (prior + ordinal) * preference_weight,
                    )
            for group_index in self.label_groups[label]:
                preference = self.group_keys[group_index][1]
                tie_break = int(
                    _stable_hash(
                        [self.seed, epoch, label, self.group_keys[group_index]]
                    )[:8],
                    16,
                ) % preference_weight
                cost = self._unit_selection_counts[(group_index, label)] * unit_weight + tie_break
                add_edge(
                    preference_nodes[(label, preference)],
                    group_nodes[group_index],
                    1,
                    cost,
                    assignment=(label, group_index),
                )
        for group_node in group_nodes.values():
            add_edge(group_node, sink, 1, 0)

        required_flow = sum(self.label_quotas.values())
        flow = 0
        while flow < required_flow:
            distance = [math.inf] * len(graph)
            parent: list[tuple[int, int] | None] = [None] * len(graph)
            in_queue = [False] * len(graph)
            queue = [source]
            distance[source] = 0
            in_queue[source] = True
            cursor = 0
            while cursor < len(queue):
                node = queue[cursor]
                cursor += 1
                in_queue[node] = False
                for edge_index, edge in enumerate(graph[node]):
                    if edge["capacity"] <= 0:
                        continue
                    candidate = distance[node] + edge["cost"]
                    if candidate >= distance[edge["to"]]:
                        continue
                    distance[edge["to"]] = candidate
                    parent[edge["to"]] = (node, edge_index)
                    if not in_queue[edge["to"]]:
                        queue.append(edge["to"])
                        in_queue[edge["to"]] = True
            if parent[sink] is None:
                raise RuntimeError("Could not construct the persisted label-balanced epoch")

            node = sink
            while node != source:
                previous, edge_index = parent[node]  # type: ignore[misc]
                edge = graph[previous][edge_index]
                edge["capacity"] -= 1
                reverse = graph[node][edge["reverse"]]
                reverse["capacity"] += 1
                node = previous
            flow += 1

        assignments: list[tuple[str, int]] = []
        for preference_node in preference_nodes.values():
            for edge in graph[preference_node]:
                assignment = edge["assignment"]
                if (
                    assignment is not None
                    and edge["original_capacity"] == 1
                    and edge["capacity"] == 0
                ):
                    assignments.append(assignment)
        if len(assignments) != required_flow:
            raise RuntimeError("Label-balanced assignment accounting is inconsistent")
        return assignments

    def _build_label_balanced_epoch(self, epoch: int) -> list[int]:
        selected: list[int] = []
        for label, group_index in sorted(self._min_cost_group_assignment(epoch)):
            alternatives = sorted(
                self.group_label_indices[(group_index, label)],
                key=lambda index: _stable_hash(
                    [self.seed, self.group_keys[group_index], label, _case_id(self.rows[index])]
                ),
            )
            occurrence = self._unit_selection_counts[(group_index, label)]
            selected.append(alternatives[occurrence % len(alternatives)])
            self._unit_selection_counts[(group_index, label)] += 1
            preference = self.group_keys[group_index][1]
            self._preference_selection_counts[(label, preference)] += 1

        rng = random.Random(self.seed + epoch)
        rng.shuffle(selected)
        return selected

    def _ensure_epoch(self, epoch: int) -> None:
        while self._next_uncached_epoch <= epoch:
            current = self._next_uncached_epoch
            self._epoch_cache[current] = self._build_label_balanced_epoch(current)
            self._next_uncached_epoch += 1

    def epoch_indices(self, epoch: int) -> list[int]:
        if epoch < 0:
            raise ValueError("epoch cannot be negative")
        if self.mode == JOB_PREFERENCE_BALANCED_MODE:
            return self._job_preference_epoch_indices(epoch)
        self._ensure_epoch(epoch)
        return list(self._epoch_cache[epoch])

    def epoch_label_distribution(self, epoch: int) -> dict[str, int]:
        return _ordered_distribution(
            [_assistant_label(self.rows[index]) for index in self.epoch_indices(epoch)]
        )

    def epoch_preference_distribution(self, epoch: int) -> dict[str, int]:
        counts = Counter(_balance_group_key(self.rows[index])[1] for index in self.epoch_indices(epoch))
        return dict(sorted(counts.items()))


def build_balanced_training_plan(
    rows: list[dict],
    *,
    seed: int,
    samples_per_group: int = 1,
    samples_per_label: int = 0,
    na_share: float = DEFAULT_NA_SHARE,
    mode: str = BALANCED_MODE,
) -> BalancedTrainingPlan:
    if mode not in {LABEL_BALANCED_MODE, JOB_PREFERENCE_BALANCED_MODE}:
        raise ValueError(f"Unsupported balanced sampling mode: {mode!r}")
    if samples_per_group <= 0:
        raise ValueError("samples_per_group must be greater than zero")

    resolved_rows, resolution = _resolve_exact_inputs(rows)
    grouped_indices: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(resolved_rows):
        grouped_indices[_balance_group_key(row)].append(index)
    group_keys = sorted(grouped_indices)
    groups = [grouped_indices[key] for key in group_keys]

    source_by_fingerprint = Counter(_balance_group_key(row)[0] for row in rows)
    resolved_by_fingerprint = Counter(_balance_group_key(row)[0] for row in resolved_rows)
    group_sizes = [len(group) for group in groups]
    source_distribution = _ordered_distribution([_assistant_label(row) for row in rows])
    resolved_distribution = _ordered_distribution([_assistant_label(row) for row in resolved_rows])

    report = {
        "mode": mode,
        "seed": seed,
        "source_record_count": len(rows),
        "resolved_record_count": len(resolved_rows),
        "rows_removed_by_exact_input_resolution": len(rows) - len(resolved_rows),
        **resolution,
        "balance_group_count": len(groups),
        "max_alternatives_per_group": max(group_sizes, default=0),
        "source_label_distribution": source_distribution,
        "resolved_label_distribution": resolved_distribution,
    }

    if mode == JOB_PREFERENCE_BALANCED_MODE:
        effective_by_fingerprint: Counter[str] = Counter()
        for key, group in zip(group_keys, groups):
            effective_by_fingerprint[key[0]] += min(samples_per_group, len(group))
        effective_records = sum(min(samples_per_group, size) for size in group_sizes)
        report.update(
            {
                "samples_per_job_preference": samples_per_group,
                "effective_records_per_epoch": effective_records,
                "epochs_to_cover_all_alternatives": max(
                    (math.ceil(size / samples_per_group) for size in group_sizes),
                    default=0,
                ),
                "rotation_window_epochs": max(
                    (math.ceil(size / samples_per_group) for size in group_sizes),
                    default=0,
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
            }
        )
        plan = BalancedTrainingPlan(
            rows=resolved_rows,
            groups=groups,
            group_keys=group_keys,
            seed=seed,
            samples_per_group=samples_per_group,
            mode=mode,
            report=report,
        )
    else:
        group_label_indices: dict[tuple[int, str], list[int]] = defaultdict(list)
        for group_index, group in enumerate(groups):
            for row_index in group:
                group_label_indices[(group_index, _assistant_label(resolved_rows[row_index]))].append(
                    row_index
                )
        label_groups = {
            label: sorted(
                group_index
                for group_index in range(len(groups))
                if (group_index, label) in group_label_indices
            )
            for label in LABEL_ORDER
        }
        label_quotas, quota_report = _derive_label_quotas(
            label_groups,
            samples_per_label=samples_per_label,
            na_share=na_share,
        )
        effective_records = sum(label_quotas.values())
        rotation_window = max(
            (
                math.ceil(resolved_distribution[label] / quota)
                for label, quota in label_quotas.items()
                if quota > 0
            ),
            default=1,
        )
        report.update(
            {
                **quota_report,
                "samples_per_label": label_quotas["0"],
                "label_quotas_per_epoch": label_quotas,
                "label_group_availability": {
                    label: len(label_groups[label]) for label in LABEL_ORDER
                },
                "effective_records_per_epoch": effective_records,
                "rotation_window_lower_bound_epochs": rotation_window,
                "rotation_window_epochs": rotation_window,
            }
        )
        plan = BalancedTrainingPlan(
            rows=resolved_rows,
            groups=groups,
            group_keys=group_keys,
            seed=seed,
            samples_per_group=samples_per_group,
            mode=mode,
            report=report,
            label_quotas=label_quotas,
            label_groups=label_groups,
            group_label_indices=dict(group_label_indices),
        )

        eligible_indices = {
            index
            for index, row in enumerate(plan.rows)
            if label_quotas.get(_assistant_label(row), 0) > 0
        }
        coverage_limit = max(100, rotation_window * 10)
        covered_indices: set[int] = set()
        verified_coverage_epochs: int | None = None
        for epoch in range(coverage_limit):
            covered_indices.update(plan.epoch_indices(epoch))
            if eligible_indices <= covered_indices:
                verified_coverage_epochs = epoch + 1
                break
        report.update(
            {
                "rotation_eligible_record_count": len(eligible_indices),
                "full_rotation_coverage_verified": verified_coverage_epochs is not None,
                "rotation_coverage_verification_limit": coverage_limit,
                "epochs_to_cover_all_alternatives": verified_coverage_epochs,
                "rotation_window_epochs": verified_coverage_epochs or rotation_window,
            }
        )

    epoch_zero_indices = plan.epoch_indices(0)
    epoch_zero_distribution = plan.epoch_label_distribution(0)
    epoch_zero_by_fingerprint = Counter(
        _balance_group_key(plan.rows[index])[0] for index in epoch_zero_indices
    )
    report["epoch_zero_label_distribution"] = epoch_zero_distribution
    report["epoch_zero_label_share"] = _ordered_shares(epoch_zero_distribution)
    report["epoch_zero_preference_distribution"] = plan.epoch_preference_distribution(0)
    if mode == LABEL_BALANCED_MODE:
        report["fingerprint_contributions"] = [
            {
                "job_fingerprint": fingerprint,
                "source_records": source_by_fingerprint[fingerprint],
                "resolved_records": resolved_by_fingerprint[fingerprint],
                "epoch_zero_records": epoch_zero_by_fingerprint[fingerprint],
            }
            for fingerprint in sorted(source_by_fingerprint)
        ]
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
    samples_per_label: int = 0,
    na_share: float = DEFAULT_NA_SHARE,
    mode: str = BALANCED_MODE,
) -> dict:
    plan = build_balanced_training_plan(
        rows,
        seed=seed,
        samples_per_group=samples_per_group,
        samples_per_label=samples_per_label,
        na_share=na_share,
        mode=mode,
    )
    if preview_epochs <= 0:
        preview_epochs = max(1, int(plan.report["rotation_window_epochs"]))
    aggregate = Counter()
    unique_indices: set[int] = set()
    epochs = []
    for epoch in range(preview_epochs):
        indices = plan.epoch_indices(epoch)
        distribution = plan.epoch_label_distribution(epoch)
        aggregate.update(distribution)
        unique_indices.update(indices)
        epochs.append(
            {
                "epoch": epoch,
                "label_distribution": distribution,
                "label_share": _ordered_shares(distribution),
                "preference_distribution": plan.epoch_preference_distribution(epoch),
            }
        )
    report = dict(plan.report)
    report["preview_epoch_count"] = preview_epochs
    report["preview_aggregate_label_distribution"] = {
        label: aggregate.get(label, 0) for label in LABEL_ORDER
    }
    report["preview_aggregate_label_share"] = _ordered_shares(
        report["preview_aggregate_label_distribution"]
    )
    report["preview_unique_record_count"] = len(unique_indices)
    report["preview_resolved_record_coverage"] = round(
        len(unique_indices) / len(plan.rows), 6
    ) if plan.rows else 0.0
    report["preview_epochs"] = epochs
    return report


def _read_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _fraction(value: str) -> float:
    parsed = float(value)
    if parsed < 0 or parsed >= 1:
        raise argparse.ArgumentTypeError("must satisfy: 0 <= value < 1")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit balanced training exposure")
    parser.add_argument("--train-jsonl", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--sampling-mode",
        choices=(LABEL_BALANCED_MODE, JOB_PREFERENCE_BALANCED_MODE),
        default=BALANCED_MODE,
    )
    parser.add_argument("--samples-per-job-preference", type=int, default=1)
    parser.add_argument(
        "--samples-per-label",
        type=int,
        default=0,
        help="Numeric records per score and epoch; 0 uses the largest feasible equal quota",
    )
    parser.add_argument("--na-share", type=_fraction, default=DEFAULT_NA_SHARE)
    parser.add_argument(
        "--preview-epochs",
        type=int,
        default=0,
        help="Epochs to preview; defaults to one estimated rotation window",
    )
    args = parser.parse_args(argv)

    report = audit_training_balance(
        _read_jsonl(args.train_jsonl),
        seed=args.seed,
        samples_per_group=args.samples_per_job_preference,
        samples_per_label=args.samples_per_label,
        na_share=args.na_share,
        mode=args.sampling_mode,
        preview_epochs=args.preview_epochs,
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "source_record_count": report["source_record_count"],
                "resolved_record_count": report["resolved_record_count"],
                "balance_group_count": report["balance_group_count"],
                "effective_records_per_epoch": report["effective_records_per_epoch"],
                "epoch_zero_label_distribution": report["epoch_zero_label_distribution"],
                "conflicting_exact_input_group_count": report[
                    "conflicting_exact_input_group_count"
                ],
                "unresolved_tie_group_count": report["unresolved_tie_group_count"],
            },
            indent=2,
        )
    )
    print(f"[training.balance-audit] report -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
