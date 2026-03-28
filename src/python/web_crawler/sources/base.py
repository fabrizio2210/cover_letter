from __future__ import annotations

from abc import ABC, abstractmethod

from src.python.web_crawler.config import CrawlerConfig
from src.python.web_crawler.models import DiscoveredCompany


class SourceAdapter(ABC):
    source_name: str

    @abstractmethod
    def discover_companies(self, roles: list[str], config: CrawlerConfig) -> list[DiscoveredCompany]:
        raise NotImplementedError