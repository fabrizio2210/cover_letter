# Cover Letters Feature Specification

This file owns frontend behavior for cover letter listing and editing.

## Scope
- `coverletters-list.component.ts/html/css`
- `letter-editor.component.ts/html/css`
- Routes:
  - `/dashboard/cover-letters`
  - `/dashboard/letter-editor/:id`

## Responsibilities
- Render cover letter list state and empty state.
- Load one cover letter by id for editing.
- Support manual save, refine, send, and delete actions.
- Keep route alias compatibility while editor remains the main detail surface.

## Dependencies
- `src/js/coverletter-frontend/src/app/core/services/feedback.service.ts`
- Shared interfaces in `src/js/coverletter-frontend/src/app/shared/models/models.ts`

## UX Rules
- `/dashboard/cover-letters` remains available as a list entry point.
- `/dashboard/letter-editor/:id` is the canonical editor/detail route.
- Editor interactions must give clear feedback for save/refine/send/delete outcomes.
