from __future__ import annotations

import unittest
from unittest.mock import patch

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.sources.ats_slug_resolver import extract_slug_from_url, resolve_slug, resolve_slug_via_search_dorking


class FakeResponse:
    def __init__(self, url: str, payload: dict | None = None, status_code: int = 200):
        self.url = url
        self._payload = payload or {}
        self.status_code = status_code

    def json(self):
        return self._payload


class AtsSlugResolverTests(unittest.TestCase):
    def setUp(self):
        self.config = CrawlerConfig(
            mongo_host="mongodb://localhost:27017/",
            db_name="cover_letter",
            serper_api_key="test-key",
        )

    def test_extract_slug_from_url_supports_greenhouse_query_format(self):
        slug = extract_slug_from_url("https://boards.greenhouse.io/embed/job_board?for=acme", "greenhouse")
        self.assertEqual(slug, "acme")

    def test_extract_slug_from_url_supports_provider_paths(self):
        self.assertEqual(extract_slug_from_url("https://jobs.lever.co/acme/platform-engineer", "lever"), "acme")
        self.assertEqual(extract_slug_from_url("https://jobs.ashbyhq.com/acme/software-engineer", "ashby"), "acme")

    def test_resolve_slug_via_search_dorking_uses_serper_results(self):
        payload = {"organic": [{"link": "https://jobs.lever.co/acme/platform-engineer"}]}
        with patch(
            "src.python.web_crawler.sources.ats_slug_resolver._request_with_retries",
            return_value=FakeResponse(url=self.config.serper_search_url, payload=payload),
        ), patch(
            "src.python.web_crawler.sources.ats_slug_resolver.validate_slug_via_api",
            return_value=True,
        ):
            slug = resolve_slug_via_search_dorking("Acme", "lever", self.config)

        self.assertEqual(slug, "acme")

    def test_resolve_slug_prefers_board_url_before_search(self):
        with patch(
            "src.python.web_crawler.sources.ats_slug_resolver.validate_slug_via_api",
            return_value=True,
        ), patch(
            "src.python.web_crawler.sources.ats_slug_resolver.resolve_slug_via_search_dorking",
            return_value="should-not-be-used",
        ):
            slug = resolve_slug(
                company_name="Acme",
                provider="lever",
                config=self.config,
                board_url="https://jobs.lever.co/acme/platform-engineer",
            )

        self.assertEqual(slug, "acme")


if __name__ == "__main__":
    unittest.main()
