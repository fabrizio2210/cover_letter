from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from bson import ObjectId

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.executor import ATSWorkerResult
from src.python.web_crawler.enrichment_ats_enrichment.workflow import _discover_candidate_urls, run_enrichment_ats_enrichment


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = docs or []

    @staticmethod
    def _set_path(target: dict, dotted_key: str, value):
        parts = dotted_key.split(".")
        current = target
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    @staticmethod
    def _inc_path(target: dict, dotted_key: str, amount: int):
        parts = dotted_key.split(".")
        current = target
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = int(current.get(parts[-1]) or 0) + amount

    def find(self, filter_doc=None):
        if not filter_doc:
            return list(self.docs)

        if filter_doc.get("_id", {}).get("$in"):
            ids = set(filter_doc["_id"]["$in"])
            return [doc for doc in self.docs if doc["_id"] in ids]
        return []

    def update_one(self, filter_doc, update_doc):
        for doc in self.docs:
            if doc.get("_id") == filter_doc.get("_id"):
                for key, value in update_doc.get("$set", {}).items():
                    self._set_path(doc, key, value)
                for key, value in update_doc.get("$inc", {}).items():
                    self._inc_path(doc, key, value)
                return
        raise AssertionError("document not found for update")

    def find_one(self, filter_doc, projection=None):
        for doc in self.docs:
            if doc.get("_id") != filter_doc.get("_id"):
                continue
            if projection is None:
                return dict(doc)
            projected: dict = {}
            for key, include in projection.items():
                if include and key in doc:
                    projected[key] = doc[key]
            return projected
        return None


class FakeDatabase(dict):
    pass


class EnrichmentAtsEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.config = CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="cover_letter")

    @staticmethod
    def _worker_result(
        company_id: ObjectId,
        company_name: str,
        company_index: int,
        *,
        success: bool,
        provider: str | None = None,
        slug: str | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        error_url: str | None = None,
    ) -> ATSWorkerResult:
        return ATSWorkerResult(
            company_id=str(company_id),
            company_object_id=company_id,
            company_name=company_name,
            company_index=company_index,
            success=success,
            provider=provider,
            slug=slug,
            error_type=error_type,
            error_message=error_message,
            error_url=error_url,
        )

    def test_run_enrichment_ats_enrichment_enriches_company_document(self):
        company_id = ObjectId()
        companies = FakeCollection(
            docs=[
                {
                    "_id": company_id,
                    "name": "Acme",
                    "discovery_sources": [{"careers_url": "https://acme.test/careers", "domain": "acme.test"}],
                }
            ]
        )
        database = FakeDatabase({"companies": companies})

        with patch(
            "src.python.web_crawler.enrichment_ats_enrichment.workflow._detect_ats_worker",
            return_value=self._worker_result(company_id, "Acme", 1, success=True, provider="lever", slug="acme"),
        ):
            result = run_enrichment_ats_enrichment(database, self.config, [str(company_id)])

        self.assertEqual(result.enriched_count, 1)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(result.ats_providers, {"lever": 1})
        self.assertEqual(companies.docs[0]["ats_provider"], "lever")
        self.assertEqual(companies.docs[0]["ats_slug"], "acme")

    def test_run_enrichment_ats_enrichment_collects_partial_failures(self):
        first_id = ObjectId()
        second_id = ObjectId()
        companies = FakeCollection(
            docs=[
                {
                    "_id": first_id,
                    "name": "Acme",
                    "discovery_sources": [{"careers_url": "https://acme.test/careers"}],
                },
                {
                    "_id": second_id,
                    "name": "Beta",
                    "discovery_sources": [{"careers_url": "https://beta.test/careers"}],
                },
            ]
        )
        database = FakeDatabase({"companies": companies})

        with patch(
            "src.python.web_crawler.enrichment_ats_enrichment.workflow._detect_ats_worker",
            side_effect=[
                self._worker_result(first_id, "Acme", 1, success=False, error_type="unexpected_error", error_message="boom"),
                self._worker_result(second_id, "Beta", 2, success=True, provider="greenhouse", slug="beta"),
            ],
        ):
            result = run_enrichment_ats_enrichment(database, self.config, [str(first_id), str(second_id)])

        self.assertEqual(result.enriched_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(result.failed_companies), 1)
        self.assertEqual(companies.docs[1]["ats_provider"], "greenhouse")
        self.assertEqual(companies.docs[1]["ats_slug"], "beta")

    def test_run_enrichment_ats_enrichment_skips_already_enriched_companies(self):
        company_id = ObjectId()
        companies = FakeCollection(
            docs=[
                {
                    "_id": company_id,
                    "name": "Acme",
                    "ats_provider": "lever",
                    "ats_slug": "acme",
                    "discovery_sources": [{"careers_url": "https://acme.test/careers"}],
                }
            ]
        )
        database = FakeDatabase({"companies": companies})

        with patch("src.python.web_crawler.enrichment_ats_enrichment.workflow._detect_ats_worker") as detect_mock:
            result = run_enrichment_ats_enrichment(database, self.config, [str(company_id)])

        self.assertEqual(result.enriched_count, 0)
        self.assertEqual(result.skipped_count, 1)
        detect_mock.assert_not_called()

    def test_run_enrichment_ats_enrichment_records_search_attempt_and_skips_repeat_paid_search(self):
        company_id = ObjectId()
        companies = FakeCollection(
            docs=[
                {
                    "_id": company_id,
                    "name": "Acme",
                    "discovery_sources": [{"careers_url": "https://acme.test/careers", "domain": "acme.test"}],
                }
            ]
        )
        database = FakeDatabase({"companies": companies})

        with patch(
            "src.python.web_crawler.enrichment_ats_enrichment.workflow._detect_ats_worker",
            return_value=self._worker_result(company_id, "Acme", 1, success=False, provider="lever", error_type="slug_not_resolved_direct", error_message="direct slug failed"),
        ), patch(
            "src.python.web_crawler.enrichment_ats_enrichment.workflow.resolve_slug_via_search_dorking",
            return_value=None,
        ) as search_mock:
            first_result = run_enrichment_ats_enrichment(database, self.config, [str(company_id)])

        self.assertEqual(first_result.skipped_count, 1)
        self.assertEqual(companies.docs[0]["ats_slug_search_attempts"]["lever"]["attempts"], 1)
        self.assertEqual(companies.docs[0]["ats_slug_search_attempts"]["lever"]["outcome"], "no_results")
        self.assertEqual(search_mock.call_count, 1)

        with patch(
            "src.python.web_crawler.enrichment_ats_enrichment.workflow._detect_ats_worker",
            return_value=self._worker_result(company_id, "Acme", 1, success=False, provider="lever", error_type="slug_not_resolved_direct", error_message="direct slug failed"),
        ), patch(
            "src.python.web_crawler.enrichment_ats_enrichment.workflow.resolve_slug_via_search_dorking",
            return_value="should-not-run",
        ) as repeated_search_mock:
            second_result = run_enrichment_ats_enrichment(database, self.config, [str(company_id)])

        self.assertEqual(second_result.skipped_count, 1)
        self.assertEqual(companies.docs[0]["ats_slug_search_attempts"]["lever"]["attempts"], 1)
        repeated_search_mock.assert_not_called()

    def test_run_enrichment_ats_enrichment_skips_company_after_terminal_failure(self):
        company_id = ObjectId()
        companies = FakeCollection(
            docs=[
                {
                    "_id": company_id,
                    "name": "Acme",
                    "discovery_sources": [{"careers_url": "https://acme.test/careers", "domain": "acme.test"}],
                }
            ]
        )
        database = FakeDatabase({"companies": companies})

        with patch(
            "src.python.web_crawler.enrichment_ats_enrichment.workflow._detect_ats_worker",
            return_value=self._worker_result(
                company_id,
                "Acme",
                1,
                success=False,
                error_type="ats_request_failure:dns_resolution",
                error_message="failed to resolve",
                error_url="https://acme.test/careers",
            ),
        ):
            first_result = run_enrichment_ats_enrichment(database, self.config, [str(company_id)])

        self.assertEqual(first_result.skipped_count, 1)
        self.assertEqual(companies.docs[0]["enrichment_ats_enrichment_terminal_failure"]["failure_type"], "dns_resolution")

        with patch("src.python.web_crawler.enrichment_ats_enrichment.workflow._detect_ats_worker") as detect_mock:
            second_result = run_enrichment_ats_enrichment(database, self.config, [str(company_id)])

        self.assertEqual(second_result.skipped_count, 1)
        detect_mock.assert_not_called()

    def test_run_enrichment_ats_enrichment_reports_additive_progress_across_parallel_tasks_and_fallback(self):
        first_id = ObjectId()
        second_id = ObjectId()
        companies = FakeCollection(
            docs=[
                {
                    "_id": first_id,
                    "name": "Acme",
                    "discovery_sources": [{"careers_url": "https://acme.test/careers"}],
                },
                {
                    "_id": second_id,
                    "name": "Beta",
                    "discovery_sources": [{"careers_url": "https://beta.test/careers"}],
                },
            ]
        )
        database = FakeDatabase({"companies": companies})
        progress_events: list[tuple[int, int, str]] = []

        with patch(
            "src.python.web_crawler.enrichment_ats_enrichment.workflow._detect_ats_worker",
            side_effect=[
                self._worker_result(first_id, "Acme", 1, success=True, provider="lever", slug="acme"),
                self._worker_result(second_id, "Beta", 2, success=False, provider="greenhouse", error_type="slug_not_resolved_direct", error_message="direct slug failed"),
            ],
        ), patch(
            "src.python.web_crawler.enrichment_ats_enrichment.workflow.resolve_slug_via_search_dorking",
            return_value=None,
        ):
            result = run_enrichment_ats_enrichment(
                database,
                self.config,
                [str(first_id), str(second_id)],
                progress_callback=lambda completed, estimated, message: progress_events.append((completed, estimated, message)),
            )

        self.assertEqual(result.enriched_count, 1)
        self.assertEqual(result.skipped_count, 1)
        self.assertTrue(progress_events)
        self.assertEqual(progress_events[-1][0], 3)
        self.assertEqual(progress_events[-1][1], 3)
        self.assertIn(3, [estimated for _, estimated, _ in progress_events])
        completed_values = [completed for completed, _, _ in progress_events]
        self.assertEqual(completed_values, sorted(completed_values))

    def test_discover_candidate_urls_returns_careers_and_jobs_paths(self):
        company = self._company_proto(
            company_id=str(ObjectId()),
            name="Acme",
            discovery_sources=[
                {"careers_url": "https://acme.test/careers", "domain": "acme.test", "source_url": "https://acme.test"}
            ],
        )

        urls = _discover_candidate_urls(company, self.config, FakeSession())

        self.assertIn("https://acme.test/careers", urls)
        self.assertIn("https://acme.test/jobs", urls)
        self.assertEqual(urls.count("https://acme.test/careers"), 1)

    @staticmethod
    def _company_proto(company_id: str, name: str, discovery_sources: list[dict]):
        company = common_pb2.Company(id=company_id, name=name)
        for source_data in discovery_sources:
            source = company.discovery_sources.add()
            source.careers_url = source_data.get("careers_url", "")
            source.source_url = source_data.get("source_url", "")
            source.domain = source_data.get("domain", "")
        return company


class FakeSession:
    def get(self, *_args, **_kwargs):
        raise AssertionError("network should be mocked in this test")


if __name__ == "__main__":
    unittest.main()