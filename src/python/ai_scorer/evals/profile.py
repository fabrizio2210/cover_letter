"""Evaluation profiles and reproducible scorer-pipeline configuration."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

from src.python.ai_scorer.scoring_config import (
    BM25_B,
    BM25_K1,
    DEFAULT_CANDIDATE_QUERY_PREFIX,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EVIDENCE_SCOPE_MODEL,
    DEFAULT_EVIDENCE_SELECTOR_MODEL,
    DEFAULT_METADATA_NORMALIZATION_MODEL,
    DEFAULT_PREFERENCE_NORMALIZATION_MODEL,
    DEFAULT_QUERY_EXPANSION_MODEL,
    DEFAULT_RERANKING_MODEL,
    DEFAULT_TITLE_NORMALIZATION_MODEL,
    PRODUCTION_PIPELINE_ENVIRONMENT,
    PRODUCTION_EVAL_MODEL_PINS,
    SCORING_PIPELINE_IMPLEMENTATION_VERSION,
    SECONDARY_EMBEDDING_MODEL,
    SNIPPET_CANDIDATE_K,
    SNIPPET_POINTWISE_CASCADE_K,
    SNIPPET_PROBE_K,
    SNIPPET_RERANKED_TOP_K,
    SNIPPET_RETRIEVER_K,
    SNIPPET_RRF_RANK_CONSTANT,
    SNIPPET_TOP_K,
    SNIPPET_WINDOW_SIZE,
    STORED_EVAL_REFERENCE_MODEL,
)


PRODUCTION_PROFILE_NAME = "production"
PRODUCTION_PROFILE_VERSION = "2"
PRODUCTION_REFERENCE_MODEL = STORED_EVAL_REFERENCE_MODEL
PRODUCTION_PROFILE_DEFAULTS: dict[str, str] = {
    "EVAL_WITH_SYSTEM_PROMPT": "true",
    **PRODUCTION_PIPELINE_ENVIRONMENT,
    **PRODUCTION_EVAL_MODEL_PINS,
}

_TRUE_VALUES = {"1", "true", "yes"}
_FALSE_VALUES = {"0", "false", "no"}
_IMPLEMENTATION_SOURCE_FILES = (
    "ai_scorer.py",
    "description_normalization.py",
    "scoring_config.py",
    "scoring_prompt.py",
    "evals/runner.py",
)


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _raw(source: Mapping[str, str], key: str) -> str:
    return str(source.get(key, "") or "")


def _configured(source: Mapping[str, str], key: str, default: str = "") -> str:
    return _raw(source, key).strip() or default


def _flag(source: Mapping[str, str], key: str, *, default: bool = False) -> bool:
    if key not in source:
        return default
    return _raw(source, key).lower() in _TRUE_VALUES


def _optional_flag(source: Mapping[str, str], key: str) -> bool | None:
    configured = _raw(source, key).strip().lower()
    if not configured:
        return None
    if configured in _TRUE_VALUES:
        return True
    if configured in _FALSE_VALUES:
        return False
    raise ValueError(f"{key} must be true or false")


def _optional_int(source: Mapping[str, str], key: str) -> int | None:
    configured = _raw(source, key).strip()
    if not configured:
        return None
    value = int(configured)
    if key == "SCORING_NUM_PREDICT" and value <= 0:
        raise ValueError("SCORING_NUM_PREDICT must be greater than zero")
    return value


def _effective_pipeline(source: Mapping[str, str]) -> dict[str, Any]:
    embedding_model = _configured(source, "EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    configured_temperature = _configured(source, "SCORING_TEMPERATURE")
    return {
        "EVAL_WITH_SYSTEM_PROMPT": _flag(
            source,
            "EVAL_WITH_SYSTEM_PROMPT",
            default=True,
        ),
        "QUERY_EXPANSION_MODEL": _configured(
            source,
            "QUERY_EXPANSION_MODEL",
            DEFAULT_QUERY_EXPANSION_MODEL,
        ),
        "QUERY_EXPANSION_PROFILE": _configured(source, "QUERY_EXPANSION_PROFILE"),
        "METADATA_NORMALIZATION_MODEL": _configured(
            source,
            "METADATA_NORMALIZATION_MODEL",
            DEFAULT_METADATA_NORMALIZATION_MODEL,
        ),
        "NORMALIZE_JOB_LOCATION": _flag(source, "NORMALIZE_JOB_LOCATION"),
        "NORMALIZE_JOB_TITLE": _flag(source, "NORMALIZE_JOB_TITLE"),
        "TITLE_NORMALIZATION_MODEL": _configured(
            source,
            "TITLE_NORMALIZATION_MODEL",
            DEFAULT_TITLE_NORMALIZATION_MODEL,
        ),
        "EXPLICIT_REMOTE_LOCATION": _flag(source, "EXPLICIT_REMOTE_LOCATION"),
        "NORMALIZE_PREFERENCE_GUIDANCE": _flag(
            source,
            "NORMALIZE_PREFERENCE_GUIDANCE",
        ),
        "PREFERENCE_NORMALIZATION_MODEL": _configured(
            source,
            "PREFERENCE_NORMALIZATION_MODEL",
            DEFAULT_PREFERENCE_NORMALIZATION_MODEL,
        ),
        "EVIDENCE_SCOPE_ROUTING": _configured(source, "EVIDENCE_SCOPE_ROUTING"),
        "EVIDENCE_SCOPE_MODEL": _configured(
            source,
            "EVIDENCE_SCOPE_MODEL",
            DEFAULT_EVIDENCE_SCOPE_MODEL,
        ),
        "EMBEDDING_MODEL": embedding_model,
        "CANDIDATE_EMBEDDING_MODEL": _configured(
            source,
            "CANDIDATE_EMBEDDING_MODEL",
            embedding_model,
        ),
        "CANDIDATE_QUERY_PREFIX": _raw(source, "CANDIDATE_QUERY_PREFIX")
        if "CANDIDATE_QUERY_PREFIX" in source
        else DEFAULT_CANDIDATE_QUERY_PREFIX,
        "CANDIDATE_RETRIEVAL_MODE": _configured(
            source,
            "CANDIDATE_RETRIEVAL_MODE",
        ),
        "EVIDENCE_FUSION_MODE": _configured(source, "EVIDENCE_FUSION_MODE"),
        "RERANKING_MODEL": _configured(
            source,
            "RERANKING_MODEL",
            DEFAULT_RERANKING_MODEL,
        ),
        "LATE_INTERACTION_RERANK_MODEL": _configured(
            source,
            "LATE_INTERACTION_RERANK_MODEL",
        ),
        "RERANK_WITH_JOB_CONTEXT": _flag(source, "RERANK_WITH_JOB_CONTEXT"),
        "EVIDENCE_SELECTION_MODE": _configured(
            source,
            "EVIDENCE_SELECTION_MODE",
        ),
        "EVIDENCE_SELECTOR_MODEL": _configured(
            source,
            "EVIDENCE_SELECTOR_MODEL",
            DEFAULT_EVIDENCE_SELECTOR_MODEL,
        ),
        "EVIDENCE_VIEW_ROUTING": _configured(source, "EVIDENCE_VIEW_ROUTING"),
        "FINAL_ORDER_ROUTING": _configured(source, "FINAL_ORDER_ROUTING"),
        "PRESERVE_CANDIDATE_ORDER": _flag(source, "PRESERVE_CANDIDATE_ORDER"),
        "SCORER_POINTWISE_RERANK": _flag(source, "SCORER_POINTWISE_RERANK"),
        "SCORER_POINTWISE_RERANK_CASCADE": _flag(
            source,
            "SCORER_POINTWISE_RERANK_CASCADE",
        ),
        "POINTWISE_RERANKER_MODEL": _configured(
            source,
            "POINTWISE_RERANKER_MODEL",
        ),
        "POINTWISE_USE_LOGPROBS": _flag(source, "POINTWISE_USE_LOGPROBS"),
        "POINTWISE_RANK_MODE": _configured(source, "POINTWISE_RANK_MODE"),
        "SCORING_TEMPERATURE": float(configured_temperature)
        if configured_temperature
        else 0.0,
        "SCORING_SEED": _optional_int(source, "SCORING_SEED"),
        "SCORING_NUM_PREDICT": _optional_int(source, "SCORING_NUM_PREDICT"),
        "SCORING_THINK": _optional_flag(source, "SCORING_THINK"),
        "AUXILIARY_THINK": _optional_flag(source, "AUXILIARY_THINK"),
    }


def _implementation_configuration() -> dict[str, Any]:
    scorer_directory = Path(__file__).resolve().parents[1]
    source_hashes = {
        relative_path: hashlib.sha256(
            (scorer_directory / relative_path).read_bytes()
        ).hexdigest()
        for relative_path in _IMPLEMENTATION_SOURCE_FILES
    }
    return {
        "version": SCORING_PIPELINE_IMPLEMENTATION_VERSION,
        "source_sha256": source_hashes,
        "retrieval": {
            "embedding_model_secondary": SECONDARY_EMBEDDING_MODEL,
            "snippet_top_k": SNIPPET_TOP_K,
            "candidate_k": SNIPPET_CANDIDATE_K,
            "retriever_k": SNIPPET_RETRIEVER_K,
            "reranked_top_k": SNIPPET_RERANKED_TOP_K,
            "pointwise_probe_k": SNIPPET_PROBE_K,
            "pointwise_cascade_k": SNIPPET_POINTWISE_CASCADE_K,
            "window_size": SNIPPET_WINDOW_SIZE,
            "rrf_rank_constant": SNIPPET_RRF_RANK_CONSTANT,
            "bm25_k1": BM25_K1,
            "bm25_b": BM25_B,
        },
    }


def pipeline_fingerprint(configuration: Mapping[str, Any]) -> str:
    """Hash the stored fields that define scorer-pipeline behavior."""
    return _stable_digest(
        {
            "profile": configuration.get("profile"),
            "profile_version": configuration.get("profile_version"),
            "pipeline": configuration.get("pipeline"),
            "implementation": configuration.get("implementation"),
        }
    )


def fixture_fingerprint(cases: Sequence[Any]) -> str:
    """Hash ordered scorer inputs and golden labels used by the metric run."""
    return _stable_digest(
        [
            {
                "case_id": case.case_id,
                "title": case.title,
                "description": case.description,
                "location": case.location,
                "preference_key": case.preference_key,
                "preference_guidance": case.preference_guidance,
                "expected_score": case.expected_score,
                "expected_score_available": case.expected_score_available,
            }
            for case in cases
        ]
    )


def metrics_fingerprint(reference_metrics: Mapping[str, Any]) -> str:
    """Hash the exact stored metrics consumed by the regression gate."""
    return _stable_digest(reference_metrics)


def apply_profile(
    profile_name: str,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Apply profile defaults without replacing explicit experiment settings."""
    if profile_name != PRODUCTION_PROFILE_NAME:
        raise ValueError(f"Unsupported eval profile: {profile_name!r}")
    target = os.environ if environ is None else environ
    for key, value in PRODUCTION_PROFILE_DEFAULTS.items():
        target.setdefault(key, value)


def build_run_configuration(
    profile_name: str,
    candidate_model: str,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return resolved pipeline behavior and its reproducible fingerprint."""
    if profile_name != PRODUCTION_PROFILE_NAME:
        raise ValueError(f"Unsupported eval profile: {profile_name!r}")
    source = os.environ if environ is None else environ
    configuration = {
        "profile": profile_name,
        "profile_version": PRODUCTION_PROFILE_VERSION,
        "pipeline": _effective_pipeline(source),
        "implementation": _implementation_configuration(),
    }
    return {
        **configuration,
        "candidate_model": candidate_model,
        "pipeline_fingerprint": pipeline_fingerprint(configuration),
    }


def reference_configuration_mismatches(
    reference_run: Mapping[str, Any],
    run_configuration: Mapping[str, Any],
    current_fixture_fingerprint: str,
    reference_metrics: Mapping[str, Any],
) -> list[str]:
    """Explain why stored metrics are not a valid baseline for this run."""
    mismatches: list[str] = []
    if not isinstance(reference_run, Mapping):
        return ["stored reference provenance is missing or malformed"]
    if reference_run.get("profile") != PRODUCTION_PROFILE_NAME:
        mismatches.append("stored reference does not use the production profile")
    if reference_run.get("profile_version") != PRODUCTION_PROFILE_VERSION:
        mismatches.append("stored reference profile version is stale")
    if reference_run.get("model") != PRODUCTION_REFERENCE_MODEL:
        mismatches.append("stored reference model is not the configured reference model")
    if not isinstance(reference_run.get("pipeline"), Mapping):
        mismatches.append("stored reference pipeline is missing or malformed")
    if not isinstance(reference_run.get("implementation"), Mapping):
        mismatches.append("stored reference implementation is missing or malformed")

    stored_fingerprint = reference_run.get("pipeline_fingerprint")
    if not isinstance(stored_fingerprint, str) or not stored_fingerprint:
        mismatches.append("stored reference pipeline fingerprint is missing")
    elif pipeline_fingerprint(reference_run) != stored_fingerprint:
        mismatches.append("stored reference pipeline provenance was modified")
    elif stored_fingerprint != run_configuration.get("pipeline_fingerprint"):
        mismatches.append("stored and current pipeline configurations differ")

    stored_fixture_fingerprint = reference_run.get("fixture_fingerprint")
    if not isinstance(stored_fixture_fingerprint, str) or not stored_fixture_fingerprint:
        mismatches.append("stored reference fixture fingerprint is missing")
    elif stored_fixture_fingerprint != current_fixture_fingerprint:
        mismatches.append("stored reference fixtures or golden labels changed")

    stored_metrics_fingerprint = reference_run.get("metrics_fingerprint")
    if not isinstance(reference_metrics, Mapping):
        mismatches.append("stored reference metrics are missing or malformed")
    elif not isinstance(stored_metrics_fingerprint, str) or not stored_metrics_fingerprint:
        mismatches.append("stored reference metrics fingerprint is missing")
    elif stored_metrics_fingerprint != metrics_fingerprint(reference_metrics):
        mismatches.append("stored reference metrics were modified")
    return mismatches


def reference_matches_configuration(
    reference_run: Mapping[str, Any],
    run_configuration: Mapping[str, Any],
    current_fixture_fingerprint: str,
    reference_metrics: Mapping[str, Any],
) -> bool:
    """Return whether stored metrics are a valid baseline for this run."""
    return not reference_configuration_mismatches(
        reference_run,
        run_configuration,
        current_fixture_fingerprint,
        reference_metrics,
    )
