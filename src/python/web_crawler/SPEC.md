# Web Crawler Specification

**This file is the authoritative reference for the Python web crawler service.**
Agents editing files in this folder MUST consult this file before making changes.

It exists to prevent contract drift between crawl adapters, MongoDB job documents, company resolution behavior, and downstream scoring queue integration.

> Shared references: `web_crawler.py` (or equivalent entry script), `../../go/cmd/api/SPEC.md`, `../ai_scorer/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `web_crawler` service discovers company targets and job opportunities from role-query job boards, curated startup communities, portfolio directories, community hiring threads, and ATS providers, normalizes postings to the shared internal job shape, and persists them into MongoDB.

This document covers:
- runtime behavior of the crawler service;
- environment variables required by the crawler;
- discovery contracts for role-first company discovery, ATS validation, and slug resolution;
- extraction contracts per source platform;
- normalization and deduplication rules;
- MongoDB persistence shape and default lifecycle values;
- optional scoring queue handoff contract;
- failure handling, rate limiting, and anti-bot posture.

This document does **not** define:
- HTTP routes and frontend behavior;
- AI scoring prompt design;
- cover-letter generation and refinement behavior;
- email delivery behavior.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Language | Python |
| Service folder | `src/python/web_crawler` |
| Execution style | On-demand run, Redis-driven worker mode, or scheduled batch run |
| Data store | MongoDB |
| Queue integration | Redis consumer for parent crawl requests, Redis publisher for crawl progress, internal workflow trigger messages, optional Redis producer for job scoring |
| Source types | ATS APIs (Greenhouse, Lever, Ashby), role-query job boards and search sources (LinkedIn, Indeed, SimplyHired, Built In, Otta), curated startup and job communities (Y Combinator, Wellfound, Work at a Startup), portfolio and investor directories (Crunchbase, Techstars, 500 Global, a16z, Sequoia), community hiring threads (Hacker News Who Is Hiring), and aggregator HTML pages (4dayweek.io) |

The crawler supports two runtime modes:
1. bounded execution for one explicit `identity_id`;
2. long-lived worker mode that listens on Redis for crawl requests and runs the same identity-scoped execution flow on demand.

Each accepted crawl request generates a parent `run_id`. The crawler fans out modular workflow executions under that parent run. Each workflow execution attempt also gets its own `workflow_run_id`, allowing retries and singular workflow triggers to remain unambiguous while still belonging to the same parent run.

The crawler uses two workflow kinds:
1. crawler workflows, which discover jobs, companies, or both;
2. enrichment workflows, which enrich a single entity (company or job). Enrichment workflows are not limited to ATS-related processing; they can be generic and operate on any entity type. For example, `enrichment_ats_enrichment` enriches company documents with ATS provider and slug metadata, while `enrichment_retiring_jobs` enriches job documents with closure state and removes stale entries.

Module naming conventions:
- `*_worker.py`: long-lived Redis queue workers and CLI entrypoints.
- `*_workflow.py`: pure workflow/business-logic orchestration modules.

Stable workflow identifiers:
- `crawler_ycombinator`
- `crawler_hackernews`
- `crawler_linkedin`
- `crawler_indeed`
- `crawler_simplyhired`
- `crawler_builtin`
- `crawler_otta`
- `crawler_wellfound`
- `crawler_workatastartup`
- `crawler_crunchbase`
- `crawler_techstars`
- `crawler_500global`
- `crawler_a16z`
- `crawler_sequoia`
- `enrichment_ats_enrichment`
- `enrichment_retiring_jobs`
- `crawler_ats_job_extraction`
- `crawler_4dayweek`
- `crawler_levelsfyi`

High-level orchestration:
1. Load crawler configuration, enabled sources, explicit `identity_id`, and the selected identity's `roles` list.
2. Accept one public crawl request keyed by parent `run_id` and `identity_id`.
3. Fan out all UI-triggered crawler workflows in parallel under the same parent `run_id`, each with its own `workflow_run_id`.
4. Persist workflow outputs immediately to MongoDB so downstream workflows can consume partial progress without waiting for parent-run completion.
5. Emit company-discovery events only when a company is newly inserted or when an existing company becomes newly actionable for ATS enrichment.
6. Consume those company-discovery events in `enrichment_ats_enrichment` and emit ATS-job-trigger events when `ats_provider` plus `ats_slug` become available.
7. Execute ATS-backed crawler workflows, such as `crawler_ats_job_extraction`, from those follow-up triggers, either as part of the same parent run or as singular workflow message executions.
8. Publish workflow-level progress snapshots to Redis, each carrying parent `run_id` plus `workflow_run_id` and `workflow_id`.
9. Optionally enqueue `{ "user_id": "...", "job_id": "..." }` messages for scoring after each successful job insert/update.
10. Emit crawl summary logs and counters, including per-workflow success/failure counts and parent-run completion state.

---

## 3. Environment Variables

| Variable | Default | Required | Used for |
|---|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | Yes | MongoDB connection URI |
| `REDIS_HOST` | `localhost` | No | Redis host for scoring queue output |
| `REDIS_PORT` | `6379` | No | Redis port for scoring queue output |
| `CRAWLER_TRIGGER_QUEUE_NAME` | `crawler_trigger_queue` | No | Redis queue name for crawl requests consumed by the worker |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | No | Redis channel used to publish crawl progress snapshots |
| `CRAWLER_YCOMBINATOR_QUEUE_NAME` | `crawler_ycombinator_queue` | No | Redis queue consumed by the `crawler_ycombinator` worker |
| `CRAWLER_HACKERNEWS_QUEUE_NAME` | `crawler_hackernews_queue` | No | Redis queue consumed by the `crawler_hackernews` worker |
| `CRAWLER_ATS_JOB_EXTRACTION_QUEUE_NAME` | `crawler_ats_job_extraction_queue` | No | Redis queue consumed by the `crawler_ats_job_extraction` worker |
| `CRAWLER_LEVELSFYI_QUEUE_NAME` | `crawler_levelsfyi_queue` | No | Redis queue consumed by the `crawler_levelsfyi` worker |
| `JOB_SCORING_QUEUE_NAME` | `job_scoring_queue` | No | Redis queue name for scoring payloads |
| `CRAWLER_ENABLE_SCORING_ENQUEUE` | `0` | No | If `1`, enqueue job ids after successful persistence |
| `CRAWLER_HTTP_TIMEOUT_SECONDS` | `20` | No | HTTP timeout per request |
| `CRAWLER_MAX_RETRIES` | `3` | No | Retry limit for transient failures |
| `CRAWLER_BASE_DELAY_MS` | `1500` | No | Baseline delay between requests |
| `CRAWLER_MAX_DELAY_MS` | `15000` | No | Max backoff delay |
| `CRAWLER_USER_AGENT` | browser-like UA string | No | Request header to reduce bot blocking |
| `CRAWLER_REFERER` | `https://4dayweek.io/jobs` | No | Referer for 4dayweek requests |
| `CRAWLER_LEVELSFYI_MAX_COMPANIES_PER_ROLE` | `50` | No | Cap on company discoveries retained per identity role from Levels.fyi |
| `CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME` | `enrichment_retiring_jobs_queue` | No | Input queue for the `enrichment_retiring_jobs` worker — one message per job to check |

Platform-specific configuration may include source names, ATS slugs, and source URLs (via config file or environment).

Rules:
- Global crawler reads/writes use `cover_letter_global`.
- Per-user identity and crawl state reads/writes use `cover_letter_<user_id>`.
- Worker mode requires Redis connectivity for both crawl request consumption and progress publication.
- If `CRAWLER_ENABLE_SCORING_ENQUEUE=1`, Redis connectivity is required for queue handoff.
- Missing platform credentials are not fatal when the platform can be scraped from public endpoints.

---

## 4. Responsibilities

The crawler is responsible for:
- consuming parent crawl requests from Redis and starting identity-scoped runs;
- rejecting duplicate active crawl requests for the same identity;
- fanning out workflow executions under one parent `run_id`;
- assigning `workflow_run_id` values for workflow attempts and retries;
- publishing workflow-level progress snapshots for active runs so the API can relay them to clients;
- taking an explicit `identity_id` plus role-focused discovery input and using it to discover actively hiring companies;
- emitting company-discovery events only when a company is newly inserted or becomes newly actionable for ATS enrichment;
- consuming company-discovery events to discover company slugs for ATS-hosted boards when not preconfigured;
- validating ATS compatibility from company careers pages before slug resolution;
- emitting ATS-job-trigger events after successful ATS enrichment;
- extracting jobs from Greenhouse, Lever, Ashby, and 4dayweek.io;
- mapping heterogeneous payloads into one normalized job schema;
- resolving or creating companies before persisting jobs, using canonicalized company names for matching;
- idempotent upsert of jobs on repeated crawls;
- writing lifecycle defaults and transitions for scoring-related fields;
- optionally enqueueing scoring jobs;
- continuing processing when one source partially fails.

The crawler is not responsible for:
- deterministic weighted score computation;
- writing per-preference scoring results;
- exposing HTTP endpoints or pushing directly to browser clients;
- requiring external enrichment APIs for company-to-domain resolution;
- cover-letter writing or refinement;
- auth, JWT, or API route validation.

---

## 5. Source Discovery Contracts

### 5.1 Discovery Input: Role and Identity Mapping

The crawler discovery input must include:
- `user_id` (required, JWT `sub` copied from API queue payload).
- `identity_id` (required, hex MongoDB ObjectId string).
- `run_id` (required in Redis-driven worker mode; server-generated unique crawl run identifier).

The selected identity must include:
- `roles` (required for role-first discovery): user-maintained list of role keywords, for example `software engineer`, `platform engineer`.

Runs missing `user_id` or `identity_id` are invalid and must fail fast before discovery starts.

Only one active crawl per `identity_id` is allowed at a time. In worker mode, a second request for the same identity must be rejected and reported through a terminal progress event with `status = "rejected"`.

Identity role rules:
- `roles` are manually typed and curated by the user on the identity profile.
- The crawler uses `identity.roles` as the primary query seed for company discovery.
- Query expansion may add normalized variants (case/spacing/seniority synonyms), but must not drop the original user-entered role terms.

Boundary with scoring preferences:
- Identity `preferences` remain scoring inputs and are not required to seed role discovery.
- Role filters are applied at discovery-query time.
- Weighted score computation remains outside crawler ownership.

Query construction rules:
- Build one or more role queries from `identity.roles` and optional identity context.
- Keep query terms broad enough for discovery, then narrow results with ATS validation and dedup.
- Do not mutate scoring contracts, queue payloads, or persistence keys when applying role filters.

### 5.2 Source Families

The discovery workflows draw from four classes of discovery sources. Full source tables, per-source behavior, Levels.fyi public-route strategy, extraction expectations, and compliance notes live in the respective `crawler_*/SPEC.md` files.

- **Role-query job boards and search sources**: LinkedIn, Indeed, SimplyHired, Built In, Levels.fyi, Otta — primary role-filtered hiring signals.
- **Curated startup and job communities**: Y Combinator, Wellfound, Work at a Startup — supplementary high-signal company discovery.
- **Portfolio and investor directories**: Crunchbase, Techstars, 500 Global, a16z, Sequoia — company-first enrichment candidates.
- **Community hiring threads**: Hacker News "Who Is Hiring" — opportunistic lower-structure discovery.

ATS compatibility detection signatures, slug resolution strategy, and search-dork query patterns belong to `enrichment_ats_enrichment`. See [`enrichment_ats_enrichment/SPEC.md`](enrichment_ats_enrichment/SPEC.md) sections 5 and 8.

4dayweek.io URL discovery strategy, extraction order, and `external_job_id` derivation belong to `crawler_4dayweek`. See [`crawler_4dayweek/SPEC.md`](crawler_4dayweek/SPEC.md) sections 5–6.

### 5.3 End-to-End Discovery Workflow

Engineer-oriented sequence:
1. Load `identity_id` and `identity.roles`.
2. Create one parent `run_id` for the accepted crawl request.
3. Fan out all UI-triggered crawler workflows in parallel, each with a distinct `workflow_run_id` and stable `workflow_id`.
4. Persist workflow outputs immediately to MongoDB.
5. Emit company-discovery events only for newly inserted companies or for existing companies whose updated metadata becomes newly actionable for ATS enrichment.
6. Run enrichment workflows from those discovery events.
7. Emit ATS-job-trigger events when ATS enrichment resolves both `ats_provider` and `ats_slug`.
8. Run ATS-backed crawler workflows from those triggers.
9. Normalize, upsert, publish workflow progress, and optionally enqueue scoring payloads.

### 5.4 Modular Workflow Contracts

The crawler is organized as independently triggerable workflows that can participate in one parent run or execute singularly by message. Implementation details, processing logic, extraction strategies, and per-source behavior live in the owning workflow's submodule spec.

#### Workflow Kind A: Crawler Workflows

Crawler workflows discover jobs, companies, or both. They may be UI-triggered, event-triggered, or both.

Stable crawler workflow identifiers:
- `crawler_ycombinator`
- `crawler_ats_job_extraction`
- `crawler_4dayweek`
- `crawler_levelsfyi`

Common output rules for crawler workflows:
- Job outputs are normalized and upserted into `job-descriptions`.
- Successful job insert/update may enqueue `{ "job_id": "..." }` for scoring.
- Company outputs are resolved and upserted into `companies`.
- Company-discovery events are emitted only when a company is newly inserted or when an existing company is updated such that its metadata becomes newly actionable for ATS enrichment.
- A company is newly actionable for ATS enrichment when persisted metadata becomes sufficient for the enrichment workflow to attempt ATS validation or slug resolution, for example a first usable domain, careers URL, or hosted ATS URL.
- Each crawler workflow must expose terminal workflow counters for dashboard visibility of the last completed parent run: `discovered_jobs` and `discovered_companies`.
- `discovered_jobs` and `discovered_companies` are persisted-result counters only (`inserted + updated`) and must not include raw pre-filter candidates.
- Workflow visibility counters must be non-negative integers.
- Successful crawler workflow completions also increment global cumulative discovered-jobs counters in MongoDB (`stats` collection) using `inserted + updated` deltas.
- Cumulative counters are keyed by `workflow_id` and exposed by API endpoint `GET /api/crawls/workflow-cumulative-jobs`.

Workflow visibility counter mapping:
- `crawler_ycombinator`: `discovered_companies` may be positive; `discovered_jobs` must be `0`.
- `crawler_hackernews`: `discovered_companies` may be positive; `discovered_jobs` must be `0`.
- `crawler_ats_job_extraction`: `discovered_jobs` may be positive; `discovered_companies` must be `0`.
- `crawler_4dayweek`: both `discovered_jobs` and `discovered_companies` may be positive.
- `crawler_levelsfyi`: both `discovered_jobs` and `discovered_companies` may be positive.

Dashboard scope rule:
- Dashboard workflow-visibility stats include only `crawler_` workflows from the latest completed parent run.
- `enrichment_` workflows still publish normal progress and internal events but are excluded from this dashboard stat aggregation.

##### `crawler_ycombinator`

Input: parent `run_id`, `workflow_run_id`, `user_id`, `identity_id`, `identities.roles`.

DB writes: upsert into `companies` using canonicalized company name; preserve source attribution metadata.

See [`crawler_ycombinator/SPEC.md`](crawler_ycombinator/SPEC.md) for details.

##### `crawler_hackernews`

Input: parent `run_id`, `workflow_run_id`, `user_id`, `identity_id`, `identities.roles`.

DB writes: upsert into `companies` using canonicalized company name; preserve source attribution metadata.

See [`crawler_hackernews/SPEC.md`](crawler_hackernews/SPEC.md) for details.

##### `crawler_ats_job_extraction`

Input: parent `run_id` (optional for singular execution), `workflow_run_id`, `user_id`, `identity_id`, ATS-job-trigger event or singular workflow trigger.

DB writes: upsert into `job-descriptions` using (`platform`, `external_job_id`) only for jobs that pass role filtering; optionally enqueue scoring payload.

Role filtering: each extracted job is validated against `identity.roles` before insertion. See [`crawler_ats_job_extraction/SPEC.md`](crawler_ats_job_extraction/SPEC.md) sections 5–6 for ATS provider endpoints and filtering rules.

##### `crawler_4dayweek`

Input: parent `run_id`, `workflow_run_id`, identity-scoped public crawl request fan-out.

DB writes: resolve/create company in `companies`; upsert job into `job-descriptions` with `platform=4dayweek`.

Role filtering: each extracted job must be validated against `identity.roles` before insertion.

See [`crawler_4dayweek/SPEC.md`](crawler_4dayweek/SPEC.md) for URL discovery strategy and extraction contract.

##### `crawler_levelsfyi`

Input: parent `run_id`, `workflow_run_id`, `user_id`, `identity_id`, `identities.roles` (loaded from identity document).

DB writes: upsert into `companies`; upsert into `job-descriptions` with `platform=levelsfyi`; stable dedup key: (`platform`, `external_job_id`).

Role filtering: each extracted job is validated against `identity.roles` before insertion. See [`crawler_levelsfyi/SPEC.md`](crawler_levelsfyi/SPEC.md) for discovery strategy and filtering rules.

#### Workflow Kind B: Enrichment Workflows

Enrichment workflows consume company-discovery events, add ATS metadata, and emit follow-up job extraction triggers.

Stable enrichment workflow identifier:
- `enrichment_ats_enrichment`

##### `enrichment_ats_enrichment`

Input: parent `run_id` (optional), `workflow_run_id`, company-discovery event.

DB writes: update company document with `ats_provider` and `ats_slug`.

Output: when both `ats_provider` and `ats_slug` become available, emit an ATS-job-trigger event for `crawler_ats_job_extraction`.

See [`enrichment_ats_enrichment/SPEC.md`](enrichment_ats_enrichment/SPEC.md) for ATS detection signatures, slug resolution sequence, and terminal failure recording.

### 5.5 Workflow Triggering, Dependency, and Persistence Policy

Triggering rules:
- A public crawl request remains identity-scoped and starts the default UI-triggered workflow set under one parent `run_id`.
- Singular workflow execution is supported by internal messages addressed to one `workflow_id`.
- Every workflow execution attempt must have a unique `workflow_run_id`.
- A retry of the same workflow under the same parent `run_id` must get a new `workflow_run_id` while retaining the same `workflow_id`.

Dependency rules:
- `crawler_ycombinator` and `crawler_4dayweek` are UI-triggered crawler workflows and can start in parallel.
- `crawler_levelsfyi` is a UI-triggered crawler workflow and starts in parallel with `crawler_ycombinator` and `crawler_4dayweek`.
- `enrichment_ats_enrichment` depends on company-discovery events emitted by crawler workflows.
- `crawler_ats_job_extraction` depends on ATS-job-trigger events emitted by `enrichment_ats_enrichment`, but it can be triggered by UI as well.
- Parent-run completion is derived when all required UI-triggered workflows and all spawned child workflows for that parent `run_id` reach terminal states.

Internal event rules:
- Company-discovery events are emitted only when a company is newly inserted or becomes newly actionable for ATS enrichment.
- A metadata update that does not change ATS-enrichment eligibility must not emit a new company-discovery event.
- ATS-job-trigger events are emitted only when ATS enrichment resolves a valid provider plus slug pair.
- Internal event replay must be safe under idempotent company/job upserts.

Persistence policy:
- Every workflow writes results immediately to MongoDB.
- Partial successes are persisted.
- Per-company or per-source failures are logged and do not trigger global rollback.
- The parent run continues unless run-level abort conditions from section 12 occur.
- Expanding crawler workflows must not change the scoring queue payload contract or MongoDB field names.

---

## 6. Extraction Contracts by Platform

Platform-specific extraction logic (ATS provider endpoints, parsing strategies, `external_job_id` derivation) lives in the owning workflow's submodule spec:

- Greenhouse, Lever, Ashby: [`crawler_ats_job_extraction/SPEC.md`](crawler_ats_job_extraction/SPEC.md) section 5
- 4dayweek.io: [`crawler_4dayweek/SPEC.md`](crawler_4dayweek/SPEC.md) section 6
- Levels.fyi: [`crawler_levelsfyi/SPEC.md`](crawler_levelsfyi/SPEC.md) sections 4–6

---

## 7. Normalization and Unified Job Schema

All source-specific records must map to the shared job shape used by the API and scorer.

Required normalized fields:
- `title`
- `description`
- `location`
- `platform`
- `external_job_id`
- `source_url`
- `company` (reference to `companies`)

Normalization rules:
- `platform` must be one of: `greenhouse`, `lever`, `ashby`, `4dayweek`, `levelsfyi`.
- `external_job_id` must be stable across recrawls for same source job.
- `source_url` should be the canonical job page URL.
- `description` may be converted from HTML to text, but content loss should be minimized.

Optional non-contract metadata may be stored in source-specific fields, but must not replace required keys above.

---

## 8. Idempotency, Deduplication, and Role-Based Filtering

### 8.1 Idempotency and Deduplication

Repeated crawls must not create duplicate job records for the same external posting.

Primary dedup key:
- (`platform`, `external_job_id`)

Secondary reconciliation signals (best effort):
- `source_url`
- normalized (`company`, `title`, `location`) tuple

Persistence behavior:
- If existing job is found by dedup key, update mutable fields and `updated_at`.
- If not found, insert new document with lifecycle defaults.

Mutable field updates should preserve contract keys while allowing refreshed description/location updates from source.

### 8.2 Role-Based Filtering Before Insertion

Role filtering is a validation gate applied before any job insert/update in job-producing crawler workflows. It is not applied in `crawler_ycombinator` or `enrichment_ats_enrichment` because those workflows do not persist jobs.

Filtering mechanism and matching rules:
- `crawler_ats_job_extraction`: see [`crawler_ats_job_extraction/SPEC.md`](crawler_ats_job_extraction/SPEC.md) section 6.
- `crawler_levelsfyi`: see [`crawler_levelsfyi/SPEC.md`](crawler_levelsfyi/SPEC.md) section 5.
- `crawler_4dayweek`: see [`crawler_4dayweek/SPEC.md`](crawler_4dayweek/SPEC.md).

Rules that apply everywhere:
- Do NOT store `identity_id`, `role_matched`, or other role-tracking fields on job documents.
- Role filtering state is not persisted downstream; it is a per-execution validation gate only.
- Empty `roles` list on the identity: emit zero jobs for that run in role-filtered job workflows (no inserts, no updates, no scoring enqueues).

---

## 9. MongoDB Contract

### 9.1 Collections Used

| Collection | Access | Purpose |
|---|---|---|
| `job-descriptions` (global DB) | read/insert/update | Store normalized job records |
| `companies` (global DB) | read/insert/update | Resolve, create, and ATS-enrich company documents |
| `identities` (per-user DB) | read | Resolve identity roles by `user_id + identity_id` |
| `crawls` (per-user DB) | optional insert/update | Store crawl run summaries/telemetry if implemented |

### 9.2 Required Job Fields on Insert

| BSON key | Type | Notes |
|---|---|---|
| `title` | string | Normalized job title |
| `description` | string | Job description body |
| `location` | string | Free-form location string |
| `platform` | string | Source platform key |
| `external_job_id` | string | Platform-native id |
| `source_url` | string | Canonical source URL |
| `company` | ObjectId | Ref to `companies` (`company_id` in API JSON) for new writes |
| `created_at` | object | `{ "seconds": <unix>, "nanos": 0 }` |
| `updated_at` | object | `{ "seconds": <unix>, "nanos": 0 }` |

Reference compatibility note:
- New crawler writes for references must use MongoDB `ObjectId`.
- Legacy string references may still exist and must be tolerated during reads.

Role filtering note:
- Jobs inserted at this stage have already passed role-based filtering (section 8.2).
- Do not add `identity_id` or `role_matched` fields to job documents during insertion.
- Role filtering is a validation gate, not persisted metadata.

### 9.3 Company Resolution Contract

Resolution order:
1. If source config already maps to a known company id, use it.
2. Else resolve by normalized company name.
3. If no match exists, create company document and use new id.

Company creation behavior:
- Canonicalize company name for storage and matching (single stored value; no separate display-name field).
- Use best-effort fallback description when no reliable description is available (for example a short extracted snippet). If unavailable, use empty description.
- Field linkage may be unresolved at creation time and can be assigned later by application flows.

The crawler must tolerate mixed reference storage (`ObjectId` and string) in related documents where legacy data exists.

### 9.4 Company ATS Enrichment Fields

`enrichment_ats_enrichment` writes ATS metadata directly on company documents.

| BSON key | Type | Notes |
|---|---|---|
| `ats_provider` | string | Nullable; one of `greenhouse`, `lever`, `ashby` |
| `ats_slug` | string | Nullable; provider-specific slug used for ATS extraction |

Rules:
- Missing ATS compatibility leaves both fields unset.
- `ats_slug` is only valid when `ats_provider` is set.
- Legacy company documents may not include these fields and must remain readable.

---

## 10. Redis Scoring Queue Handoff Contract

Queue handoff is configurable and disabled by default.

When `CRAWLER_ENABLE_SCORING_ENQUEUE=1`:
- Queue name: env `JOB_SCORING_QUEUE_NAME` (default `job_scoring_queue`).
- Payload shape per job:

```json
{
  "user_id": "<jwt sub>",
  "job_id": "<job description hex object id>"
}
```

Rules:
- Enqueue only after successful insert or update with a valid document id.
- On job updates from recrawls, always re-enqueue when enqueue is enabled.
- Enqueue payload must use key name `job_id` exactly.
- If enqueue fails, persistence remains committed, the error is logged, and crawler continues.

Ownership boundary:
- Crawler produces job ids for scoring.
- `ai_scorer` consumes queue and owns all score-document lifecycle and aggregate fields.
- Deterministic aggregate ranking remains outside crawler responsibility.

---

## 11. Redis Crawl Trigger And Progress Contract

### 11.1 Crawl Request Consumption

In worker mode, the crawler listens on `CRAWLER_TRIGGER_QUEUE_NAME` using blocking Redis queue consumption.

Expected payload:

```json
{
  "user_id": "<jwt sub>",
  "run_id": "<crawl run id>",
  "identity_id": "<identity hex object id>",
  "requested_at": { "seconds": 1711234567, "nanos": 0 }
}
```

Rules:
- Missing `user_id`, `run_id`, or `identity_id` is a malformed request and must be rejected.
- The worker must emit an initial parent-run `queued` snapshot or immediate workflow `running` snapshots for accepted work.
- Only one active crawl may exist for a given `identity_id`.
- If another crawl is already active for the same `identity_id`, the worker must not start a second run. It must instead publish a terminal progress snapshot with `status = "rejected"` and `reason = "already_running"`.
- The public crawl request does not need to name a specific workflow. Workflow fan-out happens inside the crawler orchestration layer.

Internal workflow-trigger messages are crawler-owned contracts and are not part of the public API payload.

Implementation note:
- Internal workflow messages should use `common.proto` generated types for exchange (`WorkflowDispatchMessage`, `CompanyDiscoveryEvent`, `AtsJobTriggerEvent`) instead of ad-hoc dictionary payloads.
- Redis payload transport may serialize those proto messages as JSON at rest, but producer/consumer code must construct and parse generated protobuf classes at boundaries.

Required internal routing fields:
- `workflow_id` — stable module key, one of `crawler_ycombinator`, `crawler_hackernews`, `enrichment_ats_enrichment`, `crawler_ats_job_extraction`, `crawler_4dayweek`, `crawler_levelsfyi`
- `workflow_run_id` — unique execution-attempt id for that workflow message
- `run_id` — parent crawl run when the workflow was spawned from a public crawl request; omitted or left empty for singular workflow execution when no parent run exists

#### 11.1.1 Company-Discovery Event Contract

Producer:
- crawler workflows that discover or materially enrich company records

Event emission rule:
- Emit only when a company is newly inserted or when an update makes the company newly actionable for ATS enrichment.

Payload shape:

```json
{
  "run_id": "<parent crawl run id>",
  "workflow_run_id": "<producer workflow attempt id>",
  "workflow_id": "crawler_ycombinator",
  "user_id": "<jwt sub>",
  "identity_id": "<identity hex object id>",
  "company_id": "<company hex object id>",
  "reason": "new_company_or_newly_actionable"
}
```

#### 11.1.2 ATS-Job-Trigger Event Contract

Producer:
- `enrichment_ats_enrichment`

Event emission rule:
- Emit only when ATS enrichment resolves both `ats_provider` and `ats_slug`.

Payload shape:

```json
{
  "run_id": "<parent crawl run id>",
  "workflow_run_id": "<enrichment workflow attempt id>",
  "workflow_id": "enrichment_ats_enrichment",
  "user_id": "<jwt sub>",
  "identity_id": "<identity hex object id>",
  "company_id": "<company hex object id>",
  "ats_provider": "greenhouse",
  "ats_slug": "example-company"
}
```

### 11.2 Progress Publication

The crawler publishes progress snapshots on `CRAWLER_PROGRESS_CHANNEL_NAME` for every accepted run and workflow execution.

Payload:

```json
{
  "run_id": "<crawl run id>",
  "workflow_run_id": "<workflow execution attempt id>",
  "workflow_id": "crawler_ycombinator",
  "identity_id": "<identity hex object id>",
  "status": "running",
  "workflow": "crawler_ycombinator",
  "message": "Collecting company candidates",
  "estimated_total": 120,
  "completed": 36,
  "percent": 30,
  "started_at": { "seconds": 1711234567, "nanos": 0 },
  "updated_at": { "seconds": 1711234600, "nanos": 0 },
  "finished_at": null,
  "reason": ""
}
```

Publication lifecycle:
1. `queued` when the parent request has been accepted but workflow execution has not started yet.
2. `running` when the crawler begins work for a specific `workflow_run_id`.
3. incremental `running` updates during each workflow using best-effort `estimated_total` and `completed` counters.
4. terminal workflow event with `status = "completed"`, `"failed"`, or `"rejected"`.
5. optional parent-run finalization event may omit `workflow_id` and `workflow_run_id` while still carrying the parent `run_id`.

Rules:
- `percent` must be derived from the best available estimate and must stay in the inclusive range `0..100`.
- `workflow_id` must be one of `crawler_ycombinator`, `crawler_hackernews`, `enrichment_ats_enrichment`, `crawler_ats_job_extraction`, `crawler_4dayweek`, `crawler_levelsfyi` when the event represents a workflow contribution.
- `workflow_run_id` must uniquely identify one workflow execution attempt.
- `workflow` should equal `workflow_id` for workflow-level events and may be `queued` or `finalizing` for parent-run lifecycle events.
- `finished_at` is populated only for terminal events.
- Terminal `failed` events should include a short machine-readable `reason` and a human-readable `message`.
- Clients must treat the most recent event per `workflow_run_id` as the authoritative live snapshot for that workflow contribution.
- Multiple active workflow snapshots may coexist under one parent `run_id` and one `identity_id`.
- Dashboard last-run workflow statistics are not inferred from `estimated_total` or `completed`; they are derived from terminal workflow counters defined in section 5.4 and exposed by the API contract.

### 11.3 Failure Tolerance

Progress publication failure does not invalidate crawl persistence work already completed.

Rules:
- If the crawler cannot publish one progress snapshot, it should log the failure and continue the crawl when the main crawl work can still proceed safely.
- If the crawler cannot consume the trigger queue at startup, worker mode cannot start and the process should fail fast.
- If the crawler loses Redis connectivity after starting a run, it should continue best-effort crawl execution and resume progress publication when connectivity returns if practical.

---

## 12. Rate Limiting, Retry, and Anti-Bot Strategy

Baseline behavior:
- Use realistic browser user-agent and stable request headers.
- Include referer for 4dayweek requests.
- Apply 2-5 second pacing between page fetches for aggregator crawling.

Retry behavior:
- Retry transient failures (`429`, `5xx`, timeout) with exponential backoff and jitter.
- Respect `Retry-After` when provided.
- Stop retrying after configured maximum attempts.

Throughput behavior:
- Use bounded concurrency.
- Avoid burst patterns to one domain.
- For high-volume crawls, support proxy rotation or distributed source partitions.

Parallel workflow behavior:
- Apply bounded worker pools per workflow.
- Do not let one blocked workflow starve others.
- Keep domain-level throttling independent per source type (aggregators, ATS APIs, 4dayweek).

---

## 13. Failure Handling

Recoverable failures (log and continue):
- Invalid source configuration for one source.
- Slug resolution failure for one company.
- Parsing failure for one posting.
- Duplicate-key race conditions on upsert.
- Per-workflow partial failures while other workflows continue.
- Rejected duplicate trigger for an already active identity.
- Redis progress publication failure for one update.
- Missing scoring prerequisites that require `scoring_status=skipped`.
- Redis enqueue failure when scoring enqueue is enabled.

Run-level failures (abort run):
- MongoDB connection unavailable at startup.
- No enabled sources configured.
- Redis trigger-queue connection unavailable at startup when worker mode is enabled.

Each run should emit summary counters:
- sources processed;
- jobs discovered;
- jobs inserted;
- jobs updated;
- jobs skipped;
- progress events published or failed;
- enqueue success/fail counts;
- workflow attempts started/retried/completed;
- run duration.

---

## 14. Editing Guardrails for Agents

Before changing crawler code in this folder:
1. Read this file.
2. Check `../../go/cmd/api/SPEC.md` for canonical job fields and queue payload contract.
3. Check `../ai_scorer/SPEC.md` for scoring consumer expectations.
4. Preserve exact field names in MongoDB documents and queue messages.
5. Update this spec and related service specs together when shared contracts change.

**Role-based filtering guardrails**:
- Role filtering (section 8.2) is a validation gate in `crawler_ats_job_extraction` only; do not add filtering to `crawler_ycombinator`, `enrichment_ats_enrichment`, `crawler_4dayweek`, or `crawler_levelsfyi`.
- Do NOT store `identity_id`, `role_matched`, or other role-tracking fields on job documents.
- Role filtering happens per-crawl, per-identity execution; filtering state is not persisted downstream.
- Modify role filtering logic ONLY in `crawler_ats_job_extraction` before job upsert calls.
- If filtering rules must change (for example substring vs. word-boundary matching), update this spec section 8.2 first, then update code and tests together.

**Workflow guardrails**:
- Preserve `run_id` as the parent public crawl identifier.
- Use `workflow_run_id` for per-workflow retries and singular executions.
- Use `workflow_id` exactly as documented in section 2 and section 11.
- Emit company-discovery events only for newly inserted or newly actionable companies; do not emit them for non-actionable metadata churn.

Do not change these names without coordinated cross-service updates:
- `run_id`
- `identity_id`
- `job_id`
- `title`
- `description`
- `location`
- `platform`
- `external_job_id`
- `source_url`
- `company`
- `scoring_status`
- `weighted_score`

---

## 15. Source of Truth Hierarchy

Use these references in this order when working on crawler contracts:
1. this file for crawler-local behavior and source extraction rules;
2. `../../go/cmd/api/SPEC.md` for API-side model and queue contracts;
3. `../ai_scorer/SPEC.md` for downstream scoring worker expectations;
4. `../../../spec.md` for broader product intent only.

If references disagree on a shared contract, resolve explicitly in code and docs rather than assuming intent.
