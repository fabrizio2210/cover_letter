# crawler_ycombinator — Specification

**Authoritative reference for the `crawler_ycombinator` package.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [`../SPEC.md`](../SPEC.md)
> Shared references: `../../go/cmd/api/SPEC.md`, `../../../spec.md`

---

## 1. Purpose and Scope

The `crawler_ycombinator` package discovers employer companies from Y Combinator, deduplicates and upserts them into MongoDB, then emits `CompanyDiscoveryEvent` messages for every company that still needs ATS enrichment.

This package does **not** detect ATS providers, fetch jobs, or produce scoring queue entries.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Workflow module | `src.python.web_crawler.crawler_ycombinator.workflow` |
| Worker module | `src.python.web_crawler.crawler_ycombinator.worker` |
| Docker CMD | `python -m src.python.web_crawler.crawler_ycombinator.worker --worker` |
| Input queue | `CRAWLER_YCOMBINATOR_QUEUE_NAME` (default `crawler_ycombinator_queue`) |
| Output queue | `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME` — one `CompanyDiscoveryEvent` per company needing enrichment |
| Progress channel | `CRAWLER_PROGRESS_CHANNEL_NAME` |
| Workflow ID | `crawler_ycombinator` |

---

## 3. Environment Variables

Inherited from `CrawlerConfig`. Relevant subset:

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_HOST` | `mongodb://localhost:27017/` | MongoDB URI |
| `DB_NAME` | `cover_letter` | Database name |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `CRAWLER_YCOMBINATOR_QUEUE_NAME` | `crawler_ycombinator_queue` | Input queue |
| `CRAWLER_ENRICHMENT_ATS_ENRICHMENT_QUEUE_NAME` | `crawler_enrichment_ats_enrichment_queue` | Enrichment event output |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | Progress channel |

---

## 4. Responsibilities

- Parse `WorkflowDispatchMessage` from the input queue; drop malformed messages.
- Load the identity document from MongoDB `identities` and extract `roles`; raise if identity is missing or has no roles.
- Run `YCombinatorAdapter` against the identity's role list.
- Deduplicate discovered companies by canonical name before upserting.
- Upsert companies into the `companies` collection with `discovery_sources`, `canonical_name`, and optional `field_id`.
- Determine which upserted companies have no `ats_slug` yet — these are pending enrichment.
- Push one `CompanyDiscoveryEvent(reason="no_ats_slug")` per pending company to the enrichment queue.
- Publish `running` → `completed` (or `failed`) progress snapshots on the progress channel.
- Reconnect to Redis automatically on connection loss.

---

## 5. Editing Guardrails

- Do **not** add ATS detection or job fetching logic to this package.
- The `reason` field of `CompanyDiscoveryEvent` must remain `"no_ats_slug"` for events emitted by this workflow.
