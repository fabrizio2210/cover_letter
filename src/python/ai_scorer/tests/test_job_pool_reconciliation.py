from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from src.python.ai_scorer.job_fingerprint import description_fingerprint, legacy_partial_fingerprint
from src.python.ai_scorer.training.reconcile_job_pool import (
    apply_case_mappings,
    build_reconciliation_report,
    build_applied_manifest,
    validate_job_pool,
)
from src.python.ai_scorer.training.dataset_split import build_split_manifest
from src.python.ai_scorer.training.schema import TrainingCase


def _pool_job(title: str, location: str, description: str) -> dict:
    fingerprint, basis = description_fingerprint(description, title=title, location=location)
    return {
        "job_fingerprint": fingerprint,
        "fingerprint_basis": basis,
        "title": title,
        "location": location,
        "description": description,
    }


def _labeled_case(
    case_id: str,
    title: str,
    location: str,
    snippets: list[str],
    *,
    preference_key: str = "remote",
) -> TrainingCase:
    fingerprint, basis, _ = legacy_partial_fingerprint(snippets, title=title, location=location)
    return TrainingCase(
        case_id=case_id,
        job_fingerprint=fingerprint,
        fingerprint_basis=basis,
        title=title,
        location=location,
        preference_key=preference_key,
        preference_guidance="Prefer remote work",
        relevant_snippets=snippets,
        system_prompt="System",
        user_prompt="User",
        label_score=4,
        label_available=True,
    )


class JobPoolReconciliationTests(unittest.TestCase):
    def test_conservative_unique_ambiguous_and_no_match_classification(self):
        unique_snippet = "Unique production infrastructure evidence for the backend role."
        shared_snippet = "Shared platform evidence present in more than one posting."
        unique_job = _pool_job("Backend Engineer", "Remote", unique_snippet + " More details.")
        shared_one = _pool_job("Platform Engineer", "Remote", shared_snippet + " First version.")
        shared_two = _pool_job("Platform Engineer", "Remote", shared_snippet + " Second version.")
        cases = [
            _labeled_case("unique", "Backend Engineer", "Remote", [unique_snippet]),
            _labeled_case("ambiguous", "Platform Engineer", "Remote", [shared_snippet]),
            _labeled_case("missing", "Security Engineer", "Hybrid", ["Evidence absent from the pool."]),
        ]
        statuses = {
            case.job_fingerprint: "eligible"
            for case in cases
        }
        manifest = {
            "golden_fingerprints": [],
            "reconciliation": [
                {"job_fingerprint": fingerprint, "status": status}
                for fingerprint, status in statuses.items()
            ],
        }
        root = {
            "stats": {"sampled_job_count": 3},
            "jobs": [unique_job, shared_one, shared_two],
        }

        report = build_reconciliation_report(root, cases, manifest)

        self.assertEqual(report["summary"]["mapping_proposal_count"], 1)
        self.assertEqual(report["summary"]["mapping_proposal_paid_case_count"], 1)
        self.assertEqual(report["mapping_proposals"][0]["full_fingerprint"], unique_job["job_fingerprint"])
        classifications = {item["titles"][0]: item["classification"] for item in report["groups"]}
        self.assertEqual(classifications["Backend Engineer"], "proposed_unique_match")
        self.assertEqual(classifications["Platform Engineer"], "ambiguous_exact_match")
        self.assertEqual(classifications["Security Engineer"], "no_exact_match")

    def test_previous_promotion_overlap_is_never_proposed(self):
        snippet = "Exact evidence that also belongs to a protected promotion case."
        job = _pool_job("Backend Engineer", "Remote", snippet)
        case = _labeled_case("protected", "Backend Engineer", "Remote", [snippet])
        manifest = {
            "golden_fingerprints": [],
            "reconciliation": [
                {
                    "job_fingerprint": case.job_fingerprint,
                    "status": "confirmed_promotion_overlap",
                }
            ],
        }

        report = build_reconciliation_report(
            {"stats": {"sampled_job_count": 1}, "jobs": [job]},
            [case],
            manifest,
        )

        self.assertEqual(report["mapping_proposals"], [])
        self.assertEqual(report["groups"][0]["classification"], "confirmed_promotion_overlap")

    def test_pool_validation_recomputes_fingerprints_and_rejects_golden_overlap(self):
        job = _pool_job("Engineer", "Remote", "Full job description")
        root = {"stats": {"sampled_job_count": 1}, "jobs": [job]}

        self.assertEqual(validate_job_pool(root, set()), [])
        errors = validate_job_pool(root, {job["job_fingerprint"]})
        self.assertIn("job pool overlaps 1 golden fingerprints", errors)

        broken = {"stats": {"sampled_job_count": 1}, "jobs": [{**job, "description": "Changed"}]}
        errors = validate_job_pool(broken, set())
        self.assertIn("jobs[0].job_fingerprint does not match its description", errors)

    def test_apply_preserves_splits_and_promotes_recovered_quarantine_without_reshuffle(self):
        train_case = _labeled_case("train", "Train", "Remote", ["Train snippet"])
        retained_case = _labeled_case("retained", "Retained", "Remote", ["Retained snippet"])
        val_case = _labeled_case("val", "Val", "Remote", ["Validation snippet"])
        recovered_case = _labeled_case("recovered", "Recovered", "Remote", ["Recovered snippet"])
        train_full = _pool_job("Train", "Remote", "Train snippet and full description")
        val_full = _pool_job("Val", "Remote", "Validation snippet and full description")
        recovered_full = _pool_job("Recovered", "Remote", "Recovered snippet and full description")
        golden, _ = description_fingerprint("Golden description")

        with tempfile.TemporaryDirectory() as temp_dir:
            golden_path = Path(temp_dir) / "golden.json"
            golden_path.write_text("{}", encoding="utf-8")
            manifest = build_split_manifest(
                train_fingerprints=[train_case.job_fingerprint, retained_case.job_fingerprint],
                val_fingerprints=[val_case.job_fingerprint],
                golden_fingerprints=[golden],
                quarantined_fingerprints=[recovered_case.job_fingerprint],
                seed=42,
                val_ratio=0.25,
                golden_fixture_path=str(golden_path),
                golden_case_count=1,
            )

        proposals = [
            {
                "legacy_fingerprint": train_case.job_fingerprint,
                "full_fingerprint": train_full["job_fingerprint"],
                "paid_case_count": 1,
                "review_flags": [],
            },
            {
                "legacy_fingerprint": val_case.job_fingerprint,
                "full_fingerprint": val_full["job_fingerprint"],
                "paid_case_count": 1,
                "review_flags": [],
            },
            {
                "legacy_fingerprint": recovered_case.job_fingerprint,
                "full_fingerprint": recovered_full["job_fingerprint"],
                "paid_case_count": 1,
                "review_flags": ["previously_quarantined"],
            },
        ]

        updated, applied = build_applied_manifest(
            manifest,
            proposals,
            source_report_sha256="a" * 64,
        )

        self.assertIn(retained_case.job_fingerprint, updated["train_fingerprints"])
        self.assertIn(train_full["job_fingerprint"], updated["train_fingerprints"])
        self.assertIn(val_full["job_fingerprint"], updated["val_fingerprints"])
        self.assertIn(recovered_full["job_fingerprint"], updated["train_fingerprints"])
        self.assertNotIn(recovered_case.job_fingerprint, updated["quarantined_fingerprints"])
        self.assertEqual(len(applied), 3)

        raw = [train_case.__dict__, val_case.__dict__, recovered_case.__dict__]
        mapped, changed = apply_case_mappings(
            raw,
            {item["legacy_fingerprint"]: item["full_fingerprint"] for item in proposals},
        )
        self.assertEqual(len(changed), 3)
        self.assertEqual({item["fingerprint_basis"] for item in mapped}, {"description"})


if __name__ == "__main__":
    unittest.main()
