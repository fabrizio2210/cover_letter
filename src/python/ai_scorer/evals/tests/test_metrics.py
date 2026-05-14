from __future__ import annotations

import unittest

from src.python.ai_scorer.evals.metrics import (
    EXACT_ACCURACY_DROP_THRESHOLD,
    MAE_INCREASE_THRESHOLD,
    NA_F1_DROP_THRESHOLD,
    CaseResult,
    EvalMetrics,
    check_regression,
    compute_metrics,
)


def _scored(case_id, expected, actual):
    """Helper: build a CaseResult for a score-available case."""
    return CaseResult(
        case_id=case_id,
        model="test-model",
        expected_score=expected,
        expected_score_available=True,
        actual_score=actual,
        actual_score_available=True,
    )


def _na_case(case_id, expected_na=True, actual_na=True):
    """Helper: build a CaseResult for an N/A case."""
    return CaseResult(
        case_id=case_id,
        model="test-model",
        expected_score=None,
        expected_score_available=False if expected_na else True,
        actual_score=None,
        actual_score_available=not actual_na,
    )


def _error_case(case_id):
    return CaseResult(
        case_id=case_id,
        model="test-model",
        expected_score=3,
        expected_score_available=True,
        actual_score=None,
        actual_score_available=None,
        error="connection refused",
    )


class TestComputeMetrics(unittest.TestCase):
    def test_empty_results(self):
        m = compute_metrics([])
        self.assertEqual(m.total, 0)
        self.assertEqual(m.exact_accuracy, 0.0)

    def test_perfect_accuracy_all_exact(self):
        results = [_scored("a", 3, 3), _scored("b", 5, 5), _scored("c", 1, 1)]
        m = compute_metrics(results)
        self.assertEqual(m.total, 3)
        self.assertEqual(m.exact_accuracy, 1.0)
        self.assertEqual(m.mean_abs_error, 0.0)

    def test_exact_accuracy_half_correct(self):
        results = [_scored("a", 3, 3), _scored("b", 5, 2)]
        m = compute_metrics(results)
        self.assertAlmostEqual(m.exact_accuracy, 0.5)

    def test_mae_is_average_absolute_error(self):
        results = [
            _scored("a", 1, 3),  # error = 2
            _scored("b", 5, 3),  # error = 2
            _scored("c", 3, 3),  # error = 0
        ]
        m = compute_metrics(results)
        self.assertAlmostEqual(m.mean_abs_error, 4 / 3)

    def test_na_precision_and_recall_perfect(self):
        results = [
            _na_case("a", expected_na=True, actual_na=True),
            _na_case("b", expected_na=True, actual_na=True),
        ]
        m = compute_metrics(results)
        self.assertAlmostEqual(m.na_precision, 1.0)
        self.assertAlmostEqual(m.na_recall, 1.0)
        self.assertAlmostEqual(m.na_f1, 1.0)

    def test_na_recall_zero_when_na_cases_missed(self):
        # Expected N/A but predicted scored
        result = _na_case("a", expected_na=True, actual_na=False)
        m = compute_metrics([result])
        self.assertAlmostEqual(m.na_recall, 0.0)
        self.assertAlmostEqual(m.na_f1, 0.0)

    def test_na_precision_zero_when_false_positives(self):
        # Expected scored but predicted N/A
        result = CaseResult(
            case_id="a",
            model="m",
            expected_score=3,
            expected_score_available=True,
            actual_score=None,
            actual_score_available=False,
        )
        m = compute_metrics([result])
        self.assertAlmostEqual(m.na_precision, 0.0)
        self.assertAlmostEqual(m.exact_accuracy, 0.0)

    def test_errors_are_counted_separately(self):
        results = [_scored("a", 3, 3), _error_case("b")]
        m = compute_metrics(results)
        self.assertEqual(m.errored, 1)
        self.assertEqual(m.total, 2)

    def test_score_distribution_counts_correctly(self):
        results = [
            _scored("a", 5, 5),
            _scored("b", 3, 3),
            _na_case("c"),
            _error_case("d"),
        ]
        m = compute_metrics(results)
        self.assertEqual(m.score_distribution["5"], 1)
        self.assertEqual(m.score_distribution["3"], 1)
        self.assertEqual(m.score_distribution["na"], 1)
        self.assertEqual(m.score_distribution["error"], 1)

    def test_mae_excludes_na_and_error_cases(self):
        results = [
            _scored("a", 4, 2),   # error = 2, included
            _na_case("b"),         # excluded from MAE
            _error_case("c"),      # excluded from MAE
        ]
        m = compute_metrics(results)
        self.assertAlmostEqual(m.mean_abs_error, 2.0)


class TestCheckRegression(unittest.TestCase):
    def _make_metrics(self, exact_accuracy, na_f1, mean_abs_error):
        return EvalMetrics(
            total=100,
            errored=0,
            exact_accuracy=exact_accuracy,
            na_precision=0.8,
            na_recall=0.8,
            na_f1=na_f1,
            mean_abs_error=mean_abs_error,
        )

    def test_passes_when_candidate_matches_baseline(self):
        b = self._make_metrics(0.80, 0.70, 0.50)
        c = self._make_metrics(0.80, 0.70, 0.50)
        result = check_regression(b, c)
        self.assertTrue(result.passed)
        self.assertEqual(result.reasons, [])

    def test_passes_when_candidate_improves(self):
        b = self._make_metrics(0.70, 0.60, 0.60)
        c = self._make_metrics(0.80, 0.75, 0.40)
        result = check_regression(b, c)
        self.assertTrue(result.passed)

    def test_fails_on_exact_accuracy_drop_above_threshold(self):
        b = self._make_metrics(0.80, 0.70, 0.50)
        c = self._make_metrics(0.76, 0.70, 0.50)  # drop = 0.04 > 0.03
        result = check_regression(b, c)
        self.assertFalse(result.passed)
        self.assertTrue(any("exact_accuracy" in r for r in result.reasons))

    def test_passes_on_exact_accuracy_drop_at_threshold(self):
        b = self._make_metrics(0.80, 0.70, 0.50)
        c = self._make_metrics(0.77, 0.70, 0.50)  # drop = 0.03 = threshold, not exceeding
        result = check_regression(b, c)
        self.assertTrue(result.passed)

    def test_fails_on_na_f1_drop_above_threshold(self):
        b = self._make_metrics(0.80, 0.70, 0.50)
        c = self._make_metrics(0.80, 0.64, 0.50)  # drop = 0.06 > 0.05
        result = check_regression(b, c)
        self.assertFalse(result.passed)
        self.assertTrue(any("na_f1" in r for r in result.reasons))

    def test_fails_on_mae_increase_above_threshold(self):
        b = self._make_metrics(0.80, 0.70, 0.50)
        c = self._make_metrics(0.80, 0.70, 0.71)  # increase = 0.21 > 0.20
        result = check_regression(b, c)
        self.assertFalse(result.passed)
        self.assertTrue(any("mean_abs_error" in r for r in result.reasons))

    def test_multiple_failures_reported(self):
        b = self._make_metrics(0.80, 0.70, 0.50)
        c = self._make_metrics(0.70, 0.60, 0.80)  # all three thresholds violated
        result = check_regression(b, c)
        self.assertFalse(result.passed)
        self.assertEqual(len(result.reasons), 3)

    def test_regression_deltas_are_stored(self):
        b = self._make_metrics(0.80, 0.70, 0.50)
        c = self._make_metrics(0.75, 0.68, 0.55)
        result = check_regression(b, c)
        self.assertAlmostEqual(result.exact_accuracy_drop, 0.05)
        self.assertAlmostEqual(result.na_f1_drop, 0.02)
        self.assertAlmostEqual(result.mean_abs_error_increase, 0.05)


if __name__ == "__main__":
    unittest.main()
