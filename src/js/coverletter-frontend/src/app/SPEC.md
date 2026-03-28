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

| Path | Component | Guard |
|---|---|---|
| `/` | — | Redirects to `/login` |
| `/login` | `LoginComponent` | None |
| `/dashboard` | `DashboardComponent` (shell) | `authGuard` |
| `/dashboard/recipients` | `RecipientsListComponent` | Inherited |
| `/dashboard/companies` | `CompaniesListComponent` | Inherited |
| `/dashboard/fields` | `FieldsListComponent` | Inherited |
| `/dashboard/identities` | `IdentitiesListComponent` | Inherited |
| `/dashboard/jobs` | `JobsListComponent` | Inherited |
| `/dashboard/cover-letters` | `CoverLettersListComponent` | Inherited |
| `/dashboard/cover-letters/:id` | `CoverLettersDetailComponent` | Inherited |

Default redirect: `/dashboard` → `/dashboard/recipients`.

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

| Component | Template style | Responsibilities |
|---|---|---|
| `AppComponent` | inline | Root router outlet only |
| `LoginComponent` | external HTML | Login form, token storage, redirect to dashboard |
| `DashboardComponent` | external HTML | Layout shell, sidebar, toast rendering, child route outlet |
| `RecipientsListComponent` | external HTML | Recipient CRUD, company association, generation trigger |
| `CompaniesListComponent` | external HTML | Company CRUD and field association |
| `FieldsListComponent` | inline template | Field CRUD |
| `IdentitiesListComponent` | inline template | Identity CRUD and signature editing |
| `JobsListComponent` | external HTML | Job listing, filtering, score visibility, queue scoring |
| `CoverLettersListComponent` | external HTML | Cover letter list, open detail, delete |
| `CoverLettersDetailComponent` | external HTML | Edit body, refine, send, delete |

---

## 8. Styling Conventions

| File | Scope | Purpose |
|---|---|---|
| `src/styles.css` | global | minimal global styles |
| `app/shared-styles/tables.css` | shared | tables, inputs, buttons, responsive layout |
| `app/styles/feedback.css` | shared | top-right feedback toast |
| `*.component.css` | local | section layout and component-specific adjustments |

Conventions:
- Reuse `shared-styles/tables.css` for all entity-table views.
- Keep feedback centralized through `FeedbackService` and `DashboardComponent`.
- Prefer external templates unless an inline template remains clearly readable.

---

## 9. Alignment Status

Resolved in current frontend code:
- Companies update now uses `PUT /api/companies/:id` (full payload), not `/name`.
- Recipients no longer call `PUT /api/recipients/:id/email`.
- Recipients delete uses `recipient.id`.
- `company_info` and `field_info` are modeled as objects in shared interfaces.
- Recipient creation sends `company_id` in `POST /api/recipients` and does not do a redundant association call.
- Auth headers and `401` handling are centralized via `AuthService` + interceptor.
- `/dashboard` is protected by `authGuard`.
- Feedback handling is centralized through `FeedbackService` and rendered by `DashboardComponent`.

Remaining caveat:
- In `CompaniesListComponent`, a name-only update is intentionally blocked in the UI when no field is associated yet, because the backend update endpoint currently expects a full payload that includes field context.
- The Jobs route, identity preference editing, and job scoring views are defined in this spec as the target contract, but are not implemented in the current frontend code yet.

---

## 10. Unimplemented Features

These features are described in the project-level spec but are not implemented yet:

- OTP-based login flow.
- Jobs list and ranking UI defined in this spec.
- Identity preference editing UI for job scoring.
- Real-time notifications for cover letter lifecycle events.
- Crawler task UI.
- Settings UI.
- Dedicated logout action in the dashboard UI.
- Skeleton loaders.
- Split-pane cover letter editor with live preview.
- Sorting and filtering across entity tables.