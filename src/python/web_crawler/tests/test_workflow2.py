from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from bson import ObjectId
import requests

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.sources.ats_detector import ATSDetectionResult, ATSRequestFailure, detect_ats_provider, extract_ats_signatures_from_html, fetch_url
from src.python.web_crawler.sources.ats_slug_resolver import extract_slug_from_url, resolve_slug, resolve_slug_via_search_dorking
from src.python.web_crawler.workflow2 import _discover_candidate_urls, run_workflow2


class FakeResponse:
    def __init__(self, url: str, text: str = "", status_code: int = 200, payload: dict | None = None):
        self.url = url
        self.text = text
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


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


class AtsDetectorTests(unittest.TestCase):
    def setUp(self):
        self.config = CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="cover_letter")

    def test_extract_ats_signatures_from_html_finds_all_supported_providers(self):
        html = """
        <a href=\"https://boards.greenhouse.io/acme\">Greenhouse</a>
        <div class=\"lever-job\"></div>
        <script>window.Ashby = {};</script>
        """

        providers = extract_ats_signatures_from_html(html)

        self.assertSetEqual(providers, {"greenhouse", "lever", "ashby"})

    def test_detect_ats_provider_prefers_hosted_board_links(self):
        html = '<a href="https://jobs.lever.co/acme">Jobs</a>'
        with patch(
            "src.python.web_crawler.sources.ats_detector.fetch_url",
            return_value=FakeResponse(url="https://acme.test/careers", text=html),
        ):
            result = detect_ats_provider(["https://acme.test/careers"], self.config)

        self.assertIsNotNone(result)
        self.assertEqual(result.provider, "lever")
        self.assertEqual(result.board_url, "https://jobs.lever.co/acme")

    def test_detect_ats_provider_uses_precedence_for_ambiguous_signatures(self):
        html = "boards.greenhouse.io and jobs.lever.co"
        with patch(
            "src.python.web_crawler.sources.ats_detector.fetch_url",
            return_value=FakeResponse(url="https://acme.test/jobs", text=html),
        ):
            result = detect_ats_provider(["https://acme.test/jobs"], self.config)

        self.assertIsNotNone(result)
        self.assertEqual(result.provider, "greenhouse")
        self.assertIsNone(result.board_url)

    def test_fetch_url_fails_fast_on_dns_resolution_error(self):
        session = Mock(spec=requests.Session)
        session.get.side_effect = requests.RequestException("Failed to resolve host")

        with patch("src.python.web_crawler.sources.ats_detector._is_dns_resolution_error", return_value=True), patch(
            "src.python.web_crawler.sources.ats_detector._is_timeout_error",
            return_value=False,
        ):
            with self.assertRaises(ATSRequestFailure) as ctx:
                fetch_url(session, "https://invalid.test", self.config)

        self.assertEqual(ctx.exception.failure_type, "dns_resolution")
        self.assertEqual(session.get.call_count, 1)

    def test_fetch_url_fails_fast_on_timeout_error(self):
        session = Mock(spec=requests.Session)
        session.get.side_effect = requests.Timeout("timed out")

        with patch("src.python.web_crawler.sources.ats_detector._is_dns_resolution_error", return_value=False), patch(
            "src.python.web_crawler.sources.ats_detector._is_timeout_error",
            return_value=True,
        ):
            with self.assertRaises(ATSRequestFailure) as ctx:
                fetch_url(session, "https://slow.test", self.config)

        self.assertEqual(ctx.exception.failure_type, "timeout")
        self.assertEqual(session.get.call_count, 1)


class AtsSlugResolverTests(unittest.TestCase):
    def setUp(self):
        self.config = CrawlerConfig(
            mongo_host="mongodb://localhost:27017/",
            db_name="cover_letter",
            serper_api_key="test-key",
        )

    def test_extract_slug_from_url_supports_greenhouse_query_format(self):
        slug = extract_slug_from_url("https://boards.greenhouse.io/embed/job_board?for=acme", "greenhouse")
        self.assertEqual(slug, "acme")

    def test_extract_slug_from_url_supports_provider_paths(self):
        self.assertEqual(extract_slug_from_url("https://jobs.lever.co/acme/platform-engineer", "lever"), "acme")
        self.assertEqual(extract_slug_from_url("https://jobs.ashbyhq.com/acme/software-engineer", "ashby"), "acme")

    def test_resolve_slug_via_search_dorking_uses_serper_results(self):
        payload = {"organic": [{"link": "https://jobs.lever.co/acme/platform-engineer"}]}
        with patch(
            "src.python.web_crawler.sources.ats_slug_resolver._request_with_retries",
            return_value=FakeResponse(url=self.config.serper_search_url, payload=payload),
        ), patch(
            "src.python.web_crawler.sources.ats_slug_resolver.validate_slug_via_api",
            return_value=True,
        ):
            slug = resolve_slug_via_search_dorking("Acme", "lever", self.config)

        self.assertEqual(slug, "acme")

    def test_resolve_slug_prefers_board_url_before_search(self):
        with patch(
            "src.python.web_crawler.sources.ats_slug_resolver.validate_slug_via_api",
            return_value=True,
        ), patch(
            "src.python.web_crawler.sources.ats_slug_resolver.resolve_slug_via_search_dorking",
            return_value="should-not-be-used",
        ):
            slug = resolve_slug(
                company_name="Acme",
                provider="lever",
                config=self.config,
                board_url="https://jobs.lever.co/acme/platform-engineer",
            )

        self.assertEqual(slug, "acme")


class Workflow2Tests(unittest.TestCase):
    def setUp(self):
        self.config = CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="cover_letter")

    def test_run_workflow2_enriches_company_document(self):
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
            "src.python.web_crawler.workflow2.detect_ats_provider",
            return_value=ATSDetectionResult(provider="lever", board_url="https://jobs.lever.co/acme"),
        ), patch(
            "src.python.web_crawler.workflow2.resolve_direct_slug",
            return_value="acme",
        ):
            result = run_workflow2(database, self.config, [str(company_id)])

        self.assertEqual(result.enriched_count, 1)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(result.ats_providers, {"lever": 1})
        self.assertEqual(companies.docs[0]["ats_provider"], "lever")
        self.assertEqual(companies.docs[0]["ats_slug"], "acme")

    def test_run_workflow2_collects_partial_failures(self):
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
            "src.python.web_crawler.workflow2.detect_ats_provider",
            side_effect=[RuntimeError("boom"), ATSDetectionResult(provider="greenhouse", board_url="https://boards.greenhouse.io/beta")],
        ), patch(
            "src.python.web_crawler.workflow2.resolve_direct_slug",
            return_value="beta",
        ):
            result = run_workflow2(database, self.config, [str(first_id), str(second_id)])

        self.assertEqual(result.enriched_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(result.failed_companies), 1)
        self.assertEqual(companies.docs[1]["ats_provider"], "greenhouse")
        self.assertEqual(companies.docs[1]["ats_slug"], "beta")

    def test_run_workflow2_skips_already_enriched_companies(self):
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

        with patch("src.python.web_crawler.workflow2.detect_ats_provider") as detect_mock:
            result = run_workflow2(database, self.config, [str(company_id)])

        self.assertEqual(result.enriched_count, 0)
        self.assertEqual(result.skipped_count, 1)
        detect_mock.assert_not_called()

    def test_run_workflow2_records_search_attempt_and_skips_repeat_paid_search(self):
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
            "src.python.web_crawler.workflow2.detect_ats_provider",
            return_value=ATSDetectionResult(provider="lever", board_url="https://acme.test/careers"),
        ), patch(
            "src.python.web_crawler.workflow2.resolve_direct_slug",
            return_value=None,
        ), patch(
            "src.python.web_crawler.workflow2.resolve_slug_via_search_dorking",
            return_value=None,
        ) as search_mock:
            first_result = run_workflow2(database, self.config, [str(company_id)])

        self.assertEqual(first_result.skipped_count, 1)
        self.assertEqual(companies.docs[0]["ats_slug_search_attempts"]["lever"]["attempts"], 1)
        self.assertEqual(companies.docs[0]["ats_slug_search_attempts"]["lever"]["outcome"], "no_results")
        self.assertEqual(search_mock.call_count, 1)

        with patch(
            "src.python.web_crawler.workflow2.detect_ats_provider",
            return_value=ATSDetectionResult(provider="lever", board_url="https://acme.test/careers"),
        ), patch(
            "src.python.web_crawler.workflow2.resolve_direct_slug",
            return_value=None,
        ), patch(
            "src.python.web_crawler.workflow2.resolve_slug_via_search_dorking",
            return_value="should-not-run",
        ) as repeated_search_mock:
            second_result = run_workflow2(database, self.config, [str(company_id)])

        self.assertEqual(second_result.skipped_count, 1)
        self.assertEqual(companies.docs[0]["ats_slug_search_attempts"]["lever"]["attempts"], 1)
        repeated_search_mock.assert_not_called()

    def test_run_workflow2_skips_company_after_terminal_failure(self):
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
            "src.python.web_crawler.workflow2.detect_ats_provider",
            side_effect=ATSRequestFailure("dns_resolution", "https://acme.test/careers", "failed to resolve"),
        ):
            first_result = run_workflow2(database, self.config, [str(company_id)])

        self.assertEqual(first_result.skipped_count, 1)
        self.assertEqual(companies.docs[0]["workflow2_terminal_failure"]["failure_type"], "dns_resolution")

        with patch("src.python.web_crawler.workflow2.detect_ats_provider") as detect_mock:
            second_result = run_workflow2(database, self.config, [str(company_id)])

        self.assertEqual(second_result.skipped_count, 1)
        detect_mock.assert_not_called()

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
        from src.python.ai_querier import common_pb2

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