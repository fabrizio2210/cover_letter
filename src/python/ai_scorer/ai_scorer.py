import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import ollama
import redis
from bson.objectid import ObjectId
from google.protobuf.json_format import MessageToDict
from google.protobuf.timestamp_pb2 import Timestamp
from pymongo import MongoClient
import common_pb2


_SCORING_STATUS_BSON: dict[int, str] = {
    common_pb2.SCORING_STATUS_UNSCORED: "unscored",
    common_pb2.SCORING_STATUS_QUEUED: "queued",
    common_pb2.SCORING_STATUS_SCORED: "scored",
    common_pb2.SCORING_STATUS_FAILED: "failed",
    common_pb2.SCORING_STATUS_SKIPPED: "skipped",
}


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
        weighted_score=float(job_doc.get("weighted_score", 0) or 0),
        max_score=int(job_doc.get("max_score", 0) or 0),
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


def build_prompt(job, company, identity, preference):
    job_title = get_field(job, "title", "")
    job_description = get_field(job, "description", "")
    job_location = get_field(job, "location", "")
    job_platform = get_field(job, "platform", "")

    company_name = get_field(company, "name", "")
    company_description = get_field(company, "description", "")

    identity_name = get_field(identity, "name", "")
    identity_description = get_field(identity, "description", "")

    preference_key = get_field(preference, "key", "")
    preference_guidance = get_field(preference, "guidance", "")

    system_instruction = (
        "You are an objective HR analyzer. Evaluate one candidate preference against one job posting. "
        "Return only one integer score from 1 to 5. "
        "Do not return JSON and do not add any explanation text."
    )

    user_prompt = (
        f"Job Title: {job_title}\n"
        f"Job Description: {job_description}\n"
        f"Job Location: {job_location}\n"
        f"Source Platform: {job_platform}\n\n"
        f"Company Name: {company_name}\n"
        f"Company Description: {company_description}\n\n"
        f"Candidate Identity Name: {identity_name}\n"
        f"Candidate Identity Description: {identity_description}\n\n"
        f"Preference Key: {preference_key}\n"
        f"Preference Guidance: {preference_guidance}\n\n"
        "Respond only with one number in range 1..5."
    )

    return system_instruction, user_prompt


def set_job_status(job_descriptions_col, job_object_id, status, extra_fields=None):
    payload = {
        "scoring_status": scoring_status_to_bson(status),
        "updated_at": timestamp_dict_from_proto(now_proto_timestamp()),
    }
    if extra_fields:
        payload.update(extra_fields)
    job_descriptions_col.update_one({"_id": job_object_id}, {"$set": payload})


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

    content = ""
    if isinstance(response, dict):
        content = response.get("message", {}).get("content", "")
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


def persist_preference_score(job_preference_scores_col, job_doc, identity_doc, preference, score):
    job_id_str = get_message_id(job_doc)
    identity_id_str = get_message_id(identity_doc)
    preference_key = get_field(preference, "key", "")

    scored_at = now_proto_timestamp()
    score_proto = common_pb2.JobPreferenceScore(
        job_id=job_id_str,
        identity_id=identity_id_str,
        preference_key=preference_key,
        preference_guidance=str(get_field(preference, "guidance", "") or get_field(preference, "label", "")),
        preference_weight=float(get_field(preference, "weight", 0) or 0),
        score=int(score),
        scored_at=scored_at,
    )

    score_doc = MessageToDict(score_proto, preserving_proto_field_name=True)
    score_doc.pop("id", None)
    score_doc["scored_at"] = timestamp_dict_from_proto(scored_at)

    job_preference_scores_col.update_one(
        {
            "job_id": job_id_str,
            "identity_id": identity_id_str,
            "preference_key": preference_key,
        },
        {"$set": score_doc},
        upsert=True,
    )


def compute_and_persist_aggregate(job_descriptions_col, job_preference_scores_col, job_doc, identity_doc):
    job_id_str = get_message_id(job_doc)
    identity_id_str = get_message_id(identity_doc)

    score_docs = list(
        job_preference_scores_col.find(
            {
                "job_id": job_id_str,
                "identity_id": identity_id_str,
            }
        )
    )

    if not score_docs:
        raise ValueError("No preference scores found to aggregate")

    weighted_sum = 0.0
    max_score = len(score_docs) * 5

    for doc in score_docs:
        score = int(doc.get("score", 0))
        weight = float(doc.get("preference_weight", 0))
        weighted_sum += score * (weight / max_score)

    weighted_score = 0.0
    if max_score > 0:
        weighted_score = weighted_sum

    set_job_status(
        job_descriptions_col,
        parse_object_id(job_id_str),
        common_pb2.SCORING_STATUS_SCORED,
        extra_fields={
            "weighted_score": weighted_score,
            "max_score": max_score,
        },
    )


def process_scoring_job(
    job_id,
    job_descriptions_col,
    companies_col,
    identities_col,
    job_preference_scores_col,
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
    job_object_id = job_doc.get("_id")

    if error in {"company_not_found", "identity_not_found", "no_enabled_preferences"}:
        set_job_status(job_descriptions_col, job_object_id, common_pb2.SCORING_STATUS_SKIPPED)
        print(f"warn: Skipping job '{job_id}' due to missing prerequisites ({error}).")
        return

    job_proto = job_proto_from_doc(job_doc)
    company_proto = company_proto_from_doc(company_doc)
    identity_proto = identity_proto_from_doc(identity_doc)
    enabled_preferences_proto = [pref for pref in identity_proto.preferences if pref.enabled]

    if not enabled_preferences_proto:
        set_job_status(job_descriptions_col, job_object_id, common_pb2.SCORING_STATUS_SKIPPED)
        print(f"warn: Skipping job '{job_id}' due to missing preferences list.")
        return

    set_job_status(job_descriptions_col, job_object_id, common_pb2.SCORING_STATUS_QUEUED)

    try:
        for preference in enabled_preferences_proto:
            score = score_preference(
                ollama_client,
                model_name,
                test_mode,
                str(job_object_id),
                preference,
                job_proto,
                company_proto,
                identity_proto,
            )
            persist_preference_score(
                job_preference_scores_col,
                job_proto,
                identity_proto,
                preference,
                score,
            )

        compute_and_persist_aggregate(
            job_descriptions_col,
            job_preference_scores_col,
            job_proto,
            identity_proto,
        )

        print(f"info: Successfully scored job '{job_id}'.")

    except Exception as exc:
        set_job_status(job_descriptions_col, job_object_id, common_pb2.SCORING_STATUS_FAILED)
        print(f"error: Failed to score job '{job_id}': {exc}")


def main():
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))
    queue_name = os.environ.get("JOB_SCORING_QUEUE_NAME", "job_scoring_queue")

    mongo_uri = os.environ.get("MONGO_HOST", "mongodb://localhost:27017/")
    mongo_db_name = os.environ.get("DB_NAME", "cover_letter")

    test_mode = os.environ.get("AI_SCORER_TEST_MODE", "0") == "1"
    ollama_host = os.environ.get("OLLAMA_HOST")
    ollama_model = os.environ.get("OLLAMA_MODEL")

    if not test_mode:
        if not ollama_host:
            raise RuntimeError("Environment variable OLLAMA_HOST is required when AI_SCORER_TEST_MODE != 1")
        if not ollama_model:
            raise RuntimeError("Environment variable OLLAMA_MODEL is required when AI_SCORER_TEST_MODE != 1")

    if test_mode and not ollama_model:
        ollama_model = "test-mode-model"

    client = MongoClient(mongo_uri)
    db = client[mongo_db_name]
    job_descriptions_col = db["jobs"]
    companies_col = db["companies"]
    identities_col = db["identities"]
    job_preference_scores_col = db["job-preference-scores"]

    redis_client = redis.Redis(host=redis_host, port=redis_port)

    ollama_client = ollama.Client(host=ollama_host) if ollama_host else ollama.Client()

    print(f"info: Listening for messages on Redis queue '{queue_name}'...")
    print(f"info: Mongo DB '{mongo_db_name}' at '{mongo_uri}'")
    print(f"info: Test mode = {test_mode}")

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
            if not job_id:
                print("error: Missing required field 'job_id'.")
                continue

            process_scoring_job(
                str(job_id),
                job_descriptions_col,
                companies_col,
                identities_col,
                job_preference_scores_col,
                ollama_client,
                ollama_model,
                test_mode,
            )

        except Exception as exc:
            print(f"error: Error while processing queue: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
