# Frontend Specification

**This file is the authoritative reference for the Angular frontend.**
Agents editing any component, service, model, or route MUST consult this file before making changes.
It documents exact API payload shapes, canonical TypeScript interfaces, routing, the auth pattern, and known bugs.

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

---

## 2. Routes

| Path | Component | Guard | Notes |
|---|---|---|---|
| `/` | — | Redirects to `/login` | |
| `/login` | `LoginComponent` | None | |
| `/dashboard` | `DashboardComponent` | `authGuard` | Overview: stats cards + top-scored job feed |
| `/dashboard/job-discovery` | `JobDiscoveryComponent` | Inherited | Ranked job feed, filter bar, crawler panel |
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

Define these interfaces once in `app/models/models.ts` and import everywhere. Do not re-declare them per component.

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
  label?: string;
  weight: number;
  enabled: boolean;
  guidance?: string;
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
  preference_key: string;
  preference_label?: string;
  preference_weight?: number;
  score: number;
  rationale?: string;
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
  scoring_status?: string;
  weighted_score?: number;
  max_score?: number;
  scores?: JobPreferenceScore[];
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
- `scores` on a `JobDescription` is an array of per-preference score objects.
- `created_at` and `updated_at` may be returned as protobuf-style timestamp objects: `{ seconds, nanos }`.

---

## 4. Auth

### Current state
- `POST /api/login` returns `{ token: string }`, which is stored in `localStorage['token']`.
- Auth is centralized in `app/services/auth.service.ts`.
- `app/services/auth.interceptor.ts` automatically adds `Authorization: Bearer <token>` to non-login API requests.
- On HTTP `401` (non-login requests), the interceptor calls `AuthService.logout()` and redirects to `/login`.
- `/dashboard` is protected by `authGuard` (`app/auth.guard.ts`) using `AuthService.isAuthenticated()`.
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

Location: `app/services/feedback.service.ts`

```typescript
showFeedback(message: string, isError?: boolean): void
clearFeedback(): void
feedback$: Observable<FeedbackMessage>
```

`DashboardComponent` is the only component that renders toast UI. Child components should emit feedback through the service and not maintain duplicate toast state.

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
| GET | `/api/job-descriptions` | — | `JobDescription[]` with `company_info` and optional `scores` |
| GET | `/api/job-descriptions/:id` | — | single `JobDescription` |
| POST | `/api/job-descriptions` | `{ "company_id": "<hex or omit>", "company_name": "<string or omit>", "title": "string", "description": "string", "location": "string", "platform": "string", "external_job_id": "string", "source_url": "string" }` | created `JobDescription` |
| PUT | `/api/job-descriptions/:id` | `{ "company_id": "<hex or omit>", "title": "string", "description": "string", "location": "string", "platform": "string", "external_job_id": "string", "source_url": "string" }` | `{ "message": "Job description updated successfully" }` |
| POST | `/api/job-descriptions/:id/score` | — | `{ "message": "Scoring queued successfully" }` |
| DELETE | `/api/job-descriptions/:id` | — | `{ "message": "Job description deleted successfully" }` |

Notes:
- The jobs list is the MVP entry point for the new hiring workflow.
- `weighted_score` is a deterministic aggregate computed by the backend from the stored per-preference scores.
- Per-preference values shown in the UI come from `scores`, not from re-running ranking logic in the browser.

### 6.7 Cover Letters

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

| Component | Template | Responsibilities |
|---|---|---|
| `AppComponent` | inline | Root router outlet only |
| `LoginComponent` | external HTML | Login form, token storage, redirect to dashboard |
| `DashboardComponent` | external HTML | Full-page layout: sidebar, glassmorphism top bar, toast rendering, child route outlet; at `/dashboard` also renders stats cards (Active Applications, Total Jobs Scraped, Top AI-Scored Jobs, Sent Letters) and a Top Scored Opportunities scrollable feed |
| `JobDiscoveryComponent` | external HTML | Ranked job feed (card per job: title, company, AI match score, rationale, "Prepare Cover Letter" / "Mark as Not Interested" actions); filter bar with search, filter chips, Re-Rank trigger; selecting a job surfaces company details and open positions context; right-side intelligence panel: crawler-status widget with live progress bar, per-identity discovery settings (identity selector, AI score threshold slider, toggles for Remote-first / Skill-gap analysis) |
| `LetterEditorComponent` | external HTML | Split-pane layout: left pane = rich-text editor with formatting toolbar and word count; right pane = AI Refiner chat panel with conversation history, "Apply Change" / "Undo" actions. Accessed via `/dashboard/letter-editor/:id` |
| `IdentitiesComponent` | external HTML | Bento-grid of identity cards; each card shows: header (icon, name, last-updated), Discovery Scope tag chips with "Manage Tags", Quick Stats (matches count, affinity %), Preferences & Weights bar rows with "Add Preference"; below grid: Global Curator Preferences section (writing tone, discovery interval, AI creativity slider) |
| `RecipientsComponent` | external HTML | Recipients list shell at `/dashboard/recipients`; recipients table supports CRUD and lifecycle actions. |
| `SettingsComponent` | external HTML | Settings shell hosting `FieldsListComponent`; accessible via sidebar Settings link at `/dashboard/settings` |
| `FieldsListComponent` | inline or external | Field CRUD; rendered inside `SettingsComponent` |

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

### Target features (UX-specified in mock-ups, not yet built in Angular)

- `DashboardComponent` overview page: stats cards and Top Scored Opportunities feed.
- `JobDiscoveryComponent`: ranked job feed, filter chips, Re-Rank trigger, crawler-status widget with live progress bar, per-identity discovery settings panel, and company detail context from selected jobs.
- `IdentitiesComponent`: bento-grid cards with Discovery Scope tag chips, Quick Stats, preference weight bars, Add Preference action, Global Curator Preferences section.
- `LetterEditorComponent`: split-pane layout with rich-text toolbar and AI Refiner chat panel (conversation history, Apply Change / Undo).
- `RecipientsComponent`: recipients UX refinements (sorting/filtering/lifecycle controls).
- `SettingsComponent` wrapping `FieldsListComponent`.
- Sorting and filtering across entity tables.

### Future features (no mock-up yet)

- OTP-based login flow.
- Dedicated logout action in the sidebar.
- Real-time notifications for cover letter lifecycle events.
- Full crawler task administration UI.
- Skeleton loaders.