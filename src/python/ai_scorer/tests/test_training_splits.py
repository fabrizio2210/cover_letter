from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.python.ai_scorer.training.dataset_split import build_split_manifest, write_split_manifest
from src.python.ai_scorer.training.exporter import export_jsonl_splits
from src.python.ai_scorer.training.fine_tune_manifest import collect_jsonl_paths
from src.python.ai_scorer.training.fine_tune_preflight import run_preflight
from src.python.ai_scorer.job_fingerprint import (
    description_fingerprint,
    legacy_partial_fingerprint,
    partition_fingerprints,
)
from src.python.ai_scorer.training.schema import TrainingCase


def _fingerprint(name: str) -> tuple[str, str]:
    return description_fingerprint(f"Unique job description for {name}.")


def _case(job_index: int, preference_index: int) -> TrainingCase:
    fingerprint, basis = _fingerprint(f"job-{job_index:02d}")
    return TrainingCase(
        case_id=f"case-{job_index:02d}-{preference_index:02d}",
        job_fingerprint=fingerprint,
        fingerprint_basis=basis,
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


def _write_golden(path: Path) -> str:
    fingerprint, basis = description_fingerprint("Golden promotion description")
    raw = {
        "meta": {"fixture_model": "test", "reference_metrics": {}, "format_version": "2"},
        "cases": [
            {
                "case_id": "golden-case",
                "job_fingerprint": fingerprint,
                "fingerprint_basis": basis,
                "title": "Golden job",
                "description": "Golden promotion description",
                "location": "Remote",
                "preference_key": "remote",
                "preference_guidance": "Remote work",
                "expected_score": 5,
                "expected_score_available": True,
                "rationale": "test",
                "tags": [],
                "schema_version": "2",
                "provenance": None,
            }
        ],
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    return fingerprint


def _manifest(path: Path, train: list[str], val: list[str], *, excluded: list[str] | None = None) -> dict:
    golden_path = path / "golden.json"
    golden_fingerprint = _write_golden(golden_path)
    manifest = build_split_manifest(
        train_fingerprints=train,
        val_fingerprints=val,
        golden_fingerprints=[golden_fingerprint],
        confirmed_promotion_fingerprints=excluded or [],
        seed=42,
        val_ratio=0.1,
        golden_fixture_path=str(golden_path),
        golden_case_count=1,
    )
    return manifest


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_preflight_split(path: Path, case_id: str, job_fingerprint: str) -> None:
    row = {
        "messages": [
            {"role": "system", "content": "Return one score."},
            {"role": "user", "content": "Score this job."},
            {"role": "assistant", "content": "3"},
        ],
        "meta": {
            "case_id": case_id,
            "job_fingerprint": job_fingerprint,
            "fingerprint_basis": job_fingerprint.split(":")[2],
            "preference_key": "preference",
        },
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


class TrainingSplitTests(unittest.TestCase):
    def test_default_shape_is_45_train_and_5_val_fingerprints(self):
        fingerprints = [_fingerprint(f"job-{index}")[0] for index in range(50)]

        train, val = partition_fingerprints(fingerprints, seed=42, val_ratio=0.1)

        self.assertEqual(len(train), 45)
        self.assertEqual(len(val), 5)
        self.assertTrue(set(train).isdisjoint(val))

    def test_partition_is_deterministic(self):
        fingerprints = [_fingerprint(f"job-{index}")[0] for index in range(10)]

        first = partition_fingerprints(fingerprints, seed=7, val_ratio=0.2)
        second = partition_fingerprints(reversed(fingerprints), seed=7, val_ratio=0.2)

        self.assertEqual(first, second)

    def test_export_consumes_manifest_and_keeps_excluded_labels_out(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            cases = _cases(3, 2)
            fingerprints = sorted({case.job_fingerprint for case in cases})
            manifest = _manifest(
                output_dir,
                [fingerprints[0]],
                [fingerprints[1]],
                excluded=[fingerprints[2]],
            )
            stale_test = output_dir / "test.jsonl"
            stale_test.write_text("stale\n", encoding="utf-8")

            paths = export_jsonl_splits(cases, str(output_dir), manifest)

            self.assertEqual(set(paths), {"train", "val", "summary", "split_manifest"})
            self.assertFalse(stale_test.exists())
            train_rows = _read_jsonl(output_dir / "train.jsonl")
            val_rows = _read_jsonl(output_dir / "val.jsonl")
            self.assertEqual({row["meta"]["job_fingerprint"] for row in train_rows}, {fingerprints[0]})
            self.assertEqual({row["meta"]["job_fingerprint"] for row in val_rows}, {fingerprints[1]})
            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["excluded"], 2)
            self.assertEqual(summary["split_unit"], "job_fingerprint")

    def test_partition_rejects_invalid_ratios_and_a_single_fingerprint(self):
        fingerprints = [_fingerprint("one")[0], _fingerprint("two")[0]]
        for val_ratio in (0, 1, -0.1, 1.1):
            with self.subTest(val_ratio=val_ratio):
                with self.assertRaises(ValueError):
                    partition_fingerprints(fingerprints, seed=42, val_ratio=val_ratio)
        with self.assertRaises(ValueError):
            partition_fingerprints(fingerprints[:1], seed=42, val_ratio=0.1)

    def test_preflight_rejects_fingerprint_overlap_and_empty_splits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_dir = Path(temp_dir)
            shared = _fingerprint("shared")[0]
            val_assigned = _fingerprint("val")[0]
            manifest = _manifest(dataset_dir, [shared], [val_assigned])
            write_split_manifest(manifest, str(dataset_dir / "split-manifest.json"))
            _write_preflight_split(dataset_dir / "train.jsonl", "train-case", shared)
            _write_preflight_split(dataset_dir / "val.jsonl", "val-case", shared)

            report = run_preflight(str(dataset_dir), ["train", "val"])

            self.assertGreater(report.critical_error_count, 0)
            self.assertEqual(report.overlapping_job_fingerprints, [shared])

            (dataset_dir / "val.jsonl").write_text("", encoding="utf-8")
            empty_report = run_preflight(str(dataset_dir), ["train", "val"])
            val_report = next(item for item in empty_report.split_reports if item.split == "val")
            self.assertIn("split file is empty", val_report.critical_errors)

    def test_manifest_collection_ignores_metadata_and_stale_test_split(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_dir = Path(temp_dir)
            for name in ("train.jsonl", "val.jsonl", "test.jsonl", "split-manifest.json"):
                (dataset_dir / name).write_text("{}\n", encoding="utf-8")

            collected = collect_jsonl_paths(str(dataset_dir))

            self.assertEqual([Path(path).name for path in collected], ["train.jsonl", "val.jsonl"])

    def test_mixed_fingerprint_bases_require_reviewed_mappings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            golden_path = root / "golden.json"
            golden = _write_golden(golden_path)
            legacy, _, _ = legacy_partial_fingerprint(["Legacy evidence"])
            validation_legacy, _, _ = legacy_partial_fingerprint(["Validation legacy evidence"])
            mapped_legacy, _, _ = legacy_partial_fingerprint(["Mapped legacy evidence"])
            full, _ = description_fingerprint("Mapped full description")

            with self.assertRaisesRegex(ValueError, "reviewed mappings"):
                build_split_manifest(
                    train_fingerprints=[legacy, full],
                    val_fingerprints=[validation_legacy],
                    golden_fingerprints=[golden],
                    seed=42,
                    val_ratio=0.1,
                    golden_fixture_path=str(golden_path),
                    golden_case_count=1,
                )

            manifest = build_split_manifest(
                train_fingerprints=[legacy, full],
                val_fingerprints=[validation_legacy],
                golden_fingerprints=[golden],
                seed=42,
                val_ratio=0.1,
                golden_fixture_path=str(golden_path),
                golden_case_count=1,
                applied_fingerprint_mappings=[
                    {
                        "legacy_fingerprint": mapped_legacy,
                        "full_fingerprint": full,
                        "source_report_sha256": "a" * 64,
                    }
                ],
            )

            self.assertEqual(manifest["fingerprint_bases"], ["description", "legacy-partial"])


if __name__ == "__main__":
    unittest.main()
