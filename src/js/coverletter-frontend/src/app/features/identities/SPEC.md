# Identities Feature Specification

This file owns frontend behavior for identity management and preference editing.

## Scope
- `identities.component.ts/html/css`
- Route: `/dashboard/identities`

## Responsibilities
- Render identity cards and edit/create/delete flows.
- Manage discovery roles, preferences, field association, and HTML signature.
- Keep identity editing self-contained to this feature.

## Dependencies
- `src/js/coverletter-frontend/src/app/core/services/feedback.service.ts`
- Shared interfaces in `src/js/coverletter-frontend/src/app/shared/models/models.ts`

## UX Rules
- Roles define crawler discovery scope.
- Preferences define scoring behavior.
- Identity-specific editing should not leak into unrelated routes.
