# enrichment_retiring_jobs — Specification

**Authoritative reference for the `enrichment_retiring_jobs` package.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [`../SPEC.md`](../SPEC.md)
> Shared references: `../../go/cmd/api/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `enrichment_retiring_jobs` package enriches a single job document per invocation. It is triggered by a message on a Redis queue that carries a `job_id`. When triggered, it probes the job's `source_url` for HTTP 404 and, when detected, marks the job as closed. If the job has been closed for more than 60 days it is permanently deleted.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Workflow module | `src.python.web_crawler.enrichment_retiring_jobs.workflow` |
| Worker module | `src.python.web_crawler.enrichment_retiring_jobs.worker` |
| Docker CMD | `python -m src.python.web_crawler.enrichment_retiring_jobs.worker --worker` |
| Input queue | `CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME` (default `enrichment_retiring_jobs_queue`) |
| Workflow ID | `enrichment_retiring_jobs` |

The worker uses `blpop` with `timeout=0`, blocking until a message arrives. Each message carries a `job_id` and optional `run_id`, `workflow_run_id`, and `identity_id` fields. The worker processes exactly one job per message, then blocks again.

---

## 3. Environment Variables

Inherited from `CrawlerConfig`. Relevant subset:

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | MongoDB URI |
| `DB_NAME` | `cover_letter` | Database name |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME` | `enrichment_retiring_jobs_queue` | Input queue for per-job retirement requests |
| `CRAWLER_HTTP_TIMEOUT_SECONDS` | `20` | HTTP probe timeout per job URL |
| `CRAWLER_USER_AGENT` | browser-like string | Request user-agent |

---

## 4. Input Contract

Queue messages are `JobRetireEvent` proto messages serialized as JSON (see `src/go/internal/proto/common/common.proto`):

```
{
  job_id:          string   // MongoDB _id hex of the job to check (required)
  run_id:          string   // parent run identifier (optional; defaults to workflow_run_id)
  workflow_run_id: string   // workflow execution identifier (optional; auto-generated if absent)
  workflow_id:     string   // workflow identifier (optional)
  identity_id:     string   // identity context (optional; defaults to "system")
  emitted_at:      Timestamp // when the event was emitted (optional)
}
```

Serialization helpers: `workflow_messages.job_retire_event_to_json` / `parse_job_retire_event`.

---

## 5. Responsibilities

- Parse a `job_id` from each queue message; drop malformed messages.
- Look up the job document by `_id`; skip silently if not found.
- If the job is open: probe `source_url` via HTTP HEAD.
  - On HTTP 404: write `is_open: false` and `closed_at: { seconds, nanos }`.
  - On any other response or network error: leave the document unchanged.
- If the job is closed (`is_open: false`) and `closed_at` is older than 60 days: delete the document.
- Publish `running` → `completed` / `failed` progress snapshots per queue message.
- After a successful run (`completed`), emit a `JobUpdateEvent` to `job_update_channel` **only if** the job was changed (i.e., `updated_count > 0` or `deleted_count > 0`).

---

## 5.1 Output — Job Update Event

After completing a job retirement run (status `completed`), the worker publishes a `JobUpdateEvent` to the Redis pub/sub channel defined by `JOB_UPDATE_CHANNEL_NAME` (default: `job_update_channel`) **only when** the job was actually modified — i.e., when `updated_count > 0` (job marked closed) or `deleted_count > 0` (job deleted).

```json
{
  "job_id": "<hex ObjectID of the checked job>",
  "workflow_id": "enrichment_retiring_jobs",
  "workflow_run_id": "<workflow run identifier>",
  "emitted_at": { "seconds": <unix>, "nanos": 0 }
}
```

Serialization helper: `workflow_messages.job_update_event_to_json`.

---

## 6. Processing Phases

### Phase A — Mark closed

1. Load the job document by `_id`; skip if not found.
2. If `is_open` is not `false`: send an HTTP HEAD request to `source_url` (following redirects, using `CRAWLER_HTTP_TIMEOUT_SECONDS`).
3. If the response status is 404: update the job with `$set: { is_open: false, closed_at: { seconds: <unix>, nanos: 0 } }`.
4. On network exception: log DEBUG; `failed_count += 1`; return without entering Phase B.
5. On non-404 response: leave the document unchanged; proceed to Phase B.

### Phase B — Remove expired

1. If the job document now has `is_open: false` and `closed_at.seconds < (now − 60 days in unix seconds)`: call `delete_one`.
2. On deletion: `deleted_count += 1`.

---

## 7. Job Document Fields Written

| BSON key | Type | Notes |
|---|---|---|
| `is_open` | boolean | Set to `false` when source_url returns HTTP 404 |
| `closed_at` | object | `{ "seconds": <unix>, "nanos": 0 }` — set when `is_open` transitions to `false` |

---

## 8. Failure Handling

| Scenario | Behaviour |
|---|---|
| Malformed queue payload | Drop; log WARNING |
| Missing `job_id` field | Drop; log WARNING |
| Invalid `job_id` (non-ObjectId) | `failed_count += 1`; return |
| Job document not found | `skipped_count += 1`; return |
| Network error on HEAD request | Log DEBUG; `failed_count += 1`; return (no Phase B) |
| Non-404 HTTP response | Leave document unchanged; proceed to Phase B check |
| MongoDB write failure | Propagate exception; worker loop catches and logs |
| Redis connection loss | `redis_client = None`; sleep 2 s; reconnect |

---

## 9. Editing Guardrails

- Do **not** add job discovery or scoring logic to this package.
- The workflow operates exclusively on the `jobs` collection.
- `closed_at` uses the same `{ seconds, nanos }` shape as `created_at` / `updated_at` on job documents.
- The 60-day threshold is defined by `_CLOSED_AFTER_DAYS = 60` in `workflow.py`; do not duplicate this constant elsewhere.
- Queue message serialization lives in `workflow_messages.job_retire_event_to_json` / `parse_job_retire_event`.
