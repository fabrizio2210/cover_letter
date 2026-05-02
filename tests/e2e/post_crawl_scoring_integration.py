"""
Post-crawl auto-scoring E2E integration test.

Flow under test:
  run_crawler_ats_job_extraction (patched HTTP, real Redis + MongoDB)
    → _try_enqueue(user_id, job_id, identity_id)   [inside the crawler workflow]
    → Redis job_scoring_queue
    → ai_scorer reads queue, resolves identity by identity_id, writes score
    → job-preference-scores in per-user MongoDB DB

This test catches regressions where:
  - identity_id is dropped from the _try_enqueue call
  - the crawler fails to pass identity_id to the scoring queue
  - the scorer fails to find the identity from the queue payload

Usage (run inside Docker via the post_crawl_scoring_integration service):
  python /workspace/tests/e2e/post_crawl_scoring_integration.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from unittest.mock import patch

MONGO_URI = os.environ.get("MONGO_HOST", "mongodb://mongo:27017/")
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
GLOBAL_DB_NAME = os.environ.get("DB_NAME", "cover_letter")
QUEUE_NAME = os.environ.get("JOB_SCORING_QUEUE_NAME", "job_scoring_queue")

# Fixed user identity for this test suite
_USERNAME = "e2e-post-crawl-scoring-user"
USER_ID = hashlib.sha256(_USERNAME.encode()).digest()[:16].hex()

POLL_TIMEOUT_SECONDS = 60


def _wait_for_mongo():
    from pymongo import MongoClient

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
            client.server_info()
            return client
        except Exception:
            time.sleep(0.5)
    raise SystemExit("MongoDB not reachable within 30s")


def _wait_for_redis():
    import redis

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_connect_timeout=3)
            r.ping()
            return r
        except Exception:
            time.sleep(0.5)
    raise SystemExit("Redis not reachable within 30s")


def main() -> None:
    mongo_client = _wait_for_mongo()
    _wait_for_redis()  # confirm Redis is up before we start; crawler will connect it internally

    global_db = mongo_client[GLOBAL_DB_NAME]
    user_db = mongo_client[f"cover_letter_{USER_ID}"]

    # --- Clean up leftover data from previous runs ---
    global_db["companies"].delete_many({"canonical_name": "post crawl e2e corp"})
    global_db["job-descriptions"].delete_many({"external_job_id": "post-crawl-e2e-job-1"})
    user_db["identities"].delete_many({"name": "Post Crawl E2E Identity"})
    user_db["job-preference-scores"].delete_many({"external_job_id": "post-crawl-e2e-job-1"})

    # --- Seed: one ATS-enriched company (global DB) ---
    company_id = global_db["companies"].insert_one(
        {
            "name": "Post Crawl E2E Corp",
            "canonical_name": "post crawl e2e corp",
            "ats_provider": "greenhouse",
            "ats_slug": "post-crawl-e2e",
            "discovery_sources": [],
        }
    ).inserted_id

    # --- Seed: one identity with preferences (per-user DB) ---
    identity_id = user_db["identities"].insert_one(
        {
            "name": "Post Crawl E2E Identity",
            "description": "E2E test identity for post-crawl scoring",
            "roles": ["Platform Engineer"],
            "preferences": [
                {"key": "remote", "guidance": "Prefers fully remote work", "weight": 2.0, "enabled": True},
                {"key": "infra", "guidance": "Infrastructure engineering", "weight": 1.0, "enabled": True},
            ],
        }
    ).inserted_id

    print(f"[setup] company_id={company_id} identity_id={identity_id} user_id={USER_ID}")

    # --- Run the crawler workflow (HTTP patched, real Redis + MongoDB) ---
    from src.python.ai_querier import common_pb2
    from src.python.web_crawler.config import CrawlerConfig
    from src.python.web_crawler.crawler_ats_job_extraction.workflow import run_crawler_ats_job_extraction

    config = CrawlerConfig(
        mongo_host=MONGO_URI,
        db_name=GLOBAL_DB_NAME,
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        job_scoring_queue_name=QUEUE_NAME,
        enable_scoring_enqueue=True,
    )

    def fake_fetch_jobs(provider, slug, cfg, session):
        return [
            common_pb2.Job(
                title="Senior Platform Engineer",
                description="Build distributed remote infrastructure for engineering teams.",
                location="Remote",
                platform=provider,
                external_job_id="post-crawl-e2e-job-1",
                source_url="https://example.com/post-crawl-e2e/1",
            )
        ]

    with patch(
        "src.python.web_crawler.crawler_ats_job_extraction.workflow.fetch_jobs",
        side_effect=fake_fetch_jobs,
    ):
        result = run_crawler_ats_job_extraction(
            global_db,
            config,
            user_id=USER_ID,
            identity_id=str(identity_id),
            identity_database=user_db,
        )

    print(
        f"[crawler] inserted={result.inserted_count} "
        f"enqueued={result.enqueued_count} "
        f"failed={result.enqueue_failed_count}"
    )

    if result.inserted_count != 1:
        print(f"FAIL: expected 1 inserted job, got {result.inserted_count}")
        sys.exit(1)

    if result.enqueued_count != 1:
        print(
            f"FAIL: expected 1 enqueued scoring job, got {result.enqueued_count} "
            f"(enqueue_failed={result.enqueue_failed_count})"
        )
        sys.exit(1)

    # --- Resolve the inserted job_id ---
    job_doc = global_db["job-descriptions"].find_one({"external_job_id": "post-crawl-e2e-job-1"})
    if not job_doc:
        print("FAIL: inserted job not found in DB")
        sys.exit(1)
    job_id_str = str(job_doc["_id"])
    print(f"[crawler] job inserted as job_id={job_id_str}")

    # --- Poll for the score to appear in the per-user DB ---
    scores_col = user_db["job-preference-scores"]
    scored_doc = None
    deadline = time.time() + POLL_TIMEOUT_SECONDS

    while time.time() < deadline:
        scored_doc = scores_col.find_one(
            {"job_id": job_id_str, "identity_id": str(identity_id)}
        )
        if scored_doc and scored_doc.get("scoring_status") == "scored":
            break
        time.sleep(1)

    if not scored_doc or scored_doc.get("scoring_status") != "scored":
        status = scored_doc.get("scoring_status") if scored_doc else "not found"
        print(
            f"FAIL: job {job_id_str} not scored within {POLL_TIMEOUT_SECONDS}s. "
            f"Last status: {status}"
        )
        sys.exit(1)

    print(
        f"PASS: job {job_id_str} scored — "
        f"status={scored_doc['scoring_status']} "
        f"weighted_score={scored_doc.get('weighted_score')}"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
