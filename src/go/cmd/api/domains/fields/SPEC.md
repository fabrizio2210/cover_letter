# Fields Domain Specification

This file is the authoritative reference for field handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- GET /api/fields
- POST /api/fields
- PUT /api/fields/:id
- DELETE /api/fields/:id

## Model Contract

### Field

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `field` | `field` | `string` | Name of the sector/field |

Proto-first rules:
- `common.proto` is the canonical schema for wire fields and Mongo tags.
- `POST /api/fields` and full-model operations should bind to `models.Field`.
- Avoid custom request structs for proto-defined fields unless payload is endpoint-specific.

## HTTP Contract

Common rules:
- Auth required for all endpoints in this domain.
- `:id` must be a MongoDB hex ObjectID string.

### GET /api/fields

Response `200`: array of `Field`.

```json
[{ "id": "<hex>", "field": "Photography" }]
```

### POST /api/fields

Request:

```json
{ "field": "string" }
```

Response `201`: created `Field` with `id` populated.

### PUT /api/fields/:id

Request:

```json
{ "field": "string" }
```

Response `200`:

```json
{ "message": "Field updated successfully" }
```

### DELETE /api/fields/:id

Response `200`:

```json
{ "message": "Field deleted successfully" }
```

## Implementation

- Canonical behavior is implemented in `domains/fields/handlers.go`.


