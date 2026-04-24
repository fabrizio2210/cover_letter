# Backend API Specification

**This file is the authoritative reference for the Go REST API service.**
Agents editing any handler, model, or route MUST consult this file before making changes.
It documents exact field names (JSON vs BSON divergences are a common source of bugs), Redis queue payload schemas, and all HTTP routes with request/response bodies.

> Source of truth: `handlers/`, `models/models.go`, `../../internal/proto/common/common.proto`, `main.go`

---

## 1. Tech Stack & Runtime

| Component | Detail |
|---|---|
| Language | Go |
| HTTP framework | Gin |
| Database | MongoDB (official Go driver) |
| Cache / Queue | Redis (go-redis) |
| Auth | JWT HS256 (golang-jwt) |
| Server port | `:8080` |
| Entry point | `main.go` |

---

## 2. Environment Variables

All variables are read at runtime. Handlers read `DB_NAME` lazily (inside each handler call, not at startup) so they all share one table.

| Variable | Default | Required | Used by |
|---|---|---|---|
| `JWT_SECRET` | `change_this_secret` | Yes (change in prod) | `main.go` — JWT signing/verification |
| `ADMIN_PASSWORD` | *(none)* | Yes | `handlers/auth.go` — login check |
| `MONGO_HOST` | *(none)* | Yes | `db/mongo.go` — full MongoDB URI (e.g. `mongodb://mongo:27017/`) |
| `DB_NAME` | `cover_letter` | No | all handlers — MongoDB database name |
| `REDIS_HOST` | `localhost` | No | `handlers/cover_letters.go` `init()` |
| `REDIS_PORT` | `6379` | No | `handlers/cover_letters.go` `init()` |
| `REDIS_QUEUE_GENERATE_COVER_LETTER_NAME` | `cover_letter_generation_queue` | No | `handlers/recipients.go`, `handlers/cover_letters.go` |
| `CRAWLER_TRIGGER_QUEUE_NAME` | `crawler_trigger_queue` | No | crawl-trigger producer handlers |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | No | crawler-progress consumer and SSE relay |
| `SCORING_PROGRESS_CHANNEL_NAME` | `scoring_progress_channel` | No | scoring-progress consumer and SSE relay |
| `JOB_SCORING_QUEUE_NAME` | `job_scoring_queue` | No | job-description scoring producer handlers |
| `CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME` | `enrichment_retiring_jobs_queue` | No | job-description check producer handler |
| `JOB_UPDATE_CHANNEL_NAME` | `job_update_channel` | No | job-update consumer and SSE relay |
| `EMAILS_TO_SEND_QUEUE` | `emails_to_send` | No | `handlers/cover_letters.go` |

---

## 3. Data Models

Models are type aliases to protobuf-generated structs in `../../internal/proto/common/common.pb.go`.
The proto source is `../../internal/proto/common/common.proto`.

Proto-first contract (applies to all APIs):
- `common.proto` is the canonical schema for API wire fields and MongoDB field tags via generated structs.
- For endpoints whose request body mirrors a model, handlers MUST bind directly to `models.<Type>` (proto-backed alias) instead of custom request structs.
- For inserts/updates, use the proto-backed model shape as the source of truth for JSON/BSON mapping.
- If a payload field is missing from proto, update `common.proto` and regenerate before introducing handler-local schema changes.
- Only use custom request structs for endpoint-specific payloads that intentionally do not mirror a model (documented exceptions in §7 Conventions).

**Critical**: JSON field names (used in HTTP request/response bodies) differ from BSON field names (used in MongoDB storage) in several places. Both are listed.

### 3.1 Field

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `field` | `field` | `string` | Name of the sector/field |

### 3.2 Company

The proto message also does not include ATS enrichment fields. They are stored as raw BSON fields and may be written by crawler workflows.

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `name` | `name` | `string` | |
| `description` | `description` | `string` | |
| `field_id` | `field` | `string` | Hex ObjectID ref → `fields` collection. **BSON key is `field`, not `field_id`** |
| `canonical_name` | `canonical_name` | `string` | Crawler-managed normalized name used for idempotent company upserts |
| `discovery_sources` | `discovery_sources` | `[]CompanyDiscoverySource` | Crawler-managed source attribution metadata |
| `ats_provider` | `ats_provider` | `string` | Nullable; one of `greenhouse`, `lever`, `ashby`. **Not in proto** |
| `ats_slug` | `ats_slug` | `string` | Nullable provider slug used for ATS extraction. **Not in proto** |
| `field_info` | `fieldInfo` | `Field` | Populated by `$lookup` aggregation; omitted on insert |

### 3.3 Recipient

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `email` | `email` | `string` | |
| `name` | `name` | `string` | |
| `description` | `description` | `string` | |
| `company_id` | `company` | `string` | Hex ObjectID ref → `companies` collection. **BSON key is `company`, not `company_id`** |
| `company_info` | `companyInfo` | `Company` | Populated by `$lookup` aggregation; omitted on insert |

### 3.4 Identity

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `identity` | `identity` | `string` | Internal identifier string |
| `name` | `name` | `string` | Display name |
| `description` | `description` | `string` | |
| `field_id` | `field` | `string` | Hex ObjectID ref → `fields` collection. **BSON key is `field`, not `field_id`** |
| `roles` | `roles` | `[]string` | Optional manual role list for crawler discovery scope, for example `software engineer`, `platform engineer` |
| `html_signature` | `html_signature` | `string` | HTML email signature; max **64 KiB** enforced; omitted if empty |
| `field_info` | `fieldInfo` | `Field` | Populated by `$lookup` aggregation; omitted on insert |

One identity per field enforced at the application level — duplicate `field_id` for an identity is blocked.

Identities also carry a weighted preference list used to score job descriptions.

Role and preference boundary:
- `roles` define crawler discovery scope.
- `preferences` define scoring behavior.

**IdentityPreference**

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `key` | `key` | `string` | Stable preference identifier, for example `remote_work` |
| `guidance` | `guidance` | `string` | Human-friendly preference guidance |
| `weight` | `weight` | `number` | Deterministic aggregate ranking uses this value |
| `enabled` | `enabled` | `bool` | Disabled preferences are ignored by scoring |

The `preferences` field on `Identity` is therefore a JSON array and BSON array of `IdentityPreference` objects.

### 3.4.1 JobDescription

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `company_id` | `company` | `string` | Hex ObjectID ref → `companies`. **BSON key is `company`, not `company_id`** |
| `company_info` | `companyInfo` | `Company` | Populated by `$lookup` aggregation |
| `title` | `title` | `string` | Normalized job title |
| `description` | `description` | `string` | Full job description body |
| `location` | `location` | `string` | Free-form location string |
| `platform` | `platform` | `string` | Source platform, for example `ashby` or `lever` |
| `external_job_id` | `external_job_id` | `string` | Platform-native stable job id |
| `source_url` | `source_url` | `string` | Canonical URL for the job |
| `created_at` | `created_at` | Timestamp object | See §3.7 |
| `updated_at` | `updated_at` | Timestamp object | See §3.7 |

Storage note:
- API JSON uses string ids (`company_id`), but new MongoDB writes for relation fields should use `ObjectId` values.
- Read paths must remain tolerant of legacy string reference storage where present.

### 3.4.2 JobPreferenceScore

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `job_id` | `job_id` | `string` | Hex ObjectID ref → `job-descriptions` |
| `identity_id` | `identity_id` | `string` | Hex ObjectID ref → `identities` |
| `preference_scores` | `preference_scores` | `[]PreferenceScore` | Embedded per-preference score results for this job/identity pair |
| `scoring_status` | `scoring_status` | `string` | one of `queued`, `scored`, `failed`, `skipped` |
| `weighted_score` | `weighted_score` | `number` | Deterministic aggregate for this `(job_id, identity_id)` pair |
| `max_score` | `max_score` | `integer` | Maximum attainable score span for this score document |

One `JobPreferenceScore` document exists per `(job_id, identity_id)` pair.

**PreferenceScore**

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `preference_key` | `preference_key` | `string` | Stable preference identifier |
| `preference_guidance` | `preference_guidance` | `string` | Human-friendly guidance snapshot |
| `preference_weight` | `preference_weight` | `number` | Weight snapshot used in deterministic ranking |
| `score` | `score` | `integer` | AI-generated score from 1 to 5 |
| `scored_at` | `scored_at` | Timestamp object | See §3.7 |

### 3.5 CoverLetter

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID |
| `recipient_id` | `recipient_id` | `string` | Hex ObjectID ref → `recipients`. Stored as string (not ObjectID type) |
| `conversation_id` | `conversation_id` | `string` | Gemini conversation ID |
| `cover_letter` | `cover_letter` | `string` | Markdown body of the letter |
| `prompt` | `prompt` | `string` | Prompt used to generate/refine |
| `history` | `history` | `[]HistoryEntry` | Full Gemini conversation history |
| `created_at` | `created_at` | Timestamp object | See §3.7 |
| `updated_at` | `updated_at` | Timestamp object | See §3.7 |
| `status` | `status` | `string` | e.g. `draft`, `generated`, `sent` — set by AI querier |
| `recipient_info` | `recipientInfo` | `Recipient` | Populated by `$lookup` aggregation; omitted on insert |

### 3.6 HistoryEntry / HistoryPart

**HistoryEntry**

| JSON key | BSON key | Type |
|---|---|---|
| `role` | `role` | `string` (`"user"` or `"model"`) |
| `parts` | `parts` | `[]HistoryPart` |

**HistoryPart**

| JSON key | BSON key | Type |
|---|---|---|
| `text` | `text` | `string` |

### 3.7 Timestamps

`created_at` and `updated_at` are stored in MongoDB as plain objects (not BSON Date):

```json
{ "seconds": 1711234567, "nanos": 0 }
```

They are **not** ISO 8601 strings. The Python `ai_querier` writes them this way; the Go API reads them back as-is.

---

## 4. MongoDB Collections

| Collection name | Used by handlers |
|---|---|
| `fields` | `fields.go`, `companies.go` (lookup), `identities.go` (lookup) |
| `companies` | `companies.go`, `recipients.go` (lookup) |
| `recipients` | `recipients.go`, `cover_letters.go` (lookup) |
| `identities` | `identities.go` |
| `job-descriptions` | planned job handlers and crawler ingestion |
| `job-preference-scores` | planned scoring aggregation and ranking endpoints |
| `cover-letters` | `cover_letters.go` (note the hyphen) |

---

## 5. Redis Queue Contracts

The API is a Redis **producer** for list-backed work queues and a Redis **consumer/relay** for crawler and scoring progress events. List-backed work dispatch uses `RPUSH`; workers consume with `BLPOP`. Progress snapshots are published by workers and relayed by the API to browser clients through server-side stream endpoints.

### 5.1 `cover_letter_generation_queue`

Env var: `REDIS_QUEUE_GENERATE_COVER_LETTER_NAME` (default: `cover_letter_generation_queue`)
Consumer: Python `ai_querier` service.

The consumer distinguishes **initial generation** from **refinement** by the presence of `conversation_id`.

**Initial generation** (from `POST /api/recipients/:id/generate-cover-letter`):

```json
{
  "recipient": "<email address>"
}
```

**Refinement** (from `POST /api/cover-letters/:id/refine`):

```json
{
  "recipient":       "<email address>",
  "conversation_id": "<gemini conversation id string>",
  "prompt":          "<user refinement prompt>"
}
```

Rules enforced by the consumer:
- Missing `recipient` → message is dropped with an error log.
- Missing `conversation_id` → treated as initial generation.
- `conversation_id` present but `prompt` absent → consumer will attempt refinement with an empty prompt (avoid this).

### 5.2 `job_scoring_queue`

Env var: `JOB_SCORING_QUEUE_NAME` (default: `job_scoring_queue`)
Consumer: Python `ai_scorer` service.

**Payload** (from `POST /api/job-descriptions/:id/score` or automatic post-crawl enqueue):

```json
{
  "job_id": "<job description hex object id>"
}
```

Rules enforced by the consumer:
- Missing `job_id` → message is dropped with an error log.
- The worker resolves the job description, company, field, identity, and identity preferences from MongoDB.
- AI returns only per-preference score; the weighted aggregate is computed deterministically by application logic and persisted back onto the job description.
- Queue ownership split: `ai_querier` consumes only cover-letter jobs; `ai_scorer` consumes only job-scoring jobs.

Producer-side lifecycle expectations:
- Post-crawl producers enqueue on both insert and update when scoring enqueue is enabled.
- Re-crawl updates should always produce a new `job_id` queue message for rescoring.
- Jobs that cannot resolve scoring prerequisites may be marked `skipped` and not enqueued.

### 5.3 `emails_to_send`

Env var: `EMAILS_TO_SEND_QUEUE` (default: `emails_to_send`)
Consumer: Go `emailer` service (not yet implemented, stub only).

**Payload** (from `POST /api/cover-letters/:id/send`):

```json
{
  "recipient":    "<email address>",
  "cover_letter": "<markdown body of the letter>"
}
```

The emailer is expected to:
- Lookup the recipient's associated `Identity` to get the `html_signature`.
- Convert the Markdown `cover_letter` to HTML.
- Wrap the HTML body in the `html_signature`.
- Send via SMTP.

### 5.4 `enrichment_retiring_jobs_queue`

Env var: `CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME` (default: `enrichment_retiring_jobs_queue`)
Consumer: Python `web_crawler` `enrichment_retiring_jobs` worker.

**Payload** (from `POST /api/job-descriptions/:id/check`):

```json
{
  "job_id": "<job description hex object id>"
}
```

Rules enforced by the consumer:
- Missing `job_id` → message is dropped with a warning log.
- The worker probes the job's `source_url`; if it returns HTTP 404, the job is marked as closed (`is_open=false`).
- If the job has been closed for more than 60 days it is deleted.

### 5.5 `crawler_trigger_queue`

Env var: `CRAWLER_TRIGGER_QUEUE_NAME` (default: `crawler_trigger_queue`)
Consumer: Python `web_crawler` service.

**Payload** (from `POST /api/crawls`):

```json
{
  "run_id": "<server-generated crawl run id>",
  "identity_id": "<identity hex object id>",
  "requested_at": { "seconds": 1711234567, "nanos": 0 }
}
```

Rules enforced by the consumer:
- Missing `run_id` or `identity_id` → message is rejected with an error log.
- A run without a valid `identity_id` must not start.
- If a crawl for the same `identity_id` is already active, the worker must reject the new request and publish a terminal progress event with `status = "rejected"` and `reason = "already_running"`.

Producer-side expectations:
- The API must generate a stable `run_id` before enqueueing.
- The API should reject duplicate active-run requests for the same identity with HTTP `409` whenever current active state is known.
- Queueing the crawl request is asynchronous; success means the request was accepted for worker pickup, not that crawling has started yet.

### 5.6 `crawler_progress_channel`

Env var: `CRAWLER_PROGRESS_CHANNEL_NAME` (default: `crawler_progress_channel`)
Publisher: Python `web_crawler` service.
Consumer: Go API service, which relays updates to authenticated browser clients.

**Payload**:

```json
{
  "run_id": "<crawl run id>",
  "workflow_run_id": "<workflow execution attempt id>",
  "workflow_id": "crawler_company_discovery",
  "identity_id": "<identity hex object id>",
  "status": "queued",
  "workflow": "queued",
  "message": "Waiting for worker pickup",
  "estimated_total": 100,
  "completed": 0,
  "percent": 0,
  "started_at": null,
  "updated_at": { "seconds": 1711234567, "nanos": 0 },
  "finished_at": null,
  "reason": ""
}
```

Status values:
- `queued`
- `running`
- `completed`
- `failed`
- `rejected`

Workflow values:
- `queued`
- `crawler_company_discovery`
- `enrichment_ats_enrichment`
- `crawler_ats_job_extraction`
- `crawler_4dayweek`
- `crawler_levelsfyi`
- `finalizing`

Rules:
- `percent` must be an integer from `0` to `100`.
- `estimated_total` and `completed` represent best-effort work units, such as pages, companies, or jobs depending on the active workflow; both must be present even when estimated totals are approximate.
- `workflow_id` is the stable workflow key for workflow-level events.
- `workflow_run_id` identifies one workflow execution attempt and changes on retry.
- `workflow` is the active workflow label for crawl progress and may be `queued` or `finalizing` for parent-run lifecycle events.
- `started_at` is set on the first `running` event.
- `finished_at` is set only for terminal states: `completed`, `failed`, or `rejected`.
- `reason` is reserved for terminal diagnostics, for example `already_running` or a short failure code.
- The API must treat the most recent event per `workflow_run_id` as the authoritative live snapshot for that workflow contribution.
- The API may expose multiple active workflow contributions for one `run_id` and one `identity_id`.
- Dashboard workflow visibility stats for the latest completed run are served by a dedicated endpoint in section 7.7 and are not inferred from `estimated_total`/`completed` progress units.

### 5.7 `scoring_progress_channel`

Env var: `SCORING_PROGRESS_CHANNEL_NAME` (default: `scoring_progress_channel`)
Publisher: Python `ai_scorer` service.
Consumer: Go API service, which relays updates to authenticated browser clients.

**Payload**:

```json
{
  "run_id": "<scoring run id>",
  "identity_id": "<identity hex object id>",
  "status": "running",
  "message": "Scoring in progress",
  "estimated_total": 24,
  "completed": 7,
  "percent": 29,
  "started_at": { "seconds": 1711234567, "nanos": 0 },
  "updated_at": { "seconds": 1711234600, "nanos": 0 },
  "finished_at": null,
  "reason": ""
}
```

Status values:
- `running`
- `completed`
- `failed`

Rules:
- `percent` must be an integer from `0` to `100`.
- A new scoring run starts from `0%` and is independent from crawl progress.
- `started_at` is set on the first `running` event.
- `finished_at` is set for terminal states: `completed` and `failed`.
- The API must treat the most recent event per `run_id` as the authoritative live snapshot exposed to clients.

### 5.8 `job_update_channel`

Env var: `JOB_UPDATE_CHANNEL_NAME` (default: `job_update_channel`)
Publisher: Python `enrichment_retiring_jobs` worker (after completing a per-job check).
Consumer: Go API service, which relays events to authenticated browser clients via the `GET /api/job-descriptions/stream` SSE endpoint.

**Payload** (`JobUpdateEvent` from `common.proto`):

```json
{
  "job_id": "<hex ObjectID of the checked/updated job>",
  "workflow_id": "enrichment_retiring_jobs",
  "workflow_run_id": "<workflow run identifier>",
  "emitted_at": { "seconds": <unix>, "nanos": 0 }
}
```

Rules:
- Published once per successful job retirement run (status `completed`).
- `job_id` identifies the exact job document that was checked and potentially modified.
- The UI receives this event and reloads the specific job via `GET /api/job-descriptions/:id`.

---

## 6. Authentication

- All routes except `POST /api/login` require a valid JWT.
- Token is passed in the HTTP header: `Authorization: Bearer <token>`
- Algorithm: **HS256** (HMAC-SHA256). Any other algorithm is rejected.
- Token lifetime: **24 hours** from issue.
- Claims: `{ "exp": <unix timestamp> }` — no user identity in the token (single-user app).
- The middleware (`middleware/jwt.go`) returns `401` for missing, malformed, wrong-algorithm, or expired tokens.

### Login

`POST /api/login` — no auth required.

Request:
```json
{ "password": "string" }
```

Response `200`:
```json
{ "token": "<jwt string>" }
```

Response `401`:
```json
{ "error": "Unauthorized" }
```

---

## 7. HTTP API Reference

Base path prefix: `/api`

### Conventions
- All request bodies are JSON (`Content-Type: application/json`).
- All `200`/`201` responses are JSON.
- Long-lived progress subscriptions use `text/event-stream` and are documented explicitly where applicable.
- `:id` path parameters are MongoDB hex ObjectID strings.
- `201` is used for successful creation; `200` for everything else.
- `202` is used for accepted asynchronous crawl-trigger requests.
- `409` is returned for identity-scoped crawl trigger conflicts when an active run already exists for that identity.
- `404` is returned when a document matching `:id` is not found.
- Aggregation responses embed lookup results (`field_info`, `company_info`, `recipient_info`) directly in the returned object.
- Proto-first implementation rule: when an endpoint body matches a domain model, bind and validate with `models.<Type>` generated from `common.proto`.
- Do not create custom request DTOs that duplicate proto-defined fields; this causes JSON/BSON drift bugs.
- If a handler must persist ObjectID references (`field`, `company`), convert proto string IDs (`field_id`, `company_id`) to Mongo ObjectID before write.

Allowed custom-payload exceptions (intentional, endpoint-specific):
- Partial update endpoints whose body is not a full model (for example: `/name`, `/description`, `/signature`, `/field`, `/company`, `/roles`, `/preferences`).
- `POST /api/login` (`{ password }`).
- `PUT /api/cover-letters/:id` (`{ content }`) because payload key differs from model field name.
- Queue-producing endpoints that build Redis payload objects.

---

### 7.1 Fields

Implementation guardrail for maintainers:
- `POST /api/fields` and full-model operations should use `models.Field` as the request schema source.
- Avoid custom structs for proto-defined fields unless covered by Conventions exceptions.

#### `GET /api/fields`
Auth: required.
Response `200`: array of `Field`.
```json
[{ "id": "<hex>", "field": "Photography" }]
```

#### `POST /api/fields`
Auth: required.
Request:
```json
{ "field": "string" }
```
Response `201`: created `Field` with `id` populated.

#### `PUT /api/fields/:id`
Auth: required.
Request:
```json
{ "field": "string" }
```
Response `200`:
```json
{ "message": "Field updated successfully" }
```

#### `DELETE /api/fields/:id`
Auth: required.
Response `200`:
```json
{ "message": "Field deleted successfully" }
```

---

### 7.2 Companies

Implementation guardrail for maintainers:
- Use `models.Company` as the schema source for proto-defined fields (`id`, `name`, `field_id`, `field_info`, `description`, `canonical_name`, `discovery_sources`).
- `ats_provider` and `ats_slug` remain documented non-proto fields today; if they become canonical, add them to `common.proto` and regenerate.

#### `GET /api/companies`
Auth: required.
Response `200`: array of `Company` with `field_info` embedded (nullable via `$unwind preserveNullAndEmptyArrays`).

#### `POST /api/companies`
Auth: required.
Request:
```json
{
  "name": "string",
  "description": "string",
  "field_id": "<hex or empty>",
  "ats_provider": "<greenhouse|lever|ashby or omit>",
  "ats_slug": "<provider slug or omit>"
}
```
Response `201`: created company document. If `field_id` was provided, `field_info` is included.

#### `PUT /api/companies/:id`
Auth: required.
Request (`name`, `description`, `field_id` required; ATS fields optional; `field_id` must be valid hex):
```json
{
  "name": "string",
  "description": "string",
  "field_id": "<hex>",
  "ats_provider": "<greenhouse|lever|ashby or omit>",
  "ats_slug": "<provider slug or omit>"
}
```
Response `200`:
```json
{ "message": "Company updated successfully" }
```

Crawler interoperability note:
- Crawler workflows may update only `ats_provider` and `ats_slug` on existing companies.
- Legacy company documents may not include ATS fields and remain valid.

#### `PUT /api/companies/:id/field`
Auth: required.
Request (`field_id` as `null` or absent removes the association):
```json
{ "field_id": "<hex or null>" }
```
Response `200`:
```json
{ "message": "Field associated successfully", "modifiedCount": 1 }
```

#### `DELETE /api/companies/:id`
Auth: required.
Response `200`:
```json
{ "message": "Company deleted successfully" }
```

---

### 7.3 Recipients

Implementation guardrail for maintainers:
- For `POST /api/recipients`, handlers MUST bind the request body directly to `models.Recipient` (proto-backed type alias) and use that model shape for insertion.
- Do NOT introduce a custom request struct for this endpoint unless `common.proto` is updated first and regenerated.
- Reason: custom structs can drift from proto JSON/BSON tags and silently break `company_id` / `company` mapping and `company_info` lookup behavior.

#### `GET /api/recipients`
Auth: required.
Response `200`: array of `Recipient` with `company_info` embedded (nullable).

#### `POST /api/recipients`
Auth: required.
Request (mirrors `Recipient` model; `company_id` optional):
```json
{ "email": "string", "name": "string", "description": "string", "company_id": "<hex or omit>" }
```
Handler contract:
- Bind request JSON to `models.Recipient`.
- Convert `company_id` to MongoDB ObjectID before insert so lookup joins on `companies._id` correctly.
- Keep BSON field mapping aligned with proto tags (`company_id` JSON maps to `company` BSON).

Response `201`: created `Recipient` with `id` populated.

#### `DELETE /api/recipients/:id`
Auth: required.
Response `200`:
```json
{ "message": "Recipient deleted successfully" }
```

#### `PUT /api/recipients/:id/description`
Auth: required.
Request:
```json
{ "description": "string" }
```
Response `200`:
```json
{ "message": "Recipient description updated successfully" }
```

#### `PUT /api/recipients/:id/name`
Auth: required.
Request:
```json
{ "name": "string" }
```
Response `200`:
```json
{ "message": "Recipient name updated successfully" }
```

#### `PUT /api/recipients/:id/company`
Auth: required.
Request (`companyId` as `null` removes the association — **note camelCase key**):
```json
{ "companyId": "<hex or null>" }
```
Response `200`:
```json
{ "message": "Company associated successfully", "modifiedCount": 1 }
```

#### `POST /api/recipients/:id/generate-cover-letter`
Auth: required.
No request body.
Pushes initial-generation message to `cover_letter_generation_queue` (see §5.1).
Response `200`:
```json
{ "message": "Generation queued successfully" }
```

---

### 7.4 Identities

Implementation guardrail for maintainers:
- `POST /api/identities` should bind to `models.Identity` (proto-backed) for model fields.
- Keep `field_id` (JSON) to `field` (BSON) mapping aligned with proto tags and convert to ObjectID before write.

#### `GET /api/identities`
Auth: required.
Response `200`: array of `Identity` with `field_info` embedded (nullable).

#### `POST /api/identities`
Auth: required.
Request (mirrors `Identity` model):
```json
{
  "identity":       "string",
  "name":           "string",
  "description":    "string",
  "field_id":       "<hex or empty>",
  "roles":          ["string", "string"],
  "html_signature": "<html string or omit>"
}
```
Response `201`: created `Identity` with `id` populated.

`roles` behavior:
- Role values are manually maintained by the user.
- Crawler discovery uses this list as primary query seeds.

#### `PUT /api/identities/:id/roles`
Auth: required.
Request:
```json
{ "roles": ["string"] }
```
Response `200`:
```json
{ "message": "Identity updated successfully" }
```

Rules:
- This endpoint replaces the full roles list for the identity.
- Role order is preserved as provided in the request.

#### `DELETE /api/identities/:id`
Auth: required.
Response `200`:
```json
{ "message": "Identity deleted successfully" }
```

#### `PUT /api/identities/:id/description`
Auth: required.
Request:
```json
{ "description": "string" }
```
Response `200`:
```json
{ "message": "Identity updated successfully" }
```

#### `PUT /api/identities/:id/name`
Auth: required.
Request:
```json
{ "name": "string" }
```
Response `200`:
```json
{ "message": "Identity updated successfully" }
```

#### `PUT /api/identities/:id/signature`
Auth: required.
Request (`html_signature` max 64 KiB):
```json
{ "html_signature": "<html string>" }
```
Response `200`:
```json
{ "message": "Identity updated successfully" }
```
Response `400` if payload exceeds 64 KiB.

#### `PUT /api/identities/:id/field`
Auth: required.
Request (`fieldId` must be valid hex ObjectID — **note camelCase key**):
```json
{ "fieldId": "<hex>" }
```
Response `200`:
```json
{ "message": "Identity updated successfully" }
```

#### `PUT /api/identities/:id/preferences`
Auth: required.
Request:
```json
{
  "preferences": [
    {
      "key": "remote_work",
      "guidance": "Prefer fully remote roles over hybrid ones.",
      "weight": 2,
      "enabled": true
    }
  ]
}
```
Response `200`:
```json
{ "message": "Identity updated successfully" }
```

Rules:
- Preference keys must be unique inside one identity.
- This endpoint replaces the full preference list for the identity.
- When preferences change, the default follow-up behavior is to schedule a full re-score for the affected identity rather than incremental recalculation.

---

### 7.5 Job Descriptions

Implementation guardrail for maintainers:
- `JobDescription` and `JobPreferenceScore` are proto-first domain models and should become the schema source once added to `common.proto`.
- The crawler may discover a company before it exists in the database; in that case the ingestion flow should resolve by name or create the company before persisting the job description.

#### `GET /api/job-descriptions`
Auth: required.
Response `200`: array of score-neutral `JobDescription` with `company_info` embedded.

#### `GET /api/job-descriptions/:id`
Auth: required.
Response `200`: single score-neutral `JobDescription` with `company_info`.
Response `404`:
```json
{ "error": "Job description not found" }
```

#### `POST /api/job-descriptions`
Auth: required.
Request:
```json
{
  "company_id": "<hex or omit>",
  "company_name": "<string or omit when company_id is set>",
  "title": "string",
  "description": "string",
  "location": "string",
  "platform": "string",
  "external_job_id": "string",
  "source_url": "string"
}
```
Behavior:
- If `company_id` is present, the handler links to that company.
- If `company_id` is absent and `company_name` is present, the handler should resolve or create the company automatically.

Response `201`: created `JobDescription`.

#### `PUT /api/job-descriptions/:id`
Auth: required.
Request:
```json
{
  "company_id": "<hex or omit>",
  "title": "string",
  "description": "string",
  "location": "string",
  "platform": "string",
  "external_job_id": "string",
  "source_url": "string"
}
```
Response `200`:
```json
{ "message": "Job description updated successfully" }
```

#### `DELETE /api/job-descriptions/:id`
Auth: required.
Response `200`:
```json
{ "message": "Job description deleted successfully" }
```

#### `POST /api/job-descriptions/:id/score`
Auth: required.
No request body.
Pushes a message to `job_scoring_queue` (see §5.2).
Response `200`:
```json
{ "message": "Scoring queued successfully" }
```

#### `POST /api/job-descriptions/:id/check`
Auth: required.
No request body.
Pushes a `JobRetireEvent` message to `enrichment_retiring_jobs_queue` (see §5.4) to trigger the `enrichment_retiring_jobs` workflow for that job.
Response `202`:
```json
{ "message": "Check queued successfully" }
```

#### `GET /api/job-descriptions/stream`
Auth: required.
Long-lived SSE (`text/event-stream`) connection.
Relays `JobUpdateEvent` messages from the `job_update_channel` Redis pub/sub channel to connected browser clients.
Each event is emitted with `event: job-update` and a JSON `data:` line.

**SSE event payload**:
```json
{
  "job_id": "<hex ObjectID of the updated job>",
  "workflow_id": "enrichment_retiring_jobs",
  "workflow_run_id": "<workflow run identifier>",
  "emitted_at": { "seconds": <unix>, "nanos": 0 }
}
```

The UI subscribes to this stream and reloads the specific job document when it receives a `job-update` event for a displayed job.

#### `GET /api/job-preference-scores`
Auth: required.
Query params:
- `job_id` optional hex ObjectId string.
- `identity_id` optional hex ObjectId string.

Response `200`: array of `JobPreferenceScore` documents filtered by the provided params.

Ranking semantics:
- The AI writes per-preference scores only.
- The application computes the weighted aggregate deterministically from the identity preference weights.
- The aggregate score is persisted only on the matching `job-preference-scores` document.

---

### 7.6 Cover Letters

Implementation guardrail for maintainers:
- Use `models.CoverLetter` as the schema source for model-aligned payloads and responses.
- Endpoint-specific payload wrappers (for example `{ content }` in manual update and queue payloads) are valid only where explicitly documented.

#### `GET /api/cover-letters`
Auth: required.
Response `200`: array of `CoverLetter` with `recipient_info` embedded.

#### `GET /api/cover-letters/:id`
Auth: required.
Response `200`: single `CoverLetter` with `recipient_info` embedded.
Response `404`:
```json
{ "error": "Cover letter not found" }
```

#### `DELETE /api/cover-letters/:id`
Auth: required.
Response `200`:
```json
{ "message": "Cover letter deleted successfully" }
```

#### `PUT /api/cover-letters/:id`
Auth: required.
Manually overwrites the `cover_letter` body (no AI involved).
Request:
```json
{ "content": "string" }
```
Response `200`:
```json
{ "message": "Cover letter updated successfully" }
```

#### `POST /api/cover-letters/:id/refine`
Auth: required.
Pushes a refinement message to `cover_letter_generation_queue` (see §5.1).
Request:
```json
{ "prompt": "string" }
```
Response `200`:
```json
{ "message": "Refinement queued successfully" }
```

#### `POST /api/cover-letters/:id/send`
Auth: required.
Pushes the cover letter to `emails_to_send` queue (see §5.2).
No request body.
Response `200`:
```json
{ "message": "Email queued successfully" }
```

---

### 7.7 Crawl Control And Progress

Implementation guardrail for maintainers:
- Crawl control payloads are endpoint-specific queue wrappers; they are intentionally not full proto-backed domain models.
- Frontend clients do not read Redis directly. The API is responsible for exposing the latest crawl state and a live push stream.

#### `POST /api/crawls`
Auth: required.
Request:
```json
{ "identity_id": "<hex>" }
```

Behavior:
- Enqueues a crawl request onto `crawler_trigger_queue` (see §5.4).
- Generates a `run_id` before enqueueing.
- Rejects the request with `409` when an active crawl already exists for the same `identity_id`.

Response `202`:
```json
{
  "message": "Crawl queued successfully",
  "run_id": "<crawl run id>",
  "identity_id": "<hex>",
  "status": "queued"
}
```

Response `409`:
```json
{
  "error": "A crawl is already running for this identity",
  "run_id": "<active crawl run id>",
  "identity_id": "<hex>",
  "status": "running"
}
```

#### `GET /api/crawls/active`
Auth: required.
Response `200`: array of active or recently terminal crawl snapshots.

```json
[
  {
    "run_id": "<crawl run id>",
    "workflow_run_id": "<workflow attempt id>",
    "workflow_id": "crawler_company_discovery",
    "identity_id": "<hex>",
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
]
```

Rules:
- This endpoint provides the latest server-side snapshot used to bootstrap UI state after refresh.
- Multiple entries may exist for the same `run_id` and `identity_id` when multiple workflows are active.
- The API should preserve distinct workflow contributions by `workflow_run_id`.
- Filtering by `identity_id` may be supported via query string when only one identity view is needed.

#### `GET /api/crawls/last-run/workflow-stats`
Auth: required.
Response `200`: latest completed parent crawl run workflow stats for dashboard visibility.

```json
{
  "run_id": "<latest completed parent crawl run id>",
  "completed_at": { "seconds": 1711234600, "nanos": 0 },
  "workflows": [
    {
      "workflow_id": "crawler_company_discovery",
      "discovered_jobs": 0,
      "discovered_companies": 42
    },
    {
      "workflow_id": "crawler_levelsfyi",
      "discovered_jobs": 18,
      "discovered_companies": 18
    },
    {
      "workflow_id": "crawler_4dayweek",
      "discovered_jobs": 6,
      "discovered_companies": 6
    },
    {
      "workflow_id": "crawler_ats_job_extraction",
      "discovered_jobs": 27,
      "discovered_companies": 0
    }
  ]
}
```

Response `200` when no completed run exists yet:

```json
{
  "run_id": "",
  "completed_at": null,
  "workflows": []
}
```

Rules:
- This endpoint is identity-agnostic and does not accept identity filters.
- `run_id` refers to the latest completed parent crawl run globally across identities.
- Only workflows with ids prefixed by `crawler_` are returned; `enrichment_` workflows are excluded.
- `discovered_jobs` and `discovered_companies` are persisted-result counters (`inserted + updated`) and are non-negative integers.
- Workflows should be returned in stable display order: `crawler_company_discovery`, `crawler_levelsfyi`, `crawler_4dayweek`, `crawler_ats_job_extraction`.

Current implementation note (interim):
- the API currently keeps latest completed-run workflow visibility snapshots in memory; restarting the API process clears this state until a new run completes.
- durable historical run-summary persistence in MongoDB is planned as a follow-up hardening step.

#### `GET /api/crawls/stream`
Auth: required.
Response `200`: `text/event-stream`.

Event name: `crawl-progress`

Event data payload matches the §5.5 `crawler_progress_channel` payload.

Rules:
- The stream is server-to-client only.
- The API may multiplex updates for multiple identities on one stream; clients are expected to filter by `identity_id`.
- Clients must also distinguish workflow contributions by `workflow_run_id` and `workflow_id`.
- The Dashboard and Job Discovery views both consume this stream.

---

### 7.8 Scoring Progress

Implementation guardrail for maintainers:
- Frontend clients do not read Redis directly. The API is responsible for exposing latest scoring snapshots and a live push stream.

#### `GET /api/scoring/active`
Auth: required.
Response `200`: array of active or recently terminal scoring snapshots.

```json
[
  {
    "run_id": "<scoring run id>",
    "identity_id": "<hex>",
    "status": "running",
    "message": "Scoring in progress",
    "estimated_total": 24,
    "completed": 7,
    "percent": 29,
    "started_at": { "seconds": 1711234567, "nanos": 0 },
    "updated_at": { "seconds": 1711234600, "nanos": 0 },
    "finished_at": null,
    "reason": ""
  }
]
```

Rules:
- This endpoint provides the latest server-side snapshot used to bootstrap UI state after refresh.
- Filtering by `identity_id` may be supported via query string when only one identity view is needed.

#### `GET /api/scoring/stream`
Auth: required.
Response `200`: `text/event-stream`.

Event name: `scoring-progress`

Event data payload matches the §5.6 `scoring_progress_channel` payload.

Rules:
- The stream is server-to-client only.
- The API may multiplex updates for multiple identities on one stream; clients are expected to filter by `identity_id`.
- In shared frontend progress widgets, crawl progress takes precedence over scoring progress when both are active.

---

## 8. Out of Scope

The following services exist as empty stubs (`src/go/cmd/emailer/`, `src/go/cmd/authentication/`) and are **not yet implemented**:

- **`emailer`** — consumes `emails_to_send` queue and sends emails via SMTP. When implemented, it will need `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` environment variables.
- **`authentication`** — future dedicated auth service. Currently auth is embedded in the API handler (`handlers/auth.go`).

For Python worker specs (`ai_querier`, `ai_scorer`, `telegram_bot`, `web_crawler`) see separate spec files.
