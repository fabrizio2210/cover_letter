from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.python.ai_scorer.training.training_balance import (
    RotatingGroupSampler,
    audit_training_balance,
    build_balanced_training_plan,
)


_ROOT = Path(__file__).resolve().parents[4]


def _row(
    case_id: str,
    fingerprint: str,
    preference: str,
    user: str,
    label: str,
) -> dict:
    return {
        "messages": [
            {"role": "system", "content": "Score only"},
            {"role": "user", "content": user},
            {"role": "assistant", "content": label},
        ],
        "meta": {
            "case_id": case_id,
            "job_fingerprint": fingerprint,
            "preference_key": preference,
        },
    }


class TrainingBalanceTests(unittest.TestCase):
    def test_exact_input_conflicts_use_majority_and_withhold_ties(self):
        rows = [
            _row("majority-a", "job-a", "remote", "same majority input", "1"),
            _row("majority-b", "job-a", "remote", "same majority input", "1"),
            _row("majority-c", "job-a", "remote", "same majority input", "3"),
            _row("tie-a", "job-b", "coding", "same tied input", "2"),
            _row("tie-b", "job-b", "coding", "same tied input", "4"),
            _row("clean", "job-c", "backend", "clean input", "5"),
        ]

        plan = build_balanced_training_plan(rows, seed=42)

        self.assertEqual(plan.report["source_record_count"], 6)
        self.assertEqual(plan.report["resolved_record_count"], 2)
        self.assertEqual(plan.report["duplicate_exact_input_group_count"], 2)
        self.assertEqual(plan.report["conflicting_exact_input_group_count"], 2)
        self.assertEqual(plan.report["unresolved_tie_group_count"], 1)
        selected = {_row["meta"]["case_id"] for _row in plan.rows}
        self.assertEqual(selected, {"majority-a", "clean"})

    def test_epoch_schedule_equalizes_groups_and_rotates_alternatives(self):
        rows = [
            _row("a-1", "job-a", "remote", "alternative one", "0"),
            _row("a-2", "job-a", "remote", "alternative two", "1"),
            _row("a-3", "job-a", "remote", "alternative three", "2"),
            _row("a-code", "job-a", "coding", "coding", "3"),
            _row("b-remote", "job-b", "remote", "remote", "4"),
        ]
        plan = build_balanced_training_plan(rows, seed=7)

        self.assertEqual(plan.report["balance_group_count"], 3)
        self.assertEqual(plan.report["effective_records_per_epoch"], 3)
        self.assertEqual(plan.report["epochs_to_cover_all_alternatives"], 3)
        alternative_ids = set()
        for epoch in range(3):
            selected = [plan.rows[index] for index in plan.epoch_indices(epoch)]
            self.assertEqual(len(selected), 3)
            self.assertEqual(
                len({(row["meta"]["job_fingerprint"], row["meta"]["preference_key"]) for row in selected}),
                3,
            )
            alternative_ids.update(
                row["meta"]["case_id"]
                for row in selected
                if row["meta"]["job_fingerprint"] == "job-a"
                and row["meta"]["preference_key"] == "remote"
            )
        self.assertEqual(alternative_ids, {"a-1", "a-2", "a-3"})

        sampler = RotatingGroupSampler(plan)
        sampler.set_epoch(2)
        self.assertEqual(list(sampler), plan.epoch_indices(2))

    def test_effective_fingerprint_counts_respect_small_groups(self):
        rows = [
            _row("a-1", "job-a", "remote", "alternative one", "0"),
            _row("a-2", "job-a", "remote", "alternative two", "1"),
            _row("a-code", "job-a", "coding", "coding", "3"),
            _row("b-remote", "job-b", "remote", "remote", "4"),
        ]

        plan = build_balanced_training_plan(rows, seed=7, samples_per_group=2)
        contributions = {
            item["job_fingerprint"]: item["effective_records_per_epoch"]
            for item in plan.report["fingerprint_contributions"]
        }

        self.assertEqual(plan.report["effective_records_per_epoch"], 4)
        self.assertEqual(contributions, {"job-a": 3, "job-b": 1})

    def test_current_export_has_expected_balanced_shape(self):
        path = _ROOT / "src/python/ai_scorer/training/data/export/train.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

        report = audit_training_balance(
            rows,
            seed=42,
            samples_per_group=1,
            preview_epochs=19,
        )

        self.assertEqual(report["source_record_count"], 631)
        self.assertEqual(report["resolved_record_count"], 584)
        self.assertEqual(report["duplicate_exact_input_group_count"], 36)
        self.assertEqual(report["conflicting_exact_input_group_count"], 9)
        self.assertEqual(report["unresolved_tie_group_count"], 1)
        self.assertEqual(report["balance_group_count"], 404)
        self.assertEqual(report["effective_records_per_epoch"], 404)
        self.assertEqual(report["epochs_to_cover_all_alternatives"], 19)


if __name__ == "__main__":
    unittest.main()
