"""
Pattern-based redaction of sensitive fields before committing fixtures.

Redacts email addresses, URLs, and optionally phone numbers.
Does not use NER; pattern-based only for deterministic, dependency-free operation.
"""
from __future__ import annotations

import re

_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_URL = re.compile(r"https?://[^\s\)\]\>\"\']+")
# Conservative phone: explicit country code prefix OR (NXX) NXX-XXXX style
_PHONE = re.compile(
    r"(?<!\w)"
    r"(?:"
    r"\+\d{1,3}[\s\-.]?\(?\d{1,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,4}"  # +XX ... format
    r"|"
    r"\(\d{3}\)[\s\-.]?\d{3}[\s\-.]?\d{4}"  # (XXX) XXX-XXXX
    r")"
    r"(?!\w)"
)

EMAIL_PLACEHOLDER = "[EMAIL REDACTED]"
URL_PLACEHOLDER = "[URL REDACTED]"
PHONE_PLACEHOLDER = "[PHONE REDACTED]"


def redact_text(text: str, redact_urls: bool = True, redact_phones: bool = True) -> str:
    """Apply pattern-based redaction to a text string.

    Redaction order: email first, then URLs, then phone numbers.
    Returns the redacted string; original is unchanged.
    """
    if not text:
        return text
    text = _EMAIL.sub(EMAIL_PLACEHOLDER, text)
    if redact_urls:
        text = _URL.sub(URL_PLACEHOLDER, text)
    if redact_phones:
        text = _PHONE.sub(PHONE_PLACEHOLDER, text)
    return text


def redact_case_fields(title: str, description: str, location: str) -> tuple:
    """Redact the three user-visible text fields of an eval case.

    Returns (redacted_title, redacted_description, redacted_location).
    URLs are redacted in description but kept in title and location
    (location rarely has URLs and titles never do).
    """
    return (
        redact_text(title, redact_urls=False, redact_phones=False),
        redact_text(description, redact_urls=True, redact_phones=True),
        redact_text(location, redact_urls=False, redact_phones=False),
    )
