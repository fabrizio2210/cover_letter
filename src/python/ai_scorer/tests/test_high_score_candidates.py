from __future__ import annotations

import unittest

from src.python.ai_scorer.job_fingerprint import description_fingerprint
from src.python.ai_scorer.training.cli import build_parser
from src.python.ai_scorer.training.high_score_candidates import (
    DEFAULT_PREFERENCES,
    SUPPORTED_PREFERENCES,
    _score_job,
    mine_high_score_cases,
)
from src.python.ai_scorer.training.preferences import load_preferences


def _job(title: str, location: str, description: str) -> dict:
    fingerprint, basis = description_fingerprint(
        description,
        title=title,
        location=location,
    )
    return {
        "job_fingerprint": fingerprint,
        "fingerprint_basis": basis,
        "title": title,
        "location": location,
        "description": description,
    }


EXAMPLES = {
    "pref_01_product_delivery": _job(
        "Senior Software Engineer, Product Platform",
        "hybrid",
        "Own product delivery and deliver products that create customer value. "
        "Ship new features across the full product lifecycle while reducing technical debt.",
    ),
    "pref_02_incident_response": _job(
        "Senior Site Reliability Engineer",
        "remote",
        "Lead incident response and respond to production incidents and outages. "
        "Join the on-call rotation, run postmortems, and perform root cause analysis.",
    ),
    "pref_03_architecture_ownership": _job(
        "Principal Software Engineer",
        "hybrid",
        "Own system architecture and set the technical direction for the platform. "
        "Lead system design, make architectural decisions, and evaluate trade-offs.",
    ),
    "pref_04_platform_reliability": _job(
        "Senior Platform Engineer",
        "remote",
        "Define SLOs and improve platform reliability and availability. "
        "Use observability metrics, monitoring, and alerting to produce measurable outcomes.",
    ),
    "pref_05_cross-team_communication": _job(
        "Staff Software Engineer",
        "hybrid",
        "Work cross-functionally across multiple teams and align stakeholders. "
        "Communicate with partners to raise engineering standards and the quality bar.",
    ),
    "pref_06_mentorship": _job(
        "Engineering Manager",
        "remote",
        "Mentor and coach engineers to support their career growth. "
        "Provide feedback while building clear ownership and accountability.",
    ),
    "pref_07_api_design": _job(
        "Senior Backend Engineer",
        "remote",
        "Lead API design and design APIs for core services. "
        "Define API contracts, REST interfaces, and versioned APIs for long-term maintainability.",
    ),
    "pref_08_performance_tuning": _job(
        "Performance Engineer, Backend",
        "hybrid",
        "Own performance tuning and latency optimization for backend services. "
        "Use profiling and benchmarks to remove bottlenecks and improve throughput.",
    ),
    "pref_09_data_pipelines": _job(
        "Senior Data Engineer",
        "remote",
        "Build maintainable data pipelines and ETL workflows for data ingestion. "
        "Operate Airflow and dbt orchestration with Spark streaming workloads.",
    ),
    "pref_10_developer_tooling": _job(
        "Staff Software Engineer - Developer Experience",
        "hybrid",
        "Build developer tooling and internal tools that improve developer productivity. "
        "Improve developer experience through build systems, Bazel, and CI/CD automation.",
    ),
}


class HighScoreCandidateTests(unittest.TestCase):
    def test_each_seed_preference_has_an_independent_high_score_heuristic(self):
        self.assertEqual(tuple(EXAMPLES), SUPPORTED_PREFERENCES)
        for preference_key, job in EXAMPLES.items():
            with self.subTest(preference_key=preference_key):
                scoring = _score_job(job, preference_key)
                self.assertIsNotNone(scoring)
                self.assertEqual(len(scoring["snippets"]), 2)

    def test_mines_seed_preferences_without_assigning_guessed_labels(self):
        preferences = load_preferences(DEFAULT_PREFERENCES)

        cases, report = mine_high_score_cases(
            list(EXAMPLES.values()),
            preferences,
            excluded_fingerprints=set(),
            target_per_preference=1,
            max_preferences_per_job=10,
        )

        self.assertEqual(len(cases), 10)
        self.assertEqual({case.preference_key for case in cases}, set(SUPPORTED_PREFERENCES))
        self.assertTrue(all(case.label_score is None for case in cases))
        self.assertTrue(all(case.label_available is None for case in cases))
        self.assertTrue(all(len(case.relevant_snippets) == 2 for case in cases))
        self.assertIn("exclusively", report["preference_source_policy"])
        self.assertIn("not a label", report["estimate_policy"])

        repeated, _ = mine_high_score_cases(
            list(EXAMPLES.values()),
            preferences,
            excluded_fingerprints=set(),
            target_per_preference=1,
            max_preferences_per_job=10,
        )
        self.assertEqual([case.case_id for case in cases], [case.case_id for case in repeated])

    def test_cli_defaults_to_400_seed_preference_candidates(self):
        args = build_parser().parse_args(["mine-high-score-candidates"])

        self.assertEqual(args.target_per_preference, 40)
        self.assertEqual(args.max_preferences_per_job, 2)
        self.assertEqual(args.max_evidence_reuse, 2)
        self.assertEqual(args.preferences, DEFAULT_PREFERENCES)
        self.assertTrue(args.output.endswith("high-score-candidates.json"))
        self.assertTrue(
            {"remote_work", "coding", "backend_infra"}.isdisjoint(SUPPORTED_PREFERENCES)
        )


if __name__ == "__main__":
    unittest.main()
