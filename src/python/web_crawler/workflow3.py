from __future__ import annotations

import json
import logging
import time
from typing import Callable, Iterable

import requests
from bson import ObjectId
from bson.errors import InvalidId
from google.protobuf.json_format import MessageToDict

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig, JOB_SCORING_QUEUE
from src.python.web_crawler.models import Workflow3Result
from src.python.web_crawler.sources.ats_job_fetcher import fetch_jobs
from src.python.web_crawler.workflow2 import _company_from_document

logger = logging.getLogger(__name__)

_SCORING_STATUS_BSON: dict[int, str] = {
    common_pb2.SCORING_STATUS_UNSCORED: "unscored",
    common_pb2.SCORING_STATUS_QUEUED: "queued",
    common_pb2.SCORING_STATUS_SCORED: "scored",
    common_pb2.SCORING_STATUS_FAILED: "failed",
    common_pb2.SCORING_STATUS_SKIPPED: "skipped",
}


def estimate_workflow3_job_checks(company_count: int) -> int:
    """
    Best-effort estimate for workflow3 extraction work.

    We budget at least one extraction unit per ATS-enriched company.
    """
    return max(company_count, 1)


def _scoring_status_to_bson(status: int) -> str:
    return _SCORING_STATUS_BSON.get(status, "unscored")


def _to_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _now_timestamp() -> dict:
    return {"seconds": int(time.time()), "nanos": 0}


def _build_job_document(job: common_pb2.Job, company_oid: ObjectId, scoring_status: str) -> dict:
    doc = MessageToDict(job, preserving_proto_field_name=True)
    doc.pop("id", None)
    doc.pop("company_info", None)
    # scoring_status is an enum: MessageToDict serializes it as the enum name string,
    # but we store the lowercase BSON string via _scoring_status_to_bson().
    doc.pop("scoring_status", None)
    # weighted_score defaults to 0.0 and may be omitted by MessageToDict; set explicitly.
    doc.pop("weighted_score", None)
    doc["company_id"] = company_oid
    doc["created_at"] = _now_timestamp()
    doc["updated_at"] = _now_timestamp()
    doc["scoring_status"] = scoring_status
    doc["weighted_score"] = 0
    return doc


def _try_enqueue(redis_client, job_id: str) -> bool:
    try:
        payload = json.dumps({"job_id": job_id})
        redis_client.rpush(JOB_SCORING_QUEUE, payload)
        return True
    except Exception as exc:
        logger.warning("workflow3: failed to enqueue job_id=%s: %s", job_id, exc)
        return False


def _connect_redis(config: CrawlerConfig):
    try:
        import redis as redis_lib
        client = redis_lib.Redis(host=config.redis_host, port=config.redis_port, socket_connect_timeout=5)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("workflow3: Redis unavailable (%s); scoring enqueue disabled for this run", exc)
        return None


def _load_identity_roles(identities_collection, identity_id: str) -> list[str]:
    """
    Load identity document and extract roles list.
    
    Returns list of role keywords. Empty list if identity not found or has no roles.
    """
    if not identity_id:
        logger.warning("workflow3: identity_id is empty; role filtering disabled")
        return []
    
    try:
        identity_oid = _to_object_id(identity_id)
        if identity_oid is None:
            logger.warning("workflow3: invalid identity_id %r; role filtering disabled", identity_id)
            return []
        
        identity = identities_collection.find_one({"_id": identity_oid})
        if identity is None:
            logger.warning("workflow3: identity %s not found; role filtering disabled", identity_id)
            return []
        
        roles = [role.strip() for role in identity.get("roles", []) if isinstance(role, str) and role.strip()]
        logger.debug("workflow3: loaded %d roles from identity %s: %s", len(roles), identity_id, roles)
        return roles
    except Exception as exc:
        logger.exception("workflow3: failed to load identity %s: %s", identity_id, exc)
        return []


def _job_matches_roles(job: common_pb2.Job, roles: list[str]) -> bool:
    """
    Check if job title or description matches any role keyword.
    
    Matching is case-insensitive substring search.
    Empty roles list accepts all jobs (pass-through).
    """
    if not roles:
        return True
    
    title_lower = job.title.lower()
    description_lower = job.description.lower()
    
    for role in roles:
        role_lower = role.lower()
        if role_lower in title_lower or role_lower in description_lower:
            return True
    
    return False


def upsert_job(
    jobs_collection,
    job: common_pb2.Job,
    company_oid: ObjectId,
) -> tuple[str, bool]:
    """
    Insert or update a job document.

    Returns (job_id_hex, was_inserted).
    """
    existing = jobs_collection.find_one(
        {"platform": job.platform, "external_job_id": job.external_job_id},
        {"_id": 1},
    )

    if existing is None:
        doc = _build_job_document(job, company_oid, _scoring_status_to_bson(common_pb2.SCORING_STATUS_UNSCORED))
        insert_result = jobs_collection.insert_one(doc)
        return str(insert_result.inserted_id), True
    else:
        job_id = str(existing["_id"])
        jobs_collection.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "title": job.title,
                    "description": job.description,
                    "location": job.location,
                    "source_url": job.source_url,
                    "updated_at": _now_timestamp(),
                }
            },
        )
        return job_id, False


def _load_ats_companies(companies_collection, company_ids: Iterable[str] | None) -> list[common_pb2.Company]:
    ats_filter = {"ats_provider": {"$exists": True, "$ne": ""}, "ats_slug": {"$exists": True, "$ne": ""}}

    if company_ids is not None:
        object_ids = [oid for cid in company_ids if (oid := _to_object_id(cid)) is not None]
        if not object_ids:
            return []
        query = {"_id": {"$in": object_ids}, **ats_filter}
    else:
        query = ats_filter

    docs = list(companies_collection.find(query, {"_id": 1, "name": 1, "ats_provider": 1, "ats_slug": 1}))
    return [_company_from_document(doc) for doc in docs]


def run_workflow3(
    database,
    config: CrawlerConfig,
    company_ids: list[str] | None = None,
    identity_id: str | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> Workflow3Result:
    companies_collection = database["companies"]
    identities_collection = database["identities"]
    jobs_collection = database["jobs"]
    result = Workflow3Result()

    # Load identity roles for filtering
    identity_roles = _load_identity_roles(identities_collection, identity_id) if identity_id else []
    logger.debug("workflow3: role filtering enabled with %d roles", len(identity_roles))

    companies = _load_ats_companies(companies_collection, company_ids)
    logger.debug("workflow3: loaded %d ATS-enriched companies", len(companies))
    total_companies = len(companies)
    completed_checks = 0
    estimated_checks = estimate_workflow3_job_checks(total_companies)

    if progress_callback:
        progress_callback(
            completed_checks,
            estimated_checks,
            f"Preparing job extraction for {total_companies} ATS-enriched companies",
        )

    redis_client = None
    if config.enable_scoring_enqueue:
        redis_client = _connect_redis(config)

    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})

    try:
        for company_index, company in enumerate(companies, start=1):
            company_id = company.id
            company_name = company.name
            provider = company.ats_provider
            slug = company.ats_slug

            if progress_callback:
                progress_callback(
                    completed_checks,
                    estimated_checks,
                    f"Fetching jobs for company {company_index}/{total_companies}: {company_name or company_id}",
                )

            company_oid = _to_object_id(company_id)
            if company_oid is None:
                logger.warning("workflow3: invalid company _id %r, skipping", company_id)
                result.skipped_count += 1
                continue

            try:
                jobs = fetch_jobs(provider, slug, config, session)
                result.fetched_count += len(jobs)

                for job in jobs:
                    try:
                        # Filter job by identity roles before insertion
                        if not _job_matches_roles(job, identity_roles):
                            logger.debug("workflow3: job %s (external_id=%s) does not match identity roles; skipping", job.title, job.external_job_id)
                            result.skipped_count += 1
                            continue

                        job_id, inserted = upsert_job(jobs_collection, job, company_oid)
                        result.job_ids.append(job_id)

                        if inserted:
                            result.inserted_count += 1
                        else:
                            result.updated_count += 1

                        if config.enable_scoring_enqueue and redis_client is not None:
                            job_oid = _to_object_id(job_id)
                            if _try_enqueue(redis_client, job_id):
                                result.enqueued_count += 1
                                if job_oid is not None:
                                    jobs_collection.update_one({"_id": job_oid}, {"$set": {"scoring_status": _scoring_status_to_bson(common_pb2.SCORING_STATUS_QUEUED)}})
                            else:
                                result.enqueue_failed_count += 1
                                if job_oid is not None:
                                    jobs_collection.update_one({"_id": job_oid}, {"$set": {"scoring_status": _scoring_status_to_bson(common_pb2.SCORING_STATUS_FAILED)}})

                    except Exception as exc:
                        logger.exception("workflow3: failed to upsert job external_id=%s company=%s: %s", job.external_job_id, company_id, exc)
                        result.skipped_count += 1

            except Exception as exc:
                logger.exception("workflow3: failed for company %s (%s): %s", company_id, company_name, exc)
                result.failed_companies.append({"company_id": company_id, "company_name": company_name, "error": str(exc)})
            finally:
                completed_checks += 1
                if progress_callback:
                    progress_callback(
                        completed_checks,
                        estimated_checks,
                        f"Workflow3 progress: {company_index}/{total_companies} companies processed",
                    )

    finally:
        session.close()

    logger.debug(
        "workflow3 summary: fetched=%d inserted=%d updated=%d skipped=%d enqueued=%d enqueue_failed=%d failed_companies=%d",
        result.fetched_count,
        result.inserted_count,
        result.updated_count,
        result.skipped_count,
        result.enqueued_count,
        result.enqueue_failed_count,
        len(result.failed_companies),
    )
    return result
