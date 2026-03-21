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
| `/dashboard` | `DashboardComponent` (shell) | **None — missing, see §4** |
| `/dashboard/recipients` | `RecipientsListComponent` | Inherited |
| `/dashboard/companies` | `CompaniesListComponent` | Inherited |
| `/dashboard/fields` | `FieldsListComponent` | Inherited |
| `/dashboard/identities` | `IdentitiesListComponent` | Inherited |
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

export interface FeedbackMessage {
  message: string;
  isError: boolean;
}
```

Critical alignment rules:
- `field_info` and `company_info` are objects, never arrays.
- `company_id` is the JSON key for a recipient's company reference, except for `PUT /api/recipients/:id/company`, which uses `companyId`.
- `field_id` is the JSON key for company and identity field references, except for `PUT /api/identities/:id/field`, which uses `fieldId`.
- `created_at` and `updated_at` may be returned as protobuf-style timestamp objects: `{ seconds, nanos }`.

---

## 4. Auth

### Current state
- `POST /api/login` returns `{ token: string }`, which is stored in `localStorage['token']`.
- Every data component has its own `getAuthHeaders()` that reads the token and builds the `Authorization: Bearer` header.
- On HTTP `401`, components navigate to `/login`.
- There is no `HttpInterceptor`, no `AuthService`, no route guard, and no logout action.
- Login is password-only (`{ password: string }`). OTP-based login described in the project-level spec is not yet implemented.

### Target pattern
Centralise auth in `app/services/auth.service.ts`:

```typescript
getToken(): string | null
getHeaders(): HttpHeaders
logout(): void
isAuthenticated(): boolean
```

Add an `HttpInterceptor` to attach the auth header automatically and redirect on `401`.
Add a `canActivate` guard on `/dashboard`.

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

Bug:
- The current frontend calls `PUT /api/companies/:id/name`, which does not exist in the backend. Use `PUT /api/companies/:id` with the full body instead.

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

Bugs:
- The current frontend calls `PUT /api/recipients/:id/email`, which does not exist in the backend.
- The current frontend deletes recipients using `_id`; it must use `id`.
- Recipient creation should send `company_id` only in the `POST` body and not follow up with a redundant association call.

### 6.5 Identities

| Method | Path | Request body | Success response |
|---|---|---|---|
| GET | `/api/identities` | — | `Identity[]` with `field_info` embedded |
| POST | `/api/identities` | `{ "identity": "string", "name": "string", "description": "string", "field_id": "<hex or empty>", "html_signature": "<html or omit>" }` | created `Identity` |
| PUT | `/api/identities/:id/name` | `{ "name": "string" }` | `{ "message": "Identity updated successfully" }` |
| PUT | `/api/identities/:id/description` | `{ "description": "string" }` | `{ "message": "Identity updated successfully" }` |
| PUT | `/api/identities/:id/field` | `{ "fieldId": "<hex>" }` | `{ "message": "Identity updated successfully" }` |
| PUT | `/api/identities/:id/signature` | `{ "html_signature": "<html string>" }` | `{ "message": "Identity updated successfully" }` |
| DELETE | `/api/identities/:id` | — | `{ "message": "Identity deleted successfully" }` |

Notes:
- `PUT /api/identities/:id/field` uses `fieldId` in camelCase because that is what the backend expects.
- `html_signature` is limited to 64 KiB.

### 6.6 Cover Letters

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

## 9. Known Bugs and Misalignments

| # | Area | Problem | Required behavior |
|---|---|---|---|
| 1 | Companies | Frontend uses `PUT /api/companies/:id/name` | Use `PUT /api/companies/:id` |
| 2 | Recipients | Frontend uses `PUT /api/recipients/:id/email` | Remove email editing or add backend support first |
| 3 | Recipients | Frontend deletes with `_id` | Use `id` |
| 4 | Relations | `company_info` and `field_info` are sometimes typed as arrays | Treat them as objects |
| 5 | Recipients create | Frontend may send both `company_id` and `companyId` and then a second PUT | Send only `company_id` in POST |
| 6 | Auth | Header creation is duplicated in many components | Centralize in `AuthService` / interceptor |
| 7 | Feedback | `RecipientsListComponent` has dead local feedback state | Use `FeedbackService` only |

---

## 10. Unimplemented Features

These features are described in the project-level spec but are not implemented yet:

- OTP-based login flow.
- Route guards for protected routes.
- Shared `AuthService` and `HttpInterceptor`.
- Shared model definitions file.
- Real-time notifications for cover letter lifecycle events.
- Crawler task UI.
- Settings UI.
- Skeleton loaders.
- Split-pane cover letter editor with live preview.
- Sorting and filtering across entity tables.