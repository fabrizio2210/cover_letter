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

Database ownership:
- `companies` is a global collection in `cover_letter_global`.
- Company endpoints do not use per-user Mongo databases.

## Model Contract

### Company

The proto message does not include ATS enrichment fields. They are stored as raw BSON fields and may be written by crawler workflows.

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `name` | `name` | `string` | |
| `description` | `description` | `string` | |
| `field_id` | `field` | `string` | Hex ObjectID ref to `fields`. BSON key is `field`, not `field_id` |
| `canonical_name` | `canonical_name` | `string` | Crawler-managed normalized name used for idempotent company upserts |
| `discovery_sources` | `discovery_sources` | `[]CompanyDiscoverySource` | Crawler-managed source attribution metadata |
| `ats_provider` | `ats_provider` | `string` | Nullable; one of `greenhouse`, `lever`, `ashby`. Not in proto |
| `ats_slug` | `ats_slug` | `string` | Nullable provider slug used for ATS extraction. Not in proto |
| `field_info` | `fieldInfo` | `Field` | Populated by `$lookup` aggregation; omitted on insert |

Proto-first rules:
- Use `models.Company` as the schema source for proto-defined fields.
- If ATS fields become canonical, add them to `common.proto` and regenerate.

## HTTP Contract

Common rules:
- Auth required for all endpoints in this domain.
- `:id` must be a MongoDB hex ObjectID string.

### GET /api/companies

Response `200`: array of `Company` with `field_info` embedded (nullable via `$unwind preserveNullAndEmptyArrays`).

### POST /api/companies

Request:

```json
{
	"name": "string",
	"description": "string",
	"field_id": "<hex or empty>",
	"ats_provider": "<greenhouse|lever|ashby or omit>",
	"ats_slug": "<provider slug or omit>"
}
```

Response `201`: created company document. If `field_id` was provided, `field_info` is included.

### PUT /api/companies/:id

Request (`name`, `description`, `field_id` required; ATS fields optional; `field_id` must be valid hex):

```json
{
	"name": "string",
	"description": "string",
	"field_id": "<hex>",
	"ats_provider": "<greenhouse|lever|ashby or omit>",
	"ats_slug": "<provider slug or omit>"
}
```

Response `200`:

```json
{ "message": "Company updated successfully" }
```

Crawler interoperability note:
- Crawler workflows may update only `ats_provider` and `ats_slug` on existing companies.
- Legacy company documents may not include ATS fields and remain valid.

### PUT /api/companies/:id/field

Request (`field_id` as `null` or absent removes the association):

```json
{ "field_id": "<hex or null>" }
```

Response `200`:

```json
{ "message": "Field associated successfully", "modifiedCount": 1 }
```

### DELETE /api/companies/:id

Response `200`:

```json
{ "message": "Company deleted successfully" }
```

## Implementation

- Canonical behavior is implemented in `domains/companies/handlers.go`.


