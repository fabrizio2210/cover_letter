from __future__ import annotations

import logging
import time
from urllib.parse import parse_qs, urlparse

import requests

from src.python.web_crawler.config import CrawlerConfig

logger = logging.getLogger(__name__)

_SEARCH_HOSTS = {
    "greenhouse": "boards.greenhouse.io",
    "lever": "jobs.lever.co",
    "ashby": "jobs.ashbyhq.com",
}
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def _delay_seconds(config: CrawlerConfig, attempt: int) -> float:
    base_seconds = max(config.base_delay_ms, 0) / 1000.0
    max_seconds = max(config.max_delay_ms, 0) / 1000.0
    return min(base_seconds * attempt, max_seconds if max_seconds > 0 else base_seconds * attempt)


def extract_slug_from_url(url: str | None, provider: str) -> str | None:
    if not url:
        return None

    parsed = urlparse(url)
    path_segments = [segment for segment in parsed.path.split("/") if segment]

    if provider == "greenhouse":
        query_values = parse_qs(parsed.query).get("for")
        if query_values:
            return query_values[0].strip() or None
        return path_segments[0].strip() if path_segments else None

    if provider in {"lever", "ashby"}:
        return path_segments[0].strip() if path_segments else None

    return None


def _request_with_retries(session: requests.Session, method: str, url: str, config: CrawlerConfig, **kwargs) -> requests.Response | None:
    last_error: Exception | None = None
    for attempt in range(1, max(config.max_retries, 1) + 1):
        try:
            response = session.request(method, url, timeout=config.http_timeout_seconds, **kwargs)
        except requests.RequestException as exc:
            last_error = exc
            logger.debug("request attempt %d failed for %s: %s", attempt, url, exc)
            if attempt < max(config.max_retries, 1):
                time.sleep(_delay_seconds(config, attempt))
            continue

        if response.status_code in _TRANSIENT_STATUS_CODES and attempt < max(config.max_retries, 1):
            logger.debug("transient status %d for %s on attempt %d", response.status_code, url, attempt)
            time.sleep(_delay_seconds(config, attempt))
            continue
        return response

    if last_error:
        logger.debug("request exhausted retries for %s: %s", url, last_error)
    return None


def validate_slug_via_api(provider: str, slug: str, config: CrawlerConfig, session: requests.Session | None = None) -> bool:
    if not slug:
        return False

    if provider == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}"
    elif provider == "lever":
        url = f"https://api.lever.co/v0/postings/{slug}"
    elif provider == "ashby":
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    else:
        return False

    owned_session = session is None
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": config.user_agent})

    try:
        response = _request_with_retries(session, "GET", url, config)
        return response is not None and response.status_code < 400
    finally:
        if owned_session:
            session.close()


def resolve_direct_slug(provider: str, config: CrawlerConfig, board_url: str | None = None, session: requests.Session | None = None) -> str | None:
    direct_slug = extract_slug_from_url(board_url, provider)
    if direct_slug and validate_slug_via_api(provider, direct_slug, config, session=session):
        return direct_slug
    return None


def resolve_slug_via_search_dorking(company_name: str, provider: str, config: CrawlerConfig, session: requests.Session | None = None) -> str | None:
    if not config.serper_api_key:
        logger.debug("SERPER_API_KEY not configured; skipping search fallback for %s", company_name)
        return None

    search_host = _SEARCH_HOSTS.get(provider)
    if not search_host:
        return None

    owned_session = session is None
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": config.user_agent})

    payload = {"q": f'site:{search_host} "{company_name}"'}
    headers = {
        "X-API-KEY": config.serper_api_key,
        "Content-Type": "application/json",
    }

    try:
        response = _request_with_retries(session, "POST", config.serper_search_url, config, json=payload, headers=headers)
        if response is None or response.status_code >= 400:
            return None

        body = response.json()
        results = body.get("organic", [])
        for item in results:
            link = str(item.get("link") or "").strip()
            slug = extract_slug_from_url(link, provider)
            if not slug:
                continue
            if validate_slug_via_api(provider, slug, config, session=session):
                return slug
        return None
    finally:
        if owned_session:
            session.close()


def resolve_slug(
    company_name: str,
    provider: str,
    config: CrawlerConfig,
    board_url: str | None = None,
    session: requests.Session | None = None,
    allow_search_fallback: bool = True,
) -> str | None:
    direct_slug = resolve_direct_slug(provider, config, board_url=board_url, session=session)
    if direct_slug:
        return direct_slug
    if not allow_search_fallback:
        return None
    return resolve_slug_via_search_dorking(company_name, provider, config, session=session)