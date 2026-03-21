# Cover Letter

This is a web application which allows users, currently just one, to manage the life cycle of a cover letter for a job application.
The idea is to send highly customised cover letters to potential employers by leveraging LLM (i.e. Gemini).

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
- a Redis instance to queue emails (OTP and cover letters) and store temporary codes.

### Workflow

#### Scraping recipients and their info

The user can manually insert recipients and companies. Additionally, an async web crawler will scrape emails and information about the recipients and generally of the companies which the recipients belong to. The web crawler uses Google Search to find targets and directly populates the `companies` and `recipients` lists. The user can review and remove irrelevant results later. A dedicated "Crawler Task" interface allows the user to define search parameters; the location can be left empty to rely on IP-based localization.
The web crawler runs regularly (e.g., once a day) with different patterns every time. It operates slowly to remain stealthy and avoid detection.

#### Prepare Cover Letters

After having a base of recipients and their information, asynchronously the system prepares a cover letter for each recipient. The cover letter is generated with Gemini by combining context about the recipient, their company, and the candidate identity.
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
The DB contains 7 collections:
- `fields`
- `companies`
- `identities`
- `recipients`
- `cover-letters`
- `crawls`
- `settings`

Exact model fields, API payload keys, and JSON/BSON mapping are defined in [Backend API Specification](src/go/cmd/api/SPEC.md).

Companies and identities belong to a field/sector in order to associate companies, and respective recipients, with a specific identity of the user. This keeps applications tailored to each professional domain. For example, a person can work in both charity and fashion. Keeping identities distinct allows us to describe strengths for each field more precisely.

Collections are linked through document IDs:
- recipients link to companies;
- cover letters link to recipients;
- companies and identities link to fields.

### Redis queues

Redis is used for:
- cover-letter generation and refinement jobs;
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
- split-pane Markdown editor with live preview;
- real-time cover-letter lifecycle notifications;
- crawler tasks and settings UI;
- skeleton loaders and richer sorting/filtering.

Routes, models, API usage, and frontend auth behavior are defined in [Frontend Specification](src/js/coverletter-frontend/src/app/SPEC.md).

## Life cycle of a cover letter

A cover letter starts from a system prompt by using the information about the recipient (e.g. they are a photographer), the company they work for and the identity of the candidate.
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