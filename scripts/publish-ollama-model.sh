#!/usr/bin/env bash
set -euo pipefail

readonly DEFAULT_MODEL_NAME="ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16"
readonly DEFAULT_MODEL_IMAGE="fabrizio2210/coverletter-ollama-model:fp-v2-balanced-response-cp200-f16"
readonly DEFAULT_MODEL_PLATFORMS="linux/amd64,linux/arm64"

model_name="${1:-$DEFAULT_MODEL_NAME}"
model_image="${2:-$DEFAULT_MODEL_IMAGE}"
model_platforms="${MODEL_PLATFORMS:-$DEFAULT_MODEL_PLATFORMS}"
source_models="${OLLAMA_MODELS:-/usr/share/ollama/.ollama/models}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for command_name in docker jq sha256sum; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is not installed: $command_name" >&2
    exit 2
  fi
done

buildx_command=(docker buildx)
if [[ -n "${BUILDX_BIN:-}" ]]; then
  if [[ ! -x "$BUILDX_BIN" ]]; then
    echo "BUILDX_BIN is not executable: $BUILDX_BIN" >&2
    exit 2
  fi
  buildx_command=("$BUILDX_BIN")
fi

if ! "${buildx_command[@]}" version >/dev/null 2>&1; then
  echo "Docker Buildx is required to publish the amd64/arm64 model artifact." >&2
  echo "Set BUILDX_BIN to a standalone Buildx plugin binary when necessary." >&2
  exit 2
fi

if [[ "$model_name" != *:* ]]; then
  echo "Model name must include a tag: $model_name" >&2
  exit 2
fi

model_repository="${model_name%%:*}"
model_tag="${model_name#*:}"
if [[ "$model_repository" == */* || -z "$model_repository" || -z "$model_tag" ]]; then
  echo "Expected a local Ollama model name in repository:tag form: $model_name" >&2
  exit 2
fi

source_manifest="$source_models/manifests/registry.ollama.ai/library/$model_repository/$model_tag"
if [[ ! -f "$source_manifest" ]]; then
  echo "Ollama model manifest not found: $source_manifest" >&2
  exit 2
fi

staging_dir="$(mktemp -d /tmp/coverletter-ollama-model.XXXXXX)"
cleanup() {
  if [[ -n "${staging_dir:-}" && "$staging_dir" == /tmp/coverletter-ollama-model.* ]]; then
    rm -rf -- "$staging_dir"
  fi
}
trap cleanup EXIT

destination_manifest="$staging_dir/models/manifests/registry.ollama.ai/library/$model_repository/$model_tag"
install -D -m 0644 "$source_manifest" "$destination_manifest"

mapfile -t referenced_digests < <(
  jq -r '.config.digest, (.layers[].digest)' "$source_manifest"
)

if [[ "${#referenced_digests[@]}" -eq 0 ]]; then
  echo "The Ollama manifest does not reference any blobs: $source_manifest" >&2
  exit 2
fi

for digest_reference in "${referenced_digests[@]}"; do
  if [[ ! "$digest_reference" =~ ^sha256:([0-9a-f]{64})$ ]]; then
    echo "Unsupported blob digest in Ollama manifest: $digest_reference" >&2
    exit 2
  fi

  digest="${BASH_REMATCH[1]}"
  source_blob="$source_models/blobs/sha256-$digest"
  destination_blob="$staging_dir/models/blobs/sha256-$digest"

  if [[ ! -f "$source_blob" ]]; then
    echo "Referenced Ollama blob not found: $source_blob" >&2
    exit 2
  fi

  actual_digest="$(sha256sum "$source_blob" | awk '{print $1}')"
  if [[ "$actual_digest" != "$digest" ]]; then
    echo "Ollama blob checksum mismatch: $source_blob" >&2
    echo "Expected $digest, got $actual_digest" >&2
    exit 2
  fi

  install -D -m 0644 "$source_blob" "$destination_blob"
done

manifest_digest="$(sha256sum "$source_manifest" | awk '{print $1}')"

echo "[model-publish] model=$model_name"
echo "[model-publish] source=$source_models"
echo "[model-publish] image=$model_image"
echo "[model-publish] platforms=$model_platforms"
echo "[model-publish] manifest_sha256=$manifest_digest"

"${buildx_command[@]}" build \
  --platform "$model_platforms" \
  --build-arg "MODEL_NAME=$model_name" \
  --build-arg "MODEL_MANIFEST_SHA256=$manifest_digest" \
  --file "$repo_root/docker/ollama/Dockerfile-model-store" \
  --tag "$model_image" \
  --provenance=false \
  --push \
  "$staging_dir"

inspect_output="$("${buildx_command[@]}" imagetools inspect "$model_image")"
printf '%s\n' "$inspect_output"

image_digest="$(printf '%s\n' "$inspect_output" | awk '/^Digest:/ {print $2; exit}')"
if [[ ! "$image_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Failed to resolve a registry digest for $model_image" >&2
  exit 2
fi

echo "[model-publish] Set OLLAMA_MODEL_NAME=$model_name and use this immutable"
echo "[model-publish] OLLAMA_MODEL_IMAGE in CICD.sh and docker/lib/createLocalDevStack.sh:"
echo "${model_image%%:*}@$image_digest"
