#!/usr/bin/env bash
# E2E suite: cover-letter generation (ai_querier flow)
#
# Usage:
#   bash tests/e2e/test_coverletter_e2e.sh
set -euo pipefail

COMPOSE_FILE="tests/e2e/docker-compose.test.yml"

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
docker compose -f "$COMPOSE_FILE" run --rm seeder
docker compose -f "$COMPOSE_FILE" up -d ai_querier
sleep 2 # wait for ai_querier to start listening
docker compose -f "$COMPOSE_FILE" run --rm pusher
sleep 2 # wait for ai_querier to process the message
# poll checker until FOUND
docker compose -f "$COMPOSE_FILE" run --rm checker

if docker compose -f "$COMPOSE_FILE" logs ai_querier | grep "error"; then
  echo "BUG DETECTED: ai_querier failed to process message"
  exit 1
fi

echo "[e2e] Suite coverletter PASSED"