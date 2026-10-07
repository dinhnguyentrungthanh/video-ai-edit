"""A direct media file (MP4, MOV, MKV, WebM or MPEG-TS) fetched over checked HTTP, resumable with Range.

Resolve (PROBING): one GET of the first ``SNIFF_BYTES`` only, never the whole file.
- The answer must look like a video container (its first bytes), else the link goes back to yt-dlp
  (``SourceDeclined``) like any page; an HLS playlist behind the link is handed to ``download_hls``.
- Size (Content-Range or Content-Length), Range support and a validator (strong ETag or Last-Modified).
- When the header carries the stream list (an MP4 with its index first, MKV, WebM), ffprobe reads that
  local sample: a file without video or without audio fails before it is downloaded.

Download: ``media.part`` in the task folder, continued with ``Range`` + ``If-Range`` after a stop, a
restart or a network error. ``media-source.json`` ties the part to the source identity and to the
validator it was started with, so a part is only continued for the same file: a server that answers the
range with the whole file (a new version, or no range support) restarts it from byte 0. An MPEG-TS file
is copied into MP4 by the project's FFmpeg (no re-encode).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote

from biliflow.download_http import Cancelled, HttpError, Interruptible, SafeHttp
from biliflow.download_runner import SizeGuard
from biliflow.download_source_types import (
    MAX_TITLE_LENGTH,
    ResolveContext,
    ResolvedSource,
    SourceChanged,
    SourceDeclined,
    SourceError,
    stable_url,
    title_from_url,
)
from biliflow.download_transfer import (
    LogBuffer,
    ProgressMeter,
    probe_streams,
    read_json,
    remux_to_mp4,
    replace_held,
    require_audio_video,
    stream_summary,
    write_json,
)

SNIFF_BYTES = 1024 * 1024
FILE_RETRIES = 5
PART_NAME = "media.part"
STATE_NAME = "media-source.json"
SAMPLE_NAME = "sniff.bin"
CONTAINER_SUFFIX = {"mp4": ".mp4", "mov": ".mov", "matroska": ".mkv", "webm": ".webm", "mpegts": ".mp4"}
PROBE_FORMAT = {"mp4": "mov", "mov": "mov", "matroska": "matroska", "webm": "matroska", "mpegts": "mpegts"}
CONTAINER_LABEL = {"mp4": "MP4", "mov": "MOV", "matroska": "MKV", "webm": "WebM", "mpegts": "MPEG-TS"}
_HLS_TYPES = ("mpegurl",)
_MP4_BOXES = (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip", b"pnot")
_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)
_FILENAME_STAR = re.compile(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)")
_FILENAME = re.compile(r'filename\s*=\s*"?([^";]+)"?')


def validator_of(etag: str, modified: str) -> str | None:
    """A strong ETag, else Last-Modified: what If-Range may carry (never a value with a line break)."""
    value = etag if etag and not etag.startswith("W/") else (modified or None)
    return value if value and "\r" not in value and "\n" not in value else None


class PlaylistLink(Exception):
    """The link answered with an HLS playlist: ``download_hls`` takes it."""


@dataclass(frozen=True)
class FilePlan:
    container: str
    total: int | None
    ranges: bool
    validator: str | None


def container_of(sample: bytes) -> str | None:
    """The container a file starts with, from its first bytes; None for anything else (HTML, JSON…)."""
    if len(sample) >= 12 and sample[4:8] in _MP4_BOXES:
        return "mov" if sample[8:12] == b"qt  " else "mp4"
    if sample[:4] == b"\x1a\x45\xdf\xa3":
        return "webm" if b"webm" in sample[:64] else "matroska"
    if len(sample) >= 377 and sample[0] == 0x47 and sample[188] == 0x47 and sample[376] == 0x47:
        return "mpegts"
    return None


def content_range(value: str) -> tuple[int, int, int | None] | None:
    match = _CONTENT_RANGE.search(value or "")
    if not match:
        return None
    total = None if match.group(3) == "*" else int(match.group(3))
    return int(match.group(1)), int(match.group(2)), total


def filename_title(disposition: str) -> str | None:
    match = _FILENAME_STAR.search(disposition or "") or _FILENAME.search(disposition or "")
    if not match:
        return None
    name = unquote(match.group(1)).strip().replace("\\", "/").split("/")[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem[:MAX_TITLE_LENGTH] or None


def resolve_file(url: str, ctx: ResolveContext, *, provider: str, label: str,
                 headers: Mapping[str, str] | None = None) -> ResolvedSource:
    """Read the start of a media link (see the module docstring); never the whole file."""
    extra = dict(headers or {})
    with ctx.http.open(url, ctx.control, headers={**extra, "Range": f"bytes=0-{SNIFF_BYTES - 1}"}) as response:
        sample = response.read_some(SNIFF_BYTES)
        status, kind, host = response.status, response.header("Content-Type").lower(), response.host
        ranged = content_range(response.header("Content-Range"))
        length = response.content_length
        etag, modified = response.header("ETag"), response.header("Last-Modified")
        accepts = response.header("Accept-Ranges").lower() == "bytes"
        disposition = response.header("Content-Disposition")
    if sample.lstrip(b"\xef\xbb\xbf").startswith(b"#EXTM3U") or any(item in kind for item in _HLS_TYPES):
        raise PlaylistLink()
    container = container_of(sample)
    if container is None:
        shown = (kind.split(";")[0] or "không rõ")[:40]
        raise SourceDeclined(f"link không trả về file video (kiểu {shown})")
    if status == 206:
        if ranged is None or ranged[0] != 0:
            raise SourceError("BAD_RESPONSE", f"{host} trả sai đoạn byte đầu file.")
        total, ranges = ranged[2], True
    else:
        total, ranges = length, accepts
    validator = validator_of(etag, modified)
    summary: dict[str, Any] = {}
    if ctx.ffprobe is not None and container != "mpegts":  # a TS stream list may begin after the sample
        sample_path = ctx.task_dir / SAMPLE_NAME
        sample_path.write_bytes(sample)
        try:
            probe = probe_streams(ctx.ffprobe, sample_path, input_format=PROBE_FORMAT[container])
        finally:
            sample_path.unlink(missing_ok=True)
        if probe and probe.get("streams"):
            summary = stream_summary(probe)
            require_audio_video(summary, "File video")
    title = filename_title(disposition) or title_from_url(url)
    return ResolvedSource(
        provider=provider, transport="http_file", media_url=url,
        identity={"kind": "file", "url": stable_url(url), "size": total},
        title=title, label=f"{label} · {CONTAINER_LABEL[container]}", headers=extra,
        duration_seconds=summary.get("duration_seconds"), estimated_bytes=total,
        video_codec=summary.get("video_codec"), audio_codec=summary.get("audio_codec"),
        width=summary.get("width"), height=summary.get("height"),
        plan=FilePlan(container, total, ranges, validator),
    )


class FileTransfer:
    """Fetches one resolved ``http_file`` source into the task folder (see the module docstring)."""

    def __init__(self, http: SafeHttp, ffmpeg: Path, ffprobe: Path | None = None, *, retries: int = FILE_RETRIES):
        self.http = http
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.retries = retries

    def run(self, source: ResolvedSource, refresh: Callable[[], ResolvedSource], task_dir: Path,
            scope: Interruptible, meter: ProgressMeter, log: LogBuffer, guard: SizeGuard | None,
            on_start: Callable[[int, float], None] | None) -> Path:
        plan: FilePlan = source.plan
        part, state_path = task_dir / PART_NAME, task_dir / STATE_NAME
        saved = read_json(state_path)
        if saved.get("identity") != source.identity_key or not part.is_file():
            part.unlink(missing_ok=True)  # a part of another source is never continued
            saved = {"identity": source.identity_key, "validator": plan.validator}
            write_json(state_path, saved)
        total = plan.total
        offset = part.stat().st_size if part.is_file() else 0
        if total is not None and offset > total:
            part.unlink()
            offset = 0
        if offset and not saved.get("validator"):
            # No ETag or Last-Modified: a replaced file of the same size could not be told apart. Start over.
            log("Máy chủ không cho dấu phiên bản file (ETag/Last-Modified); tải lại từ đầu thay vì tải nối.")
            part.unlink()
            offset = 0
        meter.total_bytes = total
        meter.reuse(offset)
        if offset:
            log(f"Tải nối từ {offset} byte đã có.")
        failures, refreshed = 0, False
        while True:
            if total is not None and offset >= total:
                break  # every byte is there (a stop came during the remux or just before it): never ask past the end
            before = offset
            try:
                offset, total = self._receive(source, part, offset, total, saved, state_path, scope, meter, log,
                                              guard)
                if total is None or offset >= total:
                    break
                raise HttpError("NETWORK", "Máy chủ ngắt kết nối trước khi gửi hết file.", retryable=True)
            except Cancelled:
                raise
            except HttpError as error:
                offset = part.stat().st_size if part.is_file() else 0
                if offset > before:
                    failures, refreshed = 0, False  # the connection moved the file forward: count again from zero
                if error.code == "FORBIDDEN" and not refreshed:
                    refreshed = True
                    log("Máy chủ từ chối link (có thể đã hết hạn); lấy lại nguồn rồi tải tiếp.")
                    source = refresh()
                    continue
                if not error.retryable or failures >= self.retries:
                    raise
                failures += 1
                log(f"{error.message} Thử lại {failures}/{self.retries}.")
                if scope.wait(min(8.0, 2.0 ** (failures - 1))):
                    raise Cancelled() from None
        if total is not None and offset != total:
            raise SourceError("SIZE_MISMATCH", "File tải về khác dung lượng máy chủ báo.")
        return self._finish(plan, part, state_path, task_dir, scope, meter, log, on_start)

    def _receive(self, source: ResolvedSource, part: Path, offset: int, total: int | None,
                 saved: dict[str, Any], state_path: Path, scope: Interruptible, meter: ProgressMeter,
                 log: LogBuffer, guard: SizeGuard | None) -> tuple[int, int | None]:
        headers = dict(source.headers)
        if offset:
            headers["Range"] = f"bytes={offset}-"
            if saved.get("validator"):
                headers["If-Range"] = saved["validator"]
        with self.http.open(source.media_url, scope, headers=headers) as response:
            if offset and response.status == 200:
                log("Máy chủ gửi lại cả file (không tải nối được hoặc file đã đổi); tải lại từ đầu.")
                offset = 0
                part.unlink(missing_ok=True)
                meter.reset(0)
                etag, modified = response.header("ETag"), response.header("Last-Modified")
                saved["validator"] = validator_of(etag, modified)
                write_json(state_path, saved)
            elif response.status == 206:
                ranged = content_range(response.header("Content-Range"))
                if ranged is None or ranged[0] != offset:
                    raise SourceError("BAD_RESPONSE", f"{response.host} trả sai đoạn byte khi tải nối.")
                if total is not None and ranged[2] not in (None, total):
                    raise SourceChanged("dung lượng")
                total = total if total is not None else ranged[2]
            elif total is None and response.content_length is not None:
                total = response.content_length
            if total is not None and response.content_length not in (None, total - offset):
                raise SourceChanged("dung lượng")  # a shorter or longer body than the file the probe measured
            meter.total_bytes = total
            written = 0
            try:
                with part.open("ab" if offset else "wb") as handle:
                    for chunk in response.chunks():
                        if total is not None and offset + len(chunk) > total:
                            raise SourceError("SIZE_MISMATCH", "File lớn hơn dung lượng máy chủ báo.")
                        handle.write(chunk)
                        offset += len(chunk)
                        written += len(chunk)
                        meter.advance("file", len(chunk))
                        if guard is not None and guard.over(meter.snapshot()):
                            raise SourceError("TOO_LARGE", guard.message())
            finally:
                meter.commit("file", written, fragments=0)  # what reached the disk stays counted
        return offset, total

    def _finish(self, plan: FilePlan, part: Path, state_path: Path, task_dir: Path, scope: Interruptible,
                meter: ProgressMeter, log: LogBuffer, on_start: Callable[[int, float], None] | None) -> Path:
        if scope.requested:
            raise Cancelled()
        final = task_dir / ("media" + CONTAINER_SUFFIX[plan.container])
        final.unlink(missing_ok=True)
        if plan.container == "mpegts":
            probe = probe_streams(self.ffprobe, part, input_format="mpegts")  # the probe saw only its first MiB
            if probe is not None:
                require_audio_video(stream_summary(probe), "File video")
            meter.stage("remuxing")
            log("Đang chép luồng MPEG-TS sang MP4 (không mã hóa lại).")
            output = task_dir / "media.mp4.part"
            remux_to_mp4(self.ffmpeg, [part], output, scope, on_start=on_start, log=log)
            if scope.requested:
                raise Cancelled()
            replace_held(output, final, scope)
            part.unlink(missing_ok=True)
        else:
            replace_held(part, final, scope)
        state_path.unlink(missing_ok=True)
        return final
