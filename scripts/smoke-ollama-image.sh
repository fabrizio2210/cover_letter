#!/usr/bin/env bash
set -euo pipefail

readonly DEFAULT_MODEL_NAME="ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16"
readonly DEFAULT_AUXILIARY_MODEL="qwen2.5:1.5b"

image_reference="${1:-}"
model_name="${2:-$DEFAULT_MODEL_NAME}"
auxiliary_model="${3:-$DEFAULT_AUXILIARY_MODEL}"
startup_timeout_seconds="${OLLAMA_SMOKE_STARTUP_TIMEOUT_SECONDS:-120}"
inference_timeout_seconds="${OLLAMA_SMOKE_INFERENCE_TIMEOUT_SECONDS:-600}"

if [[ -z "$image_reference" ]]; then
  echo "Usage: scripts/smoke-ollama-image.sh <image-reference> [model-name] [auxiliary-model]" >&2
  exit 2
fi

container_name="coverletter-ollama-smoke-$$"
container_id=""
cleanup() {
  if [[ -n "$container_id" ]]; then
    docker rm -f "$container_id" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[ollama-smoke] Starting $image_reference"
container_id="$(docker run --detach --name "$container_name" "$image_reference")"

deadline=$((SECONDS + startup_timeout_seconds))
model_list=""
while (( SECONDS < deadline )); do
  if model_list="$(docker exec \
      --env OLLAMA_HOST=127.0.0.1:11434 \
      "$container_id" ollama list 2>/dev/null)"; then
    break
  fi
  sleep 2
done

if [[ -z "$model_list" ]]; then
  echo "Ollama did not become ready within ${startup_timeout_seconds}s" >&2
  docker logs "$container_id" >&2 || true
  exit 1
fi

printf '%s\n' "$model_list"
for expected_model in "$model_name" "$auxiliary_model"; do
  if ! printf '%s\n' "$model_list" | awk 'NR > 1 {print $1}' | grep -Fx "$expected_model" >/dev/null; then
    echo "Expected model is not installed: $expected_model" >&2
    exit 1
  fi
done

model_details="$(docker exec \
  --env OLLAMA_HOST=127.0.0.1:11434 \
  "$container_id" ollama show "$model_name")"
printf '%s\n' "$model_details"

prompt="Preference Guidance: Remote work is required. Job Title: Remote Platform Engineer. Job Location: remote. Relevant Context Snippets: This is a fully remote position."
response="$(timeout "$inference_timeout_seconds" docker exec \
  --env OLLAMA_HOST=127.0.0.1:11434 \
  "$container_id" ollama run "$model_name" "$prompt")"
response="$(printf '%s' "$response" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

echo "[ollama-smoke] response=$response"
if [[ ! "$response" =~ ^([0-5]|N/A)$ ]]; then
  echo "Unexpected scorer response: $response" >&2
  exit 1
fi

echo "[ollama-smoke] Passed"
