from __future__ import annotations

import sys
import types
import unittest

from bson import ObjectId


if "ollama" not in sys.modules:
    fake_ollama = types.ModuleType("ollama")

    class _StubClient:
        def __init__(self, host=None):
            self.host = host

        def chat(self, model, messages, options):
            return {"message": {"content": "3"}}

    setattr(fake_ollama, "Client", _StubClient)
    sys.modules["ollama"] = fake_ollama

from src.python.ai_scorer.ai_scorer import (
    compute_and_persist_aggregate,
    now_timestamp_dict,
    parse_object_id,
    score_preference,
    stable_test_score,
    process_scoring_job,
    resolve_scoring_context,
)


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = docs or []

    @staticmethod
    def _matches(doc, filter_doc):
        for key, value in filter_doc.items():
            if doc.get(key) != value:
                return False
        return True

    def find_one(self, filter_doc):
        for doc in self.docs:
            if self._matches(doc, filter_doc):
                return doc
        return None

    def find(self, filter_doc=None):
        if not filter_doc:
            return list(self.docs)
        return [doc for doc in self.docs if self._matches(doc, filter_doc)]

    def update_one(self, filter_doc, update_doc, upsert=False):
        existing = self.find_one(filter_doc)
        if existing is None:
            if not upsert:
                return
            existing = dict(filter_doc)
            self.docs.append(existing)

        for key, value in update_doc.get("$set", {}).items():
            existing[key] = value


class FakeOllamaClient:
    def __init__(self, response):
        self.response = response

    def chat(self, model, messages, options):
        return self.response


class AiScorerUnitTests(unittest.TestCase):
    def test_parse_object_id_handles_valid_and_invalid_values(self):
        oid = ObjectId()
        self.assertEqual(parse_object_id(oid), oid)
        self.assertEqual(parse_object_id(str(oid)), oid)
        self.assertIsNone(parse_object_id("not-an-object-id"))

    def test_stable_test_score_is_deterministic_and_in_range(self):
        score_a = stable_test_score("job-1", "remote")
        score_b = stable_test_score("job-1", "remote")
        self.assertEqual(score_a, score_b)
        self.assertGreaterEqual(score_a, 1)
        self.assertLessEqual(score_a, 5)

    def test_resolve_scoring_context_success(self):
        field_id = ObjectId()
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[
                {
                    "_id": job_id,
                    "company": company_id,
                    "title": "Platform Engineer",
                    "description": "Work on infra",
                }
            ]
        )
        companies = FakeCollection(docs=[{"_id": company_id, "field": field_id, "name": "Acme"}])
        identities = FakeCollection(
            docs=[
                {
                    "_id": identity_id,
                    "field": field_id,
                    "name": "Fab",
                    "preferences": [
                        {"key": "remote", "label": "Remote", "weight": 2, "enabled": True},
                        {"key": "onsite", "label": "Onsite", "weight": 1, "enabled": False},
                    ],
                }
            ]
        )

        context, error = resolve_scoring_context(jobs, companies, identities, str(job_id))

        self.assertIsNone(error)
        self.assertIsNotNone(context)
        if context is None:
            self.fail("Expected scoring context")
        _, _, _, enabled = context
        self.assertIsNotNone(enabled)
        if enabled is None:
            self.fail("Expected enabled preferences")
        self.assertEqual(len(enabled), 1)
        self.assertEqual(enabled[0]["key"], "remote")

    def test_resolve_scoring_context_marks_missing_identity(self):
        field_id = ObjectId()
        company_id = ObjectId()
        job_id = ObjectId()

        jobs = FakeCollection(docs=[{"_id": job_id, "company": company_id}])
        companies = FakeCollection(docs=[{"_id": company_id, "field": field_id}])
        identities = FakeCollection(docs=[])

        context, error = resolve_scoring_context(jobs, companies, identities, str(job_id))

        self.assertEqual(error, "identity_not_found")
        self.assertIsNotNone(context)

    def test_score_preference_uses_test_mode(self):
        score = score_preference(
            ollama_client=None,
            model_name="unused",
            test_mode=True,
            job_id="507f1f77bcf86cd799439011",
            preference={"key": "remote", "label": "Remote", "weight": 1, "enabled": True},
            job_doc={},
            company_doc={},
            identity_doc={},
        )

        self.assertGreaterEqual(score, 1)
        self.assertLessEqual(score, 5)

    def test_score_preference_parses_ollama_response(self):
        client = FakeOllamaClient(
            {
                "message": {
                    "content": "4"
                }
            }
        )

        score = score_preference(
            ollama_client=client,
            model_name="qwen2.5:1.5b",
            test_mode=False,
            job_id="507f1f77bcf86cd799439011",
            preference={"key": "remote", "label": "Remote", "weight": 1, "enabled": True},
            job_doc={"title": "Engineer", "description": "desc", "location": "EU", "platform": "ashby"},
            company_doc={"name": "Acme", "description": "Infra"},
            identity_doc={"name": "Fab", "description": "Platform"},
        )

        self.assertEqual(score, 4)

    def test_compute_and_persist_aggregate_updates_job_document(self):
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(docs=[{"_id": job_id, "scoring_status": "queued"}])
        scores = FakeCollection(
            docs=[
                {
                    "job_id": str(job_id),
                    "identity_id": str(identity_id),
                    "preference_key": "remote",
                    "preference_weight": 2.0,
                    "score": 5,
                },
                {
                    "job_id": str(job_id),
                    "identity_id": str(identity_id),
                    "preference_key": "coding",
                    "preference_weight": 1.0,
                    "score": 3,
                },
            ]
        )

        compute_and_persist_aggregate(jobs, scores, {"_id": job_id}, {"_id": identity_id})

        updated = jobs.find_one({"_id": job_id})
        self.assertIsNotNone(updated)
        if updated is None:
            self.fail("Expected updated job document")
        self.assertEqual(updated.get("scoring_status"), "scored")
        self.assertAlmostEqual(updated.get("weighted_score"), 13.0 / 3.0)
        self.assertEqual(updated.get("max_score"), 5)
        self.assertIsInstance(updated.get("updated_at"), dict)

    def test_process_scoring_job_success_path(self):
        field_id = ObjectId()
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(
            docs=[
                {
                    "_id": job_id,
                    "company": company_id,
                    "title": "Platform Engineer",
                    "description": "distributed systems",
                    "location": "Remote",
                    "platform": "lever",
                    "scoring_status": "unscored",
                }
            ]
        )
        companies = FakeCollection(
            docs=[
                {
                    "_id": company_id,
                    "field": field_id,
                    "name": "Acme",
                    "description": "Infrastructure company",
                }
            ]
        )
        identities = FakeCollection(
            docs=[
                {
                    "_id": identity_id,
                    "field": field_id,
                    "name": "Fab",
                    "description": "Platform profile",
                    "preferences": [
                        {"key": "remote", "label": "Remote", "weight": 2, "enabled": True},
                        {"key": "backend", "label": "Backend", "weight": 1, "enabled": True},
                    ],
                }
            ]
        )
        score_docs = FakeCollection()

        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            ollama_client=None,
            model_name="unused",
            test_mode=True,
        )

        job = jobs.find_one({"_id": job_id})
        self.assertIsNotNone(job)
        if job is None:
            self.fail("Expected scored job document")
        self.assertEqual(job.get("scoring_status"), "scored")
        self.assertEqual(job.get("max_score") in {1, 2, 3, 4, 5}, True)

        stored_scores = score_docs.find({"job_id": str(job_id), "identity_id": str(identity_id)})
        self.assertEqual(len(stored_scores), 2)
        for score in stored_scores:
            self.assertIn("scored_at", score)

    def test_process_scoring_job_sets_skipped_when_no_preferences(self):
        field_id = ObjectId()
        company_id = ObjectId()
        job_id = ObjectId()
        identity_id = ObjectId()

        jobs = FakeCollection(docs=[{"_id": job_id, "company": company_id, "scoring_status": "unscored"}])
        companies = FakeCollection(docs=[{"_id": company_id, "field": field_id}])
        identities = FakeCollection(docs=[{"_id": identity_id, "field": field_id, "preferences": []}])
        score_docs = FakeCollection()

        process_scoring_job(
            job_id=str(job_id),
            job_descriptions_col=jobs,
            companies_col=companies,
            identities_col=identities,
            job_preference_scores_col=score_docs,
            ollama_client=None,
            model_name="unused",
            test_mode=True,
        )

        job = jobs.find_one({"_id": job_id})
        self.assertIsNotNone(job)
        if job is None:
            self.fail("Expected skipped job document")
        self.assertEqual(job.get("scoring_status"), "skipped")
        self.assertEqual(score_docs.docs, [])


class TimestampTests(unittest.TestCase):
    def test_now_timestamp_dict_shape(self):
        ts = now_timestamp_dict()
        self.assertIn("seconds", ts)
        self.assertIn("nanos", ts)
        self.assertIsInstance(ts["seconds"], int)
        self.assertEqual(ts["nanos"], 0)


if __name__ == "__main__":
    unittest.main()
