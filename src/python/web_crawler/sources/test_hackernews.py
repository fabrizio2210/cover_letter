from __future__ import annotations

import unittest

from src.python.web_crawler.sources.hackernews import HackerNewsAdapter


class HackerNewsAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = HackerNewsAdapter()

    def test_extract_company_name_from_pipe_line(self):
        text = "Acme Corp | Remote | Senior Software Engineer\nWe are hiring"
        self.assertEqual(self.adapter._extract_company_name(text), "Acme Corp")

    def test_select_careers_url_prefers_jobs_path(self):
        urls = [
            "https://example.com/about",
            "https://jobs.lever.co/acme/job-123",
        ]
        self.assertEqual(self.adapter._select_careers_url(urls), "https://jobs.lever.co/acme/job-123")

    def test_comment_to_company_matches_role_and_extracts_domain(self):
        comment = {
            "objectID": "123456",
            "comment_text": (
                "Acme | Remote | Senior Software Engineer"
                "<p>Apply: <a href='https://jobs.lever.co/acme/job-123'>job</a></p>"
            ),
        }

        company = self.adapter._comment_to_company(comment, ["software engineer", "platform engineer"])
        if company is None:
            self.fail("expected company extraction for matching role")
        self.assertEqual(company.name, "Acme")
        self.assertEqual(company.role, "software engineer")
        self.assertEqual(company.careers_url, "https://jobs.lever.co/acme/job-123")
        self.assertEqual(company.domain, "jobs.lever.co")
        self.assertEqual(company.source, "hackernews")

    def test_comment_to_company_rejects_non_matching_roles(self):
        comment = {
            "objectID": "123456",
            "comment_text": "Acme | Remote | Product Manager<p>Apply at acme.com</p>",
        }

        company = self.adapter._comment_to_company(comment, ["software engineer"])
        self.assertIsNone(company)

    def test_is_monthly_who_is_hiring_title_accepts_canonical_titles(self):
        self.assertTrue(self.adapter._is_monthly_who_is_hiring_title("Ask HN: Who is hiring? (April 2026)"))
        self.assertTrue(self.adapter._is_monthly_who_is_hiring_title("Who is hiring? (April 2026)"))

    def test_is_monthly_who_is_hiring_title_rejects_tell_hn_variant(self):
        self.assertFalse(self.adapter._is_monthly_who_is_hiring_title("Tell HN: Who Is Hiring Since 2016, Trend is evolving"))


if __name__ == "__main__":
    unittest.main()
