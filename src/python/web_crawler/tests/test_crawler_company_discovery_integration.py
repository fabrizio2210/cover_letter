from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from bson import ObjectId
from google.protobuf.json_format import MessageToDict
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany
from src.python.web_crawler.crawler_company_discovery_workflow import run_crawler_company_discovery


class StubAdapter:
    source_name = "stub"

    def __init__(self, companies=None, exc=None):
        self.companies = companies or []
        self.exc = exc

    def discover_companies(self, roles, config):
        if self.exc is not None:
            raise self.exc
        return list(self.companies)


class CrawlerCompanyDiscoveryMongoIntegrationTests(unittest.TestCase):
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
            enabled_sources=["stub"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def setUp(self):
        try:
            self.database["identities"].delete_many({})
            self.database["companies"].delete_many({})
        except OperationFailure as exc:
            if exc.code == 13:
                self.skipTest("Mongo integration tests require authenticated write access")
            raise

        self.field_id = ObjectId()
        self.identity_id = ObjectId()
        identity_proto = common_pb2.Identity(
            id=str(self.identity_id),
            roles=["software engineer"],
            field_id=str(self.field_id),
        )
        identity_doc = MessageToDict(identity_proto, preserving_proto_field_name=True)
        identity_doc["_id"] = ObjectId(identity_doc.pop("id"))
        identity_doc["field_id"] = ObjectId(identity_doc["field_id"])
        try:
            self.database["identities"].insert_one(identity_doc)
        except OperationFailure as exc:
            if exc.code == 13:
                self.skipTest("Mongo integration tests require authenticated write access")
            raise

    def test_run_crawler_company_discovery_persists_and_updates_companies(self):
        first_run_companies = [
            DiscoveredCompany(
                name="Acme, Inc.",
                description="Developer tooling",
                source="stub",
                role="software engineer",
                source_url="https://example.test/acme",
                careers_url="https://careers.example.test/acme",
                domain="acme.test",
            ),
            DiscoveredCompany(
                name="Beta Labs LLC",
                description="Cloud analytics",
                source="stub",
                role="software engineer",
                source_url="https://example.test/beta",
                careers_url="https://careers.example.test/beta",
                domain="beta.test",
            ),
        ]

        with patch(
            "src.python.web_crawler.crawler_company_discovery_workflow.get_enabled_adapters",
            return_value=[StubAdapter(companies=first_run_companies)],
        ):
            result = run_crawler_company_discovery(self.database, self.config, str(self.identity_id))

        self.assertEqual(result.discovered_count, 2)
        self.assertEqual(result.inserted_count, 2)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(result.failed_sources, [])
        self.assertEqual(len(result.company_ids), 2)

        stored_docs = list(self.database["companies"].find({}))
        self.assertEqual(len(stored_docs), 2)

        acme = self.database["companies"].find_one({"canonical_name": "acme"})
        if acme is None:
            self.fail("expected Acme document to be persisted")
        self.assertEqual(acme["name"], "Acme, Inc.")
        self.assertEqual(acme["field_id"], self.field_id)
        self.assertEqual(len(acme["discovery_sources"]), 1)
        self.assertEqual(acme["discovery_sources"][0]["source"], "stub")
        self.assertEqual(acme["discovery_sources"][0]["source_url"], "https://example.test/acme")

        second_run_companies = [
            DiscoveredCompany(
                name="ACME",
                description="",
                source="stub",
                role="platform engineer",
                source_url="https://example.test/acme-2",
                careers_url="https://careers.example.test/acme-2",
                domain="acme.test",
            )
        ]

        with patch(
            "src.python.web_crawler.crawler_company_discovery_workflow.get_enabled_adapters",
            return_value=[StubAdapter(companies=second_run_companies)],
        ):
            second_result = run_crawler_company_discovery(self.database, self.config, str(self.identity_id))

        self.assertEqual(second_result.discovered_count, 1)
        self.assertEqual(second_result.inserted_count, 0)
        self.assertEqual(second_result.updated_count, 1)
        self.assertEqual(second_result.skipped_count, 0)
        self.assertEqual(second_result.failed_sources, [])
        self.assertEqual(len(second_result.company_ids), 1)

        acme_updated = self.database["companies"].find_one({"canonical_name": "acme"})
        if acme_updated is None:
            self.fail("expected Acme document to be updated")
        self.assertEqual(acme_updated["field_id"], self.field_id)
        self.assertEqual(len(acme_updated["discovery_sources"]), 2)

        source_urls = {source["source_url"] for source in acme_updated["discovery_sources"]}
        self.assertSetEqual(
            source_urls,
            {
                "https://example.test/acme",
                "https://example.test/acme-2",
            },
        )


if __name__ == "__main__":
    unittest.main()
