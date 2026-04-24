from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from bson import ObjectId

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.crawler_4dayweek import worker as worker_module
from src.python.web_crawler.crawler_4dayweek import workflow as workflow_module
from src.python.web_crawler.crawler_4dayweek.fourdayweek import FourDayWeekJobCard
from src.python.web_crawler.models import WorkflowResult



class FakeInsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeCollection:
    def __init__(self, docs=None):
        self.docs: list[dict] = list(docs or [])

    def find_one(self, filter_doc, projection=None):
        for doc in self.docs:
            matched = True
            for key, value in filter_doc.items():
                if doc.get(key) != value:
                    matched = False
                    break
            if matched:
                if projection is None:
                    return doc
                return {key: doc[key] for key, include in projection.items() if include and key in doc}
        return None

    def find(self, filter_doc=None, projection=None):
        def _matches(doc, flt):
            for key, cond in flt.items():
                if key == "$or":
                    if not any(_matches(doc, branch) for branch in cond):
                        return False
                    continue
                if isinstance(cond, dict):
                    value = doc.get(key)
                    if "$in" in cond and value not in cond["$in"]:
                        return False
                    if "$exists" in cond:
                        exists = key in doc
                        if bool(cond["$exists"]) != exists:
                            return False
                    continue
                if doc.get(key) != cond:
                    return False
            return True

        results = [doc for doc in self.docs if not filter_doc or _matches(doc, filter_doc)]
        if projection is None:
            return list(results)
        return [{key: doc[key] for key, include in projection.items() if include and key in doc} for doc in results]

    def insert_one(self, document):
        stored = dict(document)
        stored.setdefault("_id", ObjectId())
        self.docs.append(stored)
        return FakeInsertResult(stored["_id"])

    def update_one(self, filter_doc, update_doc):
        target = self.find_one(filter_doc)
        if target is None:
            raise AssertionError("document not found for update")
        target.update(update_doc.get("$set", {}))


class FakeDatabase(dict):
    pass


class FakeRedis:
    def __init__(self, blpop_side_effect=None):
        self._blpop_side_effect = list(blpop_side_effect or [])
        self.rpush_calls: list[tuple[str, str]] = []
        self.publish_calls: list[tuple[str, str]] = []
        self.ping_calls = 0

    def ping(self):
        self.ping_calls += 1
        return True

    def blpop(self, _queues, timeout=0):
        if not self._blpop_side_effect:
            return None
        next_item = self._blpop_side_effect.pop(0)
        if isinstance(next_item, BaseException):
            raise next_item
        return next_item

    def rpush(self, queue_name, payload):
        self.rpush_calls.append((queue_name, payload))

    def publish(self, channel_name, payload):
        self.publish_calls.append((channel_name, payload))


def _make_config(**kwargs) -> CrawlerConfig:
    return CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="test", **kwargs)


def _make_card(
    *,
    title="Software Engineer",
    company="Acme",
    external_id="job-1",
    source_url="https://4dayweek.io/job/software-engineer-at-acme-81d43928",
    role="software engineer",
):
    return FourDayWeekJobCard(
        job_title=title,
        company_name=company,
        source_url=source_url,
        external_job_id=external_id,
        role=role,
        description="Build systems.",
        location="Remote",
        company_domain="acme.test",
    )


class Crawler4DayWeekWorkflowTests(unittest.TestCase):
    def test_run_crawler_4dayweek_returns_early_when_no_roles(self):
        identity_id = str(ObjectId())
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": []}]),
                "companies": FakeCollection(),
                "jobs": FakeCollection(),
            }
        )
        progress_events = []

        result = workflow_module.run_crawler_4dayweek(
            db,
            _make_config(),
            identity_id,
            progress_callback=lambda completed, estimated, message: progress_events.append((completed, estimated, message)),
        )

        self.assertEqual(result.discovered_count, 0)
        self.assertEqual(progress_events, [(0, 1, "Skipping: identity has no configured roles")])

    def test_run_crawler_4dayweek_tracks_new_companies(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(),
                "jobs": FakeCollection(),
            }
        )
        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = [_make_card(external_id="81d43928")]

        with patch("src.python.web_crawler.crawler_4dayweek.workflow.FourDayWeekAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_4dayweek.workflow.upsert_companies", return_value=(1, 0, [str(company_oid)])):
            result = workflow_module.run_crawler_4dayweek(db, _make_config(), identity_id)

        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.new_company_ids, [str(company_oid)])

    def test_run_crawler_4dayweek_does_not_mark_existing_company_as_new(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(docs=[{"_id": company_oid, "canonical_name": "acme", "name": "Acme"}]),
                "jobs": FakeCollection(),
            }
        )
        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = [_make_card(external_id="81d43928")]

        with patch("src.python.web_crawler.crawler_4dayweek.workflow.FourDayWeekAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_4dayweek.workflow.upsert_companies", return_value=(0, 1, [str(company_oid)])):
            result = workflow_module.run_crawler_4dayweek(db, _make_config(), identity_id)

        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.new_company_ids, [])

    def test_run_crawler_4dayweek_skips_jobs_that_do_not_match_identity_roles(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["designer"]}]),
                "companies": FakeCollection(docs=[{"_id": company_oid, "canonical_name": "acme", "name": "Acme"}]),
                "jobs": FakeCollection(),
            }
        )
        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = [_make_card(external_id="81d43928")]

        with self.assertLogs("src.python.web_crawler.crawler_4dayweek.workflow", level="DEBUG") as captured_logs, \
            patch("src.python.web_crawler.crawler_4dayweek.workflow.FourDayWeekAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_4dayweek.workflow.upsert_companies", return_value=(0, 1, [str(company_oid)])), \
            patch("src.python.web_crawler.crawler_4dayweek.workflow._upsert_job") as mock_upsert:
            result = workflow_module.run_crawler_4dayweek(db, _make_config(), identity_id)

        mock_upsert.assert_not_called()
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.inserted_count, 0)
        self.assertTrue(
            any("does not match identity roles; skipping" in entry for entry in captured_logs.output),
            captured_logs.output,
        )

    def test_run_crawler_4dayweek_logs_when_company_cannot_be_resolved(self):
        identity_id = str(ObjectId())
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(),
                "jobs": FakeCollection(),
            }
        )
        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = [
            _make_card(company="", external_id="81d43928", title="Software Engineer", role="software engineer")
        ]

        with self.assertLogs("src.python.web_crawler.crawler_4dayweek.workflow", level="DEBUG") as captured_logs:
            with patch("src.python.web_crawler.crawler_4dayweek.workflow.FourDayWeekAdapter", return_value=fake_adapter):
                result = workflow_module.run_crawler_4dayweek(db, _make_config(), identity_id)

        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.inserted_count, 0)
        self.assertTrue(
            any("could not resolve company" in entry for entry in captured_logs.output),
            captured_logs.output,
        )

    def test_run_crawler_4dayweek_logs_terminal_summary(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(),
                "jobs": FakeCollection(),
            }
        )
        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = [_make_card(external_id="81d43928")]

        with self.assertLogs("src.python.web_crawler.crawler_4dayweek.workflow", level="DEBUG") as captured_logs, \
            patch("src.python.web_crawler.crawler_4dayweek.workflow.FourDayWeekAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_4dayweek.workflow.upsert_companies", return_value=(1, 0, [str(company_oid)])):
            result = workflow_module.run_crawler_4dayweek(db, _make_config(), identity_id)

        self.assertEqual(result.inserted_count, 1)
        self.assertTrue(
            any("crawler_4dayweek summary: discovered=1 inserted=1 updated=0 skipped=0" in entry for entry in captured_logs.output),
            captured_logs.output,
        )


class Crawler4DayWeekWorkerTests(unittest.TestCase):
    def test_worker_emits_enrichment_events_for_new_companies(self):
        config = _make_config(crawler_4dayweek_queue_name="crawler_4dayweek_queue")
        message = {
            "run_id": "run-1",
            "workflow_run_id": "workflow-1",
            "workflow_id": "crawler_4dayweek",
            "identity_id": str(ObjectId()),
            "trigger_kind": "public_crawl",
            "attempt": 1,
        }
        fake_redis = FakeRedis(blpop_side_effect=[("crawler_4dayweek_queue", json.dumps(message)), KeyboardInterrupt()])

        with patch("src.python.web_crawler.crawler_4dayweek.worker._connect_redis", return_value=fake_redis), \
            patch("src.python.web_crawler.crawler_4dayweek.worker.get_database", return_value=FakeDatabase()), \
            patch(
                "src.python.web_crawler.crawler_4dayweek.worker.run_crawler_4dayweek",
                return_value=WorkflowResult(new_company_ids=[str(ObjectId())]),
            ), \
            patch("src.python.web_crawler.crawler_4dayweek.worker.increment_discovered_jobs_counter") as mock_increment:
            with self.assertRaises(KeyboardInterrupt):
                worker_module.worker_main(config)

        payloads = [json.loads(payload) for queue_name, payload in fake_redis.rpush_calls if queue_name == config.crawler_enrichment_ats_enrichment_queue_name]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["workflow_id"], "crawler_4dayweek")
        self.assertEqual(payloads[0]["reason"], "new_company_via_4dayweek")
        mock_increment.assert_called_once_with(
            config,
            workflow_id="crawler_4dayweek",
            delta=0,
        )


if __name__ == "__main__":
    unittest.main()