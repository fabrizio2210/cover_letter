from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

import requests
from bson import ObjectId
from bson.errors import InvalidId
from google.protobuf.timestamp_pb2 import Timestamp

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import Workflow2Result
from src.python.web_crawler.sources.ats_detector import ATSRequestFailure, detect_ats_provider
from src.python.web_crawler.sources.ats_slug_resolver import resolve_direct_slug, resolve_slug_via_search_dorking

logger = logging.getLogger(__name__)

_TERMINAL_FAILURE_FIELD = "workflow2_terminal_failure"
_SEARCH_ATTEMPTS_FIELD = "ats_slug_search_attempts"
_CAREER_PATHS = (
    "/careers",
    "/jobs",
    "/work-with-us",
    "/open-positions",
    "/join-us",
    "/company/careers",
)


def estimate_workflow2_url_checks(company_count: int) -> int:
    """
    Best-effort estimate for URL checks in workflow2.

    For each company we budget one probe per known career path.
    """
    normalized_count = max(company_count, 0)
    per_company_budget = max(len(_CAREER_PATHS), 1)
    return max(1, normalized_count * per_company_budget)


def _normalize_domain(domain: str | None) -> str | None:
    if not domain:
        return None
    normalized = domain.strip().casefold()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized or None


def _canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").casefold()
    netloc = parts.netloc.casefold()
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for url in urls:
        candidate = url.strip()
        if not candidate:
            continue
        key = _canonicalize_url(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_proto_timestamp(value) -> Timestamp | None:
    if not isinstance(value, datetime):
        return None

    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    timestamp = Timestamp()
    timestamp.FromDatetime(normalized)
    return timestamp





def _record_terminal_failure(collection, company_object_id: ObjectId, failure_type: str, url: str, message: str | None = None) -> None:
    collection.update_one(
        {"_id": company_object_id},
        {
            "$set": {
                _TERMINAL_FAILURE_FIELD: {
                    "failure_type": failure_type,
                    "failed_at": _now_utc(),
                    "last_url": url,
                    "message": message or "",
                }
            }
        },
    )


def _mark_search_attempt_started(collection, company_object_id: ObjectId, provider: str) -> None:
    prefix = f"{_SEARCH_ATTEMPTS_FIELD}.{provider}"
    collection.update_one(
        {"_id": company_object_id},
        {
            "$set": {f"{prefix}.attempted_at": _now_utc()},
            "$inc": {f"{prefix}.attempts": 1},
        },
    )


def _mark_search_attempt_outcome(collection, company_object_id: ObjectId, provider: str, outcome: str) -> None:
    prefix = f"{_SEARCH_ATTEMPTS_FIELD}.{provider}"
    collection.update_one(
        {"_id": company_object_id},
        {"$set": {f"{prefix}.outcome": outcome}},
    )


def _has_prior_search_attempt(company_state: dict, provider: str) -> bool:
    attempts = company_state.get(_SEARCH_ATTEMPTS_FIELD) or {}
    provider_attempt = attempts.get(provider)
    return isinstance(provider_attempt, dict) and int(provider_attempt.get("attempts") or 0) > 0


def _company_from_document(company_doc: dict) -> common_pb2.Company:
    company = common_pb2.Company(
        id=str(company_doc.get("_id") or ""),
        name=str(company_doc.get("name") or "").strip(),
        description=str(company_doc.get("description") or "").strip(),
        canonical_name=str(company_doc.get("canonical_name") or "").strip(),
        ats_provider=str(company_doc.get("ats_provider") or "").strip(),
        ats_slug=str(company_doc.get("ats_slug") or "").strip(),
    )

    field_id = company_doc.get("field_id")
    if field_id is not None:
        company.field_id = str(field_id)

    for source_doc in company_doc.get("discovery_sources", []):
        source = company.discovery_sources.add()
        source.source = str(source_doc.get("source") or "").strip()
        source.role = str(source_doc.get("role") or "").strip()
        source.source_url = str(source_doc.get("source_url") or "").strip()
        source.careers_url = str(source_doc.get("careers_url") or "").strip()
        source.domain = str(source_doc.get("domain") or "").strip()

    for provider, attempt_doc in (company_doc.get(_SEARCH_ATTEMPTS_FIELD) or {}).items():
        if not isinstance(attempt_doc, dict):
            continue
        attempt = company.ats_slug_search_attempts[provider]
        attempt.attempts = int(attempt_doc.get("attempts") or 0)
        attempt.outcome = str(attempt_doc.get("outcome") or "").strip()
        if attempted_at := _to_proto_timestamp(attempt_doc.get("attempted_at")):
            attempt.attempted_at.CopyFrom(attempted_at)

    terminal_failure_doc = company_doc.get(_TERMINAL_FAILURE_FIELD)
    if isinstance(terminal_failure_doc, dict):
        company.workflow2_terminal_failure.failure_type = str(terminal_failure_doc.get("failure_type") or "").strip()
        company.workflow2_terminal_failure.last_url = str(terminal_failure_doc.get("last_url") or "").strip()
        company.workflow2_terminal_failure.message = str(terminal_failure_doc.get("message") or "").strip()
        if failed_at := _to_proto_timestamp(terminal_failure_doc.get("failed_at")):
            company.workflow2_terminal_failure.failed_at.CopyFrom(failed_at)

    return company


def _candidate_urls_from_company(company: common_pb2.Company) -> list[str]:
    candidates: list[str] = []

    for source in company.discovery_sources:
        careers_url = source.careers_url.strip()
        source_url = source.source_url.strip()
        domain = _normalize_domain(source.domain)

        if careers_url:
            candidates.append(careers_url)
        if source_url:
            candidates.append(source_url)
        if domain:
            candidates.append(f"https://{domain}")
            candidates.extend(f"https://{domain}{path}" for path in _CAREER_PATHS)

    return _dedupe_urls(candidates)


def _discover_candidate_urls(company: common_pb2.Company, config: CrawlerConfig, session: requests.Session) -> list[str]:
    return _candidate_urls_from_company(company)


def _to_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _load_companies(collection, company_ids: Iterable[str] | None) -> list[common_pb2.Company]:
    if company_ids:
        object_ids = [obj_id for company_id in company_ids if (obj_id := _to_object_id(company_id)) is not None]
        documents = list(collection.find({"_id": {"$in": object_ids}})) if object_ids else []
    else:
        documents = list(collection.find({}))
    return [_company_from_document(document) for document in documents]


def run_workflow2(
    database,
    config: CrawlerConfig,
    company_ids: list[str] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> Workflow2Result:
    companies_collection = database["companies"]
    companies = _load_companies(companies_collection, company_ids)
    result = Workflow2Result()

    total_companies = len(companies)
    per_company_budget = max(len(_CAREER_PATHS), 1)
    completed_checks = 0
    estimated_checks = estimate_workflow2_url_checks(total_companies)

    if progress_callback:
        progress_callback(
            completed_checks,
            estimated_checks,
            f"Preparing ATS enrichment for {total_companies} companies",
        )

    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})

    try:
        for company_index, company_proto in enumerate(companies, start=1):
            company_id = company_proto.id
            result.company_ids.append(company_id)
            company_checks = per_company_budget

            if progress_callback:
                progress_callback(
                    completed_checks,
                    estimated_checks,
                    f"Analyzing company {company_index}/{total_companies}: {company_proto.name or company_id}",
                )

            company_object_id = _to_object_id(company_id)
            if company_object_id is None:
                result.failed_count += 1
                result.failed_companies.append(
                    {
                        "company_id": company_id,
                        "company_name": company_proto.name,
                        "error": "invalid company id",
                    }
                )
                continue

            company_state = companies_collection.find_one(
                {"_id": company_object_id},
                {"ats_provider": 1, "ats_slug": 1, _SEARCH_ATTEMPTS_FIELD: 1, _TERMINAL_FAILURE_FIELD: 1},
            ) or {}

            existing_provider = str(company_state.get("ats_provider") or "").strip()
            existing_slug = str(company_state.get("ats_slug") or "").strip()
            if existing_provider and existing_slug:
                result.skipped_count += 1
                continue

            terminal_failure = company_state.get(_TERMINAL_FAILURE_FIELD) or {}
            if terminal_failure.get("failure_type") in {"dns_resolution", "timeout"}:
                result.skipped_count += 1
                result.failed_companies.append(
                    {
                        "company_id": company_id,
                        "company_name": company_proto.name,
                        "error": f"skipped due to prior terminal failure: {terminal_failure.get('failure_type')}",
                    }
                )
                continue

            try:
                candidate_urls = _discover_candidate_urls(company_proto, config, session)
                company_checks = max(company_checks, len(candidate_urls) or 1)
                remaining_companies = max(total_companies - company_index, 0)
                estimated_checks = max(
                    estimated_checks,
                    completed_checks + company_checks + (remaining_companies * per_company_budget),
                )

                if not candidate_urls:
                    result.skipped_count += 1
                    result.failed_companies.append({"company_id": company_id, "company_name": company_proto.name, "error": "no candidate URLs"})
                    continue

                detection = detect_ats_provider(candidate_urls, config, session=session)
                if detection is None:
                    result.skipped_count += 1
                    continue

                slug = resolve_direct_slug(detection.provider, config, board_url=detection.board_url, session=session)
                logger.debug("workflow2 direct slug for company %s (provider=%s board_url=%s): %s", company_id, detection.provider, detection.board_url, slug)
                if slug is None:
                    prior = _has_prior_search_attempt(company_state, detection.provider)
                    should_attempt_serp = (not prior) or config.force_serp_retry_on_prior_attempt
                    logger.debug(
                        "workflow2 SERP fallback check for company %s (provider=%s): prior_attempt=%s force_retry=%s should_attempt=%s",
                        company_id,
                        detection.provider,
                        prior,
                        config.force_serp_retry_on_prior_attempt,
                        should_attempt_serp,
                    )
                    if should_attempt_serp:
                        if prior and config.force_serp_retry_on_prior_attempt:
                            logger.debug(
                                "workflow2 bypassing prior SERP-attempt gate for company %s (provider=%s)",
                                company_id,
                                detection.provider,
                            )
                        logger.debug("workflow2 calling SERP for company %s (%s) provider=%s", company_id, company_proto.name, detection.provider)
                        _mark_search_attempt_started(companies_collection, company_object_id, detection.provider)
                        slug = resolve_slug_via_search_dorking(company_proto.name, detection.provider, config, session=session)
                        logger.debug("workflow2 SERP result for company %s: slug=%s", company_id, slug)
                        _mark_search_attempt_outcome(companies_collection, company_object_id, detection.provider, "success" if slug else "no_results")

                if not slug:
                    result.skipped_count += 1
                    result.failed_companies.append(
                        {
                            "company_id": company_id,
                            "company_name": company_proto.name,
                            "error": f"slug unresolved for provider {detection.provider}",
                        }
                    )
                    continue

                companies_collection.update_one(
                    {"_id": company_object_id},
                    {"$set": {"ats_provider": detection.provider, "ats_slug": slug}},
                )
                result.enriched_count += 1
                result.ats_providers[detection.provider] = result.ats_providers.get(detection.provider, 0) + 1
            except ATSRequestFailure as exc:
                logger.debug("workflow2 terminal failure for company %s at %s: %s", company_id, exc.url, exc)
                _record_terminal_failure(companies_collection, company_object_id, exc.failure_type, exc.url, str(exc))
                result.skipped_count += 1
                result.failed_companies.append(
                    {
                        "company_id": company_id,
                        "company_name": company_proto.name,
                        "error": f"terminal {exc.failure_type} failure at {exc.url}",
                    }
                )
            except Exception as exc:
                logger.exception("workflow2 failed for company %s: %s", company_id, exc)
                result.failed_count += 1
                result.failed_companies.append(
                    {
                        "company_id": company_id,
                        "company_name": company_proto.name,
                        "error": str(exc),
                    }
                )
            finally:
                completed_checks += company_checks
                if progress_callback:
                    progress_callback(
                        completed_checks,
                        estimated_checks,
                        f"Workflow2 progress: {company_index}/{total_companies} companies processed",
                    )

        return result
    finally:
        session.close()