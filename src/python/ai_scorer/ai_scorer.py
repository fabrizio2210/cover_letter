import json
import html
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

try:
    from . import common_pb2
except ImportError:
    import common_pb2


_SCORING_STATUS_BSON: dict[int, str] = {
    common_pb2.SCORING_STATUS_UNSCORED: "unscored",
    common_pb2.SCORING_STATUS_QUEUED: "queued",
    common_pb2.SCORING_STATUS_SCORED: "scored",
    common_pb2.SCORING_STATUS_FAILED: "failed",
    common_pb2.SCORING_STATUS_SKIPPED: "skipped",
}


TERMINAL_PROGRESS_STATUSES = {"completed", "failed"}


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
    # Deterministic integer score in [1..5] for test mode.
    seed_text = f"{job_id}:{preference_key}"
    seed = sum(ord(ch) for ch in seed_text)
    return (seed % 5) + 1


def safe_json_dump(value):
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return repr(value)


def parse_ollama_response(content):
    if not content:
        return None, "empty_content"

    if isinstance(content, str):
        stripped = content.strip()
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict) and "score" in payload:
                score_value = payload.get("score")
                if score_value is not None:
                    return int(score_value), "json_dict_score"
            if isinstance(payload, int):
                return payload, "json_integer"
        except Exception:
            pass

        if stripped.isdigit():
            return int(stripped), "plain_integer"

        digits = [ch for ch in stripped if ch.isdigit()]
        if digits:
            return int(digits[0]), "first_digit_fallback"

        return None, "string_without_parseable_score"

    if isinstance(content, dict):
        try:
            score_value = content.get("score")
            if score_value is None:
                return None, "dict_without_score"
            return int(score_value), "dict_score"
        except Exception:
            return None, "dict_score_cast_failed"

    return None, f"unsupported_content_type:{type(content).__name__}"


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


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def normalize_description_markdown(description: Any) -> str:
    if description is None:
        return ""

    text = str(description)
    if not text:
        return ""

    try:
        text = html.unescape(text)

        replacements = [
            (r"(?is)<\s*br\s*/?\s*>", "\n"),
            (r"(?is)</\s*p\s*>", "\n\n"),
            (r"(?is)<\s*p\b[^>]*>", ""),
            (r"(?is)</\s*div\s*>", "\n"),
            (r"(?is)<\s*div\b[^>]*>", ""),
            (r"(?is)</\s*h([1-6])\s*>", "\n\n"),
            (r"(?is)<\s*h1\b[^>]*>", "# "),
            (r"(?is)<\s*h2\b[^>]*>", "## "),
            (r"(?is)<\s*h3\b[^>]*>", "### "),
            (r"(?is)<\s*h4\b[^>]*>", "#### "),
            (r"(?is)<\s*h5\b[^>]*>", "##### "),
            (r"(?is)<\s*h6\b[^>]*>", "###### "),
            (r"(?is)<\s*li\b[^>]*>", "\n- "),
            (r"(?is)</\s*li\s*>", ""),
            (r"(?is)<\s*(ul|ol)\b[^>]*>", "\n"),
            (r"(?is)</\s*(ul|ol)\s*>", "\n"),
            (r"(?is)<\s*strong\b[^>]*>", "**"),
            (r"(?is)</\s*strong\s*>", "**"),
            (r"(?is)<\s*b\b[^>]*>", "**"),
            (r"(?is)</\s*b\s*>", "**"),
            (r"(?is)<\s*em\b[^>]*>", "*"),
            (r"(?is)</\s*em\s*>", "*"),
            (r"(?is)<\s*i\b[^>]*>", "*"),
            (r"(?is)</\s*i\s*>", "*"),
            (r"(?is)<\s*/?\s*(script|style)\b[^>]*>", ""),
        ]

        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text)

        text = re.sub(
            r'(?is)<\s*a\b[^>]*href\s*=\s*(["\'])(.*?)\1[^>]*>(.*?)</\s*a\s*>',
            r"[\3](\2)",
            text,
        )

        # Strip any remaining tags; partial leftovers are acceptable for best-effort conversion.
        text = re.sub(r"(?is)<[^>]+>", "", text)
    except Exception:
        # Degrade gracefully and keep a minimally cleaned payload.
        text = str(description)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t\f\v]+", " ", text)
    text = _collapse_blank_lines(text)
    return text.strip()


def build_prompt(job, company, identity, preference):
    job_title = get_field(job, "title", "")
    job_description = normalize_description_markdown(get_field(job, "description", ""))
    job_location = get_field(job, "location", "")

    preference_guidance = get_field(preference, "guidance", "")

    system_instruction = (
        "You are an objective HR analyzer. Evaluate one candidate preference against one job posting using the preference guidance. "
        "Return only one integer score from 1 to 5. "
        "Do not return JSON and do not add any explanation text."
    )

    user_prompt = (
        f"Preference Guidance: {preference_guidance}\n\n"
        "Respond only with one number in range 1..5.\n\n"
        f"Job Title: {job_title}\n"
        f"Job Description: {job_description}\n"
        f"Job Location: {job_location}\n"
        "\n"
    )

    return system_instruction, user_prompt


def upsert_identity_score_doc(
    job_preference_scores_col,
    job_id_str,
    identity_id_str,
    *,
    status,
    preference_scores=None,
    weighted_score=0.0,
    max_score=0,
):
    score_proto = common_pb2.JobPreferenceScore(
        job_id=job_id_str,
        identity_id=identity_id_str,
        scoring_status=status,
        weighted_score=float(weighted_score),
        max_score=int(max_score),
    )

    for pref_score in preference_scores or []:
        score_proto.preference_scores.append(
            common_pb2.PreferenceScore(
                preference_key=str(pref_score.get("preference_key", "")),
                preference_guidance=str(pref_score.get("preference_guidance", "")),
                preference_weight=float(pref_score.get("preference_weight", 0) or 0),
                score=int(pref_score.get("score", 0) or 0),
            )
        )

    score_doc = MessageToDict(score_proto, preserving_proto_field_name=True)
    score_doc.pop("id", None)
    score_doc["job_id"] = job_id_str
    score_doc["identity_id"] = identity_id_str
    score_doc["scoring_status"] = scoring_status_to_bson(status)
    score_doc["weighted_score"] = float(weighted_score)
    score_doc["preference_scores"] = preference_scores or []
    if max_score > 0:
        score_doc["max_score"] = int(max_score)
    else:
        score_doc.pop("max_score", None)

    job_preference_scores_col.update_one(
        {"job_id": job_id_str, "identity_id": identity_id_str},
        {"$set": score_doc},
        upsert=True,
    )


def resolve_scoring_context(job_descriptions_col, companies_col, identities_col, job_id):
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

    field_ref = company_doc.get("field")
    if field_ref is None:
        field_ref = company_doc.get("field_id")
    field_object_id = parse_object_id(field_ref)

    identity_doc = None
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

    if test_mode:
        return stable_test_score(job_id, preference_key)

    system_instruction, user_prompt = build_prompt(job_doc, company_doc, identity_doc, preference)

    request_payload = {
        "job_id": job_id,
        "preference_key": preference_key,
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": 0},
    }
    print(f"debug: Ollama request: {safe_json_dump(request_payload)}")

    response = ollama_client.chat(
        model=model_name,
        messages=request_payload["messages"],
        options={"temperature": 0},
    )
    print(
        "debug: Ollama response: "
        + safe_json_dump(
            {
                "job_id": job_id,
                "preference_key": preference_key,
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
                "content": content,
            }
        )
    )

    score, parse_strategy = parse_ollama_response(content)

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

    if score < 1 or score > 5:
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
                "score": score,
                "parse_strategy": parse_strategy,
            }
        )
    )

    return score


def build_preference_score_doc(preference, score):
    preference_key = get_field(preference, "key", "")
    scored_at = now_proto_timestamp()

    preference_score = common_pb2.PreferenceScore(
        preference_key=preference_key,
        preference_guidance=str(get_field(preference, "guidance", "") or get_field(preference, "label", "")),
        preference_weight=float(get_field(preference, "weight", 0) or 0),
        score=int(score),
        scored_at=scored_at,
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
    max_score = len(preference_scores) * 5

    for doc in preference_scores:
        score = int(doc.get("score", 0))
        weight = float(doc.get("preference_weight", 0))
        weighted_sum += score * (weight / max_score)

    weighted_score = 0.0
    if max_score > 0:
        weighted_score = weighted_sum

    upsert_identity_score_doc(
        job_preference_scores_col,
        job_id_str,
        identity_id_str,
        status=common_pb2.SCORING_STATUS_SCORED,
        preference_scores=preference_scores,
        weighted_score=weighted_score,
        max_score=max_score,
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
):
    context, error = resolve_scoring_context(job_descriptions_col, companies_col, identities_col, job_id)

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
        )
        print(f"warn: Skipping job '{job_id}' due to missing preferences list.")
        return

    upsert_identity_score_doc(
        job_preference_scores_col,
        get_message_id(job_proto),
        get_message_id(identity_proto),
        status=common_pb2.SCORING_STATUS_QUEUED,
        preference_scores=[],
    )

    try:
        preference_scores = []
        job_id_str = get_message_id(job_proto)
        for preference in enabled_preferences_proto:
            score = score_preference(
                ollama_client,
                model_name,
                test_mode,
                job_id_str,
                preference,
                job_proto,
                company_proto,
                identity_proto,
            )

            preference_scores.append(build_preference_score_doc(preference, score))

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
    worker_pool_size = parse_worker_pool_size(os.environ.get("AI_SCORER_OLLAMA_PARALLELISM", "1"))

    if not test_mode:
        if not ollama_host:
            raise RuntimeError("Environment variable OLLAMA_HOST is required when AI_SCORER_TEST_MODE != 1")
        if not ollama_model:
            raise RuntimeError("Environment variable OLLAMA_MODEL is required when AI_SCORER_TEST_MODE != 1")

    if test_mode and not ollama_model:
        ollama_model = "test-mode-model"

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
                if not job_id:
                    print("error: Missing required field 'job_id'.")
                    continue
                if not user_id:
                    print("error: Missing required field 'user_id'.")
                    continue

                work_queue.put({"job_id": str(job_id), "user_id": user_id})

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
