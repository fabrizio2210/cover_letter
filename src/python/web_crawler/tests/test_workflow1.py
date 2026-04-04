from __future__ import annotations

import unittest

from bson import ObjectId

from src.python.web_crawler.company_resolver import build_company_document, canonicalize_company_name, upsert_companies
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany
from src.python.web_crawler.workflow1 import get_enabled_adapters, load_identity_seed, run_workflow1


class FakeInsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = docs or []

    def find_one(self, filter_doc):
        for doc in self.docs:
            matched = True
            for key, value in filter_doc.items():
                if doc.get(key) != value:
                    matched = False
                    break
            if matched:
                return doc
        return None

    def insert_one(self, document):
        stored = dict(document)
        stored.setdefault("_id", ObjectId())
        self.docs.append(stored)
        return FakeInsertResult(stored["_id"])

    def update_one(self, filter_doc, update_doc):
        existing = self.find_one(filter_doc)
        if existing is None:
            raise AssertionError("document not found for update")
        existing.update(update_doc.get("$set", {}))


class FakeDatabase(dict):
    pass


class StubAdapter:
    source_name = "stub"

    def __init__(self, companies=None, exc=None):
        self.companies = companies or []
        self.exc = exc

    def discover_companies(self, roles, config):
        if self.exc is not None:
            raise self.exc
        return list(self.companies)


class Workflow1Tests(unittest.TestCase):
    def test_canonicalize_company_name_strips_suffixes(self):
        self.assertEqual(canonicalize_company_name("ACME, Inc."), "acme")
        self.assertEqual(canonicalize_company_name("Example GmbH"), "example")

    def test_load_identity_seed_requires_roles(self):
        identities = FakeCollection(docs=[{"_id": ObjectId("507f1f77bcf86cd799439011"), "roles": []}])
        with self.assertRaisesRegex(ValueError, "has no roles"):
            load_identity_seed(identities, "507f1f77bcf86cd799439011")

    def test_build_company_document_uses_proto_field_mapping(self):
        field_id = "507f1f77bcf86cd799439012"
        document = build_company_document(
            DiscoveredCompany(name="Acme Inc", source="stub", role="engineer"),
            field_id=field_id,
        )
        self.assertEqual(document["name"], "Acme Inc")
        self.assertEqual(document["field_id"], ObjectId(field_id))
        self.assertNotIn("field", document)

    def test_upsert_companies_is_idempotent(self):
        companies = FakeCollection()
        discovered = [
            DiscoveredCompany(name="Acme, Inc.", source="stub", role="engineer", source_url="https://example.com/acme"),
            DiscoveredCompany(name="ACME", source="stub", role="platform engineer", source_url="https://example.com/acme-2"),
        ]

        inserted_count, updated_count, company_ids = upsert_companies(companies, discovered)
        self.assertEqual(inserted_count, 1)
        self.assertEqual(updated_count, 0)
        self.assertEqual(len(company_ids), 1)

        inserted_count, updated_count, company_ids = upsert_companies(companies, discovered)
        self.assertEqual(inserted_count, 0)
        self.assertEqual(updated_count, 1)
        self.assertEqual(len(company_ids), 1)

    def test_get_enabled_adapters_includes_hackernews(self):
        adapters = get_enabled_adapters(["hackernews"])
        self.assertEqual(len(adapters), 1)
        self.assertEqual(adapters[0].source_name, "hackernews")

    def test_upsert_companies_persists_ats_metadata_from_urls(self):
        companies = FakeCollection()
        discovered = [
            DiscoveredCompany(
                name="Acme",
                source="hackernews",
                role="software engineer",
                source_url="https://news.ycombinator.com/item?id=1",
                careers_url="https://jobs.lever.co/acme/abcd",
            )
        ]

        inserted_count, updated_count, company_ids = upsert_companies(companies, discovered)
        self.assertEqual(inserted_count, 1)
        self.assertEqual(updated_count, 0)
        self.assertEqual(len(company_ids), 1)

        stored = companies.docs[0]
        self.assertEqual(stored.get("ats_provider"), "lever")
        self.assertEqual(stored.get("ats_slug"), "acme")

    def test_upsert_companies_keeps_existing_ats_metadata(self):
        companies = FakeCollection(
            docs=[
                {
                    "_id": ObjectId("507f1f77bcf86cd799439014"),
                    "name": "Acme",
                    "canonical_name": "acme",
                    "description": "",
                    "ats_provider": "greenhouse",
                    "ats_slug": "acme-gh",
                    "discovery_sources": [],
                }
            ]
        )

        discovered = [
            DiscoveredCompany(
                name="Acme",
                source="hackernews",
                role="software engineer",
                careers_url="https://jobs.lever.co/acme/job-123",
            )
        ]

        inserted_count, updated_count, _ = upsert_companies(companies, discovered)
        self.assertEqual(inserted_count, 0)
        self.assertEqual(updated_count, 1)
        self.assertEqual(companies.docs[0].get("ats_provider"), "greenhouse")
        self.assertEqual(companies.docs[0].get("ats_slug"), "acme-gh")

    def test_run_workflow1_collects_partial_failures(self):
        identity_id = "507f1f77bcf86cd799439011"
        identities = FakeCollection(
            docs=[
                {
                    "_id": ObjectId(identity_id),
                    "roles": ["software engineer"],
                    "field_id": ObjectId("507f1f77bcf86cd799439012"),
                }
            ]
        )
        companies = FakeCollection()
        database = FakeDatabase({"identities": identities, "companies": companies})
        config = CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="cover_letter", enabled_sources=[])

        from src.python.web_crawler import workflow1 as workflow1_module

        original_get_enabled_adapters = workflow1_module.get_enabled_adapters
        workflow1_module.get_enabled_adapters = lambda enabled_sources: [
            StubAdapter(
                companies=[
                    DiscoveredCompany(name="Acme", source="stub", role="software engineer"),
                ]
            ),
            StubAdapter(exc=RuntimeError("source failed")),
        ]
        try:
            result = run_workflow1(database, config, identity_id)
        finally:
            workflow1_module.get_enabled_adapters = original_get_enabled_adapters

        self.assertEqual(result.discovered_count, 1)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(result.failed_sources), 1)
        self.assertEqual(result.failed_sources[0]["source"], "stub")


if __name__ == "__main__":
    unittest.main()