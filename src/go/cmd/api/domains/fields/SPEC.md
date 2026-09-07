# Fields Domain Specification

This file is the authoritative reference for field handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- GET /api/fields
- GET /api/admin/fields
- POST /api/admin/fields
- PUT /api/admin/fields/:id
- DELETE /api/admin/fields/:id

## Model Contract

### Field

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `field` | `field` | `string` | Name of the sector/field |

Proto-first rules:
- `common.proto` is the canonical schema for wire fields and Mongo tags.
- `POST /api/admin/fields` and full-model operations should bind to `models.Field`.
- Avoid custom request structs for proto-defined fields unless payload is endpoint-specific.

## HTTP Contract

Common rules:
- `:id` must be a MongoDB hex ObjectID string.

### GET /api/fields

User JWT required. Read-only. Returns the full list of fields.

Response `200`: array of `Field`.

```json
[{ "id": "<hex>", "field": "Photography" }]
```

---

Admin-only routes below require `role == "admin"`.

Database ownership:
- `fields` collection is global and stored in `cover_letter_global`.
- User-scoped databases do not own a `fields` collection.

### GET /api/admin/fields

Admin JWT required.

Response `200`: array of `Field`.

```json
[{ "id": "<hex>", "field": "Photography" }]
```

### POST /api/admin/fields

Request:

```json
{ "field": "string" }
```

Response `201`: created `Field` with `id` populated.

### PUT /api/admin/fields/:id

Request:

```json
{ "field": "string" }
```

Response `200`:

```json
{ "message": "Field updated successfully" }
```

### DELETE /api/admin/fields/:id

Response `200`:

```json
{ "message": "Field deleted successfully" }
```

## Implementation

- `GET /api/fields` and all admin CRUD handlers are implemented in `domains/fields/handlers.go`.
- `GetFields` is reused for both the user and admin GET endpoints.

### Startup bootstrap

Before serving requests, the API checks the global `fields` collection. When the collection is empty, it inserts this preset:

- Technology
- Financial Services
- Healthcare
- Education
- Retail & E-commerce
- Manufacturing
- Construction
- Real Estate
- Energy & Utilities
- Transportation & Logistics
- Telecommunications
- Media & Entertainment
- Advertising & Marketing
- Professional Services
- Legal Services
- Government & Public Sector
- Nonprofit & Charities
- Hospitality & Tourism
- Food & Beverage
- Agriculture
- Automotive
- Aerospace & Defense
- Pharmaceuticals & Biotechnology
- Insurance
- Consumer Goods
- Fashion & Apparel
- Sports & Fitness
- Environmental Services
- Arts & Culture
- Other

If any field already exists, the bootstrap leaves the collection unchanged. Preset fields use stable ObjectIDs and idempotent upserts so concurrent API instances do not create duplicate presets. A database error during this check or write prevents the API from starting.

