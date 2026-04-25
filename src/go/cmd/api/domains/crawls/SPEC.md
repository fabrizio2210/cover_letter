# Crawls Domain Specification

This file is the authoritative reference for crawl/scoring progress handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- POST /api/crawls
- GET /api/crawls/active
- GET /api/crawls/activity-summary
- GET /api/crawls/last-run/workflow-stats
- GET /api/crawls/workflow-cumulative-jobs
- GET /api/crawls/stream
- GET /api/scoring/active
- GET /api/scoring/stream

## HTTP Contract

Common rules:
- Auth required for all endpoints in this domain.
- Long-lived stream endpoints use `text/event-stream`.

### POST /api/crawls

Request:

```json
{ "identity_id": "<hex>" }
```

Behavior:
- Enqueue crawl request onto `crawler_trigger_queue`.
- Generate `run_id` before enqueueing.
- Return `409` if an active crawl already exists for the same `identity_id`.

Response `202`:

```json
{
	"message": "Crawl queued successfully",
	"run_id": "<crawl run id>",
	"identity_id": "<hex>",
	"status": "queued"
}
```

Response `409`:

```json
{
	"error": "A crawl is already running for this identity",
	"run_id": "<active crawl run id>",
	"identity_id": "<hex>",
	"status": "running"
}
```

### GET /api/crawls/active

Response `200`: array of active or recently terminal crawl snapshots.

Rules:
- Latest server-side snapshot used to bootstrap UI after refresh.
- Multiple entries may exist for one `run_id` and `identity_id` when workflows run in parallel.
- Distinct contributions are preserved by `workflow_run_id`.

### GET /api/crawls/activity-summary

Response `200`: global queue depths and identity-scoped active workflows.

Query params:
- `identity_id` optional: filters active workflows to the selected identity.

Rules:
- Queue-depth values are global Redis `LLEN` results, not identity-scoped.
- Active workflows include `queued` and `running` states.

### GET /api/crawls/last-run/workflow-stats

Response `200`: latest completed workflow stats for dashboard visibility.

Rules:
- Identity-agnostic endpoint.
- Includes only workflows with id prefix `crawler_`.
- Stable order: `crawler_company_discovery`, `crawler_levelsfyi`, `crawler_4dayweek`, `crawler_ats_job_extraction`.
- If nothing has completed yet: `{ "completed_at": null, "workflows": [] }`.

### GET /api/crawls/workflow-cumulative-jobs

Response `200`: global cumulative discovered-job counters by crawler workflow.

Rules:
- Counters are global and persisted.
- Stable workflow order matches dashboard order.

### GET /api/crawls/stream

Response `200`: SSE stream.

Event name: `crawl-progress`

Rules:
- API can multiplex updates for multiple identities.
- Clients filter by `identity_id`, `workflow_id`, and `workflow_run_id`.

### GET /api/scoring/active

Response `200`: array of active or recently terminal scoring snapshots.

### GET /api/scoring/stream

Response `200`: SSE stream.

Event name: `scoring-progress`

Rules:
- API can multiplex updates for multiple identities.
- Shared UI widgets should prioritize crawl progress when both crawl and scoring are active.

## Queue And Channel Contracts

### Queue: `crawler_trigger_queue`

Env var: `CRAWLER_TRIGGER_QUEUE_NAME` (default `crawler_trigger_queue`)

Consumer: Python `web_crawler` service.

Produced by: `POST /api/crawls`

Payload:

```json
{
	"run_id": "<server-generated crawl run id>",
	"identity_id": "<identity hex object id>",
	"requested_at": { "seconds": 1711234567, "nanos": 0 }
}
```

Rules:
- Missing `run_id` or `identity_id` causes rejection.
- Active duplicate run for same identity should emit terminal `rejected` progress with reason `already_running`.

### Channel: `crawler_progress_channel`

Env var: `CRAWLER_PROGRESS_CHANNEL_NAME` (default `crawler_progress_channel`)

Publisher: Python `web_crawler`.

Consumer: Go API relay to `GET /api/crawls/stream`.

Payload:

```json
{
	"run_id": "<crawl run id>",
	"workflow_run_id": "<workflow execution attempt id>",
	"workflow_id": "crawler_company_discovery",
	"identity_id": "<identity hex object id>",
	"status": "queued",
	"workflow": "queued",
	"message": "Waiting for worker pickup",
	"estimated_total": 100,
	"completed": 0,
	"percent": 0,
	"started_at": null,
	"updated_at": { "seconds": 1711234567, "nanos": 0 },
	"finished_at": null,
	"reason": ""
}
```

Status values:
- `queued`
- `running`
- `completed`
- `failed`
- `rejected`

Rules:
- `percent` is integer 0..100.
- `started_at` is set on first `running` event.
- `finished_at` is set on terminal states.
- Most recent event per `workflow_run_id` is authoritative.

### Channel: `scoring_progress_channel`

Env var: `SCORING_PROGRESS_CHANNEL_NAME` (default `scoring_progress_channel`)

Publisher: Python `ai_scorer`.

Consumer: Go API relay to `GET /api/scoring/stream`.

Payload:

```json
{
	"run_id": "<scoring run id>",
	"identity_id": "<identity hex object id>",
	"status": "running",
	"message": "Scoring in progress",
	"estimated_total": 24,
	"completed": 7,
	"percent": 29,
	"started_at": { "seconds": 1711234567, "nanos": 0 },
	"updated_at": { "seconds": 1711234600, "nanos": 0 },
	"finished_at": null,
	"reason": ""
}
```

Status values:
- `running`
- `completed`
- `failed`

Rules:
- `percent` is integer 0..100.
- New scoring runs start from 0%.
- Most recent event per `run_id` is authoritative.

## Implementation

- Canonical behavior is implemented in `domains/crawls/handlers.go`.


