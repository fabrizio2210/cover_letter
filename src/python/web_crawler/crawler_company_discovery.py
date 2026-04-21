from __future__ import annotations

import argparse
import logging
import time
from typing import cast

from bson import ObjectId
import redis

from src.python.ai_querier import common_pb2
from src.python.web_crawler.company_resolver import deduplicate_companies, upsert_companies
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.db import get_database
from src.python.web_crawler.models import CompanyDiscoveryResult
from src.python.web_crawler.progress import publish_progress, utc_timestamp
from src.python.web_crawler.sources.base import SourceAdapter
from src.python.web_crawler.sources.hackernews import HackerNewsAdapter
from src.python.web_crawler.sources.levelsfyi import LevelsFyiAdapter
from src.python.web_crawler.sources.ycombinator import YCombinatorAdapter
from src.python.web_crawler.workflow_messages import parse_workflow_dispatch

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_WORKFLOW_ID = "crawler_company_discovery"


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


def run_crawler_company_discovery(database, config: CrawlerConfig, identity_id: str) -> CompanyDiscoveryResult:
    identities_collection = database["identities"]
    companies_collection = database["companies"]
    seed = load_identity_seed(identities_collection, identity_id)
    logger.debug("seed roles: %s", list(seed.roles))

    adapters = get_enabled_adapters(config.enabled_sources)
    logger.debug("enabled adapters: %s", [a.source_name for a in adapters])

    result = CompanyDiscoveryResult()
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


def _connect_redis(config: CrawlerConfig) -> redis.Redis:
    client = redis.Redis(host=config.redis_host, port=config.redis_port, socket_connect_timeout=5, decode_responses=True)
    client.ping()
    return client


def consumer_main(config: CrawlerConfig) -> None:
    redis_client: redis.Redis | None = None

    while True:
        try:
            if redis_client is None:
                redis_client = _connect_redis(config)
                logger.info("connected to redis at %s:%s", config.redis_host, config.redis_port)

            queue_item = cast(
                tuple[str, str] | None,
                redis_client.blpop([config.crawler_workflow_dispatch_queue_name], timeout=0),
            )
            if not queue_item:
                continue

            _, raw_payload = queue_item
            try:
                message = parse_workflow_dispatch(raw_payload)
            except Exception as exc:
                logger.warning("invalid workflow dispatch payload: %s", exc)
                continue

            if message.workflow_id != _WORKFLOW_ID:
                logger.debug("skipping workflow_id=%s (not %s)", message.workflow_id, _WORKFLOW_ID)
                continue

            run_id = message.run_id.strip()
            workflow_run_id = message.workflow_run_id.strip()
            identity_id = message.identity_id.strip()

            if not run_id or not identity_id:
                logger.warning("dispatch message missing run_id or identity_id: %s", raw_payload)
                continue

            started_at = utc_timestamp()
            publish_progress(
                redis_client,
                config,
                run_id=run_id,
                identity_id=identity_id,
                status="running",
                workflow=_WORKFLOW_ID,
                estimated_total=1,
                completed=0,
                started_at=started_at,
                workflow_id=_WORKFLOW_ID,
                workflow_run_id=workflow_run_id,
            )

            try:
                database = get_database(config)
                run_crawler_company_discovery(database, config, identity_id)
                finished_at = utc_timestamp()
                publish_progress(
                    redis_client,
                    config,
                    run_id=run_id,
                    identity_id=identity_id,
                    status="completed",
                    workflow=_WORKFLOW_ID,
                    estimated_total=1,
                    completed=1,
                    started_at=started_at,
                    finished_at=finished_at,
                    message="Company discovery completed",
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=workflow_run_id,
                )
            except Exception as exc:
                logger.exception("crawler_company_discovery failed for identity %s: %s", identity_id, exc)
                finished_at = utc_timestamp()
                publish_progress(
                    redis_client,
                    config,
                    run_id=run_id,
                    identity_id=identity_id,
                    status="failed",
                    workflow=_WORKFLOW_ID,
                    estimated_total=1,
                    completed=0,
                    started_at=started_at,
                    finished_at=finished_at,
                    message="Company discovery failed",
                    reason="run_failed",
                    workflow_id=_WORKFLOW_ID,
                    workflow_run_id=workflow_run_id,
                )
        except Exception as exc:
            logger.warning("consumer loop error: %s", exc)
            redis_client = None
            time.sleep(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the crawler_company_discovery workflow consumer")
    parser.add_argument("--worker", action="store_true", required=True, help="Run as a long-lived Redis dispatch queue consumer")
    return parser


def main() -> None:
    build_parser().parse_args()
    config = CrawlerConfig.from_env()
    consumer_main(config)


if __name__ == "__main__":
    main()
