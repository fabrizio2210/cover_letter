#!/usr/bin/env bash
# E2E suite: AI scorer flow
#
# Usage:
#   bash tests/e2e/test_ai_scorer_e2e.sh
set -euo pipefail

COMPOSE_FILE="tests/e2e/docker-compose.test.yml"

cleanup() {
  echo "****** API logs ******"
  docker compose -f "$COMPOSE_FILE" logs api || true
  echo "**********************"
  echo "****** AI Scorer logs ******"
  docker compose -f "$COMPOSE_FILE" logs ai_scorer || true
  echo "****************************"
  docker compose -f "$COMPOSE_FILE" down --remove-orphans
}
trap cleanup EXIT

docker compose -f "$COMPOSE_FILE" up -d mongo redis api
docker compose -f "$COMPOSE_FILE" run --rm seeder
docker compose -f "$COMPOSE_FILE" up -d ai_scorer
sleep 2
docker compose -f "$COMPOSE_FILE" run --rm scorer_pusher
docker compose -f "$COMPOSE_FILE" run --rm scorer_checker

if docker compose -f "$COMPOSE_FILE" logs ai_scorer | grep "error"; then
  echo "BUG DETECTED: ai_scorer failed to process message"
  exit 1
fi

echo "[e2e] Suite ai_scorer PASSED"