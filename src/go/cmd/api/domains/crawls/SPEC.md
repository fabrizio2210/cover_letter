# Crawls Domain Specification

This file is the authoritative reference for crawl/scoring progress handlers under this domain slice.

> Parent index: ../../SPEC.md

## Scope

Owned endpoints:
- POST /api/crawls
- GET /api/crawls/active
- GET /api/crawls/activity-summary
- GET /api/crawls/last-run/workflow-stats
- GET /api/crawls/workflow-cumulative-jobs
- GET /api/crawls/stream
- GET /api/scoring/active
- GET /api/scoring/stream

## Runtime Contract

Current migration state:
- Canonical behavior is implemented in domains/crawls/handlers.go.

Behavior highlights:
- Trigger crawl validates identity existence and requires at least one configured role.
- Duplicate crawl starts for the same identity are rejected when an active queued/running crawl exists.
- Crawl/scoring stream endpoints emit SSE events named crawl-progress and scoring-progress.
- Last-run workflow stats and cumulative counters preserve stable dashboard workflow ordering.
- Activity summary endpoint exposes real-time global queue depths and identity-scoped active workflows for parallel work tracking.

