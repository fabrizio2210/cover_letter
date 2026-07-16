from __future__ import annotations

import unittest
from pathlib import Path

from src.python.ai_scorer.evals.schema import load_fixtures, validate_fixtures
from src.python.ai_scorer.job_fingerprint import (
    DESCRIPTION_BASIS,
    LEGACY_PARTIAL_BASIS,
    TITLE_LOCATION_BASIS,
    description_fingerprint,
    legacy_partial_fingerprint,
    validate_fingerprint,
)
from src.python.ai_scorer.training.extractor import prepare_job_pool, prepare_training_jobs
from src.python.ai_scorer.training.labeler import label_cases
from src.python.ai_scorer.training.migrate_job_fingerprints import (
    build_legacy_groups,
    migrate_training_raw,
    reconcile_legacy_groups,
)
from src.python.ai_scorer.training.schema import TrainingCase, load_cases, validate_cases


_AI_SCORER_ROOT = Path(__file__).resolve().parents[1]


class JobFingerprintTests(unittest.TestCase):
    def test_canonical_53_cases_use_25_full_description_fingerprints(self):
        fixture_path = (
            _AI_SCORER_ROOT
            / "evals"
            / "data"
            / "canonical"
            / "v1.json"
        )
        cases = load_fixtures(str(fixture_path))

        self.assertEqual(validate_fixtures(cases), [])
        self.assertEqual(len(cases), 53)
        self.assertEqual(len({case.job_fingerprint for case in cases}), 25)
        self.assertNotIn("source_job_id", fixture_path.read_text(encoding="utf-8"))
        for case in cases:
            expected, basis = description_fingerprint(
                case.description,
                title=case.title,
                location=case.location,
            )
            self.assertEqual(case.job_fingerprint, expected)
            self.assertEqual(case.fingerprint_basis, basis)

    def test_migrated_paid_inventory_keeps_all_500_labels(self):
        labeled_path = (
            _AI_SCORER_ROOT
            / "training"
            / "data"
            / "proposed"
            / "labeled.json"
        )
        cases = load_cases(str(labeled_path))

        self.assertEqual(validate_cases(cases), [])
        self.assertEqual(len(cases), 500)
        self.assertEqual(len({case.job_fingerprint for case in cases}), 30)
        self.assertTrue(all(case.label_available is not None for case in cases))
        self.assertNotIn("source_job_id", labeled_path.read_text(encoding="utf-8"))

    def test_description_normalization_is_stable(self):
        first, first_basis = description_fingerprint("<p>Hello&nbsp;world</p>")
        second, second_basis = description_fingerprint("Hello world\r\n")

        self.assertEqual(first, second)
        self.assertEqual(first_basis, DESCRIPTION_BASIS)
        self.assertEqual(second_basis, DESCRIPTION_BASIS)
        self.assertIsNone(validate_fingerprint(first))

    def test_empty_description_uses_title_location_fallback(self):
        first, basis = description_fingerprint("", title=" Engineer ", location="REMOTE")
        second, _ = description_fingerprint(None, title="engineer", location="remote")

        self.assertEqual(first, second)
        self.assertEqual(basis, TITLE_LOCATION_BASIS)

    def test_legacy_partial_is_order_independent(self):
        first, basis, snippets = legacy_partial_fingerprint(["Second", "First", "Second"])
        second, _, _ = legacy_partial_fingerprint(["First", "Second"])

        self.assertEqual(first, second)
        self.assertEqual(basis, LEGACY_PARTIAL_BASIS)
        self.assertEqual(snippets, ["First", "Second"])

    def test_migration_preserves_paid_case_content(self):
        raw = [
            {
                "case_id": "case-1",
                "source_job_id": "legacy-only",
                "title": "Engineer",
                "location": "Remote",
                "preference_key": "coding",
                "preference_guidance": "Coding",
                "relevant_snippets": ["Build reliable services."],
                "system_prompt": "System",
                "user_prompt": "User",
                "label_score": 4,
                "label_available": True,
                "schema_version": "1",
            }
        ]
        groups, mapping = build_legacy_groups(raw)

        migrated = migrate_training_raw(raw, mapping, groups)

        self.assertEqual(len(migrated), 1)
        self.assertNotIn("source_job_id", migrated[0])
        for field in ("case_id", "system_prompt", "user_prompt", "label_score", "label_available"):
            self.assertEqual(migrated[0][field], raw[0][field])

    def test_reconciliation_confirms_unique_full_containment(self):
        fingerprint, basis, snippets = legacy_partial_fingerprint(["Unique relevant evidence."])
        groups = {
            fingerprint: {
                "job_fingerprint": fingerprint,
                "fingerprint_basis": basis,
                "titles": ["Engineer"],
                "locations": ["Remote"],
                "canonical_titles": ["engineer"],
                "canonical_locations": ["remote"],
                "canonical_snippets": snippets,
                "case_count": 10,
                "legacy_group_count": 1,
            }
        }
        golden_fingerprint, golden_basis = description_fingerprint("Unique relevant evidence. More details.")
        golden = [
            {
                "job_fingerprint": golden_fingerprint,
                "fingerprint_basis": golden_basis,
                "canonical_title": "engineer",
                "canonical_location": "remote",
                "canonical_description": "Unique relevant evidence. More details.",
            }
        ]

        eligible, confirmed, quarantined, _ = reconcile_legacy_groups(groups, golden)

        self.assertEqual(eligible, [])
        self.assertEqual(confirmed, [fingerprint])
        self.assertEqual(quarantined, [])

    def test_reconciliation_quarantines_partial_substantive_overlap(self):
        shared = "Substantive shared evidence " + ("x" * 100)
        fingerprint, basis, snippets = legacy_partial_fingerprint(
            [shared, "Evidence absent from the golden description."]
        )
        groups = {
            fingerprint: {
                "job_fingerprint": fingerprint,
                "fingerprint_basis": basis,
                "titles": ["Legacy title"],
                "locations": ["Remote"],
                "canonical_titles": ["legacy title"],
                "canonical_locations": ["remote"],
                "canonical_snippets": snippets,
                "case_count": 10,
                "legacy_group_count": 1,
            }
        }
        golden_fingerprint, golden_basis = description_fingerprint(shared)
        golden = [
            {
                "job_fingerprint": golden_fingerprint,
                "fingerprint_basis": golden_basis,
                "canonical_title": "different title",
                "canonical_location": "different location",
                "canonical_description": shared,
            }
        ]

        eligible, confirmed, quarantined, _ = reconcile_legacy_groups(groups, golden)

        self.assertEqual(eligible, [])
        self.assertEqual(confirmed, [])
        self.assertEqual(quarantined, [fingerprint])

    def test_exact_label_reuse_needs_no_paid_call(self):
        fingerprint, basis = description_fingerprint("Job description")
        reusable = TrainingCase(
            case_id="old",
            job_fingerprint=fingerprint,
            fingerprint_basis=basis,
            title="Engineer",
            location="Remote",
            preference_key="coding",
            preference_guidance="Coding",
            relevant_snippets=["Code"],
            system_prompt="System",
            user_prompt="User",
            label_score=5,
            label_available=True,
        )
        candidate = TrainingCase(**{**reusable.__dict__, "case_id": "new", "label_score": None, "label_available": None})

        result = label_cases([candidate], "unused", reusable_cases=[reusable], allow_paid_calls=False)

        self.assertEqual(result[0].label_score, 5)
        self.assertTrue(result[0].label_available)

    def test_future_extraction_excludes_and_splits_before_expansion(self):
        golden, _ = description_fingerprint("Golden description")
        docs = [
            {"title": "Golden", "description": "Golden description", "location": "Remote"},
            {"title": "One", "description": "Clean description one", "location": "Remote"},
            {"title": "Two", "description": "Clean description two", "location": "Remote"},
            {"title": "Duplicate", "description": "Clean description two", "location": "Elsewhere"},
        ]

        sampled, train, val = prepare_training_jobs(
            docs,
            limit=2,
            promotion_fingerprints={golden},
            split_seed=42,
            val_ratio=0.5,
        )

        self.assertEqual(len(sampled), 2)
        self.assertNotIn(golden, {item["job_fingerprint"] for item in sampled})
        self.assertEqual(len(train), 1)
        self.assertEqual(len(val), 1)
        self.assertTrue(set(train).isdisjoint(val))

    def test_job_pool_is_reproducible_deduplicated_and_keeps_full_descriptions(self):
        golden, _ = description_fingerprint("Golden description")
        docs = [
            {"title": "Two", "description": "Second full description", "location": "Remote"},
            {"title": "Golden", "description": "Golden description", "location": "Remote"},
            {"title": "Duplicate B", "description": "First full description", "location": "B"},
            {"title": "Duplicate A", "description": "First full description", "location": "A"},
        ]

        first, stats = prepare_job_pool(
            docs,
            limit=500,
            promotion_fingerprints={golden},
        )
        second, _ = prepare_job_pool(
            list(reversed(docs)),
            limit=500,
            promotion_fingerprints={golden},
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)
        self.assertEqual({job["description"] for job in first}, {"First full description", "Second full description"})
        self.assertEqual(stats["fetched_document_count"], 4)
        self.assertEqual(stats["promotion_excluded_document_count"], 1)
        self.assertEqual(stats["duplicate_eligible_document_count"], 1)
        self.assertEqual(stats["sampled_job_count"], 2)

    def test_changed_prompt_is_not_reused_without_paid_call_approval(self):
        fingerprint, basis = description_fingerprint("Job description")
        reusable = TrainingCase(
            case_id="old",
            job_fingerprint=fingerprint,
            fingerprint_basis=basis,
            title="Engineer",
            location="Remote",
            preference_key="coding",
            preference_guidance="Coding",
            relevant_snippets=["Code"],
            system_prompt="System",
            user_prompt="Original prompt",
            label_score=5,
            label_available=True,
        )
        changed = TrainingCase(
            **{
                **reusable.__dict__,
                "case_id": "new",
                "user_prompt": "Changed prompt",
                "label_score": None,
                "label_available": None,
            }
        )

        with self.assertRaisesRegex(RuntimeError, "require Gemini"):
            label_cases([changed], "unused", reusable_cases=[reusable], allow_paid_calls=False)


if __name__ == "__main__":
    unittest.main()
