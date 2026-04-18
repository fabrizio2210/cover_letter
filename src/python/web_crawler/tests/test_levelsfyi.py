from __future__ import annotations

import unittest

from src.python.web_crawler.sources.levelsfyi import LevelsFyiAdapter


class LevelsFyiAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = LevelsFyiAdapter()

    def test_search_url_uses_search_text_query_parameter(self):
        self.assertEqual(
            self.adapter._search_url("site reliability engineer"),
            "https://www.levels.fyi/jobs?searchText=site+reliability+engineer",
        )

    def test_title_jobs_url_uses_role_slug(self):
        self.assertEqual(
            self.adapter._title_jobs_url("site-reliability-engineer"),
            "https://www.levels.fyi/jobs/title/site-reliability-engineer",
        )

    def test_role_slug_normalizes_freeform_role(self):
        self.assertEqual(self.adapter._role_slug("Platform Engineer / Backend"), "platform-engineer-backend")

    def test_extract_company_slug_supports_locale_prefix(self):
        self.assertEqual(
            self.adapter._extract_company_slug("https://www.levels.fyi/en-gb/companies/google/salaries"),
            "google",
        )

    def test_extract_companies_from_html_reads_company_salary_links(self):
        html = """
        <html>
          <body>
            <a href="/en-gb/companies/google/salaries">Google</a>
            <a href="/companies/meta/salaries">Meta</a>
            <a href="/companies/google/salaries">Google</a>
            <a href="/leaderboard">See our leaderboard</a>
          </body>
        </html>
        """

        companies = self.adapter._extract_companies_from_html(
            html,
            "https://www.levels.fyi/t/software-engineer",
            "software engineer",
        )

        self.assertEqual([company.name for company in companies], ["Google", "Meta"])
        self.assertEqual(companies[0].source_url, "https://www.levels.fyi/en-gb/companies/google/salaries")

    def test_extract_companies_from_html_reads_company_cards_on_jobs_pages(self):
        html = """
        <html>
          <body>
            <div role="button">
              <img src="https://img.logo.dev/turbineone.com?token=abc" alt="TurbineOne logo" />
              <h2>TurbineOne</h2>
              <a href="/jobs?jobId=117635543197459142">Software Development Engineer in Test</a>
            </div>
            <div role="button">
              <img src="https://static.levels.fyi/custom/hightouch.webp" alt="Hightouch logo" />
              <h2>Hightouch</h2>
              <a href="/jobs?jobId=91137312968057542">Software Engineer, Streaming Systems</a>
            </div>
            <div role="button">
              <h2>Levels.fyi Jobs</h2>
              <a href="/jobs?jobId=1">Ignore me</a>
            </div>
          </body>
        </html>
        """

        companies = self.adapter._extract_companies_from_html(
            html,
            "https://www.levels.fyi/jobs?searchText=software+engineer",
            "software engineer",
        )

        self.assertEqual([company.name for company in companies], ["TurbineOne", "Hightouch"])
        self.assertEqual(
            [company.source_url for company in companies],
            [
                "https://www.levels.fyi/jobs?jobId=117635543197459142",
                "https://www.levels.fyi/jobs?jobId=91137312968057542",
            ],
        )
        self.assertEqual(companies[0].domain, "turbineone.com")
        self.assertEqual(companies[1].domain, "")

    def test_extract_companies_from_html_ignores_intro_headings_without_job_links(self):
        html = """
        <html>
          <body>
            <h2>Levels.fyi Jobs</h2>
            <p>Introducing the most powerful way to search for a job.</p>
          </body>
        </html>
        """

        companies = self.adapter._extract_companies_from_html(
            html,
            "https://www.levels.fyi/jobs/title/site-reliability-engineer",
            "site reliability engineer",
        )

        self.assertEqual(companies, [])

    def test_extract_companies_from_markdown_strips_compensation_suffix(self):
        markdown = """
        [xAI £459,173](https://www.levels.fyi/en-gb/companies/xai/salaries)
        [Anthropic](https://www.levels.fyi/companies/anthropic/salaries)
        [See all companies](https://www.levels.fyi/companies)
        """

        companies = self.adapter._extract_companies_from_markdown(markdown, "software engineer")

        self.assertEqual([company.name for company in companies], ["xAI", "Anthropic"])
        self.assertEqual(companies[0].source_url, "https://www.levels.fyi/en-gb/companies/xai/salaries")


if __name__ == "__main__":
    unittest.main()