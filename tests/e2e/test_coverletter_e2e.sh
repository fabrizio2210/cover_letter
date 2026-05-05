#!/usr/bin/env bash
# E2E suite: cover-letter generation (ai_querier flow)
#
# Usage:
#   bash tests/e2e/test_coverletter_e2e.sh
set -euo pipefail

COMPOSE_FILE="${E2E_COMPOSE_FILE:-tests/e2e/docker-compose.test.yml}"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

cleanup() {
  echo "****** API logs ******"
  docker compose -f "$COMPOSE_FILE" logs api || true
  echo "**********************"
  echo "****** AI Querier logs ******"
  docker compose -f "$COMPOSE_FILE" logs ai_querier || true
  echo "***************************"
  docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

docker compose -f "$COMPOSE_FILE" up -d mongo redis api
e2e_prepare_artifacts
e2e_export_stack_env
e2e_run_python tests/e2e/seed_mongo.py
docker compose -f "$COMPOSE_FILE" up -d ai_querier
sleep 2 # wait for ai_querier to start listening
e2e_run_python tests/e2e/push_via_api.py
sleep 2 # wait for ai_querier to process the message
# poll checker until FOUND
e2e_run_python tests/e2e/check_coverletter.py

if docker compose -f "$COMPOSE_FILE" logs ai_querier | grep "error"; then
  echo "BUG DETECTED: ai_querier failed to process message"
  exit 1
fi

echo "[e2e] Suite coverletter PASSED"