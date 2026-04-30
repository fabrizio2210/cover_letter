from __future__ import annotations

import json
import logging
import time
from typing import Callable

import redis
from bson import ObjectId
from bson.errors import InvalidId

from src.python.ai_querier import common_pb2
from src.python.web_crawler.company_resolver import canonicalize_company_name, upsert_companies
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany, WorkflowResult
from src.python.web_crawler.progress import utc_timestamp
from src.python.web_crawler.role_filtering import load_identity_roles, text_matches_roles
from src.python.web_crawler.crawler_4dayweek.fourdayweek import FourDayWeekAdapter, FourDayWeekJobCard
from src.python.web_crawler.workflow_messages import company_discovery_event_to_json

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_WORKFLOW_ID = "crawler_4dayweek"
_PLATFORM = "4dayweek"


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


def _try_enqueue(redis_client, config: CrawlerConfig, job_id: str, user_id: str) -> bool:
    if not user_id:
        logger.warning("crawler_4dayweek: missing user_id for scoring enqueue job_id=%s", job_id)
        return False
    try:
        payload = json.dumps({"job_id": job_id, "user_id": user_id})
        redis_client.rpush(config.job_scoring_queue_name, payload)
        return True
    except Exception as exc:
        logger.warning("crawler_4dayweek: failed to enqueue job_id=%s: %s", job_id, exc)
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
        logger.warning("crawler_4dayweek: Redis unavailable (%s); scoring enqueue disabled for this run", exc)
        return None


def _emit_enrichment_events(
    redis_client: redis.Redis,
    config: CrawlerConfig,
    *,
    run_id: str,
    workflow_run_id: str,
    identity_id: str,
    company_ids: list[str],
) -> None:
    for company_id in company_ids:
        try:
            event = common_pb2.CompanyDiscoveryEvent(
                run_id=run_id,
                workflow_run_id=workflow_run_id,
                workflow_id=_WORKFLOW_ID,
                identity_id=identity_id,
                company_id=company_id,
                reason="new_company_via_4dayweek",
            )
            event.emitted_at.CopyFrom(utc_timestamp())
            redis_client.rpush(
                config.crawler_enrichment_ats_enrichment_queue_name,
                company_discovery_event_to_json(event),
            )
        except Exception as exc:
            logger.warning("crawler_4dayweek: failed to emit CompanyDiscoveryEvent for company %s: %s", company_id, exc)


def _build_company_lookup(companies_collection, company_ids: list[str]) -> dict[str, ObjectId]:
    canonical_to_oid: dict[str, ObjectId] = {}
    for company_id in company_ids:
        oid = _to_object_id(company_id)
        if oid is None:
            continue
        doc = companies_collection.find_one({"_id": oid}, {"canonical_name": 1})
        if doc and doc.get("canonical_name"):
            canonical_to_oid[str(doc["canonical_name"])] = oid
    return canonical_to_oid


def _merge_company_lookup(
    companies_collection,
    canonical_names: list[str],
    company_ids: list[str],
) -> dict[str, ObjectId]:
    canonical_to_oid = _build_company_lookup(companies_collection, company_ids)
    for canonical_name, company_id in zip(canonical_names, company_ids):
        if canonical_name in canonical_to_oid:
            continue
        oid = _to_object_id(company_id)
        if oid is not None:
            canonical_to_oid[canonical_name] = oid
    return canonical_to_oid


def _unique_company_names(cards: list[FourDayWeekJobCard]) -> dict[str, FourDayWeekJobCard]:
    result: dict[str, FourDayWeekJobCard] = {}
    for card in cards:
        canonical = canonicalize_company_name(card.company_name) if card.company_name else ""
        if canonical and canonical not in result:
            result[canonical] = card
    return result


def run_crawler_4dayweek(
    database,
    config: CrawlerConfig,
    identity_id: str,
    user_id: str = "",
    progress_callback: Callable[[int, int, str], None] | None = None,
    *,
    identity_database,
) -> WorkflowResult:
    identities_collection = identity_database["identities"]
    companies_collection = database["companies"]
    jobs_collection = database["jobs"]

    result = WorkflowResult()
    roles = load_identity_roles(
        identities_collection,
        identity_id,
        logger=logger,
        workflow_name=_WORKFLOW_ID,
    )
    if not roles:
        logger.info("crawler_4dayweek: identity %s has no roles; emitting no jobs", identity_id)
        if progress_callback:
            progress_callback(0, 1, "Skipping: identity has no configured roles")
        return result

    adapter = FourDayWeekAdapter()
    if progress_callback:
        progress_callback(0, 1, "Fetching job listings from 4dayweek")

    job_cards = adapter.discover_jobs(config)
    result.discovered_count = len(job_cards)
    logger.debug(
        "crawler_4dayweek: discovered %d job cards for identity %s across %d roles",
        result.discovered_count,
        identity_id,
        len(roles),
    )
    if not job_cards:
        if progress_callback:
            progress_callback(1, 1, "No jobs discovered from 4dayweek")
        return result

    estimated = max(len(job_cards), 1)
    company_map = _unique_company_names(job_cards)
    logger.debug(
        "crawler_4dayweek: deduplicated %d unique companies from %d discovered job cards",
        len(company_map),
        result.discovered_count,
    )
    discovered_companies: list[DiscoveredCompany] = []
    discovered_canonical_names: list[str] = []
    existing_canonicals: set[str] = set()
    for canonical, card in company_map.items():
        existing = companies_collection.find_one({"canonical_name": canonical}, {"_id": 1})
        if existing is not None:
            existing_canonicals.add(canonical)
        discovered_canonical_names.append(canonical)
        discovered_companies.append(
            DiscoveredCompany(
                name=card.company_name,
                source=_PLATFORM,
                role=card.role,
                source_url=card.source_url,
                domain=card.company_domain,
            )
        )

    _, _, all_company_ids = upsert_companies(companies_collection, discovered_companies)
    canonical_to_oid = _merge_company_lookup(companies_collection, discovered_canonical_names, all_company_ids)
    result.new_company_ids = []
    for canonical, oid in canonical_to_oid.items():
        if canonical not in existing_canonicals:
            result.new_company_ids.append(str(oid))

    redis_client = None
    if config.enable_scoring_enqueue:
        redis_client = _connect_redis(config)

    for index, card in enumerate(job_cards, start=1):
        if progress_callback:
            progress_callback(index, estimated, f"Upserting job {index}/{estimated}: {card.job_title}")

        if not text_matches_roles(card.job_title, card.description, roles):
            logger.debug(
                "crawler_4dayweek: job %s (external_id=%s) does not match identity roles; skipping",
                card.job_title,
                card.external_job_id,
            )
            result.skipped_count += 1
            continue

        canonical = canonicalize_company_name(card.company_name) if card.company_name else ""
        company_oid = canonical_to_oid.get(canonical) if canonical else None

        if company_oid is None and card.company_name:
            existing = companies_collection.find_one({"canonical_name": canonical}, {"_id": 1})
            if existing is not None:
                company_oid = existing["_id"]
                canonical_to_oid[canonical] = company_oid
            else:
                _, _, new_ids = upsert_companies(
                    companies_collection,
                    [
                        DiscoveredCompany(
                            name=card.company_name,
                            source=_PLATFORM,
                            role=card.role,
                            source_url=card.source_url,
                            domain=card.company_domain,
                        )
                    ],
                )
                if new_ids:
                    company_oid = _to_object_id(new_ids[0])
                    if company_oid is not None:
                        canonical_to_oid[canonical] = company_oid
                        if new_ids[0] not in result.new_company_ids:
                            result.new_company_ids.append(new_ids[0])

        if company_oid is None:
            logger.debug(
                "crawler_4dayweek: skipping job %s (%s) - could not resolve company for %r",
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
            logger.warning("crawler_4dayweek: upsert failed for job %s: %s", card.external_job_id, exc)
            result.failed_urls.append({"url": card.source_url, "error": str(exc)})
            continue

        result.job_ids.append(job_id)
        if was_inserted:
            result.inserted_count += 1
        else:
            result.updated_count += 1

        if redis_client is not None and config.enable_scoring_enqueue:
            if _try_enqueue(redis_client, config, job_id, user_id):
                result.enqueued_count += 1
            else:
                result.enqueue_failed_count += 1

    if progress_callback:
        progress_callback(
            estimated,
            estimated,
            (
                f"Completed: {result.inserted_count} inserted, {result.updated_count} updated, "
                f"{result.skipped_count} skipped"
            ),
        )

    logger.debug(
        "crawler_4dayweek summary: discovered=%d inserted=%d updated=%d skipped=%d enqueued=%d enqueue_failed=%d upsert_failed=%d new_companies=%d",
        result.discovered_count,
        result.inserted_count,
        result.updated_count,
        result.skipped_count,
        result.enqueued_count,
        result.enqueue_failed_count,
        len(result.failed_urls),
        len(result.new_company_ids),
    )

    return result
