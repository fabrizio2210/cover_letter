from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.enrichment_retiring_jobs.workflow import (
    _CLOSED_AFTER_DAYS,
    run_enrichment_retiring_jobs,
)


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, filter_doc=None):
        if not filter_doc:
            return list(self.docs)

        results = []
        for doc in self.docs:
            if self._matches(doc, filter_doc):
                results.append(doc)
        return results

    def _matches(self, doc: dict, filter_doc: dict) -> bool:
        for key, condition in filter_doc.items():
            value = self._get_nested(doc, key)
            if isinstance(condition, dict):
                for op, operand in condition.items():
                    if op == "$exists":
                        if operand and value is None:
                            return False
                        if not operand and value is not None:
                            return False
                    elif op == "$ne":
                        if value == operand:
                            return False
                    elif op == "$lt":
                        if value is None or value >= operand:
                            return False
                    else:
                        return False
            else:
                if value != condition:
                    return False
        return True

    @staticmethod
    def _get_nested(doc: dict, dotted_key: str):
        parts = dotted_key.split(".")
        current = doc
        for part in parts:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def update_one(self, filter_doc, update_doc):
        for doc in self.docs:
            if doc.get("_id") == filter_doc.get("_id"):
                for key, value in update_doc.get("$set", {}).items():
                    parts = key.split(".")
                    current = doc
                    for part in parts[:-1]:
                        current = current.setdefault(part, {})
                    current[parts[-1]] = value
                return
        raise AssertionError(f"document not found for update: {filter_doc}")

    def delete_one(self, filter_doc):
        for i, doc in enumerate(self.docs):
            if doc.get("_id") == filter_doc.get("_id"):
                del self.docs[i]
                return

    def delete_many(self, filter_doc):
        to_remove = [doc for doc in self.docs if self._matches(doc, filter_doc)]
        for doc in to_remove:
            self.docs.remove(doc)

        class _Result:
            deleted_count = len(to_remove)

        return _Result()


class FakeDatabase(dict):
    pass


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class EnrichmentRetiringJobsTests(unittest.TestCase):
    def setUp(self):
        self.config = CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="cover_letter")

    def _make_job(self, source_url: str = "https://example.com/jobs/123", is_open=None, closed_at=None) -> dict:
        doc: dict = {"_id": ObjectId(), "source_url": source_url}
        if is_open is not None:
            doc["is_open"] = is_open
        if closed_at is not None:
            doc["closed_at"] = closed_at
        return doc

    # ------------------------------------------------------------------
    # Phase A tests
    # ------------------------------------------------------------------

    def test_marks_job_closed_when_source_url_returns_404(self):
        job = self._make_job()
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.head.return_value = FakeResponse(404)
            mock_session_cls.return_value = mock_session

            result = run_enrichment_retiring_jobs(database, self.config)

        self.assertEqual(result.updated_count, 1)
        self.assertFalse(jobs.docs[0]["is_open"])
        self.assertIn("seconds", jobs.docs[0]["closed_at"])

    def test_does_not_mark_job_closed_when_source_url_returns_200(self):
        job = self._make_job()
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.head.return_value = FakeResponse(200)
            mock_session_cls.return_value = mock_session

            result = run_enrichment_retiring_jobs(database, self.config)

        self.assertEqual(result.updated_count, 0)
        self.assertNotIn("is_open", jobs.docs[0])

    def test_skips_already_closed_jobs_in_phase_a(self):
        job = self._make_job(is_open=False, closed_at={"seconds": int(time.time()), "nanos": 0})
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session

            result = run_enrichment_retiring_jobs(database, self.config)

        mock_session.head.assert_not_called()
        self.assertEqual(result.updated_count, 0)

    def test_increments_failed_count_on_network_error(self):
        job = self._make_job()
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.head.side_effect = OSError("connection refused")
            mock_session_cls.return_value = mock_session

            result = run_enrichment_retiring_jobs(database, self.config)

        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertNotIn("is_open", jobs.docs[0])

    # ------------------------------------------------------------------
    # Phase B tests
    # ------------------------------------------------------------------

    def test_deletes_job_closed_for_more_than_60_days(self):
        cutoff = int(time.time()) - (_CLOSED_AFTER_DAYS * 24 * 3600) - 1
        job = self._make_job(is_open=False, closed_at={"seconds": cutoff, "nanos": 0})
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()

            result = run_enrichment_retiring_jobs(database, self.config)

        self.assertEqual(result.deleted_count, 1)
        self.assertEqual(len(jobs.docs), 0)

    def test_does_not_delete_job_closed_recently(self):
        recently = int(time.time()) - 3600  # 1 hour ago
        job = self._make_job(is_open=False, closed_at={"seconds": recently, "nanos": 0})
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()

            result = run_enrichment_retiring_jobs(database, self.config)

        self.assertEqual(result.deleted_count, 0)
        self.assertEqual(len(jobs.docs), 1)

    # ------------------------------------------------------------------
    # Combined phase tests
    # ------------------------------------------------------------------

    def test_marks_404_job_closed_and_deletes_expired_job_in_same_run(self):
        cutoff = int(time.time()) - (_CLOSED_AFTER_DAYS * 24 * 3600) - 1
        open_job = self._make_job("https://example.com/open/1")
        expired_job = self._make_job(
            "https://example.com/expired/2",
            is_open=False,
            closed_at={"seconds": cutoff, "nanos": 0},
        )
        jobs = FakeCollection([open_job, expired_job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.head.return_value = FakeResponse(404)
            mock_session_cls.return_value = mock_session

            result = run_enrichment_retiring_jobs(database, self.config)

        self.assertEqual(result.updated_count, 1)
        self.assertEqual(result.deleted_count, 1)
        # The open job is now closed
        self.assertFalse(jobs.docs[0]["is_open"])
        # The expired job has been deleted
        self.assertEqual(len(jobs.docs), 1)

    def test_progress_callback_receives_monotonically_increasing_values(self):
        jobs_docs = [self._make_job(f"https://example.com/job/{i}") for i in range(3)]
        jobs = FakeCollection(jobs_docs)
        database = FakeDatabase({"jobs": jobs})

        progress_events: list[tuple[int, int, str]] = []

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.head.return_value = FakeResponse(200)
            mock_session_cls.return_value = mock_session

            run_enrichment_retiring_jobs(
                database,
                self.config,
                progress_callback=lambda completed, estimated, msg: progress_events.append(
                    (completed, estimated, msg)
                ),
            )

        self.assertTrue(progress_events)
        completed_values = [c for c, _, _ in progress_events]
        self.assertEqual(completed_values, sorted(completed_values))


if __name__ == "__main__":
    unittest.main()
