# crawler_company_discovery — Specification

**Authoritative reference for the `crawler_company_discovery` package.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [`../SPEC.md`](../SPEC.md)
> Shared references: `../../go/cmd/api/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `crawler_company_discovery` package discovers employer companies from role-query sources (Y Combinator, Hacker News), deduplicates and upserts them into MongoDB, then emits `CompanyDiscoveryEvent` messages for every company that still needs ATS enrichment.

This package does **not** detect ATS providers, fetch jobs, or produce scoring queue entries.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Workflow module | `src.python.web_crawler.crawler_company_discovery.workflow` |
| Worker module | `src.python.web_crawler.crawler_company_discovery.worker` |
| Docker CMD | `python -m src.python.web_crawler.crawler_company_discovery.worker --worker` |
| Input queue | `CRAWLER_COMPANY_DISCOVERY_QUEUE_NAME` (default `crawler_company_discovery_queue`) |
| Output queue | `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME` — one `CompanyDiscoveryEvent` per company needing enrichment |
| Progress channel | `CRAWLER_PROGRESS_CHANNEL_NAME` |
| Workflow ID | `crawler_company_discovery` |

---

## 3. Environment Variables

Inherited from `CrawlerConfig`. Relevant subset:

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | MongoDB URI |
| `DB_NAME` | `cover_letter` | Database name |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `CRAWLER_COMPANY_DISCOVERY_QUEUE_NAME` | `crawler_company_discovery_queue` | Input queue |
| `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME` | `crawler_enrichment_ats_enrichment_queue` | Enrichment event output |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | Progress channel |
| `CRAWLER_ENABLED_SOURCES` | — | Comma-separated source names; defaults to `ycombinator` only |

---

## 4. Responsibilities

- Parse `WorkflowDispatchMessage` from the input queue; drop malformed messages.
- Load the identity document from MongoDB `identities` and extract `roles`; raise if identity is missing or has no roles.
- Run all enabled source adapters against the identity's role list.
- Deduplicate discovered companies by canonical name before upserting.
- Upsert companies into the `companies` collection with `discovery_sources`, `canonical_name`, and optional `field_id`.
- Determine which upserted companies have no `ats_slug` yet — these are pending enrichment.
- Push one `CompanyDiscoveryEvent(reason="no_ats_slug")` per pending company to the enrichment queue.
- Publish `running` → `completed` (or `failed`) progress snapshots on the progress channel.
- Reconnect to Redis automatically on connection loss.

---

## 5. Identity and Role Contract

- Identity is loaded from `database["identities"]` by `ObjectId(identity_id)`.
- `roles` must be a non-empty list of non-empty strings; missing or empty roles raises `ValueError` and fails the workflow run.
- `field_id` is optional; forwarded to `upsert_companies` when present.

---

## 6. Source Adapter Contract

Enabled adapters are selected via `get_enabled_adapters(config.enabled_sources)`:
- Default (no config): `YCombinatorAdapter` only.
- Supported names: `"ycombinator"`, `"hackernews"`.
- Unknown names are silently skipped.
- Adapter failures are caught per-adapter; the run continues with remaining adapters and records the failure in `result.failed_sources`.

---

## 7. Supported Source Families

The workflow draws from four classes of sources. A source may be enabled, skipped, or partially processed depending on public accessibility, compliance posture, and whether it exposes enough company metadata for downstream enrichment.

### 7.1 Role-Query Job Boards and Search Sources

Primary input for companies actively hiring for the identity's roles.

| Website | Root URL | Path / Pattern | Discovery output |
|---|---|---|---|
| LinkedIn | `linkedin.com` | role/job search result pages | Role-filtered hiring signals, company names, job-source attribution |
| Indeed | `indeed.com` | role/job search result pages | Role-filtered hiring signals, company names, company/job URLs when present |
| SimplyHired | `simplyhired.com` | role/job search result pages | Role-filtered hiring signals, company names, company/job URLs when present |
| Built In | `builtin.com` | city/hub company pages (`/sf`, `/nyc`, …) | Company-first discovery within tech hubs, role filters applied when available |
| Levels.fyi | `levels.fyi` | `/jobs?searchText={role}`, `/t/{role-slug}`, `/t/{role-slug}.md` | Role-seeded company discovery with Levels company URLs |
| Otta | `otta.com` | `/companies` | Curated tech-company discovery |

Discovery logic:
1. Execute one or more role queries derived from `identity.roles` on each enabled source.
2. Extract at minimum: company name, source URL, and company website domain or careers URL when present.
3. Preserve role association and source attribution so discoveries can be traced to the originating board.
4. Normalize and deduplicate companies before ATS checks.

### 7.2 Levels.fyi Public Discovery Contract

Levels.fyi is used as a `crawler_company_discovery` source using public routes only.

Preferred retrieval order:
1. Fetch `/jobs?searchText={role query}` and extract company links when available.
2. Fetch `/t/{role-slug}` and extract company links such as `/companies/{company-slug}/salaries`.
3. Fallback to `/t/{role-slug}.md` and parse markdown links to company salary pages when HTML extraction yields insufficient results.

Output contract:
- Persist `source = "levelsfyi"` in source attribution metadata.
- Persist the matched identity role string in `discovery_sources.role`.
- Persist the discovered Levels company URL in `discovery_sources.source_url`.
- Do not require `careers_url` or company website domain at discovery time.

Non-goals:
- No dependency on the official paid Levels API.
- No direct `crawler_ats_job_extraction` ingestion from Levels pages in this phase; `crawler_levelsfyi` handles Levels job extraction as an independent workflow.

Compliance: respect `robots.txt` and `llms.txt`; prefer sitemap/markdown-friendly routes; preserve attribution if data is surfaced downstream.

### 7.3 Curated Startup and Job Communities

Complementary high-signal sources for ATS-backed companies and startup hiring.

| Website | Root URL | Path / Pattern | Discovery output |
|---|---|---|---|
| Y Combinator | `ycombinator.com` | `/companies` | Company directory plus careers links when exposed |
| Wellfound | `wellfound.com` | `/jobs` or `/company/:slug` | Startup job-board results plus company metadata |
| Work at a Startup | `workatastartup.com` | `/companies` | YC-aligned company and hiring discovery |

- Extract company names and any discoverable domain, job-board, or careers links.
- Capture redirects and slugs that may reveal ATS provider hints.
- Tolerate missing role filters on community directories by treating them as supporting sources.

### 7.4 Portfolio and Investor Directories

Company-first discovery candidates for startup ecosystems where ATS-backed hiring is common.

| Website | Root URL | Path / Pattern | Data type |
|---|---|---|---|
| Crunchbase | `crunchbase.com` | `/organization/:permalink` | Firmographics |
| Techstars | `techstars.com` | `/portfolio` | Portfolio list |
| 500 Global | `500.co` | `/startups/` | Portfolio list |
| a16z | `a16z.com` | `/investment-list/` | Portfolio list |
| Sequoia | `sequoiacap.com` | `/our-companies/` | Portfolio list |

- Extract at minimum the company name and any public website, domain, careers link, or permalink that can support later ATS validation.
- If a source yields only firmographic metadata without usable company links, record telemetry and skip ATS validation for that company in this run.

### 7.5 Community Hiring Threads

Opportunistic discovery for companies not reliably surfaced by structured boards.

| Website | Root URL | Path / Pattern | Note |
|---|---|---|---|
| Hacker News | `hn.algolia.com` | `/?query=Who%27s%20hiring&sort=byDate` | Monthly `Who is Hiring` threads |

- Extract company names, careers URLs, domains, and role relevance from thread content.
- Treat thread-derived discoveries as lower-structure signals; they must still pass normalization, deduplication, and ATS validation.
- When company identity cannot be resolved confidently, preserve telemetry and skip ATS follow-up.

---

## 8. Compliance and Rate Limiting

- Access patterns must respect legal and operational constraints of each source.
- Use bounded concurrency and anti-bot controls (2–5 second pacing between page fetches).
- Sources may be skipped for a run if public accessibility, response format, or compliance posture makes extraction impractical.

---

## 7. Company Upsert Semantics

- Companies are keyed by `canonical_name` (lowercased, punctuation-stripped).
- Duplicate companies within one discovery run are merged before upsert (`deduplicate_companies`).
- `upsert_companies` returns `(inserted_count, updated_count, company_ids)`.
- Companies that already have both `ats_provider` and `ats_slug` set do **not** receive a `CompanyDiscoveryEvent`.

---

## 8. Enrichment Event Output Contract

```
CompanyDiscoveryEvent {
  run_id:          string  // parent run_id
  workflow_run_id: string  // this workflow's run_id
  workflow_id:     "crawler_company_discovery"
  identity_id:     string
  company_id:      string  // MongoDB _id hex
  reason:          "no_ats_slug"
  emitted_at:      Timestamp
}
```

Pushed to `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME`.

---

## 9. Failure Handling

| Scenario | Behaviour |
|---|---|
| Malformed dispatch message | Drop, log WARNING |
| Missing `run_id` or `identity_id` | Drop, log WARNING |
| Identity not found or has no roles | Fail the run, publish `failed` progress |
| Individual adapter exception | Record in `failed_sources`, continue |
| Enrichment event push failure | Log WARNING per company, continue |
| Redis connection loss | `redis_client = None`, sleep 2 s, reconnect |

---

## 11. Editing Guardrails

- Do **not** add ATS detection or job fetching logic to this package.
- Source adapters live in `../sources/`; add new adapters there and register them in `get_enabled_adapters`.
- The `reason` field of `CompanyDiscoveryEvent` must remain `"no_ats_slug"` for events emitted by this workflow.
- When adding a new source family or adapter, update section 7 of this file to document the source table and expected behavior.
