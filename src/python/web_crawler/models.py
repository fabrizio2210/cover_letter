from __future__ import annotations

from dataclasses import dataclass, field

from src.python.ai_querier import common_pb2


DiscoveredCompany = common_pb2.DiscoveredCompany


@dataclass(slots=True)
class Workflow1Result:
    discovered_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_sources: list[dict[str, str]] = field(default_factory=list)
    company_ids: list[str] = field(default_factory=list)