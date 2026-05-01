# Backend API Specification

This file is the global index and shared contract guidance for the Go REST API service.
Detailed endpoint, model, queue, and stream contracts live in domain specifications.

> Source of truth: `main.go`, `facade/`, `domains/`, `models/models.go`, `../../internal/proto/common/common.proto`

## Subdivision Index

- Facade layer: `facade/SPEC.md`
- Auth domain: `domains/auth/SPEC.md`
- Fields domain: `domains/fields/SPEC.md`
- Companies domain: `domains/companies/SPEC.md`
- Recipients domain: `domains/recipients/SPEC.md`
- Identities domain: `domains/identities/SPEC.md`
- Cover letters domain: `domains/coverletters/SPEC.md`
- Jobs domain: `domains/jobs/SPEC.md`
- Crawls domain: `domains/crawls/SPEC.md`

Ownership rule:
- Endpoint implementations live under `domains/<domain>/` and are re-exported from `facade/`.

---

## 1. Runtime Architecture

| Component | Detail |
|---|---|
| Language | Go |
| HTTP framework | Gin |
| Database | MongoDB (official Go driver) |
| Cache and Queue | Redis (go-redis) |
| Auth | JWT HS256 (golang-jwt) |
| Server port | `:8080` |
| Entry point | `main.go` |

Database layout contract:
- Global database (fixed): `cover_letter_global`
- Per-user database: `cover_letter_<sub>` where `sub` is the authenticated user JWT claim

Global collections:
- `job-descriptions`
- `companies`
- `fields`
- `global_settings`
- `stats`

Per-user collections:
- `cover-letters`
- `identities`
- `job-preference-scores`
- `recipients`
- `user_settings`
- `crawls`

---

## 2. Environment Variables

| Variable | Default | Required | Used by |
|---|---|---|---|
| `JWT_SECRET` | `change_this_secret` | Yes (change in prod) | User JWT signing/verification |
| `AUTH_USERS_JSON` | *(none)* | Yes | User login credential map (`/api/login`) |
| `ADMIN_JWT_SECRET` | *(none)* | Yes | Admin JWT signing/verification |
| `ADMIN_PASSWORD` | *(none)* | Yes (for admin login) | Admin login credential check |
| `MONGO_HOST` | *(none)* | Yes | `db/mongo.go` MongoDB URI |
| `REDIS_HOST` | `localhost` | No | Redis clients in handlers |
| `REDIS_PORT` | `6379` | No | Redis clients in handlers |
| `REDIS_QUEUE_GENERATE_COVER_LETTER_NAME` | `cover_letter_generation_queue` | No | Recipients and cover letters domains |
| `CRAWLER_TRIGGER_QUEUE_NAME` | `crawler_trigger_queue` | No | Crawls domain |
| `CRAWLER_PROGRESS_CHANNEL_NAME` | `crawler_progress_channel` | No | Crawls domain SSE relay |
| `SCORING_PROGRESS_CHANNEL_NAME` | `scoring_progress_channel` | No | Crawls domain SSE relay |
| `JOB_SCORING_QUEUE_NAME` | `job_scoring_queue` | No | Jobs domain |
| `CRAWLER_ENRICHMENT_RETIRING_JOBS_QUEUE_NAME` | `enrichment_retiring_jobs_queue` | No | Jobs domain |
| `JOB_UPDATE_CHANNEL_NAME` | `job_update_channel` | No | Jobs domain SSE relay |
| `EMAILS_TO_SEND_QUEUE` | `emails_to_send` | No | Cover letters domain |

---

## 3. Shared API Conventions

Base path prefix: `/api`

- All request bodies are JSON (`Content-Type: application/json`).
- `200` for successful non-create operations.
- `201` for create operations.
- `202` for accepted asynchronous operations.
- `404` when `:id` targets are not found.
- `409` for identity-scoped crawl trigger conflicts.
- Long-lived progress subscriptions use `text/event-stream`.
- Aggregation responses may embed lookup results (`field_info`, `company_info`, `recipient_info`).

Proto-first implementation rules:
- `common.proto` is the canonical schema for API wire fields and Mongo field tags via generated structs.
- If an endpoint body mirrors a model, handlers must bind directly to `models.<Type>`.
- Avoid custom DTOs that duplicate proto-defined fields; this causes JSON/BSON drift.
- If a payload field is missing from proto, update `common.proto` and regenerate before handler-local schema changes.

Queue contract rule:
- Async queue payloads that are user-scoped must include `user_id` and workers must derive per-user DB name from that field.
- No user-controlled field may override per-user DB derivation from authenticated identity.
- Scoring payloads for `job_scoring_queue` must include `identity_id`.

Timestamp format used across domains:

```json
{ "seconds": 1711234567, "nanos": 0 }
```

---

## 4. Authentication Summary

User flow:
- User login endpoint authenticates `{ username, password }` against the preconfigured `AUTH_USERS_JSON` map and issues JWT with `sub` and `exp`.
- Unknown username and wrong password both return `401 Unauthorized`.
- Missing or invalid `AUTH_USERS_JSON` returns `500 Configuration error`.
- User `sub` is derived as deterministic username hash (SHA-256 first 16 bytes, lowercase hex).
- Middleware validates HS256 with `JWT_SECRET` and extracts `sub` into request context.
- Per-user DB name is derived from `sub` as `cover_letter_<sub>`.

Admin flow:
- Admin login endpoint issues JWT with `sub`, `role = "admin"`, and `exp`.
- Admin middleware validates HS256 with `ADMIN_JWT_SECRET`.
- Routes under `/api/admin/*` require `role == "admin"`.

Full login contract lives in `domains/auth/SPEC.md`.

---

## 5. Contract Ownership Matrix

### Models

- Field model: `domains/fields/SPEC.md`
- Company model: `domains/companies/SPEC.md`
- Recipient model: `domains/recipients/SPEC.md`
- Identity and IdentityPreference models: `domains/identities/SPEC.md`
- JobDescription and JobPreferenceScore models: `domains/jobs/SPEC.md`
- CoverLetter and History models: `domains/coverletters/SPEC.md`

### HTTP Endpoints

- Auth endpoints: `domains/auth/SPEC.md`
- Admin fields endpoints: `domains/fields/SPEC.md`
- Companies endpoints: `domains/companies/SPEC.md`
- Recipients endpoints: `domains/recipients/SPEC.md`
- Identities endpoints: `domains/identities/SPEC.md`
- Job and scoring endpoints: `domains/jobs/SPEC.md`
- Cover letter endpoints: `domains/coverletters/SPEC.md`
- Crawl and progress endpoints: `domains/crawls/SPEC.md`

### Queues and Streams

- `cover_letter_generation_queue`: `domains/recipients/SPEC.md` and `domains/coverletters/SPEC.md`
- `emails_to_send`: `domains/coverletters/SPEC.md`
- `job_scoring_queue`: `domains/jobs/SPEC.md`
- `enrichment_retiring_jobs_queue`: `domains/jobs/SPEC.md`
- `job_update_channel`: `domains/jobs/SPEC.md`
- `crawler_trigger_queue`: `domains/crawls/SPEC.md`
- `crawler_progress_channel`: `domains/crawls/SPEC.md`
- `scoring_progress_channel`: `domains/crawls/SPEC.md`

---

## 6. Out Of Scope

The following services exist as empty stubs and are not implemented yet:

- `src/go/cmd/emailer/`
- `src/go/cmd/authentication/`

For worker-level Python behavior, refer to Python service spec files.
