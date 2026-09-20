# The Curator — Cover Letter Platform

> **Autonomous AI Job Strategist, Multi-Source Job Discovery, Intelligent Match Scoring & Tailored Cover Letter Studio**

The Curator is a self-hosted, multi-tenant web application designed to manage the full lifecycle of job opportunities and application materials. It discovers relevant job openings across multiple platforms, ranks them against user-defined weighted preferences using fine-tuned local LLMs, and drafts highly personalized, vetted cover letters using generative AI.

The system is architected as a distributed microservice stack running in containerized environments (Docker Swarm on ARM64 Raspberry Pi nodes in production, Docker Compose locally) with a Go REST API, an Angular 20 SPA frontend, Python asynchronous workers, a MongoDB replica set, and high-availability Redis Sentinel clusters.

---

## Table of Contents

- [Core Capabilities](#core-capabilities)
- [System Architecture](#system-architecture)
- [Repository Structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Local Development Setup](#local-development-setup)
- [Testing & Quality Verification](#testing--quality-verification)
- [AI Scorer: Fine-Tuning & Model Packaging](#ai-scorer-fine-tuning--model-packaging)
- [Production Deployment](#production-deployment)
- [Specifications & Contract Index](#specifications--contract-index)

---

## Core Capabilities

### 1. Multi-Source Job Discovery & Enrichment
- **Role-Guided Discovery**: Crawling is driven by explicit professional identities configured with target role keywords.
- **Broad Source Coverage**:
  - **Job Boards & Curated Communities**: Y Combinator Work at a Startup, Hacker News ("Who is Hiring"), Levels.fyi, 4dayweek.io, Built In, Otta, LinkedIn, Indeed, SimplyHired, Wellfound.
  - **Venture Capital & Portfolio Directories**: Techstars, 500 Global, Andreessen Horowitz (a16z), Sequoia Capital, Crunchbase.
  - **Direct ATS Extraction**: Automated extraction from Greenhouse, Lever, and Ashby APIs.
- **Automated Entity Linking & Deduplication**: Discovered job postings automatically resolve or create company entities, associate sector classifications, detect stale/retired postings, and maintain clean canonical records.

### 2. Local AI Job Scoring & Ranking
- **Privacy-Preserving Local Inference**: Evaluates job descriptions locally via Ollama without leaking applicant preference criteria to external APIs.
- **Fine-Tuned Specialized Models**: Leverages custom-trained, quantized models (e.g. Qwen 2.5) optimized for strict JSON output and criteria evaluation.
- **Evidence Extraction & Snippet Reranking**: Utilizes location normalization, query expansion, and BM25 / cross-encoder snippet retrieval to provide concise, factual evidence to the LLM.
- **Deterministic Multi-Criteria Weighting**: The AI rates individual criteria on a 0–5 scale; overall compatibility (`weighted_score`) is calculated deterministically from stored user weights to guarantee reproducibility.

### 3. Bespoke Cover Letter Studio
- **Context-Aware Drafting**: Generates cover letters via Google Gemini by combining candidate profile data, company context, job requirements, and hiring manager information.
- **Interactive Split-Pane Editor**: Real-time Markdown editor alongside an interactive AI Refiner chat panel for iterative revisions while preserving conversation history.
- **Recipient & Email Lifecycle**: Tracks hiring contacts and dispatches approved cover letters via asynchronous SMTP workers.

### 4. Multi-Tenant & Multi-Identity Management
- **Distinct Personas**: Manage multiple professional identities (e.g., *Backend Systems Engineer*, *Engineering Manager*, *Data Architect*) with independent discovery roles and scoring preferences.
- **Isolated Storage**: Strict tenant separation using a global catalog (`cover_letter_global`) for shared jobs and companies, and per-user databases (`cover_letter_<sub>`) for private identities, scores, letters, and contacts.

### 5. Real-Time Feedback
- **Live SSE Streaming**: Server-Sent Events (SSE) stream real-time progress for crawler workflows and background scoring tasks directly to dashboard progress widgets.

---

## System Architecture

```
                                 +-----------------------------+
                                 |       Traefik Proxy         |
                                 |      (:80, :443, :8888)     |
                                 +--------------+--------------+
                                                |
                        +-----------------------+-----------------------+
                        |                                               |
                        v                                               v
        +-------------------------------+               +-------------------------------+
        |    Angular 20 Frontend        |               |       Go REST API (:8080)     |
        |       ("The Curator")         |               |     (Gin-Gonic, JWT, SSE)     |
        +-------------------------------+               +---------------+---------------+
                                                                        |
                        +-----------------------------------------------+-------------------------------+
                        |                               |                                               |
                        v                               v                                               v
        +-------------------------------+   +-------------------------------+   +-------------------------------+
        |   MongoDB 4.4 Replica Set     |   |   Redis 7.4 + Sentinel        |   |      Protocol Buffers         |
        |      `coverletter-rs`         |   |    (Queues, Pub/Sub, Cache)   |   |   Canonical Schema Model      |
        +---------------+---------------+   +---------------+---------------+   +-------------------------------+
                        ^                                   ^
                        |                                   |
        +---------------+-----------------------------------+---------------+
        |                               |                                   |
        v                               v                                   v
+-------------------------------+ +-------------------------------+ +-------------------------------+
|     Web Crawler Workers       | |       AI Scorer Worker        | |       AI Querier Worker       |
|  - Dispatcher & Scheduler     | |  - Ollama Worker Pool         | |  - Google Gemini API          |
|  - Source Adapters (HN, YC...) | |  - BM25 Snippet Reranking     | |  - Cover Letter Generation    |
|  - ATS Extract & Enrich       | |  - Deterministic Aggregation  | |  - Multi-Turn Refinement Chat |
+-------------------------------+ +---------------+---------------+ +-------------------------------+
                                                |
                                                v
                                  +-------------------------------+
                                  |     Ollama Model Server       |
                                  |  (Qwen 2.5 Fine-Tuned Local)  |
                                  +-------------------------------+
```

### Technology Stack Summary

| Layer | Technologies |
|---|---|
| **Frontend** | Angular 20, TypeScript, Tailwind CSS, Material Design 3 Tokens, Material Symbols |
| **Backend API** | Go 1.25, Gin-Gonic, JWT (HS256), `go-redis`, official MongoDB Go driver |
| **Data Contracts**| Protocol Buffers (`common.proto`) compiled to Go (`common.pb.go`) and Python (`common_pb2.py`) |
| **AI Workers** | Python 3.12, `google-generativeai`, `ollama`, `fastembed`, `beautifulsoup4`, `requests` |
| **Data & Cache** | MongoDB 4.4+ (Replica Set), Redis 7.4 (3-node Sentinel deployment) |
| **Routing / Edge**| Traefik Reverse Proxy (Docker Provider, SSL, Dashboard) |
| **Infrastructure**| Docker Swarm (Production on ARM64 Raspberry Pi 5), Docker Compose (Local Dev) |

---

## Repository Structure

```
cover_letter/
├── checkpoints/              # Checkpoints for trained cross-encoder / scorer models
├── docker/
│   ├── lib/                  # Local development stack (stack-dev.yml, helper scripts)
│   ├── ollama/               # Ollama Dockerfiles & model packaging runbooks
│   ├── prod/                 # Production Docker Swarm stack (stack.yml, Mongo runbook)
│   └── x86_64/               # Service Dockerfiles (API, Frontend, Workers, Crawlers)
├── docs/                     # Developer workflows, runbooks, architecture specs
├── scripts/                  # Test gate, model evaluation, and CI automation scripts
│   ├── test-gate.sh          # Pre-commit and CI test runner (fast & full modes)
│   ├── eval-scorer.sh        # Scorer regression evaluation script
│   ├── publish-ollama-model.sh # OCI model-store publisher
│   └── smoke-ollama-image.sh # Ollama container health and validation test
├── src/
│   ├── go/                   # Go REST API backend
│   │   ├── cmd/api/          # API entrypoint, routers, domain handlers, and specs
│   │   └── internal/proto/   # Protocol Buffers definitions (common.proto)
│   ├── js/coverletter-frontend/ # Angular 20 SPA application
│   └── python/               # Asynchronous worker services
│       ├── ai_querier/       # Gemini-based cover letter generator & refiner
│       ├── ai_scorer/        # Ollama-based preference scoring, evals & training pipeline
│       └── web_crawler/      # Dispatcher, scheduler, source crawlers & enrichers
├── tests/                    # End-to-end integration and unit tests
│   └── e2e/                  # Containerized E2E test suites
├── CICD.sh                   # Main build, test, candidate packaging & deploy pipeline
├── Makefile                  # Proto generation, test gates, and training targets
└── spec.md                   # System-wide high-level specification
```

---

## Prerequisites

Ensure the following tools are installed on your workstation:

- **Go**: `1.23+` (recommended `1.25`)
- **Python**: `3.12+`
- **Node.js**: `20+` and `npm`
- **Docker**: Engine with Docker Compose and Buildx
- **Browser**: Chromium or Google Chrome (required for headless Angular tests)
- **Protobuf Tools** (only needed when updating schemas):
  - `protoc`
  - `protoc-go-inject-tag`
  - `protoc-gen-mypy`

---

## Local Development Setup

### 1. Configure Repository Git Hooks
Install repository-managed Git hooks to enforce fast test gates on pre-commit and pre-push:

```bash
make install-hooks
```

### 2. Environment Variables Configuration
Create a development environment file at `~/.docker/coverletter-dev.env`:

```env
# AI & Search APIs
GEMINI_TOKEN=your_google_gemini_api_key
SERPER_API_KEY=your_serper_dev_api_key

# Authentication & Security
ADMIN_PASSWORD=admin_secret_password
AUTH_USERS_JSON={"user":"user_password"}
ADMIN_JWT_SECRET=super_secret_admin_jwt_signing_key_32bytes_long
JWT_SECRET=super_secret_user_jwt_signing_key_32bytes_long
```

### 3. Bootstrap Dependencies

#### Python Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r src/python/ai_querier/requirements.txt
pip install -r src/python/ai_scorer/requirements.txt
pip install -r src/python/web_crawler/requirements.txt
```

#### Frontend Dependencies
```bash
cd src/js/coverletter-frontend
npm ci
cd ../../..
```

#### Go Dependencies
```bash
cd src/go/cmd/api
go mod download
cd ../../..
```

### 4. Launch the Local Development Stack

Run the automated dev stack launcher, which builds the local images (including the Ollama runtime) and starts Docker Compose:

```bash
./docker/lib/createLocalDevStack.sh
```

Alternatively, launch via Docker Compose directly:

```bash
docker compose --env-file ~/.docker/coverletter-dev.env -f docker/lib/stack-dev.yml up -d
```

### Local Service Endpoints

| Service | URL | Description |
|---|---|---|
| **Web UI** | `http://localhost:80/` | Angular Frontend via Traefik |
| **Direct Frontend Dev** | `http://localhost:4200/` | Angular Dev Server (if running `npm start`) |
| **Backend API** | `http://localhost:80/api/` | REST API via Traefik |
| **Direct API** | `http://localhost:8080/` | Go Gin API Port |
| **Traefik Dashboard** | `http://localhost:8888/dashboard/` | Proxy and routing monitoring |
| **Mongo Express** | `http://localhost:8081/` | Web GUI for MongoDB |
| **Ollama Service** | `http://localhost:11434/` | Local LLM inference server |

---

## Testing & Quality Verification

This repository uses a two-tier test gate strategy defined in `scripts/test-gate.sh` and `docs/developer-cycle.md`.

### 1. Fast Gate (`make test-fast`)
Designed for rapid feedback during development and pre-commit checks:
- Go unit tests and static analysis (`go vet ./...` and `go test ./...`)
- Python unit tests (`unittest discover` for `src/python` and `tests/`)
- Angular TypeScript check (`tsc --noEmit`) and Karma unit tests with `ChromeHeadless`

```bash
make test-fast
```

### 2. Full Gate (`make test-full`)
Used by CI and presubmit checks before merging or promoting builds. Runs everything in the fast gate followed by all containerized end-to-end (E2E) suites in isolated Docker environments:

```bash
make test-full
```

*Note: E2E tests spin up full Docker Compose environments. Use persistent logging if running manually:*
```bash
bash scripts/test-gate.sh --mode full 2>&1 | tee /tmp/cover-letter-test-full.log
```

### 3. Running Individual E2E Suites
You can run any specific E2E test suite locally without executing the full gate:

```bash
# List all available suites
bash tests/e2e/run_e2e_suites.sh --list

# Run a specific suite (e.g. start_crawl or ai_scorer)
bash tests/e2e/run_e2e_suites.sh start_crawl
```

Available E2E suites:
- `coverletter`: Tests cover letter generation and Gemini prompt workflows.
- `ai_scorer`: Tests preference scoring against the local Ollama service.
- `start_crawl`: Tests identity-scoped crawler triggers, queue dispatch, and progress reporting.
- `ai_scorer_crawler`: Tests end-to-end integration between crawler outputs and scoring queue.
- `workflow1`: Tests multi-crawler fanout, ATS extraction, and enrichment.
- `post_crawl_scoring`: Tests post-discovery automated scoring triggers.

---

## AI Scorer: Fine-Tuning & Model Packaging

The scoring engine relies on a local Ollama model fine-tuned for structured, bias-resistant criteria evaluation.

### Training CLI Workflows
The `Makefile` exposes standardized training workflows:

```bash
# Verify dataset prerequisites and runtime hardware
make training-preflight
make training-runtime

# Execute supervised fine-tuning run
make training-train RUN_ID=2026-run1

# Validate checkpoints and merge LoRA adapters
make training-validate-checkpoints RUN_ID=2026-run1
make training-merge RUN_ID=2026-run1

# Package into GGUF and test against the evaluation gate
make training-package RUN_ID=2026-run1 CANDIDATE_MODEL=ai-scorer-qwen25:candidate CONVERT_SCRIPT=path/to/convert.py
make training-eval-gate RUN_ID=2026-run1 CANDIDATE_MODEL=ai-scorer-qwen25:candidate
```

### Regression Evaluation
Evaluate a candidate model against checked-in golden evaluation datasets:

```bash
make eval-scorer CANDIDATE_MODEL=ai-scorer-qwen25:candidate
```

### Publishing the OCI Model Store
Promoted Ollama models are distributed as data-only OCI images so production nodes (Raspberry Pis) do not compile or download models over the public internet during deployment:

```bash
bash scripts/publish-ollama-model.sh
```
The script outputs an immutable digest (`@sha256:...`) which is committed into `CICD.sh` and `docker/lib/createLocalDevStack.sh`.

---

## Production Deployment

### Docker Swarm Architecture
Production runs on a 3-node Docker Swarm cluster of Raspberry Pi 5 boards (`raspberrypi5`, `raspberrypi6`, `raspberrypi7`) running 64-bit Linux (`linux/arm64`):

- **MongoDB Replica Set**: Runs `coverletter-rs` across the three nodes with node-local SSD mounts (`/mnt/disk-usb1/docker/coverletter/mongo`).
- **High-Availability Redis**: Master-replica Redis deployment monitored by a 3-node Sentinel quorum (`sentinel1`, `sentinel2`, `sentinel3`) with automated master failover.
- **Swarm Secrets**: Critical credentials are injected into services using Swarm secrets:
  - `COVERLETTER_GEMINI_TOKEN`
  - `COVERLETTER_ADMIN_PASSWORD`
  - `COVERLETTER_AUTH_USERS_JSON`
  - `COVERLETTER_ADMIN_JWT_SECRET`
  - `COVERLETTER_JWT_SECRET`
  - `COVERLETTER_SERPER_API_KEY`

### Deployment Pipeline (`CICD.sh`)
Deployments are managed by `CICD.sh`, which:
1. Validates the immutable Ollama model store image.
2. Builds candidate images for all services.
3. Performs a smoke test against the Ollama runtime.
4. Executes the full test suite (`scripts/test-gate.sh --mode full`).
5. Promotes candidate images to the target deployment tags upon passing all gates.
6. Deploys the stack via `docker stack deploy -c docker/prod/stack.yml coverletter`.

---

## Specifications & Contract Index

Every domain and service in this repository follows strict contracts to prevent drift across Go, Python, and TypeScript implementations:

| Domain / Service | Specification Document |
|---|---|
| **Product & System Spec** | [`spec.md`](spec.md) |
| **Go REST API Index** | [`src/go/cmd/api/SPEC.md`](src/go/cmd/api/SPEC.md) |
| ├── Authentication Domain | [`src/go/cmd/api/domains/auth/SPEC.md`](src/go/cmd/api/domains/auth/SPEC.md) |
| ├── Companies Domain | [`src/go/cmd/api/domains/companies/SPEC.md`](src/go/cmd/api/domains/companies/SPEC.md) |
| ├── Cover Letters Domain | [`src/go/cmd/api/domains/coverletters/SPEC.md`](src/go/cmd/api/domains/coverletters/SPEC.md) |
| ├── Crawls Domain | [`src/go/cmd/api/domains/crawls/SPEC.md`](src/go/cmd/api/domains/crawls/SPEC.md) |
| ├── Fields Domain | [`src/go/cmd/api/domains/fields/SPEC.md`](src/go/cmd/api/domains/fields/SPEC.md) |
| ├── Identities Domain | [`src/go/cmd/api/domains/identities/SPEC.md`](src/go/cmd/api/domains/identities/SPEC.md) |
| ├── Jobs Domain | [`src/go/cmd/api/domains/jobs/SPEC.md`](src/go/cmd/api/domains/jobs/SPEC.md) |
| └── Recipients Domain | [`src/go/cmd/api/domains/recipients/SPEC.md`](src/go/cmd/api/domains/recipients/SPEC.md) |
| **Frontend Application** | [`src/js/coverletter-frontend/src/app/SPEC.md`](src/js/coverletter-frontend/src/app/SPEC.md) |
| **AI Querier (Gemini)** | [`src/python/ai_querier/SPEC.md`](src/python/ai_querier/SPEC.md) |
| **AI Scorer (Ollama)** | [`src/python/ai_scorer/SPEC.md`](src/python/ai_scorer/SPEC.md) |
| **Web Crawler & Workers**| [`src/python/web_crawler/SPEC.md`](src/python/web_crawler/SPEC.md) |
| ├── Crawler Dispatcher | [`src/python/web_crawler/dispatcher/SPEC.md`](src/python/web_crawler/dispatcher/SPEC.md) |
| └── Crawler Scheduler | [`src/python/web_crawler/scheduler/SPEC.md`](src/python/web_crawler/scheduler/SPEC.md) |
| **Canonical Data Schema** | [`src/go/internal/proto/common/common.proto`](src/go/internal/proto/common/common.proto) |
| **Production Runbooks** | [`docker/prod/README.md`](docker/prod/README.md) & [`docker/ollama/README.md`](docker/ollama/README.md) |
| **Developer Test Cycle**| [`docs/developer-cycle.md`](docs/developer-cycle.md) |
