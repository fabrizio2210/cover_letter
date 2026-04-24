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

    def find_one(self, filter_doc, projection=None):
        for doc in self.docs:
            if doc.get("_id") == filter_doc.get("_id"):
                return dict(doc)
        return None

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
    # Input validation
    # ------------------------------------------------------------------

    def test_returns_failed_count_for_invalid_job_id(self):
        database = FakeDatabase({"jobs": FakeCollection()})
        result = run_enrichment_retiring_jobs(database, self.config, "not-a-valid-objectid")
        self.assertEqual(result.failed_count, 1)

    def test_returns_skipped_count_when_job_not_found(self):
        jobs = FakeCollection([])
        database = FakeDatabase({"jobs": jobs})
        result = run_enrichment_retiring_jobs(database, self.config, str(ObjectId()))
        self.assertEqual(result.skipped_count, 1)

    # ------------------------------------------------------------------
    # Phase A — Mark closed
    # ------------------------------------------------------------------

    def test_marks_job_closed_when_source_url_returns_404(self):
        job = self._make_job()
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.head.return_value = FakeResponse(404)
            mock_session_cls.return_value = mock_session

            result = run_enrichment_retiring_jobs(database, self.config, str(job["_id"]))

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

            result = run_enrichment_retiring_jobs(database, self.config, str(job["_id"]))

        self.assertEqual(result.updated_count, 0)
        self.assertNotIn("is_open", jobs.docs[0])

    def test_skips_http_probe_for_already_closed_job(self):
        job = self._make_job(is_open=False, closed_at={"seconds": int(time.time()), "nanos": 0})
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session

            result = run_enrichment_retiring_jobs(database, self.config, str(job["_id"]))

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

            result = run_enrichment_retiring_jobs(database, self.config, str(job["_id"]))

        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertNotIn("is_open", jobs.docs[0])

    def test_network_error_skips_phase_b(self):
        """A network error in Phase A must not trigger Phase B deletion."""
        cutoff = int(time.time()) - (_CLOSED_AFTER_DAYS * 24 * 3600) - 1
        job = self._make_job(is_open=False, closed_at={"seconds": cutoff, "nanos": 0})
        # Overwrite is_open to simulate an open job that fails network check
        job["is_open"] = None  # not False, so Phase A runs
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.head.side_effect = OSError("timeout")
            mock_session_cls.return_value = mock_session

            result = run_enrichment_retiring_jobs(database, self.config, str(job["_id"]))

        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.deleted_count, 0)
        self.assertEqual(len(jobs.docs), 1)

    # ------------------------------------------------------------------
    # Phase B — Remove expired
    # ------------------------------------------------------------------

    def test_deletes_job_closed_for_more_than_60_days(self):
        cutoff = int(time.time()) - (_CLOSED_AFTER_DAYS * 24 * 3600) - 1
        job = self._make_job(is_open=False, closed_at={"seconds": cutoff, "nanos": 0})
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        result = run_enrichment_retiring_jobs(database, self.config, str(job["_id"]))

        self.assertEqual(result.deleted_count, 1)
        self.assertEqual(len(jobs.docs), 0)

    def test_does_not_delete_job_closed_recently(self):
        recently = int(time.time()) - 3600  # 1 hour ago
        job = self._make_job(is_open=False, closed_at={"seconds": recently, "nanos": 0})
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        result = run_enrichment_retiring_jobs(database, self.config, str(job["_id"]))

        self.assertEqual(result.deleted_count, 0)
        self.assertEqual(len(jobs.docs), 1)

    def test_marks_job_closed_then_does_not_immediately_delete_it(self):
        """A job just marked 404-closed should NOT be deleted in the same run."""
        job = self._make_job()
        jobs = FakeCollection([job])
        database = FakeDatabase({"jobs": jobs})

        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.head.return_value = FakeResponse(404)
            mock_session_cls.return_value = mock_session

            result = run_enrichment_retiring_jobs(database, self.config, str(job["_id"]))

        self.assertEqual(result.updated_count, 1)
        self.assertEqual(result.deleted_count, 0)
        self.assertEqual(len(jobs.docs), 1)


if __name__ == "__main__":
    unittest.main()
