from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import html
import json
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
_JSON_LD_PATTERN = re.compile(
    r'<script\b[^>]*\btype=["\']application/ld\+json["\'][^>]*>(?P<value>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_STYLE_PATTERN = re.compile(r"<(?P<tag>script|style)\b[^>]*>.*?</(?P=tag)>", re.IGNORECASE | re.DOTALL)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_EXTERNAL_ID_PATTERN = re.compile(r"(?:/job|/remote-job)/[^/]*-(?P<id>[a-z0-9]+)$", re.IGNORECASE)
_REMOTE_WORK_ARRANGEMENTS = frozenset({"remote", "fully_remote"})


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
    without_scripts = _SCRIPT_STYLE_PATTERN.sub(" ", unescaped)
    without_tags = _TAG_PATTERN.sub(" ", without_scripts)
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


def _clean_string(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _clean_location_component(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _location_entry_has_value(entry: dict) -> bool:
    return any(
        _clean_location_component(entry.get(key))
        for key in ("city", "state", "region", "country", "continent")
    )


def _location_entries(item: dict, *, is_remote: bool) -> tuple[list[dict], str]:
    current_locations = item.get("locations")
    if isinstance(current_locations, list):
        locations = [
            entry
            for entry in current_locations
            if isinstance(entry, dict) and _location_entry_has_value(entry)
        ]
        if locations:
            return (
                sorted(locations, key=lambda entry: entry.get("is_primary") is not True),
                "locations",
            )

    legacy_key = "remote_allowed" if is_remote else "office_locations"
    legacy_locations = item.get(legacy_key)
    if isinstance(legacy_locations, list):
        locations = [
            entry
            for entry in legacy_locations
            if isinstance(entry, dict) and _location_entry_has_value(entry)
        ]
        if locations:
            return locations, legacy_key

    if is_remote:
        office_locations = item.get("office_locations")
        if isinstance(office_locations, list):
            locations = [
                entry
                for entry in office_locations
                if isinstance(entry, dict) and _location_entry_has_value(entry)
            ]
            if locations:
                return locations, "office_locations"

    return [], ""


def _format_location_entry(entry: dict) -> str:
    specific_location = ", ".join(
        part
        for part in (
            _clean_location_component(entry.get("city")),
            _clean_location_component(entry.get("state"))
            or _clean_location_component(entry.get("region")),
            _clean_location_component(entry.get("country")),
        )
        if part
    )
    return specific_location or _clean_location_component(entry.get("continent"))


def _normalize_work_arrangement(value: object) -> str:
    cleaned = _clean_string(value)
    if not cleaned:
        return "Unknown"
    return cleaned.replace("_", " ").replace("-", " ").title()


def _normalize_location(item: dict) -> str:
    work_arrangement = _clean_string(item.get("work_arrangement"))
    normalized_arrangement = work_arrangement.casefold().replace("-", "_").replace(" ", "_")
    is_remote = item.get("is_remote") is True or normalized_arrangement in _REMOTE_WORK_ARRANGEMENTS
    locations, location_source = _location_entries(item, is_remote=is_remote)

    if is_remote:
        if location_source == "office_locations":
            restrictions = _unique_strings(
                _format_location_entry(entry) for entry in locations
            )
            separator = "; "
        else:
            restrictions = _unique_strings(
                _clean_location_component(entry.get("country")) or _format_location_entry(entry)
                for entry in locations
            )
            separator = ", "
        if restrictions:
            return f"Remote ({separator.join(restrictions)})"
        return "Remote"

    formatted_locations = _unique_strings([_format_location_entry(entry) for entry in locations])
    if formatted_locations:
        return "; ".join(formatted_locations)

    return _normalize_work_arrangement(work_arrangement)


def _schema_org_prefixes(context: object, inherited: frozenset[str]) -> frozenset[str]:
    prefixes = set(inherited)
    contexts = context if isinstance(context, list) else [context]
    for entry in contexts:
        if not isinstance(entry, dict):
            continue
        for prefix, definition in entry.items():
            if prefix.startswith("@"):
                continue
            if isinstance(definition, dict):
                definition = definition.get("@id")
            if not isinstance(definition, str):
                prefixes.discard(prefix)
                continue
            parsed = urlparse(definition)
            is_schema_base = (
                parsed.scheme in {"http", "https"}
                and parsed.netloc.casefold() == "schema.org"
                and not parsed.path.strip("/")
                and not parsed.params
                and not parsed.query
                and not parsed.fragment
            )
            if is_schema_base:
                prefixes.add(prefix)
            else:
                prefixes.discard(prefix)
    return frozenset(prefixes)


def _json_ld_type_matches(value: object, expected: str, schema_prefixes: frozenset[str]) -> bool:
    types = value if isinstance(value, list) else [value]
    expected_key = expected.casefold()
    for item in types:
        type_name = _clean_string(item).rstrip("/#")
        if not type_name:
            continue
        if all(separator not in type_name for separator in (":", "/", "#")):
            if type_name.casefold() == expected_key:
                return True
            continue

        if ":" in type_name and "://" not in type_name:
            prefix, local_name = type_name.split(":", 1)
            if prefix in schema_prefixes and local_name.casefold() == expected_key:
                return True
            continue

        parsed = urlparse(type_name)
        if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != "schema.org":
            continue
        path_name = parsed.path.strip("/")
        is_schema_type = (
            not parsed.params
            and not parsed.query
            and (
                (not parsed.fragment and path_name.casefold() == expected_key)
                or (
                    bool(parsed.fragment)
                    and not path_name
                    and parsed.fragment.casefold() == expected_key
                )
            )
        )
        if is_schema_type:
            return True
    return False


def _find_json_ld_job_posting(
    value: object,
    schema_prefixes: frozenset[str] = frozenset(),
) -> dict | None:
    if isinstance(value, dict):
        schema_prefixes = _schema_org_prefixes(value.get("@context"), schema_prefixes)
        if _json_ld_type_matches(value.get("@type"), "JobPosting", schema_prefixes):
            return value
        for child in value.values():
            match = _find_json_ld_job_posting(child, schema_prefixes)
            if match is not None:
                return match
    elif isinstance(value, list):
        for child in value:
            match = _find_json_ld_job_posting(child, schema_prefixes)
            if match is not None:
                return match
    return None


def _extract_json_ld_job_posting(html_text: str) -> dict | None:
    for match in _JSON_LD_PATTERN.finditer(html_text):
        raw_value = match.group("value").strip()
        if not raw_value:
            continue
        try:
            payload = json.loads(raw_value)
        except (TypeError, ValueError):
            try:
                payload = json.loads(html.unescape(raw_value))
            except (TypeError, ValueError):
                continue
        job_posting = _find_json_ld_job_posting(payload)
        if job_posting is not None:
            return job_posting
    return None


def _json_ld_named_locations(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    names: list[str] = []
    for entry in values:
        if isinstance(entry, str):
            names.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        name = _clean_location_component(entry.get("name"))
        if name:
            names.append(name)
            continue
        address = entry.get("address")
        if isinstance(address, dict):
            country = address.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
            names.append(_clean_location_component(country))
    return _unique_strings(names)


def _json_ld_job_locations(value: object) -> list[dict]:
    values = value if isinstance(value, list) else [value]
    locations: list[dict] = []
    for entry in values:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address")
        if not isinstance(address, dict):
            name = _clean_location_component(entry.get("name"))
            if name:
                locations.append({"city": name})
            continue
        country = address.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        location = {
            "city": _clean_location_component(address.get("addressLocality")),
            "state": _clean_location_component(address.get("addressRegion")),
            "country": _clean_location_component(country),
        }
        if any(location.values()):
            locations.append(location)
    return locations


def _normalize_json_ld_location(job_posting: dict) -> str:
    location_types = job_posting.get("jobLocationType")
    if not isinstance(location_types, list):
        location_types = [location_types]
    is_remote = any(_clean_string(value).casefold() == "telecommute" for value in location_types)

    if is_remote:
        locations = [
            {"country": name}
            for name in _json_ld_named_locations(job_posting.get("applicantLocationRequirements"))
        ]
    else:
        locations = _json_ld_job_locations(job_posting.get("jobLocation"))

    return _normalize_location(
        {
            "work_arrangement": "remote" if is_remote else "",
            "locations": locations,
        }
    )


def _json_ld_company(job_posting: dict) -> tuple[str, str]:
    organization = job_posting.get("hiringOrganization")
    if isinstance(organization, list):
        organization = next((entry for entry in organization if isinstance(entry, dict)), None)
    if not isinstance(organization, dict):
        return "", ""
    name = _clean_string(organization.get("name"))
    website = organization.get("sameAs")
    if isinstance(website, list):
        website = next((_clean_string(value) for value in website if _clean_string(value)), "")
    return name, _clean_string(website)


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
    job_posting = _extract_json_ld_job_posting(html_text)
    title_match = _H1_PATTERN.search(html_text)
    company_match = _COMPANY_LINK_PATTERN.search(html_text)

    json_ld_title = _clean_string(job_posting.get("title")) if job_posting else ""
    json_ld_company_name, json_ld_company_website = _json_ld_company(job_posting or {})
    title = json_ld_title or (_strip_tags(title_match.group("value")) if title_match else "")
    company_name = json_ld_company_name or (_strip_tags(company_match.group("name")) if company_match else "")
    page_text = _strip_tags(html_text)

    if not title or not company_name:
        return None

    description = _strip_tags(_clean_string(job_posting.get("description"))) if job_posting else ""
    location = _normalize_json_ld_location(job_posting) if job_posting else "Unknown"
    if location == "Unknown":
        location = "Remote" if " Remote " in f" {page_text} " else "Unknown"

    return FourDayWeekJobCard(
        job_title=title,
        company_name=company_name,
        source_url=source_url,
        external_job_id=derive_external_job_id(source_url),
        role=title,
        description=description or page_text,
        location=location,
        company_domain=_normalize_domain(json_ld_company_website) if json_ld_company_website else "",
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
