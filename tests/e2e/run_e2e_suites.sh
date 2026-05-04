#!/usr/bin/env bash
# E2E suite aggregator.
#
# Usage:
#   bash tests/e2e/run_e2e_suites.sh              # run all suites
#   bash tests/e2e/run_e2e_suites.sh coverletter  # run one suite by name
#   bash tests/e2e/run_e2e_suites.sh --list       # print available suites
#
# Suite names (execution order):
#   coverletter        – tests/e2e/test_coverletter_e2e.sh
#   ai_scorer          – tests/e2e/test_ai_scorer_e2e.sh
#   start_crawl        – tests/e2e/test_start_crawl_e2e.sh
#   ai_scorer_crawler  – tests/e2e/test_ai_scorer_crawler_e2e.sh
#   workflow1          – tests/e2e/test_workflow1_integration.sh
#   post_crawl_scoring – tests/e2e/test_post_crawl_scoring_e2e.sh

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# ---------------------------------------------------------------------------
# Authoritative suite registry – order matters, names must be unique.
# Each entry: "<name>:<relative-path-from-repo-root>"
# ---------------------------------------------------------------------------
SUITES=(
  "coverletter:tests/e2e/test_coverletter_e2e.sh"
  "ai_scorer:tests/e2e/test_ai_scorer_e2e.sh"
  "start_crawl:tests/e2e/test_start_crawl_e2e.sh"
  "ai_scorer_crawler:tests/e2e/test_ai_scorer_crawler_e2e.sh"
  "workflow1:tests/e2e/test_workflow1_integration.sh"
  "post_crawl_scoring:tests/e2e/test_post_crawl_scoring_e2e.sh"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
suite_names() {
  for entry in "${SUITES[@]}"; do
    echo "${entry%%:*}"
  done
}

suite_path() {
  local target="$1"
  for entry in "${SUITES[@]}"; do
    if [[ "${entry%%:*}" == "$target" ]]; then
      echo "${entry#*:}"
      return 0
    fi
  done
  return 1
}

cleanup_compose_state() {
  # Ensure stale containers from prior suites/runs cannot clash by name.
  docker compose -f "$repo_root/tests/e2e/docker-compose.test.yml" down --remove-orphans 2>/dev/null || true
  docker compose -f "$repo_root/tests/e2e/docker-compose.workflow1.yml" down --remove-orphans 2>/dev/null || true
}

print_list() {
  echo "Available E2E suites (in execution order):"
  for entry in "${SUITES[@]}"; do
    local name="${entry%%:*}"
    local path="${entry#*:}"
    printf "  %-22s  %s\n" "$name" "$path"
  done
}

run_suite() {
  local name="$1"
  local path
  if ! path="$(suite_path "$name")"; then
    echo "[e2e] ERROR: unknown suite '$name'"
    echo "[e2e] Known suites: $(suite_names | tr '\n' ' ')"
    exit 2
  fi
  local abs_path="$repo_root/$path"
  echo ""
  echo "========================================================"
  echo "[e2e] Running suite: $name  ($path)"
  echo "========================================================"
  cleanup_compose_state
  (cd "$repo_root" && bash "$abs_path")
  echo "[e2e] Suite PASSED: $name"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--list" ]]; then
  print_list
  exit 0
fi

if [[ $# -gt 0 ]]; then
  # Run only the requested suites in the order specified
  for requested in "$@"; do
    run_suite "$requested"
  done
else
  # Run all suites in registry order
  echo "[e2e] Running all ${#SUITES[@]} E2E suites"
  for entry in "${SUITES[@]}"; do
    run_suite "${entry%%:*}"
  done
fi

echo ""
echo "========================================================"
echo "[e2e] All requested suites PASSED"
echo "========================================================"
