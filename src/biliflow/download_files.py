"""File helpers of the video downloader: names, bounded deletes and checks.

Deletes are confined to one base folder (``temp\\downloads``) and never follow
a junction or symlink. Verification only reads the downloaded file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_NAME_LENGTH = 150
OUTPUT_SUFFIX = ".mp4"
# The same list as job_import.VIDEO_EXTENSIONS; the watcher ignores anything else.
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm"})
PROBE_TIMEOUT_SECONDS = 120
DECODE_TIMEOUT_SECONDS = 300
DECODE_WINDOW_SECONDS = 5
_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])", re.IGNORECASE)
_TYPED_SUFFIX = re.compile(r"\.mp4$", re.IGNORECASE)
_HASH_CHUNK = 1024 * 1024
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UnsafePathError(RuntimeError):
    """A delete was asked for a path outside the allowed base folder."""


def sanitize_name(name: str, *, fallback: str | None = "video") -> str:
    """A Windows-safe file stem (without ``.mp4``) of at most 150 characters with it.

    With ``fallback=None`` an unusable name gives an empty string instead.
    """
    limit = MAX_NAME_LENGTH - len(OUTPUT_SUFFIX)
    candidates = (name,) if fallback is None else (name, fallback, "video")
    for candidate in candidates:
        text = unicodedata.normalize("NFC", str(candidate or ""))
        text = _FORBIDDEN.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip().rstrip(". ")
        text = _TYPED_SUFFIX.sub("", text).rstrip(". ")
        text = text[:limit].rstrip(". ")
        if not text:
            continue
        head, dot, rest = text.partition(".")
        if _RESERVED.fullmatch(head.strip()):
            # Windows reserves the device name before the first dot (NUL.txt too).
            text = f"{head.rstrip()}_{dot}{rest}"[:limit]
        return text
    return "" if fallback is None else "video"


def unique_target(directory: Path, stem: str, suffix: str = OUTPUT_SUFFIX) -> Path:
    """First free ``stem.mp4``, ``stem (2).mp4``… (case-insensitive, as NTFS)."""
    taken = {entry.name.casefold() for entry in directory.iterdir()} if directory.is_dir() else set()
    for number in range(1, 10000):
        marker = "" if number == 1 else f" ({number})"
        base = stem[:MAX_NAME_LENGTH - len(suffix) - len(marker)].rstrip(". ")
        name = f"{base}{marker}{suffix}"
        if name.casefold() not in taken:
            return directory / name
    raise RuntimeError(f"Không còn tên trống cho {stem!r} trong {directory}.")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def tree_size(path: Path) -> int:
    """Bytes under ``path`` without following links."""
    if not os.path.lexists(path) or _is_link(path):
        return 0
    # FileNotFoundError: a part file or folder yt-dlp removed meanwhile counts as 0.
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        with os.scandir(path) as entries:
            for entry in entries:
                child = Path(entry.path)
                if _is_link(child):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    total += tree_size(child)
                    continue
                try:
                    total += entry.stat(follow_symlinks=False).st_size
                except FileNotFoundError:
                    continue
        return total
    except FileNotFoundError:
        return 0


def _unlink_link(path: Path) -> None:
    try:
        os.rmdir(path)
    except NotADirectoryError:
        os.unlink(path)
    except OSError:
        os.unlink(path)


def _remove(path: Path) -> None:
    if _is_link(path):
        _unlink_link(path)
        return
    if path.is_dir():
        with os.scandir(path) as entries:
            children = [Path(entry.path) for entry in entries]
        for child in children:
            _remove(child)
        os.rmdir(path)
        return
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)


def safe_remove_tree(base: Path, target: Path) -> int:
    """Delete ``target`` (a folder or file strictly inside ``base``); return the bytes freed.

    A link or junction is removed as a link: its target is never touched.
    """
    base_resolved = base.resolve(strict=True)
    if not os.path.lexists(target):
        return 0
    parent = target.parent.resolve(strict=True)
    if parent != base_resolved and base_resolved not in parent.parents:
        raise UnsafePathError(f"Không xóa ngoài {base_resolved}: {target}")
    if _is_link(target):
        _unlink_link(target)
        return 0
    resolved = target.resolve(strict=True)
    if resolved == base_resolved or base_resolved not in resolved.parents:
        raise UnsafePathError(f"Không xóa ngoài {base_resolved}: {target}")
    size = tree_size(resolved)
    _remove(resolved)
    return size


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    code: str
    message: str
    duration_seconds: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, check=False,
                          creationflags=_CREATE_NO_WINDOW)


def _probe(ffprobe: Path, path: Path) -> dict[str, Any] | None:
    try:
        completed = _run([str(ffprobe), "-v", "error", "-show_format", "-show_streams",
                          "-of", "json", str(path)], PROBE_TIMEOUT_SECONDS)
        if completed.returncode != 0:
            return None
        return json.loads(completed.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _decode_errors(ffmpeg: Path, path: Path, window: list[str]) -> str:
    """ffmpeg exits 0 on many corrupt frames, so any error line counts."""
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-v", "error", *window, "-i", str(path),
               "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"]
    try:
        completed = _run(command, DECODE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        return str(error)
    text = completed.stderr.strip()
    if completed.returncode != 0 and not text:
        text = f"ffmpeg exit {completed.returncode}"
    return text


def verify_video(ffprobe: Path, ffmpeg: Path, path: Path, *,
                 expected_duration: float | None = None) -> VerifyResult:
    """At least one video and one audio stream, the probed length, a clean head and tail."""
    if path.suffix.casefold() not in VIDEO_EXTENSIONS:
        return VerifyResult(False, "BAD_EXTENSION", f"Đuôi {path.suffix or '(trống)'} không phải video.")
    probe = _probe(ffprobe, path)
    if probe is None:
        return VerifyResult(False, "PROBE_FAILED", "ffprobe không đọc được file đã tải.")
    streams = probe.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"
                  and not item.get("disposition", {}).get("attached_pic")), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    try:
        duration = float(probe.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    details = {
        "duration_seconds": duration,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "width": int(video["width"]) if video and video.get("width") else None,
        "height": int(video["height"]) if video and video.get("height") else None,
    }
    if video is None:
        return VerifyResult(False, "NO_VIDEO_STREAM", "File không có luồng hình.", **details)
    if audio is None:
        return VerifyResult(False, "NO_AUDIO_STREAM", "File không có luồng tiếng.", **details)
    if expected_duration and duration is not None:
        allowed = max(2.0, expected_duration * 0.01)
        if abs(duration - expected_duration) > allowed:
            return VerifyResult(False, "DURATION_MISMATCH",
                                f"Thời lượng {duration:.1f} s lệch quá {allowed:.1f} s so với "
                                f"lúc thăm dò ({expected_duration:.1f} s).", **details)
    for label, window in (("đầu", ["-t", str(DECODE_WINDOW_SECONDS)]),
                          ("cuối", ["-sseof", f"-{DECODE_WINDOW_SECONDS}"])):
        errors = _decode_errors(ffmpeg, path, window)
        if errors:
            first = errors.splitlines()[0][:300]
            return VerifyResult(False, "DECODE_ERROR",
                                f"Giải mã {DECODE_WINDOW_SECONDS} giây {label} bị lỗi: {first}", **details)
    return VerifyResult(True, "OK", "File tải về đạt kiểm tra.", **details)
