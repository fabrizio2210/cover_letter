from __future__ import annotations

import re
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from src.python.ai_scorer.evals.profile import (
    PRODUCTION_PROFILE_DEFAULTS,
    PRODUCTION_PROFILE_NAME,
    PRODUCTION_REFERENCE_MODEL,
    apply_profile,
    build_run_configuration,
    fixture_fingerprint,
    metrics_fingerprint,
    reference_configuration_mismatches,
    reference_matches_configuration,
)
from src.python.ai_scorer.scoring_config import (
    PRODUCTION_EVAL_MODEL_PINS,
    PRODUCTION_PIPELINE_ENVIRONMENT,
)


REPO_ROOT = Path(__file__).resolve().parents[5]
PRODUCTION_STACK = (REPO_ROOT / "docker/prod/stack.yml").read_text(encoding="utf-8")
EVAL_SCRIPT = (REPO_ROOT / "scripts/eval-scorer.sh").read_text(encoding="utf-8")


class EvalProfileTests(unittest.TestCase):
    def test_production_profile_defaults_match_deployed_auxiliaries(self):
        scorer_service = re.search(
            r"^  ai-scorer:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n)",
            PRODUCTION_STACK,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(scorer_service)
        environment_block = re.search(
            r"^    environment:\n(?P<body>.*?)(?=^    [a-zA-Z0-9_-]+:)",
            scorer_service.group("body"),
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(environment_block)
        deployed_environment = {
            match.group("key"): match.group("value").strip('"\'')
            for match in re.finditer(
                r"^      (?P<key>[A-Z0-9_]+):\s+(?P<value>\S+)\s*$",
                environment_block.group("body"),
                re.MULTILINE,
            )
        }
        runtime_keys = {
            "REDIS_HOST",
            "REDIS_PORT",
            "JOB_SCORING_QUEUE_NAME",
            "OLLAMA_HOST",
            "OLLAMA_MODEL",
            "AI_SCORER_TEST_MODE",
            "AI_SCORER_OLLAMA_PARALLELISM",
        }
        deployed_pipeline = {
            key: value
            for key, value in deployed_environment.items()
            if key not in runtime_keys
        }
        self.assertEqual(
            deployed_pipeline,
            PRODUCTION_PIPELINE_ENVIRONMENT,
        )
        self.assertEqual(
            PRODUCTION_PROFILE_DEFAULTS,
            {
                "EVAL_WITH_SYSTEM_PROMPT": "true",
                **PRODUCTION_PIPELINE_ENVIRONMENT,
                **PRODUCTION_EVAL_MODEL_PINS,
            },
        )
        self.assertIn(
            "from src.python.ai_scorer.scoring_config import STORED_EVAL_REFERENCE_MODEL",
            EVAL_SCRIPT,
        )
        self.assertNotIn(PRODUCTION_REFERENCE_MODEL, EVAL_SCRIPT)

    def test_profile_preserves_explicit_overrides(self):
        environment = {
            "NORMALIZE_JOB_LOCATION": "false",
            "QUERY_EXPANSION_MODEL": "experiment-model",
        }

        apply_profile(PRODUCTION_PROFILE_NAME, environment)

        self.assertEqual(environment["NORMALIZE_JOB_LOCATION"], "false")
        self.assertEqual(environment["QUERY_EXPANSION_MODEL"], "experiment-model")
        self.assertEqual(environment["EVIDENCE_SCOPE_ROUTING"], "llm")

    def test_configuration_records_resolved_defaults_and_implementation(self):
        environment: dict[str, str] = {}
        apply_profile(PRODUCTION_PROFILE_NAME, environment)

        configuration = build_run_configuration(
            PRODUCTION_PROFILE_NAME,
            "candidate",
            environment,
        )

        self.assertEqual(
            configuration["pipeline"]["EMBEDDING_MODEL"],
            "BAAI/bge-small-en-v1.5",
        )
        self.assertEqual(
            configuration["pipeline"]["RERANKING_MODEL"],
            "jinaai/jina-reranker-v1-tiny-en",
        )
        self.assertFalse(configuration["pipeline"]["SCORER_POINTWISE_RERANK"])
        self.assertEqual(
            configuration["pipeline"]["POINTWISE_RERANKER_MODEL"],
            PRODUCTION_REFERENCE_MODEL,
        )
        self.assertIsNone(configuration["pipeline"]["SCORING_THINK"])
        self.assertEqual(configuration["implementation"]["retrieval"]["candidate_k"], 10)
        self.assertIn("ai_scorer.py", configuration["implementation"]["source_sha256"])

    def test_fingerprint_excludes_candidate_but_tracks_pipeline_overrides(self):
        baseline_environment: dict[str, str] = {}
        apply_profile(PRODUCTION_PROFILE_NAME, baseline_environment)
        first = build_run_configuration(
            PRODUCTION_PROFILE_NAME,
            "candidate-a",
            baseline_environment,
        )
        second = build_run_configuration(
            PRODUCTION_PROFILE_NAME,
            "candidate-b",
            baseline_environment,
        )
        changed_environment = dict(baseline_environment)
        changed_environment["EVIDENCE_SCOPE_ROUTING"] = ""
        changed = build_run_configuration(
            PRODUCTION_PROFILE_NAME,
            "candidate-a",
            changed_environment,
        )

        self.assertEqual(
            first["pipeline_fingerprint"],
            second["pipeline_fingerprint"],
        )
        self.assertNotEqual(
            first["pipeline_fingerprint"],
            changed["pipeline_fingerprint"],
        )
        reference_metrics = {"total": 1}
        reference = {
            **first,
            "model": PRODUCTION_REFERENCE_MODEL,
            "fixture_fingerprint": "fixture",
            "metrics_fingerprint": metrics_fingerprint(reference_metrics),
        }
        self.assertTrue(
            reference_matches_configuration(
                reference,
                second,
                "fixture",
                reference_metrics,
            )
        )
        self.assertFalse(
            reference_matches_configuration(
                reference,
                changed,
                "fixture",
                reference_metrics,
            )
        )

    def test_reference_validation_recomputes_provenance_and_checks_model(self):
        environment: dict[str, str] = {}
        apply_profile(PRODUCTION_PROFILE_NAME, environment)
        configuration = build_run_configuration(
            PRODUCTION_PROFILE_NAME,
            "candidate",
            environment,
        )
        reference_metrics = {"total": 1}
        reference = {
            **configuration,
            "model": PRODUCTION_REFERENCE_MODEL,
            "fixture_fingerprint": "fixture",
            "metrics_fingerprint": metrics_fingerprint(reference_metrics),
        }
        tampered = deepcopy(reference)
        tampered["pipeline"]["NORMALIZE_JOB_LOCATION"] = False

        self.assertIn(
            "stored reference pipeline provenance was modified",
            reference_configuration_mismatches(
                tampered,
                configuration,
                "fixture",
                reference_metrics,
            ),
        )
        wrong_model = {**reference, "model": "another-model"}
        self.assertIn(
            "stored reference model is not the configured reference model",
            reference_configuration_mismatches(
                wrong_model,
                configuration,
                "fixture",
                reference_metrics,
            ),
        )

    def test_reference_validation_binds_regression_metrics(self):
        environment: dict[str, str] = {}
        apply_profile(PRODUCTION_PROFILE_NAME, environment)
        configuration = build_run_configuration(
            PRODUCTION_PROFILE_NAME,
            "candidate",
            environment,
        )
        stored_metrics = {"exact_accuracy": 0.5}
        reference = {
            **configuration,
            "model": PRODUCTION_REFERENCE_MODEL,
            "fixture_fingerprint": "fixture",
            "metrics_fingerprint": metrics_fingerprint(stored_metrics),
        }

        self.assertIn(
            "stored reference metrics were modified",
            reference_configuration_mismatches(
                reference,
                configuration,
                "fixture",
                {"exact_accuracy": 1.0},
            ),
        )

    def test_fixture_fingerprint_tracks_inputs_labels_and_order(self):
        first = SimpleNamespace(
            case_id="one",
            title="Engineer",
            description="Build systems",
            location="Remote",
            preference_key="backend",
            preference_guidance="Prefers backend work",
            expected_score=4,
            expected_score_available=True,
        )
        second = SimpleNamespace(**{**vars(first), "case_id": "two"})
        changed_label = SimpleNamespace(**{**vars(first), "expected_score": 3})

        self.assertNotEqual(
            fixture_fingerprint([first]),
            fixture_fingerprint([changed_label]),
        )
        self.assertNotEqual(
            fixture_fingerprint([first, second]),
            fixture_fingerprint([second, first]),
        )


if __name__ == "__main__":
    unittest.main()
