from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.sources.ats_detector import ATSRequestFailure, detect_ats_provider, extract_ats_signatures_from_html, fetch_url


class FakeResponse:
    def __init__(self, url: str, text: str = "", status_code: int = 200):
        self.url = url
        self.text = text
        self.status_code = status_code


class AtsDetectorTests(unittest.TestCase):
    def setUp(self):
        self.config = CrawlerConfig(mongo_host="mongodb://localhost:27017/", db_name="cover_letter")

    def test_extract_ats_signatures_from_html_finds_all_supported_providers(self):
        html = """
        <a href=\"https://boards.greenhouse.io/acme\">Greenhouse</a>
        <div class=\"lever-job\"></div>
        <script>window.Ashby = {};</script>
        """

        providers = extract_ats_signatures_from_html(html)

        self.assertSetEqual(providers, {"greenhouse", "lever", "ashby"})

    def test_detect_ats_provider_prefers_hosted_board_links(self):
        html = '<a href="https://jobs.lever.co/acme">Jobs</a>'
        with patch(
            "src.python.web_crawler.sources.ats_detector.fetch_url",
            return_value=FakeResponse(url="https://acme.test/careers", text=html),
        ):
            result = detect_ats_provider(["https://acme.test/careers"], self.config)

        self.assertIsNotNone(result)
        self.assertEqual(result.provider, "lever")
        self.assertEqual(result.board_url, "https://jobs.lever.co/acme")

    def test_detect_ats_provider_uses_precedence_for_ambiguous_signatures(self):
        html = "boards.greenhouse.io and jobs.lever.co"
        with patch(
            "src.python.web_crawler.sources.ats_detector.fetch_url",
            return_value=FakeResponse(url="https://acme.test/jobs", text=html),
        ):
            result = detect_ats_provider(["https://acme.test/jobs"], self.config)

        self.assertIsNotNone(result)
        self.assertEqual(result.provider, "greenhouse")
        self.assertIsNone(result.board_url)

    def test_fetch_url_fails_fast_on_dns_resolution_error(self):
        session = Mock(spec=requests.Session)
        session.get.side_effect = requests.RequestException("Failed to resolve host")

        with patch("src.python.web_crawler.sources.ats_detector._is_dns_resolution_error", return_value=True), patch(
            "src.python.web_crawler.sources.ats_detector._is_timeout_error",
            return_value=False,
        ):
            with self.assertRaises(ATSRequestFailure) as ctx:
                fetch_url(session, "https://invalid.test", self.config)

        self.assertEqual(ctx.exception.failure_type, "dns_resolution")
        self.assertEqual(session.get.call_count, 1)

    def test_fetch_url_fails_fast_on_timeout_error(self):
        session = Mock(spec=requests.Session)
        session.get.side_effect = requests.Timeout("timed out")

        with patch("src.python.web_crawler.sources.ats_detector._is_dns_resolution_error", return_value=False), patch(
            "src.python.web_crawler.sources.ats_detector._is_timeout_error",
            return_value=True,
        ):
            with self.assertRaises(ATSRequestFailure) as ctx:
                fetch_url(session, "https://slow.test", self.config)

        self.assertEqual(ctx.exception.failure_type, "timeout")
        self.assertEqual(session.get.call_count, 1)

    def test_fetch_url_raises_host_unavailable_after_repeated_503(self):
        session = Mock(spec=requests.Session)
        session.get.return_value = FakeResponse(url="https://acme.test", status_code=503)

        with self.assertRaises(ATSRequestFailure) as ctx:
            fetch_url(session, "https://acme.test", self.config)

        self.assertEqual(ctx.exception.failure_type, "host_unavailable")
        self.assertEqual(session.get.call_count, self.config.max_retries)

    def test_detect_ats_provider_skips_remaining_paths_after_root_503(self):
        with patch(
            "src.python.web_crawler.sources.ats_detector.fetch_url",
            side_effect=[
                ATSRequestFailure("host_unavailable", "https://acme.test", "status 503"),
            ],
        ) as fetch_mock:
            result = detect_ats_provider(
                [
                    "https://acme.test",
                    "https://acme.test/careers",
                    "https://acme.test/jobs",
                ],
                self.config,
            )

        self.assertIsNone(result)
        self.assertEqual(fetch_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
