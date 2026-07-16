from __future__ import annotations

import argparse
import json
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
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    # MongoDB natural order is not portable across servers. Fingerprint order
    # makes the selected pool reproducible for the same set of descriptions.
    docs = sorted(docs, key=lambda doc: str(doc.get("job_fingerprint", "")))
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


def prepare_job_pool(
    all_docs: list[dict],
    *,
    limit: int,
    promotion_fingerprints: set[str],
) -> tuple[list[dict], dict]:
    normalized_docs: list[dict] = []
    excluded_fingerprints: set[str] = set()
    excluded_document_count = 0

    for doc in all_docs:
        description = normalize_description_markdown(doc.get("description", "") or "")
        fingerprint, basis = description_fingerprint(
            description,
            title=doc.get("title", ""),
            location=doc.get("location", ""),
        )
        if fingerprint in promotion_fingerprints:
            excluded_fingerprints.add(fingerprint)
            excluded_document_count += 1
            continue
        normalized_docs.append(
            {
                "job_fingerprint": fingerprint,
                "fingerprint_basis": basis,
                "title": str(doc.get("title", "") or ""),
                "location": str(doc.get("location", "") or ""),
                "description": description,
            }
        )

    # A fingerprint can occur in several MongoDB documents. Pick the same
    # representative regardless of MongoDB insertion order.
    normalized_docs.sort(
        key=lambda doc: (
            str(doc["job_fingerprint"]),
            str(doc["title"]).casefold(),
            str(doc["location"]).casefold(),
            str(doc["description"]),
        )
    )
    jobs_by_fingerprint: dict[str, dict] = {}
    for doc in normalized_docs:
        jobs_by_fingerprint.setdefault(str(doc["job_fingerprint"]), doc)

    sampled = _sample_diverse(list(jobs_by_fingerprint.values()), limit)
    stats = {
        "fetched_document_count": len(all_docs),
        "promotion_excluded_document_count": excluded_document_count,
        "promotion_excluded_fingerprint_count": len(excluded_fingerprints),
        "eligible_document_count": len(normalized_docs),
        "unique_eligible_fingerprint_count": len(jobs_by_fingerprint),
        "duplicate_eligible_document_count": len(normalized_docs) - len(jobs_by_fingerprint),
        "sampled_job_count": len(sampled),
        "sampled_description_buckets": {
            "empty": sum(not str(job["description"]).strip() for job in sampled),
            "short": sum(0 < len(str(job["description"]).strip()) < 300 for job in sampled),
            "medium": sum(300 <= len(str(job["description"]).strip()) <= 3000 for job in sampled),
            "rich": sum(len(str(job["description"]).strip()) > 3000 for job in sampled),
        },
    }
    return sampled, stats


def prepare_training_jobs(
    all_docs: list[dict],
    *,
    limit: int,
    promotion_fingerprints: set[str],
    split_seed: int,
    val_ratio: float,
) -> tuple[list[dict], list[str], list[str]]:
    sampled, _ = prepare_job_pool(
        all_docs,
        limit=limit,
        promotion_fingerprints=promotion_fingerprints,
    )
    train_fingerprints, val_fingerprints = partition_fingerprints(
        [doc["job_fingerprint"] for doc in sampled],
        seed=split_seed,
        val_ratio=val_ratio,
    )
    return sampled, train_fingerprints, val_fingerprints


def fetch_training_documents(mongo_uri: str, global_db_name: str) -> list[dict]:
    from pymongo import MongoClient

    client = MongoClient(mongo_uri)
    try:
        db = client[global_db_name]
        cursor = db["job-descriptions"].find(
            {},
            {"_id": 0, "title": 1, "description": 1, "location": 1},
        )
        all_docs = list(cursor)
    finally:
        client.close()

    print(f"[training.extract] fetched={len(all_docs)} from db={global_db_name}")
    return all_docs


def write_job_pool(
    jobs: list[dict],
    path: str,
    *,
    global_db_name: str,
    limit: int,
    promotion_fixture_path: str,
    promotion_case_count: int,
    promotion_fingerprint_count: int,
    stats: dict,
) -> None:
    payload = {
        "schema_version": "1",
        "source": {
            "database": global_db_name,
            "collection": "job-descriptions",
        },
        "selection": {
            "strategy": "description-length-stratified-deterministic-v1",
            "requested_limit": limit,
            "promotion_fixture_path": promotion_fixture_path,
            "promotion_case_count": promotion_case_count,
            "promotion_fingerprint_count": promotion_fingerprint_count,
        },
        "stats": stats,
        "jobs": jobs,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def extract_training_cases(
    mongo_uri: str,
    global_db_name: str,
    preferences: list[dict],
    limit: int,
    promotion_fingerprints: set[str],
    split_seed: int,
    val_ratio: float,
) -> tuple[list[TrainingCase], list[str], list[str]]:
    from src.python.ai_scorer.ai_scorer import build_prompt, retrieve_relevant_snippets

    all_docs = fetch_training_documents(mongo_uri, global_db_name)

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
    parser.add_argument(
        "--jobs-only",
        action="store_true",
        help="Write a reusable job-description pool without expanding preferences or changing the split manifest",
    )
    parser.add_argument(
        "--job-pool-output",
        default="src/python/ai_scorer/training/data/proposed/job-pool.json",
    )
    args = parser.parse_args(argv)

    golden_fingerprints, golden_case_count = load_golden_fingerprints(args.promotion_fixtures)
    if args.jobs_only:
        all_docs = fetch_training_documents(args.mongo_uri, args.global_db)
        jobs, stats = prepare_job_pool(
            all_docs,
            limit=args.limit,
            promotion_fingerprints=set(golden_fingerprints),
        )
        write_job_pool(
            jobs,
            args.job_pool_output,
            global_db_name=args.global_db,
            limit=args.limit,
            promotion_fixture_path=args.promotion_fixtures,
            promotion_case_count=golden_case_count,
            promotion_fingerprint_count=len(golden_fingerprints),
            stats=stats,
        )
        print(
            "[training.extract] jobs-only "
            f"unique-eligible={stats['unique_eligible_fingerprint_count']} "
            f"sampled={stats['sampled_job_count']} "
            f"golden-excluded={stats['promotion_excluded_fingerprint_count']}"
        )
        print(f"[training.extract] job pool -> {args.job_pool_output}")
        return

    if args.preferences == default_preferences_path():
        preferences = ensure_seed_preferences(args.preferences, count=args.seed_count, seed=args.seed)
    else:
        preferences = load_preferences(args.preferences)

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
