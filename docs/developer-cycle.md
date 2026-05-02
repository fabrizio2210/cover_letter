# Developer Test Gate

## Goal

Block local commits when fast tests fail, and run full validation in presubmit/CI.

## One-time setup

Run:

```bash
make install-hooks
```

This configures Git to use repository-managed hooks from `.githooks/`.

## Prerequisites

- `go` available in `PATH`
- `python3` available in `PATH`
- `node` and `npm` available in `PATH`
- `docker` and `docker compose` available in `PATH` for full mode
- Chrome/Chromium available for frontend tests (`google-chrome`, `chromium`, or `chromium-browser`), or `CHROME_BIN` set explicitly

## Commands

Fast gate (used by pre-commit):

```bash
make test-fast
```

Full gate (used by CI/presubmit):

```bash
make test-full
```

Direct script usage:

```bash
bash scripts/test-gate.sh --mode fast
bash scripts/test-gate.sh --mode full
```

## What each mode runs

- `fast`
  - Go unit tests (`go test ./...` in `src/go/cmd/api`)
  - Python tests (`unittest discover` in `src/python` and `tests`)
  - Frontend tests (`npm run test -- --watch=false --browsers=ChromeHeadless` in `src/js/coverletter-frontend`)
- `full`
  - Everything in `fast`
  - E2E suites via `tests/e2e/run_e2e_suites.sh` (in order):
    - `coverletter` — `tests/e2e/test_coverletter_e2e.sh` (cover-letter generation / ai_querier flow)
    - `ai_scorer` — `tests/e2e/test_ai_scorer_e2e.sh`
    - `start_crawl` — `tests/e2e/test_start_crawl_e2e.sh`
    - `ai_scorer_crawler` — `tests/e2e/test_ai_scorer_crawler_e2e.sh`
    - `workflow1` — `tests/e2e/test_workflow1_integration.sh`

To run a single suite locally:

```bash
bash tests/e2e/run_e2e_suites.sh start_crawl
```

To list all available suites:

```bash
bash tests/e2e/run_e2e_suites.sh --list
```

## VS Code

Tasks are available:

- `Test Fast`
- `Test Full`
