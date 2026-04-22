from __future__ import annotations

import unittest
from unittest.mock import Mock

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.sources.ats_job_fetcher import _fetch_ashby_jobs, _fetch_greenhouse_jobs, _fetch_lever_jobs


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


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

        jobs = _fetch_greenhouse_jobs("missing", self.config, session)
        self.assertEqual(jobs, [])

    def test_fetch_greenhouse_jobs_skips_entries_missing_id_or_title(self):
        payload = {"jobs": [{"title": "No ID"}, {"id": 456, "title": ""}, {"id": 789, "title": "Valid"}]}
        session = Mock()
        session.request.return_value = FakeResponse(payload)

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

        jobs = _fetch_ashby_jobs("acme", self.config, session)
        self.assertEqual(jobs, [])


if __name__ == "__main__":
    unittest.main()
