from __future__ import annotations

from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany
from src.python.web_crawler.sources.base import SourceAdapter


class YCombinatorAdapter(SourceAdapter):
    source_name = "ycombinator"
    base_url = "https://www.ycombinator.com/companies"

    def discover_companies(self, roles: list[str], config: CrawlerConfig) -> list[DiscoveredCompany]:
        companies: list[DiscoveredCompany] = []
        session = requests.Session()
        session.headers.update({"User-Agent": config.user_agent})

        for role in roles:
            url = f"{self.base_url}?query={quote_plus(role)}"
            response = session.get(url, timeout=config.http_timeout_seconds)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            seen_names: set[str] = set()
            for anchor in soup.select("a[href^='/companies/']"):
                name = anchor.get_text(" ", strip=True)
                href = anchor.get("href")
                if not name or not href or name in seen_names:
                    continue
                seen_names.add(name)
                companies.append(
                    DiscoveredCompany(
                        name=name,
                        source=self.source_name,
                        role=role,
                        source_url=f"https://www.ycombinator.com{href}",
                    )
                )

        return companies