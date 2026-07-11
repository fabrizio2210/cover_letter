from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.python.ai_scorer.training.preferences import default_preferences_path, ensure_seed_preferences, load_preferences
from src.python.ai_scorer.training.schema import TrainingCase, dump_cases, new_case_id

try:
    from src.python.ai_scorer.ai_scorer import (
        build_prompt,
        normalize_description_markdown,
        retrieve_relevant_snippets,
    )
except ImportError:
    from ai_scorer import build_prompt, normalize_description_markdown, retrieve_relevant_snippets  # type: ignore[no-redef]


def _sample_diverse(docs: list[dict], limit: int) -> list[dict]:
    empty = [d for d in docs if not str(d.get("description", "") or "").strip()]
    short = [d for d in docs if 0 < len(str(d.get("description", "") or "").strip()) < 300]
    medium = [d for d in docs if 300 <= len(str(d.get("description", "") or "").strip()) <= 3000]
    rich = [d for d in docs if len(str(d.get("description", "") or "").strip()) > 3000]

    alloc_empty = max(1, int(limit * 0.05))
    alloc_short = max(1, int(limit * 0.15))
    alloc_rich = max(1, int(limit * 0.40))
    alloc_medium = max(1, limit - alloc_empty - alloc_short - alloc_rich)

    selected = empty[:alloc_empty] + short[:alloc_short] + medium[:alloc_medium] + rich[:alloc_rich]
    selected_ids = {str(doc.get("_id")) for doc in selected}
    if len(selected) < limit:
        extras = [doc for doc in docs if str(doc.get("_id")) not in selected_ids]
        selected.extend(extras[: limit - len(selected)])

    return selected[:limit]


def extract_training_cases(
    mongo_uri: str,
    global_db_name: str,
    preferences: list[dict],
    limit: int,
) -> list[TrainingCase]:
    from pymongo import MongoClient

    client = MongoClient(mongo_uri)
    db = client[global_db_name]

    cursor = db["job-descriptions"].find(
        {},
        {"_id": 1, "title": 1, "description": 1, "location": 1},
    )
    all_docs = list(cursor)
    print(f"[training.extract] fetched={len(all_docs)} from db={global_db_name}")

    sampled = _sample_diverse(all_docs, limit)
    print(f"[training.extract] sampled={len(sampled)} limit={limit}")

    cases: list[TrainingCase] = []
    for doc in sampled:
        source_job_id = str(doc.get("_id"))
        title = str(doc.get("title", "") or "")
        location = str(doc.get("location", "") or "")
        description = normalize_description_markdown(doc.get("description", "") or "")

        for pref in preferences:
            guidance = str(pref.get("guidance", "") or "")
            snippets = retrieve_relevant_snippets(description, guidance)
            system_prompt, user_prompt = build_prompt(
                {"title": title, "location": location, "description": description},
                {},
                {},
                pref,
                snippets=snippets,
            )
            cases.append(
                TrainingCase(
                    case_id=new_case_id(),
                    source_job_id=source_job_id,
                    title=title,
                    location=location,
                    preference_key=str(pref.get("key", "") or ""),
                    preference_guidance=guidance,
                    relevant_snippets=snippets,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            )
            print(
                f"[training.extract] {len(cases)}/{len(sampled)*len(preferences)} case={cases[-1].case_id} job={source_job_id} preference={pref.get('key')}"
            )   

    print(
        "[training.extract] created="
        f"{len(cases)} ({len(sampled)} jobs x {len(preferences)} preferences)"
    )
    return cases


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Extract training cases from MongoDB")
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_HOST", "mongodb://localhost:27017/"))
    parser.add_argument("--global-db", default=os.environ.get("DB_NAME", "cover_letter_global"))
    parser.add_argument("--output", default="src/python/ai_scorer/training/data/proposed/candidates.json")
    parser.add_argument("--preferences", default=default_preferences_path())
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--seed-count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1337)
    args = parser.parse_args(argv)

    if args.preferences == default_preferences_path():
        preferences = ensure_seed_preferences(args.preferences, count=args.seed_count, seed=args.seed)
    else:
        preferences = load_preferences(args.preferences)

    cases = extract_training_cases(
        mongo_uri=args.mongo_uri,
        global_db_name=args.global_db,
        preferences=preferences,
        limit=args.limit,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    dump_cases(cases, args.output)
    print(f"[training.extract] done -> {args.output}")


if __name__ == "__main__":
    main()
