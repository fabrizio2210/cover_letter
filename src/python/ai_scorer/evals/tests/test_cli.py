from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.python.ai_scorer.evals.cli import main
from src.python.ai_scorer.evals.metrics import CaseResult
from src.python.ai_scorer.evals.profile import PRODUCTION_REFERENCE_MODEL
from src.python.ai_scorer.evals import runner
from src.python.ai_scorer.job_fingerprint import description_fingerprint


def _write_fixture(path: Path) -> None:
    description = "Build backend systems for a distributed team."
    fingerprint, basis = description_fingerprint(description)
    path.write_text(
        json.dumps(
            {
                "meta": {
                    "fixture_model": "human-reviewed",
                    "reference_metrics": {},
                    "format_version": "2",
                },
                "cases": [
                    {
                        "case_id": "case-1",
                        "job_fingerprint": fingerprint,
                        "fingerprint_basis": basis,
                        "title": "Backend Engineer",
                        "description": description,
                        "location": "Remote",
                        "preference_key": "backend",
                        "preference_guidance": "Prefers backend work",
                        "expected_score": 4,
                        "expected_score_available": True,
                        "rationale": "Backend work is explicit.",
                        "tags": ["remote_location"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _candidate_results(*args, **kwargs):
    return [
        CaseResult(
            case_id="case-1",
            model=kwargs.get("model_name", "candidate"),
            expected_score=4,
            expected_score_available=True,
            actual_score=4,
            actual_score_available=True,
            latency_ms=10.0,
        )
    ]


def _errored_candidate_results(*args, **kwargs):
    result = _candidate_results(*args, **kwargs)[0]
    result.actual_score = None
    result.actual_score_available = None
    result.error = "Ollama unavailable"
    return [result]


class EvalCliProfileTests(unittest.TestCase):
    def test_missing_reference_configuration_fails_before_model_run(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Path(temporary_directory) / "fixture.json"
            output = Path(temporary_directory) / "output"
            _write_fixture(fixture)

            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(
                    runner,
                    "run_eval",
                    side_effect=AssertionError("model run should not start"),
                ),
            ):
                exit_code = main(
                    [
                        "eval",
                        "--candidate",
                        "candidate",
                        "--fixtures",
                        str(fixture),
                        "--output-dir",
                        str(output),
                    ]
                )

        self.assertEqual(exit_code, 2)

    def test_allowed_mismatch_writes_ungated_configuration(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Path(temporary_directory) / "fixture.json"
            output = Path(temporary_directory) / "output"
            _write_fixture(fixture)

            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(
                    runner,
                    "run_eval",
                    side_effect=_candidate_results,
                ),
            ):
                exit_code = main(
                    [
                        "eval",
                        "--candidate",
                        "candidate",
                        "--fixtures",
                        str(fixture),
                        "--output-dir",
                        str(output),
                        "--allow-profile-mismatch",
                    ]
                )

            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 2)
        self.assertEqual(summary["regression"]["status"], "skipped")
        self.assertIsNone(summary["regression"]["passed"])
        self.assertEqual(summary["run_configuration"]["profile"], "production")
        self.assertEqual(
            summary["run_configuration"]["pipeline"]["EVIDENCE_SCOPE_ROUTING"],
            "llm",
        )

    def test_reference_refresh_persists_matching_provenance(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Path(temporary_directory) / "fixture.json"
            output = Path(temporary_directory) / "output"
            comparison_output = Path(temporary_directory) / "comparison-output"
            _write_fixture(fixture)

            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(
                    runner,
                    "run_eval",
                    side_effect=_candidate_results,
                ),
            ):
                exit_code = main(
                    [
                        "eval",
                        "--candidate",
                        PRODUCTION_REFERENCE_MODEL,
                        "--fixtures",
                        str(fixture),
                        "--output-dir",
                        str(output),
                        "--refresh-reference",
                    ]
                )

            fixture_data = json.loads(fixture.read_text(encoding="utf-8"))
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))

            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(
                    runner,
                    "run_eval",
                    side_effect=_candidate_results,
                ),
            ):
                comparison_exit_code = main(
                    [
                        "eval",
                        "--candidate",
                        "new-candidate-model",
                        "--fixtures",
                        str(fixture),
                        "--output-dir",
                        str(comparison_output),
                    ]
                )
            comparison_summary = json.loads(
                (comparison_output / "summary.json").read_text(encoding="utf-8")
            )

        reference_run = fixture_data["meta"]["reference_run"]
        self.assertEqual(exit_code, 0)
        self.assertEqual(fixture_data["meta"]["format_version"], "4")
        self.assertEqual(reference_run["model"], PRODUCTION_REFERENCE_MODEL)
        self.assertEqual(
            reference_run["fixture_fingerprint"],
            summary["run_configuration"]["fixture_fingerprint"],
        )
        self.assertTrue(reference_run["metrics_fingerprint"])
        self.assertEqual(
            reference_run["pipeline_fingerprint"],
            summary["run_configuration"]["pipeline_fingerprint"],
        )
        self.assertTrue(summary["reference_refreshed"])
        self.assertEqual(summary["candidate_metrics"]["exact_accuracy"], 1.0)
        self.assertEqual(comparison_exit_code, 0)
        self.assertEqual(comparison_summary["regression"]["status"], "evaluated")
        self.assertTrue(comparison_summary["regression"]["passed"])

    def test_changed_golden_label_rejects_stale_reference(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Path(temporary_directory) / "fixture.json"
            output = Path(temporary_directory) / "output"
            _write_fixture(fixture)

            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(runner, "run_eval", side_effect=_candidate_results),
            ):
                refresh_exit_code = main(
                    [
                        "eval",
                        "--candidate",
                        PRODUCTION_REFERENCE_MODEL,
                        "--fixtures",
                        str(fixture),
                        "--output-dir",
                        str(output),
                        "--refresh-reference",
                    ]
                )

            fixture_data = json.loads(fixture.read_text(encoding="utf-8"))
            fixture_data["cases"][0]["expected_score"] = 3
            fixture.write_text(json.dumps(fixture_data), encoding="utf-8")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(
                    runner,
                    "run_eval",
                    side_effect=AssertionError("model run should not start"),
                ),
            ):
                comparison_exit_code = main(
                    [
                        "eval",
                        "--candidate",
                        "candidate",
                        "--fixtures",
                        str(fixture),
                        "--output-dir",
                        str(output),
                    ]
                )

        self.assertEqual(refresh_exit_code, 0)
        self.assertEqual(comparison_exit_code, 2)

    def test_changed_reference_metrics_rejects_stale_reference(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Path(temporary_directory) / "fixture.json"
            output = Path(temporary_directory) / "output"
            _write_fixture(fixture)

            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(runner, "run_eval", side_effect=_candidate_results),
            ):
                refresh_exit_code = main(
                    [
                        "eval",
                        "--candidate",
                        PRODUCTION_REFERENCE_MODEL,
                        "--fixtures",
                        str(fixture),
                        "--output-dir",
                        str(output),
                        "--refresh-reference",
                    ]
                )

            fixture_data = json.loads(fixture.read_text(encoding="utf-8"))
            fixture_data["meta"]["reference_metrics"]["exact_accuracy"] = 0.0
            fixture.write_text(json.dumps(fixture_data), encoding="utf-8")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(
                    runner,
                    "run_eval",
                    side_effect=AssertionError("model run should not start"),
                ),
            ):
                comparison_exit_code = main(
                    [
                        "eval",
                        "--candidate",
                        "candidate",
                        "--fixtures",
                        str(fixture),
                        "--output-dir",
                        str(output),
                    ]
                )

        self.assertEqual(refresh_exit_code, 0)
        self.assertEqual(comparison_exit_code, 2)

    def test_malformed_reference_metadata_is_rejected_before_model_run(self):
        for field in ("reference_run", "reference_metrics"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary_directory:
                fixture = Path(temporary_directory) / "fixture.json"
                _write_fixture(fixture)
                fixture_data = json.loads(fixture.read_text(encoding="utf-8"))
                fixture_data["meta"][field] = "malformed"
                fixture.write_text(json.dumps(fixture_data), encoding="utf-8")

                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch.object(
                        runner,
                        "run_eval",
                        side_effect=AssertionError("model run should not start"),
                    ),
                ):
                    exit_code = main(
                        [
                            "eval",
                            "--candidate",
                            "candidate",
                            "--fixtures",
                            str(fixture),
                        ]
                    )

                self.assertEqual(exit_code, 2)

    def test_reference_refresh_rejects_pipeline_override(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Path(temporary_directory) / "fixture.json"
            _write_fixture(fixture)

            with (
                patch.dict(
                    os.environ,
                    {"NORMALIZE_JOB_LOCATION": "false"},
                    clear=True,
                ),
                patch.object(
                    runner,
                    "run_eval",
                    side_effect=AssertionError("model run should not start"),
                ),
            ):
                exit_code = main(
                    [
                        "eval",
                        "--candidate",
                        PRODUCTION_REFERENCE_MODEL,
                        "--fixtures",
                        str(fixture),
                        "--refresh-reference",
                    ]
                )

        self.assertEqual(exit_code, 2)

    def test_reference_refresh_rejects_errored_run(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = Path(temporary_directory) / "fixture.json"
            _write_fixture(fixture)
            before = fixture.read_text(encoding="utf-8")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(
                    runner,
                    "run_eval",
                    side_effect=_errored_candidate_results,
                ),
            ):
                exit_code = main(
                    [
                        "eval",
                        "--candidate",
                        PRODUCTION_REFERENCE_MODEL,
                        "--fixtures",
                        str(fixture),
                        "--refresh-reference",
                    ]
                )

            after = fixture.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 2)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
