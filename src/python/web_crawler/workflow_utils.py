from __future__ import annotations

import logging

import redis
from bson import ObjectId

from src.python.ai_querier import common_pb2
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.progress import utc_timestamp
from src.python.web_crawler.workflow_messages import company_discovery_event_to_json

logger = logging.getLogger(__name__)

def load_identity_seed(identities_collection, identity_id: str) -> common_pb2.Identity:
    if not identity_id:
        raise ValueError("identity_id is required")

    logger.debug("loading identity: %s", identity_id)
    identity = identities_collection.find_one({"_id": ObjectId(identity_id)})
    if identity is None:
        raise ValueError(f"identity {identity_id} not found")
    logger.debug("raw identity document: %s", identity)

    roles = [role.strip() for role in identity.get("roles", []) if isinstance(role, str) and role.strip()]
    logger.debug("resolved roles: %s", roles)
    if not roles:
        raise ValueError(f"identity {identity_id} has no roles")

    field_id = None
    if identity.get("field_id") is not None:
        field_id = str(identity["field_id"])
    logger.debug("field_id: %s", field_id)

    seed = common_pb2.Identity(id=identity_id, roles=roles)
    if field_id:
        seed.field_id = field_id
    return seed

def find_companies_missing_slug(collection, company_ids: list[str]) -> list[str]:
    """Return a subset of company_ids whose documents have no ats_slug set."""
    if not company_ids:
        return []
    object_ids = []
    for cid in company_ids:
        try:
            object_ids.append(ObjectId(cid))
        except Exception:
            continue
    if not object_ids:
        return []
    docs = list(
        collection.find(
            {"_id": {"$in": object_ids}, "$or": [{"ats_slug": {"$exists": False}}, {"ats_slug": ""}]},
            {"_id": 1},
        )
    )
    return [str(doc["_id"]) for doc in docs]

def emit_enrichment_events(
    redis_client: redis.Redis,
    config: CrawlerConfig,
    *,
    run_id: str,
    workflow_run_id: str,
    workflow_id: str,
    identity_id: str,
    company_ids: list[str],
) -> None:
    """Push one CompanyDiscoveryEvent per company into the enrichment queue."""
    for company_id in company_ids:
        try:
            event = common_pb2.CompanyDiscoveryEvent(
                run_id=run_id,
                workflow_run_id=workflow_run_id,
                workflow_id=workflow_id,
                identity_id=identity_id,
                company_id=company_id,
                reason="no_ats_slug",
            )
            event.emitted_at.CopyFrom(utc_timestamp())
            redis_client.rpush(
                config.crawler_enrichment_ats_enrichment_queue_name,
                company_discovery_event_to_json(event),
            )
            logger.debug("emitted CompanyDiscoveryEvent for company %s", company_id)
        except Exception as exc:
            logger.warning(
                "failed to emit CompanyDiscoveryEvent for company %s: %s",
                company_id,
                exc,
            )
