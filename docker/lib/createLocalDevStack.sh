#!/usr/bin/env bash
set -euo pipefail

readonly OLLAMA_MODEL_NAME="ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16"
readonly OLLAMA_MODEL_IMAGE="fabrizio2210/coverletter-ollama-model@sha256:ffdc9330119f76a5534645655059948ae5426a5efb127fbe1bf9b27daabbe23f"
readonly OLLAMA_AUXILIARY_MODEL="qwen2.5:1.5b"
readonly OLLAMA_DEV_IMAGE="fabrizio2210/coverletter-ollama-dev"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

echo "[dev] Building Ollama runtime with $OLLAMA_MODEL_NAME and $OLLAMA_AUXILIARY_MODEL"
docker build \
  --tag "$OLLAMA_DEV_IMAGE" \
  --file docker/ollama/Dockerfile-dev \
  --build-arg "MODEL_IMAGE=$OLLAMA_MODEL_IMAGE" \
  --build-arg "AUXILIARY_MODEL=$OLLAMA_AUXILIARY_MODEL" \
  .

docker build -t fabrizio2210/coverletter-ai-querier-dev -f docker/x86_64/Dockerfile-ai-dev .
docker build -t fabrizio2210/coverletter-ai-scorer-dev -f docker/x86_64/Dockerfile-ai-scorer-dev .
docker build -t fabrizio2210/coverletter-web-crawler-dev -f docker/x86_64/Dockerfile-web-crawler-dev .
docker build -t fabrizio2210/coverletter-crawler-ycombinator-dev -f docker/x86_64/Dockerfile-crawler-ycombinator-dev .
docker build -t fabrizio2210/coverletter-crawler-hackernews-dev -f docker/x86_64/Dockerfile-crawler-hackernews-dev .
docker build -t fabrizio2210/coverletter-crawler-ats-job-extraction-dev -f docker/x86_64/Dockerfile-crawler-ats-job-extraction-dev .
docker build -t fabrizio2210/coverletter-crawler-levelsfyi-dev -f docker/x86_64/Dockerfile-crawler-levelsfyi-dev .
docker build -t fabrizio2210/coverletter-crawler-4dayweek-dev -f docker/x86_64/Dockerfile-crawler-4dayweek-dev .
docker build -t fabrizio2210/coverletter-enrichment-ats-enrichment-dev -f docker/x86_64/Dockerfile-enrichment-ats-enrichment-dev .
docker build -t fabrizio2210/coverletter-enrichment-retiring-jobs-dev -f docker/x86_64/Dockerfile-enrichment-retiring-jobs-dev .
docker build -t fabrizio2210/coverletter-api-dev -f docker/x86_64/Dockerfile-api-dev .
docker build -t fabrizio2210/coverletter-frontend-dev -f docker/x86_64/Dockerfile-frontend-dev .

docker compose \
  --env-file "${HOME}/.docker/coverletter-dev.env" \
  --file docker/lib/stack-dev.yml \
  up \
  --force-recreate \
  --remove-orphans \
  --renew-anon-volumes
