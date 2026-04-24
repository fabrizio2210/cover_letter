# Auth Feature Specification

This file owns frontend behavior for authentication screens and auth entry routing.

## Scope
- `login.component.ts`
- `login.component.html`
- Route: `/login`

## Responsibilities
- Render the password-only login form.
- Call `POST /api/login` with `{ password }`.
- Persist the returned JWT through `AuthService`.
- Redirect successful login to `/dashboard`.
- Render login errors inline on the page.

## Dependencies
- `src/js/coverletter-frontend/src/app/core/auth/auth.service.ts`
- `src/js/coverletter-frontend/src/app/core/auth/auth.interceptor.ts`
- `src/js/coverletter-frontend/src/app/core/auth/auth.guard.ts`

## UX Rules
- Login remains a standalone public route.
- No dashboard shell should render on `/login`.
- OTP is out of scope until explicitly implemented.
