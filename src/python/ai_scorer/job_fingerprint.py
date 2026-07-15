"""Versioned job-description identity shared by training and evaluation."""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import unicodedata
from collections.abc import Iterable

from src.python.ai_scorer.description_normalization import normalize_description_markdown
from src.python.ai_scorer.evals.redaction import redact_text

FINGERPRINT_VERSION = "1"
DESCRIPTION_BASIS = "description"
LEGACY_PARTIAL_BASIS = "legacy-partial"
TITLE_LOCATION_BASIS = "title-location"
SUPPORTED_BASES = {DESCRIPTION_BASIS, LEGACY_PARTIAL_BASIS, TITLE_LOCATION_BASIS}

_PREFIX = "jdfp"
_WHITESPACE = re.compile(r"\s+")


def canonicalize_text(value: object, *, casefold: bool = False) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE.sub(" ", text).strip()
    return text.casefold() if casefold else text


def canonicalize_description(value: object) -> str:
    normalized = normalize_description_markdown(value)
    redacted = redact_text(normalized, redact_urls=True, redact_phones=True)
    return canonicalize_text(redacted)


def canonicalize_title(value: object) -> str:
    return canonicalize_text(value, casefold=True)


def canonicalize_location(value: object) -> str:
    return canonicalize_text(value, casefold=True)


def _digest(basis: str, payload: str) -> str:
    if basis not in SUPPORTED_BASES:
        raise ValueError(f"Unsupported fingerprint basis: {basis}")
    prefix = f"{_PREFIX}:v{FINGERPRINT_VERSION}:{basis}"
    digest = hashlib.sha256(f"{prefix}\0{payload}".encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def description_fingerprint(description: object, *, title: object = "", location: object = "") -> tuple[str, str]:
    canonical = canonicalize_description(description)
    if canonical:
        return _digest(DESCRIPTION_BASIS, canonical), DESCRIPTION_BASIS
    fallback = json.dumps(
        [canonicalize_title(title), canonicalize_location(location)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _digest(TITLE_LOCATION_BASIS, fallback), TITLE_LOCATION_BASIS


def canonicalize_snippets(snippets: Iterable[object]) -> list[str]:
    normalized = {canonicalize_description(snippet) for snippet in snippets}
    normalized.discard("")
    return sorted(normalized)


def legacy_partial_fingerprint(
    snippets: Iterable[object],
    *,
    title: object = "",
    location: object = "",
) -> tuple[str, str, list[str]]:
    canonical_snippets = canonicalize_snippets(snippets)
    if canonical_snippets:
        payload = json.dumps(canonical_snippets, ensure_ascii=False, separators=(",", ":"))
        return _digest(LEGACY_PARTIAL_BASIS, payload), LEGACY_PARTIAL_BASIS, canonical_snippets

    fallback = json.dumps(
        [canonicalize_title(title), canonicalize_location(location)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _digest(TITLE_LOCATION_BASIS, fallback), TITLE_LOCATION_BASIS, []


def parse_fingerprint(value: str) -> tuple[str, str]:
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != _PREFIX or parts[1] != f"v{FINGERPRINT_VERSION}":
        raise ValueError(f"Invalid job fingerprint: {value!r}")
    basis = parts[2]
    digest = parts[3]
    if basis not in SUPPORTED_BASES:
        raise ValueError(f"Unsupported job fingerprint: {value!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"Invalid job fingerprint digest: {value!r}")
    return basis, digest


def validate_fingerprint(value: object, expected_basis: str | None = None) -> str | None:
    if not isinstance(value, str) or not value:
        return "job_fingerprint must be a non-empty string"
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != _PREFIX or parts[1] != f"v{FINGERPRINT_VERSION}":
        return f"job_fingerprint has an unsupported format: {value!r}"
    basis = parts[2]
    digest = parts[3]
    if basis not in SUPPORTED_BASES:
        return f"job_fingerprint has unsupported basis: {basis!r}"
    if expected_basis is not None and basis != expected_basis:
        return f"job_fingerprint basis {basis!r} does not match {expected_basis!r}"
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return "job_fingerprint digest must be 64 lowercase hexadecimal characters"
    return None


def fingerprint_basis(value: str) -> str:
    error = validate_fingerprint(value)
    if error:
        raise ValueError(error)
    return value.split(":")[2]


def stable_json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def partition_fingerprints(
    fingerprints: Iterable[str],
    *,
    seed: int,
    val_ratio: float,
) -> tuple[list[str], list[str]]:
    if val_ratio <= 0 or val_ratio >= 1:
        raise ValueError("val_ratio must satisfy: 0 < val_ratio < 1")
    unique = sorted(set(fingerprints))
    if len(unique) < 2:
        raise ValueError("At least two distinct job fingerprints are required for train/val splitting")
    random.Random(seed).shuffle(unique)
    n_val = math.ceil(len(unique) * val_ratio)
    n_val = max(1, min(len(unique) - 1, n_val))
    return sorted(unique[n_val:]), sorted(unique[:n_val])
