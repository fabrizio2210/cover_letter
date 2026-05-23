from __future__ import annotations

import json
import tempfile
import unittest

from src.python.ai_scorer.evals.schema import (
    EvalCase,
    dump_fixtures,
    load_fixtures,
    new_case_id,
    validate_case,
    validate_fixtures,
)


def _valid_scored_case(**overrides) -> EvalCase:
    defaults = dict(
        case_id=new_case_id(),
        title="Platform Engineer",
        description="Build backend systems for remote teams.",
        location="Remote",
        preference_key="coding",
        preference_guidance="It requires a lot of coding",
        expected_score=4,
        expected_score_available=True,
        rationale="Job description clearly mentions coding work.",
        tags=["remote_location", "medium_description"],
    )
    defaults.update(overrides)
    return EvalCase(**defaults)


def _valid_na_case(**overrides) -> EvalCase:
    defaults = dict(
        case_id=new_case_id(),
        title="",
        description="",
        location="",
        preference_key="remote_work",
        preference_guidance="Prefers fully remote",
        expected_score=None,
        expected_score_available=False,
        rationale="No location information available.",
        tags=["empty_description"],
    )
    defaults.update(overrides)
    return EvalCase(**defaults)


class TestEvalCaseValidation(unittest.TestCase):
    def test_valid_scored_case_has_no_errors(self):
        errors = validate_case(_valid_scored_case())
        self.assertEqual(errors, [])

    def test_valid_na_case_has_no_errors(self):
        errors = validate_case(_valid_na_case())
        self.assertEqual(errors, [])

    def test_missing_preference_key(self):
        case = _valid_scored_case(preference_key="")
        errors = validate_case(case)
        self.assertTrue(any("preference_key" in e for e in errors))

    def test_missing_preference_guidance(self):
        case = _valid_scored_case(preference_guidance="")
        errors = validate_case(case)
        self.assertTrue(any("preference_guidance" in e for e in errors))

    def test_score_out_of_range(self):
        case = _valid_scored_case(expected_score=6)
        errors = validate_case(case)
        self.assertTrue(any("0..5" in e for e in errors))

    def test_negative_score_is_invalid(self):
        case = _valid_scored_case(expected_score=-1)
        errors = validate_case(case)
        self.assertTrue(any("0..5" in e for e in errors))

    def test_unlabeled_case_fails_canonical_validation(self):
        case = _valid_scored_case(expected_score=None, expected_score_available=None)
        errors = validate_case(case)
        self.assertTrue(any("not yet labeled" in e for e in errors))

    def test_na_case_with_score_is_invalid(self):
        case = _valid_na_case(expected_score=3, expected_score_available=False)
        errors = validate_case(case)
        self.assertTrue(any("must be None" in e for e in errors))

    def test_scored_case_without_score_is_invalid(self):
        case = _valid_scored_case(expected_score=None, expected_score_available=True)
        errors = validate_case(case)
        self.assertTrue(any("expected_score must be set" in e for e in errors))


class TestValidateFixtures(unittest.TestCase):
    def test_empty_list_is_valid(self):
        self.assertEqual(validate_fixtures([]), [])

    def test_duplicate_case_id_is_invalid(self):
        shared_id = new_case_id()
        a = _valid_scored_case(case_id=shared_id)
        b = _valid_scored_case(case_id=shared_id)
        errors = validate_fixtures([a, b])
        self.assertTrue(any("duplicate" in e for e in errors))

    def test_mixed_valid_and_invalid(self):
        good = _valid_scored_case()
        bad = _valid_scored_case(preference_key="")
        errors = validate_fixtures([good, bad])
        self.assertEqual(len(errors), 1)


class TestFixtureSerialization(unittest.TestCase):
    def test_round_trip_preserves_scored_case(self):
        original = _valid_scored_case()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            path = f.name

        dump_fixtures([original], path)
        loaded = load_fixtures(path)

        self.assertEqual(len(loaded), 1)
        c = loaded[0]
        self.assertEqual(c.case_id, original.case_id)
        self.assertEqual(c.expected_score, original.expected_score)
        self.assertEqual(c.expected_score_available, original.expected_score_available)
        self.assertEqual(c.preference_key, original.preference_key)

    def test_round_trip_preserves_na_case(self):
        original = _valid_na_case()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            path = f.name

        dump_fixtures([original], path)
        loaded = load_fixtures(path)

        c = loaded[0]
        self.assertIsNone(c.expected_score)
        self.assertFalse(c.expected_score_available)

    def test_load_rejects_non_array_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"not": "an array"}, f)
            path = f.name

        with self.assertRaises(ValueError):
            load_fixtures(path)


if __name__ == "__main__":
    unittest.main()
