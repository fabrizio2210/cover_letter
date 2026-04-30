# Cover Letters Domain Specification

This file is the authoritative reference for cover-letter handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- GET /api/cover-letters
- GET /api/cover-letters/:id
- DELETE /api/cover-letters/:id
- PUT /api/cover-letters/:id
- POST /api/cover-letters/:id/refine
- POST /api/cover-letters/:id/send

## Model Contract

### CoverLetter

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID |
| `recipient_id` | `recipient_id` | `string` | Hex ObjectID ref to `recipients`; stored as string |
| `conversation_id` | `conversation_id` | `string` | Gemini conversation ID |
| `cover_letter` | `cover_letter` | `string` | Markdown body |
| `prompt` | `prompt` | `string` | Prompt used to generate or refine |
| `history` | `history` | `[]HistoryEntry` | Full Gemini conversation history |
| `created_at` | `created_at` | Timestamp object | `{ "seconds": <unix>, "nanos": 0 }` |
| `updated_at` | `updated_at` | Timestamp object | `{ "seconds": <unix>, "nanos": 0 }` |
| `status` | `status` | `string` | For example `draft`, `generated`, `sent` |
| `recipient_info` | `recipientInfo` | `Recipient` | Populated by `$lookup`; omitted on insert |

### HistoryEntry

| JSON key | BSON key | Type |
|---|---|---|
| `role` | `role` | `string` (`user` or `model`) |
| `parts` | `parts` | `[]HistoryPart` |

### HistoryPart

| JSON key | BSON key | Type |
|---|---|---|
| `text` | `text` | `string` |

Proto-first rule:
- Use `models.CoverLetter` as schema source for model-aligned payloads and responses.

## HTTP Contract

Common rules:
- Auth required for all endpoints in this domain.
- `:id` must be a MongoDB hex ObjectID string.

### GET /api/cover-letters

Response `200`: array of `CoverLetter` with `recipient_info` embedded.

### GET /api/cover-letters/:id

Response `200`: one `CoverLetter` with `recipient_info` embedded.

Response `404`:

```json
{ "error": "Cover letter not found" }
```

### DELETE /api/cover-letters/:id

Response `200`:

```json
{ "message": "Cover letter deleted successfully" }
```

### PUT /api/cover-letters/:id

Manually overwrites `cover_letter` body.

Request:

```json
{ "content": "string" }
```

Response `200`:

```json
{ "message": "Cover letter updated successfully" }
```

### POST /api/cover-letters/:id/refine

Request:

```json
{ "prompt": "string" }
```

Response `200`:

```json
{ "message": "Refinement queued successfully" }
```

### POST /api/cover-letters/:id/send

No request body.

Response `200`:

```json
{ "message": "Email queued successfully" }
```

## Queue Contracts

### Queue: `cover_letter_generation_queue`

Env var: `REDIS_QUEUE_GENERATE_COVER_LETTER_NAME` (default `cover_letter_generation_queue`)

Consumer: Python `ai_querier`.

Produced by: `POST /api/cover-letters/:id/refine`

Payload:

```json
{
	"user_id": "<jwt sub>",
	"recipient": "<email address>",
	"conversation_id": "<gemini conversation id string>",
	"prompt": "<user refinement prompt>"
}
```

Rules:
- Missing `user_id` or `recipient` makes the message invalid and it is dropped.
- Missing `conversation_id` causes consumer to treat it as initial generation.
- If `conversation_id` is present and `prompt` is absent, refinement is attempted with an empty prompt.
- Worker must derive per-user DB name from `user_id` and read global company context from `cover_letter_global`.

### Queue: `emails_to_send`

Env var: `EMAILS_TO_SEND_QUEUE` (default `emails_to_send`)

Consumer: Go `emailer` service (planned).

Produced by: `POST /api/cover-letters/:id/send`

Payload:

```json
{
	"user_id": "<jwt sub>",
	"recipient": "<email address>",
	"cover_letter": "<markdown body of the letter>"
}
```

Expected downstream behavior:
- Resolve recipient `Identity` for `html_signature`.
- Convert Markdown to HTML.
- Wrap body with signature and send through SMTP.

## Implementation

- Canonical behavior is implemented in `domains/coverletters/handlers.go`.


