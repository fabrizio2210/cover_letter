import copy
import json
import math
import os
import queue
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import ollama
import redis
from bson.objectid import ObjectId
from google.protobuf.json_format import MessageToDict
from google.protobuf.timestamp_pb2 import Timestamp
from pymongo import ASCENDING, MongoClient

from . import common_pb2
from .description_normalization import normalize_description_markdown
from .scoring_prompt import SCORING_SYSTEM_INSTRUCTION


_SCORING_STATUS_BSON: dict[int, str] = {
    common_pb2.SCORING_STATUS_UNSCORED: "unscored",
    common_pb2.SCORING_STATUS_QUEUED: "queued",
    common_pb2.SCORING_STATUS_SCORED: "scored",
    common_pb2.SCORING_STATUS_FAILED: "failed",
    common_pb2.SCORING_STATUS_SKIPPED: "skipped",
}


TERMINAL_PROGRESS_STATUSES = {"completed", "failed"}

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
SECONDARY_EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5-Q"
SNIPPET_TOP_K = 2
SNIPPET_CANDIDATE_K = 10
SNIPPET_RETRIEVER_K = 20
SNIPPET_RERANKED_TOP_K = 2
SNIPPET_PROBE_K = 6
SNIPPET_WINDOW_SIZE = 1
DEFAULT_QUERY_EXPANSION_MODEL = "qwen2.5:1.5b"
DEFAULT_RERANKING_MODEL = "jinaai/jina-reranker-v1-tiny-en"
DEFAULT_EVIDENCE_SELECTOR_MODEL = "qwen2.5:3b"
DEFAULT_METADATA_NORMALIZATION_MODEL = "qwen2.5:1.5b"
DEFAULT_CANDIDATE_QUERY_PREFIX = ""

_EMBEDDING_MODEL_CACHE = {}
_EMBEDDING_MODEL_CACHE_LOCK = threading.Lock()
_QUERY_EXPANSION_CACHE: dict[tuple[str, ...], str] = {}
_QUERY_EXPANSION_CACHE_LOCK = threading.Lock()
_LOCATION_NORMALIZATION_CACHE: dict[tuple[str, str], str] = {}
_LOCATION_NORMALIZATION_CACHE_LOCK = threading.Lock()
_TITLE_NORMALIZATION_CACHE: dict[tuple[str, str], str] = {}
_TITLE_NORMALIZATION_CACHE_LOCK = threading.Lock()
_PREFERENCE_NORMALIZATION_CACHE: dict[tuple[str, ...], str] = {}
_PREFERENCE_NORMALIZATION_CACHE_LOCK = threading.Lock()
_PREFERENCE_FRAGMENT_CACHE: dict[tuple[str, ...], bool] = {}
_PREFERENCE_FRAGMENT_CACHE_LOCK = threading.Lock()
_PREFERENCE_EVIDENCE_SCOPE_CACHE: dict[tuple[str, str], str] = {}
_PREFERENCE_EVIDENCE_SCOPE_CACHE_LOCK = threading.Lock()
_CONTRASTIVE_QUERY_CACHE: dict[tuple[str, str], tuple[str, str]] = {}
_CONTRASTIVE_QUERY_CACHE_LOCK = threading.Lock()
_RERANKING_MODEL_CACHE = {}
_RERANKING_MODEL_CACHE_LOCK = threading.Lock()
_LATE_INTERACTION_MODEL_CACHE = {}
_LATE_INTERACTION_MODEL_CACHE_LOCK = threading.Lock()


def scoring_status_to_bson(status: int) -> str:
    return _SCORING_STATUS_BSON.get(status, "unscored")


def to_string_id(value):
    if value is None:
        return ""
    if isinstance(value, ObjectId):
        return str(value)
    return str(value)


def get_field(obj, field_name, default_value: Any = ""):
    if isinstance(obj, dict):
        return obj.get(field_name, default_value)
    return getattr(obj, field_name, default_value)


def set_field(obj, field_name, value):
    if isinstance(obj, dict):
        obj[field_name] = value
        return
    setattr(obj, field_name, value)


def get_message_id(value):
    if isinstance(value, dict):
        return to_string_id(value.get("_id") if value.get("_id") is not None else value.get("id"))
    return to_string_id(getattr(value, "id", ""))


def timestamp_dict_from_proto(ts: Timestamp):
    return {"seconds": int(ts.seconds), "nanos": int(ts.nanos)}


def now_proto_timestamp():
    ts = Timestamp()
    ts.GetCurrentTime()
    return ts


def job_proto_from_doc(job_doc):
    job_proto = common_pb2.Job(
        id=to_string_id(job_doc.get("_id")),
        company_id=to_string_id(job_doc.get("company_id") if job_doc.get("company_id") is not None else job_doc.get("company")),
        title=str(job_doc.get("title", "")),
        description=str(job_doc.get("description", "")),
        location=str(job_doc.get("location", "")),
        platform=str(job_doc.get("platform", "")),
    )
    return job_proto


def company_proto_from_doc(company_doc):
    company_proto = common_pb2.Company(
        id=to_string_id(company_doc.get("_id")),
        name=str(company_doc.get("name", "")),
        field_id=to_string_id(company_doc.get("field_id") if company_doc.get("field_id") is not None else company_doc.get("field")),
        description=str(company_doc.get("description", "")),
    )
    return company_proto


def identity_proto_from_doc(identity_doc):
    identity_proto = common_pb2.Identity(
        id=to_string_id(identity_doc.get("_id")),
        identity=str(identity_doc.get("identity", "")),
        name=str(identity_doc.get("name", "")),
        description=str(identity_doc.get("description", "")),
        field_id=to_string_id(identity_doc.get("field_id") if identity_doc.get("field_id") is not None else identity_doc.get("field")),
    )

    for preference in identity_doc.get("preferences", []):
        if not isinstance(preference, dict):
            continue
        identity_proto.preferences.append(
            common_pb2.IdentityPreference(
                key=str(preference.get("key", "")),
                guidance=str(preference.get("guidance", "") or preference.get("label", "")),
                weight=float(preference.get("weight", 0) or 0),
                enabled=bool(preference.get("enabled", False)),
            )
        )

    return identity_proto


def now_timestamp_dict():
    now = datetime.now(timezone.utc)
    return {"seconds": int(now.timestamp()), "nanos": 0}


def estimate_identity_scoring_backlog(job_descriptions_col, companies_col, job_preference_scores_col, identity_doc):
    field_ref = identity_doc.get("field")
    if field_ref is None:
        field_ref = identity_doc.get("field_id")

    identity_id = get_message_id(identity_doc)
    if not identity_id:
        return 0

    if field_ref is None:
        return 0

    field_candidates = [field_ref]
    field_object_id = parse_object_id(field_ref)
    if field_object_id is not None:
        field_candidates.append(field_object_id)
        field_candidates.append(str(field_object_id))

    companies_cursor = companies_col.find(
        {
            "$or": [
                {"field": {"$in": field_candidates}},
                {"field_id": {"$in": field_candidates}},
            ]
        },
        {"_id": 1},
    )

    company_candidates = []
    for company in companies_cursor:
        company_id = company.get("_id")
        if company_id is None:
            continue
        company_candidates.append(company_id)
        company_candidates.append(str(company_id))

    if not company_candidates:
        return 0

    pending = 0
    jobs_cursor = job_descriptions_col.find(
        {
            "$or": [
                {"company": {"$in": company_candidates}},
                {"company_id": {"$in": company_candidates}},
            ]
        },
        {"_id": 1},
    )

    for job_doc in jobs_cursor:
        job_id = to_string_id(job_doc.get("_id"))
        if not job_id:
            continue
        score_doc = job_preference_scores_col.find_one({"job_id": job_id, "identity_id": identity_id})
        if not score_doc:
            pending += 1
            continue

        status = str(score_doc.get("scoring_status", "") or "")
        if status in {"", "unscored", "queued"}:
            pending += 1

    return pending


def progress_percent(completed: int, estimated_total: int, status: str) -> int:
    if estimated_total <= 0:
        return 0
    if status in TERMINAL_PROGRESS_STATUSES:
        return 100
    return max(0, min(100, int((completed * 100) / estimated_total)))


def publish_scoring_progress(redis_client, channel_name, state, status: str, message: str = "", reason: str = ""):
    updated_at = now_proto_timestamp()
    started_at = state.started_at if getattr(state, "started_at", None) else None

    progress = common_pb2.ScoringProgress(
        run_id=str(getattr(state, "run_id", "")),
        identity_id=str(getattr(state, "identity_id", "")),
        status=status,
        message=message,
        estimated_total=int(getattr(state, "estimated_total", 0)),
        completed=int(getattr(state, "completed", 0)),
        percent=progress_percent(int(getattr(state, "completed", 0)), int(getattr(state, "estimated_total", 0)), status),
        updated_at=updated_at,
        reason=reason,
    )

    if started_at:
        progress.started_at.CopyFrom(started_at)
    if status in TERMINAL_PROGRESS_STATUSES:
        progress.finished_at.CopyFrom(updated_at)

    payload = MessageToDict(progress, preserving_proto_field_name=True)
    if started_at:
        payload["started_at"] = timestamp_dict_from_proto(started_at)
    if progress.updated_at:
        payload["updated_at"] = timestamp_dict_from_proto(progress.updated_at)
    if status in TERMINAL_PROGRESS_STATUSES and progress.finished_at:
        payload["finished_at"] = timestamp_dict_from_proto(progress.finished_at)

    redis_client.publish(channel_name, json.dumps(payload, ensure_ascii=False))


def snapshot_scoring_state(state):
    if state is None:
        return None
    snapshot = common_pb2.ScoringProgress(
        run_id=str(getattr(state, "run_id", "")),
        identity_id=str(getattr(state, "identity_id", "")),
        estimated_total=int(getattr(state, "estimated_total", 0)),
        completed=int(getattr(state, "completed", 0)),
        percent=progress_percent(
            int(getattr(state, "completed", 0)),
            int(getattr(state, "estimated_total", 0)),
            "running",
        ),
    )
    started_at = getattr(state, "started_at", None)
    if started_at:
        snapshot.started_at.CopyFrom(started_at)
    return snapshot


class ScoringRunManager:
    def __init__(self, job_descriptions_col, companies_col, job_preference_scores_col):
        self._job_descriptions_col = job_descriptions_col
        self._companies_col = companies_col
        self._job_preference_scores_col = job_preference_scores_col
        self._scoring_runs: dict[str, common_pb2.ScoringProgress] = {}
        self._start_event_published: set[str] = set()
        self._lock = threading.Lock()

    def start_or_reuse(self, identity_doc):
        with self._lock:
            state = start_or_reuse_scoring_run(
                identity_doc,
                self._job_descriptions_col,
                self._companies_col,
                self._job_preference_scores_col,
                self._scoring_runs,
            )
            if state is None:
                return None, False

            identity_id = str(getattr(state, "identity_id", ""))
            should_publish_start = identity_id not in self._start_event_published
            if should_publish_start:
                self._start_event_published.add(identity_id)

            return snapshot_scoring_state(state), should_publish_start

    def advance(self, identity_doc):
        identity_id = get_message_id(identity_doc)
        if not identity_id:
            return None, False

        with self._lock:
            state = self._scoring_runs.get(identity_id)
            if state is None:
                return None, False

            state, completed_run = advance_scoring_run(
                state,
                identity_doc,
                self._job_descriptions_col,
                self._companies_col,
                self._job_preference_scores_col,
                self._scoring_runs,
            )
            if completed_run:
                self._start_event_published.discard(identity_id)
            return snapshot_scoring_state(state), completed_run


def start_or_reuse_scoring_run(identity_doc, job_descriptions_col, companies_col, job_preference_scores_col, scoring_runs):
    identity_id = get_message_id(identity_doc)
    if not identity_id:
        return None

    state = scoring_runs.get(identity_id)
    if state is None:
        estimated_total = max(
            1,
            estimate_identity_scoring_backlog(
                job_descriptions_col,
                companies_col,
                job_preference_scores_col,
                identity_doc,
            ),
        )
        state = common_pb2.ScoringProgress(
            run_id=str(ObjectId()),
            identity_id=identity_id,
            estimated_total=estimated_total,
            completed=0,
            percent=0,
        )
        state.started_at.CopyFrom(now_proto_timestamp())
        scoring_runs[identity_id] = state
        return state

    refreshed_total = state.completed + estimate_identity_scoring_backlog(
        job_descriptions_col,
        companies_col,
        job_preference_scores_col,
        identity_doc,
    )
    state.estimated_total = max(int(state.estimated_total or 1), int(refreshed_total), 1)
    return state


def advance_scoring_run(
    state,
    identity_doc,
    job_descriptions_col,
    companies_col,
    job_preference_scores_col,
    scoring_runs,
):
    if state is None:
        return state, False

    state.completed = int(state.completed) + 1
    pending = estimate_identity_scoring_backlog(
        job_descriptions_col,
        companies_col,
        job_preference_scores_col,
        identity_doc,
    )
    state.estimated_total = max(int(state.estimated_total or 1), int(state.completed + pending), 1)

    if pending <= 0:
        state.completed = int(state.estimated_total)
        scoring_runs.pop(str(state.identity_id), None)
        return state, True

    return state, False


def parse_object_id(value):
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str):
        try:
            return ObjectId(value)
        except Exception:
            return None
    return None


def stable_test_score(job_id, preference_key):
    # Deterministic integer score in [0..5] for test mode.
    seed_text = f"{job_id}:{preference_key}"
    seed = sum(ord(ch) for ch in seed_text)
    return seed % 6


def safe_json_dump(value):
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return repr(value)


def parse_ollama_response(content):
    if not content:
        return None, None, "empty_content"

    if isinstance(content, str):
        stripped = content.strip()
        normalized = stripped.lower().strip(".\n\r\t ")
        if normalized in {
            "n/a",
            "na",
            "not available",
            "not enough information",
            "insufficient information",
        }:
            return None, False, "explicit_na"
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict) and "score" in payload:
                score_value = payload.get("score")
                if isinstance(score_value, str):
                    normalized_score = score_value.lower().strip(".\n\r\t ")
                    if normalized_score in {
                        "n/a",
                        "na",
                        "not available",
                        "not enough information",
                        "insufficient information",
                    }:
                        return None, False, "json_dict_na"
                if score_value is not None:
                    return int(score_value), True, "json_dict_score"
            if isinstance(payload, dict) and payload.get("score_available") is False:
                return None, False, "json_dict_score_unavailable"
            if isinstance(payload, int):
                return payload, True, "json_integer"
        except Exception:
            pass

        if stripped.isdigit():
            return int(stripped), True, "plain_integer"

        digits = [ch for ch in stripped if ch.isdigit()]
        if digits:
            return int(digits[0]), True, "first_digit_fallback"

        return None, None, "string_without_parseable_score"

    if isinstance(content, dict):
        try:
            if content.get("score_available") is False:
                return None, False, "dict_score_unavailable"
            score_value = content.get("score")
            if score_value is None:
                return None, None, "dict_without_score"
            if isinstance(score_value, str):
                normalized_score = score_value.lower().strip(".\n\r\t ")
                if normalized_score in {
                    "n/a",
                    "na",
                    "not available",
                    "not enough information",
                    "insufficient information",
                }:
                    return None, False, "dict_na"
            return int(score_value), True, "dict_score"
        except Exception:
            return None, None, "dict_score_cast_failed"

    return None, None, f"unsupported_content_type:{type(content).__name__}"


def extract_ollama_content(response):
    if response is None:
        return ""

    if isinstance(response, dict):
        message = response.get("message", {})
        if isinstance(message, dict):
            return str(message.get("content", "") or "")
        return str(getattr(message, "content", "") or "")

    message = getattr(response, "message", None)
    if message is not None:
        return str(getattr(message, "content", "") or "")

    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            if isinstance(dumped, dict):
                message = dumped.get("message", {})
                if isinstance(message, dict):
                    return str(message.get("content", "") or "")
        except Exception:
            return ""

    return ""


def resolve_embedding_model_name() -> str:
    configured = str(os.environ.get("EMBEDDING_MODEL", "") or "").strip()
    return configured or DEFAULT_EMBEDDING_MODEL


def resolve_candidate_embedding_model_name() -> str:
    configured = str(os.environ.get("CANDIDATE_EMBEDDING_MODEL", "") or "").strip()
    return configured or resolve_embedding_model_name()


def resolve_candidate_query_prefix() -> str:
    return str(
        os.environ.get("CANDIDATE_QUERY_PREFIX", DEFAULT_CANDIDATE_QUERY_PREFIX)
        or ""
    )


def resolve_evidence_fusion_mode() -> str:
    return str(os.environ.get("EVIDENCE_FUSION_MODE", "") or "").strip()


def resolve_candidate_retrieval_mode() -> str:
    return str(os.environ.get("CANDIDATE_RETRIEVAL_MODE", "") or "").strip()


def resolve_evidence_selection_mode() -> str:
    return str(os.environ.get("EVIDENCE_SELECTION_MODE", "") or "").strip()


def resolve_evidence_view_routing_mode() -> str:
    return str(os.environ.get("EVIDENCE_VIEW_ROUTING", "") or "").strip()


def resolve_evidence_scope_routing_mode() -> str:
    return str(os.environ.get("EVIDENCE_SCOPE_ROUTING", "") or "").strip()


def resolve_final_order_routing_mode() -> str:
    return str(os.environ.get("FINAL_ORDER_ROUTING", "") or "").strip()


def resolve_scoring_options() -> dict[str, Any]:
    configured_temperature = str(
        os.environ.get("SCORING_TEMPERATURE", "") or ""
    ).strip()
    options: dict[str, Any] = {
        "temperature": float(configured_temperature) if configured_temperature else 0
    }
    configured_seed = str(os.environ.get("SCORING_SEED", "") or "").strip()
    if configured_seed:
        options["seed"] = int(configured_seed)
    return options


def should_normalize_job_location() -> bool:
    return str(os.environ.get("NORMALIZE_JOB_LOCATION", "") or "").lower() in {
        "1",
        "true",
        "yes",
    }


def should_normalize_job_title() -> bool:
    return str(os.environ.get("NORMALIZE_JOB_TITLE", "") or "").lower() in {
        "1",
        "true",
        "yes",
    }


def should_render_explicit_remote_location() -> bool:
    return str(os.environ.get("EXPLICIT_REMOTE_LOCATION", "") or "").lower() in {
        "1",
        "true",
        "yes",
    }


def should_normalize_preference_guidance() -> bool:
    return str(os.environ.get("NORMALIZE_PREFERENCE_GUIDANCE", "") or "").lower() in {
        "1",
        "true",
        "yes",
    }


def should_use_scorer_pointwise_reranking() -> bool:
    return str(os.environ.get("SCORER_POINTWISE_RERANK", "") or "").lower() in {
        "1",
        "true",
        "yes",
    }


def should_use_scorer_pointwise_reranking_cascade() -> bool:
    return str(
        os.environ.get("SCORER_POINTWISE_RERANK_CASCADE", "") or ""
    ).lower() in {"1", "true", "yes"}


def should_rerank_with_job_context() -> bool:
    return str(os.environ.get("RERANK_WITH_JOB_CONTEXT", "") or "").lower() in {
        "1",
        "true",
        "yes",
    }


def should_preserve_candidate_order() -> bool:
    return str(os.environ.get("PRESERVE_CANDIDATE_ORDER", "") or "").lower() in {
        "1",
        "true",
        "yes",
    }


def fuse_raw_and_reranked_snippets(
    raw_snippets: list[str],
    reranked_snippets: list[str],
    top_k: int = SNIPPET_RERANKED_TOP_K,
) -> list[str]:
    """Reserve one result for each retrieval view, then fill without duplicates."""
    ordered = (
        raw_snippets[:1]
        + reranked_snippets[:1]
        + reranked_snippets[1:]
        + raw_snippets[1:]
    )
    selected: list[str] = []
    seen: set[str] = set()
    for snippet in ordered:
        if not snippet or snippet in seen:
            continue
        seen.add(snippet)
        selected.append(snippet)
        if len(selected) >= max(0, top_k):
            break
    return selected


def build_embedding_model(model_name: str):
    try:
        from fastembed import TextEmbedding
    except Exception as exc:
        raise RuntimeError(f"fastembed import failed: {exc}") from exc

    return TextEmbedding(model_name=model_name)


def get_embedding_model(model_name: str):
    with _EMBEDDING_MODEL_CACHE_LOCK:
        cached = _EMBEDDING_MODEL_CACHE.get(model_name)
        if cached is not None:
            return cached

        created = build_embedding_model(model_name)
        _EMBEDDING_MODEL_CACHE[model_name] = created
        return created


def _vector_to_float_list(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]


def _cosine_similarity(vector_a: list[float], vector_b: list[float]) -> float:
    if not vector_a or not vector_b or len(vector_a) != len(vector_b):
        return -1.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for idx in range(len(vector_a)):
        value_a = vector_a[idx]
        value_b = vector_b[idx]
        dot += value_a * value_b
        norm_a += value_a * value_a
        norm_b += value_b * value_b

    if norm_a <= 0.0 or norm_b <= 0.0:
        return -1.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def generate_hybrid_chunks(text: str, window_size: int = SNIPPET_WINDOW_SIZE) -> list[str]:
    if not text:
        return []

    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Some job descriptions use inline bullets (e.g. "foo • bar • baz").
    # Convert those separators into line starts so bullet-aware chunking can split them.
    normalized_text = re.sub(r"\s*•\s+", "\n• ", normalized_text)
    # Treat inline hyphen/dash bullets as line starts when they separate clauses.
    # This keeps hyphenated words intact because we require surrounding whitespace.
    normalized_text = re.sub(r"\s*[—–-]\s+", "\n- ", normalized_text)
    paragraph_blocks = re.split(r"\n\s*\n+", normalized_text)

    atomic_units: list[str] = []

    def append_atomic(candidate: str):
        cleaned = re.sub(r"\s+", " ", candidate).strip()
        if cleaned:
            atomic_units.append(cleaned)

    for block in paragraph_blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        current_lines: list[str] = []

        def flush_current_lines():
            if current_lines:
                append_atomic(" ".join(current_lines))
                current_lines.clear()

        for raw_line in lines:
            is_bullet = bool(re.match(r"^([-*•]|\d+[.)])\s+", raw_line))
            is_heading = raw_line.endswith(":") or raw_line.startswith("#")

            if is_bullet or is_heading:
                flush_current_lines()
                bullet_removed = re.sub(r"^([-*•]|\d+[.)])\s+", "", raw_line).strip()
                append_atomic(bullet_removed)
                continue

            current_lines.append(raw_line)

        flush_current_lines()

    if not atomic_units:
        return []

    sentence_like_units: list[str] = []
    for unit in atomic_units:
        parts = re.split(r"(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=[\.!\?])\s", unit)
        for part in parts:
            cleaned = part.strip()
            if cleaned:
                sentence_like_units.append(cleaned)

    if not sentence_like_units:
        return []

    ordered_chunks: list[str] = []
    seen: set[str] = set()

    for sentence_like in sentence_like_units:
        if sentence_like not in seen:
            seen.add(sentence_like)
            ordered_chunks.append(sentence_like)

    if window_size <= 1:
        return ordered_chunks

    for start_idx in range(0, max(0, len(sentence_like_units) - window_size + 1)):
        window_text = " ".join(sentence_like_units[start_idx : start_idx + window_size]).strip()
        if window_text and window_text not in seen:
            seen.add(window_text)
            ordered_chunks.append(window_text)

    return ordered_chunks


def generate_heading_contextual_chunks(text: str) -> list[str]:
    """Attach the nearest source section heading to each atomic evidence unit."""
    if not text:
        return []

    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized_text = re.sub(r"\s*•\s+", "\n• ", normalized_text)
    normalized_text = re.sub(r"\s*[—–-]\s+", "\n- ", normalized_text)

    contextual_chunks: list[str] = []
    seen: set[str] = set()
    current_heading = ""
    buffered_lines: list[str] = []

    def clean_heading(value: str) -> str:
        cleaned = re.sub(r"^#+\s*", "", value).strip()
        cleaned = re.sub(r"^\*\*(.*?)\*\*$", r"\1", cleaned).strip()
        return cleaned.rstrip(":").strip()

    def append_content(value: str):
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned:
            return
        parts = re.split(
            r"(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=[\.!?])\s",
            cleaned,
        )
        for part in parts:
            evidence = part.strip()
            if not evidence:
                continue
            contextual = (
                f"Section: {current_heading}\nEvidence: {evidence}"
                if current_heading
                else evidence
            )
            if contextual not in seen:
                seen.add(contextual)
                contextual_chunks.append(contextual)

    def flush_buffer():
        if buffered_lines:
            append_content(" ".join(buffered_lines))
            buffered_lines.clear()

    for raw_line in normalized_text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush_buffer()
            continue

        heading_candidate = clean_heading(line)
        is_markdown_heading = line.startswith("#")
        is_bold_heading = bool(re.fullmatch(r"\*\*.+:?\*\*", line))
        is_plain_heading = line.endswith(":") and len(line.split()) <= 12
        if is_markdown_heading or is_bold_heading or is_plain_heading:
            flush_buffer()
            current_heading = heading_candidate
            continue

        is_bullet = bool(re.match(r"^([-*•]|\d+[.)])\s+", line))
        if is_bullet:
            flush_buffer()
            append_content(re.sub(r"^([-*•]|\d+[.)])\s+", "", line).strip())
        else:
            buffered_lines.append(line)

    flush_buffer()
    return contextual_chunks


def get_top_snippets(
    embedding_model,
    chunks: list[str],
    cached_vectors: list[list[float]],
    requirement: str,
    top_k: int = SNIPPET_TOP_K,
) -> list[str]:
    if not chunks or not cached_vectors or not requirement:
        return []

    requirement_vector = _vector_to_float_list(next(embedding_model.embed([requirement])))
    similarities = []
    for idx, vector in enumerate(cached_vectors):
        similarities.append((idx, _cosine_similarity(vector, requirement_vector)))

    ranked = sorted(similarities, key=lambda pair: pair[1], reverse=True)
    selected = [chunks[idx] for idx, _ in ranked[: max(0, top_k)] if 0 <= idx < len(chunks)]
    return selected


def retrieve_relevant_snippets(
    job_description: str,
    preference_guidance: str,
    top_k: int = SNIPPET_TOP_K,
    model_name: str | None = None,
    exclude_heading_only: bool = False,
) -> list[str]:
    if not job_description:
        return []

    model_name = model_name or resolve_embedding_model_name()
    embedding_model = get_embedding_model(model_name)

    chunks = generate_hybrid_chunks(job_description, window_size=SNIPPET_WINDOW_SIZE)
    if exclude_heading_only:
        chunks = [chunk for chunk in chunks if not is_heading_only_chunk(chunk)]
    if not chunks:
        return []

    cached_vectors = [_vector_to_float_list(vector) for vector in embedding_model.embed(chunks)]
    if not cached_vectors:
        return []

    return get_top_snippets(
        embedding_model,
        chunks,
        cached_vectors,
        preference_guidance,
        top_k=top_k,
    )


def is_heading_only_chunk(chunk: str) -> bool:
    """Return whether an atomic chunk is formatting-only section metadata."""
    value = str(chunk or "").strip()
    if not value:
        return True
    if re.fullmatch(r"#{1,6}\s+[^\n]+", value):
        return True
    if value.startswith("**") and value.count("**") == 2 and (
        value.endswith("**") or value.endswith("**:")
    ):
        return True
    return value.endswith(":") and len(value.split()) <= 12


def retrieve_heading_contextual_snippets(
    job_description: str,
    preference_guidance: str,
    top_k: int = SNIPPET_TOP_K,
    model_name: str | None = None,
) -> list[str]:
    if not job_description:
        return []
    model_name = model_name or resolve_embedding_model_name()
    embedding_model = get_embedding_model(model_name)
    chunks = generate_heading_contextual_chunks(job_description)
    if not chunks:
        return []
    cached_vectors = [_vector_to_float_list(vector) for vector in embedding_model.embed(chunks)]
    if not cached_vectors:
        return []
    return get_top_snippets(
        embedding_model,
        chunks,
        cached_vectors,
        preference_guidance,
        top_k=top_k,
    )


def retrieve_bm25_snippets(
    job_description: str,
    query: str,
    top_k: int = SNIPPET_CANDIDATE_K,
) -> list[str]:
    """Rank atomic source chunks with standard BM25 lexical relevance."""
    chunks = generate_hybrid_chunks(job_description, window_size=SNIPPET_WINDOW_SIZE)
    query_terms = re.findall(r"[a-z0-9]+", query.lower())
    if not chunks or not query_terms:
        return []

    documents = [re.findall(r"[a-z0-9]+", chunk.lower()) for chunk in chunks]
    document_count = len(documents)
    average_length = sum(len(document) for document in documents) / document_count
    document_frequency: dict[str, int] = {}
    for document in documents:
        for term in set(document):
            document_frequency[term] = document_frequency.get(term, 0) + 1

    k1 = 1.5
    b = 0.75
    scores: list[tuple[int, float]] = []
    for index, document in enumerate(documents):
        term_frequency: dict[str, int] = {}
        for term in document:
            term_frequency[term] = term_frequency.get(term, 0) + 1
        length_normalization = 1 - b + b * len(document) / max(average_length, 1.0)
        score = 0.0
        for term in query_terms:
            frequency = term_frequency.get(term, 0)
            if frequency == 0:
                continue
            frequency_in_corpus = document_frequency.get(term, 0)
            inverse_document_frequency = math.log(
                1 + (document_count - frequency_in_corpus + 0.5) / (frequency_in_corpus + 0.5)
            )
            score += inverse_document_frequency * (
                frequency * (k1 + 1) / (frequency + k1 * length_normalization)
            )
        scores.append((index, score))

    ranked = sorted(scores, key=lambda item: (-item[1], item[0]))
    return [chunks[index] for index, _ in ranked[: max(0, top_k)]]


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    top_k: int = SNIPPET_CANDIDATE_K,
    rank_constant: int = 60,
) -> list[str]:
    """Fuse ranked source passages without comparing retriever score scales."""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    order = 0
    for ranked in ranked_lists:
        for rank, snippet in enumerate(ranked, start=1):
            if snippet not in first_seen:
                first_seen[snippet] = order
                order += 1
            scores[snippet] = scores.get(snippet, 0.0) + 1.0 / (rank_constant + rank)
    ranked_snippets = sorted(scores, key=lambda snippet: (-scores[snippet], first_seen[snippet]))
    return ranked_snippets[: max(0, top_k)]


def resolve_query_expansion_model_name() -> str:
    configured = str(os.environ.get("QUERY_EXPANSION_MODEL", "") or "").strip()
    return configured or DEFAULT_QUERY_EXPANSION_MODEL


def expand_retrieval_query(
    ollama_client,
    preference_guidance: str,
) -> str:
    """Expand terse preference guidance into a semantic retrieval query."""
    expansion_model = resolve_query_expansion_model_name()
    expansion_profile = str(
        os.environ.get("QUERY_EXPANSION_PROFILE", "") or ""
    ).strip()
    cache_key = (expansion_model, expansion_profile, preference_guidance)
    with _QUERY_EXPANSION_CACHE_LOCK:
        cached = _QUERY_EXPANSION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    system_instruction = (
        "Rewrite a candidate job preference as one concise semantic search "
        "query for retrieving evidence from a job description. Include "
        "relevant role titles, duties, technologies, synonyms, and both "
        "supporting and contradicting phrases when useful. Preserve the "
        "preference's meaning and intensity. Do not evaluate any job and do "
        "not assign a score. Return one JSON object with exactly one string "
        "field named search_query."
    )
    if expansion_profile == "responsibility_evidence":
        system_instruction = (
            "Transform one candidate job preference into one compact semantic "
            "search query made of language likely to appear in a job posting as "
            "direct evidence for or against that criterion. Preserve its exact "
            "strength, exclusivity, and qualifiers. Prioritize core "
            "responsibilities, day-to-day work, role scope, work conditions, and "
            "explicit opposite or limiting language. Avoid generic company claims "
            "and generic candidate qualifications or skill lists unless the "
            "preference itself is about a qualification. Do not evaluate a job, "
            "invent job facts, or assign a score. Return one JSON object with "
            "exactly one string field named search_query."
        )

    messages = [
        {
            "role": "system",
            "content": system_instruction,
        },
        {
            "role": "user",
            "content": f"Preference Guidance: {preference_guidance}\n",
        },
    ]

    response = ollama_client.chat(
        model=expansion_model,
        messages=messages,
        format="json",
        options={"temperature": 0},
    )
    content = extract_ollama_content(response)
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("Query expansion response is not a JSON object")

    expanded_query = str(payload.get("search_query", "") or "").strip()
    if not expanded_query:
        raise ValueError("Query expansion response is missing search_query")
    with _QUERY_EXPANSION_CACHE_LOCK:
        _QUERY_EXPANSION_CACHE[cache_key] = expanded_query
    return expanded_query


def normalize_job_location(ollama_client, raw_location: str) -> str:
    """Map free-form location metadata to the scorer's trained vocabulary."""
    model_name = (
        str(os.environ.get("METADATA_NORMALIZATION_MODEL", "") or "").strip()
        or DEFAULT_METADATA_NORMALIZATION_MODEL
    )
    cache_key = (model_name, raw_location)
    with _LOCATION_NORMALIZATION_CACHE_LOCK:
        cached = _LOCATION_NORMALIZATION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    response = ollama_client.chat(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": (
                    "Normalize one raw job-location value for a work-arrangement "
                    "evaluator. Return exactly one of remote, hybrid, onsite, or "
                    "unknown. Preserve the explicitly stated work arrangement. A "
                    "remote role restricted to a country or region is still remote. "
                    "Use unknown when the value is blank or does not state a work "
                    "arrangement. Do not infer from any information outside the raw "
                    "value. Return one JSON object with exactly one string field named "
                    "normalized_location."
                ),
            },
            {
                "role": "user",
                "content": f"Raw Job Location: {raw_location}\n",
            },
        ],
        format="json",
        options={"temperature": 0},
    )
    payload = json.loads(extract_ollama_content(response))
    if not isinstance(payload, dict):
        raise ValueError("Location normalization response is not a JSON object")
    normalized = str(payload.get("normalized_location", "") or "").strip().lower()
    if normalized not in {"remote", "hybrid", "onsite", "unknown"}:
        raise ValueError(f"Location normalizer returned invalid value: {normalized!r}")
    with _LOCATION_NORMALIZATION_CACHE_LOCK:
        _LOCATION_NORMALIZATION_CACHE[cache_key] = normalized
    return normalized


def preference_guidance_needs_rewrite(ollama_client, raw_guidance: str) -> bool:
    """Detect incomplete linguistic form without interpreting the criterion."""
    model_name = str(
        os.environ.get("PREFERENCE_NORMALIZATION_MODEL", "") or ""
    ).strip() or "qwen2.5:7b"
    cache_key = (model_name, "referent_only_v1", raw_guidance)
    with _PREFERENCE_FRAGMENT_CACHE_LOCK:
        cached = _PREFERENCE_FRAGMENT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    response = ollama_client.chat(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify only whether one candidate-preference string contains "
                    "an impersonal pronoun or demonstrative whose referent is missing. "
                    "Set needs_rewrite=true for an unresolved form such as 'It "
                    "requires Y' or 'This involves Y'. Set needs_rewrite=false for "
                    "concise preference-field forms such as 'Prefers X' or 'Strong "
                    "preference for X', as well as complete declarative or first-person "
                    "statements. Do not interpret the criterion, "
                    "paraphrase it, evaluate a job, or assign a score. Return one JSON "
                    "object with exactly one boolean field named needs_rewrite."
                ),
            },
            {
                "role": "user",
                "content": "Preference Guidance: Prefers quiet work environments\n",
            },
            {"role": "assistant", "content": '{"needs_rewrite":false}'},
            {
                "role": "user",
                "content": "Preference Guidance: It requires frequent travel\n",
            },
            {"role": "assistant", "content": '{"needs_rewrite":true}'},
            {
                "role": "user",
                "content": "Preference Guidance: Strong preference for quiet offices\n",
            },
            {"role": "assistant", "content": '{"needs_rewrite":false}'},
            {
                "role": "user",
                "content": f"Preference Guidance: {raw_guidance}\n",
            },
        ],
        format="json",
        options={"temperature": 0},
    )
    payload = json.loads(extract_ollama_content(response))
    decision = payload.get("needs_rewrite") if isinstance(payload, dict) else None
    if type(decision) is not bool:
        raise ValueError("Preference fragment classifier returned invalid output")
    with _PREFERENCE_FRAGMENT_CACHE_LOCK:
        _PREFERENCE_FRAGMENT_CACHE[cache_key] = decision
    return decision


def classify_preference_evidence_scope(ollama_client, preference_guidance: str) -> str:
    """Classify whether structured location metadata can decide a criterion."""
    model_name = str(
        os.environ.get("EVIDENCE_SCOPE_MODEL", "") or ""
    ).strip() or "qwen2.5:7b"
    cache_key = (model_name, preference_guidance)
    with _PREFERENCE_EVIDENCE_SCOPE_CACHE_LOCK:
        cached = _PREFERENCE_EVIDENCE_SCOPE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    response = ollama_client.chat(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify which source is necessary to evaluate one candidate job "
                    "preference. Return location_metadata only when the criterion is "
                    "solely about work arrangement or geographic location and an "
                    "explicit job-location field can directly support or contradict it. "
                    "Return description for criteria about role family, duties, "
                    "technologies, intensity, culture, quality, or any other content "
                    "requiring title or description evidence. Do not evaluate a job or "
                    "assign a score. Return one JSON object with exactly one string "
                    "field named evidence_scope whose value is location_metadata or "
                    "description."
                ),
            },
            {
                "role": "user",
                "content": "Preference Guidance: Prefers fully remote work\n",
            },
            {"role": "assistant", "content": '{"evidence_scope":"location_metadata"}'},
            {
                "role": "user",
                "content": "Preference Guidance: Wants roles in Amsterdam or remote\n",
            },
            {"role": "assistant", "content": '{"evidence_scope":"location_metadata"}'},
            {
                "role": "user",
                "content": "Preference Guidance: Prefers hands-on implementation work\n",
            },
            {"role": "assistant", "content": '{"evidence_scope":"description"}'},
            {
                "role": "user",
                "content": "Preference Guidance: Prefers backend roles over frontend roles\n",
            },
            {"role": "assistant", "content": '{"evidence_scope":"description"}'},
            {
                "role": "user",
                "content": f"Preference Guidance: {preference_guidance}\n",
            },
        ],
        format="json",
        options={"temperature": 0},
    )
    payload = json.loads(extract_ollama_content(response))
    scope = payload.get("evidence_scope") if isinstance(payload, dict) else None
    if scope not in {"location_metadata", "description"}:
        raise ValueError("Preference evidence-scope classifier returned invalid output")
    with _PREFERENCE_EVIDENCE_SCOPE_CACHE_LOCK:
        _PREFERENCE_EVIDENCE_SCOPE_CACHE[cache_key] = scope
    return scope


def normalize_preference_guidance(ollama_client, raw_guidance: str) -> str:
    """Canonicalize impersonal preference fragments without changing criteria."""
    if re.match(r"^\s*(?:I\b|My\b)", raw_guidance, flags=re.IGNORECASE) or re.search(
        r"\bme\b", raw_guidance, flags=re.IGNORECASE
    ):
        return raw_guidance
    if not re.match(r"^\s*(?:it|this|that)\b", raw_guidance, flags=re.IGNORECASE):
        return raw_guidance
    if not preference_guidance_needs_rewrite(ollama_client, raw_guidance):
        return raw_guidance
    model_name = str(
        os.environ.get("PREFERENCE_NORMALIZATION_MODEL", "") or ""
    ).strip() or "qwen2.5:7b"
    cache_key = (model_name, "guarded_first_person_v1", raw_guidance)
    with _PREFERENCE_NORMALIZATION_CACHE_LOCK:
        cached = _PREFERENCE_NORMALIZATION_CACHE.get(cache_key)
    if cached is not None:
        return cached
    response = ollama_client.chat(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": (
                    "Rewrite one candidate preference with an unresolved impersonal "
                    "referent as one natural first-person criterion about the desired "
                    "role. The output MUST begin with the word 'I'. Make only the "
                    "implicit role referent explicit. Preserve exactly "
                    "the stated criterion, intensity, acceptable alternatives, "
                    "exclusions, and qualifiers. Resolve only grammatical perspective "
                    "or an impersonal pronoun; do not add role titles, duties, "
                    "technologies, synonyms, examples, exceptions, opposite "
                    "conditions, or new semantic detail. Do not evaluate a job or "
                    "assign a score. Return one JSON object with exactly one string "
                    "field named normalized_guidance."
                ),
            },
            {
                "role": "user",
                "content": "Preference Guidance: Prefers quiet work environments\n",
            },
            {
                "role": "assistant",
                "content": '{"normalized_guidance":"I prefer quiet work environments"}',
            },
            {
                "role": "user",
                "content": "Preference Guidance: It requires frequent travel\n",
            },
            {
                "role": "assistant",
                "content": '{"normalized_guidance":"I prefer roles that require frequent travel"}',
            },
            {
                "role": "user",
                "content": f"Preference Guidance: {raw_guidance}\n",
            },
        ],
        format="json",
        options={"temperature": 0},
    )
    payload = json.loads(extract_ollama_content(response))
    if not isinstance(payload, dict):
        raise ValueError("Preference normalizer response is not a JSON object")
    normalized = str(payload.get("normalized_guidance", "") or "").strip()
    if not normalized:
        raise ValueError("Preference normalizer returned empty guidance")
    with _PREFERENCE_NORMALIZATION_CACHE_LOCK:
        _PREFERENCE_NORMALIZATION_CACHE[cache_key] = normalized
    return normalized


def normalize_job_title(ollama_client, raw_title: str) -> str:
    """Remove appended job-feed metadata while preserving the role title."""
    model_name = (
        str(os.environ.get("TITLE_NORMALIZATION_MODEL", "") or "").strip()
        or "qwen2.5:7b"
    )
    cache_key = (model_name, raw_title)
    with _TITLE_NORMALIZATION_CACHE_LOCK:
        cached = _TITLE_NORMALIZATION_CACHE.get(cache_key)
    if cached is not None:
        return cached
    response = ollama_client.chat(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract the job role title from one raw job-title value. Remove "
                    "only obvious appended scraping or job-feed metadata such as "
                    "relative posting age (for example \"2 days ago\"), work-"
                    "arrangement badges (for example \"Fully Remote\" or \"Hybrid\"), "
                    "geographic location, salary, or application status. Preserve the "
                    "role title's original wording, seniority, specialization, "
                    "punctuation, abbreviations, and capitalization byte-for-byte "
                    "whenever no such appended metadata is present. Do not paraphrase, "
                    "expand abbreviations, classify the role, or use outside "
                    "information. Return one JSON object with exactly one string field "
                    "named normalized_title."
                ),
            },
            {"role": "user", "content": f"Raw Job Title: {raw_title}\n"},
        ],
        format="json",
        options={"temperature": 0},
    )
    payload = json.loads(extract_ollama_content(response))
    if not isinstance(payload, dict):
        raise ValueError("Title normalization response is not a JSON object")
    normalized = str(payload.get("normalized_title", "") or "").strip()
    if not normalized:
        raise ValueError("Title normalizer returned an empty title")
    with _TITLE_NORMALIZATION_CACHE_LOCK:
        _TITLE_NORMALIZATION_CACHE[cache_key] = normalized
    return normalized


def expand_contrastive_retrieval_queries(
    ollama_client,
    preference_guidance: str,
) -> tuple[str, str]:
    """Create separate semantic queries for supporting and conflicting evidence."""
    expansion_model = resolve_query_expansion_model_name()
    cache_key = (expansion_model, preference_guidance)
    with _CONTRASTIVE_QUERY_CACHE_LOCK:
        cached = _CONTRASTIVE_QUERY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    response = ollama_client.chat(
        model=expansion_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Generate two concise semantic search queries for retrieving "
                    "verbatim evidence about one candidate job preference. The support "
                    "query should describe explicit job language that would strongly and "
                    "centrally satisfy the preference. The conflict query should describe "
                    "explicit opposite conditions, constraints, or role language showing "
                    "that the preference is contradicted or only incidental. Preserve the "
                    "preference's meaning and intensity. Include relevant role titles, "
                    "duties, technologies, and synonyms when useful. Do not evaluate any "
                    "job or assign a score. Return one JSON object with exactly two string "
                    "fields: support_query and conflict_query."
                ),
            },
            {
                "role": "user",
                "content": f"Preference Guidance: {preference_guidance}\n",
            },
        ],
        format="json",
        options={"temperature": 0},
    )
    payload = json.loads(extract_ollama_content(response))
    if not isinstance(payload, dict):
        raise ValueError("Contrastive query expansion response is not a JSON object")
    support_query = str(payload.get("support_query", "") or "").strip()
    conflict_query = str(payload.get("conflict_query", "") or "").strip()
    if not support_query or not conflict_query:
        raise ValueError("Contrastive query expansion is missing a query")
    expanded = (support_query, conflict_query)
    with _CONTRASTIVE_QUERY_CACHE_LOCK:
        _CONTRASTIVE_QUERY_CACHE[cache_key] = expanded
    return expanded


def resolve_reranking_model_name() -> str:
    configured = str(os.environ.get("RERANKING_MODEL", "") or "").strip()
    return configured or DEFAULT_RERANKING_MODEL


def resolve_late_interaction_reranking_model_name() -> str:
    return str(os.environ.get("LATE_INTERACTION_RERANK_MODEL", "") or "").strip()


def build_reranking_model(model_name: str):
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
    except Exception as exc:
        raise RuntimeError(f"fastembed cross-encoder import failed: {exc}") from exc
    return TextCrossEncoder(model_name=model_name)


def get_reranking_model(model_name: str):
    with _RERANKING_MODEL_CACHE_LOCK:
        cached = _RERANKING_MODEL_CACHE.get(model_name)
        if cached is not None:
            return cached
        created = build_reranking_model(model_name)
        _RERANKING_MODEL_CACHE[model_name] = created
        return created


def rerank_scoring_snippets(
    preference_guidance: str,
    candidate_snippets: list[str],
    top_k: int = SNIPPET_TOP_K,
) -> list[str]:
    if not preference_guidance or not candidate_snippets:
        return []
    model = get_reranking_model(resolve_reranking_model_name())
    scores = [float(score) for score in model.rerank(preference_guidance, candidate_snippets)]
    ranked = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
    return [candidate_snippets[index] for index in ranked[: max(0, top_k)]]


def get_late_interaction_model(model_name: str):
    with _LATE_INTERACTION_MODEL_CACHE_LOCK:
        cached = _LATE_INTERACTION_MODEL_CACHE.get(model_name)
        if cached is not None:
            return cached
        try:
            from fastembed import LateInteractionTextEmbedding
        except Exception as exc:
            raise RuntimeError(f"fastembed late-interaction import failed: {exc}") from exc
        created = LateInteractionTextEmbedding(model_name=model_name)
        _LATE_INTERACTION_MODEL_CACHE[model_name] = created
        return created


def late_interaction_rerank_scoring_snippets(
    preference_guidance: str,
    candidate_snippets: list[str],
    model_name: str,
    top_k: int = SNIPPET_RERANKED_TOP_K,
) -> list[str]:
    """Rank source passages with standard ColBERT token-level MaxSim."""
    if not preference_guidance or not candidate_snippets:
        return []
    model = get_late_interaction_model(model_name)
    query_embedding = next(model.query_embed([preference_guidance]))
    passage_embeddings = list(model.passage_embed(candidate_snippets))
    scores = []
    for index, passage_embedding in enumerate(passage_embeddings):
        token_similarities = query_embedding @ passage_embedding.T
        score = float(token_similarities.max(axis=1).sum())
        scores.append((index, score))
    scores.sort(key=lambda item: (-item[1], item[0]))
    return [candidate_snippets[index] for index, _ in scores[: max(0, top_k)]]


def select_scoring_snippets_with_llm(
    ollama_client,
    preference_guidance: str,
    candidate_snippets: list[str],
    job_title: str = "",
    job_location: str = "",
    top_k: int = SNIPPET_TOP_K,
) -> list[str]:
    """Select a jointly diagnostic evidence set from the complete description."""
    selection_count = min(max(0, top_k), len(candidate_snippets))
    if not preference_guidance or selection_count == 0:
        return []

    indexed_candidates = "\n".join(
        f"{index}: {snippet}" for index, snippet in enumerate(candidate_snippets)
    )
    response = ollama_client.chat(
        model=(
            str(os.environ.get("EVIDENCE_SELECTOR_MODEL", "") or "").strip()
            or DEFAULT_EVIDENCE_SELECTOR_MODEL
        ),
        messages=[
            {
                "role": "system",
                "content": (
                    "Select a jointly diagnostic set of verbatim job-description "
                    "fragments for a separate preference scorer. Cover the clearest "
                    "direct support or contradiction and, when possible, evidence "
                    "showing whether the preference is a core or repeated role feature "
                    "rather than an incidental mention. Prefer concrete duties, work "
                    "conditions, requirements, and technologies over headings, generic "
                    "employer language, and application boilerplate. Use the title and "
                    "location only as context. You must select the requested number of "
                    "distinct zero-based evidence indices; never abstain, score the job, "
                    "rewrite evidence, or invent text. Return one JSON object with "
                    "exactly one array field named selected_indices."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Preference Guidance: {preference_guidance}\n"
                    f"Job Title: {job_title}\n"
                    f"Job Location: {job_location}\n"
                    f"Select exactly {selection_count} indices.\n"
                    f"Evidence fragments:\n{indexed_candidates}\n"
                ),
            },
        ],
        format="json",
        options={"temperature": 0},
    )
    payload = json.loads(extract_ollama_content(response))
    indices = payload.get("selected_indices") if isinstance(payload, dict) else None
    if not isinstance(indices, list) or len(indices) != selection_count:
        raise ValueError(f"Selector returned invalid indices: {indices!r}")
    if any(type(index) is not int for index in indices):
        raise ValueError(f"Selector returned non-integer indices: {indices!r}")
    if len(set(indices)) != selection_count or any(
        index < 0 or index >= len(candidate_snippets) for index in indices
    ):
        raise ValueError(f"Selector returned out-of-range or duplicate indices: {indices!r}")
    return [candidate_snippets[index] for index in indices]


def select_scoring_snippets_with_compact_llm(
    ollama_client,
    preference_guidance: str,
    candidate_snippets: list[str],
    job_title: str = "",
    job_location: str = "",
    top_k: int = SNIPPET_TOP_K,
) -> list[str]:
    """Select diagnostic source passages with a compact extractive contract."""
    selection_count = min(max(0, top_k), len(candidate_snippets))
    if not preference_guidance or selection_count == 0:
        return []
    candidates = "\n".join(
        f"{index}: {snippet}" for index, snippet in enumerate(candidate_snippets)
    )
    response = ollama_client.chat(
        model=(
            str(os.environ.get("EVIDENCE_SELECTOR_MODEL", "") or "").strip()
            or DEFAULT_EVIDENCE_SELECTOR_MODEL
        ),
        messages=[
            {
                "role": "system",
                "content": (
                    f"Choose exactly {selection_count} job-evidence fragments most "
                    "diagnostic of whether the candidate preference is satisfied. "
                    "Prefer explicit central support or contradiction over vague, "
                    "generic, or administrative text. Indices are zero-based. Do not "
                    "score the job, rewrite evidence, or invent text. Return JSON only "
                    "with one field: selected_indices."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Preference: {preference_guidance}\n"
                    f"Job title: {job_title}\n"
                    f"Job location: {job_location}\n"
                    f"Evidence fragments:\n{candidates}\n"
                ),
            },
        ],
        format="json",
        options={"temperature": 0},
    )
    content = extract_ollama_content(response).strip()
    indices = None
    try:
        payload = json.loads(content)
        if isinstance(payload, dict):
            indices = payload.get("selected_indices")
        elif isinstance(payload, list):
            indices = payload
    except json.JSONDecodeError:
        array_matches = re.findall(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]", content)
        if array_matches:
            parsed_array = json.loads(array_matches[-1])
            if isinstance(parsed_array, list):
                indices = parsed_array
    if not isinstance(indices, list) or len(indices) != selection_count:
        raise ValueError(f"Compact selector returned invalid indices: {indices!r}")
    if any(type(index) is not int for index in indices):
        raise ValueError(f"Compact selector returned non-integer indices: {indices!r}")
    if len(set(indices)) != selection_count or any(
        index < 0 or index >= len(candidate_snippets) for index in indices
    ):
        raise ValueError(
            f"Compact selector returned out-of-range or duplicate indices: {indices!r}"
        )
    return [candidate_snippets[index] for index in indices]


def select_evidence_view_with_llm(
    ollama_client,
    preference_guidance: str,
    job_title: str,
    job_location: str,
    focused_snippets: list[str],
    complete_description: str,
) -> str:
    """Choose focused or global source context without producing an assessment."""
    focused_block = "\n".join(f"- {snippet}" for snippet in focused_snippets)
    response = ollama_client.chat(
        model=(
            str(os.environ.get("EVIDENCE_SELECTOR_MODEL", "") or "").strip()
            or DEFAULT_EVIDENCE_SELECTOR_MODEL
        ),
        messages=[
            {
                "role": "system",
                "content": (
                    "Choose which source-evidence view is more reliable for a separate "
                    "job-preference scorer. The focused view contains the passages with "
                    "highest query relevance; the global view contains the complete job "
                    "description. Choose focused when the title, location, or focused "
                    "passages provide explicit decisive support or conflict and unrelated "
                    "global text would dilute it. Choose global when the preference depends "
                    "on the overall balance, frequency, centrality, hands-on scope, or "
                    "limitations across duties and the focused passages alone could "
                    "misrepresent that. Do not assess the fit, assign a score, summarize, "
                    "or invent evidence. Return one JSON object with exactly one string "
                    "field named evidence_view whose value is focused or global."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Preference Guidance: {preference_guidance}\n"
                    f"Job Title: {job_title}\n"
                    f"Job Location: {job_location}\n\n"
                    f"Focused view:\n{focused_block}\n\n"
                    f"Global view:\n{complete_description}\n"
                ),
            },
        ],
        format="json",
        options={"temperature": 0},
    )
    payload = json.loads(extract_ollama_content(response))
    selected_view = payload.get("evidence_view") if isinstance(payload, dict) else None
    if selected_view not in {"focused", "global"}:
        raise ValueError(f"Evidence-view selector returned invalid view: {selected_view!r}")
    return selected_view


def build_prompt(job, company, identity, preference, snippets=None, include_system_prompt=True):
    job_title = get_field(job, "title", "")
    job_location = get_field(job, "location", "")

    preference_guidance = get_field(preference, "guidance", "")
    snippet_lines = snippets or []
    if snippet_lines:
        snippet_block = "\n".join(f"- {snippet}" for snippet in snippet_lines)
    else:
        snippet_block = "- (no relevant snippets available)"

    system_instruction = SCORING_SYSTEM_INSTRUCTION

    user_prompt = (
        f"Preference Guidance: {preference_guidance}\n\n"
        f"Job Title: {job_title}\n"
        f"Job Location: {job_location}\n"
        "Relevant Context Snippets:\n"
        f"{snippet_block}\n"
        "\n"
    )

    # When include_system_prompt is False, return empty system instruction
    if not include_system_prompt:
        system_instruction = ""

    return system_instruction, user_prompt


def upsert_identity_score_doc(
    job_preference_scores_col,
    job_id_str,
    identity_id_str,
    *,
    status,
    preference_scores=None,
    weighted_score=0.0,
    weighted_score_available=False,
):
    score_proto = common_pb2.JobPreferenceScore(
        job_id=job_id_str,
        identity_id=identity_id_str,
        scoring_status=status,
        weighted_score=float(weighted_score),
        weighted_score_available=bool(weighted_score_available),
    )

    for pref_score in preference_scores or []:
        score_proto.preference_scores.append(
            common_pb2.PreferenceScore(
                preference_key=str(pref_score.get("preference_key", "")),
                preference_guidance=str(pref_score.get("preference_guidance", "")),
                preference_weight=float(pref_score.get("preference_weight", 0) or 0),
                score=int(pref_score.get("score", 0) or 0),
                score_available=bool(pref_score.get("score_available", True)),
            )
        )

    score_doc = MessageToDict(score_proto, preserving_proto_field_name=True)
    score_doc.pop("id", None)
    score_doc["job_id"] = job_id_str
    score_doc["identity_id"] = identity_id_str
    score_doc["scoring_status"] = scoring_status_to_bson(status)
    score_doc["weighted_score"] = float(weighted_score)
    score_doc["weighted_score_available"] = bool(weighted_score_available)
    score_doc["preference_scores"] = preference_scores or []

    job_preference_scores_col.update_one(
        {"job_id": job_id_str, "identity_id": identity_id_str},
        {"$set": score_doc},
        upsert=True,
    )


def resolve_scoring_context(job_descriptions_col, companies_col, identities_col, job_id, identity_id=None):
    job_object_id = parse_object_id(job_id)
    if not job_object_id:
        return None, "invalid_job_id"

    job_doc = job_descriptions_col.find_one({"_id": job_object_id})
    if not job_doc:
        return None, "job_not_found"

    company_ref = job_doc.get("company")
    if company_ref is None:
        company_ref = job_doc.get("company_id")
    company_object_id = parse_object_id(company_ref)
    company_doc = None
    if company_object_id:
        company_doc = companies_col.find_one({"_id": company_object_id})
    elif isinstance(company_ref, str):
        company_doc = companies_col.find_one({"_id": company_ref})

    if not company_doc:
        return (job_doc, None, None, None), "company_not_found"

    # Resolve identity: prefer direct lookup by identity_id when provided (post-multi-user
    # migration path). Fall back to field-based inference for legacy payloads that predate
    # the explicit identity_id field in queue messages.
    identity_doc = None
    if identity_id:
        identity_object_id = parse_object_id(identity_id)
        if identity_object_id:
            identity_doc = identities_col.find_one({"_id": identity_object_id})
        if not identity_doc:
            # identity_id provided but not found — fail explicitly, do not fall back.
            return (job_doc, company_doc, None, None), "identity_not_found"
    else:
        # Legacy fallback: infer identity via company → field linkage.
        field_ref = company_doc.get("field")
        if field_ref is None:
            field_ref = company_doc.get("field_id")
        field_object_id = parse_object_id(field_ref)

        if field_object_id:
            identity_doc = identities_col.find_one({"field": field_object_id})
            if not identity_doc:
                identity_doc = identities_col.find_one({"field_id": field_object_id})
        if not identity_doc and isinstance(field_ref, str):
            identity_doc = identities_col.find_one({"field": field_ref})
            if not identity_doc:
                identity_doc = identities_col.find_one({"field_id": field_ref})

        if not identity_doc:
            return (job_doc, company_doc, None, None), "identity_not_found"

    preferences = identity_doc.get("preferences", [])
    enabled_preferences = [pref for pref in preferences if isinstance(pref, dict) and pref.get("enabled", False)]
    if not enabled_preferences:
        return (job_doc, company_doc, identity_doc, []), "no_enabled_preferences"

    return (job_doc, company_doc, identity_doc, enabled_preferences), None


def request_preference_score(
    ollama_client,
    model_name,
    job_id,
    preference,
    job_doc,
    company_doc,
    identity_doc,
    relevant_snippets,
    stage,
):
    preference_key = get_field(preference, "key", "")
    include_system_prompt = os.environ.get("EVAL_WITH_SYSTEM_PROMPT", "true").lower() in ("true", "1", "yes")
    system_instruction, user_prompt = build_prompt(
        job_doc,
        company_doc,
        identity_doc,
        preference,
        snippets=relevant_snippets,
        include_system_prompt=include_system_prompt,
    )

    messages = []
    if include_system_prompt and system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": user_prompt})

    scoring_options = resolve_scoring_options()
    request_payload = {
        "job_id": job_id,
        "preference_key": preference_key,
        "stage": stage,
        "model": model_name,
        "messages": messages,
        "options": scoring_options,
    }
    print(f"debug: Ollama request: {safe_json_dump(request_payload)}")

    response = ollama_client.chat(
        model=model_name,
        messages=messages,
        options=scoring_options,
    )
    print(
        "debug: Ollama response: "
        + safe_json_dump(
            {
                "job_id": job_id,
                "preference_key": preference_key,
                "stage": stage,
                "response": response,
            }
        )
    )

    content = extract_ollama_content(response)
    print(
        "debug: Ollama response content: "
        + safe_json_dump(
            {
                "job_id": job_id,
                "preference_key": preference_key,
                "stage": stage,
                "content": content,
            }
        )
    )

    score, score_available, parse_strategy = parse_ollama_response(content)

    if score_available is False:
        print(
            "debug: Parsed model score as unavailable: "
            + safe_json_dump(
                {
                    "job_id": job_id,
                    "preference_key": preference_key,
                    "stage": stage,
                    "parse_strategy": parse_strategy,
                }
            )
        )
        return {"score": 0, "score_available": False}

    if score is None:
        raise ValueError(
            "Invalid model response: missing score "
            + safe_json_dump(
                {
                    "parse_reason": parse_strategy,
                    "response_content": content,
                    "raw_response": response,
                }
            )
        )

    if score < 0 or score > 5:
        raise ValueError(
            f"Invalid model response: score out of range ({score}) "
            + safe_json_dump(
                {
                    "parse_reason": parse_strategy,
                    "response_content": content,
                    "raw_response": response,
                }
            )
        )

    print(
        "debug: Parsed model score: "
        + safe_json_dump(
            {
                "job_id": job_id,
                "preference_key": preference_key,
                "stage": stage,
                "score": score,
                "score_available": True,
                "parse_strategy": parse_strategy,
            }
        )
    )

    return {"score": score, "score_available": True}


def request_preference_score_with_confidence(
    ollama_client,
    model_name: str,
    preference,
    job_doc,
    company_doc,
    identity_doc,
    relevant_snippets: list[str],
) -> tuple[dict[str, Any], float]:
    """Return a canonical direct score and its greedy first-token confidence."""
    system_instruction, user_prompt = build_prompt(
        job_doc,
        company_doc,
        identity_doc,
        preference,
        snippets=relevant_snippets,
        include_system_prompt=True,
    )
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": user_prompt})
    response = ollama_client._client.post(
        "/api/chat",
        json={
            "model": model_name,
            "messages": messages,
            "stream": False,
            "logprobs": True,
            "top_logprobs": 20,
            "options": {"temperature": 0},
        },
    )
    response.raise_for_status()
    payload = response.json()
    content = str(payload.get("message", {}).get("content", "") or "")
    score, available, parse_strategy = parse_ollama_response(content)
    first_logprobs = payload.get("logprobs") or []
    confidence = (
        float(first_logprobs[0].get("logprob", float("-inf")))
        if first_logprobs
        else float("-inf")
    )
    if available is False:
        return {"score": 0, "score_available": False}, confidence
    if score is None or score < 0 or score > 5:
        raise ValueError(
            "Invalid confidence-scoring response: "
            + safe_json_dump(
                {
                    "parse_reason": parse_strategy,
                    "content": content,
                    "payload": payload,
                }
            )
        )
    return {"score": score, "score_available": True}, confidence


def pointwise_rerank_scoring_snippets(
    ollama_client,
    model_name: str,
    job_id: str,
    preference,
    job_doc,
    company_doc,
    identity_doc,
    candidate_snippets: list[str],
    top_k: int = SNIPPET_RERANKED_TOP_K,
    preserve_input_order: bool = False,
) -> list[str]:
    """Use the final scorer's canonical judgment as a pointwise passage ranker."""
    reranking_model_name = (
        str(os.environ.get("POINTWISE_RERANKER_MODEL", "") or "").strip()
        or model_name
    )
    use_logprob_expectation = str(
        os.environ.get("POINTWISE_USE_LOGPROBS", "") or ""
    ).lower() in {"1", "true", "yes"}
    ranked_candidates: list[tuple[float, int, str]] = []
    for index, snippet in enumerate(candidate_snippets[:SNIPPET_PROBE_K]):
        if use_logprob_expectation:
            rank_score = request_preference_score_expectation(
                ollama_client,
                reranking_model_name,
                preference,
                job_doc,
                company_doc,
                identity_doc,
                [snippet],
            )
        else:
            result = request_preference_score(
                ollama_client,
                reranking_model_name,
                job_id,
                preference,
                job_doc,
                company_doc,
                identity_doc,
                [snippet],
                f"pointwise_evidence_{index}",
            )
            rank_score = (
                float(result.get("score", 0) or 0)
                if result.get("score_available") is not False
                else -1.0
            )
        sort_score = rank_score
        if str(os.environ.get("POINTWISE_RANK_MODE", "") or "").strip() == "extremity":
            sort_score = abs(rank_score - 2.5) if rank_score >= 0.0 else -1.0
        ranked_candidates.append((sort_score, index, snippet))

    ranked_candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = ranked_candidates[: max(0, top_k)]
    if preserve_input_order:
        selected.sort(key=lambda item: item[1])
    return [item[2] for item in selected]


def request_preference_score_expectation(
    ollama_client,
    model_name: str,
    preference,
    job_doc,
    company_doc,
    identity_doc,
    relevant_snippets: list[str],
) -> float:
    """Return the scorer's continuous first-token ordinal expectation."""
    system_instruction, user_prompt = build_prompt(
        job_doc,
        company_doc,
        identity_doc,
        preference,
        snippets=relevant_snippets,
        include_system_prompt=True,
    )
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": user_prompt})
    response = ollama_client._client.post(
        "/api/chat",
        json={
            "model": model_name,
            "messages": messages,
            "stream": False,
            "logprobs": True,
            "top_logprobs": 20,
            "options": {"temperature": 0},
        },
    )
    response.raise_for_status()
    payload = response.json()
    content = str(payload.get("message", {}).get("content", "") or "")
    score, available, _ = parse_ollama_response(content)
    if available is False:
        return -1.0
    first_token = (payload.get("logprobs") or [{}])[0]
    alternatives = first_token.get("top_logprobs", [])
    probabilities: dict[int, float] = {}
    for alternative in alternatives:
        token = str(alternative.get("token", "") or "")
        if token in {"0", "1", "2", "3", "4", "5"}:
            probabilities[int(token)] = math.exp(float(alternative["logprob"]))
    mass = sum(probabilities.values())
    if mass > 0.0:
        return sum(label * probability for label, probability in probabilities.items()) / mass
    if score is None:
        raise ValueError(f"Pointwise logprob response has no ordinal score: {payload!r}")
    return float(score)


def score_preference(
    ollama_client,
    model_name,
    test_mode,
    job_id,
    preference,
    job_doc,
    company_doc,
    identity_doc,
):
    preference_key = get_field(preference, "key", "")
    preference_guidance = str(get_field(preference, "guidance", "") or get_field(preference, "label", ""))
    if test_mode:
        return {"score": stable_test_score(job_id, preference_key), "score_available": True}

    job_description = normalize_description_markdown(
        get_field(job_doc, "description", "")
    )
    normalize_title = should_normalize_job_title()
    normalize_location = should_normalize_job_location()
    scoring_preference = preference
    scoring_job_doc = job_doc
    if normalize_location or normalize_title:
        scoring_job_doc = copy.deepcopy(job_doc)
    if normalize_title:
        try:
            set_field(
                scoring_job_doc,
                "title",
                normalize_job_title(
                    ollama_client,
                    str(get_field(job_doc, "title", "") or ""),
                ),
            )
        except Exception as exc:
            print(
                "warn: Failed to normalize job-title metadata: "
                + safe_json_dump(
                    {
                        "job_id": job_id,
                        "preference_key": preference_key,
                        "error": str(exc),
                    }
                )
            )
    if normalize_location:
        try:
            normalized_location = normalize_job_location(
                ollama_client,
                str(get_field(job_doc, "location", "") or ""),
            )
            if (
                normalized_location == "remote"
                and should_render_explicit_remote_location()
            ):
                normalized_location = "fully remote"
            set_field(scoring_job_doc, "location", normalized_location)
        except Exception as exc:
            print(
                "warn: Failed to normalize job-location metadata: "
                + safe_json_dump(
                    {
                        "job_id": job_id,
                        "preference_key": preference_key,
                        "error": str(exc),
                    }
                )
            )

    baseline_snippets: list[str] = []
    try:
        baseline_snippets = retrieve_relevant_snippets(
            job_description,
            preference_guidance,
            top_k=SNIPPET_TOP_K,
        )
    except Exception as exc:
        print(
            "warn: Failed to retrieve baseline scoring evidence: "
            + safe_json_dump(
                {
                    "job_id": job_id,
                    "preference_key": preference_key,
                    "error": str(exc),
                }
            )
        )

    initial_result = request_preference_score(
        ollama_client,
        model_name,
        job_id,
        preference,
        scoring_job_doc,
        company_doc,
        identity_doc,
        baseline_snippets,
        "availability",
    )
    if initial_result.get("score_available") is False:
        return initial_result

    if should_normalize_preference_guidance():
        try:
            normalized_guidance = normalize_preference_guidance(
                ollama_client,
                preference_guidance,
            )
            scoring_preference = copy.deepcopy(preference)
            if isinstance(scoring_preference, dict):
                scoring_preference["guidance"] = normalized_guidance
            else:
                setattr(scoring_preference, "guidance", normalized_guidance)
        except Exception as exc:
            print(
                "warn: Failed to normalize preference guidance: "
                + safe_json_dump(
                    {
                        "job_id": job_id,
                        "preference_key": preference_key,
                        "error": str(exc),
                    }
                )
            )

    evidence_scope = ""
    if resolve_evidence_scope_routing_mode() == "llm":
        try:
            evidence_scope = classify_preference_evidence_scope(
                ollama_client,
                preference_guidance,
            )
        except Exception as exc:
            print(
                "warn: Failed to classify preference evidence scope: "
                + safe_json_dump(
                    {
                        "job_id": job_id,
                        "preference_key": preference_key,
                        "error": str(exc),
                    }
                )
            )
            evidence_scope = "description"
    try:
        retrieval_query = expand_retrieval_query(ollama_client, preference_guidance)
        candidate_query = resolve_candidate_query_prefix() + retrieval_query
        if resolve_candidate_retrieval_mode() == "raw_plus_expanded_query":
            candidate_query = preference_guidance + "\n" + candidate_query
        expanded_candidates = retrieve_relevant_snippets(
            job_description,
            candidate_query,
            top_k=SNIPPET_CANDIDATE_K,
            model_name=resolve_candidate_embedding_model_name(),
            exclude_heading_only=(evidence_scope == "description"),
        )
        candidates = expanded_candidates
        if resolve_candidate_retrieval_mode() == "raw_only":
            candidates = retrieve_relevant_snippets(
                job_description,
                preference_guidance,
                top_k=SNIPPET_CANDIDATE_K,
                model_name=resolve_candidate_embedding_model_name(),
            )
        elif resolve_candidate_retrieval_mode() == "raw_expanded_rrf":
            raw_candidates = retrieve_relevant_snippets(
                job_description,
                preference_guidance,
                top_k=SNIPPET_CANDIDATE_K,
                model_name=resolve_candidate_embedding_model_name(),
            )
            candidates = reciprocal_rank_fusion(
                [raw_candidates, expanded_candidates],
                top_k=SNIPPET_CANDIDATE_K,
            )
        reranking_query = preference_guidance
        if should_rerank_with_job_context():
            reranking_query = (
                f"Preference Guidance: {preference_guidance}\n"
                f"Job Title: {get_field(scoring_job_doc, 'title', '')}\n"
                f"Job Location: {get_field(scoring_job_doc, 'location', '')}\n"
            )
        late_interaction_model = resolve_late_interaction_reranking_model_name()
        if resolve_evidence_view_routing_mode() == "confidence":
            jina_snippets = rerank_scoring_snippets(
                reranking_query,
                candidates,
                top_k=SNIPPET_RERANKED_TOP_K,
            )
            pointwise_snippets = pointwise_rerank_scoring_snippets(
                ollama_client,
                model_name,
                job_id,
                preference,
                scoring_job_doc,
                company_doc,
                identity_doc,
                candidates,
                top_k=SNIPPET_RERANKED_TOP_K,
            )
            jina_result, jina_confidence = request_preference_score_with_confidence(
                ollama_client,
                model_name,
                preference,
                scoring_job_doc,
                company_doc,
                identity_doc,
                jina_snippets,
            )
            pointwise_result, pointwise_confidence = (
                request_preference_score_with_confidence(
                    ollama_client,
                    model_name,
                    preference,
                    scoring_job_doc,
                    company_doc,
                    identity_doc,
                    pointwise_snippets,
                )
            )
            return (
                pointwise_result
                if pointwise_confidence > jina_confidence
                else jina_result
            )
        if resolve_evidence_selection_mode() == "compact_llm":
            try:
                reranked_snippets = select_scoring_snippets_with_compact_llm(
                    ollama_client,
                    preference_guidance,
                    candidates,
                    job_title=str(get_field(scoring_job_doc, "title", "") or ""),
                    job_location=str(get_field(scoring_job_doc, "location", "") or ""),
                    top_k=SNIPPET_RERANKED_TOP_K,
                )
            except Exception as selector_exc:
                print(
                    "warn: Compact evidence selector failed; using cross-encoder: "
                    + safe_json_dump(
                        {
                            "job_id": job_id,
                            "preference_key": preference_key,
                            "error": str(selector_exc),
                        }
                    )
                )
                reranked_snippets = rerank_scoring_snippets(
                    reranking_query,
                    candidates,
                    top_k=SNIPPET_RERANKED_TOP_K,
                )
        elif late_interaction_model:
            reranked_snippets = late_interaction_rerank_scoring_snippets(
                reranking_query,
                candidates,
                model_name=late_interaction_model,
                top_k=SNIPPET_RERANKED_TOP_K,
            )
        elif (
            should_use_scorer_pointwise_reranking_cascade()
            or evidence_scope == "location_metadata"
        ):
            shortlist = rerank_scoring_snippets(
                reranking_query,
                candidates,
                top_k=4,
            )
            reranked_snippets = pointwise_rerank_scoring_snippets(
                ollama_client,
                model_name,
                job_id,
                preference,
                scoring_job_doc,
                company_doc,
                identity_doc,
                shortlist,
                top_k=SNIPPET_RERANKED_TOP_K,
                preserve_input_order=True,
            )
        elif should_use_scorer_pointwise_reranking():
            reranked_snippets = pointwise_rerank_scoring_snippets(
                ollama_client,
                model_name,
                job_id,
                preference,
                scoring_job_doc,
                company_doc,
                identity_doc,
                candidates,
                top_k=SNIPPET_RERANKED_TOP_K,
            )
        else:
            reranked_snippets = rerank_scoring_snippets(
                reranking_query,
                candidates,
                top_k=SNIPPET_RERANKED_TOP_K,
            )
        if should_preserve_candidate_order():
            selected_snippets = set(reranked_snippets)
            reranked_snippets = [
                snippet for snippet in candidates if snippet in selected_snippets
            ][:SNIPPET_RERANKED_TOP_K]
    except Exception as exc:
        print(
            "warn: Failed to expand or rerank scoring evidence: "
            + safe_json_dump(
                {
                    "job_id": job_id,
                    "preference_key": preference_key,
                    "error": str(exc),
                }
            )
        )
        return initial_result

    if not reranked_snippets:
        return initial_result
    final_snippets = reranked_snippets
    if resolve_evidence_fusion_mode() == "raw_and_reranked":
        final_snippets = fuse_raw_and_reranked_snippets(
            baseline_snippets,
            reranked_snippets,
            top_k=SNIPPET_RERANKED_TOP_K,
        )
    if (
        resolve_evidence_view_routing_mode() == "confidence_global"
        and job_description
    ):
        focused_result, focused_confidence = request_preference_score_with_confidence(
            ollama_client,
            model_name,
            scoring_preference,
            scoring_job_doc,
            company_doc,
            identity_doc,
            final_snippets,
        )
        global_result, global_confidence = request_preference_score_with_confidence(
            ollama_client,
            model_name,
            scoring_preference,
            scoring_job_doc,
            company_doc,
            identity_doc,
            [job_description],
        )
        return (
            global_result
            if global_confidence > focused_confidence
            else focused_result
        )
    if resolve_final_order_routing_mode() == "confidence" and len(final_snippets) >= 2:
        forward_result, forward_confidence = request_preference_score_with_confidence(
            ollama_client,
            model_name,
            scoring_preference,
            scoring_job_doc,
            company_doc,
            identity_doc,
            final_snippets,
        )
        reverse_result, reverse_confidence = request_preference_score_with_confidence(
            ollama_client,
            model_name,
            scoring_preference,
            scoring_job_doc,
            company_doc,
            identity_doc,
            list(reversed(final_snippets)),
        )
        return reverse_result if reverse_confidence > forward_confidence else forward_result
    return request_preference_score(
        ollama_client,
        model_name,
        job_id,
        scoring_preference,
        scoring_job_doc,
        company_doc,
        identity_doc,
        final_snippets,
        "expanded_query_cross_encoder_final",
    )


def build_preference_score_doc(preference, score_result):
    preference_key = get_field(preference, "key", "")
    scored_at = now_proto_timestamp()
    score_value = int(score_result.get("score", 0) or 0)
    score_available = bool(score_result.get("score_available", True))

    preference_score = common_pb2.PreferenceScore(
        preference_key=preference_key,
        preference_guidance=str(get_field(preference, "guidance", "") or get_field(preference, "label", "")),
        preference_weight=float(get_field(preference, "weight", 0) or 0),
        score=score_value,
        scored_at=scored_at,
        score_available=score_available,
    )

    score_doc = MessageToDict(preference_score, preserving_proto_field_name=True)
    score_doc["scored_at"] = timestamp_dict_from_proto(scored_at)
    return score_doc


def persist_identity_scores(job_preference_scores_col, job_doc, identity_doc, preference_scores):
    job_id_str = get_message_id(job_doc)
    identity_id_str = get_message_id(identity_doc)
    upsert_identity_score_doc(
        job_preference_scores_col,
        job_id_str,
        identity_id_str,
        status=common_pb2.SCORING_STATUS_QUEUED,
        preference_scores=preference_scores,
        weighted_score_available=False,
    )


def compute_and_persist_aggregate(job_preference_scores_col, job_doc, identity_doc):
    job_id_str = get_message_id(job_doc)
    identity_id_str = get_message_id(identity_doc)

    score_doc = job_preference_scores_col.find_one(
        {
            "job_id": job_id_str,
            "identity_id": identity_id_str,
        }
    )

    if not score_doc:
        raise ValueError("No identity score document found to aggregate")

    preference_scores = score_doc.get("preference_scores", [])
    if not isinstance(preference_scores, list) or not preference_scores:
        raise ValueError("No preference scores found to aggregate")

    weighted_sum = 0.0
    total_weight = 0.0
    for doc in preference_scores:
        score_available = doc.get("score_available")
        if score_available is False:
            continue
        score = int(doc.get("score", 0))
        weight = float(doc.get("preference_weight", 0))
        weighted_sum += score * weight
        total_weight += weight

    weighted_score = 0.0
    weighted_score_available = False
    if total_weight > 0:
        weighted_score = weighted_sum / total_weight
        weighted_score_available = True

    upsert_identity_score_doc(
        job_preference_scores_col,
        job_id_str,
        identity_id_str,
        status=common_pb2.SCORING_STATUS_SCORED,
        preference_scores=preference_scores,
        weighted_score=weighted_score,
        weighted_score_available=weighted_score_available,
    )


def process_scoring_job(
    job_id,
    job_descriptions_col,
    companies_col,
    identities_col,
    job_preference_scores_col,
    redis_client,
    scoring_progress_channel,
    scoring_run_manager,
    ollama_client,
    model_name,
    test_mode,
    identity_id=None,
):
    context, error = resolve_scoring_context(job_descriptions_col, companies_col, identities_col, job_id, identity_id=identity_id)

    if error == "invalid_job_id":
        print(f"error: Invalid job_id '{job_id}'.")
        return

    if error == "job_not_found":
        print(f"error: job_id '{job_id}' not found.")
        return

    if context is None:
        print(f"error: Empty scoring context for job_id '{job_id}'.")
        return

    job_doc, company_doc, identity_doc, enabled_preferences = context
    scoring_state = None
    if identity_doc:
        scoring_state, should_publish_start = scoring_run_manager.start_or_reuse(identity_doc)
        if scoring_state and should_publish_start:
            try:
                publish_scoring_progress(
                    redis_client,
                    scoring_progress_channel,
                    scoring_state,
                    "running",
                    message="Scoring started",
                )
            except Exception as exc:
                print(f"warn: Failed to publish scoring progress start event: {exc}")

    if error in {"company_not_found", "identity_not_found", "no_enabled_preferences"}:
        if identity_doc:
            upsert_identity_score_doc(
                job_preference_scores_col,
                get_message_id(job_doc),
                get_message_id(identity_doc),
                status=common_pb2.SCORING_STATUS_SKIPPED,
                preference_scores=[],
                weighted_score_available=False,
            )
        print(f"warn: Skipping job '{job_id}' due to missing prerequisites ({error}).")
        if identity_doc and scoring_state:
            scoring_state, completed_run = scoring_run_manager.advance(identity_doc)
            try:
                publish_scoring_progress(
                    redis_client,
                    scoring_progress_channel,
                    scoring_state,
                    "completed" if completed_run else "running",
                    message="Scoring completed" if completed_run else "Scoring in progress",
                )
            except Exception as exc:
                print(f"warn: Failed to publish scoring progress update: {exc}")
        return

    job_proto = job_proto_from_doc(job_doc)
    company_proto = company_proto_from_doc(company_doc)
    identity_proto = identity_proto_from_doc(identity_doc)
    enabled_preferences_proto = [pref for pref in identity_proto.preferences if pref.enabled]

    if not enabled_preferences_proto:
        upsert_identity_score_doc(
            job_preference_scores_col,
            get_message_id(job_proto),
            get_message_id(identity_proto),
            status=common_pb2.SCORING_STATUS_SKIPPED,
            preference_scores=[],
            weighted_score_available=False,
        )
        print(f"warn: Skipping job '{job_id}' due to missing preferences list.")
        return

    job_id_str = get_message_id(job_proto)
    identity_id_str = get_message_id(identity_proto)

    # Load existing per-preference scores before marking QUEUED so we can reuse
    # entries whose guidance snapshot still matches the current identity preference.
    existing_pref_map = {}
    existing_doc = job_preference_scores_col.find_one(
        {"job_id": job_id_str, "identity_id": identity_id_str}
    )
    if existing_doc:
        for entry in (existing_doc.get("preference_scores") or []):
            pref_key = entry.get("preference_key", "")
            if pref_key:
                existing_pref_map[pref_key] = entry

    print(
        "debug: Per-preference scoring setup: "
        + safe_json_dump(
            {
                "job_id": job_id_str,
                "identity_id": identity_id_str,
                "enabled_preferences": len(enabled_preferences_proto),
                "existing_preferences": len(existing_pref_map),
                "test_mode": bool(test_mode),
            }
        )
    )

    upsert_identity_score_doc(
        job_preference_scores_col,
        job_id_str,
        identity_id_str,
        status=common_pb2.SCORING_STATUS_QUEUED,
        preference_scores=[],
        weighted_score_available=False,
    )

    try:
        preference_scores = []
        reused_count = 0
        rescored_count = 0
        for preference in enabled_preferences_proto:
            pref_key = get_field(preference, "key", "")
            current_guidance = str(
                get_field(preference, "guidance", "") or get_field(preference, "label", "")
            )
            stored = existing_pref_map.get(pref_key)
            if stored and stored.get("preference_guidance") == current_guidance:
                # Guidance unchanged: reuse existing score and scored_at; update weight snapshot.
                reused = dict(stored)
                reused["preference_weight"] = float(get_field(preference, "weight", 0) or 0)
                if "score_available" not in reused:
                    reused["score_available"] = True
                preference_scores.append(reused)
                reused_count += 1
                print(
                    "debug: Reusing stored preference score: "
                    + safe_json_dump(
                        {
                            "job_id": job_id_str,
                            "identity_id": identity_id_str,
                            "preference_key": pref_key,
                            "stored_score": reused.get("score"),
                            "score_available": reused.get("score_available", True),
                            "stored_guidance": stored.get("preference_guidance", ""),
                            "current_guidance": current_guidance,
                        }
                    )
                )
            else:
                reason = "guidance_changed_or_missing"
                if not stored:
                    reason = "no_existing_score"
                elif stored.get("preference_guidance") != current_guidance:
                    reason = "guidance_changed"
                print(
                    "debug: Scoring preference via model: "
                    + safe_json_dump(
                        {
                            "job_id": job_id_str,
                            "identity_id": identity_id_str,
                            "preference_key": pref_key,
                            "reason": reason,
                            "previous_guidance": (stored or {}).get("preference_guidance", ""),
                            "current_guidance": current_guidance,
                        }
                    )
                )
                score_result = score_preference(
                    ollama_client,
                    model_name,
                    test_mode,
                    job_id_str,
                    preference,
                    job_proto,
                    company_proto,
                    identity_proto,
                )
                preference_scores.append(build_preference_score_doc(preference, score_result))
                rescored_count += 1
                print(
                    "debug: Model score computed for preference: "
                    + safe_json_dump(
                        {
                            "job_id": job_id_str,
                            "identity_id": identity_id_str,
                            "preference_key": pref_key,
                            "score": score_result.get("score", 0),
                            "score_available": score_result.get("score_available", True),
                        }
                    )
                )

        print(
            "info: Per-preference scoring summary: "
            + safe_json_dump(
                {
                    "job_id": job_id_str,
                    "identity_id": identity_id_str,
                    "enabled_preferences": len(enabled_preferences_proto),
                    "reused_preferences": reused_count,
                    "rescored_preferences": rescored_count,
                }
            )
        )

        persist_identity_scores(
            job_preference_scores_col,
            job_proto,
            identity_proto,
            preference_scores,
        )

        compute_and_persist_aggregate(
            job_preference_scores_col,
            job_proto,
            identity_proto,
        )

        if scoring_state:
            scoring_state, completed_run = scoring_run_manager.advance(identity_doc)
            try:
                publish_scoring_progress(
                    redis_client,
                    scoring_progress_channel,
                    scoring_state,
                    "completed" if completed_run else "running",
                    message="Scoring completed" if completed_run else "Scoring in progress",
                )
            except Exception as exc:
                print(f"warn: Failed to publish scoring progress update: {exc}")

        print(f"info: Successfully scored job '{job_id}'.")

    except Exception as exc:
        upsert_identity_score_doc(
            job_preference_scores_col,
            get_message_id(job_proto),
            get_message_id(identity_proto),
            status=common_pb2.SCORING_STATUS_FAILED,
            preference_scores=[],
            weighted_score_available=False,
        )
        if identity_doc and scoring_state:
            scoring_state, completed_run = scoring_run_manager.advance(identity_doc)
            try:
                publish_scoring_progress(
                    redis_client,
                    scoring_progress_channel,
                    scoring_state,
                    "failed" if completed_run else "running",
                    message="Scoring failed" if completed_run else "Scoring in progress",
                    reason="job_failed" if completed_run else "",
                )
            except Exception as publish_exc:
                print(f"warn: Failed to publish scoring failure progress event: {publish_exc}")
        print(f"error: Failed to score job '{job_id}': {exc}")


def parse_worker_pool_size(raw_value):
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        print(f"warn: Invalid AI_SCORER_OLLAMA_PARALLELISM='{raw_value}', falling back to 1")
        return 1

    if value <= 0:
        print(f"warn: AI_SCORER_OLLAMA_PARALLELISM must be > 0 (got {value}), falling back to 1")
        return 1

    return value


def build_ollama_client(ollama_host):
    return ollama.Client(host=ollama_host) if ollama_host else ollama.Client()


def ensure_score_collection_indexes(job_preference_scores_col):
    # Enforce single score document per (job_id, identity_id).
    job_preference_scores_col.create_index(
        [("job_id", ASCENDING), ("identity_id", ASCENDING)],
        unique=True,
        name="uq_job_identity_score",
    )


def scoring_worker_loop(
    worker_id,
    work_queue,
    global_db,
    mongo_client,
    redis_client,
    scoring_progress_channel,
    user_managers,
    user_managers_lock,
    ollama_host,
    model_name,
    test_mode,
):
    ollama_client = build_ollama_client(ollama_host)
    print(f"info: Worker {worker_id} started")

    while True:
        item = work_queue.get()
        if item is None:
            work_queue.task_done()
            print(f"info: Worker {worker_id} stopping")
            return

        job_id = item["job_id"]
        user_id = item["user_id"]
        identity_id = item.get("identity_id") or None

        # Derive the per-user database from user_id (which is the JWT sub claim,
        # already a SHA-256-derived hex string set at login time).
        user_db = mongo_client[f"cover_letter_{user_id}"]
        identities_col = user_db["identities"]
        job_preference_scores_col = user_db["job-preference-scores"]

        # Lazily create a ScoringRunManager for this user.
        with user_managers_lock:
            if user_id not in user_managers:
                user_managers[user_id] = ScoringRunManager(
                    global_db["job-descriptions"],
                    global_db["companies"],
                    job_preference_scores_col,
                )
            scoring_run_manager = user_managers[user_id]

        print(f"info: Worker {worker_id} processing job '{job_id}' for user '{user_id}'")
        try:
            process_scoring_job(
                str(job_id),
                global_db["job-descriptions"],
                global_db["companies"],
                identities_col,
                job_preference_scores_col,
                redis_client,
                scoring_progress_channel,
                scoring_run_manager,
                ollama_client,
                model_name,
                test_mode,
                identity_id=identity_id,
            )
        except Exception as exc:
            print(f"error: Worker {worker_id} failed while processing job '{job_id}': {exc}")
        finally:
            work_queue.task_done()


def main():
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))
    queue_name = os.environ.get("JOB_SCORING_QUEUE_NAME", "job_scoring_queue")
    scoring_progress_channel = os.environ.get("SCORING_PROGRESS_CHANNEL_NAME", "scoring_progress_channel")

    mongo_uri = os.environ.get("MONGO_HOST", "mongodb://localhost:27017/")
    mongo_db_name = os.environ.get("DB_NAME", "cover_letter_global")

    test_mode = os.environ.get("AI_SCORER_TEST_MODE", "0") == "1"
    ollama_host = os.environ.get("OLLAMA_HOST")
    ollama_model = os.environ.get("OLLAMA_MODEL")
    embedding_model_name = str(os.environ.get("EMBEDDING_MODEL", "") or "").strip()
    worker_pool_size = parse_worker_pool_size(os.environ.get("AI_SCORER_OLLAMA_PARALLELISM", "1"))

    if not test_mode:
        if not ollama_host:
            raise RuntimeError("Environment variable OLLAMA_HOST is required when AI_SCORER_TEST_MODE != 1")
        if not ollama_model:
            raise RuntimeError("Environment variable OLLAMA_MODEL is required when AI_SCORER_TEST_MODE != 1")

    if test_mode and not ollama_model:
        ollama_model = "test-mode-model"

    effective_embedding_model = embedding_model_name or DEFAULT_EMBEDDING_MODEL
    try:
        get_embedding_model(effective_embedding_model)
    except Exception as exc:
        raise RuntimeError(f"Failed to initialize embedding model '{effective_embedding_model}': {exc}") from exc

    client = MongoClient(mongo_uri)
    global_db = client[mongo_db_name]
    job_descriptions_col = global_db["job-descriptions"]
    companies_col = global_db["companies"]

    redis_client = redis.Redis(host=redis_host, port=redis_port)

    # user_managers maps user_id → ScoringRunManager (created lazily per user).
    user_managers: dict[str, ScoringRunManager] = {}
    user_managers_lock = threading.Lock()

    work_queue: queue.Queue[dict | None] = queue.Queue(maxsize=max(1, worker_pool_size * 4))
    worker_threads = []
    for worker_id in range(worker_pool_size):
        worker_thread = threading.Thread(
            target=scoring_worker_loop,
            args=(
                worker_id,
                work_queue,
                global_db,
                client,
                redis_client,
                scoring_progress_channel,
                user_managers,
                user_managers_lock,
                ollama_host,
                ollama_model,
                test_mode,
            ),
            daemon=True,
        )
        worker_thread.start()
        worker_threads.append(worker_thread)

    print(f"info: Listening for messages on Redis queue '{queue_name}'...")
    print(f"info: Publishing progress on Redis channel '{scoring_progress_channel}'...")
    print(f"info: Global Mongo DB '{mongo_db_name}' at '{mongo_uri}'")
    print(f"info: Test mode = {test_mode}")
    print(f"info: Embedding model = {effective_embedding_model}")
    print(f"info: AI_SCORER_OLLAMA_PARALLELISM (worker pool size) = {worker_pool_size}")

    try:
        while True:
            try:
                msg: Any = redis_client.blpop([queue_name], timeout=0)
                if not msg:
                    continue

                data = msg[1]
                try:
                    payload = json.loads(data.decode("utf-8"))
                except Exception as exc:
                    print(f"error: Invalid JSON in queue message: {exc}")
                    continue

                job_id = payload.get("job_id")
                user_id = str(payload.get("user_id") or "").strip()
                identity_id = str(payload.get("identity_id") or "").strip()
                if not job_id:
                    print("error: Missing required field 'job_id'.")
                    continue
                if not user_id:
                    print("error: Missing required field 'user_id'.")
                    continue
                if not identity_id:
                    print("error: Missing required field 'identity_id'.")
                    continue

                work_queue.put({"job_id": str(job_id), "user_id": user_id, "identity_id": identity_id})

            except Exception as exc:
                print(f"error: Error while consuming queue: {exc}")
                time.sleep(5)
    finally:
        for _ in worker_threads:
            work_queue.put(None)
        for worker_thread in worker_threads:
            worker_thread.join(timeout=5)


if __name__ == "__main__":
    main()
