from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.sources.ats_slug_resolver import _request_with_retries

logger = logging.getLogger(__name__)


def _html_to_text(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(separator="\n").strip()


def _fetch_greenhouse_jobs(slug: str, config: CrawlerConfig, session: requests.Session) -> list[common_pb2.Job]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    response = _request_with_retries(session, "GET", url, config)
    if response is None or response.status_code >= 400:
        logger.warning("greenhouse: failed to fetch jobs for slug=%s status=%s", slug, response.status_code if response else "no response")
        return []

    try:
        body = response.json()
    except ValueError:
        logger.warning("greenhouse: invalid JSON for slug=%s", slug)
        return []

    jobs: list[common_pb2.Job] = []
    for item in body.get("jobs") or []:
        external_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        raw_content = str(item.get("content") or "").strip()
        description = _html_to_text(raw_content) if raw_content else ""
        location_obj = item.get("location") or {}
        location = str(location_obj.get("name") or "").strip() if isinstance(location_obj, dict) else ""
        source_url = str(item.get("absolute_url") or "").strip()

        if not external_id or not title:
            continue

        jobs.append(
            common_pb2.Job(
                title=title,
                description=description,
                location=location,
                platform="greenhouse",
                external_job_id=external_id,
                source_url=source_url,
            )
        )

    logger.debug("greenhouse: fetched %d jobs for slug=%s", len(jobs), slug)
    return jobs


def _fetch_lever_jobs(slug: str, config: CrawlerConfig, session: requests.Session) -> list[common_pb2.Job]:
    url = f"https://api.lever.co/v0/postings/{slug}"
    response = _request_with_retries(session, "GET", url, config)
    if response is None or response.status_code >= 400:
        logger.warning("lever: failed to fetch jobs for slug=%s status=%s", slug, response.status_code if response else "no response")
        return []

    try:
        items = response.json()
    except ValueError:
        logger.warning("lever: invalid JSON for slug=%s", slug)
        return []

    if not isinstance(items, list):
        logger.warning("lever: unexpected response shape for slug=%s", slug)
        return []

    jobs: list[common_pb2.Job] = []
    for item in items:
        external_id = str(item.get("id") or "").strip()
        title = str(item.get("text") or "").strip()
        categories = item.get("categories") or {}
        location = str(categories.get("location") or "").strip() if isinstance(categories, dict) else ""
        source_url = str(item.get("hostedUrl") or "").strip()

        # Build description by concatenating named section texts
        description_parts: list[str] = []
        for section in item.get("lists") or []:
            section_text = str(section.get("text") or "").strip()
            section_content = str(section.get("content") or "").strip()
            if section_text:
                description_parts.append(section_text)
            if section_content:
                description_parts.append(_html_to_text(section_content))
        description = "\n".join(description_parts).strip()

        if not external_id or not title:
            continue

        jobs.append(
            common_pb2.Job(
                title=title,
                description=description,
                location=location,
                platform="lever",
                external_job_id=external_id,
                source_url=source_url,
            )
        )

    logger.debug("lever: fetched %d jobs for slug=%s", len(jobs), slug)
    return jobs


def _fetch_ashby_jobs(slug: str, config: CrawlerConfig, session: requests.Session) -> list[common_pb2.Job]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    response = _request_with_retries(session, "GET", url, config)
    if response is None or response.status_code >= 400:
        logger.warning("ashby: failed to fetch jobs for slug=%s status=%s", slug, response.status_code if response else "no response")
        return []

    try:
        body = response.json()
    except ValueError:
        logger.warning("ashby: invalid JSON for slug=%s", slug)
        return []

    jobs: list[common_pb2.Job] = []
    for item in (body.get("jobPostings") or []):
        external_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        raw_description = str(item.get("descriptionHtml") or item.get("description") or "").strip()
        description = _html_to_text(raw_description) if raw_description else ""
        location = str(item.get("location") or "").strip()
        source_url = str(item.get("jobUrl") or item.get("applyUrl") or "").strip()

        if not external_id or not title:
            continue

        jobs.append(
            common_pb2.Job(
                title=title,
                description=description,
                location=location,
                platform="ashby",
                external_job_id=external_id,
                source_url=source_url,
            )
        )

    logger.debug("ashby: fetched %d jobs for slug=%s", len(jobs), slug)
    return jobs


_FETCHERS = {
    "greenhouse": _fetch_greenhouse_jobs,
    "lever": _fetch_lever_jobs,
    "ashby": _fetch_ashby_jobs,
}


def fetch_jobs(provider: str, slug: str, config: CrawlerConfig, session: requests.Session) -> list[common_pb2.Job]:
    fetcher = _FETCHERS.get(provider)
    if fetcher is None:
        logger.warning("fetch_jobs: unknown provider %r", provider)
        return []
    try:
        return fetcher(slug, config, session)
    except Exception as exc:
        logger.exception("fetch_jobs: unhandled error for provider=%s slug=%s: %s", provider, slug, exc)
        return []
