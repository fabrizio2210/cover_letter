# crawler_ats_job_extraction — Specification

**Authoritative reference for the `crawler_ats_job_extraction` package.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [`../SPEC.md`](../SPEC.md)
> Shared references: `../../go/cmd/api/SPEC.md`, `../ai_scorer/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `crawler_ats_job_extraction` package fetches job listings from ATS providers (Greenhouse, Lever, Ashby) for companies that have already been enriched with `ats_provider` + `ats_slug`, filters them by identity roles, upserts them into MongoDB, and optionally enqueues accepted jobs for AI scoring.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Workflow module | `src.python.web_crawler.crawler_ats_job_extraction.workflow` |
| Worker module | `src.python.web_crawler.crawler_ats_job_extraction.worker` |
| Docker CMD | `python -m src.python.web_crawler.crawler_ats_job_extraction.worker --worker` |
| Input queue | `CRAWLER_ATS_JOB_EXTRACTION_QUEUE_NAME` (default `crawler_ats_job_extraction_queue`) |
| Scoring output queue | `JOB_SCORING_QUEUE_NAME` (default `job_scoring_queue`) — optional |
| Progress channel | `CRAWLER_PROGRESS_CHANNEL_NAME` |
| Workflow ID | `crawler_ats_job_extraction` |

---

## 3. Environment Variables

Inherited from `CrawlerConfig`. Relevant subset:

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | MongoDB URI |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `CRAWLER_ATS_JOB_EXTRACTION_QUEUE_NAME` | `crawler_ats_job_extraction_queue` | Input queue |
| `JOB_SCORING_QUEUE_NAME` | `job_scoring_queue` | Scoring enqueue target |
| `CRAWLER_ENABLE_SCORING_ENQUEUE` | `0` | Set to `1` to enqueue jobs after upsert |
| `CRAWLER_HTTP_TIMEOUT_SECONDS` | `20` | ATS API request timeout |
| `CRAWLER_USER_AGENT` | browser-like string | HTTP user-agent header |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | Progress channel |

---

## 4. Responsibilities

- Parse `WorkflowDispatchMessage` from the input queue; drop malformed messages.
- Validate `user_id` on input payload and derive per-user DB name as `cover_letter_<user_id>`.
- Load identity roles from per-user `database["identities"]`; skip extraction entirely if the identity has no roles.
- Load ATS-enriched companies from global `cover_letter_global.companies` (filter: `ats_provider` and `ats_slug` both non-empty).
- For each company call `fetch_jobs(provider, slug, config, session)` from `sources/ats_job_fetcher.py`.
- Filter each returned job with `_job_matches_roles(job, identity_roles)` — case-insensitive substring match against `title` and `description`; skip non-matching jobs.
- Upsert matching jobs into `database["job-descriptions"]` via `upsert_job`; deduplication key is `(platform, external_job_id)`.
- If `CRAWLER_ENABLE_SCORING_ENQUEUE=1` and Redis is available: push `{"user_id": "<jwt sub>", "job_id": "<hex>"}` to the scoring queue.
- New jobs are inserted without score-bearing fields; identity-scoped score lifecycle belongs to `job-preference-scores`.
- Report progress via `progress_callback` when supplied.
- Publish `running` → `completed` / `failed` progress snapshots per dispatch message.

---

## 5. ATS Provider Extraction Contracts

### 5.1 Greenhouse

- Endpoint: `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`
- `content=true` is mandatory to retrieve full role content.
- Extract at least: title, location, content/body, external id, and canonical URL.

### 5.2 Lever

- Endpoint: `https://api.lever.co/v0/postings/{slug}`
- Parse modular sections (requirements, responsibilities, and related structures).
- Preserve meaningful formatting in `description` while normalizing to one text field.

### 5.3 Ashby

- Endpoint: `https://api.ashbyhq.com/posting-api/job-board/{slug}`
- Extract: title, location, description/body, compensation fields when present, apply/source URL, and stable job id.
- Preserve structured optional fields as non-contractual metadata if stored.

ATS fetch logic lives in `../sources/ats_job_fetcher.py`; add new ATS providers there.

---

## 6. Identity Role Filtering

- Identity is loaded from per-user `database["identities"]` by `ObjectId(identity_id)`.
- Empty identity or identity with no roles: skip all ATS extraction for this run and return an empty result.
- `_job_matches_roles(job, roles)`: returns `True` if any role keyword appears as a substring (case-insensitive) in `job.title` or `job.description`. Empty roles list always returns `False`.

**Matching rules**:
- Case-insensitive: `"Software Engineer"`, `"software engineer"`, `"SOFTWARE ENGINEER"` all match a `"software engineer"` role.
- Substring: a role of `"engineer"` matches `"engineering role"` in description.
- Both `title` and `description` are checked independently; either match accepts the job (OR logic).
- Empty `roles` list: no jobs are accepted.

**Behavior**:
- Match → proceed to deduplication and insertion.
- No match → skip, log reason, increment skip counter. No tombstone created.
- Empty `roles` → emit zero jobs, no inserts, no scoring enqueues.

**Examples**:
- Identity roles: `["software engineer", "platform engineer"]`
- `"Senior Software Engineer"` → matches → accepted
- `"Data Scientist"` → no match → skipped
- Description contains `"platform engineering tasks"` → matches → accepted

---

## 7. Job Document Shape

Jobs are stored in the `job-descriptions` collection:

```
{
  title:           string,
  description:     string,
  location:        string,
  platform:        string,   // e.g. "greenhouse", "lever", "ashby"
  external_job_id: string,
  source_url:      string,
  company_id:      ObjectId,
  created_at:      { seconds, nanos },
  updated_at:      { seconds, nanos },
}
```

Upsert on `{platform, external_job_id}`: inserts on first sight, updates `title`, `description`, `location`, `source_url`, `updated_at` on subsequent runs.

---

## 8. Scoring Queue Handoff

Enabled via `config.enable_scoring_enqueue`. When active:
1. After a successful job upsert, push `{"user_id": "<jwt sub>", "job_id": "<hex>"}` to `JOB_SCORING_QUEUE_NAME`.
2. On Redis push failure: log WARNING and continue.

---

## 9. Progress Callback Contract

`progress_callback(completed: int, estimated_total: int, message: str)` is called:
- Once before the loop with `(0, estimated_checks, "Preparing…")`.
- Once per company processed.

`estimate_ats_job_extraction_checks(company_count)` returns `max(company_count, 1)`.

---

## 10. Failure Handling

| Scenario | Behaviour |
|---|---|
| Malformed dispatch message | Drop, log WARNING |
| Missing `user_id`, `run_id`, or `identity_id` | Drop, log WARNING |
| Identity has no roles | Return empty result, log INFO, no jobs emitted |
| Invalid company `_id` | `skipped_count += 1`, log WARNING |
| `fetch_jobs` raises | Record in `failed_companies`, log exception, continue |
| Job upsert fails | `skipped_count += 1`, log exception, continue |
| Redis unavailable (scoring) | Log WARNING, scoring disabled for this run |
| Redis connection loss (worker) | `redis_client = None`, sleep 2 s, reconnect |

---

## 11. Cross-Package Dependency

`workflow.py` imports `_company_from_document` from `src.python.web_crawler.enrichment_ats_enrichment.workflow`. Keep the function signature and field mapping stable across both packages. If this converter is ever extracted to a shared module, update both imports simultaneously.

---

## 12. Editing Guardrails

- Role filtering must be applied **before** `upsert_job`; do not insert jobs that do not match identity roles.
- ATS fetch logic lives in `../sources/ats_job_fetcher.py`; add new ATS providers there and document the endpoint in section 5 of this file.
- Do **not** add company discovery or enrichment logic to this package.
- `platform` value stored in MongoDB must match the ATS provider identifier returned by `fetch_jobs`.
