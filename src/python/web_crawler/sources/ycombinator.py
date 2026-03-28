from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote_plus
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany
from src.python.web_crawler.sources.base import SourceAdapter

logger = logging.getLogger(__name__)


class YCombinatorAdapter(SourceAdapter):
    source_name = "ycombinator"
    base_url = "https://www.ycombinator.com/companies"
    algolia_index = "YCCompany_production"

    @staticmethod
    def _extract_algolia_opts(html: str) -> tuple[str, str] | None:
        match = re.search(r"window\.AlgoliaOpts\s*=\s*(\{.*?\});", html, re.DOTALL)
        if not match:
            return None

        try:
            algolia_opts = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.debug("failed to decode window.AlgoliaOpts payload")
            return None

        app = algolia_opts.get("app")
        key = algolia_opts.get("key")
        if not app or not key:
            logger.debug("Algolia options are missing app/key")
            return None
        return app, key

    @staticmethod
    def _extract_domain(website: str) -> str:
        if not website:
            return ""
        parsed = urlparse(website)
        domain = parsed.netloc.casefold()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain

    def _discover_companies_via_algolia(
        self,
        session: requests.Session,
        role: str,
        algolia_app: str,
        algolia_key: str,
        timeout_seconds: int,
    ) -> list[DiscoveredCompany]:
        endpoint = f"https://{algolia_app}-dsn.algolia.net/1/indexes/*/queries"
        headers = {
            "X-Algolia-Application-Id": algolia_app,
            "X-Algolia-API-Key": algolia_key,
            "Content-Type": "application/json",
        }
        payload = {
            "requests": [
                {
                    "indexName": self.algolia_index,
                    "params": f"query={quote_plus(role)}&hitsPerPage=100&page=0",
                }
            ]
        }

        logger.debug("querying Algolia fallback endpoint for role %r", role)
        response = session.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
        body = response.json()
        hits = body.get("results", [{}])[0].get("hits", [])
        logger.debug("Algolia fallback returned %d hits for role %r", len(hits), role)

        companies: list[DiscoveredCompany] = []
        seen_names: set[str] = set()
        for hit in hits:
            name = str(hit.get("name") or "").strip()
            if not name or name in seen_names:
                continue

            seen_names.add(name)
            slug = str(hit.get("slug") or "").strip()
            website = str(hit.get("website") or "").strip()
            one_liner = str(hit.get("one_liner") or "").strip()
            source_url = f"https://www.ycombinator.com/companies/{slug}" if slug else self.base_url

            companies.append(
                DiscoveredCompany(
                    name=name,
                    description=one_liner,
                    source=self.source_name,
                    role=role,
                    source_url=source_url,
                    domain=self._extract_domain(website),
                )
            )

        return companies

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

            if not anchors:
                logger.debug("no server-rendered company anchors found, trying Algolia fallback")
                algolia_opts = self._extract_algolia_opts(response.text)
                if not algolia_opts:
                    logger.debug("Algolia fallback unavailable for role %r", role)
                    continue

                fallback_companies = self._discover_companies_via_algolia(
                    session=session,
                    role=role,
                    algolia_app=algolia_opts[0],
                    algolia_key=algolia_opts[1],
                    timeout_seconds=config.http_timeout_seconds,
                )
                companies.extend(fallback_companies)
                continue

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