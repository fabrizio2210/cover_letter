from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.crawler_4dayweek.fourdayweek import (
    FourDayWeekAdapter,
    _normalize_location,
    _parse_job_detail_html,
    derive_external_job_id,
)


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
                                "work_arrangement": "remote",
                                "locations": [
                                    {
                                        "country": "Italy",
                                        "work_arrangement": "remote",
                                        "is_primary": True,
                                    }
                                ],
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

    def test_normalize_location_uses_current_api_location_restriction(self):
        item = {
            "work_arrangement": "remote",
            "locations": [
                {
                    "country": "United States",
                    "continent": "North America",
                    "work_arrangement": "remote",
                    "is_primary": True,
                }
            ],
        }

        self.assertEqual(_normalize_location(item), "Remote (United States)")

    def test_normalize_location_orders_primary_and_deduplicates_remote_countries(self):
        item = {
            "work_arrangement": "remote",
            "locations": [
                {"country": "Canada", "is_primary": "false"},
                {"country": "United States", "is_primary": True},
                {"country": "united states"},
            ],
        }

        self.assertEqual(_normalize_location(item), "Remote (United States, Canada)")

    def test_normalize_location_uses_continent_only_as_a_remote_restriction(self):
        cases = [
            (
                {"work_arrangement": "remote", "locations": [{"continent": "Europe"}]},
                "Remote (Europe)",
            ),
            (
                {
                    "work_arrangement": "remote",
                    "locations": [
                        {"country": "United States", "continent": "North America"}
                    ],
                },
                "Remote (United States)",
            ),
        ]

        for item, expected in cases:
            with self.subTest(item=item):
                self.assertEqual(_normalize_location(item), expected)

    def test_normalize_location_keeps_unrestricted_remote_generic(self):
        self.assertEqual(_normalize_location({"work_arrangement": "remote", "locations": []}), "Remote")

    def test_normalize_location_formats_current_api_office_locations(self):
        item = {
            "work_arrangement": "hybrid",
            "locations": [
                {"city": "Toronto", "country": "Canada"},
                {
                    "city": "Austin",
                    "state": "Texas",
                    "country": "United States",
                    "is_primary": True,
                },
            ],
        }

        self.assertEqual(
            _normalize_location(item),
            "Austin, Texas, United States; Toronto, Canada",
        )

    def test_normalize_location_supports_legacy_api_fields(self):
        item = {
            "is_remote": True,
            "remote_allowed": [{"country": "Italy"}],
        }

        self.assertEqual(_normalize_location(item), "Remote (Italy)")

    def test_normalize_location_does_not_treat_malformed_is_remote_as_true(self):
        for malformed_value in ("false", "true", 1, {}):
            with self.subTest(malformed_value=malformed_value):
                item = {
                    "is_remote": malformed_value,
                    "work_arrangement": "hybrid",
                    "office_locations": [
                        {"city": "Milan", "country": "Italy"}
                    ],
                }

                self.assertEqual(_normalize_location(item), "Milan, Italy")

    def test_normalize_location_preserves_legacy_remote_office_precision(self):
        cases = [
            (
                {
                    "is_remote": True,
                    "office_locations": [
                        {"city": "Berlin", "country": "Germany"}
                    ],
                },
                "Remote (Berlin, Germany)",
            ),
            (
                {
                    "is_remote": True,
                    "office_locations": [
                        {"city": "Berlin", "country": "Germany"},
                        {
                            "city": "Paris",
                            "region": "Île-de-France",
                            "country": "France",
                        },
                    ],
                },
                "Remote (Berlin, Germany; Paris, Île-de-France, France)",
            ),
        ]

        for item, expected in cases:
            with self.subTest(item=item):
                self.assertEqual(_normalize_location(item), expected)

    def test_normalize_location_falls_back_when_current_locations_are_unusable(self):
        item = {
            "work_arrangement": "remote",
            "locations": [{}, {"is_primary": True}],
            "remote_allowed": [{"country": "Italy"}],
        }

        self.assertEqual(_normalize_location(item), "Remote (Italy)")

    def test_normalize_location_ignores_non_string_current_components(self):
        for malformed_value in ({}, [], False, 42):
            with self.subTest(malformed_value=malformed_value):
                item = {
                    "work_arrangement": "remote",
                    "locations": [{"country": malformed_value}],
                    "remote_allowed": [{"country": "Italy"}],
                }

                self.assertEqual(_normalize_location(item), "Remote (Italy)")

    def test_normalize_location_uses_valid_region_when_state_is_malformed(self):
        item = {
            "work_arrangement": "remote",
            "locations": [{"state": 42, "region": "California"}],
        }

        self.assertEqual(_normalize_location(item), "Remote (California)")

    def test_parse_job_detail_html_prefers_job_posting_json_ld(self):
        job_posting = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Graphic Design Expert",
            "description": "<p>Build <strong>design systems</strong>.</p>",
            "hiringOrganization": {
                "@type": "Organization",
                "name": "HumanSignal",
                "sameAs": "https://www.humansignal.com/about",
            },
            "jobLocationType": "TELECOMMUTE",
            "applicantLocationRequirements": [
                {"@type": "Country", "name": "United States"}
            ],
        }
        html_text = (
            "<html><head>"
            f'<script type="application/ld+json">{json.dumps({"@graph": [job_posting]})}</script>'
            "</head><body><h1>Wrong fallback title</h1></body></html>"
        )

        card = _parse_job_detail_html(
            "https://4dayweek.io/job/graphic-design-expert-at-humansignal-e5620d3a",
            html_text,
        )

        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.job_title, "Graphic Design Expert")
        self.assertEqual(card.company_name, "HumanSignal")
        self.assertEqual(card.description, "Build design systems .")
        self.assertEqual(card.location, "Remote (United States)")
        self.assertEqual(card.company_domain, "humansignal.com")

    def test_parse_job_detail_html_does_not_treat_remote_office_as_a_restriction(self):
        job_posting = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Software Engineer",
            "description": "Build systems.",
            "hiringOrganization": {"name": "Acme"},
            "jobLocationType": "TELECOMMUTE",
            "jobLocation": {
                "@type": "Place",
                "address": {
                    "@type": "PostalAddress",
                    "addressLocality": "Austin",
                    "addressRegion": "Texas",
                    "addressCountry": "United States",
                },
            },
        }
        html_text = (
            '<script type="application/ld+json">'
            f"{json.dumps(job_posting)}"
            "</script>"
        )

        card = _parse_job_detail_html(
            "https://4dayweek.io/job/software-engineer-at-acme-81d43928",
            html_text,
        )

        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.location, "Remote")

    def test_parse_job_detail_html_accepts_expanded_job_posting_type(self):
        job_posting = {
            "@context": "https://schema.org",
            "@type": "https://schema.org/JobPosting",
            "title": "Software Engineer",
            "description": "Build systems.",
            "hiringOrganization": {"name": "Acme"},
            "jobLocationType": "TELECOMMUTE",
            "applicantLocationRequirements": {
                "@type": "Country",
                "name": "Germany",
            },
        }
        html_text = (
            '<script type="application/ld+json">'
            f"{json.dumps(job_posting)}"
            "</script>"
        )

        card = _parse_job_detail_html(
            "https://4dayweek.io/job/software-engineer-at-acme-81d43928",
            html_text,
        )

        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.location, "Remote (Germany)")

    def test_parse_job_detail_html_accepts_compact_schema_org_type(self):
        job_posting = {
            "@context": {"schema": "https://schema.org/"},
            "@type": "schema:JobPosting",
            "title": "Software Engineer",
            "description": "Build systems.",
            "hiringOrganization": {"name": "Acme"},
            "jobLocationType": "TELECOMMUTE",
            "applicantLocationRequirements": {
                "@type": "schema:Country",
                "name": "Germany",
            },
        }
        html_text = (
            '<script type="application/ld+json">'
            f"{json.dumps(job_posting)}"
            "</script>"
        )

        card = _parse_job_detail_html(
            "https://4dayweek.io/job/software-engineer-at-acme-81d43928",
            html_text,
        )

        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.location, "Remote (Germany)")

    def test_parse_job_detail_html_rejects_unknown_job_posting_namespace(self):
        unknown_posting = {
            "@type": "https://example.com/JobPosting",
            "title": "Wrong Job",
            "description": "Wrong description.",
            "hiringOrganization": {"name": "Wrong Company"},
        }
        schema_posting = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Software Engineer",
            "description": "Build systems.",
            "hiringOrganization": {"name": "Acme"},
            "jobLocationType": "TELECOMMUTE",
            "applicantLocationRequirements": {
                "@type": "Country",
                "name": "Germany",
            },
        }
        html_text = (
            '<script type="application/ld+json">'
            f"{json.dumps(unknown_posting)}"
            "</script>"
            '<script type="application/ld+json">'
            f"{json.dumps(schema_posting)}"
            "</script>"
        )

        card = _parse_job_detail_html(
            "https://4dayweek.io/job/software-engineer-at-acme-81d43928",
            html_text,
        )

        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.job_title, "Software Engineer")
        self.assertEqual(card.company_name, "Acme")
        self.assertEqual(card.location, "Remote (Germany)")

    def test_parse_job_detail_html_uses_dom_when_json_ld_is_malformed(self):
        html_text = """
            <html>
              <head><script type="application/ld+json">{not-json}</script></head>
              <body>
                <h1>Software Engineer</h1>
                <a href="/company/acme">Acme</a>
                <p>This is a Remote role.</p>
              </body>
            </html>
        """

        card = _parse_job_detail_html(
            "https://4dayweek.io/job/software-engineer-at-acme-81d43928",
            html_text,
        )

        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.job_title, "Software Engineer")
        self.assertEqual(card.company_name, "Acme")
        self.assertEqual(card.location, "Remote")
        self.assertNotIn("not-json", card.description)


if __name__ == "__main__":
    unittest.main()
