"""The contract between a source provider, the worker and the source transfers.

A provider (``download_sources``) turns a pasted link into a ``ResolvedSource``:

- the media to fetch (``media_url``, allow-listed ``headers``): private, never stored in the database,
  shown on a page or written to a log (it may carry a signed token);
- a stable ``identity`` (which file, which variant, which segments) without any expiring token: the
  worker stores it after the probe and compares it with a fresh resolution before every download
  and every resume, so a source that changed is never continued from parts of the old one;
- the public details a page may show (title, duration, size, codecs, a label of the provider);
- the ``transport`` that fetches it (``http_file`` or ``hls``) and its private ``plan``.

``SourceDeclined`` sends the link back to yt-dlp unchanged (a form the provider does not handle);
``SourceError`` ends the task (refused, DRM, live, login, network policy). Nothing here falls back to
yt-dlp after an auth, DRM or policy error.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from biliflow.download_http import Interruptible, SafeHttp

TRANSPORTS = frozenset({"http_file", "hls"})
MAX_TITLE_LENGTH = 300
# Query names that only carry an expiring signature: left out of an identity, so a fresh link to the same
# file or segment keeps it. Matched on whole parts of a name ("auth_key", "Key-Pair-Id", "wsSecret"), never
# inside another word ("author", "keyframe"). Any other query value is kept (it may choose the content).
_VOLATILE_PARTS = frozenset({
    "token", "sig", "sign", "signature", "key", "auth", "session", "policy", "expire", "expires", "expiry", "hmac",
    "hash", "md5", "nonce", "cookie", "secret", "credential", "credentials", "pass", "password", "passwd"})
_VOLATILE_AFFIXES = ("token", "signature", "session", "expire", "secret", "credential", "password", "cookie",
                     "nonce", "hmac", "policy")
_VOLATILE_EXACT = frozenset({"e", "exp", "st", "ts", "t", "hdnts", "hdnea", "acl", "validfrom", "validto"})
_VOLATILE_PREFIXES = ("x-amz-", "x-goog-")
_NAME_SEPARATORS = re.compile(r"[-_.\s]+")
_NAME_WORDS = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
_GENERIC_NAMES = frozenset({"index", "master", "playlist", "prog_index", "video", "stream", "manifest", "media"})
CODEC_NAMES = {"avc1": "h264", "avc3": "h264", "hvc1": "hevc", "hev1": "hevc", "mp4a": "aac", "ac-3": "ac3",
               "ec-3": "eac3", "vp09": "vp9", "av01": "av1", "opus": "opus"}


class SourceError(Exception):
    """The task ends FAILED with ``code`` and a Vietnamese ``message`` (never a link)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class SourceChanged(SourceError):
    """A fresh resolution no longer matches the identity stored after the probe."""

    def __init__(self, detail: str):
        super().__init__("SOURCE_CHANGED", f"Nguồn đã đổi so với lúc thăm dò ({detail}); không tải nối vào phần cũ. "
                         "Bấm Thử lại từ đầu để tải theo nguồn mới.")


class SourceDeclined(Exception):
    """Not a form this provider handles: the link goes to yt-dlp as before."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _name_parts(name: str) -> list[str]:
    """``auth_key`` → auth, key; ``wsSecret`` → ws, secret; ``accesstoken`` stays one part."""
    parts: list[str] = []
    for chunk in _NAME_SEPARATORS.split(name):
        parts += [word.lower() for word in _NAME_WORDS.findall(chunk)] or ([chunk.lower()] if chunk else [])
    return parts


def is_volatile_query_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in _VOLATILE_EXACT or lowered.startswith(_VOLATILE_PREFIXES):
        return True
    return any(part in _VOLATILE_PARTS or part.startswith(_VOLATILE_AFFIXES) or part.endswith(_VOLATILE_AFFIXES)
               for part in _name_parts(name))


def stable_url(url: str) -> str:
    """``url`` without its fragment and without signature-like query values: what names the content."""
    parts = urlsplit(url)
    query = [(name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True)
             if not is_volatile_query_name(name)]
    return urlunsplit((parts.scheme.lower(), (parts.hostname or "").lower(), parts.path, urlencode(sorted(query)), ""))


def identity_key(identity: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def describe_change(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> str:
    """Which identity fields differ, for the SOURCE_CHANGED message (never a link)."""
    names = {"kind": "loại nguồn", "count": "số đoạn", "duration": "thời lượng", "segments": "danh sách đoạn",
             "variant": "chất lượng đã chọn", "size": "dung lượng", "etag": "phiên bản file",
             "last_modified": "ngày sửa file", "url": "đường dẫn file", "item": "mục đã chọn"}
    if not old:
        return "thiếu thông tin lúc thăm dò"
    changed = [names.get(key, key) for key in sorted(set(old) | set(new)) if old.get(key) != new.get(key)]
    return ", ".join(changed) or "khác dấu nhận diện"


def title_from_url(url: str, fallback: str = "video") -> str:
    """A readable title from a media link: its file name, or the folder above a generic playlist name."""
    segments = [unquote(part) for part in urlsplit(url).path.split("/") if part]
    for part in reversed(segments):
        stem = part.rsplit(".", 1)[0] if "." in part else part
        if stem and stem.lower() not in _GENERIC_NAMES:
            return stem[:MAX_TITLE_LENGTH]
    return fallback


def codec_names(codecs: str | None) -> tuple[str | None, str | None]:
    """(video, audio) names from an HLS CODECS attribute such as "avc1.640028,mp4a.40.2"."""
    video = audio = None
    for item in (codecs or "").split(","):
        name = CODEC_NAMES.get(item.strip().split(".")[0].lower())
        if name in ("h264", "hevc", "vp9", "av1") and video is None:
            video = name
        elif name in ("aac", "ac3", "eac3", "opus") and audio is None:
            audio = name
    return video, audio


@dataclass(frozen=True)
class ResolvedSource:
    provider: str
    transport: str
    media_url: str = field(repr=False)
    identity: Mapping[str, Any]
    title: str
    label: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    duration_seconds: float | None = None
    estimated_bytes: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    fragments: int | None = None
    plan: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.transport not in TRANSPORTS:
            raise ValueError(f"Unknown transport {self.transport!r}")

    @property
    def identity_key(self) -> str:
        return identity_key(self.identity)

    def public(self) -> dict[str, Any]:
        """What the database and the pages may keep: no media link, no header, no token."""
        return {"provider": self.provider, "transport": self.transport, "source_label": self.label,
                "identity": self.identity_key, "identity_detail": dict(self.identity),
                "fragments_total": self.fragments}


@dataclass(frozen=True)
class ResolveContext:
    """What a provider may use: the checked HTTP client, the task's control, its own temp folder,
    ffprobe for a header check (None on a refresh) and the identity stored after the probe."""
    http: SafeHttp
    control: Interruptible
    task_dir: Path
    ffprobe: Path | None = None
    previous: Mapping[str, Any] | None = None
    log: Callable[[str], None] = field(default=lambda line: None)
