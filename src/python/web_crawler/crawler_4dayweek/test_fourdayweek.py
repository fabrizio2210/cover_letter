from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.crawler_4dayweek.fourdayweek import FourDayWeekAdapter, derive_external_job_id


def _make_config(**kwargs) -> CrawlerConfig:
    return CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="test", **kwargs)


class FourDayWeekSourceTests(unittest.TestCase):
    def test_derive_external_job_id_prefers_slug_suffix(self):
        self.assertEqual(
            derive_external_job_id("https://4dayweek.io/job/software-engineer-at-acme-81d43928"),
            "81d43928",
        )

    def test_derive_external_job_id_hashes_unmatched_paths(self):
        first = derive_external_job_id("https://4dayweek.io/jobs/custom/path")
        second = derive_external_job_id("https://4dayweek.io/jobs/custom/path")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)

    def test_discover_jobs_uses_api_pagination(self):
        config = _make_config()
        adapter = FourDayWeekAdapter()
        fake_session = Mock()
        fake_session.get.side_effect = [
            Mock(
                status_code=200,
                json=Mock(
                    return_value={
                        "data": [
                            {
                                "url": "https://4dayweek.io/job/software-engineer-at-acme-81d43928",
                                "title": "Software Engineer",
                                "description": "Build systems",
                                "role": "Software Engineer",
                                "is_remote": True,
                                "remote_allowed": [{"country": "Italy"}],
                                "company": {"name": "Acme", "website": "https://acme.test"},
                            }
                        ],
                        "has_more": True,
                    }
                ),
                headers={},
                raise_for_status=Mock(),
            ),
            Mock(
                status_code=200,
                json=Mock(return_value={"data": [], "has_more": False}),
                headers={},
                raise_for_status=Mock(),
            ),
        ]

        with patch.object(adapter, "_build_session", return_value=fake_session):
            cards = adapter.discover_jobs(config)

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].external_job_id, "81d43928")
        self.assertEqual(cards[0].location, "Remote (Italy)")
        fake_session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
