# Settings Feature Specification

This file owns frontend behavior for shared configuration screens, currently fields management.

## Scope
- `settings.component.ts`
- `fields-list.component.ts/css`
- Route: `/dashboard/settings`

## Responsibilities
- Render the settings shell.
- Host field CRUD under the settings route.
- Keep field editing isolated from unrelated feature routes.

## Dependencies
- `src/js/coverletter-frontend/src/app/core/services/feedback.service.ts`
- Shared interfaces in `src/js/coverletter-frontend/src/app/shared/models/models.ts`

## UX Rules
- `/dashboard/settings` is the only primary route for field management.
- The settings shell owns composition; the field list owns CRUD details.
