from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.workflow_counters import (
    WORKFLOW_COUNTERS_DOC_ID,
    WORKFLOW_COUNTERS_FIELD,
    increment_discovered_jobs_counter,
)


def _make_config() -> CrawlerConfig:
    return CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="cover_letter")


class WorkflowCountersTests(unittest.TestCase):
    def test_increment_discovered_jobs_counter_updates_stats_document(self):
        config = _make_config()
        fake_collection = Mock()
        fake_database = {"stats": fake_collection}

        with patch("src.python.web_crawler.workflow_counters.get_database", return_value=fake_database):
            increment_discovered_jobs_counter(
                config,
                workflow_id="crawler_levelsfyi",
                delta=3,
            )

        fake_collection.update_one.assert_called_once_with(
            {"_id": WORKFLOW_COUNTERS_DOC_ID},
            {"$inc": {f"{WORKFLOW_COUNTERS_FIELD}.crawler_levelsfyi": 3}},
            upsert=True,
        )

    def test_increment_discovered_jobs_counter_skips_non_positive_delta(self):
        config = _make_config()
        fake_collection = Mock()
        fake_database = {"stats": fake_collection}

        with patch("src.python.web_crawler.workflow_counters.get_database", return_value=fake_database):
            increment_discovered_jobs_counter(config, workflow_id="crawler_4dayweek", delta=0)
            increment_discovered_jobs_counter(config, workflow_id="crawler_4dayweek", delta=-1)

        fake_collection.update_one.assert_not_called()


if __name__ == "__main__":
    unittest.main()
