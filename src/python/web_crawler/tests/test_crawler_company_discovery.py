from __future__ import annotations

import unittest

from bson import ObjectId

from src.python.web_crawler.company_resolver import build_company_document, canonicalize_company_name, upsert_companies
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany
from src.python.web_crawler.crawler_company_discovery.workflow import get_enabled_adapters, load_identity_seed, run_crawler_company_discovery


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

    def find(self, filter_doc=None, projection=None):
        """Minimal find() implementation handling $in, $exists, $or for tests."""
        def _doc_matches(doc, flt):
            for key, condition in flt.items():
                if key == "$or":
                    if not any(_doc_matches(doc, branch) for branch in condition):
                        return False
                elif isinstance(condition, dict):
                    value = doc.get(key)
                    if "$in" in condition:
                        if value not in condition["$in"]:
                            return False
                    if "$exists" in condition:
                        present = key in doc and doc[key] is not None
                        if condition["$exists"] != present:
                            return False
                else:
                    if doc.get(key) != condition:
                        return False
            return True

        results = [doc for doc in self.docs if (filter_doc is None or _doc_matches(doc, filter_doc))]

        if projection:
            projected = []
            for doc in results:
                p: dict = {}
                for key, include in projection.items():
                    if include and key in doc:
                        p[key] = doc[key]
                projected.append(p)
            return projected
        return results

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


class CrawlerCompanyDiscoveryTests(unittest.TestCase):
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

    def test_run_crawler_company_discovery_collects_partial_failures(self):
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

        from src.python.web_crawler.crawler_company_discovery import workflow as ccd_module

        original_get_enabled_adapters = ccd_module.get_enabled_adapters
        ccd_module.get_enabled_adapters = lambda enabled_sources: [
            StubAdapter(
                companies=[
                    DiscoveredCompany(name="Acme", source="stub", role="software engineer"),
                ]
            ),
            StubAdapter(exc=RuntimeError("source failed")),
        ]
        try:
            result = run_crawler_company_discovery(database, config, identity_id)
        finally:
            ccd_module.get_enabled_adapters = original_get_enabled_adapters

        self.assertEqual(result.discovered_count, 1)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(result.failed_sources), 1)
        self.assertEqual(result.failed_sources[0]["source"], "stub")

    def test_run_crawler_company_discovery_sets_enrichment_pending_ids_for_companies_without_slug(self):
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

        from src.python.web_crawler.crawler_company_discovery import workflow as ccd_module

        original_adapters = ccd_module.get_enabled_adapters
        ccd_module.get_enabled_adapters = lambda enabled_sources: [
            StubAdapter(
                companies=[
                    DiscoveredCompany(name="Acme", source="stub", role="software engineer"),
                ]
            ),
        ]
        try:
            result = run_crawler_company_discovery(database, config, identity_id)
        finally:
            ccd_module.get_enabled_adapters = original_adapters

        # Company has no ats_slug, so it should appear in enrichment_pending_company_ids.
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(len(result.enrichment_pending_company_ids), 1)
        self.assertIn(result.company_ids[0], result.enrichment_pending_company_ids)

    def test_run_crawler_company_discovery_excludes_already_enriched_companies(self):
        identity_id = "507f1f77bcf86cd799439011"
        company_oid = ObjectId("507f1f77bcf86cd799439099")
        identities = FakeCollection(
            docs=[
                {
                    "_id": ObjectId(identity_id),
                    "roles": ["software engineer"],
                }
            ]
        )
        # Pre-existing company with slug already set.
        companies = FakeCollection(
            docs=[
                {
                    "_id": company_oid,
                    "name": "Acme",
                    "canonical_name": "acme",
                    "description": "",
                    "ats_provider": "lever",
                    "ats_slug": "acme",
                    "discovery_sources": [],
                }
            ]
        )
        database = FakeDatabase({"identities": identities, "companies": companies})
        config = CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="cover_letter", enabled_sources=[])

        from src.python.web_crawler.crawler_company_discovery import workflow as ccd_module

        original_adapters = ccd_module.get_enabled_adapters
        ccd_module.get_enabled_adapters = lambda enabled_sources: [
            StubAdapter(
                companies=[
                    DiscoveredCompany(name="Acme", source="stub", role="software engineer"),
                ]
            ),
        ]
        try:
            result = run_crawler_company_discovery(database, config, identity_id)
        finally:
            ccd_module.get_enabled_adapters = original_adapters

        # Company already has ats_slug so should NOT appear in enrichment_pending.
        self.assertEqual(result.updated_count, 1)
        self.assertEqual(len(result.enrichment_pending_company_ids), 0)


class FakeCompanyCollectionWithFilter(FakeCollection):
    """Extended FakeCollection that supports the $or+$in filter used by _find_companies_missing_slug."""

    def find(self, filter_doc=None, projection=None):
        if not filter_doc:
            return list(self.docs)

        id_filter = filter_doc.get("_id", {}).get("$in", [])
        or_conditions = filter_doc.get("$or", [])

        results = []
        for doc in self.docs:
            if id_filter and doc["_id"] not in id_filter:
                continue
            if or_conditions:
                slug = doc.get("ats_slug")
                missing = not slug
                if missing:
                    results.append(doc)
            else:
                results.append(doc)

        if projection:
            projected = []
            for doc in results:
                p: dict = {}
                for key, include in projection.items():
                    if include and key in doc:
                        p[key] = doc[key]
                projected.append(p)
            return projected
        return results


class FindCompaniesMissingSlugTests(unittest.TestCase):
    def test_returns_ids_for_companies_without_slug(self):
        from src.python.web_crawler.crawler_company_discovery.workflow import _find_companies_missing_slug

        oid1 = ObjectId()
        oid2 = ObjectId()
        collection = FakeCompanyCollectionWithFilter(
            docs=[
                {"_id": oid1, "name": "Acme", "canonical_name": "acme"},
                {"_id": oid2, "name": "Beta", "canonical_name": "beta", "ats_slug": "beta-co"},
            ]
        )
        result = _find_companies_missing_slug(collection, [str(oid1), str(oid2)])
        self.assertIn(str(oid1), result)
        self.assertNotIn(str(oid2), result)

    def test_returns_empty_for_empty_input(self):
        from src.python.web_crawler.crawler_company_discovery.workflow import _find_companies_missing_slug

        collection = FakeCompanyCollectionWithFilter()
        result = _find_companies_missing_slug(collection, [])
        self.assertEqual(result, [])

    def test_skips_invalid_object_ids(self):
        from src.python.web_crawler.crawler_company_discovery.workflow import _find_companies_missing_slug

        collection = FakeCompanyCollectionWithFilter()
        result = _find_companies_missing_slug(collection, ["not-an-id"])
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
