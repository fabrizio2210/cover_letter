#!/usr/bin/env bash
# E2E test: Post-crawl auto-scoring journey
#
# Flow:
#   1. Start mongo + redis + ai_scorer
#   2. Run the local post_crawl_scoring_integration helper which:
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

set -xeuo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
COMPOSE="docker compose -f $COMPOSE_FILE"
KEEP=${1:-}

teardown() {
  if [[ "$KEEP" != "--keep" ]]; then
    e2e_cleanup_compose 0 mongo ai_scorer
  else
    e2e_dump_compose_logs mongo ai_scorer
  fi
}
trap teardown EXIT

echo "=== [post-crawl scoring e2e] Bringing up mongo + redis + ai_scorer ==="
$COMPOSE up -d mongo redis ai_scorer
e2e_prepare_artifacts
e2e_export_stack_env

echo "=== [post-crawl scoring e2e] Waiting for ai_scorer to be ready ==="
sleep 3

echo "=== [post-crawl scoring e2e] Running crawler integration + scoring check ==="
e2e_run_python tests/e2e/post_crawl_scoring_integration.py

echo ""
echo "=== [post-crawl scoring e2e] Suite PASSED ==="
