from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany
from src.python.web_crawler.sources.base import SourceAdapter

logger = logging.getLogger(__name__)


_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_ATS_HOSTS = {
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
}
_HN_HOSTS = {
    "news.ycombinator.com",
    "hn.algolia.com",
}
_STOP_COMPANY_TOKENS = {
    "who is hiring",
    "hiring",
    "remote",
    "onsite",
    "on-site",
    "hybrid",
    "full-time",
    "full time",
    "part-time",
    "part time",
}


class HackerNewsAdapter(SourceAdapter):
    source_name = "hackernews"
    _search_by_date_url = "https://hn.algolia.com/api/v1/search_by_date"
    _search_comments_url = "https://hn.algolia.com/api/v1/search"

    @staticmethod
    def _is_monthly_who_is_hiring_title(title: str) -> bool:
        normalized = title.strip()
        if not normalized:
            return False
        return re.match(r"(?i)^(ask\s+hn:\s*)?who\s+is\s+hiring\?\s*(\(.*\))?$", normalized) is not None

    @staticmethod
    def _delay_seconds(config: CrawlerConfig, attempt: int) -> float:
        base_seconds = max(config.base_delay_ms, 0) / 1000.0
        max_seconds = max(config.max_delay_ms, 0) / 1000.0
        return min(base_seconds * attempt, max_seconds if max_seconds > 0 else base_seconds * attempt)

    def _request_with_retries(self, session: requests.Session, url: str, config: CrawlerConfig, params: dict | None = None) -> requests.Response | None:
        last_error: Exception | None = None
        for attempt in range(1, max(config.max_retries, 1) + 1):
            try:
                response = session.get(url, params=params, timeout=config.http_timeout_seconds)
            except requests.RequestException as exc:
                last_error = exc
                logger.debug("hackernews request attempt %d failed for %s: %s", attempt, url, exc)
                if attempt < max(config.max_retries, 1):
                    time.sleep(self._delay_seconds(config, attempt))
                continue

            if response.status_code in _TRANSIENT_STATUS_CODES and attempt < max(config.max_retries, 1):
                logger.debug("hackernews transient status %d for %s on attempt %d", response.status_code, url, attempt)
                time.sleep(self._delay_seconds(config, attempt))
                continue
            return response

        if last_error is not None:
            logger.debug("hackernews request exhausted retries for %s: %s", url, last_error)
        return None

    @staticmethod
    def _first_non_empty_line(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""

    @staticmethod
    def _normalize_domain(url_or_domain: str) -> str:
        if not url_or_domain:
            return ""
        value = url_or_domain.strip()
        if not value:
            return ""

        if "//" not in value:
            value = f"https://{value}"

        parsed = urlparse(value)
        domain = parsed.netloc.casefold()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain

    @staticmethod
    def _extract_candidate_links(comment_html: str) -> list[str]:
        if not comment_html:
            return []

        soup = BeautifulSoup(comment_html, "html.parser")
        urls: list[str] = []

        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            if href:
                urls.append(href)

        text = soup.get_text(" ", strip=True)
        for bare_domain in re.findall(r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[\w\-./?%&=]*)?", text):
            urls.append(bare_domain)

        return urls

    @staticmethod
    def _select_careers_url(urls: list[str]) -> str:
        for raw_url in urls:
            normalized = raw_url.strip()
            if not normalized:
                continue
            parsed = urlparse(normalized if "://" in normalized else f"https://{normalized}")
            host = parsed.netloc.casefold().removeprefix("www.")
            if not host or host in _HN_HOSTS:
                continue

            path = parsed.path.casefold()
            if "/careers" in path or "/jobs" in path or host in _ATS_HOSTS:
                return parsed.geturl()

        for raw_url in urls:
            normalized = raw_url.strip()
            if not normalized:
                continue
            parsed = urlparse(normalized if "://" in normalized else f"https://{normalized}")
            host = parsed.netloc.casefold().removeprefix("www.")
            if host and host not in _HN_HOSTS:
                return parsed.geturl()

        return ""

    @staticmethod
    def _looks_like_company_name(value: str) -> bool:
        candidate = value.strip().strip("-:|")
        if not candidate:
            return False
        if len(candidate) < 2 or len(candidate) > 80:
            return False
        lowered = candidate.casefold()
        for token in _STOP_COMPANY_TOKENS:
            if token in lowered:
                return False
        return bool(re.search(r"[a-zA-Z]", candidate))

    @classmethod
    def _extract_company_name(cls, plain_text: str) -> str:
        if not plain_text:
            return ""

        prefix_patterns = [
            r"(?im)^\s*(?:company|employer|organization)\s*[:\-]\s*([^\n|]{2,80})",
            r"(?im)^\s*(?:we(?:'re| are)?)\s*[:\-]\s*([^\n|]{2,80})",
        ]
        for pattern in prefix_patterns:
            match = re.search(pattern, plain_text)
            if match:
                candidate = match.group(1).strip()
                if cls._looks_like_company_name(candidate):
                    return candidate

        first_line = cls._first_non_empty_line(plain_text)
        if "|" in first_line:
            candidate = first_line.split("|", maxsplit=1)[0].strip()
            if cls._looks_like_company_name(candidate):
                return candidate

        if "-" in first_line:
            candidate = first_line.split("-", maxsplit=1)[0].strip()
            if cls._looks_like_company_name(candidate):
                return candidate

        if cls._looks_like_company_name(first_line):
            return first_line

        return ""

    @staticmethod
    def _role_matches(text: str, roles: list[str]) -> str:
        lowered = text.casefold()
        for role in roles:
            normalized_role = role.strip()
            if normalized_role and normalized_role.casefold() in lowered:
                return normalized_role
        return ""

    def _fetch_recent_threads(self, session: requests.Session, config: CrawlerConfig) -> list[dict]:
        # Search a larger window than the final cap because by-date results include
        # related posts (e.g. "Tell HN") that are not the canonical monthly threads.
        search_hits_per_page = min(max(config.hn_max_threads * 10, 30), 200)
        params = {
            "query": "who is hiring",
            "tags": "story",
            "hitsPerPage": search_hits_per_page,
        }
        response = self._request_with_retries(session, self._search_by_date_url, config, params=params)
        if response is None:
            return []
        response.raise_for_status()
        payload = response.json()
        hits = payload.get("hits", [])
        logger.debug("hackernews thread search returned %d hits", len(hits))

        threads: list[dict] = []
        for hit in hits:
            title = str(hit.get("title") or "")
            if not self._is_monthly_who_is_hiring_title(title):
                continue
            threads.append(hit)
            if len(threads) >= config.hn_max_threads:
                break

        return threads

    def _fetch_thread_comments(self, session: requests.Session, config: CrawlerConfig, story_id: str) -> list[dict]:
        comments: list[dict] = []
        page = 0

        while len(comments) < config.hn_max_comments_per_thread:
            params = {
                "tags": f"comment,story_{story_id}",
                "hitsPerPage": config.hn_comments_hits_per_page,
                "page": page,
            }
            response = self._request_with_retries(session, self._search_comments_url, config, params=params)
            if response is None:
                break

            response.raise_for_status()
            payload = response.json()
            hits = payload.get("hits", [])
            if not hits:
                break

            comments.extend(hits)
            total_pages = int(payload.get("nbPages") or 0)
            page += 1
            if page >= total_pages:
                break

            if config.base_delay_ms > 0:
                time.sleep(max(config.base_delay_ms, 0) / 1000.0)

        return comments[: config.hn_max_comments_per_thread]

    def _comment_to_company(self, comment: dict, roles: list[str]) -> DiscoveredCompany | None:
        comment_html = str(comment.get("comment_text") or "").strip()
        if not comment_html:
            return None

        soup = BeautifulSoup(comment_html, "html.parser")
        plain_text = soup.get_text("\n", strip=True)
        if not plain_text:
            return None

        matched_role = self._role_matches(plain_text, roles)
        if not matched_role:
            return None

        company_name = self._extract_company_name(plain_text)
        if not company_name:
            return None

        links = self._extract_candidate_links(comment_html)
        careers_url = self._select_careers_url(links)
        domain = self._normalize_domain(careers_url)
        if not domain:
            for raw_link in links:
                parsed_domain = self._normalize_domain(raw_link)
                if parsed_domain and parsed_domain not in _HN_HOSTS:
                    domain = parsed_domain
                    break

        comment_id = str(comment.get("objectID") or "").strip()
        source_url = f"https://news.ycombinator.com/item?id={comment_id}" if comment_id else ""

        if not careers_url and not domain and not source_url:
            return None

        return DiscoveredCompany(
            name=company_name,
            source=self.source_name,
            role=matched_role,
            source_url=source_url,
            careers_url=careers_url,
            domain=domain,
        )

    def discover_companies(self, roles: list[str], config: CrawlerConfig) -> list[DiscoveredCompany]:
        if not roles:
            return []

        discovered: list[DiscoveredCompany] = []
        session = requests.Session()
        session.headers.update({"User-Agent": config.user_agent})

        threads = self._fetch_recent_threads(session, config)
        logger.debug("hackernews selected %d recent Who Is Hiring threads", len(threads))

        for thread in threads:
            story_id = str(thread.get("objectID") or "").strip()
            if not story_id:
                continue
            comments = self._fetch_thread_comments(session, config, story_id)
            logger.debug("hackernews story=%s yielded %d comments", story_id, len(comments))

            per_role_counts: dict[str, int] = {role: 0 for role in roles}
            for comment in comments:
                company = self._comment_to_company(comment, roles)
                if company is None:
                    continue

                if per_role_counts.get(company.role, 0) >= config.hn_max_companies_per_role:
                    continue

                per_role_counts[company.role] = per_role_counts.get(company.role, 0) + 1
                discovered.append(company)

        logger.debug("hackernews discovered %d companies", len(discovered))
        return discovered
