#!/usr/bin/env bash
set -xeuo pipefail

COMPOSE_FILE="${E2E_WORKFLOW1_COMPOSE_FILE:-tests/e2e/docker-compose.workflow1.yml}"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

cleanup() {
  e2e_cleanup_compose 0 mongo
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
