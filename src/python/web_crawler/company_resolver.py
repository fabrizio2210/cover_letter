from __future__ import annotations

import logging
import re
from collections import OrderedDict
from typing import Iterable
from urllib.parse import urlparse

from bson import ObjectId
from google.protobuf.json_format import MessageToDict

from src.python.ai_querier import common_pb2
from src.python.web_crawler.models import DiscoveredCompany
from src.python.web_crawler.sources.ats_slug_resolver import extract_slug_from_url

logger = logging.getLogger(__name__)


_LEGAL_SUFFIXES = {
    "inc",
    "inc.",
    "llc",
    "ltd",
    "ltd.",
    "limited",
    "corp",
    "corp.",
    "corporation",
    "company",
    "co",
    "co.",
    "gmbh",
    "srl",
    "spa",
    "plc",
    "ag",
    "bv",
    "oy",
}

_ATS_HOST_PROVIDER_MAP = {
    "boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
}


def _extract_ats_from_url(url: str) -> tuple[str, str] | None:
    if not url:
        return None

    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc.casefold().removeprefix("www.")
    provider = _ATS_HOST_PROVIDER_MAP.get(host)
    if not provider:
        return None

    slug = extract_slug_from_url(parsed.geturl(), provider)
    if not slug:
        return None

    return provider, slug


def _extract_ats_from_company_document(document: dict) -> tuple[str, str] | None:
    discovery_sources = document.get("discovery_sources", [])
    if not isinstance(discovery_sources, list):
        return None

    for source in discovery_sources:
        if not isinstance(source, dict):
            continue
        for key in ("careers_url", "source_url", "domain"):
            value = str(source.get(key) or "").strip()
            metadata = _extract_ats_from_url(value)
            if metadata:
                return metadata

    return None


def canonicalize_company_name(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s]", " ", name.casefold())
    tokens = [token for token in normalized.split() if token and token not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


def deduplicate_companies(companies: Iterable[DiscoveredCompany]) -> list[DiscoveredCompany]:
    deduped: OrderedDict[str, DiscoveredCompany] = OrderedDict()
    for company in companies:
        canonical_name = canonicalize_company_name(company.name)
        if not canonical_name:
            logger.debug("dropping company with empty canonical name: %r", company.name)
            continue
        if canonical_name not in deduped:
            deduped[canonical_name] = company
            continue

        logger.debug("merging duplicate canonical_name=%r (incoming name=%r)", canonical_name, company.name)
        existing = deduped[canonical_name]
        if not existing.description and company.description:
            existing.description = company.description
        if not existing.domain and company.domain:
            existing.domain = company.domain
        if not existing.careers_url and company.careers_url:
            existing.careers_url = company.careers_url

    logger.debug("deduplicate_companies: %d unique companies", len(deduped))
    return list(deduped.values())


def build_company_document(company: DiscoveredCompany, field_id: str | None = None) -> dict:
    company_source_proto = common_pb2.CompanyDiscoverySource(
        source=company.source,
        role=company.role,
        source_url=company.source_url,
        careers_url=company.careers_url,
        domain=company.domain,
    )
    company_proto = common_pb2.Company(
        name=company.name.strip(),
        description=company.description.strip(),
        discovery_sources=[company_source_proto],
    )
    if field_id:
        company_proto.field_id = field_id

    company_data = MessageToDict(
        company_proto,
        preserving_proto_field_name=True,
    )
    company_data.setdefault("description", company.description.strip())
    company_data.setdefault("discovery_sources", [])
    if "field_id" in company_data:
        company_data["field_id"] = ObjectId(company_data["field_id"])
    return company_data


def upsert_companies(collection, companies: Iterable[DiscoveredCompany], field_id: str | None = None) -> tuple[int, int, list[str]]:
    inserted_count = 0
    updated_count = 0
    company_ids: list[str] = []

    for company in deduplicate_companies(companies):
        canonical_name = canonicalize_company_name(company.name)
        if not canonical_name:
            logger.debug("upsert: skipping company with empty canonical name: %r", company.name)
            continue

        existing = collection.find_one({"canonical_name": canonical_name})
        document = build_company_document(company, field_id=field_id)
        document["canonical_name"] = canonical_name

        discovered_ats = _extract_ats_from_company_document(document)
        if discovered_ats:
            document["ats_provider"] = discovered_ats[0]
            document["ats_slug"] = discovered_ats[1]

        if existing:
            logger.debug("updating existing company canonical_name=%r _id=%s", canonical_name, existing["_id"])
            existing_sources = existing.get("discovery_sources", [])
            merged_sources = existing_sources + [src for src in document["discovery_sources"] if src not in existing_sources]
            update = {
                "$set": {
                    "name": existing.get("name") or document["name"],
                    "canonical_name": canonical_name,
                    "description": document["description"] or existing.get("description", ""),
                    "discovery_sources": merged_sources,
                }
            }
            if field_id:
                update["$set"]["field_id"] = document["field_id"]

            if not existing.get("ats_provider") or not existing.get("ats_slug"):
                merged_document = dict(existing)
                merged_document["discovery_sources"] = merged_sources
                merged_ats = _extract_ats_from_company_document(merged_document)
                if merged_ats:
                    update["$set"]["ats_provider"] = merged_ats[0]
                    update["$set"]["ats_slug"] = merged_ats[1]

            collection.update_one({"_id": existing["_id"]}, update)
            updated_count += 1
            company_ids.append(str(existing["_id"]))
            continue

        logger.debug("inserting new company canonical_name=%r", canonical_name)
        insert_result = collection.insert_one(document)
        inserted_count += 1
        company_ids.append(str(insert_result.inserted_id))

    return inserted_count, updated_count, company_ids