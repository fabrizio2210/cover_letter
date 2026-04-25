# Fields Domain Specification

This file is the authoritative reference for field handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- GET /api/fields
- POST /api/fields
- PUT /api/fields/:id
- DELETE /api/fields/:id

## Runtime Contract

Current migration state:
- The canonical behavior is implemented in domains/fields/handlers.go.

Response semantics:
- GET returns normalized objects with `id` (hex string) and without `_id`.
- POST returns created field payload with inserted id.
- PUT returns 200 with success message when modified; 404 when not found/unchanged.
- DELETE returns 200 with success message when deleted; 404 when not found.

