from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, Mock, patch

from bson import ObjectId

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig, JOB_SCORING_QUEUE
from src.python.web_crawler.workflow3 import run_workflow3, upsert_job


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeCollection:
    def __init__(self, docs=None):
        self.docs: list[dict] = list(docs or [])
        self._id_counter = 0

    def find(self, filter_doc=None, projection=None):
        if not filter_doc:
            return list(self.docs)

        in_ids = (filter_doc.get("_id") or {}).get("$in")
        if in_ids is not None:
            id_set = set(in_ids)
            result = [doc for doc in self.docs if doc["_id"] in id_set]
        else:
            result = list(self.docs)

        ats_provider_filter = filter_doc.get("ats_provider")
        ats_slug_filter = filter_doc.get("ats_slug")
        if ats_provider_filter and "$exists" in ats_provider_filter:
            result = [doc for doc in result if doc.get("ats_provider")]
        if ats_slug_filter and "$exists" in ats_slug_filter:
            result = [doc for doc in result if doc.get("ats_slug")]

        if projection:
            projected_result = []
            for doc in result:
                projected = {}
                for key, include in projection.items():
                    if include and key in doc:
                        projected[key] = doc[key]
                projected_result.append(projected)
            return projected_result

        return result

    def find_one(self, filter_doc, projection=None):
        # match by platform + external_job_id
        if "platform" in filter_doc and "external_job_id" in filter_doc:
            for doc in self.docs:
                if doc.get("platform") == filter_doc["platform"] and doc.get("external_job_id") == filter_doc["external_job_id"]:
                    return self._project(doc, projection)
            return None

        if "_id" in filter_doc:
            for doc in self.docs:
                if doc.get("_id") == filter_doc["_id"]:
                    return self._project(doc, projection)
        return None

    @staticmethod
    def _project(doc: dict, projection) -> dict:
        if projection is None:
            return dict(doc)
        result = {}
        for key, include in projection.items():
            if include and key in doc:
                result[key] = doc[key]
        return result

    def insert_one(self, document: dict):
        self._id_counter += 1
        oid = ObjectId()
        document["_id"] = oid
        self.docs.append(document)
        result = Mock()
        result.inserted_id = oid
        return result

    def update_one(self, filter_doc, update_doc):
        for doc in self.docs:
            if "_id" in filter_doc and doc.get("_id") != filter_doc["_id"]:
                continue
            for key, value in update_doc.get("$set", {}).items():
                doc[key] = value


class FakeDatabase(dict):
    pass


def _make_config(**kwargs) -> CrawlerConfig:
    return CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="test", **kwargs)


class AtsFetcherGreenhouseTests(unittest.TestCase):
    def setUp(self):
        self.config = _make_config()

    def test_fetch_greenhouse_jobs_normalizes_fields(self):
        payload = {
            "jobs": [
                {
                    "id": 123,
                    "title": "Software Engineer",
                    "content": "<p>Build things.</p>",
                    "location": {"name": "Remote"},
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                }
            ]
        }
        session = Mock()
        session.request.return_value = FakeResponse(payload)

        from src.python.web_crawler.sources.ats_job_fetcher import _fetch_greenhouse_jobs

        jobs = _fetch_greenhouse_jobs("acme", self.config, session)

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.title, "Software Engineer")
        self.assertEqual(job.platform, "greenhouse")
        self.assertEqual(job.external_job_id, "123")
        self.assertEqual(job.location, "Remote")
        self.assertEqual(job.source_url, "https://boards.greenhouse.io/acme/jobs/123")
        self.assertIn("Build things", job.description)

    def test_fetch_greenhouse_jobs_returns_empty_on_non_200(self):
        session = Mock()
        session.request.return_value = FakeResponse({}, status_code=404)

        from src.python.web_crawler.sources.ats_job_fetcher import _fetch_greenhouse_jobs

        jobs = _fetch_greenhouse_jobs("missing", self.config, session)
        self.assertEqual(jobs, [])

    def test_fetch_greenhouse_jobs_skips_entries_missing_id_or_title(self):
        payload = {"jobs": [{"title": "No ID"}, {"id": 456, "title": ""}, {"id": 789, "title": "Valid"}]}
        session = Mock()
        session.request.return_value = FakeResponse(payload)

        from src.python.web_crawler.sources.ats_job_fetcher import _fetch_greenhouse_jobs

        jobs = _fetch_greenhouse_jobs("acme", self.config, session)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_job_id, "789")


class AtsFetcherLeverTests(unittest.TestCase):
    def setUp(self):
        self.config = _make_config()

    def test_fetch_lever_jobs_normalizes_fields(self):
        payload = [
            {
                "id": "abc-123",
                "text": "Backend Engineer",
                "categories": {"location": "London"},
                "hostedUrl": "https://jobs.lever.co/acme/abc-123",
                "lists": [
                    {"text": "Requirements", "content": "<ul><li>5 years Python</li></ul>"},
                ],
            }
        ]
        session = Mock()
        session.request.return_value = FakeResponse(payload)

        from src.python.web_crawler.sources.ats_job_fetcher import _fetch_lever_jobs

        jobs = _fetch_lever_jobs("acme", self.config, session)

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.title, "Backend Engineer")
        self.assertEqual(job.platform, "lever")
        self.assertEqual(job.external_job_id, "abc-123")
        self.assertEqual(job.location, "London")
        self.assertEqual(job.source_url, "https://jobs.lever.co/acme/abc-123")
        self.assertIn("Requirements", job.description)

    def test_fetch_lever_jobs_returns_empty_on_non_list_response(self):
        session = Mock()
        session.request.return_value = FakeResponse({"error": "not found"}, status_code=200)

        from src.python.web_crawler.sources.ats_job_fetcher import _fetch_lever_jobs

        jobs = _fetch_lever_jobs("acme", self.config, session)
        self.assertEqual(jobs, [])


class AtsFetcherAshbyTests(unittest.TestCase):
    def setUp(self):
        self.config = _make_config()

    def test_fetch_ashby_jobs_normalizes_fields(self):
        payload = {
            "jobPostings": [
                {
                    "id": "jid-42",
                    "title": "Data Engineer",
                    "descriptionHtml": "<p>Work with data.</p>",
                    "location": "New York",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/jid-42",
                }
            ]
        }
        session = Mock()
        session.request.return_value = FakeResponse(payload)

        from src.python.web_crawler.sources.ats_job_fetcher import _fetch_ashby_jobs

        jobs = _fetch_ashby_jobs("acme", self.config, session)

        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.title, "Data Engineer")
        self.assertEqual(job.platform, "ashby")
        self.assertEqual(job.external_job_id, "jid-42")
        self.assertEqual(job.location, "New York")
        self.assertEqual(job.source_url, "https://jobs.ashbyhq.com/acme/jid-42")
        self.assertIn("Work with data", job.description)

    def test_fetch_ashby_jobs_returns_empty_on_request_failure(self):
        session = Mock()
        session.request.return_value = FakeResponse({}, status_code=500)

        from src.python.web_crawler.sources.ats_job_fetcher import _fetch_ashby_jobs

        jobs = _fetch_ashby_jobs("acme", self.config, session)
        self.assertEqual(jobs, [])


class UpsertJobTests(unittest.TestCase):
    def setUp(self):
        self.config = _make_config()
        self.company_oid = ObjectId()

    def _make_job(self, external_id="ext-1", title="Engineer", platform="greenhouse") -> common_pb2.Job:
        return common_pb2.Job(
            title=title,
            description="Do things.",
            location="Remote",
            platform=platform,
            external_job_id=external_id,
            source_url="https://example.com/job/1",
        )

    def test_upsert_job_inserts_new_document_with_lifecycle_defaults(self):
        jobs_coll = FakeCollection()
        job = self._make_job()

        job_id, inserted = upsert_job(jobs_coll, job, self.company_oid)

        self.assertTrue(inserted)
        self.assertEqual(len(jobs_coll.docs), 1)
        doc = jobs_coll.docs[0]
        self.assertEqual(doc["title"], "Engineer")
        self.assertEqual(doc["platform"], "greenhouse")
        self.assertEqual(doc["external_job_id"], "ext-1")
        self.assertEqual(doc["scoring_status"], "unscored")
        self.assertEqual(doc["weighted_score"], 0)
        self.assertEqual(doc["company_id"], self.company_oid)
        self.assertIn("seconds", doc["created_at"])
        self.assertIn("seconds", doc["updated_at"])
        self.assertEqual(str(doc["_id"]), job_id)

    def test_upsert_job_updates_existing_document_on_recrawl(self):
        existing_id = ObjectId()
        jobs_coll = FakeCollection(
            docs=[
                {
                    "_id": existing_id,
                    "platform": "greenhouse",
                    "external_job_id": "ext-1",
                    "title": "Old Title",
                    "description": "Old desc",
                    "location": "Old place",
                    "source_url": "https://old.com",
                    "scoring_status": "scored",
                    "weighted_score": 4.2,
                    "company_id": self.company_oid,
                    "created_at": {"seconds": 1000, "nanos": 0},
                    "updated_at": {"seconds": 1000, "nanos": 0},
                }
            ]
        )

        updated_job = self._make_job(title="New Title")
        job_id, inserted = upsert_job(jobs_coll, updated_job, self.company_oid)

        self.assertFalse(inserted)
        self.assertEqual(job_id, str(existing_id))
        doc = jobs_coll.docs[0]
        self.assertEqual(doc["title"], "New Title")
        # scoring_status must not be reset on update
        self.assertEqual(doc["scoring_status"], "scored")
        # created_at must not change
        self.assertEqual(doc["created_at"]["seconds"], 1000)


class Workflow3Tests(unittest.TestCase):
    def setUp(self):
        self.config = _make_config()
        self.company_oid = ObjectId()
        self.identity_oid = ObjectId()

    def _make_fake_database(self, companies=None, jobs=None, identities=None):
        db = FakeDatabase()
        db["companies"] = FakeCollection(docs=companies or [])
        db["jobs"] = FakeCollection(docs=jobs or [])
        db["identities"] = FakeCollection(docs=identities or [])
        return db

    def _make_company_doc(self, provider="greenhouse", slug="acme"):
        return {
            "_id": self.company_oid,
            "name": "Acme",
            "ats_provider": provider,
            "ats_slug": slug,
        }

    def _make_identity_doc(self, roles=None):
        return {
            "_id": self.identity_oid,
            "roles": ["software engineer"] if roles is None else roles,
        }

    def _stub_fetch_jobs(self, *args, **kwargs):
        return [
            common_pb2.Job(
                title="Stub Engineer",
                description="Stub desc",
                location="Remote",
                platform="greenhouse",
                external_job_id="stub-1",
                source_url="https://example.com/1",
            )
        ]

    def _make_job(self, title="Stub Engineer", description="Stub desc", external_job_id="stub-1"):
        return common_pb2.Job(
            title=title,
            description=description,
            location="Remote",
            platform="greenhouse",
            external_job_id=external_job_id,
            source_url=f"https://example.com/{external_job_id}",
        )

    def test_run_workflow3_returns_inserted_count(self):
        db = self._make_fake_database(companies=[self._make_company_doc()])

        with patch("src.python.web_crawler.workflow3.fetch_jobs", side_effect=self._stub_fetch_jobs):
            result = run_workflow3(db, self.config)

        self.assertEqual(result.fetched_count, 1)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(len(result.job_ids), 1)

    def test_run_workflow3_updates_on_recrawl(self):
        existing_id = ObjectId()
        db = self._make_fake_database(
            companies=[self._make_company_doc()],
            jobs=[
                {
                    "_id": existing_id,
                    "platform": "greenhouse",
                    "external_job_id": "stub-1",
                    "title": "Old",
                    "description": "Old",
                    "location": "Old",
                    "source_url": "https://old.com",
                    "scoring_status": "scored",
                    "weighted_score": 3.0,
                    "company_id": self.company_oid,
                    "created_at": {"seconds": 1000, "nanos": 0},
                    "updated_at": {"seconds": 1000, "nanos": 0},
                }
            ],
        )

        with patch("src.python.web_crawler.workflow3.fetch_jobs", side_effect=self._stub_fetch_jobs):
            result = run_workflow3(db, self.config)

        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.updated_count, 1)

    def test_run_workflow3_skips_companies_without_ats_slug(self):
        db = self._make_fake_database(
            companies=[
                {"_id": self.company_oid, "name": "No ATS", "ats_provider": "greenhouse"},
                # No ats_slug
            ]
        )

        with patch("src.python.web_crawler.workflow3.fetch_jobs", side_effect=self._stub_fetch_jobs) as mock_fetch:
            result = run_workflow3(db, self.config)

        mock_fetch.assert_not_called()
        self.assertEqual(result.inserted_count, 0)

    def test_run_workflow3_filters_by_company_ids(self):
        other_oid = ObjectId()
        db = self._make_fake_database(
            companies=[
                self._make_company_doc(),
                {"_id": other_oid, "name": "Other", "ats_provider": "lever", "ats_slug": "other"},
            ]
        )

        with patch("src.python.web_crawler.workflow3.fetch_jobs", side_effect=self._stub_fetch_jobs) as mock_fetch:
            result = run_workflow3(db, self.config, company_ids=[str(self.company_oid)])

        self.assertEqual(mock_fetch.call_count, 1)
        self.assertEqual(result.inserted_count, 1)

    def test_run_workflow3_collects_failed_companies(self):
        db = self._make_fake_database(companies=[self._make_company_doc()])

        with patch("src.python.web_crawler.workflow3.fetch_jobs", side_effect=RuntimeError("boom")):
            result = run_workflow3(db, self.config)

        self.assertEqual(len(result.failed_companies), 1)
        self.assertEqual(result.failed_companies[0]["company_name"], "Acme")
        self.assertIn("boom", result.failed_companies[0]["error"])

    def test_run_workflow3_enqueues_on_insert_when_enabled(self):
        config = _make_config(enable_scoring_enqueue=True)
        db = self._make_fake_database(companies=[self._make_company_doc()])

        fake_redis = Mock()
        fake_redis.rpush = Mock()

        with patch("src.python.web_crawler.workflow3.fetch_jobs", side_effect=self._stub_fetch_jobs), \
             patch("src.python.web_crawler.workflow3._connect_redis", return_value=fake_redis):
            result = run_workflow3(db, config)

        fake_redis.rpush.assert_called_once()
        call_args = fake_redis.rpush.call_args
        self.assertEqual(call_args[0][0], JOB_SCORING_QUEUE)
        payload = json.loads(call_args[0][1])
        self.assertIn("job_id", payload)
        self.assertEqual(result.enqueued_count, 1)
        self.assertEqual(result.enqueue_failed_count, 0)

        doc = db["jobs"].docs[0]
        self.assertEqual(doc["scoring_status"], "queued")

    def test_run_workflow3_sets_scoring_status_failed_on_enqueue_failure(self):
        config = _make_config(enable_scoring_enqueue=True)
        db = self._make_fake_database(companies=[self._make_company_doc()])

        fake_redis = Mock()
        fake_redis.rpush = Mock(side_effect=Exception("redis down"))

        with patch("src.python.web_crawler.workflow3.fetch_jobs", side_effect=self._stub_fetch_jobs), \
             patch("src.python.web_crawler.workflow3._connect_redis", return_value=fake_redis):
            result = run_workflow3(db, config)

        self.assertEqual(result.enqueue_failed_count, 1)
        self.assertEqual(result.enqueued_count, 0)
        doc = db["jobs"].docs[0]
        self.assertEqual(doc["scoring_status"], "failed")

    def test_run_workflow3_no_enqueue_when_disabled(self):
        config = _make_config(enable_scoring_enqueue=False)
        db = self._make_fake_database(companies=[self._make_company_doc()])

        with patch("src.python.web_crawler.workflow3.fetch_jobs", side_effect=self._stub_fetch_jobs), \
             patch("src.python.web_crawler.workflow3._connect_redis") as mock_connect:
            result = run_workflow3(db, config)

        mock_connect.assert_not_called()
        self.assertEqual(result.enqueued_count, 0)
        doc = db["jobs"].docs[0]
        self.assertEqual(doc["scoring_status"], "unscored")

    def test_run_workflow3_skips_jobs_that_do_not_match_identity_roles(self):
        db = self._make_fake_database(
            companies=[self._make_company_doc()],
            identities=[self._make_identity_doc(["software engineer"])],
        )

        with patch(
            "src.python.web_crawler.workflow3.fetch_jobs",
            return_value=[self._make_job(title="Data Scientist", description="Analyze metrics", external_job_id="role-miss")],
        ):
            result = run_workflow3(db, self.config, identity_id=str(self.identity_oid))

        self.assertEqual(result.fetched_count, 1)
        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(len(db["jobs"].docs), 0)

    def test_run_workflow3_inserts_job_when_title_matches_identity_role(self):
        db = self._make_fake_database(
            companies=[self._make_company_doc()],
            identities=[self._make_identity_doc(["software engineer"])],
        )

        with patch(
            "src.python.web_crawler.workflow3.fetch_jobs",
            return_value=[self._make_job(title="Senior Software Engineer", description="Build systems", external_job_id="role-title")],
        ):
            result = run_workflow3(db, self.config, identity_id=str(self.identity_oid))

        self.assertEqual(result.fetched_count, 1)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(db["jobs"].docs), 1)

    def test_run_workflow3_inserts_job_when_description_matches_identity_role(self):
        db = self._make_fake_database(
            companies=[self._make_company_doc()],
            identities=[self._make_identity_doc(["platform engineer"])],
        )

        with patch(
            "src.python.web_crawler.workflow3.fetch_jobs",
            return_value=[self._make_job(title="Infrastructure Specialist", description="You will work as a platform engineer across our stack", external_job_id="role-description")],
        ):
            result = run_workflow3(db, self.config, identity_id=str(self.identity_oid))

        self.assertEqual(result.fetched_count, 1)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(db["jobs"].docs[0]["external_job_id"], "role-description")

    def test_run_workflow3_accepts_all_jobs_when_identity_roles_are_empty(self):
        db = self._make_fake_database(
            companies=[self._make_company_doc()],
            identities=[self._make_identity_doc([])],
        )

        with patch(
            "src.python.web_crawler.workflow3.fetch_jobs",
            return_value=[self._make_job(title="Data Scientist", description="Analyze metrics", external_job_id="role-empty")],
        ):
            result = run_workflow3(db, self.config, identity_id=str(self.identity_oid))

        self.assertEqual(result.fetched_count, 1)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(db["jobs"].docs), 1)


if __name__ == "__main__":
    unittest.main()
