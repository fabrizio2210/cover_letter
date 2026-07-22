# syntax=docker/dockerfile:1

ARG MODEL_IMAGE
ARG OLLAMA_IMAGE=ollama/ollama:0.32.0

FROM ${MODEL_IMAGE} AS model_store

FROM ${OLLAMA_IMAGE}

COPY --from=model_store /models/ /root/.ollama/models/

ENV OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_MODELS=/root/.ollama/models

EXPOSE 11434

ENTRYPOINT ["/bin/ollama"]
CMD ["serve"]
