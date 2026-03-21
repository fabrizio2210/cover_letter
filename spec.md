# Cover Letter

This is a web application which allows users, currently just one, to manage the life cycle of a cover letter for a job application and to evaluate job opportunities before applying.
The idea is to discover relevant openings, rank them against user preferences, and send highly customised cover letters to potential employers by leveraging LLM (i.e. Gemini).

This document describes product and architecture intent at a high level.
Implementation contracts are defined in:
- [Backend API Specification](src/go/cmd/api/SPEC.md)
- [Frontend Specification](src/js/coverletter-frontend/src/app/SPEC.md)
- [AI Querier Specification](src/python/ai_querier/SPEC.md)

## Structure of the application

It will be the usual 3 tier architecture on Docker, deployed on an on-premise infrastructure composed of Raspberry Pis (ARM) running Docker Swarm. Traefik is used as a reverse proxy to handle traffic and provide TLS certificates.

The stack consists of:
- a frontend in Angular served from one or multiple Docker containers.
- a backend in Golang exposing HTTP API with GinTonic, served from one or multiple Docker containers.
- batch workers in Python and Golang to asynchronously process data, in Docker containers.
- a MongoDB database to store the data, still in containerized form.
- a Redis instance to queue cover-letter jobs, job-scoring jobs, emails, and temporary codes.

### Workflow

#### Scraping job descriptions and company info

The primary acquisition flow is now job discovery rather than recipient-email discovery. An async crawler will query common hiring platforms such as Ashby, Greenhouse, Lever, and 4dayweek.io, which typically expose structured job APIs. The crawler normalizes jobs into a shared internal shape and persists all discovered job descriptions first, together with source metadata and company linkage.

If a scraped job references a company not yet present in the database, the system should create the company automatically and link the job description to it. This keeps the discovery pipeline autonomous while preserving the company-centric data model already used by the application.

The crawler may still coexist with manual data entry for companies and recipients, but job-description discovery becomes the primary way to identify application opportunities.

#### Score and filter job descriptions

After job descriptions are stored, the system asynchronously evaluates them against weighted user preferences defined on the selected identity profile. Preferences can represent requirements such as remote work, heavy coding, or sector fit. The AI does not decide the final ranking directly: for each preference it returns a score from 1 to 5 plus a short rationale, while the overall score is computed deterministically by the application using the stored weights.

The preferred architecture is to store all job descriptions first and score them afterward. This separates scraping from AI latency, preserves raw data for later re-scoring, and allows the user to change preferences without having to crawl the sources again.

The `ai_querier` service is reused for this scoring flow. It remains the Gemini-facing worker for both cover-letter generation/refinement and job-preference scoring.

#### Prepare Cover Letters

After a job description has been reviewed and ranked as interesting, the system can prepare a cover letter using the candidate identity, the company context, and the job description itself. Recipient/contact management can still exist as a later step for delivery, but the application process now starts from the job description rather than from a known email address.
See [AI Querier Specification](src/python/ai_querier/SPEC.md) for generation flow, prompt construction, and persistence behavior.

#### Refine Cover Letters

The user can review and refine cover letters from the web application. Refinement can be manual or AI-assisted through follow-up prompts. Conversation context is preserved across refinements using stored history and conversation identifiers.
See [AI Querier Specification](src/python/ai_querier/SPEC.md) for refinement behavior.

#### Send Cover Letters

Once the user is satisfied with a cover letter, they can queue it for delivery to the selected recipient. The backend prepares the final HTML email body and enqueues it for asynchronous sending via SMTP.
See [Backend API Specification](src/go/cmd/api/SPEC.md) for queue contract details.

### MVP and experiment

Initially the application was developed with a Telegram bot to avoid dealing with frontend display. Now, we want to implement a full web frontend.

## Structure of the data

The database is currently named `cover_letter`, but will be in the future named after the user in order to have separated instances.
The DB contains at least these collections:
- `fields`
- `companies`
- `identities`
- `recipients`
- `job-descriptions`
- `job-preference-scores`
- `cover-letters`
- `crawls`
- `settings`

Exact model fields, API payload keys, and JSON/BSON mapping are defined in [Backend API Specification](src/go/cmd/api/SPEC.md).

Companies and identities belong to a field/sector in order to associate companies, job descriptions, and recipients with a specific identity of the user. This keeps applications tailored to each professional domain. For example, a person can work in both charity and fashion. Keeping identities distinct allows us to describe strengths and job preferences for each field more precisely.

Identities also store weighted job preferences. These preferences are used by the AI scoring flow to evaluate whether a job description matches the kind of work the user wants.

Collections are linked through document IDs:
- recipients link to companies;
- job descriptions link to companies;
- job-preference scores link to job descriptions and identities;
- cover letters link to recipients;
- companies and identities link to fields.

### Redis queues

Redis is used for:
- cover-letter generation and refinement jobs;
- job-description scoring jobs;
- queued outgoing emails;
- temporary OTP code storage.

Queue payload contracts are defined in [Backend API Specification](src/go/cmd/api/SPEC.md) and [AI Querier Specification](src/python/ai_querier/SPEC.md).

## Frontend 

It is an Angular application styled with Tailwind CSS, using JWT tokens for authentication. By using JWT, we can have multiple backends.

Implemented capabilities:
- dashboard navigation and authenticated access;
- CRUD management for recipients, companies, fields, and identities;
- cover letter listing, detail editing, refinement requests, and send actions;
- feedback toasts for asynchronous operations.

Future features (not yet implemented):
- jobs dashboard with ranking and filters;
- identity preference editing for job scoring;
- split-pane Markdown editor with live preview;
- real-time cover-letter lifecycle notifications;
- crawler tasks and settings UI;
- skeleton loaders and richer sorting/filtering.

Routes, models, API usage, and frontend auth behavior are defined in [Frontend Specification](src/js/coverletter-frontend/src/app/SPEC.md).

## Life cycle of a cover letter

A cover letter starts from a system prompt by using the information about the target job description, the company offering it, the recipient/contact when available, and the identity of the candidate.
The cover letter can be refined by the user by manual edits or further prompts.
If vetted by the user, the cover letter will be sent via email.

## Authentication

Current state:
- password-based login for the web app;
- JWT-based authenticated API access.

Future state:
- OTP login via email for allowed addresses;
- temporary OTP codes with expiration stored in Redis;
- OTP emails sent asynchronously through the email queue.

See [Backend API Specification](src/go/cmd/api/SPEC.md) and [Frontend Specification](src/js/coverletter-frontend/src/app/SPEC.md) for implementation details.