from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import defaultdict

from src.python.ai_scorer.training.dataset_split import (
    DEFAULT_PROMOTION_FIXTURES,
    DEFAULT_SPLIT_MANIFEST,
    build_split_manifest,
)
from src.python.ai_scorer.job_fingerprint import (
    canonicalize_description,
    canonicalize_location,
    canonicalize_title,
    description_fingerprint,
    legacy_partial_fingerprint,
    partition_fingerprints,
    stable_json_hash,
)

DEFAULT_CANDIDATES = "src/python/ai_scorer/training/data/proposed/candidates.json"
DEFAULT_LABELED = "src/python/ai_scorer/training/data/proposed/labeled.json"
DEFAULT_PREFERENCES = "src/python/ai_scorer/training/data/training_preferences.seed.json"


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: str, value) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _legacy_group_key(item: dict) -> str:
    # This is intentionally confined to the one-time migration. The returned
    # value is never serialized into migrated artifacts or reports.
    value = item.get("source_job_id")
    if not isinstance(value, str) or not value:
        raise ValueError(f"Legacy case {item.get('case_id')!r} has no grouping key")
    return value


def migrate_golden_raw(raw: dict | list) -> tuple[dict | list, list[dict]]:
    cases = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(cases, list):
        raise ValueError("Golden fixture must contain a cases array")

    jobs: dict[str, dict] = {}
    migrated_cases: list[dict] = []
    for item in cases:
        fingerprint, basis = description_fingerprint(
            item.get("description", ""),
            title=item.get("title", ""),
            location=item.get("location", ""),
        )
        migrated = dict(item)
        migrated["job_fingerprint"] = fingerprint
        migrated["fingerprint_basis"] = basis
        migrated["schema_version"] = "2"
        provenance = dict(migrated.get("provenance") or {})
        provenance.pop("source_job_id", None)
        migrated["provenance"] = provenance or None
        migrated_cases.append(migrated)
        jobs.setdefault(
            fingerprint,
            {
                "job_fingerprint": fingerprint,
                "fingerprint_basis": basis,
                "title": item.get("title", ""),
                "location": item.get("location", ""),
                "canonical_title": canonicalize_title(item.get("title", "")),
                "canonical_location": canonicalize_location(item.get("location", "")),
                "canonical_description": canonicalize_description(item.get("description", "")),
            },
        )

    if isinstance(raw, dict):
        migrated_root = dict(raw)
        migrated_root["cases"] = migrated_cases
    else:
        migrated_root = migrated_cases
    return migrated_root, sorted(jobs.values(), key=lambda item: item["job_fingerprint"])


def build_legacy_groups(raw_cases: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in raw_cases:
        grouped[_legacy_group_key(item)].append(item)

    groups: dict[str, dict] = {}
    legacy_to_fingerprint: dict[str, str] = {}
    for legacy_key, cases in grouped.items():
        first = cases[0]
        snippets = [snippet for case in cases for snippet in case.get("relevant_snippets", [])]
        fingerprint, basis, canonical_snippets = legacy_partial_fingerprint(
            snippets,
            title=first.get("title", ""),
            location=first.get("location", ""),
        )
        group = groups.setdefault(
            fingerprint,
            {
                "job_fingerprint": fingerprint,
                "fingerprint_basis": basis,
                "titles": [],
                "locations": [],
                "canonical_titles": [],
                "canonical_locations": [],
                "canonical_snippets": canonical_snippets,
                "case_count": 0,
                "legacy_group_count": 0,
            },
        )
        title = first.get("title", "")
        location = first.get("location", "")
        if title not in group["titles"]:
            group["titles"].append(title)
        if location not in group["locations"]:
            group["locations"].append(location)
        canonical_title = canonicalize_title(title)
        canonical_location = canonicalize_location(location)
        if canonical_title not in group["canonical_titles"]:
            group["canonical_titles"].append(canonical_title)
        if canonical_location not in group["canonical_locations"]:
            group["canonical_locations"].append(canonical_location)
        group["case_count"] += len(cases)
        group["legacy_group_count"] += 1
        legacy_to_fingerprint[legacy_key] = fingerprint
    return groups, legacy_to_fingerprint


def migrate_training_raw(raw_cases: list[dict], legacy_to_fingerprint: dict[str, str], groups: dict[str, dict]) -> list[dict]:
    migrated: list[dict] = []
    for item in raw_cases:
        fingerprint = legacy_to_fingerprint[_legacy_group_key(item)]
        updated = dict(item)
        updated.pop("source_job_id", None)
        updated["job_fingerprint"] = fingerprint
        updated["fingerprint_basis"] = groups[fingerprint]["fingerprint_basis"]
        updated["schema_version"] = "2"
        for key, value in item.items():
            if key not in {"source_job_id", "schema_version"} and updated.get(key) != value:
                raise ValueError(f"Migration changed paid case field {key!r} for {item.get('case_id')!r}")
        migrated.append(updated)
    return migrated


def reconcile_legacy_groups(groups: dict[str, dict], golden_jobs: list[dict]) -> tuple[list[str], list[str], list[str], list[dict]]:
    eligible: list[str] = []
    confirmed: list[str] = []
    quarantined: list[str] = []
    report: list[dict] = []

    for fingerprint, group in sorted(groups.items()):
        snippets = group["canonical_snippets"]
        candidates: list[dict] = []
        for golden in golden_jobs:
            description = golden["canonical_description"]
            matched = [snippet for snippet in snippets if snippet and snippet in description]
            candidates.append(
                {
                    "golden_fingerprint": golden["job_fingerprint"],
                    "matched_snippet_count": len(matched),
                    "all_snippets_contained": bool(snippets) and len(matched) == len(snippets),
                    "title_equal": golden["canonical_title"] in group["canonical_titles"],
                    "location_equal": golden["canonical_location"] in group["canonical_locations"],
                    "substantive_match": any(len(snippet) >= 80 for snippet in matched),
                }
            )

        if snippets:
            exact = [candidate for candidate in candidates if candidate["all_snippets_contained"]]
            if len(exact) == 1:
                status = "confirmed_promotion_overlap"
                reason = "all normalized snippets occur in exactly one golden description"
                confirmed.append(fingerprint)
                evidence = exact
            elif len(exact) > 1:
                status = "quarantined"
                reason = "all normalized snippets occur in multiple golden descriptions"
                quarantined.append(fingerprint)
                evidence = exact
            else:
                suspicious = [
                    candidate
                    for candidate in candidates
                    if candidate["title_equal"]
                    or (candidate["matched_snippet_count"] > 0 and candidate["substantive_match"])
                ]
                if suspicious:
                    status = "quarantined"
                    reason = "partial snippet or title evidence overlaps the golden set"
                    quarantined.append(fingerprint)
                    evidence = suspicious
                else:
                    status = "eligible"
                    reason = "no meaningful golden overlap found"
                    eligible.append(fingerprint)
                    evidence = []
        else:
            exact = [
                candidate
                for candidate in candidates
                if candidate["title_equal"] and candidate["location_equal"]
            ]
            if len(exact) == 1:
                status = "confirmed_promotion_overlap"
                reason = "empty-snippet fallback title and location match one golden job"
                confirmed.append(fingerprint)
                evidence = exact
            elif exact:
                status = "quarantined"
                reason = "empty-snippet fallback matches multiple golden jobs"
                quarantined.append(fingerprint)
                evidence = exact
            else:
                title_matches = [candidate for candidate in candidates if candidate["title_equal"]]
                if title_matches:
                    status = "quarantined"
                    reason = "empty-snippet fallback has ambiguous golden title evidence"
                    quarantined.append(fingerprint)
                    evidence = title_matches
                else:
                    status = "eligible"
                    reason = "empty-snippet fallback has no golden match"
                    eligible.append(fingerprint)
                    evidence = []

        report.append(
            {
                "job_fingerprint": fingerprint,
                "fingerprint_basis": group["fingerprint_basis"],
                "titles": sorted(group["titles"]),
                "locations": sorted(group["locations"]),
                "case_count": group["case_count"],
                "legacy_group_count": group["legacy_group_count"],
                "snippet_count": len(snippets),
                "status": status,
                "reason": reason,
                "evidence": evidence,
            }
        )

    return sorted(eligible), sorted(confirmed), sorted(quarantined), report


def _write_temp_json(target: str, value) -> str:
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json.tmp",
        prefix="fingerprint-migration-",
        dir=directory,
        delete=False,
    )
    try:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        return handle.name
    finally:
        handle.close()


def migrate(
    *,
    candidates_path: str,
    labeled_path: str,
    golden_path: str,
    preferences_path: str,
    split_manifest_path: str,
    seed: int,
    val_ratio: float,
    apply: bool,
) -> dict:
    candidate_raw = _read_json(candidates_path)
    labeled_raw = _read_json(labeled_path)
    golden_raw = _read_json(golden_path)
    preferences = _read_json(preferences_path)
    if not isinstance(candidate_raw, list) or not isinstance(labeled_raw, list):
        raise ValueError("Legacy candidate and labeled inputs must be arrays")

    golden_migrated, golden_jobs = migrate_golden_raw(golden_raw)
    groups, legacy_to_fingerprint = build_legacy_groups(labeled_raw)
    candidate_migrated = migrate_training_raw(candidate_raw, legacy_to_fingerprint, groups)
    labeled_migrated = migrate_training_raw(labeled_raw, legacy_to_fingerprint, groups)

    candidate_ids = {item.get("case_id") for item in candidate_migrated}
    labeled_ids = {item.get("case_id") for item in labeled_migrated}
    if candidate_ids != labeled_ids:
        raise ValueError("Candidate and labeled case IDs differ; refusing partial migration")
    if len(labeled_migrated) != len(labeled_raw):
        raise ValueError("Labeled case count changed during migration")

    eligible, confirmed, quarantined, reconciliation = reconcile_legacy_groups(groups, golden_jobs)
    train, val = partition_fingerprints(eligible, seed=seed, val_ratio=val_ratio)

    golden_temp = _write_temp_json(golden_path, golden_migrated)
    try:
        manifest = build_split_manifest(
            train_fingerprints=train,
            val_fingerprints=val,
            golden_fingerprints=[item["job_fingerprint"] for item in golden_jobs],
            confirmed_promotion_fingerprints=confirmed,
            quarantined_fingerprints=quarantined,
            seed=seed,
            val_ratio=val_ratio,
            golden_fixture_path=golden_temp,
            golden_case_count=len(golden_migrated["cases"] if isinstance(golden_migrated, dict) else golden_migrated),
            preference_set_hash=stable_json_hash(preferences),
            reconciliation=reconciliation,
        )
        manifest["golden_fixture"] = golden_path

        summary = {
            "labeled_case_count": len(labeled_migrated),
            "legacy_job_fingerprint_count": len(groups),
            "golden_case_count": manifest["golden_case_count"],
            "golden_fingerprint_count": len(manifest["golden_fingerprints"]),
            "eligible_fingerprint_count": len(eligible),
            "confirmed_promotion_fingerprint_count": len(confirmed),
            "quarantined_fingerprint_count": len(quarantined),
            "train_fingerprint_count": len(train),
            "val_fingerprint_count": len(val),
            "train_case_count": sum(groups[item]["case_count"] for item in train),
            "val_case_count": sum(groups[item]["case_count"] for item in val),
            "apply": apply,
        }

        if apply:
            candidate_temp = _write_temp_json(candidates_path, candidate_migrated)
            labeled_temp = _write_temp_json(labeled_path, labeled_migrated)
            manifest_temp = _write_temp_json(split_manifest_path, manifest)
            from src.python.ai_scorer.evals.schema import load_fixtures, validate_fixtures
            from src.python.ai_scorer.training.schema import load_cases, validate_cases

            validation_errors = (
                validate_fixtures(load_fixtures(golden_temp))
                + validate_cases(load_cases(candidate_temp))
                + validate_cases(load_cases(labeled_temp))
            )
            if validation_errors:
                raise ValueError("Migrated artifact validation failed: " + "; ".join(validation_errors))
            serialized = json.dumps([golden_migrated, candidate_migrated, labeled_migrated])
            if "source_job_id" in serialized:
                raise ValueError("Migrated artifacts still contain source_job_id")
            os.replace(golden_temp, golden_path)
            golden_temp = ""
            os.replace(candidate_temp, candidates_path)
            os.replace(labeled_temp, labeled_path)
            os.replace(manifest_temp, split_manifest_path)
        return {"summary": summary, "manifest": manifest}
    finally:
        if golden_temp and os.path.exists(golden_temp):
            os.unlink(golden_temp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate paid training labels to job fingerprints")
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES)
    parser.add_argument("--labeled", default=DEFAULT_LABELED)
    parser.add_argument("--golden-fixtures", default=DEFAULT_PROMOTION_FIXTURES)
    parser.add_argument("--preferences", default=DEFAULT_PREFERENCES)
    parser.add_argument("--split-manifest", default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--report-out", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    result = migrate(
        candidates_path=args.candidates,
        labeled_path=args.labeled,
        golden_path=args.golden_fixtures,
        preferences_path=args.preferences,
        split_manifest_path=args.split_manifest,
        seed=args.seed,
        val_ratio=args.val_ratio,
        apply=args.apply,
    )
    if args.report_out:
        _write_json(args.report_out, result)
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    if not args.apply:
        print("[training.migrate-fingerprints] dry run only; pass --apply to update artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
