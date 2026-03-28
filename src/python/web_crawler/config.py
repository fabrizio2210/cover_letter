from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(slots=True)
class CrawlerConfig:
    mongo_host: str
    db_name: str
    http_timeout_seconds: int = 20
    max_retries: int = 3
    base_delay_ms: int = 1500
    max_delay_ms: int = 15000
    user_agent: str = DEFAULT_USER_AGENT
    enabled_sources: list[str] | None = None
    yc_hits_per_page: int = 100
    yc_max_companies: int = 500
    yc_max_companies_per_role: int | None = None

    @classmethod
    def from_env(cls) -> "CrawlerConfig":
        enabled_sources = _parse_csv(os.getenv("CRAWLER_ENABLED_SOURCES", "ycombinator"))
        return cls(
            mongo_host=os.getenv("MONGO_HOST", "mongodb://localhost:27017/"),
            db_name=os.getenv("DB_NAME", "cover_letter"),
            http_timeout_seconds=int(os.getenv("CRAWLER_HTTP_TIMEOUT_SECONDS", "20")),
            max_retries=int(os.getenv("CRAWLER_MAX_RETRIES", "3")),
            base_delay_ms=int(os.getenv("CRAWLER_BASE_DELAY_MS", "1500")),
            max_delay_ms=int(os.getenv("CRAWLER_MAX_DELAY_MS", "15000")),
            user_agent=os.getenv("CRAWLER_USER_AGENT", DEFAULT_USER_AGENT),
            enabled_sources=enabled_sources,
            yc_hits_per_page=max(1, min(int(os.getenv("CRAWLER_YC_HITS_PER_PAGE", "100")), 1000)),
            yc_max_companies=max(1, int(os.getenv("CRAWLER_YC_MAX_COMPANIES", "500"))),
            yc_max_companies_per_role=max(1, int(os.getenv("CRAWLER_YC_MAX_COMPANIES_PER_ROLE"))) if os.getenv("CRAWLER_YC_MAX_COMPANIES_PER_ROLE") else None,
        )