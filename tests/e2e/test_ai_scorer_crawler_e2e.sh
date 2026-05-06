#!/usr/bin/env bash
# Integration test: ai-scorer identity resolution via identity_id
#
# This test catches the regression where ai-scorer emits identity_not_found for
# every job when the global company document has no field_id — the real state
# after the mono-user → multi-user DB migration.
#
# The test seeds a company WITHOUT field_id, then:
#   - Pushes a scoring queue message WITH identity_id   → must be scored
#   - Pushes a scoring queue message WITHOUT identity_id → must fail/skip
#
# Usage:
#   bash tests/e2e/test_ai_scorer_crawler_e2e.sh
set -euo pipefail

COMPOSE_FILE="${E2E_COMPOSE_FILE:-tests/e2e/docker-compose.test.yml}"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

cleanup() {
  e2e_cleanup_compose 0 mongo ai_scorer
}
trap cleanup EXIT

echo "=== [crawler scoring e2e] Bringing up mongo + redis + ai_scorer ==="
docker compose -f "$COMPOSE_FILE" up -d mongo redis ai_scorer
e2e_prepare_artifacts
e2e_export_stack_env

echo "=== [crawler scoring e2e] Seeding MongoDB ==="
e2e_run_python tests/e2e/seed_mongo_crawler_scoring.py

echo "=== [crawler scoring e2e] Waiting for ai_scorer to be ready ==="
sleep 3

echo "=== [crawler scoring e2e] Pushing scoring queue messages ==="
e2e_run_python tests/e2e/push_score_crawler_via_redis.py

echo "=== [crawler scoring e2e] Waiting for scorer to process messages ==="
sleep 2

echo "=== [crawler scoring e2e] Checking results ==="
e2e_run_python tests/e2e/check_ai_scorer_crawler.py

echo ""
echo "=== [crawler scoring e2e] ai_scorer logs ==="
docker compose -f "$COMPOSE_FILE" logs ai_scorer

# Fail if scorer logged identity_not_found for the job that had identity_id
if docker compose -f "$COMPOSE_FILE" logs ai_scorer \
     | grep "identity_not_found" \
     | grep "100000000000000000000001"; then
  echo "BUG DETECTED: ai_scorer emitted identity_not_found for job with explicit identity_id"
  exit 1
fi

echo "[e2e] Suite ai_scorer_crawler PASSED"
