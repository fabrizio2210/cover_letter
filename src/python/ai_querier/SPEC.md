# AI Querier Specification

**This file is the authoritative reference for the Python AI cover-letter worker.**
Agents editing `ai_querier.py` or related files in this folder MUST consult this file before making changes.

It exists to prevent contract drift between the Go API, Redis queue payloads, MongoDB documents, and the Python worker implementation.

> Shared references: `ai_querier.py`, `../../go/cmd/api/SPEC.md`, `../../go/internal/proto/common/common.proto`, `../../../spec.md`

---

## 1. Purpose and Scope

The `ai_querier` service consumes cover-letter generation jobs from Redis, uses Gemini to generate or refine letter content, and persists the resulting cover letter conversation state into MongoDB.

This document covers:
- runtime behavior of the Python worker;
- environment variables required by the worker;
- Redis queue contracts consumed by the worker;
- MongoDB collections and fields read or written by the worker;
- prompt-building inputs and generation/refinement flow;
- protobuf-backed storage shape for cover letters and history.

This document does **not** define:
- HTTP routes or frontend behavior;
- email sending contracts;
- crawler behavior;
- OTP or authentication behavior.

---

## 2. Runtime and Entry Point

| Item | Value |
|---|---|
| Language | Python |
| Main file | `ai_querier.py` |
| Entry point | `main()` |
| Queue pattern | Redis `BLPOP` consumer |
| Database | MongoDB |
| AI provider | Gemini via `google.generativeai` |

The worker runs as a long-lived process. It blocks on the Redis queue and processes one message at a time.

High-level flow:
1. Read one JSON payload from Redis.
2. Validate the payload shape.
3. Resolve the recipient from MongoDB by email.
4. Branch to initial generation or refinement.
5. Persist the resulting cover letter state into the `cover-letters` collection.

---

## 3. Environment Variables

| Variable | Default | Required | Used for |
|---|---|---|---|
| `REDIS_HOST` | `localhost` | No | Redis connection |
| `REDIS_PORT` | `6379` | No | Redis connection |
| `REDIS_QUEUE_GENERATE_COVER_LETTER_NAME` | `cover_letter_generation_queue` | No | Input queue name |
| `MONGO_HOST` | `mongodb://localhost:27017/` | Yes | MongoDB connection URI |
| `DB_NAME` | `cover_letter` | No | MongoDB database name |
| `GEMINI_TOKEN` | none | Yes in normal mode | Gemini API key |
| `AI_QUERIER_TEST_MODE` | `0` | No | If `1`, disable real Gemini calls and use deterministic fake responses |

Rules:
- If `AI_QUERIER_TEST_MODE=1`, the worker may run without `GEMINI_TOKEN`.
- If `AI_QUERIER_TEST_MODE!=1`, missing `GEMINI_TOKEN` is a startup error.
- `DB_NAME` must match the database used by the Go API.

---

## 4. Responsibilities

The worker is responsible for:
- consuming jobs from the cover-letter generation queue;
- generating an initial cover letter for a recipient;
- refining an existing cover letter using saved conversation history;
- storing the generated Markdown body and prompt history in MongoDB;
- preserving the conversation state needed for subsequent refinement.

The worker is not responsible for:
- creating recipients, companies, identities, or cover letters through HTTP;
- sending emails;
- rendering Markdown to HTML;
- validating JWTs;
- maintaining frontend-facing aggregate views.

---

## 5. Redis Input Queue Contract

### 5.1 Queue Name

The worker consumes one queue:

- `cover_letter_generation_queue`

This queue name may be overridden by `REDIS_QUEUE_GENERATE_COVER_LETTER_NAME`.

The Go API is the producer for this queue.

### 5.2 Message Shape

Each message is a UTF-8 JSON object.

#### Initial generation message

```json
{
  "recipient": "recipient@example.com"
}
```

#### Refinement message

```json
{
  "recipient": "recipient@example.com",
  "conversation_id": "gemini-conversation-id-or-worker-conversation-token",
  "prompt": "Please make it more concise and more specific to documentary photography."
}
```

### 5.3 Semantics

- `recipient` is required and is the recipient email address, not the MongoDB `_id`.
- If `conversation_id` is absent, the worker treats the message as an initial generation request.
- If `conversation_id` is present, the worker treats the message as a refinement request.
- For refinement, `prompt` is required.

### 5.4 Validation Rules

- Invalid JSON messages are rejected and logged.
- Messages without `recipient` are rejected and logged.
- If `recipient` does not match a document in `recipients.email`, the message is rejected and logged.
- If `conversation_id` is present but no cover letter exists for it, the refinement request is rejected and logged.

### 5.5 Contract Ownership

If this payload changes, the following must be updated together:
- this file;
- `../../go/cmd/api/SPEC.md`;
- Go queue producer logic in the API handlers;
- Python queue consumer logic in `ai_querier.py`.

---

## 6. MongoDB Contract

### 6.1 Collections Used

| Collection | Access | Purpose |
|---|---|---|
| `recipients` | read | Resolve recipient by email |
| `companies` | read | Resolve company linked to recipient |
| `identities` | read | Resolve identity linked to company field |
| `cover-letters` | insert/update/read | Persist cover-letter conversation state |

### 6.2 Required Read Path for Initial Generation

The initial generation flow resolves context in this order:
1. `recipients.email` equals the Redis payload `recipient`.
2. The recipient document references a company through BSON field `company`.
3. The company document references a field through BSON field `field`.
4. The identity document is resolved through BSON field `field` matching the company field reference.

Expected BSON keys used by the worker:

| Collection | BSON key | Meaning |
|---|---|---|
| `recipients` | `email` | recipient email |
| `recipients` | `name` | recipient display name |
| `recipients` | `description` | recipient context |
| `recipients` | `company` | company reference |
| `companies` | `name` | company name |
| `companies` | `description` | company description |
| `companies` | `field` | field reference |
| `identities` | `name` | candidate display name |
| `identities` | `description` | candidate description |
| `identities` | `field` | field reference |

### 6.3 Reference Storage Notes

The worker must tolerate legacy or mixed MongoDB data where relation fields are stored either as:
- `ObjectId`; or
- stringified ObjectID values.

This applies at minimum to:
- `recipients.company`;
- `companies.field`;
- `identities.field`.

Agents must not narrow this behavior unless all stored data and the Go API handlers are migrated consistently.

---

## 7. Persisted Cover-Letter Document Shape

The stored document is based on the shared protobuf `CoverLetter` message from `../../go/internal/proto/common/common.proto`.

### 7.1 Required BSON Fields

| BSON key | Type | Notes |
|---|---|---|
| `recipient_id` | string | String form of the recipient MongoDB `_id` |
| `conversation_id` | string | Worker conversation key used for refinements |
| `cover_letter` | string | Markdown body |
| `prompt` | string | Prompt used for the last generation or refinement |
| `history` | array | Conversation history in Gemini-compatible shape |
| `created_at` | object | Present on insert |
| `updated_at` | object | Present on update and should also be present after insert |
| `status` | string | Intended lifecycle state, see below |

### 7.2 History Entry Shape

Each history entry must use this shape:

```json
{
  "role": "user",
  "parts": [
    { "text": "Prompt text" }
  ]
}
```

Allowed roles:
- `user`
- `model`

The worker must preserve this schema because refinement depends on replaying the stored history into Gemini.

### 7.3 Timestamp Encoding

Timestamps are stored as plain objects, not BSON dates and not ISO strings:

```json
{
  "seconds": 1711234567,
  "nanos": 0
}
```

This must remain aligned with the Go API expectations.

### 7.4 Status Semantics

The intended `status` values are:
- `generated` after successful initial generation;
- `generated` after successful refinement unless another workflow-specific status is later introduced;
- `sent` after downstream email processing handled by another service;
- other values only if explicitly standardized in the shared contract.

The AI worker owns writing the generated state for successful AI output.

If the status contract changes, update both this file and `../../go/cmd/api/SPEC.md`.

---

## 8. Prompt Construction Contract

### 8.1 Initial Generation Inputs

The initial prompt must be built from:
- recipient email;
- recipient name if available;
- recipient description;
- company name;
- company description;
- identity name;
- identity description.

The prompt must instruct the model to write a cover letter customized to the recipient and grounded in the candidate identity.

### 8.2 Refinement Inputs

Refinement must use:
- the stored `history` from the existing cover letter document;
- the incoming refinement `prompt`;
- the existing `conversation_id` as the lookup key for the document.

The worker does not create a fresh conversation during refinement. It continues from the persisted history.

---

## 9. Processing Flows

### 9.1 Initial Generation Flow

1. Receive a queue message with `recipient` only.
2. Resolve the recipient document by email.
3. Resolve company via `recipients.company`.
4. Resolve identity via the company's field.
5. Build the initial prompt from recipient, company, and identity data.
6. Generate a cover letter through Gemini or the test-mode fake client.
7. Create a new `conversation_id`.
8. Persist a new `cover-letters` document with prompt, generated body, history, timestamps, and status.

### 9.2 Refinement Flow

1. Receive a queue message with `recipient`, `conversation_id`, and `prompt`.
2. Load the existing cover letter by `conversation_id`.
3. Append the user follow-up prompt to `history`.
4. Ask Gemini to continue from the stored history.
5. Append the model response to `history`.
6. Update the existing `cover-letters` document with the new body, last prompt, updated history, `updated_at`, and status.

---

## 10. Test Mode

When `AI_QUERIER_TEST_MODE=1`:
- the worker must not require a real Gemini API key;
- the worker still consumes real Redis messages and reads or writes MongoDB;
- generated output is synthetic and deterministic enough for integration testing;
- queue payload shape and MongoDB persistence shape must remain the same as in normal mode.

Test mode must never change the contract shape. It only replaces the model response source.

---

## 11. Failure Handling

The worker should log and skip, rather than crash the process, for recoverable message-level failures such as:
- malformed JSON;
- missing `recipient`;
- missing refinement `prompt`;
- missing MongoDB recipient, company, identity, or cover letter;
- invalid or incomplete AI response.

Connection or infrastructure failures may temporarily abort processing, but the worker is expected to continue its main loop after retry delay.

---

## 12. Shared Protobuf Dependency

Shared data structures come from:

- `../../go/internal/proto/common/common.proto`

Relevant messages:
- `CoverLetter`
- `HistoryEntry`
- `HistoryPart`

Important constraint:
- the Python worker may rely on raw MongoDB fields such as `companies.description` that are not represented in protobuf messages;
- this is allowed only for prompt-building input, not as justification to change stored cover-letter schema independently.

Any change to shared persisted cover-letter fields must be evaluated against both the Python generated code and the Go API consumers.

---

## 13. Current Implementation Deviations

The items below describe known differences between the intended contract and the current `ai_querier.py` implementation.

### 13.1 Missing `status` Persistence

The intended contract requires the worker to persist `status`, but the current implementation does not write `status` on insert or update.

Impact:
- downstream consumers may observe missing or empty status values.

### 13.2 Insert Path Does Not Persist `updated_at`

The intended contract expects `updated_at` to be available on created cover letters as well, but the current insert path only writes `created_at`.

### 13.3 Company Description Comes From Raw MongoDB Data

The worker reads `companies.description` directly from MongoDB for prompt construction even though `Company` in the shared protobuf does not define that field.

This is valid for prompt input but must not be mistaken for a protobuf-backed shared field.

### 13.4 Mixed Reference Type Handling Is Best-Effort

The current worker attempts to handle both `ObjectId` and string references for company and field links. This behavior is intentional for compatibility, but it is not enforced by schema.

### 13.5 Refinement Still Uses `recipient` in Queue Payload

Refinement looks up the document by `conversation_id`, but the queue payload still includes `recipient` and the worker validates that the email exists before refining.

This field remains part of the shared queue contract and must not be removed casually.

---

## 14. Editing Guardrails for Agents

Before changing this worker:
1. Read this file.
2. Check `../../go/cmd/api/SPEC.md` for the producer-side queue contract.
3. Check `../../go/internal/proto/common/common.proto` before changing persisted cover-letter fields.
4. Preserve exact field names in Redis payloads and MongoDB documents.
5. If you change shared contracts, update the Go API, Python worker, protobuf definitions, and both spec files in the same change set.

Do not change any of these names without a coordinated cross-service change:
- `recipient`
- `conversation_id`
- `prompt`
- `recipient_id`
- `cover_letter`
- `history`
- `created_at`
- `updated_at`
- `status`

---

## 15. Source of Truth Hierarchy

Use these files in this order when working on the AI worker:
1. this file for worker-local behavior and contracts;
2. `../../go/cmd/api/SPEC.md` for the API-produced queue contract;
3. `../../go/internal/proto/common/common.proto` for shared persisted structure;
4. `../../../spec.md` for broader product intent only.

If two files disagree on a shared contract, resolve the discrepancy explicitly in code and docs rather than guessing.