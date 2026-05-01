# AI Scorer Specification

**This file is the authoritative reference for the Python scoring worker.**
Agents editing `ai_scorer.py` or related files in this folder MUST consult this file before making changes.

It exists to prevent contract drift between the Go API, Redis queue payloads, MongoDB scoring documents, and the Python scoring worker implementation.

> Shared references: `ai_scorer.py`, `../../go/cmd/api/SPEC.md`, `../../go/internal/proto/common/common.proto`, `../../../spec.md`

---

## 1. Purpose and Scope

The `ai_scorer` service consumes job-scoring jobs from Redis, uses a local model served by an internal Ollama service in the stack network, and persists per-preference scores plus deterministic aggregate ranking data into MongoDB.

This document covers:
- runtime behavior of the Python scoring worker;
- environment variables required by the worker;
- Redis queue contracts consumed by the worker;
- MongoDB collections and fields read or written by the worker;
- prompt-building inputs and scoring flow;
- scoring lifecycle transitions.

This document does **not** define:
- HTTP routes or frontend behavior;
- cover-letter generation or refinement contracts;
- crawler extraction behavior;
- email delivery behavior;
- authentication behavior.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Language | Python |
| Main file | `ai_scorer.py` |
| Entry point | `main()` |
| Queue pattern | Redis `BLPOP` consumer |
| Database | MongoDB |
| AI provider | Ollama (internal stack endpoint) |
| Job execution model | Worker pool; each worker is assigned one dequeued job payload at a time |

The worker runs as a long-lived process. A queue consumer dispatches dequeued jobs to a bounded worker pool.

Concurrency model:
- queue-level assignment is per job (`job_id`) payload;
- each worker processes one job at a time;
- total concurrent job executions are bounded by `AI_SCORER_OLLAMA_PARALLELISM`.
- each worker creates its own Ollama client connection path; load balancing across Ollama replicas is handled at TCP/network level.

High-level flow:
1. Read one JSON payload from Redis.
2. Validate the payload shape.
3. Resolve required MongoDB context for scoring.
4. Score each enabled identity preference using Ollama.
5. Persist per-preference scores.
6. Compute deterministic aggregate ranking from stored scores and weights.
7. Update job scoring fields and lifecycle status.

---

## 3. Environment Variables

| Variable | Default | Required | Used for |
|---|---|---|---|
| `REDIS_HOST` | `localhost` | No | Redis connection |
| `REDIS_PORT` | `6379` | No | Redis connection |
| `JOB_SCORING_QUEUE_NAME` | `job_scoring_queue` | No | Scoring queue name |
| `SCORING_PROGRESS_CHANNEL_NAME` | `scoring_progress_channel` | No | Redis channel used to publish scoring progress snapshots |
| `MONGO_HOST` | `mongodb://localhost:27017/` | Yes | MongoDB connection URI |
| `OLLAMA_HOST` | none | Yes | Ollama base URL (dev stack uses `http://ollama:11434`) |
| `OLLAMA_MODEL` | none | Yes | Ollama model name |
| `AI_SCORER_TEST_MODE` | `0` | No | If `1`, disable real Ollama calls and use deterministic fake responses |
| `AI_SCORER_OLLAMA_PARALLELISM` | `1` | No | Worker-pool size (maximum number of jobs processed in parallel) |

Rules:
- If `AI_SCORER_TEST_MODE=1`, the worker may run without a reachable Ollama endpoint.
- If `AI_SCORER_TEST_MODE!=1`, missing `OLLAMA_HOST` or `OLLAMA_MODEL` is a startup error.
- `AI_SCORER_OLLAMA_PARALLELISM` must be an integer greater than zero; invalid values fall back to `1`.
- Queue-level worker assignment remains per job payload when `AI_SCORER_OLLAMA_PARALLELISM > 1`; parallelism scales by concurrent jobs, not by per-preference fan-out within one job.
- Global reads use `cover_letter_global` (`job-descriptions`, `companies`).
- Per-user reads/writes use `cover_letter_<user_id>` (`identities`, `job-preference-scores`).
- In `docker/lib/stack-dev.yml`, `OLLAMA_HOST` is expected to target the internal service DNS name (`http://ollama:11434`).

---

## 4. Responsibilities

The worker is responsible for:
- consuming jobs from the job scoring queue;
- resolving job/company/identity scoring context from MongoDB;
- scoring stored job descriptions against enabled weighted identity preferences;
- upserting one identity-scoring document per `(job_id, identity_id)` in `job-preference-scores` with embedded per-preference scores;
- computing weighted aggregate ranking deterministically;
- persisting identity-scoped aggregate fields and scoring lifecycle on `job-preference-scores` documents;
- publishing scoring-progress snapshots to Redis for API relay to frontend clients.

The worker is not responsible for:
- generating or refining cover letters;
- creating recipients, companies, identities, or job descriptions via HTTP;
- crawling job sources;
- sending emails;
- validating JWTs.

---

## 5. Redis Input Queue Contract

### 5.1 Queue Name

The worker consumes one queue:
- `job_scoring_queue`

This queue name may be overridden by `JOB_SCORING_QUEUE_NAME`.

Producer-side references:
- Go API `POST /api/job-descriptions/:id/score` producer;
- crawler post-persistence enqueue flow when enabled.

### 5.2 Message Shape

Each scoring message is a UTF-8 JSON object.

```json
{
  "user_id": "<jwt sub>",
  "job_id": "<job description hex object id>",
  "identity_id": "<identity hex object id>"  // required
}
```

### 5.3 Semantics

- `job_id` is required and is the `job-descriptions._id` hex string.
- `user_id` is required and is used to derive per-user DB name.
- `identity_id` is required for all producer messages. The worker resolves identity directly by `_id` in the per-user DB.
- Messages missing `identity_id` are rejected during queue ingestion before scoring execution.
- When `identity_id` is present but does not resolve to a document in the per-user DB, the job is recorded as `skipped` with reason `identity_not_found` without any fallback.
- The worker produces one score per enabled preference.
- The aggregate ranking is not generated by AI output; it is computed deterministically after per-preference score persistence.

### 5.4 Validation Rules

- Invalid JSON messages are rejected and logged.
- Messages without `user_id`, `job_id`, or `identity_id` are rejected and logged.
- Messages whose `job_id` does not resolve to a job description are rejected and logged.
- Messages that cannot resolve scoring prerequisites must produce no job-level score writes; when an identity is resolved, the worker records `scoring_status = skipped` on the matching `job-preference-scores` document.

### 5.5 Contract Ownership

If this payload changes, the following must be updated together:
- this file;
- `../../go/cmd/api/SPEC.md`;
- Go queue producer logic in API handlers;
- crawler scoring enqueue logic;
- Python queue consumer logic in `ai_scorer.py`.

### 5.6 Scoring Progress Output Contract

The worker publishes scoring progress snapshots to Redis channel:
- `scoring_progress_channel`

This channel name may be overridden by `SCORING_PROGRESS_CHANNEL_NAME`.

Payload shape:

```json
{
  "run_id": "<scoring run id>",
  "identity_id": "<identity hex object id>",
  "status": "running",
  "message": "Scoring in progress",
  "estimated_total": 24,
  "completed": 7,
  "percent": 29,
  "started_at": { "seconds": 1711234567, "nanos": 0 },
  "updated_at": { "seconds": 1711234600, "nanos": 0 },
  "finished_at": null,
  "reason": ""
}
```

Progress rules:
- Scoring progress is independent from crawl progress.
- Each scoring run starts from `0%`.
- `percent` is an integer in range `0..100`.
- Terminal statuses are `completed` and `failed`; terminal events include `finished_at`.
- Publish failures must be logged but must not terminate scoring-job processing.

---

## 6. MongoDB Contract

### 6.1 Collections Used

| Collection | Access | Purpose |
|---|---|---|
| `job-descriptions` (global DB) | read | Load jobs for scoring context |
| `companies` (global DB) | read | Resolve company linked to the job |
| `identities` (per-user DB) | read | Resolve identity linked by company field |
| `job-preference-scores` (per-user DB) | insert/update/read | Persist one score document per `(job_id, identity_id)` with embedded preference scores, aggregate fields, and lifecycle status |

### 6.2 Required Read Path for Job Scoring

The scoring flow resolves context in this order:
1. `jobs._id` equals Redis payload `job_id`.
2. The job document references a company through BSON key `company`.
3. The company document references a field through BSON key `field`.
4. The identity document is resolved through BSON key `field` matching the company field reference.
5. The identity document provides a `preferences` array.

Expected BSON keys used by the worker:

| Collection | BSON key | Meaning |
|---|---|---|
| `job-descriptions` | `title` | job title |
| `job-descriptions` | `description` | job description body |
| `job-descriptions` | `location` | location text |
| `job-descriptions` | `company` | company reference |
| `companies` | `field` | field reference |
| `identities` | `preferences` | weighted preference list |

### 6.3 Reference Storage Notes

The worker must tolerate legacy or mixed MongoDB data where relation fields are stored either as:
- `ObjectId`; or
- stringified ObjectID values.

For cross-service consistency, new upstream writes are expected to use `ObjectId` for relation fields. Mixed-type tolerance remains a read-compatibility requirement.

At minimum this applies to:
- `jobs.company`;
- `companies.field`;
- `identities.field`.

---

## 7. Persisted Job Scoring Document Shape

### 7.1 Required score-document fields (`job-preference-scores`)

| BSON key | Type | Notes |
|---|---|---|
| `job_id` | string | String form of `jobs._id` |
| `identity_id` | string | String form of `identities._id` |
| `preference_scores` | array | Embedded per-preference scores for this job/identity pair |
| `scoring_status` | string | One of `queued`, `scored`, `failed`, `skipped` |
| `weighted_score` | number | Deterministic weighted aggregate for this job/identity pair |

Each entry in `preference_scores` must include:

| BSON key | Type | Notes |
|---|---|---|
| `preference_key` | string | Stable key from identity preference |
| `preference_guidance` | string | Guidance snapshot |
| `preference_weight` | number | Weight snapshot |
| `score` | integer | Integer score in range `1..5` |
| `scored_at` | object | `{ "seconds": <unix>, "nanos": 0 }` |

Uniqueness rule:
- one document per `(job_id, identity_id)`;
- writes are performed as MongoDB upserts using this key pair.

Aggregate and lifecycle fields are stored on `job-preference-scores`, not on `job-descriptions`.

---

## 8. Prompt Construction Contract

Scoring prompt inputs must include:
- job title, description, and location;
- one enabled identity preference guidance at a time.

Scoring prompt inputs must exclude:
- source platform;
- company name and company description;
- identity name and identity description.
- preference key.

For each enabled preference, the prompt must ask Ollama for:
- an integer score from 1 to 5.

The worker must treat model output as per-preference evidence only. Weighted aggregate ranking is computed deterministically outside the model output.

---

## 9. Processing Flow

1. Receive a queue message with `user_id` and `job_id`.
2. Derive per-user DB from `user_id`.
3. Load the job description by `_id` from global `job-descriptions`.
4. Resolve company via `job-descriptions.company` in global DB.
5. Resolve identity via company field in per-user `identities`.
6. Keep only enabled preferences from `identities.preferences`.
7. For each enabled preference, request a score from Ollama within the currently assigned job worker.
8. Upsert one `job-preference-scores` document for `(job_id, identity_id)` containing embedded per-preference scores.
9. Compute weighted aggregate from stored scores and weights.
10. Update the same `job-preference-scores` document with aggregate fields and terminal `scoring_status`.
11. Publish scoring-progress updates for run start, incremental completion, and terminal state.

Scoring lifecycle expectations:
- Allowed values on score documents: `queued`, `scored`, `failed`, `skipped`.
- Set `scoring_status = scored` only after per-preference writes and aggregate update succeed.
- Set `scoring_status = skipped` when prerequisites are missing.
- Set `scoring_status = failed` on processing errors after dequeue where scoring cannot complete.

---

## 10. Test Mode

When `AI_SCORER_TEST_MODE=1`:
- the worker must not require a real Ollama connection;
- the worker still consumes real Redis messages and reads/writes MongoDB;
- scoring output is synthetic and deterministic enough for integration testing;
- queue payload shape and MongoDB persistence shape must remain unchanged.

Test mode must never change contract shape. It only replaces the model response source.

---

## 11. Failure Handling

The worker should log and skip, rather than crash the process, for recoverable message-level failures such as:
- malformed JSON;
- missing `user_id` or `job_id`;
- missing MongoDB job/company/identity linkage;
- no enabled identity preferences;
- invalid or incomplete model response.

Connection or infrastructure failures may temporarily abort processing, but the worker is expected to continue its main loop after retry delay.

---

## 12. Shared Protobuf Dependency

Shared data structures come from:
- `../../go/internal/proto/common/common.proto`

Relevant messages:
- `IdentityPreference`
- `JobDescription`
- `JobPreferenceScore`

Any shared persisted scoring-field change must be evaluated against both Python and Go consumers.

---

## 13. Editing Guardrails for Agents

Before changing this worker:
1. Read this file.
2. Check `../../go/cmd/api/SPEC.md` for producer-side queue contract.
3. Check `../web_crawler/SPEC.md` for scoring enqueue producer behavior.
4. Check `../../go/internal/proto/common/common.proto` before changing persisted scoring fields.
5. Preserve exact field names in Redis payloads and MongoDB documents.
6. If you change shared contracts, update Go API, crawler, scorer code, protobuf definitions, and related spec files in the same change set.

Do not change these names without coordinated cross-service updates:
- `job_id`
- `preference_key`
- `score`
- `preference_weight`
- `weighted_score`
- `scoring_status`

---

## 14. Source of Truth Hierarchy

Use these files in this order when working on the scoring worker:
1. this file for scorer-local behavior and contracts;
2. `../../go/cmd/api/SPEC.md` for API-produced queue contract;
3. `../web_crawler/SPEC.md` for crawler enqueue behavior;
4. `../../go/internal/proto/common/common.proto` for shared persisted structure;
5. `../../../spec.md` for broader product intent only.

If two files disagree on a shared contract, resolve the discrepancy explicitly in code and docs rather than guessing.
