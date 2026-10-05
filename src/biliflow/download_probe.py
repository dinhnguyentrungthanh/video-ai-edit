"""Read a yt-dlp probe (``--dump-single-json``) and pick what to download.

The probe is how BiliFlow learns whether a page can be downloaded at all: any
public page may be added, and a page without a video yt-dlp can read ends as
UNSUPPORTED ("Trang này chưa được hỗ trợ"). There is no site-specific code.

Two reading rules, chosen by the extractor yt-dlp used:
- a site with its own yt-dlp reader (YouTube, Bilibili, ...): only the video in
  the link, any length; a link that yields several videos (a playlist, a
  channel) is refused;
- a page read by yt-dlp's generic reader: several videos may come back (ads,
  trailers, the film); entries shorter than ``GENERIC_MIN_DURATION_SECONDS``
  are dropped, the longest wins when it is at least twice the next one, or the
  user chooses. A page that only exposes short videos is reported, never worked
  around.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

LIVE_CODES = {"is_live": "LIVE", "is_upcoming": "UPCOMING", "post_live": "POST_LIVE"}
LIVE_MESSAGES = {
    "LIVE": "Video đang phát trực tiếp; không tải.",
    "UPCOMING": "Buổi phát chưa bắt đầu; không tải.",
    "POST_LIVE": "Buổi phát vừa kết thúc, trang chưa xử lý xong bản lưu; thử lại sau.",
}
DRM_MESSAGE = "Video có DRM; không hỗ trợ."
UNSUPPORTED_MESSAGE = "Trang này chưa được hỗ trợ: yt-dlp không tìm thấy video nào đọc được trong trang."
NO_VIDEOS_MESSAGE = "Link không có video nào (danh sách phát hoặc kênh trống)."
LONGEST_RATIO = 2.0
MAX_TITLE_LENGTH = 300
# yt-dlp's error lines echo links, redirect targets and socket addresses; a page could use them to map
# the local network through redirects, so they never reach the message (nor decide the code).
_LINK = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_ADDRESS = re.compile(r"\[[0-9a-f:.%]*:[0-9a-f:.%]*\](?::\d+)?|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", re.IGNORECASE)
GENERIC_EXTRACTOR = "generic"
GENERIC_MIN_DURATION_SECONDS = 600.0  # ads and trailers on pages without a reader of their own
_ERROR_RULES: tuple[tuple[str, str, str], ...] = (
    # First: "Unsupported URL" is unambiguous, and the other rules must not read words of the link.
    ("UNSUPPORTED", r"unsupported url", UNSUPPORTED_MESSAGE),
    ("DRM", r"\bDRM\b", DRM_MESSAGE),
    ("AGE_RESTRICTED", r"confirm your age|age[- ]restrict|inappropriate for some users",
     "Video giới hạn tuổi, cần đăng nhập; không dùng tài khoản hay cookie nên không tải."),
    ("BOT_CHECK", r"not a bot",
     "Trang đòi đăng nhập để xác minh không phải bot; không dùng cookie nên không tải."),
    ("LOGIN_REQUIRED",
     r"private video|video is private|members[- ]only|join this channel|log ?in|sign ?in|"
     r"account|cookies|premium|subscri",
     "Video cần đăng nhập hoặc tài khoản trả phí; không hỗ trợ."),
    ("UPCOMING", r"live event will begin|premieres in", LIVE_MESSAGES["UPCOMING"]),
    ("GEO_BLOCKED", r"in your country|geo[- ]?restrict|not available in your (region|location)",
     "Video bị chặn theo khu vực."),
    ("UNAVAILABLE", r"video unavailable|http error 404|has been removed|does not exist|no longer available",
     "Video không còn hoặc link sai."),
    ("NO_FORMAT", r"requested format is not available", "Không có định dạng tải được."),
    ("TOO_LARGE", r"larger than max-filesize|max-filesize", "File lớn hơn chỗ trống cho phép."),
    ("DISK_FULL", r"no space left|errno 28|not enough space", "Ổ đĩa hết chỗ."),
    ("NETWORK", r"timed out|connection (reset|refused|aborted)|getaddrinfo|unable to download|"
     r"temporary failure|network is unreachable|ssl", "Lỗi mạng khi đọc trang nguồn."),
)


@dataclass(frozen=True)
class ProbeEntry:
    index: int
    title: str
    duration_seconds: float | None
    estimated_bytes: int | None
    video_id: str | None
    live_status: str | None
    drm: bool
    expected_files: int
    video_codec: str | None = None
    audio_codec: str | None = None
    height: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ProbeChoice:
    kind: str
    entry: ProbeEntry | None = None
    entries: tuple[ProbeEntry, ...] = field(default_factory=tuple)
    code: str | None = None
    message: str | None = None


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 else None


def _format_bytes(item: dict[str, Any], duration: float | None) -> int | None:
    for key in ("filesize", "filesize_approx"):
        size = _number(item.get(key))
        if size:
            return int(size)
    bitrate = _number(item.get("tbr"))
    if bitrate and duration:
        return int(bitrate * 125 * duration)  # kbit/s -> bytes
    return None


def _entry(info: dict[str, Any], index: int) -> ProbeEntry:
    duration = _number(info.get("duration"))
    formats = [item for item in info.get("requested_formats") or [] if isinstance(item, dict)]
    if formats:
        sizes = [_format_bytes(item, duration) for item in formats]
        estimated = sum(sizes) if all(size is not None for size in sizes) else None
    else:
        estimated = _format_bytes(info, duration)
    drm = bool(info.get("_has_drm") or info.get("has_drm")
               or any(item.get("has_drm") for item in formats))
    live = info.get("live_status")
    if live is None and info.get("is_live"):
        live = "is_live"
    video = next((item for item in formats if item.get("vcodec") not in (None, "none")), info)
    audio = next((item for item in formats if item.get("acodec") not in (None, "none")), info)
    height = _number(video.get("height"))
    return ProbeEntry(
        index=index,
        title=str(info.get("title") or info.get("id") or "video")[:MAX_TITLE_LENGTH],
        duration_seconds=duration,
        estimated_bytes=estimated,
        video_id=str(info["id"]) if info.get("id") is not None else None,
        live_status=live,
        drm=drm,
        expected_files=max(1, len(formats)),
        video_codec=video.get("vcodec") if video.get("vcodec") != "none" else None,
        audio_codec=audio.get("acodec") if audio.get("acodec") != "none" else None,
        height=int(height) if height else None,
    )


def entries_from_info(info: dict[str, Any]) -> list[ProbeEntry]:
    """Entries of a probe; a single video has index 0 (no ``--playlist-items``)."""
    if info.get("_type") not in ("playlist", "multi_video"):
        return [_entry(info, 0)]
    entries = []
    for position, item in enumerate(info.get("entries") or [], start=1):
        if isinstance(item, dict):
            entries.append(_entry(item, int(item.get("playlist_index") or position)))
    return entries


def _refusal(entry: ProbeEntry) -> tuple[str, str] | None:
    code = LIVE_CODES.get(entry.live_status or "")
    if code:
        return code, LIVE_MESSAGES[code]
    if entry.drm:
        return "DRM", DRM_MESSAGE
    return None


def is_generic(info: dict[str, Any]) -> bool:
    """True when yt-dlp read the page with its generic reader (no reader for this site)."""
    name = info.get("extractor_key") or info.get("extractor") or ""
    return str(name).strip().lower() == GENERIC_EXTRACTOR


def choose(info: dict[str, Any]) -> ProbeChoice:
    """READY with the entry to download, NEEDS_CHOICE with candidates, or FAILED."""
    generic = is_generic(info)
    min_duration_seconds = GENERIC_MIN_DURATION_SECONDS if generic else 0.0
    entries = entries_from_info(info)
    if not entries:
        if not generic:
            return ProbeChoice("FAILED", code="NO_VIDEOS", message=NO_VIDEOS_MESSAGE)
        return ProbeChoice("FAILED", code="NO_ENTRIES", message=UNSUPPORTED_MESSAGE)
    if len(entries) > 1 and not generic:
        return ProbeChoice("FAILED", entries=tuple(entries), code="MULTIPLE_ENTRIES",
                           message="Link có nhiều video (danh sách phát hoặc kênh); chỉ tải link của một video.")
    candidates = [entry for entry in entries if _refusal(entry) is None]
    if not candidates:
        code, message = _refusal(entries[0])
        return ProbeChoice("FAILED", entries=tuple(entries), code=code, message=message)
    long_enough = [entry for entry in candidates
                   if entry.duration_seconds is None or entry.duration_seconds >= min_duration_seconds]
    if not long_enough:
        lengths = ", ".join(format_duration(entry.duration_seconds) for entry in candidates)
        return ProbeChoice(
            "FAILED", entries=tuple(entries), code="ONLY_SHORT_ENTRIES",
            message=(f"Không tìm thấy phim, chỉ thấy {len(candidates)} video ngắn ({lengths}), "
                     "có thể là quảng cáo. Trang không có bộ đọc riêng nên chỉ tải video từ "
                     f"{int(min_duration_seconds // 60)} phút trở lên."),
        )
    if len(long_enough) == 1:
        return ProbeChoice("READY", entry=long_enough[0], entries=tuple(entries))
    if all(entry.duration_seconds is not None for entry in long_enough):
        ranked = sorted(long_enough, key=lambda entry: entry.duration_seconds, reverse=True)
        if ranked[0].duration_seconds >= LONGEST_RATIO * ranked[1].duration_seconds:
            return ProbeChoice("READY", entry=ranked[0], entries=tuple(entries))
    return ProbeChoice("NEEDS_CHOICE", entries=tuple(long_enough),
                       message=f"Trang có {len(long_enough)} video dài; chọn video cần tải.")


def classify_error(text: str, *, stage: str) -> tuple[str, str]:
    """Map yt-dlp's error output to a code and a Vietnamese message with the last ERROR line."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("ERROR")] or lines[-1:]
    detail = _ADDRESS.sub("<địa chỉ>", _LINK.sub("<link>", errors[-1]))[:300] if errors else ""
    for code, pattern, message in _ERROR_RULES:
        if detail and re.search(pattern, detail, re.IGNORECASE):
            return code, f"{message} ({detail})"
    fallback = "PROBE_FAILED" if stage == "probe" else "DOWNLOAD_FAILED"
    base = "Thăm dò link không thành công." if stage == "probe" else "Tải không thành công."
    return fallback, f"{base} ({detail})" if detail else base
