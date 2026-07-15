"""
Extractor: sample job descriptions from MongoDB and write unlabeled candidate fixtures.

Usage (from repo root):
    python -m src.python.ai_scorer.evals.extractor \\
        --mongo-uri 'mongodb://root:develop@localhost:27017/' \\
        --global-db cover_letter_global \\
        --output src/python/ai_scorer/evals/data/proposed/candidates.json \\
        --preferences src/python/ai_scorer/evals/data/eval_preferences.json \\
        --limit 50
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

# ---------------------------------------------------------------------------
# Import path setup — works both as module (PYTHONPATH=.) and direct script
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.python.ai_scorer.description_normalization import normalize_description_markdown
from src.python.ai_scorer.evals.redaction import redact_case_fields
from src.python.ai_scorer.evals.schema import (
    EXTRACTOR_VERSION,
    EvalCase,
    Provenance,
    dump_fixtures,
    new_case_id,
)
from src.python.ai_scorer.job_fingerprint import description_fingerprint


# ---------------------------------------------------------------------------
# Tag helpers
# ---------------------------------------------------------------------------

def _description_tags(description: str) -> list:
    tags = []
    length = len(description.strip())
    if length == 0:
        tags.append("empty_description")
    elif length < 300:
        tags.append("short_description")
    elif length > 3000:
        tags.append("rich_description")
    else:
        tags.append("medium_description")
    return tags


def _location_tags(location: str) -> list:
    loc_lower = location.lower()
    if "remote" in loc_lower:
        return ["remote_location"]
    if "hybrid" in loc_lower:
        return ["hybrid_location"]
    if loc_lower.strip():
        return ["onsite_location"]
    return []


def _build_tags(title: str, description: str, location: str) -> list:
    return _description_tags(description) + _location_tags(location)


# ---------------------------------------------------------------------------
# Diversity sampling
# ---------------------------------------------------------------------------

def _sample_diverse(docs: list, limit: int) -> list:
    """Return up to `limit` docs with diversity across description length categories."""
    empty = [d for d in docs if not d.get("description", "").strip()]
    short = [d for d in docs if 0 < len(d.get("description", "").strip()) < 300]
    medium = [d for d in docs if 300 <= len(d.get("description", "").strip()) <= 3000]
    rich = [d for d in docs if len(d.get("description", "").strip()) > 3000]

    # Rough allocation: 5% empty edge cases, 15% short, 40% medium, 40% rich
    # (capped at available sizes)
    alloc_empty = max(1, int(limit * 0.05))
    alloc_short = max(1, int(limit * 0.15))
    alloc_rich = max(1, int(limit * 0.40))
    alloc_medium = limit - alloc_empty - alloc_short - alloc_rich

    def pick(pool: list, n: int) -> list:
        return pool[:n]

    selected = (
        pick(empty, alloc_empty)
        + pick(short, alloc_short)
        + pick(medium, alloc_medium)
        + pick(rich, alloc_rich)
    )
    # Fill remaining slots if some buckets had fewer than allocated
    remaining_limit = limit - len(selected)
    if remaining_limit > 0:
        used_fingerprints = {str(d["job_fingerprint"]) for d in selected}
        extras = [d for d in docs if str(d["job_fingerprint"]) not in used_fingerprints]
        selected.extend(extras[:remaining_limit])

    return selected[:limit]


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract_candidates(
    mongo_uri: str,
    global_db_name: str,
    preferences: list,
    limit: int,
) -> list:
    """Connect to Mongo, sample job descriptions, and return unlabeled EvalCase list."""
    from pymongo import MongoClient

    client = MongoClient(mongo_uri)
    global_db = client[global_db_name]

    # Fetch all docs; projection keeps only the fields we need
    cursor = global_db["job-descriptions"].find(
        {},
        {"_id": 0, "title": 1, "description": 1, "location": 1},
    )
    all_docs = []
    seen_fingerprints: set[str] = set()
    for doc in cursor:
        raw_title = doc.get("title", "") or ""
        raw_description = doc.get("description", "") or ""
        raw_location = doc.get("location", "") or ""
        normalized_description = normalize_description_markdown(raw_description)
        title, description, location = redact_case_fields(
            raw_title, normalized_description, raw_location
        )
        fingerprint, basis = description_fingerprint(
            description,
            title=title,
            location=location,
        )
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        all_docs.append(
            {
                "title": title,
                "description": description,
                "location": location,
                "job_fingerprint": fingerprint,
                "fingerprint_basis": basis,
            }
        )
    print(f"[extractor] Fetched {len(all_docs)} job descriptions from {global_db_name}")

    sampled = _sample_diverse(all_docs, limit)
    print(f"[extractor] Selected {len(sampled)} diverse samples (limit={limit})")

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cases: list = []

    for doc in sampled:
        title = doc["title"]
        description = doc["description"]
        location = doc["location"]

        provenance = Provenance(
            source_db=global_db_name,
            extracted_at=now_iso,
            extractor_version=EXTRACTOR_VERSION,
        )

        tags = _build_tags(title, description, location)

        for pref in preferences:
            cases.append(
                EvalCase(
                    case_id=new_case_id(),
                    job_fingerprint=doc["job_fingerprint"],
                    fingerprint_basis=doc["fingerprint_basis"],
                    title=title,
                    description=description,
                    location=location,
                    preference_key=pref["key"],
                    preference_guidance=pref["guidance"],
                    expected_score=None,
                    expected_score_available=None,
                    rationale="",
                    tags=tags,
                    provenance=provenance,
                )
            )

    print(f"[extractor] Created {len(cases)} candidate cases "
          f"({len(sampled)} jobs × {len(preferences)} preferences)")
    return cases


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_preferences(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        prefs = json.load(f)
    for p in prefs:
        if "key" not in p or "guidance" not in p:
            raise ValueError(f"Each preference must have 'key' and 'guidance': {p!r}")
    return prefs


def _default_preferences_path() -> str:
    return os.path.join(os.path.dirname(__file__), "data", "eval_preferences.json")


def main(argv: list = None) -> None:
    parser = argparse.ArgumentParser(description="Extract candidate eval fixtures from MongoDB")
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("MONGO_HOST", "mongodb://localhost:27017/"),
        help="MongoDB connection URI (default: MONGO_HOST env or localhost:27017)",
    )
    parser.add_argument(
        "--global-db",
        default=os.environ.get("DB_NAME", "cover_letter_global"),
        help="Global DB name (default: DB_NAME env or cover_letter_global)",
    )
    parser.add_argument(
        "--output",
        default="src/python/ai_scorer/evals/data/proposed/candidates.json",
        help="Output path for candidate fixture file",
    )
    parser.add_argument(
        "--preferences",
        default=_default_preferences_path(),
        help="Path to JSON file with eval preferences",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of job descriptions to sample (default: 50)",
    )
    args = parser.parse_args(argv)

    preferences = _load_preferences(args.preferences)
    candidates = extract_candidates(
        mongo_uri=args.mongo_uri,
        global_db_name=args.global_db,
        preferences=preferences,
        limit=args.limit,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    dump_fixtures(candidates, args.output)
    print(f"[extractor] Done → {args.output}")


if __name__ == "__main__":
    main()
