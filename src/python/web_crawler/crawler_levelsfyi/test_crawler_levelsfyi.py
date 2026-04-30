from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from bson import ObjectId

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.crawler_levelsfyi import worker as worker_module
from src.python.web_crawler.crawler_levelsfyi import workflow as workflow_module
from src.python.web_crawler.models import WorkflowResult
from src.python.web_crawler import role_filtering
from src.python.web_crawler.sources.levelsfyi import LevelsFyiJobCard


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
                return {
                    key: doc[key]
                    for key, include in projection.items()
                    if include and key in doc
                }
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

        projected = []
        for doc in results:
            projected.append({
                key: doc[key]
                for key, include in projection.items()
                if include and key in doc
            })
        return projected

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
        if isinstance(next_item, Exception):
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
    source_url="https://www.levels.fyi/jobs?jobId=1",
    role="software engineer",
):
    return LevelsFyiJobCard(
        job_title=title,
        company_name=company,
        source_url=source_url,
        external_job_id=external_id,
        role=role,
        description="Build systems.",
        location="Remote",
    )


class CrawlerLevelsFyiHelperTests(unittest.TestCase):
    def test_to_object_id_handles_invalid_values(self):
        self.assertIsNotNone(workflow_module._to_object_id(str(ObjectId())))
        self.assertIsNone(workflow_module._to_object_id("not-an-objectid"))
        self.assertIsNone(workflow_module._to_object_id(123))

    def test_load_identity_roles_handles_missing_and_invalid_identity(self):
        identities = FakeCollection(docs=[])
        self.assertEqual(
            role_filtering.load_identity_roles(
                identities,
                "",
                logger=workflow_module.logger,
                workflow_name="crawler_levelsfyi",
            ),
            [],
        )
        self.assertEqual(
            role_filtering.load_identity_roles(
                identities,
                "not-an-id",
                logger=workflow_module.logger,
                workflow_name="crawler_levelsfyi",
            ),
            [],
        )

    def test_load_identity_roles_returns_trimmed_roles(self):
        identity_id = str(ObjectId())
        identities = FakeCollection(
            docs=[{"_id": ObjectId(identity_id), "roles": ["  software engineer  ", "", "platform engineer", 42]}]
        )

        roles = role_filtering.load_identity_roles(
            identities,
            identity_id,
            logger=workflow_module.logger,
            workflow_name="crawler_levelsfyi",
        )
        self.assertEqual(roles, ["software engineer", "platform engineer"])

    def test_find_companies_missing_slug_filters_correctly(self):
        with_slug = ObjectId()
        without_slug = ObjectId()
        collection = FakeCollection(
            docs=[
                {"_id": with_slug, "ats_slug": "acme"},
                {"_id": without_slug, "name": "NoSlug"},
            ]
        )

        result = workflow_module._find_companies_missing_slug(
            collection,
            [str(with_slug), str(without_slug), "not-an-id"],
        )

        self.assertEqual(result, [str(without_slug)])

    def test_upsert_job_insert_and_update_paths(self):
        jobs = FakeCollection()
        company_oid = ObjectId()

        job_id, inserted = workflow_module._upsert_job(
            jobs,
            job_title="Engineer",
            description="Desc",
            location="Remote",
            external_job_id="ext-1",
            source_url="https://example.com/1",
            company_oid=company_oid,
        )
        self.assertTrue(inserted)
        self.assertEqual(jobs.docs[0]["company"], company_oid)
        self.assertEqual(jobs.docs[0]["platform"], "levelsfyi")
        self.assertEqual(str(jobs.docs[0]["_id"]), job_id)

        created_at = jobs.docs[0]["created_at"]

        job_id_2, inserted_2 = workflow_module._upsert_job(
            jobs,
            job_title="Engineer II",
            description="Updated",
            location="Hybrid",
            external_job_id="ext-1",
            source_url="https://example.com/2",
            company_oid=company_oid,
        )
        self.assertFalse(inserted_2)
        self.assertEqual(job_id_2, job_id)
        self.assertEqual(jobs.docs[0]["title"], "Engineer II")
        self.assertEqual(jobs.docs[0]["created_at"], created_at)

    def test_try_enqueue_success_and_failure(self):
        config = _make_config()
        redis_client = Mock()

        self.assertTrue(workflow_module._try_enqueue(redis_client, config, "abc"))
        queue_name, payload = redis_client.rpush.call_args[0]
        self.assertEqual(queue_name, config.job_scoring_queue_name)
        self.assertEqual(json.loads(payload), {"job_id": "abc"})

        redis_client.rpush.side_effect = RuntimeError("boom")
        self.assertFalse(workflow_module._try_enqueue(redis_client, config, "def"))


class CrawlerLevelsFyiWorkflowTests(unittest.TestCase):
    def test_run_crawler_levelsfyi_returns_early_when_no_roles(self):
        identity_id = str(ObjectId())
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": []}]),
                "companies": FakeCollection(),
                "jobs": FakeCollection(),
            }
        )
        config = _make_config()
        progress_events = []

        result = workflow_module.run_crawler_levelsfyi(
            db,
            config,
            identity_id,
            progress_callback=lambda completed, estimated, message: progress_events.append((completed, estimated, message)),
            identity_database=db,
        )

        self.assertEqual(result.discovered_count, 0)
        self.assertEqual(progress_events, [(0, 1, "Skipping: identity has no configured roles")])

    def test_run_crawler_levelsfyi_returns_early_when_no_jobs(self):
        identity_id = str(ObjectId())
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(),
                "jobs": FakeCollection(),
            }
        )
        config = _make_config()
        progress_events = []

        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = []

        with patch("src.python.web_crawler.crawler_levelsfyi.workflow.LevelsFyiAdapter", return_value=fake_adapter):
            result = workflow_module.run_crawler_levelsfyi(
                db,
                config,
                identity_id,
                progress_callback=lambda completed, estimated, message: progress_events.append((completed, estimated, message)),
                identity_database=db,
            )

        self.assertEqual(result.discovered_count, 0)
        self.assertEqual(progress_events[0], (0, 1, "Fetching job listings from Levels.fyi"))
        self.assertEqual(progress_events[-1], (1, 1, "No jobs discovered from Levels.fyi"))

    def test_run_crawler_levelsfyi_happy_path_insert_and_update(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(docs=[{"_id": company_oid, "canonical_name": "acme"}]),
                "jobs": FakeCollection(),
            }
        )
        config = _make_config(enable_scoring_enqueue=False)
        cards = [
            _make_card(external_id="job-1", title="Software Engineer I"),
            _make_card(external_id="job-1", title="Software Engineer II"),
        ]
        progress_events = []

        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = cards

        with patch("src.python.web_crawler.crawler_levelsfyi.workflow.LevelsFyiAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow.upsert_companies", return_value=(0, 0, [str(company_oid)])), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow._connect_redis") as mock_connect_redis:
            result = workflow_module.run_crawler_levelsfyi(
                db,
                config,
                identity_id,
                progress_callback=lambda completed, estimated, message: progress_events.append((completed, estimated, message)),
                identity_database=db,
            )

        mock_connect_redis.assert_not_called()
        self.assertEqual(result.discovered_count, 2)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.updated_count, 1)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(result.job_ids), 2)
        self.assertEqual(result.new_company_ids, [str(company_oid)])
        self.assertEqual(progress_events[-1][0], 2)

    def test_run_crawler_levelsfyi_fallback_company_resolution(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(docs=[{"_id": company_oid}]),
                "jobs": FakeCollection(),
            }
        )
        config = _make_config()
        card = _make_card(external_id="job-fallback")

        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = [card]

        with patch("src.python.web_crawler.crawler_levelsfyi.workflow.LevelsFyiAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow.upsert_companies", side_effect=[(1, 0, [str(company_oid)]), (1, 0, [str(company_oid)])]):
            result = workflow_module.run_crawler_levelsfyi(db, config, identity_id, identity_database=db)

        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(result.job_ids), 1)

    def test_run_crawler_levelsfyi_skips_unresolvable_company(self):
        identity_id = str(ObjectId())
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(),
                "jobs": FakeCollection(),
            }
        )
        config = _make_config()
        card = _make_card(external_id="job-skip", company="Unknown Co")

        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = [card]

        with patch("src.python.web_crawler.crawler_levelsfyi.workflow.LevelsFyiAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow.upsert_companies", side_effect=[(0, 0, []), (0, 0, [])]):
            result = workflow_module.run_crawler_levelsfyi(db, config, identity_id, identity_database=db)

        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.skipped_count, 1)

    def test_run_crawler_levelsfyi_collects_failed_urls_when_upsert_raises(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(docs=[{"_id": company_oid, "canonical_name": "acme"}]),
                "jobs": FakeCollection(),
            }
        )
        config = _make_config()
        cards = [_make_card(external_id="job-bad"), _make_card(external_id="job-good", source_url="https://example.com/good")]

        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = cards

        with patch("src.python.web_crawler.crawler_levelsfyi.workflow.LevelsFyiAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow.upsert_companies", return_value=(0, 0, [str(company_oid)])), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow._upsert_job", side_effect=[RuntimeError("db down"), ("abc", True)]):
            result = workflow_module.run_crawler_levelsfyi(db, config, identity_id, identity_database=db)

        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(len(result.failed_urls), 1)
        self.assertEqual(result.failed_urls[0]["url"], "https://www.levels.fyi/jobs?jobId=1")

    def test_run_crawler_levelsfyi_skips_jobs_that_do_not_match_identity_roles(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(docs=[{"_id": company_oid, "canonical_name": "acme"}]),
                "jobs": FakeCollection(),
            }
        )
        config = _make_config()
        cards = [_make_card(title="Data Scientist", external_id="job-role-miss")]

        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = cards

        with patch("src.python.web_crawler.crawler_levelsfyi.workflow.LevelsFyiAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow.upsert_companies", return_value=(0, 0, [str(company_oid)])), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow._upsert_job") as mock_upsert:
            result = workflow_module.run_crawler_levelsfyi(db, config, identity_id, identity_database=db)

        mock_upsert.assert_not_called()
        self.assertEqual(result.discovered_count, 1)
        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.skipped_count, 1)

    def test_run_crawler_levelsfyi_accepts_job_when_description_matches_identity_role(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["platform engineer"]}]),
                "companies": FakeCollection(docs=[{"_id": company_oid, "canonical_name": "acme"}]),
                "jobs": FakeCollection(),
            }
        )
        config = _make_config(enable_scoring_enqueue=False)
        cards = [
            _make_card(
                title="Infrastructure Specialist",
                external_id="job-role-description",
                role="platform engineer",
            )
        ]
        cards[0].description = "You will work as a platform engineer across our stack."

        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = cards

        with patch("src.python.web_crawler.crawler_levelsfyi.workflow.LevelsFyiAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow.upsert_companies", return_value=(0, 0, [str(company_oid)])):
            result = workflow_module.run_crawler_levelsfyi(db, config, identity_id, identity_database=db)

        self.assertEqual(result.discovered_count, 1)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(len(db["jobs"].docs), 1)

    def test_run_crawler_levelsfyi_enqueue_success_tracks_queue_handoff(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(docs=[{"_id": company_oid, "canonical_name": "acme"}]),
                "jobs": FakeCollection(),
            }
        )
        config = _make_config(enable_scoring_enqueue=True)

        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = [_make_card(external_id="job-score")]
        fake_redis = Mock()

        with patch("src.python.web_crawler.crawler_levelsfyi.workflow.LevelsFyiAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow.upsert_companies", return_value=(0, 0, [str(company_oid)])), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow._connect_redis", return_value=fake_redis):
            result = workflow_module.run_crawler_levelsfyi(db, config, identity_id, identity_database=db)

        self.assertEqual(result.enqueued_count, 1)
        self.assertEqual(result.enqueue_failed_count, 0)

    def test_run_crawler_levelsfyi_enqueue_failure_tracks_failed_enqueue(self):
        identity_id = str(ObjectId())
        company_oid = ObjectId()
        db = FakeDatabase(
            {
                "identities": FakeCollection(docs=[{"_id": ObjectId(identity_id), "roles": ["software engineer"]}]),
                "companies": FakeCollection(docs=[{"_id": company_oid, "canonical_name": "acme"}]),
                "jobs": FakeCollection(),
            }
        )
        config = _make_config(enable_scoring_enqueue=True)

        fake_adapter = Mock()
        fake_adapter.discover_jobs.return_value = [_make_card(external_id="job-score-fail")]
        fake_redis = Mock()
        fake_redis.rpush.side_effect = RuntimeError("redis down")

        with patch("src.python.web_crawler.crawler_levelsfyi.workflow.LevelsFyiAdapter", return_value=fake_adapter), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow.upsert_companies", return_value=(0, 0, [str(company_oid)])), \
            patch("src.python.web_crawler.crawler_levelsfyi.workflow._connect_redis", return_value=fake_redis):
            result = workflow_module.run_crawler_levelsfyi(db, config, identity_id, identity_database=db)

        self.assertEqual(result.enqueued_count, 0)
        self.assertEqual(result.enqueue_failed_count, 1)


class CrawlerLevelsFyiWorkerTests(unittest.TestCase):
    def test_connect_redis_initializes_client_and_pings(self):
        config = _make_config(redis_host="redis.local", redis_port=6380)
        fake_client = FakeRedis()

        with patch("src.python.web_crawler.crawler_levelsfyi.worker.redis.Redis", return_value=fake_client) as redis_ctor:
            client = worker_module._connect_redis(config)

        self.assertIs(client, fake_client)
        self.assertEqual(fake_client.ping_calls, 1)
        self.assertEqual(redis_ctor.call_args.kwargs["host"], "redis.local")
        self.assertEqual(redis_ctor.call_args.kwargs["port"], 6380)
        self.assertEqual(redis_ctor.call_args.kwargs["decode_responses"], True)

    def test_build_parser_requires_worker_flag(self):
        with self.assertRaises(SystemExit):
            worker_module.build_parser().parse_args([])
        parsed = worker_module.build_parser().parse_args(["--worker"])
        self.assertTrue(parsed.worker)

    def test_worker_main_drops_invalid_dispatch_payload(self):
        config = _make_config()
        fake_redis = FakeRedis(blpop_side_effect=[("queue", "bad payload"), RuntimeError("stop")])

        with patch("src.python.web_crawler.crawler_levelsfyi.worker._connect_redis", return_value=fake_redis), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.parse_workflow_dispatch", side_effect=ValueError("invalid")), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.run_crawler_levelsfyi") as mock_run, \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.time.sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                worker_module.worker_main(config)

        mock_run.assert_not_called()

    def test_worker_main_drops_payload_missing_identity(self):
        config = _make_config()
        fake_redis = FakeRedis(blpop_side_effect=[("queue", "ignored"), RuntimeError("stop")])
        message = common_pb2.WorkflowDispatchMessage(
            run_id="run-1",
            workflow_run_id="wf-1",
            identity_id="",
        )

        with patch("src.python.web_crawler.crawler_levelsfyi.worker._connect_redis", return_value=fake_redis), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.parse_workflow_dispatch", return_value=message), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.run_crawler_levelsfyi") as mock_run, \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.time.sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                worker_module.worker_main(config)

        mock_run.assert_not_called()

    def test_worker_main_success_publishes_running_and_completed(self):
        config = _make_config()
        fake_redis = FakeRedis(blpop_side_effect=[("queue", "payload"), RuntimeError("stop")])
        message = common_pb2.WorkflowDispatchMessage(
            run_id="run-1",
            workflow_run_id="wf-1",
            identity_id=str(ObjectId()),
            user_id="test_user",
        )
        result = WorkflowResult(discovered_count=2, inserted_count=1, updated_count=1, skipped_count=0, new_company_ids=[str(ObjectId())])

        with patch("src.python.web_crawler.crawler_levelsfyi.worker._connect_redis", return_value=fake_redis), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.parse_workflow_dispatch", return_value=message), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.get_database", return_value=FakeDatabase()), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.get_user_database", return_value=FakeDatabase()), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.run_crawler_levelsfyi", return_value=result) as mock_run, \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.increment_discovered_jobs_counter") as mock_increment, \
            patch("src.python.web_crawler.crawler_levelsfyi.worker._emit_enrichment_events") as mock_emit, \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.publish_progress") as mock_publish, \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.time.sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                worker_module.worker_main(config)

        mock_run.assert_called_once()
        mock_increment.assert_called_once_with(
            config,
            workflow_id="crawler_levelsfyi",
            delta=2,
        )
        mock_emit.assert_called_once()
        statuses = [call.kwargs.get("status") for call in mock_publish.mock_calls if hasattr(call, "kwargs")]
        self.assertIn("running", statuses)
        self.assertIn("completed", statuses)

    def test_worker_main_failure_publishes_failed_status(self):
        config = _make_config()
        fake_redis = FakeRedis(blpop_side_effect=[("queue", "payload"), RuntimeError("stop")])
        message = common_pb2.WorkflowDispatchMessage(
            run_id="run-1",
            workflow_run_id="wf-1",
            identity_id=str(ObjectId()),
            user_id="test_user",
        )

        with patch("src.python.web_crawler.crawler_levelsfyi.worker._connect_redis", return_value=fake_redis), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.parse_workflow_dispatch", return_value=message), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.get_database", return_value=FakeDatabase()), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.get_user_database", return_value=FakeDatabase()), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.run_crawler_levelsfyi", side_effect=RuntimeError("boom")), \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.publish_progress") as mock_publish, \
            patch("src.python.web_crawler.crawler_levelsfyi.worker.time.sleep", side_effect=StopIteration):
            with self.assertRaises(StopIteration):
                worker_module.worker_main(config)

        status_calls = [call.kwargs.get("status") for call in mock_publish.mock_calls if hasattr(call, "kwargs")]
        self.assertIn("running", status_calls)
        self.assertIn("failed", status_calls)


if __name__ == "__main__":
    unittest.main()
