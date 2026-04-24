# enrichment_retiring_jobs — Specification

**Authoritative reference for the `enrichment_retiring_jobs` package.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [`../SPEC.md`](../SPEC.md)
> Shared references: `../../go/cmd/api/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `enrichment_retiring_jobs` package continuously runs as a background process to clean up stale job documents from the `jobs` collection. A job is considered stale when its `source_url` points to a non-existent resource (HTTP 404). When this condition is detected the job is enriched with `is_open: false` and `closed_at: <timestamp>`. After 60 days in the closed state the job document is permanently deleted.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Workflow module | `src.python.web_crawler.enrichment_retiring_jobs.workflow` |
| Worker module | `src.python.web_crawler.enrichment_retiring_jobs.worker` |
| Docker CMD | `python -m src.python.web_crawler.enrichment_retiring_jobs.worker --worker` |
| Input queue | `CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME` (default `enrichment_retiring_jobs_queue`) — optional on-demand trigger |
| Progress channel | `CRAWLER_PROGRESS_CHANNEL_NAME` |
| Workflow ID | `enrichment_retiring_jobs` |
| Run interval | `CRAWLER_ENRICHMENT_RETIRING_JOBS_INTERVAL_SECONDS` (default `3600`) |

The worker uses `blpop` with a timeout equal to the configured interval. When no trigger message arrives before the timeout, the workflow runs automatically. When a trigger message is received, the workflow runs immediately. This design allows both periodic background operation and on-demand triggering.

---

## 3. Environment Variables

Inherited from `CrawlerConfig`. Relevant subset:

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | MongoDB URI |
| `DB_NAME` | `cover_letter` | Database name |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME` | `enrichment_retiring_jobs_queue` | Optional on-demand trigger queue |
| `CRAWLER_ENRICHMENT_RETIRING_JOBS_INTERVAL_SECONDS` | `3600` | Seconds between automatic runs |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | Progress channel |
| `CRAWLER_HTTP_TIMEOUT_SECONDS` | `20` | HTTP probe timeout per job URL |
| `CRAWLER_USER_AGENT` | browser-like string | Request user-agent |

---

## 4. Responsibilities

- Continuously probe `source_url` for each open job document (all jobs where `is_open` is not `false`).
- On HTTP 404: write `is_open: false` and `closed_at: { seconds, nanos }` to the job document.
- On any HTTP status other than 404 or on network error: leave the job document unchanged.
- Delete job documents where `is_open` is `false` and `closed_at` is older than 60 days.
- Publish `running` → `completed` / `failed` progress snapshots per run using `identity_id: "system"`.

---

## 5. Processing Phases

### Phase A — Mark closed

1. Query all jobs where `source_url` exists and is non-empty and `is_open` is not `false`.
2. For each job, send an HTTP HEAD request to `source_url` (following redirects, using `CRAWLER_HTTP_TIMEOUT_SECONDS`).
3. If the response status is 404: update the job document with `$set: { is_open: false, closed_at: { seconds: <unix>, nanos: 0 } }`.
4. On any exception or non-404 response: leave the document unchanged; increment `failed_count` on network errors.

### Phase B — Remove expired

1. Query all jobs where `is_open: false` and `closed_at.seconds < (now - 60 days in unix seconds)`.
2. For each matched document: call `delete_one`.
3. Increment `deleted_count` for each deletion.

---

## 6. Job Document Fields Written

| BSON key | Type | Notes |
|---|---|---|
| `is_open` | boolean | Set to `false` when source_url returns HTTP 404 |
| `closed_at` | object | `{ "seconds": <unix>, "nanos": 0 }` — set when `is_open` transitions to `false` |

---

## 7. Failure Handling

| Scenario | Behaviour |
|---|---|
| Network error on HEAD request | Log DEBUG; `failed_count += 1`; skip to next job |
| Non-404 HTTP response | Leave document unchanged; continue |
| MongoDB write failure | Propagate exception; log; continue outer loop |
| Redis connection loss | `redis_client = None`, sleep 2 s, reconnect |

---

## 8. Editing Guardrails

- Do **not** add job discovery or scoring logic to this package.
- The workflow operates exclusively on the `jobs` collection.
- `closed_at` uses the same `{ seconds, nanos }` shape as `created_at` / `updated_at` on job documents.
- The 60-day threshold is defined by `_CLOSED_AFTER_DAYS = 60` in `workflow.py`; do not duplicate this constant elsewhere.
