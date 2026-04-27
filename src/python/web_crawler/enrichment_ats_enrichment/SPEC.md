# enrichment_ats_enrichment — Specification

**Authoritative reference for the `enrichment_ats_enrichment` package.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [`../SPEC.md`](../SPEC.md)
> Shared references: `../../go/cmd/api/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `enrichment_ats_enrichment` package consumes `CompanyDiscoveryEvent` messages, probes each company's web presence to identify its ATS provider and career-board slug, writes `ats_provider` and `ats_slug` back to the company document, then dispatches a `WorkflowDispatchMessage` to trigger ATS job extraction for successfully enriched companies.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Workflow module | `src.python.web_crawler.enrichment_ats_enrichment.workflow` |
| Worker module | `src.python.web_crawler.enrichment_ats_enrichment.worker` |
| Docker CMD | `python -m src.python.web_crawler.enrichment_ats_enrichment.worker --worker` |
| Input queue | `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME` (default `crawler_enrichment_ats_enrichment_queue`) |
| Output queue | `CRAWLER_ATS_JOB_EXTRACTION_QUEUE_NAME` — one `WorkflowDispatchMessage` per enriched company |
| Progress channel | `CRAWLER_PROGRESS_CHANNEL_NAME` |
| Workflow ID | `enrichment_ats_enrichment` |

---

## 3. Environment Variables

Inherited from `CrawlerConfig`. Relevant subset:

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | MongoDB URI |
| `DB_NAME` | `cover_letter` | Database name |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME` | `crawler_enrichment_ats_enrichment_queue` | Input queue |
| `CRAWLER_ATS_JOB_EXTRACTION_QUEUE_NAME` | `crawler_ats_job_extraction_queue` | Extraction dispatch output |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | Progress channel |
| `SERPER_API_KEY` | — | Required for SERP-based slug fallback |
| `CRAWLER_HTTP_TIMEOUT_SECONDS` | `20` | HTTP probe timeout |
| `CRAWLER_MAX_RETRIES` | `3` | Per-URL retry limit |
| `CRAWLER_USER_AGENT` | browser-like string | Request user-agent |

---

## 4. Responsibilities

- Parse `CompanyDiscoveryEvent` from the input queue; drop malformed messages.
- Accept events emitted by producers: the `dispatcher` (existing unenriched companies queued at user-trigger time), `crawler_ycombinator`, `crawler_hackernews`, `crawler_levelsfyi` and `crawler_4dayweek` (newly discovered companies).
- For each company: build a list of candidate career URLs from `discovery_sources` (careers_url, source_url, domain-derived paths).
- Run parallel ATS detection across candidate URLs using a `ThreadPoolExecutor` with up to 10 workers.
- **Phase A** (pre-fetch): load company state, skip already-enriched companies (`ats_provider` + `ats_slug` both set) and companies with terminal failures.
- **Phase B** (parallel detection): probe URLs with `_detect_ats_worker`; collect results via `as_completed`.
- On successful direct detection: write `ats_provider` and `ats_slug` to the `companies` collection.
- On `slug_not_resolved_direct`: attempt SERP-based fallback (`resolve_slug_via_search_dorking`) unless a prior search attempt exists for that provider.
- On terminal failure (`dns_resolution`, `timeout`): record `enrichment_ats_enrichment_terminal_failure` on the company document; skip on subsequent runs.
- Dispatch `WorkflowDispatchMessage` to `CRAWLER_ATS_JOB_EXTRACTION_QUEUE_NAME` for each company where enrichment succeeds.
- Publish `running` → `completed` / `failed` progress snapshots per event.
- Report intra-phase progress via `progress_callback` when supplied.

---

## 5. ATS Compatibility Detection

Before attempting slug resolution, each candidate career URL is probed for ATS signature indicators.

**Validation target**: company careers page (e.g. `/careers`, `/jobs`, or equivalent from `discovery_sources`).

| ATS | Indicators in careers HTML or links |
|---|---|
| Greenhouse | `grnh.io`, `boards.greenhouse.io` |
| Lever | `jobs.lever.co`, `.lever-job` markers |
| Ashby | `window.ashby` hints, `ashbyhq.com` links |

Rules:
- If ATS signatures are absent: keep company in discovery telemetry, log the reason, skip extraction for that company in this run.
- If multiple ATS hints appear: prioritize the strongest hosted-board signal and log ambiguity.
- If careers content is client-rendered: optional headless rendering may be used under rate-limiting controls.

---

## 6. Input Contract

```
CompanyDiscoveryEvent {
  run_id:          string
  workflow_run_id: string
  identity_id:     string
  company_id:      string  // MongoDB _id hex
  reason:          string  // e.g. "no_ats_slug"
}
```

---

## 7. Output Contract — ATS Extraction Dispatch

```
WorkflowDispatchMessage {
  run_id:           string
  workflow_run_id:  string  // new UUID hex
  workflow_id:      "crawler_ats_job_extraction"
  identity_id:      string
  company_id:       string
  ats_provider:     string  // e.g. "lever", "greenhouse", "ashby"
  ats_slug:         string
  trigger_kind:     "company_enriched"
  attempt:          1
  dispatched_at:    Timestamp
}
```

---

## 8. Slug Resolution Sequence

1. `_detect_ats_worker` probes all candidate URLs; if direct slug is found in the ATS board URL, resolution succeeds.
2. If ATS provider is detected but direct slug resolution fails (`slug_not_resolved_direct`), the main thread calls `resolve_slug_via_search_dorking`.
3. SERP fallback is skipped if a prior attempt exists for that provider (tracked via `ats_slug_search_attempts.<provider>`).
4. Both attempts and outcomes are recorded per-provider in `ats_slug_search_attempts` on the company document.

**Search dork query patterns** (used in SERP fallback):
- Greenhouse: `site:boards.greenhouse.io "<Company Name>"`
- Lever: `site:jobs.lever.co "<Company Name>"`
- Ashby: `site:jobs.ashbyhq.com "<Company Name>"`

Expected extraction from dork results:
- Parse result URLs.
- Extract the first path segment after host as slug.
- Validate slug by requesting the provider API endpoint.

If slug cannot be resolved via either method:
- Log unresolved source with reason.
- Skip company for this run.

---

## 9. Terminal Failure Recording

```python
company.enrichment_ats_enrichment_terminal_failure = {
  "failure_type": "dns_resolution" | "timeout",
  "failed_at":    datetime,
  "last_url":     string,
  "message":      string,
}
```

Companies with a terminal failure are skipped on all subsequent enrichment runs without re-probing.

---

## 10. Failure Handling

| Scenario | Behaviour |
|---|---|
| Malformed event payload | Drop, log WARNING |
| Missing `identity_id` or `company_id` | Drop, log WARNING |
| Company already enriched | Skip silently (`skipped_count += 1`) |
| Terminal failure on record | Skip, append to `failed_companies` |
| No candidate URLs | Skip, append to `failed_companies` |
| Worker thread exception | `failed_count += 1`, append error |
| SERP fallback returns no slug | `skipped_count += 1`, record `no_results` outcome |
| Redis connection loss | `redis_client = None`, sleep 2 s, reconnect |

---

## 11. Editing Guardrails

- ATS detection logic lives in `../sources/ats_detector.py`; slug resolution in `../sources/ats_slug_resolver.py`.
- Do **not** add job fetching or scoring logic to this package.
- `_company_from_document` is a shared converter also imported by `crawler_ats_job_extraction`; keep its signature and field mapping stable.
- New ATS providers must be registered in `ats_detector.py` and `ats_slug_resolver.py`, not here.
