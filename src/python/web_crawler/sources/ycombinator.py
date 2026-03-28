from __future__ import annotations

import logging
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany
from src.python.web_crawler.sources.base import SourceAdapter

logger = logging.getLogger(__name__)


class YCombinatorAdapter(SourceAdapter):
    source_name = "ycombinator"
    base_url = "https://www.ycombinator.com/companies"

    def discover_companies(self, roles: list[str], config: CrawlerConfig) -> list[DiscoveredCompany]:
        companies: list[DiscoveredCompany] = []
        session = requests.Session()
        session.headers.update({"User-Agent": config.user_agent})

        for role in roles:
            url = f"{self.base_url}?query={quote_plus(role)}"
            logger.debug("fetching YC URL: %s", url)
            response = session.get(url, timeout=config.http_timeout_seconds)
            logger.debug("HTTP %d for %s (content-length: %s)", response.status_code, url, len(response.content))
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            anchors = soup.select("a[href^='/companies/']")
            logger.debug("found %d raw anchors for role %r", len(anchors), role)
            seen_names: set[str] = set()
            for anchor in anchors:
                name = anchor.get_text(" ", strip=True)
                href = anchor.get("href")
                if not name or not href or name in seen_names:
                    logger.debug("skipping anchor name=%r href=%r (duplicate=%s)", name, href, name in seen_names)
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

        logger.debug("YCombinator total companies found: %d", len(companies))
        return companies