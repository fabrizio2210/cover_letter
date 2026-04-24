# Recipients Feature Specification

This file owns frontend behavior for recipient management.

## Scope
- `recipients.component.ts/html/css`
- Route: `/dashboard/recipients`

## Responsibilities
- Render recipient list and detail context.
- Handle recipient CRUD and company association updates.
- Generate cover letters from recipients when supported by the backend.
- Reuse the shared selected identity context when needed for related actions.

## Dependencies
- `src/js/coverletter-frontend/src/app/core/services/api.service.ts`
- `src/js/coverletter-frontend/src/app/core/services/feedback.service.ts`
- `src/js/coverletter-frontend/src/app/core/services/identity-context.service.ts`

## UX Rules
- Recipient management stays focused on recipients, not general company browsing.
- Empty states should guide the user toward creating the first recipient.
