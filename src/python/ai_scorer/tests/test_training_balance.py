from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.python.ai_scorer.training.training_balance import (
    JOB_PREFERENCE_BALANCED_MODE,
    LABEL_BALANCED_MODE,
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

        plan = build_balanced_training_plan(
            rows,
            seed=42,
            mode=JOB_PREFERENCE_BALANCED_MODE,
        )

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
        plan = build_balanced_training_plan(
            rows,
            seed=7,
            mode=JOB_PREFERENCE_BALANCED_MODE,
        )

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

        plan = build_balanced_training_plan(
            rows,
            seed=7,
            samples_per_group=2,
            mode=JOB_PREFERENCE_BALANCED_MODE,
        )
        contributions = {
            item["job_fingerprint"]: item["effective_records_per_epoch"]
            for item in plan.report["fingerprint_contributions"]
        }

        self.assertEqual(plan.report["effective_records_per_epoch"], 4)
        self.assertEqual(contributions, {"job-a": 3, "job-b": 1})

    def test_label_balanced_schedule_is_even_and_rotates_surplus(self):
        rows = []
        for label in ("0", "1", "2", "3", "4", "5"):
            count = 4 if label == "4" else 2
            rows.extend(
                _row(
                    f"case-{label}-{index}",
                    f"job-{label}-{index}",
                    f"preference-{index % 2}",
                    f"input-{label}-{index}",
                    label,
                )
                for index in range(count)
            )
        rows.extend(
            [
                _row("na-0", "job-na-0", "preference-0", "na-input-0", "N/A"),
                _row("na-1", "job-na-1", "preference-1", "na-input-1", "N/A"),
            ]
        )

        plan = build_balanced_training_plan(
            rows,
            seed=42,
            samples_per_label=2,
            na_share=0.08,
        )

        expected = {"0": 2, "1": 2, "2": 2, "3": 2, "4": 2, "5": 2, "N/A": 1}
        self.assertEqual(plan.mode, LABEL_BALANCED_MODE)
        self.assertEqual(plan.epoch_label_distribution(0), expected)
        self.assertEqual(plan.epoch_label_distribution(1), expected)
        selected_score_four = {
            plan.rows[index]["meta"]["case_id"]
            for epoch in range(2)
            for index in plan.epoch_indices(epoch)
            if plan.rows[index]["messages"][-1]["content"] == "4"
        }
        self.assertEqual(selected_score_four, {"case-4-0", "case-4-1", "case-4-2", "case-4-3"})
        for epoch in range(2):
            keys = [
                (
                    plan.rows[index]["meta"]["job_fingerprint"],
                    plan.rows[index]["meta"]["preference_key"],
                )
                for index in plan.epoch_indices(epoch)
            ]
            self.assertEqual(len(keys), len(set(keys)))

    def test_label_balanced_rotation_shares_a_group_between_labels(self):
        rows = [
            _row("shared-zero", "shared-job", "shared-preference", "zero input", "0"),
            _row("other-zero", "zero-job", "zero-preference", "other zero", "0"),
            _row("shared-na", "shared-job", "shared-preference", "na input", "N/A"),
            _row("other-na", "na-job", "na-preference", "other na", "N/A"),
        ]
        for label in ("1", "2", "3", "4", "5"):
            rows.extend(
                [
                    _row(f"{label}-a", f"job-{label}-a", "preference-a", f"{label} a", label),
                    _row(f"{label}-b", f"job-{label}-b", "preference-b", f"{label} b", label),
                ]
            )

        plan = build_balanced_training_plan(rows, seed=11, na_share=0.15)
        selected = {
            plan.rows[index]["meta"]["case_id"]
            for epoch in range(6)
            for index in plan.epoch_indices(epoch)
        }

        self.assertIn("shared-zero", selected)
        self.assertIn("shared-na", selected)
        for epoch in range(6):
            epoch_ids = {
                plan.rows[index]["meta"]["case_id"] for index in plan.epoch_indices(epoch)
            }
            self.assertFalse({"shared-zero", "shared-na"} <= epoch_ids)

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
        self.assertEqual(report["mode"], LABEL_BALANCED_MODE)
        self.assertEqual(report["label_quotas_per_epoch"], {
            "0": 15,
            "1": 15,
            "2": 15,
            "3": 15,
            "4": 15,
            "5": 15,
            "N/A": 3,
        })
        self.assertEqual(report["effective_records_per_epoch"], 93)
        self.assertEqual(report["epoch_zero_label_distribution"], report["label_quotas_per_epoch"])
        self.assertEqual(report["rotation_window_lower_bound_epochs"], 15)
        self.assertEqual(report["epochs_to_cover_all_alternatives"], 40)
        self.assertTrue(report["full_rotation_coverage_verified"])


if __name__ == "__main__":
    unittest.main()
