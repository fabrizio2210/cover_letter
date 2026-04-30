# crawler_levelsfyi — Specification

**Authoritative reference for the `crawler_levelsfyi` package.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [`../SPEC.md`](../SPEC.md)
> Shared references: `../../go/cmd/api/SPEC.md`, `../ai_scorer/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `crawler_levelsfyi` package fetches job listings from Levels.fyi for an identity's role keywords, upserts matching companies and jobs into MongoDB, emits `CompanyDiscoveryEvent` messages for newly discovered companies that need ATS enrichment, and optionally enqueues accepted jobs for AI scoring.

This package combines company discovery and direct job extraction into a single workflow. It does **not** perform ATS URL probing — that is handled by `enrichment_ats_enrichment`.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Workflow module | `src.python.web_crawler.crawler_levelsfyi.workflow` |
| Worker module | `src.python.web_crawler.crawler_levelsfyi.worker` |
| Docker CMD | `python -m src.python.web_crawler.crawler_levelsfyi.worker --worker` |
| Input queue | `CRAWLER_LEVELSFYI_QUEUE_NAME` (default `crawler_levelsfyi_queue`) |
| Enrichment output queue | `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME` — one `CompanyDiscoveryEvent` per new company without `ats_slug` |
| Scoring output queue | `JOB_SCORING_QUEUE_NAME` — optional, per-job |
| Progress channel | `CRAWLER_PROGRESS_CHANNEL_NAME` |
| Workflow ID | `crawler_levelsfyi` |
| Platform identifier | `"levelsfyi"` (stored in `jobs.platform`) |

---

## 3. Environment Variables

Inherited from `CrawlerConfig`. Relevant subset:

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | MongoDB URI |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `CRAWLER_LEVELSFYI_QUEUE_NAME` | `crawler_levelsfyi_queue` | Input queue |
| `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME` | `crawler_enrichment_ats_enrichment_queue` | Enrichment event output |
| `JOB_SCORING_QUEUE_NAME` | `job_scoring_queue` | Scoring enqueue target |
| `CRAWLER_ENABLE_SCORING_ENQUEUE` | `0` | Set to `1` to enqueue jobs after upsert |
| `CRAWLER_LEVELSFYI_MAX_COMPANIES_PER_ROLE` | `50` | Cap on Levels.fyi results per role |
| `CRAWLER_HTTP_TIMEOUT_SECONDS` | `20` | HTTP request timeout |
| `CRAWLER_USER_AGENT` | browser-like string | HTTP user-agent header |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | Progress channel |

---

## 4. Responsibilities

- Parse `WorkflowDispatchMessage` from the input queue; drop malformed messages.
- Validate `user_id` on input payload and derive per-user DB name as `cover_letter_<user_id>`.
- Load identity roles from per-user `database["identities"]`; skip extraction if no roles are present.
- Call `LevelsFyiAdapter.discover_jobs(roles, config)` to fetch job cards.
- Levels.fyi job extraction supports layered parsing: structured JSON in inline scripts first, then company-grouped `/jobs` HTML (company heading + job links), then legacy card markup fallbacks.
- Batch-upsert all discovered companies via `upsert_companies`; build a canonical-name → `ObjectId` lookup.
- For each job card: validate `job_title` or `description` against `identity.roles` using case-insensitive substring matching; skip non-matching cards.
- For each matching job card: resolve the company `ObjectId`; upsert the job via `_upsert_job` with `platform = "levelsfyi"` and dedup key `(platform, external_job_id)`.
- Determine newly discovered companies missing `ats_slug` and emit `CompanyDiscoveryEvent(reason="new_company_or_newly_actionable")` per company to the enrichment queue.
- If `CRAWLER_ENABLE_SCORING_ENQUEUE=1`: enqueue `{"user_id": "<jwt sub>", "job_id": "<hex>"}` to `JOB_SCORING_QUEUE_NAME`.
- Publish `running` → `completed` / `failed` progress snapshots.
- Report intra-run progress via `progress_callback`.

---

## 5. Identity Role Contract

- Roles loaded from per-user `database["identities"]` by `ObjectId(identity_id)`.
- No roles → skip all extraction; log INFO; return empty `WorkflowResult`.
- A job card is accepted only if any role keyword appears in `job_title` or `description` (case-insensitive substring match).
- Non-matching job cards are skipped before job upsert.

---

## 6. Company Upsert Semantics

- Companies are discovered inline from job-card `company_name` fields recovered by the Levels.fyi adapter fallback chain.
- `canonicalize_company_name` is used as the dedup key for the lookup map.
- If a job card's company cannot be resolved after batch upsert, a single-item `upsert_companies` call is made inline; if still unresolvable the job is skipped (`skipped_count += 1`).

---

## 7. Job Document Shape

Jobs are stored in the `job-descriptions` collection:

```
{
  title:           string,
  description:     string,
  location:        string,
  platform:        "levelsfyi",
  external_job_id: string,
  source_url:      string,
  company:         ObjectId,   // NOTE: field name is "company" not "company_id"
  created_at:      { seconds, nanos },
  updated_at:      { seconds, nanos },
}
```

Upsert on `{platform, external_job_id}`: inserts on first sight, updates `title`, `description`, `location`, `source_url`, `updated_at` on subsequent runs.

---

## 8. Enrichment Event Output Contract

```
CompanyDiscoveryEvent {
  user_id:         string
  run_id:          string
  workflow_run_id: string
  workflow_id:     "crawler_levelsfyi"
  identity_id:     string
  company_id:      string  // MongoDB _id hex
  reason:          "new_company_or_newly_actionable"
  emitted_at:      Timestamp
}
```

Only companies without `ats_slug` are included. Events are emitted **after** job upsert is complete.

---

## 9. Scoring Queue Handoff

Same semantics as `crawler_ats_job_extraction`. See that package's SPEC section 7.

---

## 10. Progress Callback Contract

`progress_callback(completed: int, estimated_total: int, message: str)` is called:
- Once before fetching with `(0, 1, "Fetching job listings…")`.
- Once per job upsert iteration.
- Once at completion with a summary message.

---

## 11. Failure Handling

| Scenario | Behaviour |
|---|---|
| Malformed dispatch message | Drop, log WARNING |
| Missing `user_id` or `identity_id` | Drop, log WARNING |
| Identity has no roles | Return empty result, log INFO |
| `discover_jobs` returns empty | Return early, report no-jobs progress |
| Parser cannot recover company from any fallback | Job may still be discovered, but unresolved company leads to skip path |
| Company resolution fails after inline upsert | `skipped_count += 1`, log DEBUG |
| Job upsert exception | Append to `failed_urls`, log WARNING, continue |
| Enrichment event push failure | Log WARNING per company, continue |
| Redis unavailable (scoring) | Log WARNING, scoring disabled for this run |
| Redis connection loss (worker) | `redis_client = None`, sleep 2 s, reconnect |

---

## 12. Editing Guardrails

- The `LevelsFyiAdapter` lives in `../sources/levelsfyi.py`; changes to its `LevelsFyiJobCard` shape must be reflected here.
- Parser updates for Levels.fyi selectors or script payload keys MUST be accompanied by unit tests under `src/python/web_crawler/tests`.
- Do **not** add ATS probing logic to this package; newly discovered companies are forwarded to `enrichment_ats_enrichment` via events.
- The `platform` value stored in MongoDB must remain `"levelsfyi"` to match `external_job_id` dedup semantics.
- Job field name is `"company"` (not `"company_id"`) for levelsfyi jobs; do not change it without updating downstream scoring and API queries.
