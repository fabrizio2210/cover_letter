#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="tests/e2e/docker-compose.workflow1.yml"

cleanup() {
  docker compose -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true
}

trap cleanup EXIT

docker compose -f "$COMPOSE_FILE" up -d mongo

docker compose -f "$COMPOSE_FILE" run --rm workflow1_integration_tests
