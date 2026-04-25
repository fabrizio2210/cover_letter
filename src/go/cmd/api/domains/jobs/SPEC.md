# Jobs Domain Specification

This file is the authoritative reference for job handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- GET /api/job-descriptions
- GET /api/job-descriptions/stream
- GET /api/job-descriptions/:id
- GET /api/job-preference-scores
- POST /api/job-descriptions
- PUT /api/job-descriptions/:id
- DELETE /api/job-descriptions/:id
- POST /api/job-descriptions/:id/score
- POST /api/job-descriptions/:id/check

## Runtime Contract

Current migration state:
- Canonical behavior is implemented in domains/jobs/handlers.go.

Behavior highlights:
- Job and score payloads normalize ObjectID fields to string IDs.
- `GET /api/job-preference-scores` validates optional `job_id` and `identity_id` query params.
- Stream endpoint emits SSE events named `job-update`.
- Check endpoint enqueues retire-job events to the retiring-jobs queue.

