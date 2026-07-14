from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.python.ai_scorer.training.exporter import _split_cases, export_jsonl_splits
from src.python.ai_scorer.training.fine_tune_manifest import collect_jsonl_paths
from src.python.ai_scorer.training.fine_tune_preflight import run_preflight
from src.python.ai_scorer.training.schema import TrainingCase


def _case(job_index: int, preference_index: int) -> TrainingCase:
    return TrainingCase(
        case_id=f"case-{job_index:02d}-{preference_index:02d}",
        source_job_id=f"job-{job_index:02d}",
        title=f"Job {job_index}",
        location="Remote",
        preference_key=f"preference-{preference_index:02d}",
        preference_guidance=f"Preference {preference_index}",
        relevant_snippets=["Relevant context"],
        system_prompt="Return one score.",
        user_prompt="Score this job.",
        label_score=preference_index % 6,
        label_available=True,
    )


def _cases(job_count: int, preferences_per_job: int) -> list[TrainingCase]:
    return [
        _case(job_index, preference_index)
        for job_index in range(job_count)
        for preference_index in range(preferences_per_job)
    ]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_preflight_split(path: Path, case_id: str, source_job_id: str) -> None:
    row = {
        "messages": [
            {"role": "system", "content": "Return one score."},
            {"role": "user", "content": "Score this job."},
            {"role": "assistant", "content": "3"},
        ],
        "meta": {
            "case_id": case_id,
            "source_job_id": source_job_id,
            "preference_key": "preference",
        },
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


class TrainingSplitTests(unittest.TestCase):
    def test_default_shape_is_450_train_and_50_val_grouped_by_job(self):
        train, val = _split_cases(_cases(50, 10), seed=42, val_ratio=0.1)

        self.assertEqual(len(train), 450)
        self.assertEqual(len(val), 50)
        train_jobs = {case.source_job_id for case in train}
        val_jobs = {case.source_job_id for case in val}
        self.assertTrue(train_jobs.isdisjoint(val_jobs))
        self.assertEqual(len(train_jobs), 45)
        self.assertEqual(len(val_jobs), 5)

    def test_split_is_deterministic_and_assigns_every_case_once(self):
        cases = _cases(10, 3)
        first_train, first_val = _split_cases(cases, seed=7, val_ratio=0.2)
        second_train, second_val = _split_cases(cases, seed=7, val_ratio=0.2)

        self.assertEqual(
            [case.case_id for case in first_train],
            [case.case_id for case in second_train],
        )
        self.assertEqual(
            [case.case_id for case in first_val],
            [case.case_id for case in second_val],
        )
        assigned_ids = {case.case_id for case in first_train + first_val}
        self.assertEqual(assigned_ids, {case.case_id for case in cases})
        self.assertEqual(len(first_train) + len(first_val), len(cases))

    def test_export_writes_only_train_and_val_and_removes_stale_test(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            stale_test = output_dir / "test.jsonl"
            stale_test.write_text("stale\n", encoding="utf-8")

            paths = export_jsonl_splits(
                _cases(10, 2),
                output_dir=str(output_dir),
                seed=42,
                val_ratio=0.1,
            )

            self.assertEqual(set(paths), {"train", "val", "summary"})
            self.assertTrue((output_dir / "train.jsonl").is_file())
            self.assertTrue((output_dir / "val.jsonl").is_file())
            self.assertFalse(stale_test.exists())

            train_rows = _read_jsonl(output_dir / "train.jsonl")
            val_rows = _read_jsonl(output_dir / "val.jsonl")
            train_jobs = {row["meta"]["source_job_id"] for row in train_rows}
            val_jobs = {row["meta"]["source_job_id"] for row in val_rows}
            self.assertTrue(train_jobs.isdisjoint(val_jobs))

            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertNotIn("test", summary)
            self.assertEqual(summary["split_unit"], "source_job_id")

    def test_split_rejects_invalid_ratios_and_a_single_job(self):
        for val_ratio in (0, 1, -0.1, 1.1):
            with self.subTest(val_ratio=val_ratio):
                with self.assertRaises(ValueError):
                    _split_cases(_cases(2, 1), seed=42, val_ratio=val_ratio)

        with self.assertRaises(ValueError):
            _split_cases(_cases(1, 10), seed=42, val_ratio=0.1)

    def test_preflight_rejects_source_job_overlap_and_empty_splits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_dir = Path(temp_dir)
            _write_preflight_split(dataset_dir / "train.jsonl", "train-case", "shared-job")
            _write_preflight_split(dataset_dir / "val.jsonl", "val-case", "shared-job")

            report = run_preflight(str(dataset_dir), ["train", "val"])

            self.assertGreater(report.critical_error_count, 0)
            self.assertEqual(report.overlapping_source_job_ids, ["shared-job"])

            (dataset_dir / "val.jsonl").write_text("", encoding="utf-8")
            empty_report = run_preflight(str(dataset_dir), ["train", "val"])
            val_report = next(item for item in empty_report.split_reports if item.split == "val")
            self.assertIn("split file is empty", val_report.critical_errors)

    def test_manifest_collection_ignores_stale_test_split(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_dir = Path(temp_dir)
            for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
                (dataset_dir / name).write_text("{}\n", encoding="utf-8")

            collected = collect_jsonl_paths(str(dataset_dir))

            self.assertEqual(
                [Path(path).name for path in collected],
                ["train.jsonl", "val.jsonl"],
            )


if __name__ == "__main__":
    unittest.main()
