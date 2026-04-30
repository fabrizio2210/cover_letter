# Cover Letter

This is a web application which allows multiple users to manage the life cycle of cover letters for job applications and evaluate job opportunities before applying.
The idea is to discover relevant openings, rank them against user preferences, and send highly customised cover letters to potential employers by leveraging LLM services (Gemini for cover letters, Ollama for local job scoring).

This document describes product and architecture intent at a high level.
Implementation contracts are defined in:
- [Backend API Specification](src/go/cmd/api/SPEC.md)
- [Frontend Specification](src/js/coverletter-frontend/src/app/SPEC.md)
- [AI Querier Specification](src/python/ai_querier/SPEC.md)
- [AI Scorer Specification](src/python/ai_scorer/SPEC.md)
- [Web Crawler Specification](src/python/web_crawler/SPEC.md)

## Structure of the application

It will be the usual 3 tier architecture on Docker, deployed on an on-premise infrastructure composed of Raspberry Pis (ARM) running Docker Swarm. Traefik is used as a reverse proxy to handle traffic and provide TLS certificates.

The stack consists of:
- a frontend in Angular served from one or multiple Docker containers.
- a backend in Golang exposing HTTP API with GinTonic, served from one or multiple Docker containers.
- batch workers in Python and Golang to asynchronously process data, in Docker containers.
- a MongoDB database to store the data, still in containerized form.
- a Redis instance to queue cover-letter jobs, crawler-trigger jobs, job-scoring jobs, crawler progress events, emails, and temporary codes.

### Workflow

#### Scraping job descriptions and company info

The primary acquisition flow is now job discovery rather than recipient-email discovery. An async crawler collects openings from supported discovery sources, normalizes jobs into a shared internal shape, and persists discovered job descriptions first, together with source metadata and company linkage.

The Job Discovery tab can trigger a crawl for an arbitrary selected identity. The frontend sends the trigger to the Go API, the API enqueues a crawl request on Redis, and the `web_crawler` worker consumes that request asynchronously.

Each crawl execution is scoped by an explicit `identity_id`. Runs without `identity_id` are invalid.

Only one active crawl per identity is allowed at a time. A second trigger for the same identity is rejected until the active crawl reaches a terminal state.

Crawler internals, including discovery inputs, source adapters, and workflow design, are intentionally specified only in [Web Crawler Specification](src/python/web_crawler/SPEC.md).

At this product level, the crawler behavior is defined as an asynchronous, identity-scoped process that persists partial and final results to MongoDB during execution.

While a crawl is running, the crawler emits progress updates via Redis. The backend relays the latest crawl progress to the frontend so it can be shown live on both the Dashboard and Job Discovery views.

The scoring worker also emits independent progress updates through Redis while AI scoring is processing queued jobs. Scoring progress is a separate process lifecycle and starts at `0%` for each scoring run. In shared UI widgets that can show either crawl or scoring progress, crawl progress has precedence whenever both streams are active for the same identity.

If a scraped job references a company not yet present in the database, the system should create the company automatically and link the job description to it. This keeps the discovery pipeline autonomous while preserving the company-centric data model already used by the application.

The crawler may still coexist with manual data entry for companies and recipients, but job-description discovery becomes the primary way to identify application opportunities.

#### Score and filter job descriptions

After job descriptions are stored, the system asynchronously evaluates them against weighted user preferences defined on the selected identity profile. Preferences can represent requirements such as remote work, heavy coding, or sector fit. The AI does not decide the final ranking directly: for each preference it returns only a score from 1 to 5, while the overall score is computed deterministically by the application using the stored weights.

Separation of concerns:
- `roles` define discovery scope for crawler queries.
- `preferences` define scoring criteria and weights.

ATS extraction guardrail:
- if an identity has an empty `roles` list, `crawler_ats_job_extraction` emits no jobs for that execution.

The preferred architecture is to store all job descriptions first and score them afterward. This separates scraping from AI latency, preserves raw data for later re-scoring, and allows the user to change preferences without having to crawl the sources again.

If a job cannot resolve required scoring prerequisites (for example company-field-identity linkage), scoring is skipped for that job rather than failing the full crawl.

Re-crawled jobs are always re-enqueued for scoring when scoring enqueue is enabled.

The scoring flow is handled by a dedicated `ai_scorer` worker service. `ai_scorer` consumes job-scoring queue messages and evaluates job/preference fit using a local model exposed by an internal Ollama service in the Docker network. In the dev stack, Ollama can run with multiple replicas, and `ai_scorer` controls the number of parallel job workers through `AI_SCORER_OLLAMA_PARALLELISM` (typically aligned with replica count). Each scorer worker maintains its own Ollama client connection path so traffic is balanced across replicas at the network/TCP layer. The `ai_querier` service remains dedicated to cover-letter generation and refinement through Gemini.

When crawl progress or scoring progress reaches a terminal state, the Job Discovery view refreshes the job list automatically so newly discovered jobs and updated scoring fields are visible without a manual page reload.

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

The persistence model is split between one global database and one database per user.

Global database (fixed name): `cover_letter_global`
- `jobs`
- `companies`
- `fields`
- `global_settings`
- `stats`

Per-user database (name: `cover_letter_<sub>`, where `sub` is the user UUID from JWT):
- `cover-letters`
- `identities`
- `job-preference-scores`
- `recipients`
- `user_settings`
- `crawls`

Exact model fields, API payload keys, and JSON/BSON mapping are defined in [Backend API Specification](src/go/cmd/api/SPEC.md).

Cross-service persistence directive:
- when reading from or writing to MongoDB for entities covered by the shared schema, services and workers must use the models defined in `src/go/internal/proto/common/common.proto` as the canonical contract;
- ad hoc BSON/JSON shapes for canonical entities should be avoided because they can drift from the shared schema and break silently when the model evolves;
- service-local raw BSON fields are allowed only for explicitly documented non-proto extensions, and those exceptions must be recorded in the relevant service specification.

Companies and identities belong to a field/sector in order to associate companies, job descriptions, and recipients with a specific identity of the user. This keeps applications tailored to each professional domain. For example, a person can work in both charity and fashion. Keeping identities distinct allows us to describe strengths and job preferences for each field more precisely.

Identities also store weighted job preferences. These preferences are used by the AI scoring flow to evaluate whether a job description matches the kind of work the user wants.

Collections are linked through document IDs:
- recipients link to companies;
- job descriptions link to companies;
- job-preference scores link to job descriptions and identities, with one `job-preference-scores` document per `(job_id, identity_id)` and embedded per-preference results;
- cover letters link to recipients;
- companies and identities link to fields.

Queue message contract:
- user-scoped asynchronous payloads include `user_id` (equal to JWT `sub`);
- workers derive per-user DB name from `user_id` at runtime and must not accept user-controlled DB overrides.

Crawler-enriched company metadata may also include ATS linkage fields (`ats_provider`, `ats_slug`) used to drive ATS job extraction.

### Redis queues

Redis is used for:
- cover-letter generation and refinement jobs;
- crawler-trigger jobs;
- crawler progress events used to update backend push streams;
- job-description scoring jobs;
- queued outgoing emails;
- temporary OTP code storage.

Queue payload contracts are defined in [Backend API Specification](src/go/cmd/api/SPEC.md), [AI Querier Specification](src/python/ai_querier/SPEC.md), and [AI Scorer Specification](src/python/ai_scorer/SPEC.md).

## Frontend

The frontend is branded **"The Curator"** (subtitle: "AI Job Strategist"). It is an Angular application styled with Tailwind CSS using a Material Design 3 token palette, JWT tokens for authentication, and Material Symbols Outlined icons throughout.

### Navigation structure

Five primary routes are accessible from the persistent sidebar, plus a Settings route reachable via the sidebar footer link:

| Sidebar label | Route | Purpose |
|---|---|---|
| Dashboard | `/dashboard` | Overview stats and top-scored job feed |
| Job Discovery | `/dashboard/job-discovery` | Ranked job feed, filter bar, crawler-status widget, per-identity discovery settings |
| Letter Editor | `/dashboard/letter-editor/:id` | Split-pane: markdown editor (left) + AI Refiner chat (right) |
| Identities | `/dashboard/identities` | Bento-grid identity cards with discovery-scope tags, quick stats, and preference weight bars; global curator preferences |
| Recipients | `/dashboard/recipients` | Recipients list and management |
| Settings | `/dashboard/settings` | User-specific settings management |

Default redirect: `/dashboard` renders the overview directly (no longer redirects to recipients).

### Implemented capabilities
- authenticated dashboard access with stats overview and top-scored job cards;
- CRUD management for companies (via Job discovery tab), recipients (via Recipients tab), and identities;
- admin-only fields CRUD management through `/api/admin/fields`;
- cover letter listing, split-pane editor, AI refinement requests, and send actions;
- feedback toasts for asynchronous operations.

### Target features (UX-specified, not yet built)
- Job Discovery page: ranked feed, filter chips, Re-Rank trigger, identity selector, manual crawl trigger, crawler-status widget with progress bar and live phase updates, per-identity discovery settings panel; company details and open positions are shown when a job is selected;
- Identity preference editing: weight bars per preference, "Add Preference" action, Global Curator Preferences section (writing tone, discovery interval, AI creativity);
- Split-pane Letter Editor with rich-text toolbar and AI Refiner chat panel (conversation history, Apply Change / Undo);
- Dashboard overview with stat cards (Active Applications, Total Jobs Scraped, Top AI-Scored Jobs, Sent Letters), scrollable Top Scored Opportunities feed, live crawler progress for the currently active identity run, and a last-completed-run workflow stats widget that shows discovered jobs and discovered companies for each `crawler_` workflow;
- Recipients page refinements (sorting/filtering and lifecycle actions);
- Settings page hosting user-specific settings (not global fields management).

Dashboard workflow-visibility rules:
- The workflow stats widget shows the last completed result for each workflow independently; each workflow card reflects its own most recent completion regardless of which parent run it belonged to.
- Only `crawler_` workflows are included in this widget; `enrichment_` workflows are excluded.
- For each included workflow, the dashboard shows:
	- `discovered_jobs`
	- `discovered_companies`
- `discovered_*` values represent persisted results only (`inserted + updated`), not raw pre-filter discovery candidates.
- If no workflow has ever completed, the widget shows an empty state.

### Future features (no mock-up yet)
- OTP-based login flow;
- real-time cover-letter lifecycle notifications;
- full crawler task administration UI;
- skeleton loaders;
- dedicated logout action in the sidebar;
- dark mode.

Routes, models, API usage, and frontend auth behavior are defined in [Frontend Specification](src/js/coverletter-frontend/src/app/SPEC.md).

## Life cycle of a cover letter

A cover letter starts from a system prompt by using the information about the target job description, the company offering it, the recipient/contact when available, and the identity of the candidate.
The cover letter can be refined by the user by manual edits or further prompts.
If vetted by the user, the cover letter will be sent via email.

## Authentication

Current state:
- password-based login for user sessions;
- admin login flow for admin-only routes;
- JWT-based authenticated API access for both flows.

JWT contract:
- user JWT includes `sub` and `exp` and is verified with `JWT_SECRET`;
- admin JWT includes `sub`, `role: "admin"`, and `exp` and is verified with `ADMIN_JWT_SECRET`;
- per-user DB scope derives from `sub`.

AI implementation testing can be performed from http://localhost/dashboard; use password "password" at login.

Future state:
- OTP login via email for allowed addresses;
- temporary OTP codes with expiration stored in Redis;
- OTP emails sent asynchronously through the email queue.

See [Backend API Specification](src/go/cmd/api/SPEC.md) and [Frontend Specification](src/js/coverletter-frontend/src/app/SPEC.md) for implementation details.