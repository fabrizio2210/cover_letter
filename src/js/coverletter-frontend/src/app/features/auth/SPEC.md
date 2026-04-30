# Auth Feature Specification

This file owns frontend behavior for authentication screens and auth entry routing.

## Scope
- `login.component.ts`
- `login.component.html`
- `admin-login.component.ts` (if present)
- Route: `/login`
- Route: `/admin/login`

## Responsibilities
- Render user-password login form for user, while password-only login form for admin flow.
- Call `POST /api/login` with `{ password }` for user login.
- Call `POST /api/admin/login` with `{ password }` for admin login.
- Persist user/admin JWTs through `AuthService` with separate scopes.
- Redirect successful user login to `/dashboard`.
- Redirect successful admin login to admin-only UI surface.
- Render login errors inline on the page.

## Dependencies
- `src/js/coverletter-frontend/src/app/core/auth/auth.service.ts`
- `src/js/coverletter-frontend/src/app/core/auth/auth.interceptor.ts`
- `src/js/coverletter-frontend/src/app/core/auth/auth.guard.ts`

## UX Rules
- Login remains a standalone public route.
- No dashboard shell should render on `/login`.
- No user dashboard shell should render on `/admin/login`.
- OTP is out of scope until explicitly implemented.
