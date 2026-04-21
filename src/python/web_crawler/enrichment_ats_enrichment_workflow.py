from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

import requests
from bson import ObjectId
from bson.errors import InvalidId
from google.protobuf.timestamp_pb2 import Timestamp

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.executor import (
    ATSWorkerResult,
    ATSWorkerTask,
    ThreadSafeSessionPool,
    _detect_ats_worker,
)
from src.python.web_crawler.models import WorkflowResult
from src.python.web_crawler.sources.ats_detector import ATSRequestFailure, detect_ats_provider
from src.python.web_crawler.sources.ats_slug_resolver import resolve_direct_slug, resolve_slug_via_search_dorking

logger = logging.getLogger(__name__)

_TERMINAL_FAILURE_FIELD = "enrichment_ats_enrichment_terminal_failure"
_SEARCH_ATTEMPTS_FIELD = "ats_slug_search_attempts"
_CAREER_PATHS = (
    "/careers",
    "/jobs",
    "/work-with-us",
    "/open-positions",
    "/join-us",
    "/company/careers",
)


def estimate_enrichment_ats_enrichment_url_checks(company_count: int) -> int:
    """
    Best-effort estimate for URL checks in enrichment_ats_enrichment.

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
        company.enrichment_ats_enrichment_terminal_failure.failure_type = str(terminal_failure_doc.get("failure_type") or "").strip()
        company.enrichment_ats_enrichment_terminal_failure.last_url = str(terminal_failure_doc.get("last_url") or "").strip()
        company.enrichment_ats_enrichment_terminal_failure.message = str(terminal_failure_doc.get("message") or "").strip()
        if failed_at := _to_proto_timestamp(terminal_failure_doc.get("failed_at")):
            company.enrichment_ats_enrichment_terminal_failure.failed_at.CopyFrom(failed_at)

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


def _task_progress_units(task: ATSWorkerTask) -> int:
    return max(len(task.candidate_urls), 1)


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


def run_enrichment_ats_enrichment(
    database,
    config: CrawlerConfig,
    company_ids: list[str] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> WorkflowResult:
    companies_collection = database["companies"]
    companies = _load_companies(companies_collection, company_ids)
    result = WorkflowResult()

    total_companies = len(companies)
    completed_checks = 0
    estimated_checks = estimate_enrichment_ats_enrichment_url_checks(total_companies)

    if progress_callback:
        progress_callback(
            completed_checks,
            estimated_checks,
            f"Preparing ATS enrichment for {total_companies} companies",
        )

    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})

    try:
        # ========== PHASE A: Pre-fetch company state and build task list ==========
        logger.info("enrichment_ats_enrichment: Phase A - Pre-fetching company state for %d companies", total_companies)
        
        tasks_to_process: list[tuple[ATSWorkerTask, common_pb2.Company, dict]] = []
        
        for company_index, company_proto in enumerate(companies, start=1):
            company_id = company_proto.id
            result.company_ids.append(company_id)

            if progress_callback and company_index % max(1, total_companies // 10) == 0:
                progress_callback(
                    completed_checks,
                    estimated_checks,
                    f"Pre-processing company {company_index}/{total_companies}: {company_proto.name or company_id}",
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

            candidate_urls = _discover_candidate_urls(company_proto, config, session)
            if not candidate_urls:
                result.skipped_count += 1
                result.failed_companies.append({"company_id": company_id, "company_name": company_proto.name, "error": "no candidate URLs"})
                continue

            task = ATSWorkerTask(
                company_id=company_id,
                company_object_id=company_object_id,
                company_name=company_proto.name,
                candidate_urls=candidate_urls,
                company_index=company_index,
                total_companies=total_companies,
            )
            tasks_to_process.append((task, company_proto, company_state))

        ready_for_processing = len(tasks_to_process)
        estimated_checks = max(sum(_task_progress_units(task) for task, _, _ in tasks_to_process), 1)
        logger.info("enrichment_ats_enrichment: Phase A complete. %d/%d companies ready for processing", ready_for_processing, total_companies)

        if progress_callback:
            progress_callback(
                completed_checks,
                estimated_checks,
                f"Phase A complete: {ready_for_processing}/{total_companies} companies ready for ATS detection",
            )

        # ========== PHASE B: Parallel ATS detection using thread pool ==========
        logger.info("enrichment_ats_enrichment: Phase B - Starting parallel ATS detection with 10 workers")
        
        session_pool = ThreadSafeSessionPool(config.user_agent)
        
        try:
            with ThreadPoolExecutor(max_workers=10) as executor:
                future_to_task_info = {
                    executor.submit(_detect_ats_worker, task, config, session_pool): (task, company_proto, company_state)
                    for task, company_proto, company_state in tasks_to_process
                }

                logger.info("enrichment_ats_enrichment: Submitted %d tasks to executor", len(future_to_task_info))

                completed_count = 0
                for future in as_completed(future_to_task_info):
                    task, company_proto, company_state = future_to_task_info[future]
                    completed_count += 1
                    completed_checks += _task_progress_units(task)

                    try:
                        worker_result: ATSWorkerResult = future.result()
                        
                        # ===== PHASE B.1: Process worker result =====
                        if worker_result.success:
                            provider = worker_result.provider
                            slug = worker_result.slug
                            if not provider or not slug:
                                raise ValueError("worker returned success without provider and slug")

                            # Successful detection + direct slug resolution
                            logger.debug(
                                "enrichment_ats_enrichment: ATS detection successful for company %s: provider=%s slug=%s",
                                worker_result.company_id,
                                provider,
                                slug,
                            )
                            companies_collection.update_one(
                                {"_id": worker_result.company_object_id},
                                {"$set": {"ats_provider": provider, "ats_slug": slug}},
                            )
                            result.enriched_count += 1
                            result.ats_providers[provider] = result.ats_providers.get(provider, 0) + 1
                        
                        elif worker_result.error_type == "ats_request_failure:dns_resolution" or worker_result.error_type == "ats_request_failure:timeout":
                            # Terminal failures: record and skip
                            logger.debug(
                                "enrichment_ats_enrichment: Terminal failure for company %s: %s at %s",
                                worker_result.company_id,
                                worker_result.error_type,
                                worker_result.error_url,
                            )
                            failure_type = worker_result.error_type.split(":")[-1]
                            _record_terminal_failure(
                                companies_collection,
                                worker_result.company_object_id,
                                failure_type,
                                worker_result.error_url or "unknown",
                                worker_result.error_message,
                            )
                            result.skipped_count += 1
                            result.failed_companies.append(
                                {
                                    "company_id": worker_result.company_id,
                                    "company_name": worker_result.company_name,
                                    "error": f"terminal {failure_type} failure",
                                }
                            )
                        
                        elif worker_result.error_type == "slug_not_resolved_direct":
                            provider = worker_result.provider
                            if not provider:
                                raise ValueError("worker returned slug_not_resolved_direct without provider")

                            # Direct slug resolution failed; attempt SERP fallback in main thread
                            logger.debug(
                                "enrichment_ats_enrichment: Direct slug resolution failed for company %s (provider=%s). Attempting SERP fallback.",
                                worker_result.company_id,
                                provider,
                            )

                            prior = _has_prior_search_attempt(company_state, provider)
                            should_attempt_serp = (not prior) or config.force_serp_retry_on_prior_attempt

                            if should_attempt_serp:
                                estimated_checks += 1
                                if prior and config.force_serp_retry_on_prior_attempt:
                                    logger.debug(
                                        "enrichment_ats_enrichment: Bypassing prior SERP-attempt gate for company %s (provider=%s)",
                                        worker_result.company_id,
                                        provider,
                                    )

                                logger.debug(
                                    "enrichment_ats_enrichment: Calling SERP fallback for company %s (provider=%s)",
                                    worker_result.company_id,
                                    provider,
                                )
                                try:
                                    _mark_search_attempt_started(
                                        companies_collection,
                                        worker_result.company_object_id,
                                        provider,
                                    )
                                    slug = resolve_slug_via_search_dorking(
                                        company_proto.name,
                                        provider,
                                        config,
                                        session=session,
                                    )
                                    logger.debug(
                                        "enrichment_ats_enrichment: SERP fallback result for company %s: slug=%s",
                                        worker_result.company_id,
                                        slug or "not found",
                                    )
                                    _mark_search_attempt_outcome(
                                        companies_collection,
                                        worker_result.company_object_id,
                                        provider,
                                        "success" if slug else "no_results",
                                    )

                                    if slug:
                                        companies_collection.update_one(
                                            {"_id": worker_result.company_object_id},
                                            {"$set": {"ats_provider": provider, "ats_slug": slug}},
                                        )
                                        result.enriched_count += 1
                                        result.ats_providers[provider] = result.ats_providers.get(provider, 0) + 1
                                        logger.debug(
                                            "enrichment_ats_enrichment: SERP fallback successful for company %s: provider=%s slug=%s",
                                            worker_result.company_id,
                                            provider,
                                            slug,
                                        )
                                    else:
                                        result.skipped_count += 1
                                        result.failed_companies.append(
                                            {
                                                "company_id": worker_result.company_id,
                                                "company_name": worker_result.company_name,
                                                "error": f"slug unresolved for provider {provider}",
                                            }
                                        )
                                finally:
                                    completed_checks += 1
                            else:
                                result.skipped_count += 1
                                result.failed_companies.append(
                                    {
                                        "company_id": worker_result.company_id,
                                        "company_name": worker_result.company_name,
                                        "error": f"direct slug resolution failed; SERP fallback skipped (already attempted)",
                                    }
                                )
                        
                        else:
                            # Other errors: no ATS provider detected, unexpected errors, etc.
                            if worker_result.error_type == "unexpected_error":
                                result.failed_count += 1
                            else:
                                result.skipped_count += 1
                            result.failed_companies.append(
                                {
                                    "company_id": worker_result.company_id,
                                    "company_name": worker_result.company_name,
                                    "error": worker_result.error_message or "ATS detection failed",
                                }
                            )
                            logger.debug(
                                "enrichment_ats_enrichment: ATS detection failed for company %s: %s",
                                worker_result.company_id,
                                worker_result.error_message,
                            )
                        
                        # Progress reporting
                        if progress_callback:
                            progress_callback(
                            completed_checks,
                                estimated_checks,
                                f"ATS detection progress: {completed_count}/{ready_for_processing} companies completed",
                            )

                    except Exception as exc:
                        logger.exception("enrichment_ats_enrichment: Unexpected error processing worker result for company %s", task.company_id)
                        result.failed_count += 1
                        result.failed_companies.append(
                            {
                                "company_id": task.company_id,
                                "company_name": task.company_name,
                                "error": f"Error processing worker result: {str(exc)}",
                            }
                        )

                if progress_callback:
                    progress_callback(
                        completed_checks,
                        estimated_checks,
                        f"Phase B complete: {completed_count}/{ready_for_processing} companies processed",
                    )

            logger.info("enrichment_ats_enrichment: Phase B complete. Enriched=%d, Skipped=%d, Failed=%d", result.enriched_count, result.skipped_count, result.failed_count)
        
        finally:
            session_pool.close_all()

        return result
    
    finally:
        session.close()