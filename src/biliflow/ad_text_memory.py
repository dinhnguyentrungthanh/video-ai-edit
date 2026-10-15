"""Web addresses the reviewer blurred, remembered for later review builds (session D item 5, 2026-10-15).

BLUR on a text card that shows a web address (its ``web_address`` tokens, a
scrolling line's addresses, or an address in the card's OCR lines) stores the
address's letters in ``state/ad-text-memory.json``, folded: lower case, no
marks, no dot or space, so "EXAMPLE.COM", "Example com" and "EXAMPLECOM" are
one key. A later review build with the user's memories on (the switch of the
studio-logo memory; benchmarks build without it) suggests BLUR, in the main
list, for a track whose folded reading holds a remembered key: forms the
web-address rule does not read, such as "examplecom" in lower case or
"EXAMPLE - COM". A name without its ending ("example") is not matched.

Only a BLUR adds or refreshes a key; no other decision, rebuild or error removes
one. Only review_workflow imports this module: it is in no scan stage's cache key.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from biliflow.resource_lock import project_resource_lock
from biliflow.scrolling_text import fold_alnum, web_address_tokens

AD_TEXT_MEMORY_PATH = Path("state/ad-text-memory.json")
SCHEMA_VERSION = 1
MINIMUM_KEY_LETTERS = 6  # "abc.vn" (5) is too short to look for inside other readings
_ADDRESS_PREFIX = re.compile(r"(?i)^\s*(?:https?\s*:?\s*/*\s*)?(?:www\s*[.,]?\s*)?")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def memory_key(token: object) -> str | None:
    """The folded letters of one web address, without ``http://`` or ``www``; None when too short."""
    key = fold_alnum(_ADDRESS_PREFIX.sub("", str(token or "")))
    return key if len(key) >= MINIMUM_KEY_LETTERS else None


def card_addresses(item: dict) -> list[str]:
    """Memory keys of the web addresses a review card shows."""
    tokens: list[object] = []
    web = item.get("web_address")
    if isinstance(web, dict):
        tokens += list(web.get("tokens") or []) + list(web.get("remembered") or [])
    scrolling = item.get("scrolling_text")
    if isinstance(scrolling, dict):
        tokens += list(scrolling.get("web_address") or [])
    for text in item.get("labels") or []:
        tokens += web_address_tokens(text)
    return list(dict.fromkeys(key for key in (memory_key(token) for token in tokens) if key))


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "records": []}


def load_ad_text_memory(root: Path) -> dict:
    """The memory file, or an empty memory when there is none; ValueError when it cannot be read."""
    path = root / AD_TEXT_MEMORY_PATH
    if not path.exists():
        return _empty()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{AD_TEXT_MEMORY_PATH.as_posix()} is not readable JSON: {error}") from error
    records = payload.get("records") if isinstance(payload, dict) else None
    if (
        not isinstance(records, list)
        or not all(isinstance(record, dict) and isinstance(record.get("key"), str) for record in records)
    ):
        raise ValueError(f"{AD_TEXT_MEMORY_PATH.as_posix()} has no valid records")
    return payload


def remembered_keys(root: Path) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(record["key"]) for record in load_ad_text_memory(root)["records"]))


def remember_blurred_addresses(root: Path, item: dict) -> list[str]:
    """Store the addresses of a card just decided BLUR; the keys stored (none for other cards).

    ValueError, before anything is written, when the memory cannot be read.
    """
    if item.get("decision") != "BLUR" or item.get("category") != "text":
        return []
    keys = card_addresses(item)
    if not keys:
        return []
    with project_resource_lock(root, "ad-text-memory"):
        payload = load_ad_text_memory(root)
        records = [dict(record) for record in payload["records"]]
        by_key = {record["key"]: record for record in records}
        now = _now()
        for key in keys:
            record = by_key.get(key)
            if record is None:
                record = {"key": key, "first_remembered_at": now, "blur_decisions": 0}
                records.append(record)
                by_key[key] = record
            record["last_remembered_at"] = now
            record["blur_decisions"] = int(record.get("blur_decisions") or 0) + 1
        path = root / AD_TEXT_MEMORY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(
            {**payload, "schema_version": SCHEMA_VERSION, "updated_at": now, "records": records},
            ensure_ascii=False, indent=1,
        ) + "\n", encoding="utf-8")
        temporary.replace(path)
    return keys
