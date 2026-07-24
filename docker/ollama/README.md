# Custom Ollama runtime

Production uses a custom Ollama image containing the promoted scorer model and
the `qwen2.5:1.5b` auxiliary model used by the Attempt 103 scorer setup. The
promoted model is distributed through Docker Hub as a data-only OCI image so the
Raspberry Pi does not need access to local training artifacts.

## Publish a model artifact

The publisher exports the exact registered Ollama manifest and every blob it
references. It intentionally does not rebuild the model from the GGUF, because
the registered model store is the artifact that has passed evaluation.

```bash
bash scripts/publish-ollama-model.sh
```

The default publication contains:

- `ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16`
- image tag
  `fabrizio2210/coverletter-ollama-model:fp-v2-balanced-response-cp200-f16`
- OCI manifests for `linux/amd64` and `linux/arm64`

The command prints an immutable `name@sha256:...` reference when the push
finishes. Commit that complete reference as `OLLAMA_MODEL_IMAGE` in both
`CICD.sh` and `docker/lib/createLocalDevStack.sh`. Keep their
`OLLAMA_MODEL_NAME` values aligned with the model selectors in
`docker/prod/stack.yml`, `docker/lib/stack-dev.yml`, and
`scripts/smoke-ollama-image.sh`. Do not deploy from the mutable model tag.

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

The build adds `qwen2.5:1.5b` to the copied promoted model store. The resulting
`coverletter-ollama` image starts `ollama serve` directly and never runs
`ollama pull` during container startup.

## Update the model

1. Register and evaluate the new model locally.
2. Publish its clean model-store image.
3. Copy the printed immutable image reference into `CICD.sh` and
   `docker/lib/createLocalDevStack.sh`.
4. Update `OLLAMA_MODEL_NAME` in both build scripts, `OLLAMA_MODEL` in both
   stack files, and `DEFAULT_MODEL_NAME` in the smoke script.
5. Update the publisher defaults and this document when the default model name
   changes.
6. Run the packaging tests, commit the change, and run `CICD.sh`.
7. Let the native candidate smoke test pass before promotion and deployment.
