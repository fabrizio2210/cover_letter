# dispatcher — Specification

**Authoritative reference for the `dispatcher` package.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [`../SPEC.md`](../SPEC.md)
> Shared references: `../../go/cmd/api/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `dispatcher` package is the public entry point for the web-crawler system.
It consumes raw crawl-trigger requests from a Redis queue, validates their payload, bootstraps a progress record, and fans out `WorkflowDispatchMessage` messages to every parallel crawler workflow queue.

The dispatcher has **no knowledge of business extraction logic**. It does not touch MongoDB, does not load identities, and does not inspect job or company data.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Module | `src.python.web_crawler.dispatcher.main` |
| Docker CMD | `python -m src.python.web_crawler.dispatcher.main --worker` |
| Execution style | Long-lived Redis queue worker (`--worker` flag required) |
| Input queue | `CRAWLER_TRIGGER_QUEUE_NAME` (default `crawler_trigger_queue`) |
| Output queues | `CRAWLER_YCOMBINATOR_QUEUE_NAME`, `CRAWLER_HACKERNEWS_QUEUE_NAME`, `CRAWLER_ATS_JOB_EXTRACTION_QUEUE_NAME`, `CRAWLER_LEVELSFYI_QUEUE_NAME`, `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME` |
| Progress channel | `CRAWLER_PROGRESS_CHANNEL_NAME` (publishes initial `queued` snapshot) |

---

## 3. Environment Variables

Inherited from `CrawlerConfig`. Relevant subset:

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | MongoDB URI for company fan-out query |
| `DB_NAME` | `cover_letter` | MongoDB database name |
| `REDIS_HOST` | `localhost` | Redis connection host |
| `REDIS_PORT` | `6379` | Redis connection port |
| `CRAWLER_TRIGGER_QUEUE_NAME` | `crawler_trigger_queue` | Queue this worker blocks on |
| `CRAWLER_YCOMBINATOR_QUEUE_NAME` | `crawler_ycombinator_queue` | Fan-out target |
| `CRAWLER_HACKERNEWS_QUEUE_NAME` | `crawler_hackernews_queue` | Fan-out target |
| `CRAWLER_ATS_JOB_EXTRACTION_QUEUE_NAME` | `crawler_ats_job_extraction_queue` | Fan-out target |
| `CRAWLER_LEVELSFYI_QUEUE_NAME` | `crawler_levelsfyi_queue` | Fan-out target |
| `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME` | `crawler_enrichment_ats_enrichment_queue` | Enrichment fan-out target |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | Progress publication channel |

---

## 4. Responsibilities

- Block on `CRAWLER_TRIGGER_QUEUE_NAME` and consume one `CrawlTrigger` payload at a time.
- Validate that `run_id` and `identity_id` are both non-empty; drop malformed messages with a warning log.
- Publish a single `queued` progress snapshot before dispatching any workflows.
- Generate one `workflow_run_id` per downstream workflow and push one `WorkflowDispatchMessage` per target queue.
- Query MongoDB `companies` collection for all companies that need ATS enrichment (no terminal failure, and `ats_provider` or `ats_slug` missing/null) and push one `CompanyDiscoveryEvent(reason="no_ats_slug")` per company to `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME`.
- Log each dispatched workflow with `run_id`, `workflow_run_id`, and `identity_id`.
- Reconnect to Redis automatically on connection loss and retry after a brief sleep.

**Not responsible for:**
- Loading or validating identity documents from MongoDB.
- Deduplicating active runs for the same identity.
- Any crawl, enrichment, or scoring business logic.

---

## 5. Input Contract

Payload shape consumed from `CRAWLER_TRIGGER_QUEUE_NAME`:

```
CrawlTrigger {
  run_id:      non-empty string   // parent run identifier
  identity_id: non-empty string   // MongoDB identity _id (hex)
}
```

Parsed via `parse_crawl_trigger` from `workflow_messages.py`. Invalid JSON or missing required fields causes the message to be dropped with a `WARNING` log.

---

## 6. Output Contracts

### 6.1 WorkflowDispatchMessage

One message per target crawler workflow pushed to each queue:

```
WorkflowDispatchMessage {
  run_id:           string  // forwarded from trigger
  workflow_run_id:  string  // new UUID hex per workflow
  workflow_id:      string  // e.g. "crawler_ycombinator"
  identity_id:      string  // forwarded from trigger
  trigger_kind:     "public_crawl"
  attempt:          1
  dispatched_at:    Timestamp
}
```

### 6.2 CompanyDiscoveryEvent

One message per unenriched company pushed to `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME`:

```
CompanyDiscoveryEvent {
  run_id:           string  // forwarded from trigger
  workflow_run_id:  string  // single UUID hex shared across all enrichment fan-out events for this trigger
  workflow_id:      "dispatcher"
  identity_id:      string  // forwarded from trigger
  company_id:       string  // MongoDB _id hex
  reason:           "no_ats_slug"
  emitted_at:       Timestamp
}
```

Selection criteria: companies collection documents where `enrichment_ats_enrichment_terminal_failure` does not exist **and** (`ats_provider` is missing/null **or** `ats_slug` is missing/null).

---

## 7. Failure Handling

| Scenario | Behaviour |
|---|---|
| Malformed trigger payload | Drop message, log WARNING, continue |
| Missing `run_id` or `identity_id` | Drop message, log WARNING, continue |
| Redis connection loss | Set `redis_client = None`, sleep 2 s, reconnect on next iteration |
| Fan-out push failure | Propagates as unhandled exception, caught by outer loop, reconnects |

---

## 8. Editing Guardrails

- Do **not** add workflow-specific business logic here.
- MongoDB access is limited to the `companies` collection for enrichment fan-out; no other collections may be queried in this module.
- New parallel workflows must be added as additional `_dispatch_workflow` calls and documented in this SPEC.
- The `trigger_kind` field must remain `"public_crawl"` for dispatcher-originated `WorkflowDispatchMessage` messages.
