# Jobs Domain Specification

This file is the authoritative reference for job handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- GET /api/job-descriptions
- GET /api/job-descriptions/stream
- GET /api/job-descriptions/:id
- GET /api/job-preference-scores
- POST /api/job-descriptions
- PUT /api/job-descriptions/:id
- DELETE /api/job-descriptions/:id
- POST /api/job-descriptions/:id/score
- POST /api/job-descriptions/:id/check

## Model Contract

### JobDescription

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `company_id` | `company` | `string` | Hex ObjectID ref to `companies`. BSON key is `company`, not `company_id` |
| `company_info` | `companyInfo` | `Company` | Populated by `$lookup` aggregation |
| `title` | `title` | `string` | Normalized job title |
| `description` | `description` | `string` | Full job description body |
| `location` | `location` | `string` | Free-form location string |
| `platform` | `platform` | `string` | Source platform, for example `ashby` or `lever` |
| `external_job_id` | `external_job_id` | `string` | Platform-native stable job id |
| `source_url` | `source_url` | `string` | Canonical URL for the job |
| `created_at` | `created_at` | Timestamp object | `{ "seconds": <unix>, "nanos": 0 }` |
| `updated_at` | `updated_at` | Timestamp object | `{ "seconds": <unix>, "nanos": 0 }` |

Storage note:
- API JSON uses string ids (`company_id`), but new Mongo writes for relation fields should use ObjectID values.
- Read paths must remain tolerant of legacy string reference storage.

### JobPreferenceScore

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `job_id` | `job_id` | `string` | Hex ObjectID ref to `job-descriptions` |
| `identity_id` | `identity_id` | `string` | Hex ObjectID ref to `identities` |
| `preference_scores` | `preference_scores` | `[]PreferenceScore` | Embedded per-preference score results |
| `scoring_status` | `scoring_status` | `string` | One of `queued`, `scored`, `failed`, `skipped` |
| `weighted_score` | `weighted_score` | `number` | Deterministic aggregate for one `(job_id, identity_id)` pair |

One `JobPreferenceScore` document exists per `(job_id, identity_id)` pair.

### PreferenceScore

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `preference_key` | `preference_key` | `string` | Stable preference identifier |
| `preference_guidance` | `preference_guidance` | `string` | Human-friendly guidance snapshot |
| `preference_weight` | `preference_weight` | `number` | Weight snapshot used in deterministic ranking |
| `score` | `score` | `integer` | AI-generated score from 1 to 5 |
| `scored_at` | `scored_at` | Timestamp object | `{ "seconds": <unix>, "nanos": 0 }` |

Proto-first rule:
- `JobDescription` and `JobPreferenceScore` should become canonical proto-backed schema once added to `common.proto`.

## HTTP Contract

Common rules:
- Auth required for all endpoints in this domain.
- `:id` must be a MongoDB hex ObjectID string.

### GET /api/job-descriptions

Response `200`: array of score-neutral `JobDescription` with `company_info` embedded.

### GET /api/job-descriptions/:id

Response `200`: single score-neutral `JobDescription` with `company_info`.

Response `404`:

```json
{ "error": "Job description not found" }
```

### POST /api/job-descriptions

Request:

```json
{
	"company_id": "<hex or omit>",
	"company_name": "<string or omit when company_id is set>",
	"title": "string",
	"description": "string",
	"location": "string",
	"platform": "string",
	"external_job_id": "string",
	"source_url": "string"
}
```

Behavior:
- If `company_id` is present, the handler links to that company.
- If `company_id` is absent and `company_name` is present, the handler resolves or creates the company.

Response `201`: created `JobDescription`.

### PUT /api/job-descriptions/:id

Request:

```json
{
	"company_id": "<hex or omit>",
	"title": "string",
	"description": "string",
	"location": "string",
	"platform": "string",
	"external_job_id": "string",
	"source_url": "string"
}
```

Response `200`:

```json
{ "message": "Job description updated successfully" }
```

### DELETE /api/job-descriptions/:id

Response `200`:

```json
{ "message": "Job description deleted successfully" }
```

### POST /api/job-descriptions/:id/score

Request body (required):

```json
{
	"identity_id": "<hex ObjectID>"
}
```

Behavior:
- `identity_id` is mandatory and must be a valid MongoDB hex ObjectID string.
- The validated `identity_id` is always forwarded in the `job_scoring_queue` payload.

Response `200`:

```json
{ "message": "Scoring queued successfully" }
```

### POST /api/job-descriptions/:id/check

No request body.

Response `202`:

```json
{ "message": "Check queued successfully" }
```

### GET /api/job-descriptions/stream

Auth required. Long-lived SSE (`text/event-stream`) connection.

Event name: `job-update`

SSE data payload:

```json
{
	"job_id": "<hex ObjectID of the checked or updated job>",
	"workflow_id": "enrichment_retiring_jobs",
	"workflow_run_id": "<workflow run identifier>",
	"emitted_at": { "seconds": <unix>, "nanos": 0 }
}
```

### GET /api/job-preference-scores

Query params:
- `job_id` optional hex ObjectID string
- `identity_id` optional hex ObjectID string

Response `200`: array of `JobPreferenceScore` filtered by query params.

Ranking semantics:
- AI writes per-preference scores only.
- The application computes weighted aggregate deterministically from preference weights.
- Aggregate score is persisted only on the matching `job-preference-scores` document.

## Queue And Channel Contracts

### Queue: `job_scoring_queue`

Env var: `JOB_SCORING_QUEUE_NAME` (default `job_scoring_queue`)

Consumer: Python `ai_scorer`.

Produced by: `POST /api/job-descriptions/:id/score` and automatic post-crawl enqueue.

Payload:

```json
{
	"user_id": "<jwt sub>",
	"job_id": "<job description hex object id>",
	"identity_id": "<identity hex object id>"
}
```

Rules:
- Missing `user_id`, `job_id`, or `identity_id` makes the message invalid and it is dropped with an error log.
- `identity_id` is mandatory for API-triggered and crawler-triggered scoring messages.
- Re-crawl updates should enqueue a new `job_id` for rescoring.
- Jobs missing scoring prerequisites may be marked `skipped` and not enqueued.
- Scoring worker derives per-user DB name from `user_id` and reads global job/company data from `cover_letter_global`.

### Queue: `enrichment_retiring_jobs_queue`

Env var: `CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME` (default `enrichment_retiring_jobs_queue`)

Consumer: Python `web_crawler` `enrichment_retiring_jobs` worker.

Produced by: `POST /api/job-descriptions/:id/check`

Payload:

```json
{
	"user_id": "<jwt sub>",
	"job_id": "<job description hex object id>"
}
```

Worker rules:
- Missing `user_id` or `job_id` makes the message invalid and it is dropped with a warning log.
- Worker probes `source_url`; on HTTP 404 the job is marked closed (`is_open=false`).
- If job remains closed for more than 60 days, it is deleted.

### Channel: `job_update_channel`

Env var: `JOB_UPDATE_CHANNEL_NAME` (default `job_update_channel`)

Publisher: Python `enrichment_retiring_jobs` worker.

Consumer: Go API SSE relay on `GET /api/job-descriptions/stream`.

Payload:

```json
{
	"job_id": "<hex ObjectID of the checked or updated job>",
	"workflow_id": "enrichment_retiring_jobs",
	"workflow_run_id": "<workflow run identifier>",
	"emitted_at": { "seconds": <unix>, "nanos": 0 }
}
```

Rules:
- Published once per successful job retirement run (`completed`).
- UI reloads `GET /api/job-descriptions/:id` for the matching `job_id`.

## Implementation

- Canonical behavior is implemented in `domains/jobs/handlers.go`.


