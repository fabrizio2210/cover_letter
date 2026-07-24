# crawler_4dayweek — Specification

**Authoritative reference for the `crawler_4dayweek` package.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [`../SPEC.md`](../SPEC.md)
> Shared references: `../../go/cmd/api/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `crawler_4dayweek` package discovers and extracts job listings from [4dayweek.io](https://4dayweek.io), a curated job board for remote and 4-day workweek positions. It resolves or creates companies, upserts normalized job records into MongoDB, and optionally enqueues accepted jobs for AI scoring.

This package must apply identity role filtering before job persistence, using the same matching rule as `crawler_ats_job_extraction` and `crawler_levelsfyi`.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Workflow module | `src.python.web_crawler.crawler_4dayweek.workflow` |
| Worker module | `src.python.web_crawler.crawler_4dayweek.worker` |
| Docker CMD | `python -m src.python.web_crawler.crawler_4dayweek.worker --worker` |
| Input queue | `CRAWLER_4DAYWEEK_QUEUE_NAME` (default `crawler_4dayweek_queue`) |
| Scoring output queue | `JOB_SCORING_QUEUE_NAME` — optional, per-job |
| Progress channel | `CRAWLER_PROGRESS_CHANNEL_NAME` |
| Workflow ID | `crawler_4dayweek` |
| Platform identifier | `"4dayweek"` (stored in `jobs.platform`) |

---

## 3. Environment Variables

Inherited from `CrawlerConfig`. Relevant subset:

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | MongoDB URI |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `CRAWLER_4DAYWEEK_QUEUE_NAME` | `crawler_4dayweek_queue` | Input queue |
| `JOB_SCORING_QUEUE_NAME` | `job_scoring_queue` | Scoring enqueue target |
| `CRAWLER_ENABLE_SCORING_ENQUEUE` | `0` | Set to `1` to enqueue jobs after upsert |
| `CRAWLER_HTTP_TIMEOUT_SECONDS` | `20` | HTTP request timeout |
| `CRAWLER_USER_AGENT` | browser-like string | HTTP user-agent header |
| `CRAWLER_REFERER` | `https://4dayweek.io/jobs` | Referer header for 4dayweek requests |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | Progress channel |

---

## 4. Responsibilities

- Parse `WorkflowDispatchMessage` from the input queue; drop malformed messages.
- Validate `user_id` on input payload and derive per-user DB name as `cover_letter_<user_id>`.
- Discover jobs from the 4dayweek v2 API or list-page fallback (see section 5).
- Deduplicate discovered URLs before extraction.
- For each job URL: extract job and company details using JSON-LD then DOM fallback (see section 6).
- Resolve or create company in `companies` using canonicalized company name.
- Validate extracted jobs against per-user `identity.roles` before persistence; skip non-matching jobs.
- Upsert matching jobs into `job-descriptions` with `platform = "4dayweek"` and dedup key `(platform, external_job_id)`.
- If `CRAWLER_ENABLE_SCORING_ENQUEUE=1`: enqueue `{"user_id": "<jwt sub>", "job_id": "<hex>", "identity_id": "<hex>"}` to `JOB_SCORING_QUEUE_NAME`.
- Publish `running` → `completed` / `failed` progress snapshots.
- Role filtering is required before any job upsert.

---

## 5. Job URL Discovery

### 5.1 Preferred Strategy: API Pagination

1. Fetch `https://4dayweek.io/api/v2/jobs` with `page` and `limit` parameters.
2. Continue until the response has `has_more = false`.
3. Extract each job from the current API fields, including `work_arrangement` and `locations`.
4. Deduplicate jobs by canonical source URL before returning them.

### 5.2 Fallback Strategy: List-Page Crawl

- Start from `https://4dayweek.io/jobs`.
- Follow page links and capture `/job/` or `/remote-job/` detail URLs.
- Parse each detail page according to section 6.

---

## 6. Extraction Contract

### 6.1 Extraction Order

1. Prefer the v2 API payload during normal discovery.
2. For HTML fallback pages, prefer JSON-LD in `<script type="application/ld+json">` with `JobPosting` schema.
3. Fall back to DOM parsing when JSON-LD is absent or incomplete.

### 6.2 Minimum Extraction Targets

- Title
- Company name
- Description/body
- Location/remote signal
- Salary or compensation when present
- Canonical job URL

### 6.3 Location Normalization

- Treat `work_arrangement = "remote"` as remote even when legacy `is_remote` is absent.
- Read string-valued geographic data from `locations`, ordering entries whose `is_primary` value is the boolean `true` first and removing duplicates; use `region` when `state` is unusable and `continent` only when no country, state, region, or city is available.
- Store geographically restricted remote jobs as `Remote (Country or Continent A, Country or Continent B)`.
- Store unrestricted remote jobs as `Remote`.
- Store non-remote locations as `City, State, Country`, separated by semicolons when multiple locations exist.
- Continue accepting legacy `remote_allowed` and `office_locations` fields for compatibility; remote office restrictions retain their full `City, State, Country` precision.
- Treat current entries without usable string-valued geography as absent so valid legacy data can still be used.
- For JSON-LD, accept bare `JobPosting` types and types that resolve to the schema.org namespace, including mapped compact IRIs; ignore foreign namespaces.
- Derive JSON-LD remote restrictions only from `applicantLocationRequirements`; use `jobLocation` for non-remote physical locations.

### 6.4 `external_job_id` Strategy

- **Primary**: parse the terminal alphanumeric id from the job URL pattern `...-{id}`.
- **Fallback**: if the id cannot be parsed, use a stable hash of the canonical `source_url` path.
- This key must remain stable across recrawls for deduplication.

---

## 7. Job Document Shape

Jobs are stored in the `job-descriptions` collection:

```
{
  title:           string,
  description:     string,
  location:        string,
  platform:        "4dayweek",
  external_job_id: string,
  source_url:      string,
  company:         ObjectId,
  created_at:      { seconds, nanos },
  updated_at:      { seconds, nanos },
}
```

Upsert on `{platform, external_job_id}`: inserts on first sight, updates `title`, `description`, `location`, `source_url`, `updated_at` on subsequent runs.

---

## 8. Scoring Queue Handoff

Same semantics as `crawler_ats_job_extraction`. See that package's SPEC section 7.

---

## 9. Failure Handling

| Scenario | Behaviour |
|---|---|
| Malformed dispatch message | Drop, log WARNING |
| Missing `user_id`, `run_id`, or `identity_id` | Drop, log WARNING |
| Sitemap fetch fails | Fall back to list-page crawl; if both fail, publish `failed` progress |
| Job URL extraction fails | Log WARNING, skip URL, continue |
| Company resolution fails | Log WARNING, skip job, continue |
| Job upsert exception | Log WARNING, continue |
| Redis unavailable (scoring) | Log WARNING, scoring disabled for this run |
| Redis connection loss (worker) | `redis_client = None`, sleep 2 s, reconnect |

---

## 10. Editing Guardrails

- 4dayweek API and HTML extraction logic lives in `fourdayweek.py`.
- The `platform` value stored in MongoDB must remain `"4dayweek"` to match `external_job_id` dedup semantics.
- `CRAWLER_REFERER` must be included in HTTP request headers for 4dayweek requests to reduce bot blocking.
- `external_job_id` derivation (section 6.4) must remain stable; do not change the primary parsing strategy without migrating existing records.
