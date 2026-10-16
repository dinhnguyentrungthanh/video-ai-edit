"""Web addresses in OCR text, also when OCR lost the dot or added a space: the patterns alone.

Moved unchanged from ``scrolling_text`` (item 6, 2026-10-16) so that the text scan can keep every
track with a web address past its track limit without importing the review-build logic. The text
scan imports it, so it is in the text stage's cache key: an edit here makes "Chạy lại" scan the
text again. Review-build modules use it through ``scrolling_text``. Standard library only.
"""
from __future__ import annotations

import re
import unicodedata

# --- Web addresses --------------------------------------------------------------

_DOTTED_TLDS = (
    "com|net|org|vn|tv|me|io|xyz|cc|co|info|top|club|link|live|online|site|biz|asia|pro|"
    "app|fun|vip|bet|win|us|to|ly|gg"
)
# A domain whose dot OCR lost is only trusted with its name in capitals and for
# these endings: "nấu cơm", "xem tv", "SMART TV" or "Saigon, VN" stay ordinary text.
_DOTLESS_TLDS = "COM|NET|ORG|VN|TV"
_WEB_ADDRESS_PATTERNS = (
    re.compile(r"(?i)https?://|(?<![\w.@-])www(?![\w])"),
    # name.tld and sub.name.tld, the ending in any case (OCR reads "cOM", "aPP")
    re.compile(
        rf"(?<![\w.@-])(?:[A-Za-z0-9][A-Za-z0-9-]{{0,62}}\.)*[A-Za-z0-9][A-Za-z0-9-]{{1,62}}"
        rf"\.(?i:{_DOTTED_TLDS})(?![\w])"
    ),
    # NAME. COM, NAME .COM: the dot read with a space next to it
    re.compile(rf"(?<![\w.@-])[A-Z0-9][A-Z0-9-]{{2,62}}(?:\s+\.\s*|\.\s+)(?:{_DOTLESS_TLDS})(?![\w])"),
    # NAME,COM, NAME ,COM: the dot read as a comma ("SAIGON, VN" is a place)
    re.compile(r"(?<![\w.@-])[A-Z0-9][A-Z0-9-]{2,62}\s*,\s*(?:COM|NET|ORG)(?![\w])"),
    # NAMECOM: the dot lost
    re.compile(rf"(?<![\w.@-])[A-Z0-9][A-Z0-9-]{{2,62}}(?:{_DOTLESS_TLDS})(?![\w])"),
    # NAME COM, NAME NET: the dot read as a space ("THE NET", "LIVE TV" are words)
    re.compile(r"(?<![\w.@-])(?:[A-Z0-9][A-Z0-9-]{2,62}\sCOM|[A-Z0-9][A-Z0-9-]{3,62}\s(?:NET|ORG))(?![\w])"),
)
# Ordinary words that end like a dot-less domain.
_DOTLESS_WORDS = frozenset({
    "SITCOM", "TELECOM", "INTERCOM", "DOTCOM", "INTERNET", "CABINET", "PLANET", "MAGNET",
    "GARNET", "SONNET", "BONNET", "HORNET", "CORNET", "SIGNET", "BAYONET", "CLARINET",
    "DRAGNET", "CYBORG",
})
# Endings that are also words: "Wait.Me", "Stop.To" are two sentences with the space lost.
_WORD_TLDS = frozenset({
    "me", "to", "us", "pro", "top", "fun", "win", "live", "link", "club", "site", "app", "bet",
    "vip", "co", "cc", "io", "ly", "gg", "info", "online",
})


def _sentence_break(token: str) -> bool:
    ending = token.rsplit(".", 1)[-1] if "." in token else ""
    return ending.lower() in _WORD_TLDS and ending[:1].isupper() and ending[1:].islower()


def web_address_tokens(text: object) -> list[str]:
    """Web addresses in one OCR reading, also when OCR lost the dot or added a space.

    A general rule for every site: ``name.tld`` and ``sub.name.tld``, ``www``/``http``,
    and with the name in capitals ``NAME. COM``, ``NAME,COM``, ``NAMECOM`` (COM, NET,
    ORG, VN, TV), ``NAME COM`` and ``NAME NET``.
    """
    found = []
    for pattern in _WEB_ADDRESS_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            token = match.group(0).strip()
            if re.sub(r"[^A-Z]", "", token.upper()) in _DOTLESS_WORDS or _sentence_break(token):
                continue
            found.append(token)
    return list(dict.fromkeys(found))


def fold_alnum(value: object) -> str:
    """Lower-case letters and digits without Vietnamese marks ("Đ" → "d")."""
    text = str(value).casefold().replace("đ", "d")
    text = "".join(
        character for character in unicodedata.normalize("NFD", text)
        if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"[^0-9a-z]+", "", text)
