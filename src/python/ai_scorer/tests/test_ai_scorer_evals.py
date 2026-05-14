from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.python.ai_scorer.evals.core import (
    EvalThresholds,
    FixtureValidationError,
    compare_candidate_vs_baseline,
    compute_metrics,
    load_and_validate_cases,
)
from src.python.ai_scorer.evals.extract_goldens import redact_text


class AiScorerEvalsCoreTests(unittest.TestCase):
    def test_compute_metrics_with_na_and_scores(self):
        rows = [
            {
                "expected_score_available": True,
                "expected_score": 5,
                "predicted_score_available": True,
                "predicted_score": 5,
            },
            {
                "expected_score_available": True,
                "expected_score": 3,
                "predicted_score_available": True,
                "predicted_score": 2,
            },
            {
                "expected_score_available": False,
                "expected_score": None,
                "predicted_score_available": False,
                "predicted_score": None,
            },
        ]

        metrics = compute_metrics(rows)

        self.assertAlmostEqual(metrics["exact_accuracy"], 2.0 / 3.0)
        self.assertGreater(metrics["na"]["f1"], 0.0)
        self.assertAlmostEqual(metrics["mean_abs_error"], 0.5)

    def test_compare_candidate_vs_baseline_thresholds(self):
        baseline = {
            "exact_accuracy": 0.80,
            "na": {"f1": 0.90},
            "mean_abs_error": 0.40,
        }
        candidate = {
            "exact_accuracy": 0.78,
            "na": {"f1": 0.86},
            "mean_abs_error": 0.55,
        }

        decision = compare_candidate_vs_baseline(
            baseline_metrics=baseline,
            candidate_metrics=candidate,
            thresholds=EvalThresholds(
                exact_accuracy_drop=0.03,
                na_f1_drop=0.05,
                mean_abs_error_increase=0.20,
            ),
        )

        self.assertTrue(decision["overall_passed"])
        self.assertTrue(decision["checks"]["exact_accuracy_drop"]["passed"])
        self.assertTrue(decision["checks"]["na_f1_drop"]["passed"])
        self.assertTrue(decision["checks"]["mean_abs_error_increase"]["passed"])

    def test_fixture_validation_rejects_invalid_expected(self):
        fixtures = [
            {
                "case_id": "bad",
                "job": {"title": "T", "description": "D", "location": "L"},
                "preference": {"key": "remote", "guidance": "Remote"},
                "expected": {"score_available": True, "score": 9},
            }
        ]

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "fixtures.json"
            path.write_text(json.dumps(fixtures), encoding="utf-8")
            with self.assertRaises(FixtureValidationError):
                load_and_validate_cases(path)

    def test_redact_text_masks_sensitive_patterns(self):
        raw = "Contact me at person@example.com or +39 333 123 4567. See https://example.com/job"
        redacted, stats = redact_text(raw)

        self.assertIn("[REDACTED_EMAIL]", redacted)
        self.assertIn("[REDACTED_PHONE]", redacted)
        self.assertIn("[REDACTED_URL]", redacted)
        self.assertEqual(stats["emails"], 1)
        self.assertEqual(stats["phones"], 1)
        self.assertEqual(stats["urls"], 1)


if __name__ == "__main__":
    unittest.main()
