# API Facade Specification

This file defines the facade layer used to keep route wiring centralized.

> Parent index: ../SPEC.md

## Purpose

- Keep route wiring in main.go bound to a single import path.
- Re-export handlers from domain slices.
- Keep route wiring stable while domain packages evolve.

## Rules

- The facade must not contain business logic.
- Each exported symbol maps 1:1 to an implementation function.
- New endpoints must be added in the owning domain first, then re-exported here.
- Exports should point directly to canonical domain implementations.

## Current Ownership Split

- Auth endpoints are sourced from domains/auth.
- Identities endpoints are sourced from domains/identities.
- Fields endpoints are sourced from domains/fields.
- Companies endpoints are sourced from domains/companies.
- Recipients endpoints are sourced from domains/recipients.
- Cover letters endpoints are sourced from domains/coverletters.
- Jobs endpoints are sourced from domains/jobs.
- Crawls and scoring-progress endpoints are sourced from domains/crawls.
