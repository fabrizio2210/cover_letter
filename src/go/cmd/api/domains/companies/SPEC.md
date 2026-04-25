# Companies Domain Specification

This file is the authoritative reference for company handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- GET /api/companies
- POST /api/companies
- PUT /api/companies/:id
- PUT /api/companies/:id/field
- DELETE /api/companies/:id

## Runtime Contract

Current migration state:
- Canonical behavior is implemented in domains/companies/handlers.go.

Response and validation highlights:
- GET normalizes Mongo `_id` to `id` and maps lookup payload to `field_info`.
- POST validates optional `field_id` as ObjectID when present.
- PUT validates both `:id` and `field_id` and returns 404 when no company is matched.
- PUT /field supports unset behavior when `field_id` is null or empty.
- DELETE returns 404 when target company does not exist.

