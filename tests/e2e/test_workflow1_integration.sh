#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${E2E_WORKFLOW1_COMPOSE_FILE:-tests/e2e/docker-compose.workflow1.yml}"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

cleanup() {
  docker compose -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true
}

trap cleanup EXIT

docker compose -f "$COMPOSE_FILE" up -d mongo
e2e_prepare_artifacts
e2e_export_stack_env
export DB_NAME="cover_letter_workflow1_it"

(
  cd "$E2E_REPO_ROOT"
  python3 -m unittest discover -v -s src/python/web_crawler/tests -p 'test_workflow1_integration.py'
)
