#!/usr/bin/env bash
# E2E test: Post-crawl auto-scoring journey
#
# Flow:
#   1. Start mongo + redis + ai_scorer
#   2. Run post_crawl_scoring_integration container which:
#      a. Seeds a company (global DB) and an identity (per-user DB)
#      b. Runs run_crawler_ats_job_extraction with patched HTTP + real Redis
#      c. Verifies the crawler called _try_enqueue with identity_id
#      d. Polls MongoDB until ai_scorer writes the score to the per-user DB
#
# This test catches regressions where identity_id is dropped anywhere in the
# crawler → Redis → scorer pipeline, which would silently produce un-scored jobs.
#
# Usage:
#   bash tests/e2e/test_post_crawl_scoring_e2e.sh
#   bash tests/e2e/test_post_crawl_scoring_e2e.sh --keep   # keep containers on success

set -euo pipefail

COMPOSE="docker compose -f tests/e2e/docker-compose.test.yml"
KEEP=${1:-}

teardown() {
  echo ""
  echo "****** ai_scorer logs ******"
  $COMPOSE logs ai_scorer || true
  echo "****************************"
  if [[ "$KEEP" != "--keep" ]]; then
    $COMPOSE down --remove-orphans 2>/dev/null || true
  fi
}
trap teardown EXIT

echo "=== [post-crawl scoring e2e] Bringing up mongo + redis + ai_scorer ==="
$COMPOSE up -d mongo redis ai_scorer

echo "=== [post-crawl scoring e2e] Waiting for ai_scorer to be ready ==="
sleep 3

echo "=== [post-crawl scoring e2e] Running crawler integration + scoring check ==="
$COMPOSE run --rm post_crawl_scoring_integration

echo ""
echo "=== [post-crawl scoring e2e] Suite PASSED ==="
