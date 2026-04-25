# Recipients Domain Specification

This file is the authoritative reference for recipient handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- GET /api/recipients
- POST /api/recipients
- DELETE /api/recipients/:id
- PUT /api/recipients/:id/description
- PUT /api/recipients/:id/name
- PUT /api/recipients/:id/company
- POST /api/recipients/:id/generate-cover-letter

## Model Contract

### Recipient

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `email` | `email` | `string` | |
| `name` | `name` | `string` | |
| `description` | `description` | `string` | |
| `company_id` | `company` | `string` | Hex ObjectID ref to `companies`. BSON key is `company`, not `company_id` |
| `company_info` | `companyInfo` | `Company` | Populated by `$lookup` aggregation; omitted on insert |

Proto-first rules:
- `POST /api/recipients` must bind directly to `models.Recipient`.
- Do not introduce a custom request struct for this endpoint unless `common.proto` is updated and regenerated.
- Convert `company_id` to MongoDB ObjectID before write so joins to `companies._id` work correctly.

## HTTP Contract

Common rules:
- Auth required for all endpoints in this domain.
- `:id` must be a MongoDB hex ObjectID string.

### GET /api/recipients

Response `200`: array of `Recipient` with `company_info` embedded (nullable).

### POST /api/recipients

Request (mirrors `Recipient`; `company_id` optional):

```json
{ "email": "string", "name": "string", "description": "string", "company_id": "<hex or omit>" }
```

Response `201`: created `Recipient` with `id` populated.

### DELETE /api/recipients/:id

Response `200`:

```json
{ "message": "Recipient deleted successfully" }
```

### PUT /api/recipients/:id/description

Request:

```json
{ "description": "string" }
```

Response `200`:

```json
{ "message": "Recipient description updated successfully" }
```

### PUT /api/recipients/:id/name

Request:

```json
{ "name": "string" }
```

Response `200`:

```json
{ "message": "Recipient name updated successfully" }
```

### PUT /api/recipients/:id/company

Request (`companyId` as `null` removes association, camelCase key is required):

```json
{ "companyId": "<hex or null>" }
```

Response `200`:

```json
{ "message": "Company associated successfully", "modifiedCount": 1 }
```

### POST /api/recipients/:id/generate-cover-letter

No request body.

Response `200`:

```json
{ "message": "Generation queued successfully" }
```

## Queue Contract

### Queue: `cover_letter_generation_queue`

Env var: `REDIS_QUEUE_GENERATE_COVER_LETTER_NAME` (default `cover_letter_generation_queue`)

Consumer: Python `ai_querier`.

Produced by: `POST /api/recipients/:id/generate-cover-letter`

Payload:

```json
{
	"recipient": "<email address>"
}
```

Consumer rules:
- Missing `recipient` makes the message invalid and it is dropped with an error log.
- Missing `conversation_id` means initial generation.

## Implementation

- Canonical behavior is implemented in `domains/recipients/handlers.go`.


