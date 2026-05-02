#!/usr/bin/env bash
# E2E test: Start Crawl from Job Discovery journey
#
# Flow:
#   1. Seed MongoDB with an identity that has roles configured
#   2. Start API + Redis + dispatcher
#   3. POST /api/crawls via the crawl_trigger_pusher container
#   4. Verify the dispatcher fanned out workflow dispatch messages to Redis
#
# Usage:
#   bash tests/e2e/test_start_crawl_e2e.sh
#   bash tests/e2e/test_start_crawl_e2e.sh --keep   # do not tear down on success

set -eux

COMPOSE="docker compose -f tests/e2e/docker-compose.test.yml"
KEEP=${1:-}

teardown() {
  echo "****** Dispatcher logs ******"
  $COMPOSE logs dispatcher || true
  echo "****************************"
  echo "****** API logs ******"
  $COMPOSE logs api || true
  echo "**********************"
  if [[ "$KEEP" != "--keep" ]]; then
    $COMPOSE down --remove-orphans --volumes
  fi
}
trap teardown EXIT

# Spin up infrastructure + API + dispatcher
$COMPOSE up -d mongo redis
$COMPOSE up -d api

# Seed identity data
$COMPOSE run --rm crawl_trigger_seeder

# Start dispatcher (background, long-lived)
$COMPOSE up -d dispatcher

# Brief pause so the dispatcher worker loop is listening before we push
sleep 3

# Trigger the crawl via the API
$COMPOSE run --rm crawl_trigger_pusher

# Wait for dispatcher to consume trigger and fan out, then verify
$COMPOSE run --rm crawl_trigger_checker

echo "[e2e] Suite start_crawl PASSED"
