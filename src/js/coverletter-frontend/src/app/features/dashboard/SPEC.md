# Dashboard Feature Specification

This file owns frontend behavior for the authenticated dashboard shell and overview experience.

## Scope
- `dashboard.component.ts/html/css`
- `dashboard-overview.component.ts/html/css`
- Route: `/dashboard`

## Responsibilities
- Render the authenticated shell: sidebar, top bar, toast outlet, child router outlet.
- Persist sidebar collapsed state locally.
- Subscribe to `FeedbackService` and render shared toast feedback.
- Render overview cards, workflow stats, cumulative workflow counters, and top-scored opportunities.

## Dependencies
- `src/js/coverletter-frontend/src/app/core/services/api.service.ts`
- `src/js/coverletter-frontend/src/app/core/services/feedback.service.ts`
- `src/js/coverletter-frontend/src/app/shared/utils/workflow-utils.ts`

## UX Rules
- `/dashboard` is the authenticated landing page.
- Sidebar navigation is the authoritative navigation surface for feature routes.
- Workflow visibility widgets are read-only summaries; crawl triggering lives in Job Discovery.
