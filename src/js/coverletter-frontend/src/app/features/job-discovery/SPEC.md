# Job Discovery Feature Specification

This file owns frontend behavior for job discovery, ranking, crawl triggering, and progress display.

## Scope
- `job-discovery.component.ts/html/css`
- Route: `/dashboard/job-discovery`

## Responsibilities
- Render ranked job feed and search/filter controls.
- Maintain selected identity context for crawl and scoring views.
- Start crawls with `POST /api/crawls`.
- Consume active crawl/scoring snapshots and live SSE updates.
- Join jobs with `job-preference-scores` client-side.

## Dependencies
- `src/js/coverletter-frontend/src/app/core/services/api.service.ts`
- `src/js/coverletter-frontend/src/app/core/services/feedback.service.ts`
- `src/js/coverletter-frontend/src/app/core/services/identity-context.service.ts`
- `src/js/coverletter-frontend/src/app/shared/utils/workflow-utils.ts`

## UX Rules
- Crawl controls are identity-scoped.
- When crawl and scoring are both active, crawl progress has precedence in shared progress UI.
- Job Discovery is the primary route for manual crawl triggering.
