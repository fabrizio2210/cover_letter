from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, call, patch

from bson import ObjectId

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.dispatcher.main import (
    _fan_out_enrichment_events,
    _query_companies_needing_enrichment,
)


class FakeCollection:
    """Minimal MongoDB collection fake that handles the $and/$or/$exists/$in operators
    used by _query_companies_needing_enrichment."""

    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, filter_doc=None, projection=None):
        def _matches(doc, flt):
            for key, condition in flt.items():
                if key == "$and":
                    if not all(_matches(doc, branch) for branch in condition):
                        return False
                elif key == "$or":
                    if not any(_matches(doc, branch) for branch in condition):
                        return False
                elif isinstance(condition, dict):
                    value = doc.get(key)
                    if "$in" in condition:
                        if value not in condition["$in"]:
                            return False
                    if "$exists" in condition:
                        present = key in doc
                        if condition["$exists"] != present:
                            return False
                else:
                    if doc.get(key) != condition:
                        return False
            return True

        results = [doc for doc in self.docs if filter_doc is None or _matches(doc, filter_doc)]

        if projection is None:
            return results

        projected = []
        for doc in results:
            p: dict = {}
            for key, include in projection.items():
                if include and key in doc:
                    p[key] = doc[key]
            projected.append(p)
        return projected


class FakeDatabase(dict):
    pass


# ---------------------------------------------------------------------------
# Tests for _query_companies_needing_enrichment
# ---------------------------------------------------------------------------

class QueryCompaniesNeedingEnrichmentTests(unittest.TestCase):
    def _db(self, docs):
        return FakeDatabase({"companies": FakeCollection(docs)})

    def test_returns_company_missing_both_ats_fields(self):
        oid = ObjectId()
        db = self._db([{"_id": oid, "name": "Acme"}])
        result = _query_companies_needing_enrichment(db)
        self.assertEqual(result, [str(oid)])

    def test_returns_company_with_null_ats_provider(self):
        oid = ObjectId()
        db = self._db([{"_id": oid, "name": "Acme", "ats_provider": None, "ats_slug": "acme"}])
        result = _query_companies_needing_enrichment(db)
        self.assertEqual(result, [str(oid)])

    def test_returns_company_with_null_ats_slug(self):
        oid = ObjectId()
        db = self._db([{"_id": oid, "name": "Acme", "ats_provider": "lever", "ats_slug": None}])
        result = _query_companies_needing_enrichment(db)
        self.assertEqual(result, [str(oid)])

    def test_returns_company_with_empty_string_ats_provider(self):
        oid = ObjectId()
        db = self._db([{"_id": oid, "name": "Acme", "ats_provider": "", "ats_slug": "acme"}])
        result = _query_companies_needing_enrichment(db)
        self.assertEqual(result, [str(oid)])

    def test_returns_company_with_empty_string_ats_slug(self):
        oid = ObjectId()
        db = self._db([{"_id": oid, "name": "Acme", "ats_provider": "lever", "ats_slug": ""}])
        result = _query_companies_needing_enrichment(db)
        self.assertEqual(result, [str(oid)])

    def test_excludes_fully_enriched_company(self):
        oid = ObjectId()
        db = self._db([{"_id": oid, "name": "Acme", "ats_provider": "lever", "ats_slug": "acme"}])
        result = _query_companies_needing_enrichment(db)
        self.assertEqual(result, [])

    def test_excludes_company_with_terminal_failure(self):
        oid = ObjectId()
        db = self._db([
            {
                "_id": oid,
                "name": "Acme",
                "enrichment_ats_enrichment_terminal_failure": {
                    "failure_type": "dns_resolution",
                    "message": "NXDOMAIN",
                },
            }
        ])
        result = _query_companies_needing_enrichment(db)
        self.assertEqual(result, [])

    def test_returns_empty_list_when_no_companies(self):
        db = self._db([])
        result = _query_companies_needing_enrichment(db)
        self.assertEqual(result, [])

    def test_returns_only_unenriched_from_mixed_collection(self):
        enriched_id = ObjectId()
        terminal_id = ObjectId()
        pending_id = ObjectId()
        db = self._db([
            {"_id": enriched_id, "name": "Done", "ats_provider": "greenhouse", "ats_slug": "done"},
            {
                "_id": terminal_id,
                "name": "Failed",
                "enrichment_ats_enrichment_terminal_failure": {"failure_type": "timeout"},
            },
            {"_id": pending_id, "name": "Pending"},
        ])
        result = _query_companies_needing_enrichment(db)
        self.assertEqual(result, [str(pending_id)])

    def test_returns_hex_strings_not_objectids(self):
        oid = ObjectId()
        db = self._db([{"_id": oid, "name": "Acme"}])
        result = _query_companies_needing_enrichment(db)
        self.assertIsInstance(result[0], str)
        self.assertEqual(result[0], str(oid))


# ---------------------------------------------------------------------------
# Tests for _fan_out_enrichment_events
# ---------------------------------------------------------------------------

class FanOutEnrichmentEventsTests(unittest.TestCase):
    def setUp(self):
        self.config = CrawlerConfig(
            mongo_host="mongodb://localhost:27017/",
            db_name="cover_letter",
        )

    def _redis(self):
        return MagicMock()

    def _db(self, docs):
        return FakeDatabase({"companies": FakeCollection(docs)})

    def test_pushes_one_event_per_unenriched_company(self):
        ids = [ObjectId(), ObjectId(), ObjectId()]
        db = self._db([{"_id": oid, "name": f"Co{i}"} for i, oid in enumerate(ids)])
        redis_client = self._redis()

        count = _fan_out_enrichment_events(
            redis_client, self.config, db, run_id="run1", identity_id="ident1"
        )

        self.assertEqual(count, 3)
        self.assertEqual(redis_client.rpush.call_count, 3)
        for c in redis_client.rpush.call_args_list:
            queue_name = c.args[0]
            self.assertEqual(queue_name, self.config.crawler_enrichment_ats_enrichment_queue_name)

    def test_event_payload_has_correct_fields(self):
        oid = ObjectId()
        db = self._db([{"_id": oid, "name": "Acme"}])
        redis_client = self._redis()

        _fan_out_enrichment_events(
            redis_client, self.config, db, run_id="run42", identity_id="id99"
        )

        self.assertEqual(redis_client.rpush.call_count, 1)
        _, raw_payload = redis_client.rpush.call_args.args
        payload = json.loads(raw_payload)

        self.assertEqual(payload["run_id"], "run42")
        self.assertEqual(payload["identity_id"], "id99")
        self.assertEqual(payload["company_id"], str(oid))
        self.assertEqual(payload["reason"], "no_ats_slug")
        self.assertEqual(payload["workflow_id"], "dispatcher")
        self.assertIn("workflow_run_id", payload)

    def test_all_events_share_the_same_workflow_run_id(self):
        ids = [ObjectId(), ObjectId()]
        db = self._db([{"_id": oid, "name": f"Co{i}"} for i, oid in enumerate(ids)])
        redis_client = self._redis()

        _fan_out_enrichment_events(
            redis_client, self.config, db, run_id="r1", identity_id="i1"
        )

        payloads = [json.loads(c.args[1]) for c in redis_client.rpush.call_args_list]
        workflow_run_ids = {p["workflow_run_id"] for p in payloads}
        self.assertEqual(len(workflow_run_ids), 1)

    def test_returns_zero_when_no_companies_need_enrichment(self):
        oid = ObjectId()
        db = self._db([{"_id": oid, "ats_provider": "lever", "ats_slug": "acme"}])
        redis_client = self._redis()

        count = _fan_out_enrichment_events(
            redis_client, self.config, db, run_id="r1", identity_id="i1"
        )

        self.assertEqual(count, 0)
        redis_client.rpush.assert_not_called()

    def test_continues_after_individual_push_failure(self):
        ids = [ObjectId(), ObjectId(), ObjectId()]
        db = self._db([{"_id": oid, "name": f"Co{i}"} for i, oid in enumerate(ids)])
        redis_client = self._redis()
        # Fail on the second push, succeed on the others.
        redis_client.rpush.side_effect = [None, RuntimeError("connection lost"), None]

        count = _fan_out_enrichment_events(
            redis_client, self.config, db, run_id="r1", identity_id="i1"
        )

        self.assertEqual(count, 2)
        self.assertEqual(redis_client.rpush.call_count, 3)

    def test_excludes_terminal_failure_companies_from_fan_out(self):
        good_id = ObjectId()
        bad_id = ObjectId()
        db = self._db([
            {"_id": good_id, "name": "Good"},
            {
                "_id": bad_id,
                "name": "Bad",
                "enrichment_ats_enrichment_terminal_failure": {"failure_type": "timeout"},
            },
        ])
        redis_client = self._redis()

        count = _fan_out_enrichment_events(
            redis_client, self.config, db, run_id="r1", identity_id="i1"
        )

        self.assertEqual(count, 1)
        _, raw_payload = redis_client.rpush.call_args.args
        payload = json.loads(raw_payload)
        self.assertEqual(payload["company_id"], str(good_id))

    def test_event_run_id_forwarded_correctly(self):
        oid = ObjectId()
        db = self._db([{"_id": oid, "name": "X"}])
        redis_client = self._redis()

        _fan_out_enrichment_events(
            redis_client, self.config, db, run_id="my-run-id", identity_id="my-identity"
        )

        _, raw_payload = redis_client.rpush.call_args.args
        payload = json.loads(raw_payload)
        self.assertEqual(payload["run_id"], "my-run-id")
        self.assertEqual(payload["identity_id"], "my-identity")


if __name__ == "__main__":
    unittest.main()
