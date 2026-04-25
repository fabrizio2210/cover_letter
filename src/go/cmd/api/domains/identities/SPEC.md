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

## Model Contract

### Identity

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `id` | `_id` | `string` | Hex ObjectID; omitted on insert |
| `identity` | `identity` | `string` | Internal identifier string |
| `name` | `name` | `string` | Display name |
| `description` | `description` | `string` | |
| `field_id` | `field` | `string` | Hex ObjectID ref to `fields`. BSON key is `field`, not `field_id` |
| `roles` | `roles` | `[]string` | Optional manual role list for crawler discovery scope |
| `html_signature` | `html_signature` | `string` | HTML email signature; max 64 KiB; omitted if empty |
| `field_info` | `fieldInfo` | `Field` | Populated by `$lookup` aggregation; omitted on insert |

One identity per field is enforced at the application level. Duplicate `field_id` for one identity is blocked.

Roles and preferences boundary:
- `roles` define crawler discovery scope.
- `preferences` define scoring behavior.

### IdentityPreference

| JSON key | BSON key | Type | Notes |
|---|---|---|---|
| `key` | `key` | `string` | Stable preference identifier |
| `guidance` | `guidance` | `string` | Human-friendly preference guidance |
| `weight` | `weight` | `number` | Used in deterministic aggregate ranking |
| `enabled` | `enabled` | `bool` | Disabled preferences are ignored by scoring |

Proto-first rules:
- `POST /api/identities` should bind to `models.Identity`.
- Keep `field_id` JSON to `field` BSON mapping aligned with proto tags.
- Convert `field_id` to MongoDB ObjectID before write.

## HTTP Contract

Common rules:
- Auth required for all endpoints in this domain.
- `:id` must be a MongoDB hex ObjectID string.

### GET /api/identities

Response `200`: array of `Identity` with `field_info` embedded (nullable).

### POST /api/identities

Request (mirrors `Identity` model):

```json
{
	"identity": "string",
	"name": "string",
	"description": "string",
	"field_id": "<hex or empty>",
	"roles": ["string", "string"],
	"html_signature": "<html string or omit>"
}
```

Response `201`: created `Identity` with `id` populated.

### PUT /api/identities/:id/roles

Request:

```json
{ "roles": ["string"] }
```

Response `200`:

```json
{ "message": "Identity updated successfully" }
```

Rules:
- This endpoint replaces the full roles list.
- Role order is preserved as provided.

### DELETE /api/identities/:id

Response `200`:

```json
{ "message": "Identity deleted successfully" }
```

### PUT /api/identities/:id/description

Request:

```json
{ "description": "string" }
```

Response `200`:

```json
{ "message": "Identity updated successfully" }
```

### PUT /api/identities/:id/name

Request:

```json
{ "name": "string" }
```

Response `200`:

```json
{ "message": "Identity updated successfully" }
```

### PUT /api/identities/:id/signature

Request (`html_signature` max 64 KiB):

```json
{ "html_signature": "<html string>" }
```

Response `200`:

```json
{ "message": "Identity updated successfully" }
```

Response `400` when payload exceeds 64 KiB.

### PUT /api/identities/:id/field

Request (`fieldId` must be valid hex ObjectID, camelCase key is required):

```json
{ "fieldId": "<hex>" }
```

Response `200`:

```json
{ "message": "Identity updated successfully" }
```

### PUT /api/identities/:id/preferences

Request:

```json
{
	"preferences": [
		{
			"key": "remote_work",
			"guidance": "Prefer fully remote roles over hybrid ones.",
			"weight": 2,
			"enabled": true
		}
	]
}
```

Response `200`:

```json
{ "message": "Identity updated successfully" }
```

Rules:
- Preference keys must be unique inside one identity.
- This endpoint replaces the full preference list.
- Changing preferences should trigger a full re-score for the identity.

## Implementation

- Canonical behavior is implemented in `domains/identities/handlers.go`.


