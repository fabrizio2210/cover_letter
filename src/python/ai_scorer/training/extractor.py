from __future__ import annotations

import argparse
import os

from src.python.ai_scorer.description_normalization import normalize_description_markdown
from src.python.ai_scorer.job_fingerprint import (
    description_fingerprint,
    partition_fingerprints,
    stable_json_hash,
)
from src.python.ai_scorer.training.dataset_split import (
    DEFAULT_PROMOTION_FIXTURES,
    DEFAULT_SPLIT_MANIFEST,
    build_split_manifest,
    load_golden_fingerprints,
    write_split_manifest,
)
from src.python.ai_scorer.training.preferences import (
    default_preferences_path,
    ensure_seed_preferences,
    load_preferences,
)
from src.python.ai_scorer.training.schema import TrainingCase, dump_cases, new_case_id


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
    selected_fingerprints = {str(doc.get("job_fingerprint")) for doc in selected}
    if len(selected) < limit:
        extras = [doc for doc in docs if str(doc.get("job_fingerprint")) not in selected_fingerprints]
        selected.extend(extras[: limit - len(selected)])

    return selected[:limit]


def prepare_training_jobs(
    all_docs: list[dict],
    *,
    limit: int,
    promotion_fingerprints: set[str],
    split_seed: int,
    val_ratio: float,
) -> tuple[list[dict], list[str], list[str]]:
    jobs_by_fingerprint: dict[str, dict] = {}
    for doc in all_docs:
        description = normalize_description_markdown(doc.get("description", "") or "")
        fingerprint, basis = description_fingerprint(
            description,
            title=doc.get("title", ""),
            location=doc.get("location", ""),
        )
        if fingerprint in promotion_fingerprints:
            continue
        jobs_by_fingerprint.setdefault(
            fingerprint,
            {
                **doc,
                "description": description,
                "job_fingerprint": fingerprint,
                "fingerprint_basis": basis,
            },
        )

    sampled = _sample_diverse(list(jobs_by_fingerprint.values()), limit)
    train_fingerprints, val_fingerprints = partition_fingerprints(
        [doc["job_fingerprint"] for doc in sampled],
        seed=split_seed,
        val_ratio=val_ratio,
    )
    return sampled, train_fingerprints, val_fingerprints


def extract_training_cases(
    mongo_uri: str,
    global_db_name: str,
    preferences: list[dict],
    limit: int,
    promotion_fingerprints: set[str],
    split_seed: int,
    val_ratio: float,
) -> tuple[list[TrainingCase], list[str], list[str]]:
    from pymongo import MongoClient
    from src.python.ai_scorer.ai_scorer import build_prompt, retrieve_relevant_snippets

    client = MongoClient(mongo_uri)
    db = client[global_db_name]

    cursor = db["job-descriptions"].find(
        {},
        {"_id": 0, "title": 1, "description": 1, "location": 1},
    )
    all_docs = list(cursor)
    print(f"[training.extract] fetched={len(all_docs)} from db={global_db_name}")

    sampled, train_fingerprints, val_fingerprints = prepare_training_jobs(
        all_docs,
        limit=limit,
        promotion_fingerprints=promotion_fingerprints,
        split_seed=split_seed,
        val_ratio=val_ratio,
    )
    print(f"[training.extract] sampled={len(sampled)} limit={limit}")

    cases: list[TrainingCase] = []
    for doc in sampled:
        job_fingerprint = str(doc["job_fingerprint"])
        title = str(doc.get("title", "") or "")
        location = str(doc.get("location", "") or "")
        description = str(doc.get("description", "") or "")

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
                    job_fingerprint=job_fingerprint,
                    fingerprint_basis=str(doc["fingerprint_basis"]),
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
                f"[training.extract] {len(cases)}/{len(sampled)*len(preferences)} case={cases[-1].case_id} job={job_fingerprint} preference={pref.get('key')}"
            )

    print(
        "[training.extract] created="
        f"{len(cases)} ({len(sampled)} jobs x {len(preferences)} preferences)"
    )
    return cases, train_fingerprints, val_fingerprints


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Extract training cases from MongoDB")
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_HOST", "mongodb://localhost:27017/"))
    parser.add_argument("--global-db", default=os.environ.get("DB_NAME", "cover_letter_global"))
    parser.add_argument("--output", default="src/python/ai_scorer/training/data/proposed/candidates.json")
    parser.add_argument("--preferences", default=default_preferences_path())
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--seed-count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--split-seed", type=int, default=42)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--promotion-fixtures", default=DEFAULT_PROMOTION_FIXTURES)
    parser.add_argument("--split-manifest", default=DEFAULT_SPLIT_MANIFEST)
    args = parser.parse_args(argv)

    if args.preferences == default_preferences_path():
        preferences = ensure_seed_preferences(args.preferences, count=args.seed_count, seed=args.seed)
    else:
        preferences = load_preferences(args.preferences)

    golden_fingerprints, golden_case_count = load_golden_fingerprints(args.promotion_fixtures)
    cases, train_fingerprints, val_fingerprints = extract_training_cases(
        mongo_uri=args.mongo_uri,
        global_db_name=args.global_db,
        preferences=preferences,
        limit=args.limit,
        promotion_fingerprints=set(golden_fingerprints),
        split_seed=args.split_seed,
        val_ratio=args.val_ratio,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    dump_cases(cases, args.output)
    manifest = build_split_manifest(
        train_fingerprints=train_fingerprints,
        val_fingerprints=val_fingerprints,
        golden_fingerprints=golden_fingerprints,
        seed=args.split_seed,
        val_ratio=args.val_ratio,
        golden_fixture_path=args.promotion_fixtures,
        golden_case_count=golden_case_count,
        preference_set_hash=stable_json_hash(preferences),
    )
    write_split_manifest(manifest, args.split_manifest)
    print(f"[training.extract] done -> {args.output}")
    print(f"[training.extract] split manifest -> {args.split_manifest}")


if __name__ == "__main__":
    main()
