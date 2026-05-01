from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.crawler_ats_job_extraction.workflow import run_crawler_ats_job_extraction


class CrawlerAtsJobExtractionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mongo_host = os.getenv("MONGO_HOST", "mongodb://localhost:27017/")
        db_name = os.getenv("DB_NAME", "cover_letter_integration")

        cls.client = MongoClient(mongo_host, serverSelectionTimeoutMS=5000)
        cls.client.admin.command("ping")
        cls.database = cls.client[db_name]
        cls.config = CrawlerConfig(mongo_host=mongo_host, db_name=db_name)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def setUp(self):
        try:
            self.database["companies"].delete_many({})
            self.database["job-descriptions"].delete_many({})
            self.database["identities"].delete_many({})
        except OperationFailure as exc:
            if exc.code == 13:
                self.skipTest("Mongo integration tests require authenticated write access")
            raise

        self.company_id = ObjectId()
        self.identity_id = ObjectId()
        try:
            self.database["companies"].insert_one(
                {
                    "_id": self.company_id,
                    "name": "Acme Corp",
                    "canonical_name": "acme corp",
                    "ats_provider": "greenhouse",
                    "ats_slug": "acme",
                    "discovery_sources": [],
                }
            )
            self.database["identities"].insert_one(
                {
                    "_id": self.identity_id,
                    "roles": ["software engineer"],
                }
            )
        except OperationFailure as exc:
            if exc.code == 13:
                self.skipTest("Mongo integration tests require authenticated write access")
            raise

    def _fake_fetch_jobs(self, provider, slug, config, session):
        return [
            common_pb2.Job(
                title="Integration Test Role",
                description="Does things.",
                location="Remote",
                platform=provider,
                external_job_id="int-test-1",
                source_url=f"https://example.com/{slug}/1",
            )
        ]

    def test_run_crawler_ats_job_extraction_inserts_job_into_jobs_collection(self):
        with patch("src.python.web_crawler.crawler_ats_job_extraction.workflow.fetch_jobs", side_effect=self._fake_fetch_jobs):
            result = run_crawler_ats_job_extraction(self.database, self.config)

        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(len(result.job_ids), 1)

        doc = self.database["job-descriptions"].find_one({"platform": "greenhouse", "external_job_id": "int-test-1"})
        self.assertIsNotNone(doc)
        if doc is None:
            self.fail("expected job document to be inserted")
        self.assertEqual(doc["title"], "Integration Test Role")
        self.assertEqual(doc["company"], self.company_id)
        self.assertIn("seconds", doc["created_at"])
        self.assertIn("seconds", doc["updated_at"])

    def test_run_crawler_ats_job_extraction_is_idempotent_on_recrawl(self):
        with patch("src.python.web_crawler.crawler_ats_job_extraction.workflow.fetch_jobs", side_effect=self._fake_fetch_jobs):
            first = run_crawler_ats_job_extraction(self.database, self.config)

        with patch("src.python.web_crawler.crawler_ats_job_extraction.workflow.fetch_jobs", side_effect=self._fake_fetch_jobs):
            second = run_crawler_ats_job_extraction(self.database, self.config)

        self.assertEqual(first.inserted_count, 1)
        self.assertEqual(second.inserted_count, 0)
        self.assertEqual(second.updated_count, 1)
        self.assertEqual(self.database["job-descriptions"].count_documents({}), 1)

    def test_run_crawler_ats_job_extraction_filters_by_company_ids(self):
        other_id = ObjectId()
        self.database["companies"].insert_one(
            {
                "_id": other_id,
                "name": "Other Corp",
                "canonical_name": "other corp",
                "ats_provider": "lever",
                "ats_slug": "other",
                "discovery_sources": [],
            }
        )

        with patch("src.python.web_crawler.crawler_ats_job_extraction.workflow.fetch_jobs", side_effect=self._fake_fetch_jobs) as mock_fetch:
            result = run_crawler_ats_job_extraction(self.database, self.config, company_ids=[str(self.company_id)])

        self.assertEqual(mock_fetch.call_count, 1)
        self.assertEqual(result.inserted_count, 1)

    def test_run_crawler_ats_job_extraction_skips_non_matching_jobs_for_identity_roles(self):
        def fake_fetch_jobs(provider, slug, config, session):
            return [
                common_pb2.Job(
                    title="Data Scientist",
                    description="Analyze metrics and forecasts.",
                    location="Remote",
                    platform=provider,
                    external_job_id="int-role-miss",
                    source_url=f"https://example.com/{slug}/role-miss",
                )
            ]

        with patch("src.python.web_crawler.crawler_ats_job_extraction.workflow.fetch_jobs", side_effect=fake_fetch_jobs):
            result = run_crawler_ats_job_extraction(self.database, self.config, identity_id=str(self.identity_id))

        self.assertEqual(result.fetched_count, 1)
        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(self.database["job-descriptions"].count_documents({}), 0)


if __name__ == "__main__":
    unittest.main()
