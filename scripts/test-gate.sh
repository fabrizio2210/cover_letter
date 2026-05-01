#!/usr/bin/env bash
set -euo pipefail

mode="fast"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --mode"
        exit 2
      fi
      mode="$2"
      shift 2
      ;;
    fast|full)
      mode="$1"
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: scripts/test-gate.sh [--mode fast|full]"
      exit 2
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run_go_tests() {
  echo "[gate] Running Go tests"
  (
    cd "$repo_root/src/go/cmd/api"
    go vet ./...
    go test ./...
  )
}

run_python_tests() {
  echo "[gate] Running Python tests"
  (
    cd "$repo_root"
    PYTHONPATH=. python3 -m unittest discover -s src/python -p "test*.py"
    PYTHONPATH=. python3 -m unittest discover -s tests -p "test*.py"
  )
}

run_frontend_tests() {
  echo "[gate] Running frontend tests"

  local chrome_bin=""
  for candidate in google-chrome chromium chromium-browser; do
    if command -v "$candidate" >/dev/null 2>&1; then
      chrome_bin="$(command -v "$candidate")"
      break
    fi
  done

  if [[ -z "$chrome_bin" ]]; then
    echo "[gate] Frontend tests require Chrome/Chromium."
    echo "[gate] Install one of: google-chrome, chromium, chromium-browser"
    echo "[gate] Or set CHROME_BIN to a valid browser executable path."
    exit 1
  fi

  (
    cd "$repo_root/src/js/coverletter-frontend"
    echo "[gate] Type-checking frontend"
    npx tsc --noEmit
    CHROME_BIN="$chrome_bin" npm run test -- --watch=false --browsers=ChromeHeadless
  )
}

run_e2e_tests() {
  echo "[gate] Running Docker E2E tests"
  (
    cd "$repo_root"
    bash tests/e2e.sh
    bash tests/e2e/test_ai_scorer_e2e.sh
    bash tests/e2e/test_ai_scorer_crawler_e2e.sh
    bash tests/e2e/test_workflow1_integration.sh
  )
}

case "$mode" in
  fast)
    echo "[gate] Mode: fast"
    run_go_tests
    run_python_tests
    run_frontend_tests
    ;;
  full)
    echo "[gate] Mode: full"
    run_go_tests
    run_python_tests
    run_frontend_tests
    run_e2e_tests
    ;;
  *)
    echo "Unsupported mode: $mode"
    exit 2
    ;;
esac

echo "[gate] All checks passed in '$mode' mode"
