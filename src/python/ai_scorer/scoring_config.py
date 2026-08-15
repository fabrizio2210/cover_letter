"""Shared constants for scorer runtime behavior and production deployment parity."""

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
SECONDARY_EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5-Q"
SNIPPET_TOP_K = 2
SNIPPET_CANDIDATE_K = 10
SNIPPET_RETRIEVER_K = 20
SNIPPET_RERANKED_TOP_K = 2
SNIPPET_PROBE_K = 6
SNIPPET_WINDOW_SIZE = 1
SNIPPET_RRF_RANK_CONSTANT = 60
SNIPPET_POINTWISE_CASCADE_K = 4
BM25_K1 = 1.5
BM25_B = 0.75
DEFAULT_QUERY_EXPANSION_MODEL = "qwen2.5:1.5b"
DEFAULT_RERANKING_MODEL = "jinaai/jina-reranker-v1-tiny-en"
DEFAULT_EVIDENCE_SELECTOR_MODEL = "qwen2.5:3b"
DEFAULT_METADATA_NORMALIZATION_MODEL = "qwen2.5:1.5b"
DEFAULT_PREFERENCE_NORMALIZATION_MODEL = "qwen2.5:7b"
DEFAULT_EVIDENCE_SCOPE_MODEL = "qwen2.5:7b"
DEFAULT_TITLE_NORMALIZATION_MODEL = "qwen2.5:7b"
DEFAULT_CANDIDATE_QUERY_PREFIX = ""

# Increment when scorer prompts, branching, or retrieval algorithms change in
# a way that invalidates stored evaluation metrics.
SCORING_PIPELINE_IMPLEMENTATION_VERSION = "1"

PRODUCTION_SCORER_MODEL = "ai-scorer-qwen25:fp-v2-balanced-response-cp200-f16"
PRODUCTION_PIPELINE_ENVIRONMENT: dict[str, str] = {
    "QUERY_EXPANSION_MODEL": "qwen2.5:1.5b",
    "METADATA_NORMALIZATION_MODEL": "qwen2.5:1.5b",
    "NORMALIZE_JOB_LOCATION": "true",
    "EXPLICIT_REMOTE_LOCATION": "true",
    "NORMALIZE_PREFERENCE_GUIDANCE": "true",
    "PREFERENCE_NORMALIZATION_MODEL": "qwen2.5:1.5b",
    "EVIDENCE_SCOPE_ROUTING": "llm",
    "EVIDENCE_SCOPE_MODEL": "qwen2.5:1.5b",
}

# In production, pointwise evidence routing falls back to OLLAMA_MODEL. During
# an eval the final scorer is intentionally replaced, so pin this auxiliary
# role to the promoted model to preserve the production evidence-selection path.
PRODUCTION_EVAL_MODEL_PINS: dict[str, str] = {
    "POINTWISE_RERANKER_MODEL": PRODUCTION_SCORER_MODEL,
}
