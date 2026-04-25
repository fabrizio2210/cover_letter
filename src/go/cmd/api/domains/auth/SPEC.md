# Auth Domain Specification

This file is the authoritative reference for auth handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- POST /api/login

Out of scope:
- JWT middleware validation for protected endpoints (owned by middleware package)

## Runtime Contract

- Handler entrypoint: Login(jwtSecret []byte) gin.HandlerFunc
- Password source: ADMIN_PASSWORD environment variable
- Token algorithm: HS256
- Token expiry claim: exp = now + 24h

## Request/Response

Request:
```json
{ "password": "string" }
```

Success response (200):
```json
{ "token": "<jwt>" }
```

Validation failures:
- 400 with `{ "error": "Invalid request" }` when body cannot be parsed.
- 401 with `{ "error": "Unauthorized" }` when password is missing/wrong or ADMIN_PASSWORD is empty.
- 500 with `{ "error": "Token error" }` if signing fails.

## Implementation

- Canonical implementation lives in domains/auth/login.go.
