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

## Runtime Contract

Current migration state:
- Canonical behavior is implemented in domains/coverletters/handlers.go.

Behavior highlights:
- Get endpoints enrich with recipient info using aggregation pipeline.
- Update endpoint writes `cover_letter` from request `content`.
- Refine endpoint enqueues recipient email, conversation_id, and prompt to `REDIS_QUEUE_GENERATE_COVER_LETTER_NAME`.
- Send endpoint enqueues recipient email and body to `EMAILS_TO_SEND_QUEUE`.

