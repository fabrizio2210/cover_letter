# Frontend Specification

**This file is the root contract index for the Angular frontend.**
Agents editing any component, service, model, or route MUST consult this file before making changes.
It owns shared contracts: API payload shapes, canonical TypeScript interfaces, routing, auth behavior, and cross-feature conventions.
Route-specific screen behavior is delegated to the per-feature `SPEC.md` files under `features/`.

> Source of truth: `src/js/coverletter-frontend/src/app/` — backend contract is in `src/go/cmd/api/SPEC.md`.

---

## 1. Tech Stack & Build

| Component | Detail |
|---|---|
| Language | TypeScript |
| Framework | Angular (standalone components, no NgModules) |
| HTTP | `HttpClient` (`@angular/common/http`) |
| Styling | Tailwind CSS (global) + component-scoped CSS files |
| Auth | JWT stored in `localStorage` under key `token` |
| Dev port | `:4200` (Angular CLI default) |
| API base | All calls use relative path `/api` — no environment config |

The frontend is served behind an Nginx reverse proxy that routes `/api/*` to the Go backend. The proxy config is in `docker/x86_64/nginx.conf`.

### Feature Contract Ownership

Feature-local behavior and design intent are delegated to:

- `src/js/coverletter-frontend/src/app/features/auth/SPEC.md`
- `src/js/coverletter-frontend/src/app/features/dashboard/SPEC.md`
- `src/js/coverletter-frontend/src/app/features/job-discovery/SPEC.md`
- `src/js/coverletter-frontend/src/app/features/identities/SPEC.md`
- `src/js/coverletter-frontend/src/app/features/recipients/SPEC.md`
- `src/js/coverletter-frontend/src/app/features/cover-letters/SPEC.md`
- `src/js/coverletter-frontend/src/app/features/settings/SPEC.md`

Folder ownership:
- `core/` contains cross-cutting infrastructure such as auth and app-wide services.
- `shared/` contains canonical frontend models and reusable utilities.
- `features/` contains route-facing components and per-feature behavioral specs.

---

## 2. Routes

| Path | Component | Guard | Notes |
|---|---|---|---|
| `/` | — | Redirects to `/login` | |
| `/login` | `LoginComponent` | None | |
| `/dashboard` | `DashboardComponent` | `authGuard` | Overview: stats cards + top-scored job feed |
| `/dashboard/job-discovery` | `JobDiscoveryComponent` | Inherited | Ranked job feed, filter bar, identity-scoped crawl controls, crawler panel |
| `/dashboard/letter-editor/:id` | `LetterEditorComponent` | Inherited | Split-pane editor + AI Refiner chat |
| `/dashboard/identities` | `IdentitiesComponent` | Inherited | Bento-grid cards, preference weights, global prefs |
| `/dashboard/recipients` | `RecipientsComponent` | Inherited | Recipients list and lifecycle actions |
| `/dashboard/settings` | `SettingsComponent` | Inherited | Fields CRUD; linked from sidebar Settings link |

Default redirect: `/dashboard` renders the overview page directly — no child redirect.

Removed from primary nav (routes retired):
- `/dashboard/companies` — companies listing was removed from primary nav; recipients are managed at `/dashboard/recipients`.
- `/dashboard/fields` — fields are now managed under `/dashboard/settings`.
- `/dashboard/jobs` — superseded by `/dashboard/job-discovery`.
- `/dashboard/cover-letters` and `/dashboard/cover-letters/:id` — superseded by `/dashboard/letter-editor/:id`.

---

## 3. Canonical TypeScript Models

Define these interfaces once in `app/shared/models/models.ts` and import everywhere. Do not re-declare them per component.

```typescript
export interface Field {
  id: string;
  field: string;
}

export interface Company {
  id: string;
  name: string;
  description?: string;
  field_id?: string;
  field_info?: Field;
}

export interface Recipient {
  id: string;
  email: string;
  name?: string;
  description?: string;
  company_id?: string;
  company_info?: Company;
}

export interface Identity {
  id: string;
  identity: string;
  name?: string;
  description?: string;
  field_id?: string;
  field_info?: Field;
  html_signature?: string;
  roles?: string[];
  preferences?: IdentityPreference[];
}

export interface IdentityPreference {
  key: string;
  guidance?: string;
  weight: number;
  enabled: boolean;
}

export interface HistoryPart {
  text: string;
}

export interface HistoryEntry {
  role: 'user' | 'model';
  parts: HistoryPart[];
}

export interface Timestamp {
  seconds: number;
  nanos: number;
}

export interface CoverLetter {
  id: string;
  recipient_id: string;
  cover_letter?: string;
  prompt?: string;
  status?: string;
  conversation_id?: string;
  history?: HistoryEntry[];
  created_at?: string | Timestamp;
  updated_at?: string | Timestamp;
  recipient_info?: Recipient;
}

export interface JobPreferenceScore {
  id: string;
  job_id: string;
  identity_id: string;
  preference_scores: PreferenceScore[];
  scoring_status?: string;
  weighted_score?: number;
  max_score?: number;
}

export interface PreferenceScore {
  preference_key: string;
  preference_guidance?: string;
  preference_weight?: number;
  score: number;
  scored_at?: string | Timestamp;
}

export interface JobDescription {
  id: string;
  company_id?: string;
  company_info?: Company;
  title: string;
  description?: string;
  location?: string;
  platform?: string;
  external_job_id?: string;
  source_url?: string;
  created_at?: string | Timestamp;
  updated_at?: string | Timestamp;
}

export interface CrawlProgress {
  run_id: string;
  workflow_run_id?: string;
  workflow_id?:
    | 'crawler_company_discovery'
    | 'enrichment_ats_enrichment'
    | 'crawler_ats_job_extraction'
    | 'crawler_4dayweek'
    | 'crawler_levelsfyi';
  identity_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'rejected';
  workflow:
    | 'queued'
    | 'crawler_company_discovery'
    | 'enrichment_ats_enrichment'
    | 'crawler_ats_job_extraction'
    | 'crawler_4dayweek'
    | 'crawler_levelsfyi'
    | 'finalizing';
  message?: string;
  estimated_total: number;
  completed: number;
  percent: number;
  started_at?: string | Timestamp | null;
  updated_at?: string | Timestamp;
  finished_at?: string | Timestamp | null;
  reason?: string;
}

export interface ScoringProgress {
  run_id: string;
  identity_id: string;
  status: 'running' | 'completed' | 'failed';
  message?: string;
  estimated_total: number;
  completed: number;
  percent: number;
  started_at?: string | Timestamp | null;
  updated_at?: string | Timestamp;
  finished_at?: string | Timestamp | null;
  reason?: string;
}

export interface LastRunWorkflowStatsItem {
  workflow_id:
    | 'crawler_company_discovery'
    | 'crawler_ats_job_extraction'
    | 'crawler_4dayweek'
    | 'crawler_levelsfyi';
  discovered_jobs: number;
  discovered_companies: number;
}

export interface LastRunWorkflowStatsResponse {
  completed_at?: string | Timestamp | null;
  workflows: LastRunWorkflowStatsItem[];
}

export interface WorkflowCumulativeJobsItem {
  workflow_id:
    | 'crawler_company_discovery'
    | 'crawler_ats_job_extraction'
    | 'crawler_4dayweek'
    | 'crawler_levelsfyi';
  discovered_jobs_cumulative: number;
}

export interface WorkflowCumulativeJobsResponse {
  workflows: WorkflowCumulativeJobsItem[];
}

export interface FeedbackMessage {
  message: string;
  isError: boolean;
}
```

Critical alignment rules:
- `field_info` and `company_info` are objects, never arrays.
- `company_id` is the JSON key for a recipient's company reference, except for `PUT /api/recipients/:id/company`, which uses `companyId`.
- `field_id` is the JSON key for company and identity field references, except for `PUT /api/identities/:id/field`, which uses `fieldId`.
- `roles` on an `Identity` is a manually curated string array used for crawler discovery scope.
- `preferences` on an `Identity` is an array of weighted preference descriptors.
- Score data is not embedded on `JobDescription`; the UI joins jobs with `JobPreferenceScore` documents fetched separately.
- `run_id` on a `CrawlProgress` is the parent identity-scoped crawl request id.
- `workflow_run_id` on a `CrawlProgress` identifies one workflow execution attempt and changes on retry.
- `workflow_id` on a `CrawlProgress` is the stable workflow key for workflow-level events.
- `workflow` on a `CrawlProgress` names the currently active workflow or parent-run lifecycle stage (`queued`, `finalizing`).
- `identity_id` on a `CrawlProgress` is required for both parent-run and workflow-level crawl events.
- `percent` on a `CrawlProgress` is an integer from `0` to `100` and is the primary UI progress-bar input.
- `run_id` and `identity_id` on a `ScoringProgress` are required and are stable across stream reconnections for the same active run.
- `percent` on a `ScoringProgress` is an integer from `0` to `100` and each scoring run starts from `0%`.
- `created_at` and `updated_at` may be returned as protobuf-style timestamp objects: `{ seconds, nanos }`.

---

## 4. Auth

### Current state
- `POST /api/login` returns `{ token: string }`, which is stored in `localStorage['token']`.
- Auth is centralized in `app/core/auth/auth.service.ts`.
- `app/core/auth/auth.interceptor.ts` automatically adds `Authorization: Bearer <token>` to non-login API requests.
- On HTTP `401` (non-login requests), the interceptor calls `AuthService.logout()` and redirects to `/login`.
- `/dashboard` is protected by `authGuard` (`app/core/auth/auth.guard.ts`) using `AuthService.isAuthenticated()`.
- There is no visible logout button in the UI yet, but the logout behavior exists in the auth service.
- Login is password-only (`{ password: string }`). OTP-based login described in the project-level spec is not yet implemented.

### Implemented auth API (`AuthService`)

```typescript
getToken(): string | null
setToken(token: string): void
getHeaders(): HttpHeaders
logout(): void
isAuthenticated(): boolean
```

### Remaining auth gaps
- OTP-based login flow is not implemented yet.
- No dedicated logout action in dashboard navigation yet.

---

## 5. Services

### `FeedbackService`

Location: `app/core/services/feedback.service.ts`

```typescript
showFeedback(message: string, isError?: boolean): void
clearFeedback(): void
feedback$: Observable<FeedbackMessage>
```

`DashboardComponent` is the only component that renders toast UI. Child components should emit feedback through the service and not maintain duplicate toast state.

### Crawl and scoring progress consumption

Crawler and scoring progress are consumed from the backend, never directly from Redis.

Required frontend behavior:
- Fetch an initial crawl snapshot from `GET /api/crawls/active` when Dashboard or Job Discovery loads.
- Subscribe to `GET /api/crawls/stream` as a server-sent events stream for live updates.
- Fetch latest completed crawler workflow visibility stats from `GET /api/crawls/last-run/workflow-stats` when Dashboard loads.
- Fetch cumulative discovered-jobs counters from `GET /api/crawls/workflow-cumulative-jobs` when Dashboard loads.
- Fetch an initial scoring snapshot from `GET /api/scoring/active` when Job Discovery loads.
- Subscribe to `GET /api/scoring/stream` as a server-sent events stream for live updates.
- Filter stream events by the currently selected identity in Job Discovery.
- Allow Dashboard to show active progress even when the user is not on Job Discovery.
- Render Dashboard workflow visibility stats as identity-agnostic values from the latest completed run globally, independent of selected identity in Job Discovery.
- Render Dashboard workflow visibility stats as identity-agnostic values; each workflow card reflects its own most recent completion, independent of parent run and selected identity.
- Include only `crawler_` workflow rows/cards in the Dashboard visibility widget and exclude `enrichment_` workflows.
- Display `discovered_jobs` and `discovered_companies` exactly as returned by the API, where values are persisted-result counters (`inserted + updated`).
- Display cumulative stat cards using `discovered_jobs_cumulative` exactly as returned by the API.
- Render an empty state for the Dashboard visibility widget when `run_id` is empty and `workflows` is an empty array.
- Render an empty state for the Dashboard visibility widget when `workflows` is an empty array.
- Treat `completed`, `failed`, and `rejected` as terminal UI states.
- Preserve distinct crawl workflow contributions by `workflow_run_id` rather than collapsing everything into one snapshot per identity.
- Support multiple active crawl workflow items for one `identity_id` under the same parent `run_id`.
- Use `workflow_id` as the stable lookup key for labels, icons, and workflow-specific progress presentation.
- For shared progress widgets, crawl progress has precedence when both crawl and scoring are active for the selected identity.
- On terminal progress state (`completed`, `failed`, or `rejected`), Job Discovery refreshes the jobs list automatically.

---

## 6. API Contract

All protected requests send `Authorization: Bearer <token>`.

### 6.1 Login

| Method | Path | Auth |
|---|---|---|
| POST | `/api/login` | None |

Request:

```json
{ "password": "string" }
```

Response `200`:

```json
{ "token": "string" }
```

Response `401`:

```json
{ "error": "Unauthorized" }
```

### 6.2 Fields

| Method | Path | Request body | Success response |
|---|---|---|---|
| GET | `/api/fields` | — | `Field[]` |
| POST | `/api/fields` | `{ "field": "string" }` | created `Field` |
| PUT | `/api/fields/:id` | `{ "field": "string" }` | `{ "message": "Field updated successfully" }` |
| DELETE | `/api/fields/:id` | — | `{ "message": "Field deleted successfully" }` |

### 6.3 Companies

| Method | Path | Request body | Success response |
|---|---|---|---|
| GET | `/api/companies` | — | `Company[]` with `field_info` embedded |
| POST | `/api/companies` | `{ "name": "string", "description": "string", "field_id": "<hex or omit>" }` | created `Company` |
| PUT | `/api/companies/:id` | `{ "name": "string", "description": "string", "field_id": "<hex>" }` | `{ "message": "Company updated successfully" }` |
| PUT | `/api/companies/:id/field` | `{ "field_id": "<hex or null>" }` | `{ "message": "Field associated successfully", "modifiedCount": 1 }` |
| DELETE | `/api/companies/:id` | — | `{ "message": "Company deleted successfully" }` |

### 6.4 Recipients

| Method | Path | Request body | Success response |
|---|---|---|---|
| GET | `/api/recipients` | — | `Recipient[]` with `company_info` embedded |
| POST | `/api/recipients` | `{ "email": "string", "name": "string", "description": "string", "company_id": "<hex or omit>" }` | created `Recipient` |
| PUT | `/api/recipients/:id/name` | `{ "name": "string" }` | `{ "message": "Recipient name updated successfully" }` |
| PUT | `/api/recipients/:id/description` | `{ "description": "string" }` | `{ "message": "Recipient description updated successfully" }` |
| PUT | `/api/recipients/:id/company` | `{ "companyId": "<hex or null>" }` | `{ "message": "Company associated successfully", "modifiedCount": 1 }` |
| DELETE | `/api/recipients/:id` | — | `{ "message": "Recipient deleted successfully" }` |
| POST | `/api/recipients/:id/generate-cover-letter` | — | `{ "message": "Generation queued successfully" }` |

### 6.5 Identities

| Method | Path | Request body | Success response |
|---|---|---|---|
| GET | `/api/identities` | — | `Identity[]` with `field_info` embedded |
| POST | `/api/identities` | `{ "identity": "string", "name": "string", "description": "string", "field_id": "<hex or empty>", "roles": ["string"], "html_signature": "<html or omit>" }` | created `Identity` |
| PUT | `/api/identities/:id/name` | `{ "name": "string" }` | `{ "message": "Identity updated successfully" }` |
| PUT | `/api/identities/:id/description` | `{ "description": "string" }` | `{ "message": "Identity updated successfully" }` |
| PUT | `/api/identities/:id/field` | `{ "fieldId": "<hex>" }` | `{ "message": "Identity updated successfully" }` |
| PUT | `/api/identities/:id/roles` | `{ "roles": ["string"] }` | `{ "message": "Identity updated successfully" }` |
| PUT | `/api/identities/:id/preferences` | `{ "preferences": IdentityPreference[] }` | `{ "message": "Identity updated successfully" }` |
| PUT | `/api/identities/:id/signature` | `{ "html_signature": "<html string>" }` | `{ "message": "Identity updated successfully" }` |
| DELETE | `/api/identities/:id` | — | `{ "message": "Identity deleted successfully" }` |

Notes:
- `PUT /api/identities/:id/field` uses `fieldId` in camelCase because that is what the backend expects.
- `html_signature` is limited to 64 KiB.

### 6.6 Jobs

| Method | Path | Request body | Success response |
|---|---|---|---|
| GET | `/api/job-descriptions` | — | score-neutral `JobDescription[]` with `company_info` |
| GET | `/api/job-descriptions/:id` | — | single `JobDescription` |
| GET | `/api/job-preference-scores` | — | `JobPreferenceScore[]`; filterable by `job_id` and `identity_id` |
| POST | `/api/job-descriptions` | `{ "company_id": "<hex or omit>", "company_name": "<string or omit>", "title": "string", "description": "string", "location": "string", "platform": "string", "external_job_id": "string", "source_url": "string" }` | created `JobDescription` |
| PUT | `/api/job-descriptions/:id` | `{ "company_id": "<hex or omit>", "title": "string", "description": "string", "location": "string", "platform": "string", "external_job_id": "string", "source_url": "string" }` | `{ "message": "Job description updated successfully" }` |
| POST | `/api/job-descriptions/:id/score` | — | `{ "message": "Scoring queued successfully" }` |
| DELETE | `/api/job-descriptions/:id` | — | `{ "message": "Job description deleted successfully" }` |

Notes:
- The jobs list is the MVP entry point for the new hiring workflow.
- Identity-scoped aggregates such as `weighted_score` are read from `JobPreferenceScore`, not from `JobDescription`.
- Per-preference values shown in the UI come from `JobPreferenceScore`, not from re-running ranking logic in the browser.

### 6.7 Crawl Control And Progress

| Method | Path | Request body | Success response |
|---|---|---|---|
| POST | `/api/crawls` | `{ "identity_id": "<hex>" }` | `{ "message": "Crawl queued successfully", "run_id": "string", "identity_id": "string", "status": "queued" }` |
| GET | `/api/crawls/active` | — | `CrawlProgress[]` |
| GET | `/api/crawls/stream` | — | `text/event-stream` carrying `crawl-progress` events |
| GET | `/api/crawls/last-run/workflow-stats` | — | `LastRunWorkflowStatsResponse` |
| GET | `/api/crawls/workflow-cumulative-jobs` | — | `WorkflowCumulativeJobsResponse` |

Notes:
- Job Discovery is the primary screen that starts crawls.
- The selected identity in Job Discovery is the `identity_id` sent to `POST /api/crawls`.
- The backend rejects a new crawl for an identity that already has an active run with HTTP `409`.
- The stream event payload matches `CrawlProgress` exactly.
- Dashboard and Job Discovery both listen for the same crawl-progress event shape.
- Dashboard workflow visibility stats are loaded from the dedicated `GET /api/crawls/last-run/workflow-stats` endpoint and are intentionally separate from active progress stream payloads.
- Dashboard cumulative workflow stat cards are loaded from `GET /api/crawls/workflow-cumulative-jobs` and reuse the existing stat-card visual style.

### 6.8 Scoring Progress

| Method | Path | Request body | Success response |
|---|---|---|---|
| GET | `/api/scoring/active` | — | `ScoringProgress[]` |
| GET | `/api/scoring/stream` | — | `text/event-stream` carrying `scoring-progress` events |

Notes:
- Job Discovery consumes scoring progress to display AI scoring lifecycle on the shared progress bar.
- Scoring progress is independent from crawling and starts from `0%` for each scoring run.
- When crawl and scoring are both active, Job Discovery renders crawl progress on the shared bar until crawl reaches a terminal state.

### 6.9 Cover Letters

| Method | Path | Request body | Success response |
|---|---|---|---|
| GET | `/api/cover-letters` | — | `CoverLetter[]` with `recipient_info` embedded |
| GET | `/api/cover-letters/:id` | — | single `CoverLetter` with `recipient_info` |
| PUT | `/api/cover-letters/:id` | `{ "content": "string" }` | `{ "message": "Cover letter updated successfully" }` |
| POST | `/api/cover-letters/:id/refine` | `{ "prompt": "string" }` | `{ "message": "Refinement queued successfully" }` |
| POST | `/api/cover-letters/:id/send` | — | `{ "message": "Email queued successfully" }` |
| DELETE | `/api/cover-letters/:id` | — | `{ "message": "Cover letter deleted successfully" }` |

Notes:
- The read key is `cover_letter`; the write key for manual updates is `content`.
- `created_at` and `updated_at` are not ISO strings in the backend contract; they may be plain timestamp objects.

---

## 7. Component Inventory

| Area | Primary route components | Owning spec |
|---|---|---|
| App root | `AppComponent` | this file |
| Auth | `LoginComponent` | `features/auth/SPEC.md` |
| Dashboard | `DashboardComponent`, `DashboardOverviewComponent` | `features/dashboard/SPEC.md` |
| Job Discovery | `JobDiscoveryComponent` | `features/job-discovery/SPEC.md` |
| Identities | `IdentitiesComponent` | `features/identities/SPEC.md` |
| Recipients | `RecipientsComponent` | `features/recipients/SPEC.md` |
| Cover Letters | `CoverLettersListComponent`, `LetterEditorComponent` | `features/cover-letters/SPEC.md` |
| Settings | `SettingsComponent`, `FieldsListComponent` | `features/settings/SPEC.md` |

### Retired components (superseded)
The following components are replaced by the new inventory above and should not be used for new development:
- `RecipientsListComponent` → `RecipientsComponent`
- `CompaniesListComponent` → `RecipientsComponent`
- `IdentitiesListComponent` → `IdentitiesComponent`
- `JobsListComponent` → `JobDiscoveryComponent`
- `CoverLettersListComponent` → `LetterEditorComponent`
- `CoverLettersDetailComponent` → `LetterEditorComponent`

---

## 8. Styling Conventions

### Design system

The UI uses a Material Design 3 token palette mapped to Tailwind CSS custom colors. The canonical token set is defined in the Tailwind config inside the mock-up HTML files and must be mirrored in `tailwind.config.js`.

Key color tokens:

| Token | Light value |
|---|---|
| `surface` | `#faf8ff` |
| `surface-container-lowest` | `#ffffff` |
| `surface-container-low` | `#f2f3ff` |
| `surface-container` | `#eaedff` |
| `surface-container-high` | `#e2e7ff` |
| `surface-container-highest` | `#dae2fd` |
| `on-surface` | `#131b2e` |
| `on-surface-variant` | `#45464d` |
| `primary-container` | `#00174b` |
| `on-primary-container` | `#497cff` |
| `surface-tint` | `#0053db` |
| `tertiary-container` | `#07006c` |
| `on-tertiary-container` | `#7073ff` |
| `error` | `#ba1a1a` |
| `error-container` | `#ffdad6` |
| `background` | `#faf8ff` |

### Typography

| Role | Font family | Usage |
|---|---|---|
| `font-headline` | Manrope | Page titles, card headings, numbers |
| `font-body` | Inter | Body text, labels, inputs |
| `font-label` | Inter | Uppercase tracking labels, captions |

### Icons

Use **Material Symbols Outlined** throughout (`<span class="material-symbols-outlined">`). Do not use the legacy Material Icons font or SVG icon packs.

### Key visual patterns

| Pattern | Implementation |
|---|---|
| Glassmorphism top bar | `bg-white/70 backdrop-blur-xl` |
| Sidebar background | `bg-slate-50` with `border-r border-slate-200/50` |
| Active nav item | `text-blue-600 bg-white shadow-sm rounded-xl` |
| Primary CTA button | `rounded-full` with gradient `from-on-primary-container to-surface-tint` (`#497cff` → `#0053db`) |
| Card surface | `bg-surface-container-lowest rounded-xl shadow-sm` |
| Pill chips / tags | `rounded-full` or `rounded-lg` with `bg-surface-container` |
| Border radius scale | `rounded-xl` (cards), `rounded-2xl` / `rounded-3xl` (panels), `rounded-full` (pills, nav items) |
| Hover feedback | `hover:shadow-md`, `active:scale-[0.98]` or `active:scale-95` |
| Error / delete hover | `hover:bg-error-container hover:text-error` |

### File layout

| File | Scope | Purpose |
|---|---|---|
| `src/styles.css` | global | minimal resets and Material Symbols font setup |
| `tailwind.config.js` | global | MD3 color tokens, Manrope/Inter font families, border-radius scale |
| `*.component.css` | local | section layout and component-specific adjustments |

Keep feedback centralized through `FeedbackService` and `DashboardComponent`. Do not maintain duplicate toast state in child components.

---

## 9. Alignment Status

Resolved and carried forward:
- Companies update uses `PUT /api/companies/:id` (full payload).
- Recipients delete uses `recipient.id`.
- `company_info` and `field_info` are modeled as objects in shared interfaces.
- Recipient creation sends `company_id` in `POST /api/recipients`.
- Auth headers and `401` handling are centralized via `AuthService` + interceptor.
- `/dashboard` is protected by `authGuard`.
- Feedback handling is centralized through `FeedbackService` and rendered by `DashboardComponent`.

Component renames (old → new):
- `RecipientsListComponent` + `CompaniesListComponent` → `RecipientsComponent`
- `IdentitiesListComponent` → `IdentitiesComponent`
- `JobsListComponent` → `JobDiscoveryComponent`
- `CoverLettersListComponent` + `CoverLettersDetailComponent` → `LetterEditorComponent`
- `FieldsListComponent` hosted inside new `SettingsComponent`

Remaining caveat:
- Company detail exploration should happen from `JobDiscoveryComponent` job selection; `RecipientsComponent` focuses only on recipients.

---

## 10. Unimplemented Features

Feature-local unimplemented UX details now belong in the relevant feature `SPEC.md` files under `features/`.
This root file should only keep cross-feature gaps.

### Future features (no mock-up yet)

- OTP-based login flow.
- Dedicated logout action in the sidebar.
- Real-time notifications for cover letter lifecycle events.
- Full crawler task administration UI.
- Skeleton loaders.