from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable

from src.python.ai_scorer.job_fingerprint import (
    DESCRIPTION_BASIS,
    LEGACY_PARTIAL_BASIS,
    fingerprint_basis,
    stable_json_hash,
    validate_fingerprint,
)

SPLIT_MANIFEST_VERSION = "1"
DEFAULT_SPLIT_MANIFEST = "src/python/ai_scorer/training/data/proposed/split-manifest.json"
DEFAULT_PROMOTION_FIXTURES = "src/python/ai_scorer/evals/data/canonical/v1.json"


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_golden_fingerprints(path: str = DEFAULT_PROMOTION_FIXTURES) -> tuple[list[str], int]:
    from src.python.ai_scorer.evals.schema import load_fixtures

    cases = load_fixtures(path)
    fingerprints: set[str] = set()
    for index, case in enumerate(cases):
        error = validate_fingerprint(case.job_fingerprint)
        if error:
            raise ValueError(f"golden case[{index}] {case.case_id!r}: {error}")
        fingerprints.add(case.job_fingerprint)
    return sorted(fingerprints), len(cases)


def build_split_manifest(
    *,
    train_fingerprints: Iterable[str],
    val_fingerprints: Iterable[str],
    golden_fingerprints: Iterable[str],
    confirmed_promotion_fingerprints: Iterable[str] = (),
    quarantined_fingerprints: Iterable[str] = (),
    seed: int,
    val_ratio: float,
    golden_fixture_path: str,
    golden_case_count: int,
    preference_set_hash: str = "",
    reconciliation: list[dict] | None = None,
    applied_fingerprint_mappings: list[dict] | None = None,
    native_description_fingerprints: Iterable[str] = (),
) -> dict:
    train = sorted(set(train_fingerprints))
    val = sorted(set(val_fingerprints))
    golden = sorted(set(golden_fingerprints))
    confirmed = sorted(set(confirmed_promotion_fingerprints))
    quarantined = sorted(set(quarantined_fingerprints))
    native = sorted(set(native_description_fingerprints))
    excluded = sorted(set(golden) | set(confirmed) | set(quarantined))

    manifest = {
        "format_version": SPLIT_MANIFEST_VERSION,
        "seed": seed,
        "requested_val_ratio": val_ratio,
        "fingerprint_bases": sorted({fingerprint_basis(item) for item in train + val}),
        "preference_set_hash": preference_set_hash,
        "golden_fixture": golden_fixture_path,
        "golden_fixture_sha256": file_sha256(golden_fixture_path),
        "golden_case_count": golden_case_count,
        "golden_fingerprints": golden,
        "golden_fingerprint_set_sha256": stable_json_hash(golden),
        "confirmed_promotion_fingerprints": confirmed,
        "quarantined_fingerprints": quarantined,
        "promotion_exclusion_fingerprints": excluded,
        "train_fingerprints": train,
        "val_fingerprints": val,
        "reconciliation": reconciliation or [],
        "applied_fingerprint_mappings": applied_fingerprint_mappings or [],
        "native_description_fingerprints": native,
    }
    errors = validate_split_manifest(manifest)
    if errors:
        raise ValueError("Invalid split manifest: " + "; ".join(errors))
    return manifest


def validate_split_manifest(manifest: dict) -> list[str]:
    errors: list[str] = []
    if manifest.get("format_version") != SPLIT_MANIFEST_VERSION:
        errors.append("unsupported split manifest format_version")

    names = (
        "train_fingerprints",
        "val_fingerprints",
        "golden_fingerprints",
        "confirmed_promotion_fingerprints",
        "quarantined_fingerprints",
        "promotion_exclusion_fingerprints",
    )
    values: dict[str, set[str]] = {}
    for name in names:
        raw = manifest.get(name)
        if not isinstance(raw, list):
            errors.append(f"{name} must be an array")
            values[name] = set()
            continue
        values[name] = set(raw)
        for value in raw:
            error = validate_fingerprint(value)
            if error:
                errors.append(f"{name}: {error}")

    train = values["train_fingerprints"]
    val = values["val_fingerprints"]
    excluded = values["promotion_exclusion_fingerprints"]
    if not train:
        errors.append("train_fingerprints must not be empty")
    if not val:
        errors.append("val_fingerprints must not be empty")
    if train & val:
        errors.append("train and validation fingerprints overlap")
    if train & excluded:
        errors.append("train and promotion exclusion fingerprints overlap")
    if val & excluded:
        errors.append("validation and promotion exclusion fingerprints overlap")

    expected_excluded = (
        values["golden_fingerprints"]
        | values["confirmed_promotion_fingerprints"]
        | values["quarantined_fingerprints"]
    )
    if excluded != expected_excluded:
        errors.append("promotion_exclusion_fingerprints is not the union of golden, confirmed, and quarantined")

    actual_bases = sorted({fingerprint_basis(item) for item in train | val})
    if manifest.get("fingerprint_bases") != actual_bases:
        errors.append("fingerprint_bases does not match the train/validation fingerprints")
    raw_mappings = manifest.get("applied_fingerprint_mappings", [])
    if not isinstance(raw_mappings, list):
        errors.append("applied_fingerprint_mappings must be an array")
        raw_mappings = []

    mapping_legacy: set[str] = set()
    mapping_full: set[str] = set()
    assigned = train | val
    for index, mapping in enumerate(raw_mappings):
        where = f"applied_fingerprint_mappings[{index}]"
        if not isinstance(mapping, dict):
            errors.append(f"{where} must be an object")
            continue
        legacy = mapping.get("legacy_fingerprint")
        full = mapping.get("full_fingerprint")
        if not isinstance(legacy, str) or validate_fingerprint(legacy, LEGACY_PARTIAL_BASIS):
            errors.append(f"{where}.legacy_fingerprint must be a valid legacy-partial fingerprint")
        elif legacy in mapping_legacy:
            errors.append(f"{where}.legacy_fingerprint is duplicated")
        else:
            mapping_legacy.add(legacy)
        if not isinstance(full, str) or validate_fingerprint(full, DESCRIPTION_BASIS):
            errors.append(f"{where}.full_fingerprint must be a valid description fingerprint")
        elif full in mapping_full:
            errors.append(f"{where}.full_fingerprint is duplicated")
        else:
            mapping_full.add(full)
        if isinstance(legacy, str) and legacy in (assigned | excluded):
            errors.append(f"{where}.legacy_fingerprint remains assigned or excluded")
        if isinstance(full, str) and full not in assigned:
            errors.append(f"{where}.full_fingerprint is not assigned to train or validation")
        if not isinstance(mapping.get("source_report_sha256"), str) or len(mapping["source_report_sha256"]) != 64:
            errors.append(f"{where}.source_report_sha256 must be a SHA-256 digest")

    raw_native = manifest.get("native_description_fingerprints", [])
    if not isinstance(raw_native, list):
        errors.append("native_description_fingerprints must be an array")
        raw_native = []
    native: set[str] = set()
    for index, fingerprint in enumerate(raw_native):
        where = f"native_description_fingerprints[{index}]"
        error = validate_fingerprint(fingerprint, DESCRIPTION_BASIS)
        if error:
            errors.append(f"{where}: {error}")
        elif fingerprint in native:
            errors.append(f"{where} is duplicated")
        else:
            native.add(fingerprint)
        if isinstance(fingerprint, str) and fingerprint not in assigned:
            errors.append(f"{where} is not assigned to train or validation")
    if native & mapping_full:
        errors.append("native description fingerprints overlap reviewed legacy mappings")

    if LEGACY_PARTIAL_BASIS in actual_bases and len(actual_bases) > 1:
        assigned_full = {fingerprint for fingerprint in assigned if fingerprint_basis(fingerprint) == DESCRIPTION_BASIS}
        unmapped_full = assigned_full - mapping_full - native
        if unmapped_full:
            errors.append(
                "legacy-partial fingerprints can mix with description fingerprints only through "
                "reviewed mappings or declared native additions; "
                f"unclassified description fingerprints: {len(unmapped_full)}"
            )
    return errors


def load_split_manifest(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise ValueError(f"Expected object in {path}")
    errors = validate_split_manifest(manifest)
    if errors:
        raise ValueError(f"Invalid split manifest {path}: " + "; ".join(errors))
    return manifest


def validate_current_golden(manifest: dict) -> list[str]:
    errors: list[str] = []
    path = manifest.get("golden_fixture")
    if not isinstance(path, str) or not path:
        return ["golden_fixture must be a non-empty path"]
    if not os.path.isfile(path):
        return [f"golden fixture missing: {path}"]
    if file_sha256(path) != manifest.get("golden_fixture_sha256"):
        errors.append("golden fixture hash changed after the dataset split was created")
    try:
        fingerprints, case_count = load_golden_fingerprints(path)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        return errors
    if fingerprints != manifest.get("golden_fingerprints"):
        errors.append("golden fingerprint set changed after the dataset split was created")
    if case_count != manifest.get("golden_case_count"):
        errors.append("golden case count changed after the dataset split was created")
    return errors


def write_split_manifest(manifest: dict, path: str) -> None:
    errors = validate_split_manifest(manifest)
    if errors:
        raise ValueError("Invalid split manifest: " + "; ".join(errors))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
