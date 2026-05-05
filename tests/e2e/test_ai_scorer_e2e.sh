#!/usr/bin/env bash
# E2E suite: AI scorer flow
#
# Usage:
#   bash tests/e2e/test_ai_scorer_e2e.sh
set -euo pipefail

COMPOSE_FILE="${E2E_COMPOSE_FILE:-tests/e2e/docker-compose.test.yml}"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

cleanup() {
  echo "****** API logs ******"
  docker compose -f "$COMPOSE_FILE" logs api || true
  echo "**********************"
  echo "****** AI Scorer logs ******"
  docker compose -f "$COMPOSE_FILE" logs ai_scorer || true
  echo "****************************"
  docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

docker compose -f "$COMPOSE_FILE" up -d mongo redis api
e2e_prepare_artifacts
e2e_export_stack_env
e2e_run_python tests/e2e/seed_mongo.py
docker compose -f "$COMPOSE_FILE" up -d ai_scorer
sleep 2
e2e_run_python tests/e2e/push_score_via_api.py
e2e_run_python tests/e2e/check_ai_scorer.py

if docker compose -f "$COMPOSE_FILE" logs ai_scorer | grep "error"; then
  echo "BUG DETECTED: ai_scorer failed to process message"
  exit 1
fi

echo "[e2e] Suite ai_scorer PASSED"