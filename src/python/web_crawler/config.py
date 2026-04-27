from __future__ import annotations

import os
from dataclasses import dataclass


JOB_SCORING_QUEUE = "job_scoring_queue"
CRAWLER_TRIGGER_QUEUE = "crawler_trigger_queue"
CRAWLER_PROGRESS_CHANNEL = "crawler_progress_channel"
CRAWLER_ATS_JOB_EXTRACTION_QUEUE = "crawler_ats_job_extraction_queue"
CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE = "enrichment_ats_enrichment_queue"
CRAWLER_LEVELSFYI_QUEUE = "crawler_levelsfyi_queue"
CRAWLER_YCOMBINATOR_QUEUE = "crawler_ycombinator_queue"
CRAWLER_HACKERNEWS_QUEUE = "crawler_hackernews_queue"
CRAWLER_4DAYWEEK_QUEUE = "crawler_4dayweek_queue"
CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE = "enrichment_retiring_jobs_queue"
JOB_UPDATE_CHANNEL = "job_update_channel"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


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
    hn_max_threads: int = 3
    hn_comments_hits_per_page: int = 500
    hn_max_comments_per_thread: int = 1000
    hn_max_companies_per_role: int = 50
    levelsfyi_max_companies_per_role: int = 50
    serper_api_key: str | None = None
    serper_search_url: str = "https://google.serper.dev/search"
    force_serp_retry_on_prior_attempt: bool = False
    redis_host: str = "localhost"
    redis_port: int = 6379
    enable_scoring_enqueue: bool = False
    referer: str = "https://4dayweek.io/jobs"
    crawler_trigger_queue_name: str = CRAWLER_TRIGGER_QUEUE
    crawler_ats_job_extraction_queue_name: str = CRAWLER_ATS_JOB_EXTRACTION_QUEUE
    crawler_enrichment_ats_enrichment_queue_name: str = CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE
    crawler_levelsfyi_queue_name: str = CRAWLER_LEVELSFYI_QUEUE
    crawler_ycombinator_queue_name: str = CRAWLER_YCOMBINATOR_QUEUE
    crawler_hackernews_queue_name: str = CRAWLER_HACKERNEWS_QUEUE
    crawler_4dayweek_queue_name: str = CRAWLER_4DAYWEEK_QUEUE
    crawler_progress_channel_name: str = CRAWLER_PROGRESS_CHANNEL
    job_scoring_queue_name: str = JOB_SCORING_QUEUE
    enable_workflow_dispatch_mode: bool = False
    crawler_enrichment_retiring_jobs_queue_name: str = CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE
    job_update_channel_name: str = JOB_UPDATE_CHANNEL

    @classmethod
    def from_env(cls) -> "CrawlerConfig":
        enabled_sources = _parse_csv(os.getenv("CRAWLER_ENABLED_SOURCES", "ycombinator,hackernews"))
        yc_max_companies_per_role = os.getenv("CRAWLER_YC_MAX_COMPANIES_PER_ROLE")
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
            yc_max_companies_per_role=max(1, int(yc_max_companies_per_role)) if yc_max_companies_per_role else None,
            hn_max_threads=max(1, int(os.getenv("CRAWLER_HN_MAX_THREADS", "5"))),
            hn_comments_hits_per_page=max(1, min(int(os.getenv("CRAWLER_HN_COMMENTS_HITS_PER_PAGE", "500")), 1000)),
            hn_max_comments_per_thread=max(1, int(os.getenv("CRAWLER_HN_MAX_COMMENTS_PER_THREAD", "1000"))),
            hn_max_companies_per_role=max(1, int(os.getenv("CRAWLER_HN_MAX_COMPANIES_PER_ROLE", "50"))),
            levelsfyi_max_companies_per_role=max(1, int(os.getenv("CRAWLER_LEVELSFYI_MAX_COMPANIES_PER_ROLE", "50"))),
            serper_api_key=os.getenv("SERPER_API_KEY") or None,
            serper_search_url=os.getenv("SERPER_SEARCH_URL", "https://google.serper.dev/search"),
            force_serp_retry_on_prior_attempt=_parse_bool(os.getenv("CRAWLER_FORCE_SERP_RETRY_ON_PRIOR_ATTEMPT"), default=False),
            redis_host=os.getenv("REDIS_HOST", "localhost"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            enable_scoring_enqueue=_parse_bool(os.getenv("CRAWLER_ENABLE_SCORING_ENQUEUE"), default=False),
            referer=os.getenv("CRAWLER_REFERER", "https://4dayweek.io/jobs"),
            crawler_trigger_queue_name=os.getenv("CRAWLER_TRIGGER_QUEUE_NAME", CRAWLER_TRIGGER_QUEUE),
            crawler_ats_job_extraction_queue_name=os.getenv("CRAWLER_ATS_JOB_EXTRACTION_QUEUE_NAME", CRAWLER_ATS_JOB_EXTRACTION_QUEUE),
            crawler_enrichment_ats_enrichment_queue_name=os.getenv("CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME", CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE),
            crawler_levelsfyi_queue_name=os.getenv("CRAWLER_LEVELSFYI_QUEUE_NAME", CRAWLER_LEVELSFYI_QUEUE),
            crawler_ycombinator_queue_name=os.getenv("CRAWLER_YCOMBINATOR_QUEUE_NAME", CRAWLER_YCOMBINATOR_QUEUE),
            crawler_hackernews_queue_name=os.getenv("CRAWLER_HACKERNEWS_QUEUE_NAME", CRAWLER_HACKERNEWS_QUEUE),
            crawler_4dayweek_queue_name=os.getenv("CRAWLER_4DAYWEEK_QUEUE_NAME", CRAWLER_4DAYWEEK_QUEUE),
            crawler_progress_channel_name=os.getenv("CRAWLER_PROGRESS_CHANNEL_NAME", CRAWLER_PROGRESS_CHANNEL),
            job_scoring_queue_name=os.getenv("JOB_SCORING_QUEUE_NAME", JOB_SCORING_QUEUE),
            enable_workflow_dispatch_mode=_parse_bool(os.getenv("CRAWLER_ENABLE_WORKFLOW_DISPATCH_MODE"), default=False),
            crawler_enrichment_retiring_jobs_queue_name=os.getenv("CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME", CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE),
            job_update_channel_name=os.getenv("JOB_UPDATE_CHANNEL_NAME", JOB_UPDATE_CHANNEL),
        )
