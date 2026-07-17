from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import tempfile
from collections import Counter

from src.python.ai_scorer.job_fingerprint import (
    DESCRIPTION_BASIS,
    canonicalize_description,
    canonicalize_location,
    canonicalize_title,
    description_fingerprint,
    fingerprint_basis,
)
from src.python.ai_scorer.training.dataset_split import (
    DEFAULT_SPLIT_MANIFEST,
    file_sha256,
    load_split_manifest,
    validate_current_golden,
    validate_split_manifest,
)
from src.python.ai_scorer.training.schema import TrainingCase, load_cases, validate_cases


DEFAULT_JOB_POOL = "src/python/ai_scorer/training/data/proposed/job-pool.json"
DEFAULT_LABELED = "src/python/ai_scorer/training/data/proposed/labeled.json"
DEFAULT_REPORT = "src/python/ai_scorer/training/data/proposed/job-pool-reconciliation.json"
DEFAULT_MARKDOWN = "src/python/ai_scorer/training/data/proposed/job-pool-reconciliation.md"
DEFAULT_CANDIDATES = "src/python/ai_scorer/training/data/proposed/candidates.json"
DEFAULT_APPLY_RECEIPT = "src/python/ai_scorer/training/data/proposed/job-pool-reconciliation-apply.json"
REPORT_SCHEMA_VERSION = "1"


def _read_json(path: str) -> object:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: str, value: object) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _write_text(path: str, value: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(value)


def _write_temp_json(target: str, value: object) -> str:
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json.tmp",
        prefix="job-pool-apply-",
        dir=directory,
        delete=False,
    )
    try:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        return handle.name
    finally:
        handle.close()


def validate_job_pool(root: object, golden_fingerprints: set[str]) -> list[str]:
    if not isinstance(root, dict):
        return ["job pool root must be an object"]
    jobs = root.get("jobs")
    if not isinstance(jobs, list):
        return ["job pool jobs must be an array"]

    errors: list[str] = []
    seen: set[str] = set()
    for index, job in enumerate(jobs):
        where = f"jobs[{index}]"
        if not isinstance(job, dict):
            errors.append(f"{where} must be an object")
            continue
        if "_id" in job or "source_job_id" in job:
            errors.append(f"{where} contains a database identity field")
        fingerprint = job.get("job_fingerprint")
        if not isinstance(fingerprint, str):
            errors.append(f"{where}.job_fingerprint must be a string")
            continue
        if fingerprint in seen:
            errors.append(f"{where} duplicates job_fingerprint {fingerprint!r}")
        seen.add(fingerprint)
        try:
            expected, basis = description_fingerprint(
                job.get("description", ""),
                title=job.get("title", ""),
                location=job.get("location", ""),
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"{where}: unable to compute fingerprint: {exc}")
            continue
        if fingerprint != expected:
            errors.append(f"{where}.job_fingerprint does not match its description")
        if job.get("fingerprint_basis") != basis:
            errors.append(f"{where}.fingerprint_basis does not match its description")

    overlap = seen & golden_fingerprints
    if overlap:
        errors.append(f"job pool overlaps {len(overlap)} golden fingerprints")
    stats = root.get("stats")
    if isinstance(stats, dict) and stats.get("sampled_job_count") != len(jobs):
        errors.append("stats.sampled_job_count does not match jobs length")
    return errors


def _group_labeled_cases(cases: list[TrainingCase]) -> list[dict]:
    groups: dict[str, dict] = {}
    for case in cases:
        group = groups.setdefault(
            case.job_fingerprint,
            {
                "legacy_fingerprint": case.job_fingerprint,
                "fingerprint_basis": case.fingerprint_basis,
                "titles": set(),
                "locations": set(),
                "canonical_titles": set(),
                "canonical_locations": set(),
                "canonical_snippets": set(),
                "preference_keys": set(),
                "paid_case_count": 0,
            },
        )
        group["titles"].add(case.title)
        group["locations"].add(case.location)
        group["canonical_titles"].add(canonicalize_title(case.title))
        group["canonical_locations"].add(canonicalize_location(case.location))
        group["canonical_snippets"].update(
            snippet
            for snippet in (canonicalize_description(item) for item in case.relevant_snippets)
            if snippet
        )
        group["preference_keys"].add(case.preference_key)
        group["paid_case_count"] += 1
    return [groups[fingerprint] for fingerprint in sorted(groups)]


def _canonical_pool_jobs(jobs: list[dict]) -> list[dict]:
    return [
        {
            "job_fingerprint": job["job_fingerprint"],
            "title": str(job.get("title", "") or ""),
            "location": str(job.get("location", "") or ""),
            "canonical_title": canonicalize_title(job.get("title", "")),
            "canonical_location": canonicalize_location(job.get("location", "")),
            "canonical_description": canonicalize_description(job.get("description", "")),
        }
        for job in jobs
    ]


def _candidate_evidence(group: dict, pool_job: dict) -> dict:
    snippets = group["canonical_snippets"]
    matched = [snippet for snippet in snippets if snippet in pool_job["canonical_description"]]
    return {
        "full_fingerprint": pool_job["job_fingerprint"],
        "title": pool_job["title"],
        "location": pool_job["location"],
        "matched_snippet_count": len(matched),
        "all_snippets_contained": bool(snippets) and len(matched) == len(snippets),
        "title_equal": pool_job["canonical_title"] in group["canonical_titles"],
        "location_equal": pool_job["canonical_location"] in group["canonical_locations"],
        "substantive_match": any(len(snippet) >= 80 for snippet in matched),
    }


def build_reconciliation_report(
    job_pool_root: dict,
    labeled_cases: list[TrainingCase],
    split_manifest: dict,
    *,
    inputs: dict[str, dict] | None = None,
) -> dict:
    pool_jobs = _canonical_pool_jobs(job_pool_root["jobs"])
    prior = {
        item["job_fingerprint"]: item
        for item in split_manifest.get("reconciliation", [])
        if isinstance(item, dict) and isinstance(item.get("job_fingerprint"), str)
    }

    results: list[dict] = []
    proposals: list[dict] = []
    for group in _group_labeled_cases(labeled_cases):
        evidence = [_candidate_evidence(group, pool_job) for pool_job in pool_jobs]
        exact = [candidate for candidate in evidence if candidate["all_snippets_contained"]]
        supporting = [
            candidate
            for candidate in evidence
            if candidate["title_equal"]
            or (candidate["matched_snippet_count"] and candidate["substantive_match"])
        ]
        supporting.sort(
            key=lambda item: (
                not item["all_snippets_contained"],
                -item["matched_snippet_count"],
                not item["title_equal"],
                item["full_fingerprint"],
            )
        )
        previous_status = prior.get(group["legacy_fingerprint"], {}).get("status", "unrecorded")
        review_flags: list[str] = []
        proposed_fingerprint: str | None = None

        if previous_status == "confirmed_promotion_overlap":
            classification = "confirmed_promotion_overlap"
        elif fingerprint_basis(group["legacy_fingerprint"]) == DESCRIPTION_BASIS:
            classification = "already_full_identity"
        elif not group["canonical_snippets"]:
            classification = "insufficient_legacy_evidence"
        elif len(exact) > 1:
            classification = "ambiguous_exact_match"
        elif len(exact) == 1:
            match = exact[0]
            if match["title_equal"] and match["location_equal"]:
                classification = "proposed_unique_match"
                proposed_fingerprint = match["full_fingerprint"]
                if previous_status == "quarantined":
                    review_flags.append("previously_quarantined")
            else:
                classification = "unique_match_needs_review"
                if not match["title_equal"]:
                    review_flags.append("title_mismatch")
                if not match["location_equal"]:
                    review_flags.append("location_mismatch")
        else:
            classification = "no_exact_match"

        result = {
            "legacy_fingerprint": group["legacy_fingerprint"],
            "fingerprint_basis": group["fingerprint_basis"],
            "titles": sorted(group["titles"]),
            "locations": sorted(group["locations"]),
            "preference_keys": sorted(group["preference_keys"]),
            "paid_case_count": group["paid_case_count"],
            "snippet_count": len(group["canonical_snippets"]),
            "previous_status": previous_status,
            "classification": classification,
            "proposed_full_fingerprint": proposed_fingerprint,
            "review_flags": review_flags,
            "exact_match_count": len(exact),
            "evidence": supporting[:20],
            "evidence_truncated": len(supporting) > 20,
        }
        results.append(result)
        if proposed_fingerprint:
            proposals.append(
                {
                    "legacy_fingerprint": group["legacy_fingerprint"],
                    "full_fingerprint": proposed_fingerprint,
                    "titles": sorted(group["titles"]),
                    "locations": sorted(group["locations"]),
                    "paid_case_count": group["paid_case_count"],
                    "previous_status": previous_status,
                    "review_flags": review_flags,
                }
            )

    classification_counts = Counter(item["classification"] for item in results)
    classification_case_counts = Counter()
    for item in results:
        classification_case_counts[item["classification"]] += item["paid_case_count"]

    pool_fingerprints = {job["job_fingerprint"] for job in job_pool_root["jobs"]}
    golden_fingerprints = set(split_manifest["golden_fingerprints"])
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "dry_run": True,
        "inputs": inputs or {},
        "summary": {
            "pool_job_count": len(pool_fingerprints),
            "pool_golden_overlap_count": len(pool_fingerprints & golden_fingerprints),
            "labeled_case_count": len(labeled_cases),
            "legacy_group_count": len(results),
            "classification_group_counts": dict(sorted(classification_counts.items())),
            "classification_paid_case_counts": dict(sorted(classification_case_counts.items())),
            "mapping_proposal_count": len(proposals),
            "mapping_proposal_paid_case_count": sum(item["paid_case_count"] for item in proposals),
        },
        "mapping_proposals": proposals,
        "groups": results,
    }


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Job-pool reconciliation dry run",
        "",
        "This report is read-only. No candidates, labels, or split assignments were modified.",
        "",
        "## Summary",
        "",
        f"- Pool jobs: {summary['pool_job_count']}",
        f"- Golden overlap: {summary['pool_golden_overlap_count']}",
        f"- Paid labels: {summary['labeled_case_count']}",
        f"- Legacy groups: {summary['legacy_group_count']}",
        f"- Proposed unique mappings: {summary['mapping_proposal_count']} groups / {summary['mapping_proposal_paid_case_count']} paid labels",
        "",
        "### Classification counts",
        "",
        "| Classification | Groups | Paid labels |",
        "| --- | ---: | ---: |",
    ]
    group_counts = summary["classification_group_counts"]
    case_counts = summary["classification_paid_case_counts"]
    for classification in sorted(group_counts):
        lines.append(f"| {classification} | {group_counts[classification]} | {case_counts[classification]} |")

    lines.extend(
        [
            "",
            "## Proposed unique mappings",
            "",
            "Every proposal requires review; this command cannot apply mappings.",
            "",
            "| Title | Paid labels | Previous status | Review flags | Legacy fingerprint | Full fingerprint |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for proposal in report["mapping_proposals"]:
        title = " / ".join(proposal["titles"])
        flags = ", ".join(proposal["review_flags"]) or "none"
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    title,
                    proposal["paid_case_count"],
                    proposal["previous_status"],
                    flags,
                    proposal["legacy_fingerprint"],
                    proposal["full_fingerprint"],
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## All groups",
            "",
            "| Title | Paid labels | Previous status | Classification | Exact matches |",
            "| --- | ---: | --- | --- | ---: |",
        ]
    )
    for group in report["groups"]:
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    " / ".join(group["titles"]),
                    group["paid_case_count"],
                    group["previous_status"],
                    group["classification"],
                    group["exact_match_count"],
                )
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def reconcile(
    *,
    job_pool_path: str,
    labeled_path: str,
    split_manifest_path: str,
    report_out: str,
    markdown_out: str,
) -> dict:
    manifest = load_split_manifest(split_manifest_path)
    golden_errors = validate_current_golden(manifest)
    if golden_errors:
        raise ValueError("Golden fixture validation failed: " + "; ".join(golden_errors))

    job_pool_root = _read_json(job_pool_path)
    pool_errors = validate_job_pool(job_pool_root, set(manifest["golden_fingerprints"]))
    if pool_errors:
        raise ValueError("Invalid job pool: " + "; ".join(pool_errors))

    labeled_cases = load_cases(labeled_path)
    label_errors = validate_cases(labeled_cases)
    unlabeled_count = sum(case.label_available is None for case in labeled_cases)
    if unlabeled_count:
        label_errors.append(f"labeled inventory contains {unlabeled_count} unlabeled cases")
    if label_errors:
        raise ValueError("Invalid labeled inventory: " + "; ".join(label_errors))

    inputs = {
        "job_pool": {"path": job_pool_path, "sha256": file_sha256(job_pool_path)},
        "labeled": {"path": labeled_path, "sha256": file_sha256(labeled_path)},
        "split_manifest": {
            "path": split_manifest_path,
            "sha256": file_sha256(split_manifest_path),
        },
    }
    report = build_reconciliation_report(
        job_pool_root,
        labeled_cases,
        manifest,
        inputs=inputs,
    )
    _write_json(report_out, report)
    _write_text(markdown_out, render_markdown(report))
    return report


def apply_case_mappings(raw_cases: list[dict], mappings: dict[str, str]) -> tuple[list[dict], list[str]]:
    updated: list[dict] = []
    changed_case_ids: list[str] = []
    for item in raw_cases:
        case = dict(item)
        replacement = mappings.get(case.get("job_fingerprint"))
        if replacement:
            case["job_fingerprint"] = replacement
            case["fingerprint_basis"] = DESCRIPTION_BASIS
            changed_case_ids.append(str(case.get("case_id", "")))
        updated.append(case)
    return updated, changed_case_ids


def build_applied_manifest(
    manifest: dict,
    proposals: list[dict],
    *,
    source_report_sha256: str,
) -> tuple[dict, list[dict]]:
    updated = json.loads(json.dumps(manifest))
    train = set(updated["train_fingerprints"])
    val = set(updated["val_fingerprints"])
    quarantined = set(updated["quarantined_fingerprints"])
    confirmed = set(updated["confirmed_promotion_fingerprints"])
    golden = set(updated["golden_fingerprints"])
    newly_eligible: list[tuple[dict, str]] = []
    applied: list[dict] = list(updated.get("applied_fingerprint_mappings", []))
    existing_legacy = {item["legacy_fingerprint"] for item in applied}
    existing_full = {item["full_fingerprint"] for item in applied}

    assignments: dict[str, str] = {}
    for proposal in proposals:
        legacy = proposal["legacy_fingerprint"]
        full = proposal["full_fingerprint"]
        if legacy in existing_legacy or full in existing_full:
            raise ValueError(f"Mapping is already applied: {legacy} -> {full}")
        if full in (train | val | quarantined | confirmed | golden):
            raise ValueError(f"Proposed full fingerprint is already assigned: {full}")
        memberships = [name for name, values in (("train", train), ("val", val), ("quarantined", quarantined), ("confirmed", confirmed)) if legacy in values]
        if len(memberships) != 1:
            raise ValueError(f"Legacy fingerprint must have exactly one assignment: {legacy}")
        previous = memberships[0]
        if previous == "confirmed":
            raise ValueError(f"Refusing to map a confirmed promotion overlap: {legacy}")
        if previous == "train":
            train.remove(legacy)
            train.add(full)
            assignments[legacy] = "train"
        elif previous == "val":
            val.remove(legacy)
            val.add(full)
            assignments[legacy] = "val"
        else:
            quarantined.remove(legacy)
            newly_eligible.append((proposal, full))

    total_after = len(train) + len(val) + len(newly_eligible)
    target_val = max(1, min(total_after - 1, math.ceil(total_after * updated["requested_val_ratio"])))
    val_needed = max(0, target_val - len(val))
    ordered_new = sorted(
        newly_eligible,
        key=lambda item: stable_mapping_order_key(updated["seed"], item[1]),
    )
    for index, (proposal, full) in enumerate(ordered_new):
        legacy = proposal["legacy_fingerprint"]
        if index < val_needed:
            val.add(full)
            assignments[legacy] = "val"
        else:
            train.add(full)
            assignments[legacy] = "train"

    proposal_by_legacy = {item["legacy_fingerprint"]: item for item in proposals}
    for legacy in sorted(proposal_by_legacy):
        proposal = proposal_by_legacy[legacy]
        applied.append(
            {
                "legacy_fingerprint": legacy,
                "full_fingerprint": proposal["full_fingerprint"],
                "assigned_split": assignments[legacy],
                "paid_case_count": proposal["paid_case_count"],
                "source_report_sha256": source_report_sha256,
                "review_flags": list(proposal.get("review_flags", [])),
            }
        )

    for item in updated.get("reconciliation", []):
        legacy = item.get("job_fingerprint")
        if legacy in proposal_by_legacy:
            item["applied_full_fingerprint"] = proposal_by_legacy[legacy]["full_fingerprint"]
            item["applied_split"] = assignments[legacy]
            item["mapping_source_report_sha256"] = source_report_sha256

    updated["train_fingerprints"] = sorted(train)
    updated["val_fingerprints"] = sorted(val)
    updated["quarantined_fingerprints"] = sorted(quarantined)
    updated["promotion_exclusion_fingerprints"] = sorted(golden | confirmed | quarantined)
    updated["fingerprint_bases"] = sorted(
        {fingerprint_basis(item) for item in train | val}
    )
    updated["applied_fingerprint_mappings"] = applied
    errors = validate_split_manifest(updated)
    if errors:
        raise ValueError("Applied split manifest is invalid: " + "; ".join(errors))
    return updated, applied


def stable_mapping_order_key(seed: int, fingerprint: str) -> str:
    import hashlib

    return hashlib.sha256(f"{seed}:{fingerprint}".encode("utf-8")).hexdigest()


def _case_payload_without_labels(item: dict) -> dict:
    return {key: value for key, value in item.items() if key not in {"label_score", "label_available"}}


def _validate_candidate_label_alignment(candidates: list[dict], labeled: list[dict]) -> None:
    candidate_by_id = {item.get("case_id"): item for item in candidates}
    labeled_by_id = {item.get("case_id"): item for item in labeled}
    if len(candidate_by_id) != len(candidates) or len(labeled_by_id) != len(labeled):
        raise ValueError("Candidate or labeled inventory contains duplicate case IDs")
    if set(candidate_by_id) != set(labeled_by_id):
        raise ValueError("Candidate and labeled case IDs differ")
    mismatched = [
        case_id
        for case_id in candidate_by_id
        if _case_payload_without_labels(candidate_by_id[case_id])
        != _case_payload_without_labels(labeled_by_id[case_id])
    ]
    if mismatched:
        raise ValueError(f"Candidate and labeled payloads differ for {len(mismatched)} cases")


def _replace_validated_files(staged: dict[str, str]) -> None:
    backups: dict[str, str] = {}
    try:
        for target in staged:
            handle = tempfile.NamedTemporaryFile(
                suffix=".backup",
                prefix="job-pool-apply-",
                dir=os.path.dirname(os.path.abspath(target)),
                delete=False,
            )
            handle.close()
            shutil.copy2(target, handle.name)
            backups[target] = handle.name
        try:
            for target, temp_path in staged.items():
                os.replace(temp_path, target)
        except Exception:
            for target, backup_path in backups.items():
                shutil.copy2(backup_path, target)
            raise
    finally:
        for path in list(staged.values()) + list(backups.values()):
            if os.path.exists(path):
                os.unlink(path)


def apply_reconciliation_report(
    *,
    report_path: str,
    candidates_path: str,
    labeled_path: str,
    split_manifest_path: str,
    receipt_out: str,
) -> dict:
    report = _read_json(report_path)
    if not isinstance(report, dict) or report.get("dry_run") is not True:
        raise ValueError("Reconciliation report must be a dry-run report object")
    proposals = report.get("mapping_proposals")
    if not isinstance(proposals, list) or not proposals:
        raise ValueError("Reconciliation report contains no mapping proposals")

    report_sha256 = file_sha256(report_path)
    expected_inputs = report.get("inputs", {})
    for name, path in (("labeled", labeled_path), ("split_manifest", split_manifest_path)):
        expected = expected_inputs.get(name, {}).get("sha256")
        actual = file_sha256(path)
        if expected != actual:
            raise ValueError(f"{name} changed after the reconciliation report was generated")
    job_pool_input = expected_inputs.get("job_pool", {})
    job_pool_path = job_pool_input.get("path")
    if not isinstance(job_pool_path, str) or file_sha256(job_pool_path) != job_pool_input.get("sha256"):
        raise ValueError("job pool changed after the reconciliation report was generated")

    candidates_raw = _read_json(candidates_path)
    labeled_raw = _read_json(labeled_path)
    if not isinstance(candidates_raw, list) or not isinstance(labeled_raw, list):
        raise ValueError("Candidate and labeled inventories must be arrays")
    _validate_candidate_label_alignment(candidates_raw, labeled_raw)
    manifest = load_split_manifest(split_manifest_path)

    mappings = {item["legacy_fingerprint"]: item["full_fingerprint"] for item in proposals}
    if len(mappings) != len(proposals) or len(set(mappings.values())) != len(proposals):
        raise ValueError("Reconciliation proposals are not one-to-one")
    candidates_updated, candidate_ids = apply_case_mappings(candidates_raw, mappings)
    labeled_updated, labeled_ids = apply_case_mappings(labeled_raw, mappings)
    if sorted(candidate_ids) != sorted(labeled_ids):
        raise ValueError("Candidate and labeled mappings affect different cases")
    expected_changed = sum(int(item["paid_case_count"]) for item in proposals)
    if len(candidate_ids) != expected_changed:
        raise ValueError(
            f"Expected to update {expected_changed} cases but matched {len(candidate_ids)}"
        )

    manifest_updated, applied = build_applied_manifest(
        manifest,
        proposals,
        source_report_sha256=report_sha256,
    )
    candidate_temp = _write_temp_json(candidates_path, candidates_updated)
    labeled_temp = _write_temp_json(labeled_path, labeled_updated)
    manifest_temp = _write_temp_json(split_manifest_path, manifest_updated)
    staged = {
        candidates_path: candidate_temp,
        labeled_path: labeled_temp,
        split_manifest_path: manifest_temp,
    }
    try:
        candidate_errors = validate_cases(load_cases(candidate_temp))
        labeled_errors = validate_cases(load_cases(labeled_temp))
        manifest_errors = validate_split_manifest(_read_json(manifest_temp))
        if candidate_errors or labeled_errors or manifest_errors:
            raise ValueError(
                "Staged mapping validation failed: "
                + "; ".join(candidate_errors + labeled_errors + manifest_errors)
            )
        _validate_candidate_label_alignment(candidates_updated, labeled_updated)
        before = {
            "candidates_sha256": file_sha256(candidates_path),
            "labeled_sha256": file_sha256(labeled_path),
            "split_manifest_sha256": file_sha256(split_manifest_path),
        }
        _replace_validated_files(staged)
    except Exception:
        for path in staged.values():
            if os.path.exists(path):
                os.unlink(path)
        raise

    receipt = {
        "receipt_schema_version": "1",
        "source_report": {"path": report_path, "sha256": report_sha256},
        "updated_case_count": len(candidate_ids),
        "applied_mapping_count": len(applied),
        "applied_mappings": applied,
        "before": before,
        "after": {
            "candidates_sha256": file_sha256(candidates_path),
            "labeled_sha256": file_sha256(labeled_path),
            "split_manifest_sha256": file_sha256(split_manifest_path),
        },
    }
    _write_json(receipt_out, receipt)
    return receipt


def apply_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply reviewed job-pool fingerprint mappings")
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES)
    parser.add_argument("--labeled", default=DEFAULT_LABELED)
    parser.add_argument("--split-manifest", default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--receipt-out", default=DEFAULT_APPLY_RECEIPT)
    args = parser.parse_args(argv)

    receipt = apply_reconciliation_report(
        report_path=args.report,
        candidates_path=args.candidates,
        labeled_path=args.labeled,
        split_manifest_path=args.split_manifest,
        receipt_out=args.receipt_out,
    )
    print(json.dumps({
        "updated_case_count": receipt["updated_case_count"],
        "applied_mapping_count": receipt["applied_mapping_count"],
    }, indent=2))
    print(f"[training.apply-job-pool-reconciliation] receipt -> {args.receipt_out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run reconciliation of paid labels against a full job pool")
    parser.add_argument("--job-pool", default=DEFAULT_JOB_POOL)
    parser.add_argument("--labeled", default=DEFAULT_LABELED)
    parser.add_argument("--split-manifest", default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument("--report-out", default=DEFAULT_REPORT)
    parser.add_argument("--markdown-out", default=DEFAULT_MARKDOWN)
    args = parser.parse_args(argv)

    report = reconcile(
        job_pool_path=args.job_pool,
        labeled_path=args.labeled,
        split_manifest_path=args.split_manifest,
        report_out=args.report_out,
        markdown_out=args.markdown_out,
    )
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    print(f"[training.reconcile-job-pool] JSON -> {args.report_out}")
    print(f"[training.reconcile-job-pool] Markdown -> {args.markdown_out}")
    print("[training.reconcile-job-pool] dry run only; no training artifacts were modified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
