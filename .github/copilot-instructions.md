# Copilot Agent Instructions

## After every code change

After making any code change (Go, Python, TypeScript, proto), run:

```bash
make test-full
```

Do not commit, do not present the result as done, and do not proceed to the next step if this command fails.
Fix the failure first, then re-run `make test-full` until it passes.

## What `make test-full` checks

- `go vet ./...` — Go static analysis
- `go test ./...` — Go unit tests
- Python `unittest discover` — Python unit tests in `src/python` and `tests`
- `tsc --noEmit` — Angular TypeScript type-check
- `npm run test` via Karma/ChromeHeadless — Angular unit tests
- e2e tests in `tests/e2e`

## Specification files

Each service has a SPEC.md that is the authoritative contract for that service.
Read the relevant SPEC.md before editing implementation files:

| Area | SPEC |
|---|---|
| Go API | `src/go/cmd/api/SPEC.md` |
| Frontend | `src/js/coverletter-frontend/src/app/SPEC.md` |
| AI Querier | `src/python/ai_querier/SPEC.md` |
| AI Scorer | `src/python/ai_scorer/SPEC.md` |
| Web Crawler | `src/python/web_crawler/SPEC.md` |
| Product overview | `spec.md` |

## Shared data model

The canonical data model is defined in `src/go/internal/proto/common/common.proto`.
Do not create ad-hoc BSON/JSON shapes for entities already covered there.

## Queue and channel names

Always use the env-var-driven config (e.g., `config.job_scoring_queue_name`) rather than hardcoded string literals for Redis queue and channel names.

## Testing conventions

- Go handler tests use interface-based fakes (no real Mongo/Redis). See `src/go/cmd/api/domains/*/handlers_test.go` for examples.
- Python tests use `FakeCollection` / `FakeDatabase` in-memory fakes. See existing `test_*.py` files for examples.
- Frontend tests use `HttpClientTestingModule` and `jasmine.createSpyObj`. See `src/js/coverletter-frontend/src/app/`.
- Do not merge if `make test-fast` is red.
