# Auth Domain Specification

This file is the authoritative reference for auth handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- POST /api/login
- POST /api/admin/login

Out of scope:
- JWT middleware validation for protected endpoints (owned by middleware package)

## Runtime Contract

- User handler entrypoint: `Login(jwtSecret []byte) gin.HandlerFunc`
- Admin handler entrypoint: `AdminLogin(adminJwtSecret []byte) gin.HandlerFunc`
- Token algorithm: HS256
- Token expiry claim: `exp = now + 24h`

JWT claim contracts:
- User token (`POST /api/login`) includes `sub` and `exp`.
- Admin token (`POST /api/admin/login`) includes `sub`, `role = "admin"`, and `exp`.

Signing secrets:
- User token signing and verification use `JWT_SECRET`.
- Admin token signing and verification use `ADMIN_JWT_SECRET`.

## Request/Response

### POST /api/login (user)

Request:
```json
{ "username": "string", "password": "string" }
```

Success response (200):
```json
{ "token": "<jwt>" }
```

Validation failures:
- 400 with `{ "error": "Invalid request" }` when body cannot be parsed.
- 401 with `{ "error": "Unauthorized" }` when password is missing or wrong.
- 500 with `{ "error": "Token error" }` if signing fails.

### POST /api/admin/login (admin)

Request:
```json
{ "password": "string" }
```

Success response (200):
```json
{ "token": "<admin-jwt>" }
```

Validation failures:
- 400 with `{ "error": "Invalid request" }` when body cannot be parsed.
- 401 with `{ "error": "Unauthorized" }` when password is missing or wrong.
- 500 with `{ "error": "Token error" }` if signing fails.

## Middleware Contract Summary

- Protected user routes validate user JWT and must extract `sub` into Gin context for per-user DB derivation.
- Protected admin routes under `/api/admin/*` must require `role == "admin"` and validate token signature with `ADMIN_JWT_SECRET`.

## Implementation

- Canonical implementation lives in domains/auth/login.go.
- Admin login implementation lives in domains/auth/admin_login.go.
