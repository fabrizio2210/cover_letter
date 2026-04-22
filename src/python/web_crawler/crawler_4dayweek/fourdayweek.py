from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import logging
import re
import time
from urllib.parse import urljoin, urlparse

import requests

from src.python.web_crawler.config import CrawlerConfig

logger = logging.getLogger(__name__)

_API_URL = "https://4dayweek.io/api/v2/jobs"
_FALLBACK_PAGE_URL = "https://4dayweek.io/jobs"
_JOB_LINK_PATTERN = re.compile(r'href=["\'](?P<url>(?:https://4dayweek\.io)?/(?:job|remote-job)/[^"\'#?]+)["\']', re.IGNORECASE)
_NEXT_PAGE_PATTERN = re.compile(r'href=["\'](?P<url>[^"\']+\?page=\d+)["\']', re.IGNORECASE)
_H1_PATTERN = re.compile(r"<h1[^>]*>(?P<value>.*?)</h1>", re.IGNORECASE | re.DOTALL)
_COMPANY_LINK_PATTERN = re.compile(r'href=["\'](?:https://4dayweek\.io)?/company/(?P<slug>[^"\'#?/]+)["\'][^>]*>(?P<name>.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_EXTERNAL_ID_PATTERN = re.compile(r"(?:/job|/remote-job)/[^/]*-(?P<id>[a-z0-9]+)$", re.IGNORECASE)


@dataclass(slots=True)
class FourDayWeekJobCard:
    job_title: str
    company_name: str
    source_url: str
    external_job_id: str
    role: str
    description: str
    location: str
    company_domain: str = ""


def _strip_tags(value: str) -> str:
    unescaped = html.unescape(value or "")
    without_tags = _TAG_PATTERN.sub(" ", unescaped)
    return _WHITESPACE_PATTERN.sub(" ", without_tags).strip()


def derive_external_job_id(source_url: str) -> str:
    path = urlparse(source_url).path.rstrip("/")
    match = _EXTERNAL_ID_PATTERN.search(path)
    if match:
        return match.group("id").lower()
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


def _normalize_domain(url: str) -> str:
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    return host


def _normalize_location(item: dict) -> str:
    if item.get("is_remote"):
        countries = [entry.get("country", "").strip() for entry in item.get("remote_allowed") or [] if entry.get("country")]
        if countries:
            return f"Remote ({', '.join(countries)})"

        office_locations = item.get("office_locations") or []
        if office_locations:
            parts = []
            for office in office_locations:
                city = office.get("city", "").strip()
                country = office.get("country", "").strip()
                joined = ", ".join(part for part in (city, country) if part)
                if joined:
                    parts.append(joined)
            if parts:
                return f"Remote ({'; '.join(parts)})"

        return "Remote"

    office_locations = item.get("office_locations") or []
    if office_locations:
        office = office_locations[0]
        city = office.get("city", "").strip()
        country = office.get("country", "").strip()
        return ", ".join(part for part in (city, country) if part) or item.get("work_arrangement", "")

    return str(item.get("work_arrangement") or "").strip() or "Unknown"


def _job_card_from_api_item(item: dict) -> FourDayWeekJobCard | None:
    source_url = str(item.get("url") or "").strip()
    company = item.get("company") or {}
    company_name = str(company.get("name") or "").strip()
    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or "").strip()

    if not source_url or not title or not company_name:
        return None

    website = str(company.get("website") or "").strip()
    role = str(item.get("role") or "").strip() or title
    return FourDayWeekJobCard(
        job_title=title,
        company_name=company_name,
        source_url=source_url,
        external_job_id=derive_external_job_id(source_url),
        role=role,
        description=description,
        location=_normalize_location(item),
        company_domain=_normalize_domain(website) if website else "",
    )


def _extract_job_urls(html_text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in _JOB_LINK_PATTERN.finditer(html_text):
        resolved = urljoin("https://4dayweek.io", match.group("url"))
        if resolved in seen:
            continue
        seen.add(resolved)
        urls.append(resolved)
    return urls


def _extract_next_page_urls(html_text: str, current_url: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in _NEXT_PAGE_PATTERN.finditer(html_text):
        resolved = urljoin(current_url, match.group("url"))
        if resolved in seen:
            continue
        seen.add(resolved)
        urls.append(resolved)
    return urls


def _parse_job_detail_html(source_url: str, html_text: str) -> FourDayWeekJobCard | None:
    title_match = _H1_PATTERN.search(html_text)
    company_match = _COMPANY_LINK_PATTERN.search(html_text)
    title = _strip_tags(title_match.group("value")) if title_match else ""
    company_name = _strip_tags(company_match.group("name")) if company_match else ""
    page_text = _strip_tags(html_text)

    if not title or not company_name:
        return None

    location = "Remote" if " Remote " in f" {page_text} " else "Unknown"
    return FourDayWeekJobCard(
        job_title=title,
        company_name=company_name,
        source_url=source_url,
        external_job_id=derive_external_job_id(source_url),
        role=title,
        description=page_text,
        location=location,
    )


class FourDayWeekAdapter:
    def _build_session(self, config: CrawlerConfig) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": config.user_agent,
                "Referer": config.referer,
            }
        )
        return session

    def discover_jobs(self, config: CrawlerConfig) -> list[FourDayWeekJobCard]:
        session = self._build_session(config)
        try:
            try:
                return self._discover_jobs_via_api(session, config)
            except Exception as exc:
                logger.warning("4dayweek API discovery failed, falling back to HTML crawl: %s", exc)
                return self._discover_jobs_via_html(session, config)
        finally:
            session.close()

    def _discover_jobs_via_api(self, session: requests.Session, config: CrawlerConfig) -> list[FourDayWeekJobCard]:
        page = 1
        jobs: list[FourDayWeekJobCard] = []
        seen_urls: set[str] = set()

        while True:
            response = session.get(
                _API_URL,
                params={"page": page, "limit": 100},
                timeout=config.http_timeout_seconds,
            )
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "1"))
                time.sleep(max(retry_after, 1))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("4dayweek API returned non-object payload")

            for item in payload.get("data") or []:
                if not isinstance(item, dict):
                    continue
                card = _job_card_from_api_item(item)
                if card is None or card.source_url in seen_urls:
                    continue
                seen_urls.add(card.source_url)
                jobs.append(card)

            if not payload.get("has_more"):
                break
            page += 1

        return jobs

    def _discover_jobs_via_html(self, session: requests.Session, config: CrawlerConfig) -> list[FourDayWeekJobCard]:
        queue = [_FALLBACK_PAGE_URL]
        seen_pages: set[str] = set()
        seen_jobs: set[str] = set()
        jobs: list[FourDayWeekJobCard] = []

        while queue:
            page_url = queue.pop(0)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)

            response = session.get(page_url, timeout=config.http_timeout_seconds)
            response.raise_for_status()
            html_text = response.text

            for next_page in _extract_next_page_urls(html_text, page_url):
                if next_page not in seen_pages:
                    queue.append(next_page)

            for job_url in _extract_job_urls(html_text):
                if job_url in seen_jobs:
                    continue
                seen_jobs.add(job_url)

                detail_response = session.get(job_url, timeout=config.http_timeout_seconds)
                detail_response.raise_for_status()
                card = _parse_job_detail_html(job_url, detail_response.text)
                if card is not None:
                    jobs.append(card)

        return jobs
