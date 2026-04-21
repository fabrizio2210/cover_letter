from __future__ import annotations

import unittest

from src.python.web_crawler.sources.levelsfyi import LevelsFyiAdapter


class LevelsFyiAdapterExtractionTests(unittest.TestCase):
    def test_extract_job_cards_from_grouped_company_headings(self):
        adapter = LevelsFyiAdapter()
        html = """
        <html>
          <body>
            <section>
              <h2>Fivetran</h2>
              <div role="button">
                <a href="/jobs?jobId=105486970502685382">Senior Site Reliability Engineer</a>
                <img src="https://img.logo.dev/fivetran.com?token=abc" />
              </div>
              <div role="button">
                <a href="/jobs?jobId=110402886990471878">Platform Engineer</a>
              </div>
            </section>
          </body>
        </html>
        """

        cards = adapter._extract_job_cards_from_html(
            html,
            "https://www.levels.fyi/jobs?searchText=site+reliability+engineer",
            "site reliability engineer",
        )

        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0].external_job_id, "105486970502685382")
        self.assertEqual(cards[0].job_title, "Senior Site Reliability Engineer")
        self.assertEqual(cards[0].company_name, "Fivetran")
        self.assertEqual(cards[0].domain, "fivetran.com")

        self.assertEqual(cards[1].external_job_id, "110402886990471878")
        self.assertEqual(cards[1].job_title, "Platform Engineer")
        self.assertEqual(cards[1].company_name, "Fivetran")

    def test_extract_job_cards_from_json_script_payload(self):
        adapter = LevelsFyiAdapter()
        html = """
        <html>
          <body>
            <script id="__NEXT_DATA__" type="application/json">
            {
              "props": {
                "pageProps": {
                  "jobs": [
                    {
                      "jobId": "76365638389375686",
                      "title": "Site Reliability Engineer",
                      "companyName": "Dropbox",
                      "url": "/jobs?jobId=76365638389375686"
                    }
                  ],
                  "other": {
                    "cards": [
                      {
                        "job": {
                          "id": "119257254128952006",
                          "title": "Site Reliability Engineer II"
                        },
                        "company": {
                          "name": "Dataiku"
                        },
                        "href": "/jobs?jobId=119257254128952006"
                      }
                    ]
                  }
                }
              }
            }
            </script>
          </body>
        </html>
        """

        cards = adapter._extract_job_cards_from_html(
            html,
            "https://www.levels.fyi/jobs?searchText=site+reliability+engineer",
            "site reliability engineer",
        )

        by_id = {card.external_job_id: card for card in cards}
        self.assertEqual(len(by_id), 2)
        self.assertEqual(by_id["76365638389375686"].job_title, "Site Reliability Engineer")
        self.assertEqual(by_id["76365638389375686"].company_name, "Dropbox")
        self.assertEqual(by_id["119257254128952006"].job_title, "Site Reliability Engineer II")
        self.assertEqual(by_id["119257254128952006"].company_name, "Dataiku")


if __name__ == "__main__":
    unittest.main()
