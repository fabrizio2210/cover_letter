#!/usr/bin/env bash
# E2E test: Start Crawl from Job Discovery journey
#
# Flow:
#   1. Seed MongoDB with an identity that has roles configured
#   2. Start API + Redis + dispatcher
#   3. POST /api/crawls via the local helper script
#   4. Verify the dispatcher fanned out workflow dispatch messages to Redis
#
# Usage:
#   bash tests/e2e/test_start_crawl_e2e.sh
#   bash tests/e2e/test_start_crawl_e2e.sh --keep   # do not tear down on success

set -eux

COMPOSE_FILE="${E2E_COMPOSE_FILE:-tests/e2e/docker-compose.test.yml}"
COMPOSE="docker compose -f $COMPOSE_FILE"
KEEP=${1:-}
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

teardown() {
  echo "****** Dispatcher logs ******"
  $COMPOSE logs dispatcher || true
  echo "****************************"
  echo "****** API logs ******"
  $COMPOSE logs api || true
  echo "**********************"
  if [[ "$KEEP" != "--keep" ]]; then
    $COMPOSE down --remove-orphans --volumes 2>/dev/null || true
  fi
}
trap teardown EXIT

# Spin up infrastructure + API + dispatcher
$COMPOSE up -d mongo redis
$COMPOSE up -d api
e2e_prepare_artifacts
e2e_export_stack_env

# Seed identity data
e2e_run_python tests/e2e/seed_crawl_trigger_e2e.py

# Start dispatcher (background, long-lived)
$COMPOSE up -d dispatcher

# Brief pause so the dispatcher worker loop is listening before we push
sleep 3

# Trigger the crawl via the API
e2e_run_python tests/e2e/push_crawl_trigger_via_api.py

# Wait for dispatcher to consume trigger and fan out, then verify
e2e_run_python tests/e2e/check_crawl_trigger.py

echo "[e2e] Suite start_crawl PASSED"
