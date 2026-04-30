# Settings Feature Specification

This file owns frontend behavior for user-scoped settings screens.

## Scope
- `settings.component.ts`
- Route: `/dashboard/settings`

## Responsibilities
- Render the settings shell.
- Host user-specific settings under the settings route.
- Keep user settings isolated from admin-only global configuration.

## Dependencies
- `src/js/coverletter-frontend/src/app/core/services/feedback.service.ts`
- Shared interfaces in `src/js/coverletter-frontend/src/app/shared/models/models.ts`

## UX Rules
- `/dashboard/settings` is the primary route for user settings.
- Global fields management is admin-only and out of scope for this feature slice.
