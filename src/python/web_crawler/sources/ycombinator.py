from __future__ import annotations

import json
import logging
import re
import time
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
        hits_per_page: int,
        max_companies: int,
        request_delay_seconds: float,
    ) -> list[DiscoveredCompany]:
        endpoint = f"https://{algolia_app}-dsn.algolia.net/1/indexes/*/queries"
        headers = {
            "X-Algolia-Application-Id": algolia_app,
            "X-Algolia-API-Key": algolia_key,
            "Content-Type": "application/json",
        }

        companies: list[DiscoveredCompany] = []
        seen_names: set[str] = set()
        page = 0
        total_pages: int | None = None
        while len(companies) < max_companies:
            payload = {
                "requests": [
                    {
                        "indexName": self.algolia_index,
                        "params": f"query={quote_plus(role)}&hitsPerPage={hits_per_page}&page={page}",
                    }
                ]
            }
            logger.debug(
                "querying Algolia fallback endpoint for role %r page=%d hits_per_page=%d",
                role,
                page,
                hits_per_page,
            )
            response = session.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
            response.raise_for_status()
            body = response.json()
            result = body.get("results", [{}])[0]
            hits = result.get("hits", [])
            total_pages = int(result.get("nbPages") or 0)
            logger.debug(
                "Algolia fallback returned %d hits for role %r page=%d/%d",
                len(hits),
                role,
                page,
                max(total_pages - 1, 0),
            )
            if not hits:
                break

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
                if len(companies) >= max_companies:
                    logger.debug("reached configured max companies (%d)", max_companies)
                    break

            page += 1
            if total_pages is not None and page >= total_pages:
                break
            if len(companies) >= max_companies:
                break

            if request_delay_seconds > 0:
                logger.debug("sleeping %.2fs before next Algolia page", request_delay_seconds)
                time.sleep(request_delay_seconds)

        logger.debug("Algolia fallback collected %d unique companies for role %r", len(companies), role)

        return companies

    def discover_companies(self, roles: list[str], config: CrawlerConfig) -> list[DiscoveredCompany]:
        companies: list[DiscoveredCompany] = []
        session = requests.Session()
        session.headers.update({"User-Agent": config.user_agent})

        num_roles = len(roles) or 1
        per_role_limit = (
            config.yc_max_companies_per_role
            if config.yc_max_companies_per_role is not None
            else max(1, config.yc_max_companies // num_roles)
        )
        logger.debug(
            "per-role company limit: %d (total: %d, roles: %d)",
            per_role_limit,
            config.yc_max_companies,
            num_roles,
        )

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
                    hits_per_page=config.yc_hits_per_page,
                    max_companies=per_role_limit,
                    request_delay_seconds=max(config.base_delay_ms, 0) / 1000.0,
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