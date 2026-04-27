# cover-letter-frontend — Specification

**Authoritative reference for the cover-letter-frontend Angular application.**
Agents editing files in this folder MUST consult this file before making changes.

> Parent index: [\`../../../spec.md\`](../../../spec.md)
> Shared references: \`../../go/cmd/api/SPEC.md\`, \`../../python/web_crawler/SPEC.md\`

---

## 1. Purpose and Scope

The \`cover-letter-frontend\` is a single-page Angular application that allows users to manage their professional identities, discover job opportunities, score them based on personal preferences, and generate tailored cover letters using generative AI.

---

## 2. Technical Stack

| Item | Value |
|---|---|
| Framework | Angular 20 |
| Language | TypeScript |
| Build Tool | Angular CLI (\`ng build\`) |
| Styling | Tailwind CSS / SCSS |
| API Integration | RESTful (Go backend), Server-Sent Events (SSE) for progress |

---

## 3. Core Models and Types

The application shares data models with the Go API. Key interfaces live in \`src/app/shared/models/models.ts\`.

### 3.1 Crawl and Scoring Progress

Crawl progress is tracked via SSE. The \`CrawlProgress\` type defines the status of background crawler workflows.

| Workflow ID | Description |
|---|---|
| \`crawler_ycombinator\` | Discovers companies from Y Combinator |
| \`crawler_hackernews\` | Discovers companies from Hacker News |
| \`enrichment_ats_enrichment\` | Enriches companies with ATS metadata |
| \`crawler_ats_job_extraction\` | Extracts jobs from ATS platforms |
| \`crawler_4dayweek\` | Extracts jobs from 4dayweek.io |
| \`crawler_levelsfyi\` | Extracts jobs from Levels.fyi |

---

## 4. Features and Modules

- **Auth**: User login and registration using JWT.
- **Identities**: CRUD for user profiles, including role keywords and scoring preferences.
- **Job Discovery**: Trigger and monitor crawl runs; browse discovered jobs.
- **Scoring**: View preference-based scores for jobs; trigger re-scoring.
- **Cover Letters**: Generate and manage tailored cover letters for specific jobs/recipients.
- **Recipients**: Manage contact information for hiring managers and companies.

---

## 5. SSE Progress Handling

The \`ApiService\` provides methods to subscribe to SSE streams for real-time progress updates:
- \`streamCrawlProgress(identityId: string)\`: Listens for \`crawl-progress\` events.
- \`streamScoringProgress(identityId: string)\`: Listens for \`scoring-progress\` events.

Components use these streams to update progress bars and status indicators in the UI.

---

## 6. Editing Guardrails

- **Type Safety**: Maintain consistency with the Go API models. Update \`models.ts\` when API contracts change.
- **Component Design**: Prefer small, reusable components in the \`shared\` folder.
- **State Management**: Use RxJS observables for reactive state management within services.
- **Styling**: Use Tailwind CSS utility classes where possible. Custom styles should be scoped to components.
- **Verification**: After making UI changes, run the Playwright verification workflow to ensure visual correctness.
