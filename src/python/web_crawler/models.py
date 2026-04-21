from __future__ import annotations

from dataclasses import dataclass, field

from src.python.ai_querier import common_pb2


DiscoveredCompany = common_pb2.DiscoveredCompany


@dataclass(slots=True)
class CompanyDiscoveryResult:
    discovered_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_sources: list[dict[str, str]] = field(default_factory=list)
    company_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Workflow2Result:
    enriched_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    ats_providers: dict[str, int] = field(default_factory=dict)
    failed_companies: list[dict[str, str]] = field(default_factory=list)
    company_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CrawlerAtsJobExtractionResult:
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    enqueued_count: int = 0
    enqueue_failed_count: int = 0
    failed_companies: list[dict[str, str]] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)