from __future__ import annotations

import html
import re
from typing import Any


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text)


def normalize_description_markdown(description: Any) -> str:
    if description is None:
        return ""

    text = str(description)
    if not text:
        return ""

    try:
        text = html.unescape(text)

        replacements = [
            (r"(?is)<\s*br\s*/?\s*>", "\n"),
            (r"(?is)</\s*p\s*>", "\n\n"),
            (r"(?is)<\s*p\b[^>]*>", ""),
            (r"(?is)</\s*div\s*>", "\n"),
            (r"(?is)<\s*div\b[^>]*>", ""),
            (r"(?is)</\s*h([1-6])\s*>", "\n\n"),
            (r"(?is)<\s*h1\b[^>]*>", "# "),
            (r"(?is)<\s*h2\b[^>]*>", "## "),
            (r"(?is)<\s*h3\b[^>]*>", "### "),
            (r"(?is)<\s*h4\b[^>]*>", "#### "),
            (r"(?is)<\s*h5\b[^>]*>", "##### "),
            (r"(?is)<\s*h6\b[^>]*>", "###### "),
            (r"(?is)<\s*li\b[^>]*>", "\n- "),
            (r"(?is)</\s*li\s*>", ""),
            (r"(?is)<\s*(ul|ol)\b[^>]*>", "\n"),
            (r"(?is)</\s*(ul|ol)\s*>", "\n"),
            (r"(?is)<\s*strong\b[^>]*>", "**"),
            (r"(?is)</\s*strong\s*>", "**"),
            (r"(?is)<\s*b\b[^>]*>", "**"),
            (r"(?is)</\s*b\s*>", "**"),
            (r"(?is)<\s*em\b[^>]*>", "*"),
            (r"(?is)</\s*em\s*>", "*"),
            (r"(?is)<\s*i\b[^>]*>", "*"),
            (r"(?is)</\s*i\s*>", "*"),
            (r"(?is)<\s*/?\s*(script|style)\b[^>]*>", ""),
        ]

        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text)

        text = re.sub(
            r'(?is)<\s*a\b[^>]*href\s*=\s*(["\'])(.*?)\1[^>]*>(.*?)</\s*a\s*>',
            r"[\3](\2)",
            text,
        )
        text = re.sub(r"(?is)<[^>]+>", "", text)
    except Exception:
        text = str(description)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\t\f\v]+", " ", text)
    text = _collapse_blank_lines(text)
    return text.strip()
