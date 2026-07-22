# syntax=docker/dockerfile:1

ARG MODEL_IMAGE
ARG OLLAMA_IMAGE=ollama/ollama:0.32.0

FROM ${MODEL_IMAGE} AS model_store

FROM ${OLLAMA_IMAGE}

ARG AUXILIARY_MODEL=qwen2.5:1.5b

COPY --from=model_store /models/ /root/.ollama/models/

ENV OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_MODELS=/root/.ollama/models

# The scorer uses the promoted model for final scoring and the stock model for
# query expansion, metadata/preference normalization, and evidence routing.
# Download the auxiliary model while building so deployment never mutates the
# model store at container startup.
RUN set -eu; \
    server_pid=""; \
    cleanup() { \
      if [ -n "$server_pid" ]; then \
        kill "$server_pid" >/dev/null 2>&1 || true; \
        wait "$server_pid" >/dev/null 2>&1 || true; \
      fi; \
    }; \
    trap cleanup EXIT INT TERM; \
    OLLAMA_HOST=127.0.0.1:11434 /bin/ollama serve >/tmp/ollama-build.log 2>&1 & \
    server_pid=$!; \
    attempts=0; \
    until OLLAMA_HOST=127.0.0.1:11434 /bin/ollama list >/dev/null 2>&1; do \
      attempts=$((attempts + 1)); \
      if [ "$attempts" -ge 120 ]; then \
        cat /tmp/ollama-build.log >&2; \
        exit 1; \
      fi; \
      sleep 1; \
    done; \
    OLLAMA_HOST=127.0.0.1:11434 /bin/ollama pull "$AUXILIARY_MODEL"; \
    cleanup; \
    server_pid=""; \
    trap - EXIT INT TERM; \
    rm -f /tmp/ollama-build.log

EXPOSE 11434

ENTRYPOINT ["/bin/ollama"]
CMD ["serve"]
