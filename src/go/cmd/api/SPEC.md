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
| `EMAILS_TO_SEND_QUEUE` | `emails_to_send` | No | `handlers/cover_letters.go` |

---

## 3. Data Models

Models are type aliases to protobuf-generated structs in `../../internal/proto/common/common.pb.go`.
The proto source is `../../internal/proto/common/common.proto`.

**Critical**: JSON field names (used in HTTP request/response bodies) differ from BSON field names (used in MongoDB storage) in several places. Both are listed.

### 3.1 Field

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `field` | `field` | `string` | Name of the sector/field |

### 3.2 Company

The proto message does not include `description`. It is stored as a raw BSON field by the handler.

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `name` | `name` | `string` | |
| `description` | `description` | `string` | **Not in proto** — written directly as bson.M by handler |
| `field_id` | `field` | `string` | Hex ObjectID ref → `fields` collection. **BSON key is `field`, not `field_id`** |
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
| `html_signature` | `html_signature` | `string` | HTML email signature; max **64 KiB** enforced; omitted if empty |
| `field_info` | `fieldInfo` | `Field` | Populated by `$lookup` aggregation; omitted on insert |

One identity per field enforced at the application level — duplicate `field_id` for an identity is blocked.

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
| `cover-letters` | `cover_letters.go` (note the hyphen) |

---

## 5. Redis Queue Contracts

The API is a **producer only**. It uses `RPUSH`; consumers use `BLPOP`.

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

### 5.2 `emails_to_send`

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
- `:id` path parameters are MongoDB hex ObjectID strings.
- `201` is used for successful creation; `200` for everything else.
- `404` is returned when a document matching `:id` is not found.
- Aggregation responses embed lookup results (`field_info`, `company_info`, `recipient_info`) directly in the returned object.

---

### 7.1 Fields

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

#### `GET /api/companies`
Auth: required.
Response `200`: array of `Company` with `field_info` embedded (nullable via `$unwind preserveNullAndEmptyArrays`).

#### `POST /api/companies`
Auth: required.
Request:
```json
{ "name": "string", "description": "string", "field_id": "<hex or empty>" }
```
Response `201`: created company document. If `field_id` was provided, `field_info` is included.

#### `PUT /api/companies/:id`
Auth: required.
Request (all three fields required; `field_id` must be valid hex):
```json
{ "name": "string", "description": "string", "field_id": "<hex>" }
```
Response `200`:
```json
{ "message": "Company updated successfully" }
```

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

#### `GET /api/recipients`
Auth: required.
Response `200`: array of `Recipient` with `company_info` embedded (nullable).

#### `POST /api/recipients`
Auth: required.
Request (mirrors `Recipient` model; `company_id` optional):
```json
{ "email": "string", "name": "string", "description": "string", "company_id": "<hex or omit>" }
```
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
  "html_signature": "<html string or omit>"
}
```
Response `201`: created `Identity` with `id` populated.

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

---

### 7.5 Cover Letters

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

## 8. Out of Scope

The following services exist as empty stubs (`src/go/cmd/emailer/`, `src/go/cmd/authentication/`) and are **not yet implemented**:

- **`emailer`** — consumes `emails_to_send` queue and sends emails via SMTP. When implemented, it will need `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` environment variables.
- **`authentication`** — future dedicated auth service. Currently auth is embedded in the API handler (`handlers/auth.go`).

For Python worker specs (`ai_querier`, `telegram_bot`, `web_crawler`) see a separate spec file if created.
