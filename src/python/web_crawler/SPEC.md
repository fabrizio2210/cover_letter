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
2. enrichment workflows, which consume newly actionable company discoveries and emit ATS-ready follow-up triggers.

Stable workflow identifiers:
- `crawler_company_discovery`
- `enrichment_ats_enrichment`
- `crawler_ats_job_extraction`
- `crawler_4dayweek`

High-level orchestration:
1. Load crawler configuration, enabled sources, explicit `identity_id`, and the selected identity's `roles` list.
2. Accept one public crawl request keyed by parent `run_id` and `identity_id`.
3. Fan out all UI-triggered crawler workflows in parallel under the same parent `run_id`, each with its own `workflow_run_id`.
4. Persist workflow outputs immediately to MongoDB so downstream workflows can consume partial progress without waiting for parent-run completion.
5. Emit company-discovery events only when a company is newly inserted or when an existing company becomes newly actionable for ATS enrichment.
6. Consume those company-discovery events in `enrichment_ats_enrichment` and emit ATS-job-trigger events when `ats_provider` plus `ats_slug` become available.
7. Execute ATS-backed crawler workflows, such as `crawler_ats_job_extraction`, from those follow-up triggers, either as part of the same parent run or as singular workflow message executions.
8. Publish workflow-level progress snapshots to Redis, each carrying parent `run_id` plus `workflow_run_id` and `workflow_id`.
9. Optionally enqueue `{ "job_id": "..." }` messages for scoring after each successful job insert/update.
10. Emit crawl summary logs and counters, including per-workflow success/failure counts and parent-run completion state.

---

## 3. Environment Variables

| Variable | Default | Required | Used for |
|---|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | Yes | MongoDB connection URI |
| `DB_NAME` | `cover_letter` | No | MongoDB database name |
| `REDIS_HOST` | `localhost` | No | Redis host for scoring queue output |
| `REDIS_PORT` | `6379` | No | Redis port for scoring queue output |
| `CRAWLER_TRIGGER_QUEUE_NAME` | `crawler_trigger_queue` | No | Redis queue name for crawl requests consumed by the worker |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | No | Redis channel used to publish crawl progress snapshots |
| `CRAWLER_COMPANY_DISCOVERY_QUEUE_NAME` | `crawler_company_discovery_queue` | No | Redis queue consumed by the `crawler_company_discovery` worker |
| `CRAWLER_ATS_JOB_EXTRACTION_QUEUE_NAME` | `crawler_ats_job_extraction_queue` | No | Redis queue consumed by the `crawler_ats_job_extraction` worker |
| `JOB_SCORING_QUEUE_NAME` | `job_scoring_queue` | No | Redis queue name for scoring payloads |
| `CRAWLER_ENABLE_SCORING_ENQUEUE` | `0` | No | If `1`, enqueue job ids after successful persistence |
| `CRAWLER_HTTP_TIMEOUT_SECONDS` | `20` | No | HTTP timeout per request |
| `CRAWLER_MAX_RETRIES` | `3` | No | Retry limit for transient failures |
| `CRAWLER_BASE_DELAY_MS` | `1500` | No | Baseline delay between requests |
| `CRAWLER_MAX_DELAY_MS` | `15000` | No | Max backoff delay |
| `CRAWLER_USER_AGENT` | browser-like UA string | No | Request header to reduce bot blocking |
| `CRAWLER_REFERER` | `https://4dayweek.io/jobs` | No | Referer for 4dayweek requests |
| `CRAWLER_LEVELSFYI_MAX_COMPANIES_PER_ROLE` | `50` | No | Cap on company discoveries retained per identity role from Levels.fyi |

Platform-specific configuration may include source names, ATS slugs, and source URLs (via config file or environment).

Rules:
- `DB_NAME` must match the database used by the Go API.
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
- `identity_id` (required, hex MongoDB ObjectId string).
- `run_id` (required in Redis-driven worker mode; server-generated unique crawl run identifier).

The selected identity must include:
- `roles` (required for role-first discovery): user-maintained list of role keywords, for example `software engineer`, `platform engineer`.

Runs missing `identity_id` are invalid and must fail fast before discovery starts.

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

### 5.2 Role-Query Job Boards and Search Sources

Use role-query job boards and search-oriented discovery sources as the primary `crawler_company_discovery` input for currently hiring companies.

Preferred sources:

| Website | Root URL | Path / Pattern to Lookup | Discovery output |
|---|---|---|---|
| LinkedIn | `linkedin.com` | role/job search result pages | Role-filtered hiring signals, company names, and job-source attribution |
| Indeed | `indeed.com` | role/job search result pages | Role-filtered hiring signals, company names, and company/job URLs when present |
| SimplyHired | `simplyhired.com` | role/job search result pages | Role-filtered hiring signals, company names, and company/job URLs when present |
| Built In | `builtin.com` | city or hub company pages such as `/sf`, `/nyc`, and related company listings | Company-first discovery within tech hubs, with role filters applied when available |
| Levels.fyi | `levels.fyi` | `/jobs?searchText={role query}`, `/t/{role-slug}`, role markdown routes such as `/t/{role-slug}.md` | Role-seeded company discovery with Levels company URLs and optional job/company attribution when public routes expose it |
| Otta | `otta.com` | `/companies` | Curated tech-company discovery with job and company metadata when publicly available |

Objective:
- Treat role-query sources as high-signal evidence that companies are actively hiring for one or more requested identity roles.

Discovery logic:
1. Execute one or more role queries derived from `identity.roles` on each enabled role-query source.
2. Extract at minimum: company name, source URL, and company website domain or careers URL when present.
3. Preserve role association and source attribution metadata so repeated discoveries can be traced to the originating board or search result.
4. Normalize and deduplicate companies before ATS checks (for example, one company listed across many postings is kept once).

Levels.fyi public-route strategy:
- Preferred routes are the search page `/jobs?searchText={role query}` and the role taxonomy page `/t/{role-slug}`.
- When structured markdown is available, `/t/{role-slug}.md` is preferred as a stable fallback for company extraction.
- Extraction targets are company names and Levels company URLs such as `/companies/{company-slug}/salaries`; direct careers URLs may be absent and are not required for persistence.
- If public role pages expose only company-level links and no job-detail or ATS links, persist the deduplicable company discovery anyway so `enrichment_ats_enrichment` can attempt later enrichment.

Compliance and safety:
- Access patterns must respect legal and operational constraints of each source.
- Use bounded concurrency and anti-bot controls defined in section 11.
- For Levels.fyi, respect `robots.txt` and `llms.txt`, prefer documented sitemap/markdown-friendly routes when available, and preserve attribution if data from those routes is surfaced downstream.
- Sources in this group are preferred inputs, but some may be skipped for a run if public accessibility, response format, or compliance posture makes extraction impractical.

### 5.2.1 Levels.fyi Public Discovery Contract

Levels.fyi is integrated as a `crawler_company_discovery` source using public routes only.

Supported discovery inputs:
- Role slug derived from `identity.roles` using lowercase hyphenated normalization.

Preferred retrieval order:
1. Fetch `/jobs?searchText={role query}` and extract company links when available.
2. Fetch `/t/{role-slug}` and extract company links such as `/companies/{company-slug}/salaries`.
3. Fallback to `/t/{role-slug}.md` and parse markdown links to company salary pages when HTML extraction yields insufficient results.

Output contract:
- Persist `source = "levelsfyi"` in source attribution metadata.
- Persist the matched identity role string in `discovery_sources.role`.
- Persist the discovered Levels company URL in `discovery_sources.source_url`.
- Do not require `careers_url` or company website domain at discovery time.

Non-goals for this integration:
- No dependency on the official paid Levels API.
- No direct `crawler_ats_job_extraction` ingestion from Levels job pages in this phase.
- No schema or queue contract changes.

### 5.3 Curated Startup and Job Communities

Use curated startup and job communities as complementary high-signal sources for ATS-backed companies and startup-focused hiring activity.

Preferred sources:

| Website | Root URL | Path / Pattern to Lookup | Discovery output |
|---|---|---|---|
| Y Combinator | `ycombinator.com` | `/companies` | Company directory plus discoverable jobs/careers links when exposed |
| Wellfound | `wellfound.com` | `/jobs` or `/company/:slug` | Startup job-board results plus company pages with links, hiring signals, and metadata when available |
| Work at a Startup | `workatastartup.com` | `/companies` | YC-aligned company and hiring discovery with direct startup job signals |

Expected behavior:
- Extract company names and any discoverable domain, job-board, or careers links.
- Capture redirects, company slugs, and metadata that may reveal ATS provider hints.
- Merge and deduplicate against role-query-source discoveries before ATS validation.

Notes:
- Sources in this group may expose either company-first discovery, job-first discovery, or both.
- `crawler_company_discovery` should tolerate missing role filters on some community directories by treating them as supporting sources rather than the only source of truth for role relevance.

### 5.4 Portfolio and Investor Directories

Use public portfolio and investor directories as company-first discovery inputs for startup ecosystems where ATS-backed hiring is common, even when direct job signals are weaker than dedicated job boards.

Preferred sources:

| Website | Root URL | Path / Pattern to Lookup | Data type |
|---|---|---|---|
| Crunchbase | `crunchbase.com` | `/organization/:permalink` | Firmographics |
| Techstars | `techstars.com` | `/portfolio` | Portfolio list |
| 500 Global | `500.co` | `/startups/` | Portfolio list |
| a16z | `a16z.com` | `/investment-list/` | Portfolio list |
| Sequoia | `sequoiacap.com` | `/our-companies/` | Portfolio list |

Expected behavior:
- Extract at minimum the company name and any public website, domain, careers link, or company permalink that can support later ATS validation.
- Treat these sources as company-first enrichment sources rather than direct proof of a matching open role.
- Use identity roles to prioritize or filter follow-up ATS validation and downstream extraction, but do not require every portfolio source to natively support role search.

Notes:
- Portfolio directories are preferred `crawler_company_discovery` inputs when they expose enough public metadata to produce deduplicable company records.
- If a portfolio source yields only firmographic metadata without usable company links, the crawler may record telemetry and skip the company for ATS validation in that run.

### 5.5 Community Hiring Threads

Use community hiring threads as opportunistic discovery sources for companies that may not be surfaced reliably by structured boards.

Preferred sources:

| Website | Root URL | Path / Pattern to Lookup | Note |
|---|---|---|---|
| Hacker News | `hn.algolia.com` | `/?query=Who%27s%20hiring&sort=byDate` | Search monthly `Who is Hiring` threads and extract company/hiring signals from thread content |

Expected behavior:
- Discover the current and recent `Who is Hiring` threads, then extract company names, careers URLs, domains, and role relevance from structured or semi-structured postings when feasible.
- Treat thread-derived discoveries as lower-structure signals that must still pass normalization, deduplication, and ATS validation before downstream extraction.

Notes:
- Community-thread extraction is preferred but optional because post formatting and attribution quality may vary across runs.
- When company identity cannot be resolved confidently from thread content, the crawler should preserve telemetry and skip ATS follow-up for that item.

### 5.6 ATS Compatibility Validation

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

### 5.7 ATS Slug Discovery

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

### 5.8 4dayweek Job URL Discovery

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

### 5.9 End-to-End Discovery Workflow

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

### 5.10 Modular Workflow Contracts

The crawler is organized as independently triggerable workflows that can participate in one parent run or execute singularly by message.

#### Workflow Kind A: Crawler Workflows

Crawler workflows discover jobs, companies, or both. They may be UI-triggered, event-triggered, or both.

Stable crawler workflow identifiers:
- `crawler_company_discovery`
- `crawler_ats_job_extraction`
- `crawler_4dayweek`

Common output rules for crawler workflows:
- Job outputs are normalized and upserted into `jobs`.
- Successful job insert/update may enqueue `{ "job_id": "..." }` for scoring.
- Company outputs are resolved and upserted into `companies`.
- Company-discovery events are emitted only when a company is newly inserted or when an existing company is updated such that its metadata becomes newly actionable for ATS enrichment.
- A company is newly actionable for ATS enrichment when persisted metadata becomes sufficient for the enrichment workflow to attempt ATS validation or slug resolution, for example a first usable domain, careers URL, or hosted ATS URL.

##### `crawler_company_discovery`

Input:
- parent `run_id`
- `workflow_run_id`
- `identity_id`
- `identities.roles`

Sources:
- Role-query job boards and search sources: LinkedIn, Indeed, SimplyHired, Built In, Levels.fyi, Otta
- Curated startup and job communities: Y Combinator, Wellfound, Work at a Startup
- Portfolio and investor directories: Crunchbase, Techstars, 500 Global, a16z, Sequoia
- Community hiring threads: Hacker News `Who is Hiring`

Source-specific output expectations:
- Job-board and search sources should provide role-filtered hiring signals plus company names and source attribution.
- Some role-query sources such as Levels.fyi may expose company-first public links without direct careers URLs; those discoveries remain valid output and may still be skipped later by enrichment if ATS prerequisites cannot be resolved.
- Curated startup and job communities may provide company-first discovery, job-first discovery, or both.
- Portfolio directories may only provide company metadata and public links sufficient for company creation and later ATS validation.
- Community-thread sources may require extracting company identity from unstructured post text and should be treated as lower-structure inputs.

Source policy:
- These sources are preferred inputs, not a guarantee that identical adapters or extraction quality exist for every source.
- A source may be enabled, skipped, or partially processed in a run depending on public accessibility, compliance posture, and whether it exposes enough company metadata for downstream enrichment.

DB writes:
- Upsert into `companies` using canonicalized company name.
- Preserve source attribution metadata when available.

##### `crawler_ats_job_extraction`

Input:
- parent `run_id` when part of a larger crawl, or no parent `run_id` for singular message execution
- `workflow_run_id`
- `identity_id` for role filtering
- ATS-job-trigger event or direct singular workflow trigger containing enough company identity plus ATS metadata to execute extraction

Processing:
- Load identity and extract `roles` list from identity document.
- Call provider-specific ATS endpoints.
- Normalize postings into the shared job schema.
- **Validate each extracted job against identity roles before insertion** (see section 8.2 for role filtering rules).
- Skip jobs that do not match any identity role.

DB writes:
- Upsert into `job-descriptions` using (`platform`, `external_job_id`) only for jobs that pass role filtering.
- Update mutable fields and `updated_at` on recrawl.
- Optionally enqueue scoring payload after successful write.

##### `crawler_4dayweek`

Input:
- parent `run_id`
- `workflow_run_id`
- identity-scoped public crawl request fan-out

Processing:
- Discover job URLs from 4dayweek sitemap or list pages.
- Extract job and company details from JSON-LD or DOM fallback.

DB writes:
- Resolve/create company in `companies`.
- Upsert job into `job-descriptions` with `platform=4dayweek`.

#### Workflow Kind B: Enrichment Workflows

Enrichment workflows consume company-discovery events, add ATS metadata, and emit follow-up job extraction triggers.

Stable enrichment workflow identifier:
- `enrichment_ats_enrichment`

##### `enrichment_ats_enrichment`

Input:
- parent `run_id` when part of a larger crawl, or no parent `run_id` for singular message execution
- `workflow_run_id`
- company-discovery event emitted by a crawler workflow

Processing:
- Detect ATS compatibility from careers links/pages.
- Resolve slug from hosted-board URLs or search dorking.
- Validate provider/slug via provider API endpoint.

DB writes:
- Update company document with:
  - `ats_provider` (`greenhouse`, `lever`, `ashby`)
  - `ats_slug` (provider-specific slug)

Output contract:
- When both `ats_provider` and `ats_slug` become available, emit an ATS-job-trigger event for `crawler_ats_job_extraction`.

### 5.11 Workflow Triggering, Dependency, and Persistence Policy

Triggering rules:
- A public crawl request remains identity-scoped and starts the default UI-triggered workflow set under one parent `run_id`.
- Singular workflow execution is supported by internal messages addressed to one `workflow_id`.
- Every workflow execution attempt must have a unique `workflow_run_id`.
- A retry of the same workflow under the same parent `run_id` must get a new `workflow_run_id` while retaining the same `workflow_id`.

Dependency rules:
- `crawler_company_discovery` and `crawler_4dayweek` are UI-triggered crawler workflows and can start in parallel.
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

Before inserting extracted jobs into the database, each job must be validated against the identity's roles to ensure relevance.

**Filtering scope**:
- Applied in `crawler_ats_job_extraction`.
- Workflows 1, 2, and 4 are not affected by role filtering.

**Filtering mechanism**:
- Load the identity document using `identity_id` and extract the `roles` array (for example `["software engineer", "platform engineer"]`).
- For each extracted job, check if the job's `title` or `description` contains any role keyword from `identity.roles`.
- Matching is case-insensitive substring matching.
- Job is accepted if ANY role keyword appears in title or description (OR logic).
- Empty `roles` list accepts all jobs (treat as "discover broadly").

**Filtering behavior**:
- Jobs matching at least one role: proceed to deduplication and insertion.
- Jobs not matching any role: skip insertion, log skipped reason, increment skip counter.
- No tombstone or skip marker is created for non-matching jobs.

**Example**:
- Identity roles: `["software engineer", "platform engineer"]`
- Job 1 title: "Senior Software Engineer" → matches "software engineer" → accepted
- Job 2 title: "Data Scientist" → does not match any role → skipped
- Job 3 description: "...responsible for platform engineering tasks..." → matches "platform engineer" → accepted

**Role matching rules**:
- Matching is case-insensitive ("Software Engineer", "software engineer", "SOFTWARE ENGINEER" all match).
- Substring matching is used ("engineer" as a role matches "engineering role" in description).
- Both `title` and `description` fields are checked independently; job is accepted if either field contains any role keyword.

---

## 9. MongoDB Contract

### 9.1 Collections Used

| Collection | Access | Purpose |
|---|---|---|
| `job-descriptions` | read/insert/update | Store normalized job records |
| `companies` | read/insert/update | Resolve, create, and ATS-enrich company documents |
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
- `ai_scorer` consumes queue and writes per-preference scores.
- Deterministic aggregate ranking remains outside crawler responsibility.

---

## 11. Redis Crawl Trigger And Progress Contract

### 11.1 Crawl Request Consumption

In worker mode, the crawler listens on `CRAWLER_TRIGGER_QUEUE_NAME` using blocking Redis queue consumption.

Expected payload:

```json
{
  "run_id": "<crawl run id>",
  "identity_id": "<identity hex object id>",
  "requested_at": { "seconds": 1711234567, "nanos": 0 }
}
```

Rules:
- Missing `run_id` or `identity_id` is a malformed request and must be rejected.
- The worker must emit an initial parent-run `queued` snapshot or immediate workflow `running` snapshots for accepted work.
- Only one active crawl may exist for a given `identity_id`.
- If another crawl is already active for the same `identity_id`, the worker must not start a second run. It must instead publish a terminal progress snapshot with `status = "rejected"` and `reason = "already_running"`.
- The public crawl request does not need to name a specific workflow. Workflow fan-out happens inside the crawler orchestration layer.

Internal workflow-trigger messages are crawler-owned contracts and are not part of the public API payload.

Implementation note:
- Internal workflow messages should use `common.proto` generated types for exchange (`WorkflowDispatchMessage`, `CompanyDiscoveryEvent`, `AtsJobTriggerEvent`) instead of ad-hoc dictionary payloads.
- Redis payload transport may serialize those proto messages as JSON at rest, but producer/consumer code must construct and parse generated protobuf classes at boundaries.

Required internal routing fields:
- `workflow_id` — stable module key, one of `crawler_company_discovery`, `enrichment_ats_enrichment`, `crawler_ats_job_extraction`, `crawler_4dayweek`
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
  "workflow_id": "crawler_company_discovery",
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
  "workflow_id": "crawler_company_discovery",
  "identity_id": "<identity hex object id>",
  "status": "running",
  "workflow": "crawler_company_discovery",
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
- `workflow_id` must be one of `crawler_company_discovery`, `enrichment_ats_enrichment`, `crawler_ats_job_extraction`, `crawler_4dayweek` when the event represents a workflow contribution.
- `workflow_run_id` must uniquely identify one workflow execution attempt.
- `workflow` should equal `workflow_id` for workflow-level events and may be `queued` or `finalizing` for parent-run lifecycle events.
- `finished_at` is populated only for terminal events.
- Terminal `failed` events should include a short machine-readable `reason` and a human-readable `message`.
- Clients must treat the most recent event per `workflow_run_id` as the authoritative live snapshot for that workflow contribution.
- Multiple active workflow snapshots may coexist under one parent `run_id` and one `identity_id`.

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
- Role filtering (section 8.2) is a validation gate in `crawler_ats_job_extraction` only; do not add filtering to `crawler_company_discovery`, `enrichment_ats_enrichment`, or `crawler_4dayweek`.
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
