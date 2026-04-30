from __future__ import annotations

import json
import logging
import time
from typing import Callable

import redis
from bson import ObjectId
from bson.errors import InvalidId

from src.python.ai_querier import common_pb2
from src.python.web_crawler.company_resolver import (
    canonicalize_company_name,
    upsert_companies,
)
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany, WorkflowResult
from src.python.web_crawler.progress import utc_timestamp
from src.python.web_crawler.role_filtering import load_identity_roles, text_matches_roles
from src.python.web_crawler.sources.levelsfyi import LevelsFyiAdapter, LevelsFyiJobCard
from src.python.web_crawler.workflow_messages import company_discovery_event_to_json

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_WORKFLOW_ID = "crawler_levelsfyi"
_PLATFORM = "levelsfyi"


def _now_timestamp() -> dict:
    return {"seconds": int(time.time()), "nanos": 0}


def _to_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _upsert_job(
    jobs_collection,
    *,
    job_title: str,
    description: str,
    location: str,
    external_job_id: str,
    source_url: str,
    company_oid: ObjectId,
) -> tuple[str, bool]:
    """Insert or update a job document. Returns (job_id_hex, was_inserted)."""
    existing = jobs_collection.find_one(
        {"platform": _PLATFORM, "external_job_id": external_job_id},
        {"_id": 1},
    )
    if existing is None:
        doc = {
            "title": job_title,
            "description": description,
            "location": location,
            "platform": _PLATFORM,
            "external_job_id": external_job_id,
            "source_url": source_url,
            "company": company_oid,
            "created_at": _now_timestamp(),
            "updated_at": _now_timestamp(),
        }
        result = jobs_collection.insert_one(doc)
        return str(result.inserted_id), True
    else:
        job_id = str(existing["_id"])
        jobs_collection.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "title": job_title,
                    "description": description,
                    "location": location,
                    "source_url": source_url,
                    "updated_at": _now_timestamp(),
                }
            },
        )
        return job_id, False


def _try_enqueue(redis_client, config: CrawlerConfig, job_id: str) -> bool:
    try:
        payload = json.dumps({"job_id": job_id})
        redis_client.rpush(config.job_scoring_queue_name, payload)
        return True
    except Exception as exc:
        logger.warning("crawler_levelsfyi: failed to enqueue job_id=%s: %s", job_id, exc)
        return False


def _connect_redis(config: CrawlerConfig):
    try:
        client = redis.Redis(
            host=config.redis_host,
            port=config.redis_port,
            socket_connect_timeout=5,
            decode_responses=True,
        )
        client.ping()
        return client
    except Exception as exc:
        logger.warning("crawler_levelsfyi: Redis unavailable (%s); scoring enqueue disabled for this run", exc)
        return None


def _find_companies_missing_slug(collection, company_ids: list[str]) -> list[str]:
    """Return company_ids whose documents have no ats_slug set."""
    if not company_ids:
        return []
    object_ids = [oid for cid in company_ids if (oid := _to_object_id(cid)) is not None]
    if not object_ids:
        return []
    docs = list(
        collection.find(
            {"_id": {"$in": object_ids}, "$or": [{"ats_slug": {"$exists": False}}, {"ats_slug": ""}]},
            {"_id": 1},
        )
    )
    return [str(doc["_id"]) for doc in docs]


def _emit_enrichment_events(
    redis_client: redis.Redis,
    config: CrawlerConfig,
    *,
    run_id: str,
    workflow_run_id: str,
    identity_id: str,
    company_ids: list[str],
) -> None:
    """Push one CompanyDiscoveryEvent per company into the enrichment queue."""
    for company_id in company_ids:
        try:
            event = common_pb2.CompanyDiscoveryEvent(
                run_id=run_id,
                workflow_run_id=workflow_run_id,
                workflow_id=_WORKFLOW_ID,
                identity_id=identity_id,
                company_id=company_id,
                reason="new_company_or_newly_actionable",
            )
            event.emitted_at.CopyFrom(utc_timestamp())
            redis_client.rpush(
                config.crawler_enrichment_ats_enrichment_queue_name,
                company_discovery_event_to_json(event),
            )
            logger.debug("crawler_levelsfyi: emitted CompanyDiscoveryEvent for company %s", company_id)
        except Exception as exc:
            logger.warning(
                "crawler_levelsfyi: failed to emit CompanyDiscoveryEvent for company %s: %s",
                company_id,
                exc,
            )


def run_crawler_levelsfyi(
    database,
    config: CrawlerConfig,
    identity_id: str,
    progress_callback: Callable[[int, int, str], None] | None = None,
    *,
    identity_database,
) -> WorkflowResult:
    """Run the crawler_levelsfyi workflow: discover jobs from Levels.fyi and upsert them."""
    identities_collection = identity_database["identities"]
    companies_collection = database["companies"]
    jobs_collection = database["jobs"]

    result = WorkflowResult()

    roles = load_identity_roles(
        identities_collection,
        identity_id,
        logger=logger,
        workflow_name="crawler_levelsfyi",
    )
    if not roles:
        logger.info("crawler_levelsfyi: identity %s has no roles; emitting no jobs", identity_id)
        if progress_callback:
            progress_callback(0, 1, "Skipping: identity has no configured roles")
        return result

    adapter = LevelsFyiAdapter()

    if progress_callback:
        progress_callback(0, 1, "Fetching job listings from Levels.fyi")

    job_cards = adapter.discover_jobs(roles, config)
    result.discovered_count = len(job_cards)
    logger.debug("crawler_levelsfyi: discovered %d job cards", len(job_cards))

    if not job_cards:
        if progress_callback:
            progress_callback(1, 1, "No jobs discovered from Levels.fyi")
        return result

    estimated = max(len(job_cards), 1)

    # Collect all DiscoveredCompany objects for batch upsert
    discovered_companies: list[DiscoveredCompany] = []
    company_name_to_card: dict[str, LevelsFyiJobCard] = {}
    for card in job_cards:
        if card.company_name:
            dc = DiscoveredCompany(
                name=card.company_name,
                source=_PLATFORM,
                role=card.role,
                source_url=card.source_url,
                domain=card.domain,
            )
            discovered_companies.append(dc)
            canonical = canonicalize_company_name(card.company_name)
            company_name_to_card.setdefault(canonical, card)

    _, _, all_company_ids = upsert_companies(companies_collection, discovered_companies)

    # Build a lookup: canonical_name -> company_oid (from DB after upsert)
    canonical_to_oid: dict[str, ObjectId] = {}
    for company_id in all_company_ids:
        oid = _to_object_id(company_id)
        if oid is None:
            continue
        doc = companies_collection.find_one({"_id": oid}, {"canonical_name": 1})
        if doc and doc.get("canonical_name"):
            canonical_to_oid[doc["canonical_name"]] = oid

    # Determine new companies pending enrichment
    result.new_company_ids = _find_companies_missing_slug(companies_collection, all_company_ids)

    # Optional scoring enqueue
    redis_client = None
    if config.enable_scoring_enqueue:
        redis_client = _connect_redis(config)

    for idx, card in enumerate(job_cards, start=1):
        if progress_callback:
            progress_callback(idx, estimated, f"Upserting job {idx}/{estimated}: {card.job_title}")

        if not text_matches_roles(card.job_title, card.description, roles):
            logger.debug(
                "crawler_levelsfyi: job %s (external_id=%s) does not match identity roles; skipping",
                card.job_title,
                card.external_job_id,
            )
            result.skipped_count += 1
            continue

        canonical = canonicalize_company_name(card.company_name) if card.company_name else ""
        company_oid = canonical_to_oid.get(canonical) if canonical else None

        if company_oid is None:
            # Create a minimal company record inline for jobs with no resolvable company name
            if card.company_name:
                dc = DiscoveredCompany(
                    name=card.company_name,
                    source=_PLATFORM,
                    role=card.role,
                    source_url=card.source_url,
                    domain=card.domain,
                )
                _, _, new_ids = upsert_companies(companies_collection, [dc])
                if new_ids:
                    company_oid = _to_object_id(new_ids[0])
                    if company_oid:
                        canonical_to_oid[canonical] = company_oid
                        if new_ids[0] not in result.new_company_ids:
                            missing = _find_companies_missing_slug(companies_collection, new_ids)
                            result.new_company_ids.extend(missing)

        if company_oid is None:
            logger.debug(
                "crawler_levelsfyi: skipping job %s (%s) — could not resolve company for %r",
                card.external_job_id,
                card.source_url,
                card.company_name,
            )
            result.skipped_count += 1
            continue

        try:
            job_id, was_inserted = _upsert_job(
                jobs_collection,
                job_title=card.job_title,
                description=card.description,
                location=card.location,
                external_job_id=card.external_job_id,
                source_url=card.source_url,
                company_oid=company_oid,
            )
        except Exception as exc:
            logger.warning(
                "crawler_levelsfyi: upsert failed for job %s: %s",
                card.external_job_id,
                exc,
            )
            result.failed_urls.append({"url": card.source_url, "error": str(exc)})
            continue

        result.job_ids.append(job_id)
        if was_inserted:
            result.inserted_count += 1
        else:
            result.updated_count += 1

        if redis_client is not None and config.enable_scoring_enqueue:
            if _try_enqueue(redis_client, config, job_id):
                result.enqueued_count += 1
            else:
                result.enqueue_failed_count += 1

    if progress_callback:
        progress_callback(
            estimated,
            estimated,
            f"Completed: {result.inserted_count} inserted, {result.updated_count} updated, {result.skipped_count} skipped",
        )

    logger.info(
        "crawler_levelsfyi: done — discovered=%d inserted=%d updated=%d skipped=%d new_companies=%d",
        result.discovered_count,
        result.inserted_count,
        result.updated_count,
        result.skipped_count,
        len(result.new_company_ids),
    )
    return result
