from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.sources.ats_detector import ATSDetectionResult, ATSRequestFailure
from src.python.web_crawler.workflow2 import run_workflow2


class Workflow2MongoIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mongo_host = os.getenv("MONGO_HOST", "mongodb://localhost:27017/")
        db_name = os.getenv("DB_NAME", "cover_letter_integration")

        cls.client = MongoClient(mongo_host, serverSelectionTimeoutMS=5000)
        cls.client.admin.command("ping")
        cls.database = cls.client[db_name]
        cls.config = CrawlerConfig(
            mongo_host=mongo_host,
            db_name=db_name,
            serper_api_key="test-key",
        )

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def setUp(self):
        try:
            self.database["companies"].delete_many({})
        except OperationFailure as exc:
            if exc.code == 13:
                self.skipTest("Mongo integration tests require authenticated write access")
            raise

        self.company_id = ObjectId()
        try:
            self.database["companies"].insert_one(
                {
                    "_id": self.company_id,
                    "name": "Acme",
                    "canonical_name": "acme",
                    "discovery_sources": [
                        {
                            "source": "stub",
                            "role": "software engineer",
                            "careers_url": "https://acme.test/careers",
                            "domain": "acme.test",
                        }
                    ],
                }
            )
        except OperationFailure as exc:
            if exc.code == 13:
                self.skipTest("Mongo integration tests require authenticated write access")
            raise

    def test_run_workflow2_persists_ats_metadata_and_is_idempotent(self):
        with patch(
            "src.python.web_crawler.workflow2.detect_ats_provider",
            return_value=ATSDetectionResult(provider="lever", board_url="https://jobs.lever.co/acme"),
        ), patch(
            "src.python.web_crawler.workflow2.resolve_direct_slug",
            return_value="acme",
        ), patch(
            "src.python.web_crawler.workflow2._fetch_sitemap_candidate_urls",
            return_value=[],
        ):
            first_result = run_workflow2(self.database, self.config, [str(self.company_id)])

        self.assertEqual(first_result.enriched_count, 1)
        stored = self.database["companies"].find_one({"_id": self.company_id})
        if stored is None:
            self.fail("expected company document to exist")
        self.assertEqual(stored["ats_provider"], "lever")
        self.assertEqual(stored["ats_slug"], "acme")

        with patch("src.python.web_crawler.workflow2.detect_ats_provider") as detect_mock:
            second_result = run_workflow2(self.database, self.config, [str(self.company_id)])

        self.assertEqual(second_result.enriched_count, 0)
        self.assertEqual(second_result.skipped_count, 1)
        detect_mock.assert_not_called()

    def test_run_workflow2_persists_search_attempt_and_avoids_repeat_search(self):
        with patch(
            "src.python.web_crawler.workflow2.detect_ats_provider",
            return_value=ATSDetectionResult(provider="lever", board_url="https://acme.test/careers"),
        ), patch(
            "src.python.web_crawler.workflow2.resolve_direct_slug",
            return_value=None,
        ), patch(
            "src.python.web_crawler.workflow2.resolve_slug_via_search_dorking",
            return_value=None,
        ), patch(
            "src.python.web_crawler.workflow2._fetch_sitemap_candidate_urls",
            return_value=[],
        ) as first_search_mock:
            first_result = run_workflow2(self.database, self.config, [str(self.company_id)])

        self.assertEqual(first_result.skipped_count, 1)
        self.assertEqual(first_search_mock.call_count, 1)
        stored = self.database["companies"].find_one({"_id": self.company_id})
        if stored is None:
            self.fail("expected company document to exist")
        self.assertEqual(stored["ats_slug_search_attempts"]["lever"]["attempts"], 1)
        self.assertEqual(stored["ats_slug_search_attempts"]["lever"]["outcome"], "no_results")

        with patch(
            "src.python.web_crawler.workflow2.detect_ats_provider",
            return_value=ATSDetectionResult(provider="lever", board_url="https://acme.test/careers"),
        ), patch(
            "src.python.web_crawler.workflow2.resolve_direct_slug",
            return_value=None,
        ), patch(
            "src.python.web_crawler.workflow2.resolve_slug_via_search_dorking",
            return_value="should-not-run",
        ), patch(
            "src.python.web_crawler.workflow2._fetch_sitemap_candidate_urls",
            return_value=[],
        ) as second_search_mock:
            second_result = run_workflow2(self.database, self.config, [str(self.company_id)])

        self.assertEqual(second_result.skipped_count, 1)
        second_search_mock.assert_not_called()

    def test_run_workflow2_persists_terminal_failure_and_skips_future_runs(self):
        with patch(
            "src.python.web_crawler.workflow2.detect_ats_provider",
            side_effect=ATSRequestFailure("timeout", "https://acme.test/careers", "timed out"),
        ), patch(
            "src.python.web_crawler.workflow2._fetch_sitemap_candidate_urls",
            return_value=[],
        ):
            first_result = run_workflow2(self.database, self.config, [str(self.company_id)])

        self.assertEqual(first_result.skipped_count, 1)
        stored = self.database["companies"].find_one({"_id": self.company_id})
        if stored is None:
            self.fail("expected company document to exist")
        self.assertEqual(stored["workflow2_terminal_failure"]["failure_type"], "timeout")

        with patch("src.python.web_crawler.workflow2.detect_ats_provider") as detect_mock:
            second_result = run_workflow2(self.database, self.config, [str(self.company_id)])

        self.assertEqual(second_result.skipped_count, 1)
        detect_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()