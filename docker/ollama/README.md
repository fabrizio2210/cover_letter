# Custom Ollama runtime

Production uses a custom Ollama image containing the promoted scorer model. The
model is distributed through Docker Hub as a data-only OCI image so the
Raspberry Pi does not need access to local training artifacts or an Ollama model
registry at deployment time.

## Publish a model artifact

The publisher exports the exact registered Ollama manifest and every blob it
references. It intentionally does not rebuild the model from the GGUF, because
the registered model store is the artifact that has passed evaluation.

```bash
bash scripts/publish-ollama-model.sh
```

The default publication contains:

- `ai-scorer-qwen25:fp-v2-balanced-response-cp200-q4_k_m`
- image tag
  `fabrizio2210/coverletter-ollama-model:fp-v2-balanced-response-cp200-q4_k_m`
- OCI manifests for `linux/amd64` and `linux/arm64`

The command prints an immutable `name@sha256:...` reference when the push
finishes. Commit that complete reference as `OLLAMA_MODEL_IMAGE` in `CICD.sh`.
Do not deploy from the mutable model tag.

The publisher disables generated provenance attestations for this data-only
image so identical model-store content produces a stable multi-platform index
digest across a retry after a failed registry upload.

`OLLAMA_MODELS` may point at a non-default local model store. The publisher
requires Docker Buildx, `jq`, and authenticated push access to Docker Hub. If
Buildx is available only as a standalone binary, set `BUILDX_BIN` to its path.

## Deployment build

`CICD.sh` runs natively on the build host. It does not pass a Docker platform
option. On the ARM64 Raspberry Pi, Docker selects the ARM64 manifest of both the
data-only model image and the pinned Ollama runtime image.

The resulting `coverletter-ollama` image starts `ollama serve` directly. It
must never run `ollama pull` during container startup.

## Update the model

1. Register and evaluate the new model locally.
2. Publish its clean model-store image.
3. Copy the printed immutable image reference into `CICD.sh`.
4. Commit the change and run `CICD.sh`.
5. Let the native candidate smoke test pass before promotion and deployment.
