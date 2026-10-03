"""Video-platform names that OCR or the logo VLM can read inside an ident.

Stdlib only: textscan imports this module, so it is part of the text stage
cache key and must stay free of model, image or project-state imports.
A match only creates a review candidate; it never authorizes an edit.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

PLATFORMS: dict[str, dict] = {
    "iqiyi": {
        "name": "iQIYI",
        "latin": ("iqiyi", "ioiyi", "ioiy"),
        "chinese": ("爱奇艺",),
    },
    "youku": {"name": "Youku", "latin": ("youku",), "chinese": ("优酷",)},
    "tencent_video": {
        "name": "Tencent Video (WeTV)",
        "latin": ("tencentvideo", "wetv", "vqqcom"),
        "chinese": ("腾讯视频",),
    },
    # Not bare "芒果": that is the fruit in subtitles and scene text.
    "mango_tv": {"name": "Mango TV", "latin": ("mangotv",), "chinese": ("芒果TV",)},
    "sohu": {"name": "Sohu", "latin": ("sohu", "sohuvideo"), "chinese": ("搜狐视频",)},
    "pptv": {"name": "PPTV", "latin": ("pptv",), "chinese": ("PP视频",)},
}
# Bilibili is the upload target, not a third-party platform ident.
EXCLUDED = ("bilibili", "哔哩哔哩", "b站")
# An excluded name's Latin letters exclude a text only when this long ("b站" → "b" would
# exclude every text with the letter b).
EXCLUDED_LATIN_MIN_LENGTH = 4
# Shorter forms ("wetv", "sohu", "iqiyi") must match exactly after folding.
NEAR_MATCH_MIN_LENGTH = 6
# Domains match exactly: "v.qq.com" folds to "yoocom", one edit from "ZOO COM".
EXACT_ONLY = ("vqqcom",)

# OCR confusables, folded on both sides: 0/q→o, l/1/|/!/I→i, v→y.
_CONFUSABLES = str.maketrans({
    "0": "o", "q": "o", "l": "i", "1": "i", "|": "i", "!": "i", "v": "y",
})


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _fold(value: str) -> str:
    return _normalize(value).translate(_CONFUSABLES)


def _compact(folded: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", folded)


def _latin_candidates(folded: str) -> list[str]:
    """Tokens, whitespace chunks without punctuation and adjacent token pairs."""
    tokens = re.findall(r"[a-z0-9]+", folded)
    chunks = [_compact(chunk) for chunk in folded.split()]
    pairs = [first + second for first, second in zip(tokens, tokens[1:])]
    return list(dict.fromkeys(value for value in tokens + chunks + pairs if value))


def _within_one_edit(first: str, second: str) -> bool:
    if abs(len(first) - len(second)) > 1:
        return False
    if len(first) > len(second):
        first, second = second, first
    index = 0
    while index < len(first) and first[index] == second[index]:
        index += 1
    if len(first) == len(second):
        return first[index + 1:] == second[index + 1:]
    return first[index:] == second[index + 1:]


_FOLDED_FORMS = {
    key: tuple(_compact(_fold(form)) for form in platform["latin"])
    for key, platform in PLATFORMS.items()
}
_CHINESE_FORMS = {
    key: tuple(_normalize(form) for form in platform["chinese"])
    for key, platform in PLATFORMS.items()
}
_EXACT_ONLY = {_compact(_fold(form)) for form in EXACT_ONLY}


def _result(key: str, text: str, rule: str) -> dict:
    return {"key": key, "name": PLATFORMS[key]["name"], "text": text, "rule": rule}


def _excluded(normalized: str, folded: str) -> bool:
    compact = _compact(folded)
    for form in EXCLUDED:
        if _normalize(form) in normalized:
            return True
        latin = _compact(_fold(form))
        if len(latin) >= EXCLUDED_LATIN_MIN_LENGTH and latin in compact:
            return True
    return False


def match_platform_text(text: object) -> dict | None:
    """Return the video platform named by one OCR/VLM text, or ``None``.

    Chinese names match as substrings; Latin names match a whole token, a
    whole whitespace chunk or two adjacent tokens, so short scene words
    ("mango", "video", "DEN") never qualify.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    original = text.strip()
    normalized = _normalize(original)
    folded = normalized.translate(_CONFUSABLES)
    if _excluded(normalized, folded):
        return None
    for key, forms in _CHINESE_FORMS.items():
        if any(form in normalized for form in forms):
            return _result(key, original, "chinese")
    candidates = _latin_candidates(folded)
    for key, forms in _FOLDED_FORMS.items():
        if any(candidate in forms for candidate in candidates):
            return _result(key, original, "latin")
    for key, forms in _FOLDED_FORMS.items():
        for form in forms:
            if len(form) < NEAR_MATCH_MIN_LENGTH or form in _EXACT_ONLY:
                continue
            if any(_within_one_edit(candidate, form) for candidate in candidates):
                return _result(key, original, "latin_near")
    return None


def match_platform_texts(texts: Iterable[object]) -> dict | None:
    """Return the first platform match among several readings of one track."""
    for text in texts:
        match = match_platform_text(text)
        if match is not None:
            return match
    return None
