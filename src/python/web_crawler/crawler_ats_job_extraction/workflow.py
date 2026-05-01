from __future__ import annotations

import json
import logging
import time
from typing import Callable, Iterable

import redis as redis_lib

import requests
from bson import ObjectId
from bson.errors import InvalidId
from google.protobuf.json_format import MessageToDict

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import WorkflowResult
from src.python.web_crawler.sources.ats_job_fetcher import fetch_jobs
from src.python.web_crawler.enrichment_ats_enrichment.workflow import _company_from_document
from src.python.web_crawler.role_filtering import load_identity_roles, text_matches_roles

logger = logging.getLogger(__name__)


def estimate_ats_job_extraction_checks(company_count: int) -> int:
    """
    Best-effort estimate for ATS job extraction work.

    We budget at least one extraction unit per ATS-enriched company.
    """
    return max(company_count, 1)


def _to_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _now_timestamp() -> dict:
    return {"seconds": int(time.time()), "nanos": 0}


def _build_job_document(job: common_pb2.Job, company_oid: ObjectId) -> dict:
    doc = MessageToDict(job, preserving_proto_field_name=True)
    doc.pop("id", None)
    doc.pop("company_info", None)
    doc["company_id"] = company_oid
    doc["created_at"] = _now_timestamp()
    doc["updated_at"] = _now_timestamp()
    return doc


def _try_enqueue(redis_client, config: CrawlerConfig, job_id: str, user_id: str, identity_id: str = "") -> bool:
    if not user_id:
        logger.warning("crawler_ats_job_extraction: missing user_id for scoring enqueue job_id=%s", job_id)
        return False
    try:
        msg: dict = {"job_id": job_id, "user_id": user_id}
        if identity_id:
            msg["identity_id"] = identity_id
        payload = json.dumps(msg)
        redis_client.rpush(config.job_scoring_queue_name, payload)
        return True
    except Exception as exc:
        logger.warning("crawler_ats_job_extraction: failed to enqueue job_id=%s: %s", job_id, exc)
        return False


def _connect_redis(config: CrawlerConfig):
    try:
        client = redis_lib.Redis(host=config.redis_host, port=config.redis_port, socket_connect_timeout=5)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("crawler_ats_job_extraction: Redis unavailable (%s); scoring enqueue disabled for this run", exc)
        return None


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
        doc = _build_job_document(job, company_oid)
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


def run_crawler_ats_job_extraction(
    database,
    config: CrawlerConfig,
    user_id: str = "",
    company_ids: list[str] | None = None,
    identity_id: str | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    *,
    identity_database,
) -> WorkflowResult:
    companies_collection = database["companies"]
    identities_collection = identity_database["identities"]
    jobs_collection = database["job-descriptions"]
    result = WorkflowResult()

    # Load identity roles for filtering
    if identity_id:
        identity_roles = load_identity_roles(
            identities_collection,
            identity_id,
            logger=logger,
            workflow_name="crawler_ats_job_extraction",
        )
    else:
        logger.info("crawler_ats_job_extraction: identity_id missing; skipping ATS extraction and emitting no jobs")
        identity_roles = []
    logger.debug("crawler_ats_job_extraction: role filtering loaded %d roles", len(identity_roles))

    if not identity_roles:
        logger.info(
            "crawler_ats_job_extraction: identity %s has no roles; skipping ATS extraction and emitting no jobs",
            identity_id,
        )
        if progress_callback:
            progress_callback(0, 1, "Skipping ATS extraction: identity has no configured roles")
        return result

    companies = _load_ats_companies(companies_collection, company_ids)
    logger.debug("crawler_ats_job_extraction: loaded %d ATS-enriched companies", len(companies))
    total_companies = len(companies)
    completed_checks = 0
    estimated_checks = estimate_ats_job_extraction_checks(total_companies)

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
                logger.warning("crawler_ats_job_extraction: invalid company _id %r, skipping", company_id)
                result.skipped_count += 1
                continue

            try:
                jobs = fetch_jobs(provider, slug, config, session)
                result.fetched_count += len(jobs)

                for job in jobs:
                    try:
                        # Filter job by identity roles before insertion
                        if not text_matches_roles(job.title, job.description, identity_roles):
                            logger.debug("crawler_ats_job_extraction: job %s (external_id=%s) does not match identity roles; skipping", job.title, job.external_job_id)
                            result.skipped_count += 1
                            continue

                        job_id, inserted = upsert_job(jobs_collection, job, company_oid)
                        result.job_ids.append(job_id)

                        if inserted:
                            result.inserted_count += 1
                        else:
                            result.updated_count += 1

                        if config.enable_scoring_enqueue and redis_client is not None:
                            if _try_enqueue(redis_client, config, job_id, user_id, identity_id=identity_id or ""):
                                result.enqueued_count += 1
                            else:
                                result.enqueue_failed_count += 1

                    except Exception as exc:
                        logger.exception("crawler_ats_job_extraction: failed to upsert job external_id=%s company=%s: %s", job.external_job_id, company_id, exc)
                        result.skipped_count += 1

            except Exception as exc:
                logger.exception("crawler_ats_job_extraction: failed for company %s (%s): %s", company_id, company_name, exc)
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
        "crawler_ats_job_extraction summary: fetched=%d inserted=%d updated=%d skipped=%d enqueued=%d enqueue_failed=%d failed_companies=%d",
        result.fetched_count,
        result.inserted_count,
        result.updated_count,
        result.skipped_count,
        result.enqueued_count,
        result.enqueue_failed_count,
        len(result.failed_companies),
    )
    return result
