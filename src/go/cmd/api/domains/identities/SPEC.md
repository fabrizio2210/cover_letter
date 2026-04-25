# Identities Domain Specification

This file is the authoritative reference for identity handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- GET /api/identities
- POST /api/identities
- DELETE /api/identities/:id
- PUT /api/identities/:id/description
- PUT /api/identities/:id/name
- PUT /api/identities/:id/signature
- PUT /api/identities/:id/roles
- PUT /api/identities/:id/preferences
- PUT /api/identities/:id/field

## Runtime Contract

Current migration state:
- Canonical behavior now resides in domains/identities/handlers.go.

Behavior highlights:
- GET normalizes `_id` to `id` and `fieldInfo` to `field_info`.
- Signature update rejects payloads larger than 64 KiB.
- Preferences update enforces non-empty unique keys.
- Field association expects request key `fieldId` and validates ObjectID format.

