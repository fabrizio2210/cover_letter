# scheduler - Specification

**Authoritative reference for the scheduled crawl-trigger producer in the web crawler service set.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [`../SPEC.md`](../SPEC.md)
> Shared references: `../../../spec.md`, `../../../go/cmd/api/domains/crawls/SPEC.md`, `../../../go/cmd/api/domains/identities/SPEC.md`

---

## 1. Purpose and Scope

The `scheduler` package is a periodic trigger producer for identity-scoped crawl runs.
It reads identities, applies scheduling eligibility rules, and enqueues `CrawlTrigger` payloads on `CRAWLER_TRIGGER_QUEUE_NAME`.

The scheduler does not execute crawl workflows directly. Crawl execution remains owned by dispatcher and crawler workers.

This specification covers:
- runtime behavior and scheduling loop contract;
- environment variables for scheduler enablement and cadence;
- identity selection and override rules;
- queue publication contract;
- failure and skip behavior.

This specification does not cover:
- crawler extraction logic;
- progress event aggregation semantics;
- frontend polling or SSE behavior.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Module | `src.python.web_crawler.scheduler.main` |
| Docker CMD | `python -m src.python.web_crawler.scheduler.main --worker` |
| Execution style | Long-lived scheduler loop |
| Trigger cadence | Cron expression from env |
| Timezone | UTC only |
| Output queue | `CRAWLER_TRIGGER_QUEUE_NAME` |

High-level loop:
1. Load scheduler config.
2. Evaluate current UTC time against configured cron expression.
3. On each cron tick, iterate identities in user databases.
4. For each eligible identity, check whether a crawl is already active.
5. If active, skip enqueue and log warning.
6. If not active, publish a `CrawlTrigger` payload.

---

## 3. Environment Variables

| Variable | Default | Required | Purpose |
|---|---|---|---|
| `CRAWLER_SCHEDULER_ENABLED` | `0` | No | Enables periodic trigger producer when `1` |
| `CRAWLER_SCHEDULER_CRON` | `0 */6 * * *` | No | Cron expression interpreted in UTC |
| `CRAWLER_SCHEDULER_SCAN_BATCH_SIZE` | `200` | No | Max identities processed per scan batch |
| `CRAWLER_SCHEDULER_DB_TIMEOUT_SECONDS` | `30` | No | Mongo query timeout guardrail |
| `MONGO_HOST` | `mongodb://localhost:27017/` | Yes | MongoDB connection URI |
| `REDIS_HOST` | `localhost` | No | Redis host |
| `REDIS_PORT` | `6379` | No | Redis port |
| `CRAWLER_TRIGGER_QUEUE_NAME` | `crawler_trigger_queue` | No | Redis queue for crawl-trigger payloads |

Rules:
- Queue and channel names must remain env-driven and must not be hardcoded.
- Cron expressions are interpreted in UTC only.
- Invalid cron config is fatal at startup and should prevent scheduler loop start.

---

## 4. Identity Selection Rules

Database enumeration:
- Scheduler discovers user databases by querying MongoDB for all databases matching pattern `cover_letter_*`.
- Extract `user_id` from database name by removing the `cover_letter_` prefix (the suffix is the JWT `sub` claim).
- Iterate identities within each user database's `identities` collection.

Global policy:
- Scheduler behavior is enabled or disabled globally using `CRAWLER_SCHEDULER_ENABLED`.
- Tick timing is defined globally with `CRAWLER_SCHEDULER_CRON`.

Per-identity override policy:
- Identity documents may include optional `scheduled_crawl_enabled`.
- If `scheduled_crawl_enabled` is set, it overrides global default for that identity.
- If `scheduled_crawl_enabled` is absent, scheduler uses global default behavior.

Eligibility decision:
- Skip identity when scheduler is disabled by effective policy.
- Skip identity when a crawl is already active for that identity.
- Enqueue trigger when effective policy is enabled and identity has no active crawl.

---

## 5. Queue Contract

Queue: `CRAWLER_TRIGGER_QUEUE_NAME` (default `crawler_trigger_queue`)

Payload shape:

```json
{
  "user_id": "<jwt sub>",
  "run_id": "<server- or worker-generated run id>",
  "identity_id": "<identity hex object id>",
  "requested_at": { "seconds": 1711234567, "nanos": 0 }
}
```

Contract rules:
- Scheduler must publish the same payload shape used by manual API crawl triggers.
- `user_id`, `run_id`, and `identity_id` are required and non-empty.
- Scheduler must not publish user-controlled database names; workers derive per-user DB from `user_id`.

---

## 6. Failure and Logging

| Scenario | Behavior |
|---|---|
| Invalid cron expression | Fail fast at startup with error log |
| Mongo read error for one user DB | Log warning, continue with next DB |
| Redis publish failure | Retry with backoff; preserve at-least-once behavior |
| Identity already running crawl | Skip enqueue and log warning |
| Missing identity required fields | Skip identity and log warning |

Observability expectations:
- Log cron tick start and completion counts.
- Log enqueue decisions with `user_id`, `identity_id`, and `run_id`.
- Log skip reason categories (`disabled`, `already_running`, `invalid_identity`).

---

## 7. Editing Guardrails

- Do not add crawler extraction business logic to scheduler.
- Do not bypass `CRAWLER_TRIGGER_QUEUE_NAME` with direct workflow dispatch.
- Keep trigger payload contract aligned with crawls domain specification.
- Keep one-active-crawl-per-identity protections intact by respecting active-run checks before enqueue.
