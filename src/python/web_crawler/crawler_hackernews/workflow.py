from __future__ import annotations

import logging

from src.python.web_crawler.company_resolver import deduplicate_companies, upsert_companies
from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.workflow_utils import (
    find_companies_missing_slug as _find_companies_missing_slug,
    load_identity_seed,
)
from src.python.web_crawler.models import WorkflowResult
from src.python.web_crawler.sources.hackernews import HackerNewsAdapter

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_WORKFLOW_ID = "crawler_hackernews"


def run_crawler_hackernews(database, config: CrawlerConfig, identity_id: str) -> WorkflowResult:
    identities_collection = database["identities"]
    companies_collection = database["companies"]
    seed = load_identity_seed(identities_collection, identity_id)
    logger.debug("seed roles: %s", list(seed.roles))

    adapter = HackerNewsAdapter()
    logger.debug("running adapter: %s", adapter.source_name)

    result = WorkflowResult()
    discovered_companies = []

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

    result.enrichment_pending_company_ids = _find_companies_missing_slug(companies_collection, company_ids)
    logger.debug(
        "companies pending enrichment (no ats_slug): %d",
        len(result.enrichment_pending_company_ids),
    )
    return result
