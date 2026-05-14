from __future__ import annotations

import argparse
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pymongo import MongoClient


EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b")
URL_RE = re.compile(r"https?://\S+")


def redact_text(raw: str) -> tuple[str, dict[str, int]]:
    text = raw
    stats = {"emails": 0, "phones": 0, "urls": 0}

    emails = EMAIL_RE.findall(text)
    if emails:
        stats["emails"] = len(emails)
        text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)

    phones = PHONE_RE.findall(text)
    if phones:
        stats["phones"] = len(phones)
        text = PHONE_RE.sub("[REDACTED_PHONE]", text)

    urls = URL_RE.findall(text)
    if urls:
        stats["urls"] = len(urls)
        text = URL_RE.sub("[REDACTED_URL]", text)

    return text, stats


def _heuristic_label(description: str, preference_key: str) -> tuple[bool, int | None, str]:
    content = description.lower()
    if not content.strip():
        return False, None, "No job description text available"

    if preference_key == "remote":
        if "fully remote" in content or "remote" in content:
            return True, 5, "Remote evidence detected"
        if "hybrid" in content:
            return True, 3, "Hybrid evidence detected"
        if "on-site" in content or "onsite" in content:
            return True, 1, "Onsite evidence detected"
        return False, None, "No clear remote evidence"

    if preference_key in content:
        return True, 4, f"Keyword '{preference_key}' detected in description"

    return False, None, "Insufficient evidence for preference"


def build_case(job_doc: dict[str, Any], preference: dict[str, str], now_iso: str) -> dict[str, Any]:
    title = str(job_doc.get("title", ""))
    description = str(job_doc.get("description", ""))
    location = str(job_doc.get("location", ""))
    redacted_description, redact_stats = redact_text(description)

    score_available, score, rationale = _heuristic_label(redacted_description, preference["key"])
    case_id = f"job_{job_doc.get('_id')}_{preference['key']}"

    tags: list[str] = []
    if not redacted_description.strip():
        tags.append("empty_description")
    elif len(redacted_description) < 240:
        tags.append("short_description")
    else:
        tags.append("long_description")

    return {
        "case_id": case_id,
        "source": {
            "job_id": str(job_doc.get("_id", "")),
            "db": "cover_letter_global",
            "extracted_at": now_iso,
        },
        "job": {
            "title": title,
            "description": redacted_description,
            "location": location,
        },
        "preference": {
            "key": preference["key"],
            "guidance": preference["guidance"],
        },
        "expected": {
            "score_available": score_available,
            "score": score,
        },
        "label_status": "proposed",
        "label_rationale": rationale,
        "tags": tags,
        "redaction": redact_stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract ai_scorer golden fixture candidates from MongoDB")
    parser.add_argument("--mongo-uri", required=True, help="Mongo connection URI")
    parser.add_argument("--db-name", default="cover_letter_global", help="Global DB name")
    parser.add_argument("--limit", type=int, default=50, help="Number of jobs to sample")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--preferences-json",
        required=True,
        help="Path to JSON list of preference objects: [{\"key\":..., \"guidance\":...}]",
    )
    parser.add_argument("--out", required=True, help="Output JSON file for proposed fixture cases")

    args = parser.parse_args()

    preferences = json.loads(Path(args.preferences_json).read_text(encoding="utf-8"))
    if not isinstance(preferences, list) or not preferences:
        raise ValueError("preferences-json must contain a non-empty array")

    random.seed(args.seed)
    now_iso = datetime.now(timezone.utc).isoformat()

    client = MongoClient(args.mongo_uri)
    db = client[args.db_name]

    docs = list(
        db["job-descriptions"].find({}, {"_id": 1, "title": 1, "description": 1, "location": 1})
    )
    if not docs:
        raise RuntimeError("No documents found in job-descriptions")

    sample_size = min(args.limit, len(docs))
    sampled_docs = random.sample(docs, sample_size)

    cases: list[dict[str, Any]] = []
    for job_doc in sampled_docs:
        for pref in preferences:
            key = str(pref.get("key", "")).strip()
            guidance = str(pref.get("guidance", "")).strip()
            if not key or not guidance:
                continue
            cases.append(build_case(job_doc, {"key": key, "guidance": guidance}, now_iso))

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(cases, indent=2, ensure_ascii=True), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "ok",
                "cases": len(cases),
                "jobs_sampled": sample_size,
                "output": str(output_path),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
