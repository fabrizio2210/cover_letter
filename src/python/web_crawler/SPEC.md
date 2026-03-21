# Web Crawler Specification

**This file is the authoritative reference for the Python web crawler service.**
Agents editing files in this folder MUST consult this file before making changes.

It exists to prevent contract drift between crawl adapters, MongoDB job documents, company resolution behavior, and downstream scoring queue integration.

> Shared references: `web_crawler.py` (or equivalent entry script), `../../go/cmd/api/SPEC.md`, `../ai_querier/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `web_crawler` service discovers company targets and job opportunities from aggregators, curated communities, and ATS providers, normalizes postings to the shared internal job shape, and persists them into MongoDB.

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
| Execution style | On-demand run or scheduled batch run |
| Data store | MongoDB |
| Queue integration | Optional Redis producer for job scoring |
| Source types | ATS APIs (Greenhouse, Lever, Ashby), aggregator sources (LinkedIn, Indeed, SimplyHired), curated startup communities (Y Combinator, Wellfound), and aggregator HTML pages (4dayweek.io) |

The crawler runs as a bounded batch process and may process one or multiple source configurations per run.

High-level flow:
1. Load crawler configuration, enabled sources, explicit `identity_id`, and discovery input role.
2. Discover companies for the role from aggregators and curated startup communities.
3. Validate ATS compatibility from company careers pages and resolve ATS slugs.
4. Fetch raw job listings from source endpoint(s).
5. Normalize each listing into shared job fields.
6. Resolve or create company documents.
7. Upsert `job-descriptions` documents idempotently.
8. Optionally enqueue `{ "job_id": "..." }` messages for scoring.
9. Emit crawl summary logs and counters.

---

## 3. Environment Variables

| Variable | Default | Required | Used for |
|---|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | Yes | MongoDB connection URI |
| `DB_NAME` | `cover_letter` | No | MongoDB database name |
| `REDIS_HOST` | `localhost` | No | Redis host for scoring queue output |
| `REDIS_PORT` | `6379` | No | Redis port for scoring queue output |
| `JOB_SCORING_QUEUE_NAME` | `job_scoring_queue` | No | Redis queue name for scoring payloads |
| `CRAWLER_ENABLE_SCORING_ENQUEUE` | `0` | No | If `1`, enqueue job ids after successful persistence |
| `CRAWLER_HTTP_TIMEOUT_SECONDS` | `20` | No | HTTP timeout per request |
| `CRAWLER_MAX_RETRIES` | `3` | No | Retry limit for transient failures |
| `CRAWLER_BASE_DELAY_MS` | `1500` | No | Baseline delay between requests |
| `CRAWLER_MAX_DELAY_MS` | `15000` | No | Max backoff delay |
| `CRAWLER_USER_AGENT` | browser-like UA string | No | Request header to reduce bot blocking |
| `CRAWLER_REFERER` | `https://4dayweek.io/jobs` | No | Referer for 4dayweek requests |

Platform-specific configuration may include source names, ATS slugs, and source URLs (via config file or environment).

Rules:
- `DB_NAME` must match the database used by the Go API.
- If `CRAWLER_ENABLE_SCORING_ENQUEUE=1`, Redis connectivity is required for queue handoff.
- Missing platform credentials are not fatal when the platform can be scraped from public endpoints.

---

## 4. Responsibilities

The crawler is responsible for:
- taking an explicit `identity_id` plus role-focused discovery input and using it to discover actively hiring companies;
- discovering company slugs for ATS-hosted boards when not preconfigured;
- validating ATS compatibility from company careers pages before slug resolution;
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
- requiring external enrichment APIs for company-to-domain resolution;
- cover-letter writing or refinement;
- auth, JWT, or API route validation.

---

## 5. Source Discovery Contracts

### 5.1 Discovery Input: Role and Identity Mapping

The crawler discovery input must include:
- `identity_id` (required, hex MongoDB ObjectId string);
- one or more role query keywords (for example, `Software Engineer`).

Runs missing `identity_id` are invalid and must fail fast before discovery starts.

Terminology mapping with product spec:
- The user-facing `ROLE` maps to identity profile context in `../../../spec.md`.
- The crawler may derive additional role query variants from identity description text and role-relevant identity preferences.
- Identity preferences are used only to shape discovery and filtering scope; weighted score computation remains outside crawler ownership.

Query construction rules:
- Build one or more role queries from provided input role terms and optional identity context.
- Keep query terms broad enough for discovery, then narrow results with ATS validation and dedup.
- Do not mutate scoring contracts, queue payloads, or persistence keys when applying role filters.

### 5.2 Aggregator Reverse Search (Company-First)

Primary company-name discovery sources for v1:
- LinkedIn
- Indeed
- SimplyHired

Objective:
- Treat aggregator results as a source of truth for currently hiring companies for the requested role.

Discovery logic:
1. Execute role query on each aggregator source.
2. Extract at minimum: company name and company website domain (when present).
3. Normalize and deduplicate companies before ATS checks (for example, one company listed across many postings is kept once).
4. Preserve source attribution metadata for troubleshooting and recrawl decisions.

Compliance and safety:
- Access patterns must respect legal and operational constraints of each source.
- Use bounded concurrency and anti-bot controls defined in section 11.

### 5.3 Niche Community Crawling

Use curated startup communities as a complementary high-signal source for ATS-backed companies.

Priority niche sources:
- Y Combinator company directory (`https://www.ycombinator.com/companies`)
- Wellfound

Expected behavior:
- Extract company names and any discoverable domain/careers links.
- Capture redirects or metadata that may reveal ATS provider hints.
- Merge and deduplicate against aggregator-derived companies before ATS validation.

### 5.4 ATS Compatibility Validation

Before slug dorking, validate whether a company appears compatible with Greenhouse, Lever, or Ashby.

Validation target:
- Company careers page (for example `/careers`, `/jobs`, or equivalent path from discovered links).

Signature indicators to detect:

| ATS | Indicators in careers HTML or links |
|---|---|
| Greenhouse | `grnh.io`, `boards.greenhouse.io` |
| Lever | `jobs.lever.co`, `.lever-job` markers |
| Ashby | `window.ashby` hints, `ashbyhq.com` links |

Rules:
- If ATS signatures are absent, keep company in discovery telemetry, log the reason, and skip extraction for that company in that run.
- If multiple ATS hints appear, prioritize the strongest hosted-board signal and log ambiguity.
- If careers content is client-rendered, optional headless rendering may be used under section 11 controls.

### 5.5 ATS Slug Discovery

To call ATS public APIs, the crawler needs the provider-specific company slug.

Primary discovery method (search dorking):
- Greenhouse query pattern: `site:boards.greenhouse.io "<Company Name>"`
- Lever query pattern: `site:jobs.lever.co "<Company Name>"`
- Ashby query pattern: `site:jobs.ashbyhq.com "<Company Name>"`

Expected extraction:
- Parse result URLs.
- Extract the first path segment after host as slug.
- Validate slug by requesting the provider API endpoint.

Secondary method (direct hosted-board extraction):
- If ATS compatibility validation already exposed a hosted-board URL, derive slug directly from that URL.
- Validate slug by requesting the provider API endpoint.

If slug cannot be resolved:
- Log unresolved source with reason.
- Skip source for this run.

### 5.6 4dayweek Job URL Discovery

Preferred strategy: sitemap traversal.

Discovery steps:
1. Fetch `https://4dayweek.io/sitemap.xml`.
2. Resolve nested sitemaps if present.
3. Extract URLs matching job pattern:
   - `https://4dayweek.io/remote-job/{job-title-slug}-{id}`
4. Deduplicate URLs before extraction.

Fallback strategy: list-page crawl.
- Start from `https://4dayweek.io/jobs`.
- Use headless browser only if required by client-side pagination/infinite scroll.
- Capture job detail URLs from loaded content or intercepted XHR payloads.

### 5.7 End-to-End Discovery Workflow

Engineer-oriented sequence:
1. Input role keyword from identity profile context.
2. Discover company names/domains from LinkedIn, Indeed, SimplyHired, and niche startup communities.
3. Validate ATS compatibility on careers pages using signature checks.
4. Resolve ATS slug via hosted-board extraction or dorking.
5. Fetch postings from ATS APIs (and 4dayweek flow where configured), normalize, upsert, and optionally enqueue scoring payloads.

---

## 6. Extraction Contracts by Platform

### 6.1 Greenhouse

Endpoint:
- `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`

Requirements:
- `content=true` is mandatory to retrieve full role content.
- Extract at least title, location, content/body, external id, and canonical URL.

### 6.2 Lever

Endpoint:
- `https://api.lever.co/v0/postings/{slug}`

Requirements:
- Parse modular sections (requirements, responsibilities, and related structures).
- Preserve meaningful formatting in `description` while normalizing to one text field.

### 6.3 Ashby

Endpoint:
- `https://api.ashbyhq.com/posting-api/job-board/{slug}`

Requirements:
- Extract title, location, description/body, compensation fields when present, apply/source URL, and stable job id.
- Preserve structured optional fields as non-contractual metadata if stored.

### 6.4 4dayweek.io

Extraction order:
1. Prefer JSON-LD in `<script type="application/ld+json">` with `JobPosting` schema.
2. Fallback to DOM parsing when JSON-LD is absent/incomplete.

Minimum extraction targets:
- Title
- Company name
- Description/body
- Location/remote signal
- Salary or compensation when present
- Canonical job URL

`external_job_id` strategy for 4dayweek:
- Primary: parse the terminal numeric id from job URL pattern `...-{id}`.
- Fallback: if numeric id cannot be parsed, use a stable hash of the canonical `source_url` path.
- This key must remain stable across recrawls for dedup.

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
- `platform` must be one of: `greenhouse`, `lever`, `ashby`, `4dayweek`.
- `external_job_id` must be stable across recrawls for same source job.
- `source_url` should be the canonical job page URL.
- `description` may be converted from HTML to text, but content loss should be minimized.

Optional non-contract metadata may be stored in source-specific fields, but must not replace required keys above.

---

## 8. Idempotency and Deduplication

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

---

## 9. MongoDB Contract

### 9.1 Collections Used

| Collection | Access | Purpose |
|---|---|---|
| `job-descriptions` | read/insert/update | Store normalized job records |
| `companies` | read/insert | Resolve or create company documents |
| `crawls` | optional insert/update | Store crawl run summaries/telemetry if implemented |

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
| `scoring_status` | string | One of `unscored`, `queued`, `scored`, `failed`, `skipped` |
| `weighted_score` | number | Must initialize to `0` |

Reference compatibility note:
- New crawler writes for references must use MongoDB `ObjectId`.
- Legacy string references may still exist and must be tolerated during reads.

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

---

## 10. Redis Scoring Queue Handoff Contract

Queue handoff is configurable and disabled by default.

When `CRAWLER_ENABLE_SCORING_ENQUEUE=1`:
- Queue name: env `JOB_SCORING_QUEUE_NAME` (default `job_scoring_queue`).
- Payload shape per job:

```json
{
  "job_id": "<job description hex object id>"
}
```

Rules:
- Enqueue only after successful insert or update with a valid document id.
- On job updates from recrawls, always re-enqueue when enqueue is enabled.
- Enqueue payload must use key name `job_id` exactly.
- If enqueue fails, persistence remains committed, `scoring_status` should be set to `failed`, error is logged, and crawler continues.

Scoring lifecycle contract:
- Allowed `scoring_status` values: `unscored`, `queued`, `scored`, `failed`, `skipped`.
- Insert/update with enqueue enabled and enqueue success: set `scoring_status` to `queued`.
- Insert/update with enqueue disabled: set `scoring_status` to `unscored`.
- Enqueue failure: set `scoring_status` to `failed`.
- Missing scoring prerequisites (for example unresolved company-field-identity linkage): set `scoring_status` to `skipped` and do not enqueue.
- Successful scoring write by downstream worker: set `scoring_status` to `scored`.

Ownership boundary:
- Crawler produces job ids for scoring.
- `ai_querier` consumes queue and writes per-preference scores.
- Deterministic aggregate ranking remains outside crawler responsibility.

---

## 11. Rate Limiting, Retry, and Anti-Bot Strategy

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

---

## 12. Failure Handling

Recoverable failures (log and continue):
- Invalid source configuration for one source.
- Slug resolution failure for one company.
- Parsing failure for one posting.
- Duplicate-key race conditions on upsert.
- Missing scoring prerequisites that require `scoring_status=skipped`.
- Redis enqueue failure when scoring enqueue is enabled.

Run-level failures (abort run):
- MongoDB connection unavailable at startup.
- No enabled sources configured.

Each run should emit summary counters:
- sources processed;
- jobs discovered;
- jobs inserted;
- jobs updated;
- jobs skipped;
- enqueue success/fail counts;
- run duration.

---

## 13. Editing Guardrails for Agents

Before changing crawler code in this folder:
1. Read this file.
2. Check `../../go/cmd/api/SPEC.md` for canonical job fields and queue payload contract.
3. Check `../ai_querier/SPEC.md` for scoring consumer expectations.
4. Preserve exact field names in MongoDB documents and queue messages.
5. Update this spec and related service specs together when shared contracts change.

Do not change these names without coordinated cross-service updates:
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

## 14. Source of Truth Hierarchy

Use these references in this order when working on crawler contracts:
1. this file for crawler-local behavior and source extraction rules;
2. `../../go/cmd/api/SPEC.md` for API-side model and queue contracts;
3. `../ai_querier/SPEC.md` for downstream scoring worker expectations;
4. `../../../spec.md` for broader product intent only.

If references disagree on a shared contract, resolve explicitly in code and docs rather than assuming intent.
