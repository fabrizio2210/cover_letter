from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.python.ai_scorer.job_fingerprint import (
    description_fingerprint,
    legacy_partial_fingerprint,
    stable_json_hash,
)
from src.python.ai_scorer.training.dataset_split import (
    build_split_manifest,
    load_split_manifest,
    write_split_manifest,
)
from src.python.ai_scorer.training.merge_labeled_expansion import merge_labeled_expansion
from src.python.ai_scorer.training.schema import TrainingCase, dump_cases, load_cases


def _case(
    case_id: str,
    fingerprint: str,
    basis: str,
    preference_key: str,
    guidance: str,
    *,
    score: int | None = None,
) -> TrainingCase:
    return TrainingCase(
        case_id=case_id,
        job_fingerprint=fingerprint,
        fingerprint_basis=basis,
        title=case_id,
        location="remote",
        preference_key=preference_key,
        preference_guidance=guidance,
        relevant_snippets=[f"Evidence for {case_id}"],
        system_prompt="Score only",
        user_prompt=f"Score {case_id}",
        label_score=score,
        label_available=True if score is not None else None,
    )


class LabeledExpansionMergeTests(unittest.TestCase):
    def test_apply_preserves_base_labels_and_declares_native_fingerprints(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            preference = {"key": "preference", "guidance": "Guidance"}
            preferences = [preference]
            preferences_path = root / "preferences.json"
            preferences_path.write_text(json.dumps(preferences), encoding="utf-8")

            golden_fingerprint, golden_basis = description_fingerprint("Golden description")
            golden_path = root / "golden.json"
            golden_path.write_text(
                json.dumps(
                    {
                        "meta": {"fixture_model": "test", "reference_metrics": {}, "format_version": "2"},
                        "cases": [
                            {
                                "case_id": "golden",
                                "job_fingerprint": golden_fingerprint,
                                "fingerprint_basis": golden_basis,
                                "title": "Golden",
                                "description": "Golden description",
                                "location": "remote",
                                "preference_key": "preference",
                                "preference_guidance": "Guidance",
                                "expected_score": 5,
                                "expected_score_available": True,
                                "rationale": "test",
                                "tags": [],
                                "schema_version": "2",
                                "provenance": None,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            train_legacy, train_basis, _ = legacy_partial_fingerprint(["Train evidence"])
            val_legacy, val_basis, _ = legacy_partial_fingerprint(["Val evidence"])
            new_one, new_basis = description_fingerprint("New full description one")
            new_two, _ = description_fingerprint("New full description two")
            base_candidates = [
                _case("base-train", train_legacy, train_basis, "preference", "Guidance"),
                _case("base-val", val_legacy, val_basis, "preference", "Guidance"),
            ]
            base_labeled = [
                _case("base-train", train_legacy, train_basis, "preference", "Guidance", score=1),
                _case("base-val", val_legacy, val_basis, "preference", "Guidance", score=2),
            ]
            expansion_candidates = [
                _case("new-one", new_one, new_basis, "preference", "Guidance"),
                _case("new-two", new_two, new_basis, "preference", "Guidance"),
            ]
            merged_labeled = base_labeled + [
                _case("new-one", new_one, new_basis, "preference", "Guidance", score=5),
                _case("new-two", new_two, new_basis, "preference", "Guidance", score=4),
            ]

            candidates_path = root / "candidates.json"
            labeled_path = root / "labeled.json"
            expansion_path = root / "expansion.json"
            merged_path = root / "merged.json"
            manifest_path = root / "split-manifest.json"
            report_path = root / "report.json"
            receipt_path = root / "receipt.json"
            dump_cases(base_candidates, str(candidates_path))
            dump_cases(base_labeled, str(labeled_path))
            dump_cases(expansion_candidates, str(expansion_path))
            dump_cases(merged_labeled, str(merged_path))
            manifest = build_split_manifest(
                train_fingerprints=[train_legacy],
                val_fingerprints=[val_legacy],
                golden_fingerprints=[golden_fingerprint],
                seed=42,
                val_ratio=0.5,
                golden_fixture_path=str(golden_path),
                golden_case_count=1,
                preference_set_hash=stable_json_hash(preferences),
            )
            write_split_manifest(manifest, str(manifest_path))

            receipt = merge_labeled_expansion(
                candidates_path=str(candidates_path),
                labeled_path=str(labeled_path),
                expansion_candidates_path=str(expansion_path),
                merged_labeled_path=str(merged_path),
                split_manifest_path=str(manifest_path),
                preferences_path=str(preferences_path),
                report_out=str(report_path),
                receipt_out=str(receipt_path),
                teacher_model="test-model",
                apply=True,
            )

            self.assertEqual(receipt["summary"]["preserved_base_label_count"], 2)
            self.assertEqual(receipt["summary"]["changed_base_label_count"], 0)
            self.assertEqual(len(load_cases(str(candidates_path))), 4)
            labels = load_cases(str(labeled_path))
            self.assertEqual([case.label_score for case in labels], [1, 2, 5, 4])
            updated = load_split_manifest(str(manifest_path))
            self.assertEqual(
                set(updated["native_description_fingerprints"]),
                {new_one, new_two},
            )
            self.assertEqual(len(updated["train_fingerprints"]), 2)
            self.assertEqual(len(updated["val_fingerprints"]), 2)
            self.assertTrue(report_path.is_file())
            self.assertTrue(receipt_path.is_file())


if __name__ == "__main__":
    unittest.main()
