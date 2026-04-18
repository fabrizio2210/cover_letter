from __future__ import annotations

import logging

from bson import ObjectId

from src.python.ai_querier import common_pb2
from src.python.web_crawler.company_resolver import deduplicate_companies, upsert_companies
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import Workflow1Result
from src.python.web_crawler.sources.base import SourceAdapter
from src.python.web_crawler.sources.hackernews import HackerNewsAdapter
from src.python.web_crawler.sources.levelsfyi import LevelsFyiAdapter
from src.python.web_crawler.sources.ycombinator import YCombinatorAdapter

logger = logging.getLogger(__name__)


def get_enabled_adapters(enabled_sources: list[str] | None) -> list[SourceAdapter]:
    source_map: dict[str, SourceAdapter] = {
        "hackernews": HackerNewsAdapter(),
        "levelsfyi": LevelsFyiAdapter(),
        "ycombinator": YCombinatorAdapter(),
    }

    if not enabled_sources:
        return [YCombinatorAdapter()]

    adapters: list[SourceAdapter] = []
    for source_name in enabled_sources:
        adapter = source_map.get(source_name)
        if adapter:
            adapters.append(adapter)
    return adapters


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


def run_workflow1(database, config: CrawlerConfig, identity_id: str) -> Workflow1Result:
    identities_collection = database["identities"]
    companies_collection = database["companies"]
    seed = load_identity_seed(identities_collection, identity_id)
    logger.debug("seed roles: %s", list(seed.roles))

    adapters = get_enabled_adapters(config.enabled_sources)
    logger.debug("enabled adapters: %s", [a.source_name for a in adapters])

    result = Workflow1Result()
    discovered_companies = []

    for adapter in adapters:
        logger.debug("running adapter: %s", adapter.source_name)
        try:
            companies = adapter.discover_companies(list(seed.roles), config)
            logger.debug("adapter %s returned %d companies", adapter.source_name, len(companies))
            discovered_companies.extend(companies)
        except Exception as exc:
            logger.exception("adapter %s failed: %s", adapter.source_name, exc)
            result.failed_sources.append({"source": adapter.source_name, "error": str(exc)})

    logger.debug("total raw discovered: %d", len(discovered_companies))
    deduped_companies = deduplicate_companies(discovered_companies)
    logger.debug("after dedup: %d", len(deduped_companies))
    result.discovered_count = len(deduped_companies)
    result.skipped_count = max(len(discovered_companies) - len(deduped_companies), 0)

    inserted_count, updated_count, company_ids = upsert_companies(
        companies_collection,
        deduped_companies,
        field_id=seed.field_id or None,
    )
    result.inserted_count = inserted_count
    result.updated_count = updated_count
    result.company_ids = company_ids
    logger.debug("upsert done — inserted: %d, updated: %d", inserted_count, updated_count)
    return result