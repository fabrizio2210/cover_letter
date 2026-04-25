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

## Runtime Contract

Current migration state:
- Canonical behavior is implemented in domains/recipients/handlers.go.

Behavior highlights:
- Create validates optional company_id as ObjectID and returns created recipient with lookup-enriched company_info when available.
- Update name and description return 404 when target is not found or unchanged.
- Associate company accepts companyId null to unset association.
- Generate cover letter enqueues payload with recipient email to REDIS_QUEUE_GENERATE_COVER_LETTER_NAME.

