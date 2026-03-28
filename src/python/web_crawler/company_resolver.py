from __future__ import annotations

import logging
import re
from collections import OrderedDict
from typing import Iterable

from bson import ObjectId
from google.protobuf.json_format import MessageToDict

from src.python.ai_querier import common_pb2
from src.python.web_crawler.models import DiscoveredCompany

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
        company_data["field"] = ObjectId(company_data.pop("field_id"))
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
                update["$set"]["field"] = document["field"]
            collection.update_one({"_id": existing["_id"]}, update)
            updated_count += 1
            company_ids.append(str(existing["_id"]))
            continue

        logger.debug("inserting new company canonical_name=%r", canonical_name)
        insert_result = collection.insert_one(document)
        inserted_count += 1
        company_ids.append(str(insert_result.inserted_id))

    return inserted_count, updated_count, company_ids