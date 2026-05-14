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

Database ownership:
- `identities` is a per-user collection in `cover_letter_<sub>`.
- `sub` comes from authenticated JWT claims and is not user-overridable.
- Identity `field_id` references global `fields` documents in `cover_letter_global`.

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
| `scheduled_crawl_enabled` | `scheduled_crawl_enabled` | `bool` | Optional scheduler override. If omitted, global scheduler default applies. |
| `html_signature` | `html_signature` | `string` | HTML email signature; max 64 KiB; omitted if empty |
| `field_info` | `fieldInfo` | `Field` | Populated by `$lookup` aggregation; omitted on insert |

One identity per field is enforced at the application level. Duplicate `field_id` for one identity is blocked.

Roles and preferences boundary:
- `roles` define crawler discovery scope.
- `preferences` define scoring behavior.

Scheduled crawl override boundary:
- Global scheduler enablement and cron timing are configured via environment variables.
- `scheduled_crawl_enabled` is identity-local override only.
- If `scheduled_crawl_enabled` is absent, the scheduler falls back to global default behavior.

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
- Preference updates are applied with immediate bulk recompute semantics for existing score documents of that identity.

Preference update lifecycle:
1. Compute key sets from old and new lists:
	- `updated_keys`: keys present in both lists where `guidance`, `weight`, or `enabled` changed.
	- `removed_keys`: keys present in old list and absent from new list.
2. Persist the new identity preference list.
3. Load all `job-preference-scores` documents for that `identity_id`.
4. For each loaded score document:
	- remove embedded `preference_scores` entries with `preference_key` in `removed_keys`;
	- for embedded entries with `preference_key` in `updated_keys`, refresh `preference_weight` from the new identity preference;
	- when `guidance` changed for a key in `updated_keys`, recompute that single preference score and update its embedded snapshot (`preference_guidance`, `preference_weight`, `score`, `scored_at`);
	- recompute and persist `weighted_score` deterministically after all per-document mutations, excluding entries where `score_available=false` (N/A);
	- persist `weighted_score_available=false` when no entry remains available for weighting.
5. If a score document has zero remaining embedded `preference_scores` after removal, set `scoring_status` to `skipped` and persist `weighted_score = 0`.

Removal lookup rule:
- Matching for cleanup must use `preference_key` only.

## Implementation

- Canonical behavior is implemented in `domains/identities/handlers.go`.


