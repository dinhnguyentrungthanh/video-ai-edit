"""Website, domain, phone and @handle tokens in OCR text: the shared ad-text evidence.

Moved unchanged from ``review_workflow`` (session D, 2026-10-15) so that
``subtitle_band`` keeps every line holding such a token a main-list card without
importing ``review_workflow``. The text scan imports it too, to keep every track
holding such a token past its track limit (item 6, 2026-10-16): it is in the text
stage's cache key, so an edit here makes "Chạy lại" scan the text again.
"""
from __future__ import annotations

import re

AD_TEXT_PATTERNS = (
    re.compile(r"\b(?:https?|www)\b", re.IGNORECASE),
    re.compile(
        r"[a-z0-9][a-z0-9-]*\s?[.,·•]\s?(?:com|net|org|vn|tv|me|io|xyz|cc|co|info|top|club|"
        r"link|live|online|site|biz|asia|pro|app|fun|vip|bet|win|us|to|ly|gg)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b[a-z0-9][a-z0-9-]{2,}\s(?:com|net|org|vn|tv|xyz)\b", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:\+?\d[\s.\-]?){8,}(?!\d)"),
    re.compile(r"(?<![\w.])@[a-z0-9_.]{3,}", re.IGNORECASE),
)


def ad_text_tokens(text: str) -> list[str]:
    """Website, domain, phone or @handle tokens in one OCR reading."""
    found = []
    for pattern in AD_TEXT_PATTERNS:
        found += [match.group(0).strip() for match in pattern.finditer(str(text or ""))]
    return list(dict.fromkeys(value for value in found if value))
