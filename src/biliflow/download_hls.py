"""HLS from a pasted ``.m3u8`` link: playlist checks, variant choice, parallel segments, ordered remux.

Handled: a finished (VOD, ``EXT-X-ENDLIST``) media playlist of MPEG-TS segments that carry both video
and audio, directly or behind a master playlist. A master variant is chosen like the format sort of the
other links: the largest at most 1080p, then H.264 + AAC, then the highest bandwidth.

Refused (FAILED, never handed to another downloader): a live or unfinished stream, SAMPLE-AES or a key
format other than "identity" (DRM).

Declined (the link goes to yt-dlp exactly as before this provider existed): AES-128, fMP4/CMAF
(``EXT-X-MAP``), byte ranges, discontinuities, gaps, low-latency parts, separate audio renditions.

PROBING reads the playlists and the first segment (at most ``MAX_PROBE_SEGMENT_BYTES``): ffprobe must
find video and audio in it before anything else is downloaded; it also gives the codecs, the frame size
and, without a master playlist, the size estimate (first segment × duration).

Segments: up to ``SEGMENT_WORKERS`` at once, each written to ``seg/NNNNNN.ts.part`` and renamed when it
is complete and MPEG-TS (a sync byte every 188 bytes). ``seg/manifest.json`` keeps the size and SHA-256
of every finished segment with the source identity: a resume only trusts segments it can check, never
a file name alone, and never segments of another source. Expired segment links (403/404/410) make the
transfer resolve the playlist again (same identity; at most ``MAX_REFRESHES`` times in a row without a
new segment, so links that expire every few minutes on a long film keep working). The MP4 is made
from the segments in playlist order, never in the order they finished, by FFmpeg ``-c copy``; the
segments are deleted afterwards (peak on disk: the segments plus the MP4).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import urljoin, urlsplit

from biliflow.download_files import UnsafePathError, safe_remove_tree, sha256_file
from biliflow.download_http import Cancelled, HttpError, Interruptible, SafeHttp, Scope, with_retries
from biliflow.download_runner import SizeGuard
from biliflow.download_png_ts import ts_payload
from biliflow.download_source_types import (
    ResolveContext,
    ResolvedSource,
    SourceChanged,
    SourceDeclined,
    SourceError,
    codec_names,
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

SEGMENT_WORKERS = 4
SEGMENT_RETRIES = 3
MAX_REFRESHES = 2
MAX_PLAYLIST_BYTES = 8 * 1024 * 1024
MAX_SEGMENT_BYTES = 256 * 1024 * 1024
MAX_PROBE_SEGMENT_BYTES = 64 * 1024 * 1024  # the first segment, read at PROBING
PROBE_SEGMENT_NAME = "probe-segment.ts"
MAX_SEGMENTS = 50_000
MAX_SEGMENT_SECONDS = 600.0
MAX_HEIGHT = 1080
TS_PACKET = 188
SEG_DIR = "seg"
MANIFEST_NAME = "manifest.json"
MANIFEST_SAVE_SECONDS = 2.0
MAX_LINE_CHARS = 16 * 1024  # a derived link may be 8192 characters; a longer line is not a playlist
MAX_VARIANTS = 500
REFRESH_CODES = frozenset({"FORBIDDEN", "UNAVAILABLE"})
# Anchored at the start or a comma: linear on any line (an unanchored pattern is quadratic on a long
# line without "=" and holds the GIL meanwhile).
_ATTRIBUTE = re.compile(r'(?:^|,)\s*([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_SEGMENT_FILE = re.compile(r"^\d{6}\.ts(?:\.part)?$")
_DECLINED_TAGS = {
    "#EXT-X-MAP": "fMP4/CMAF (EXT-X-MAP)", "#EXT-X-BYTERANGE": "byte range", "#EXT-X-DISCONTINUITY": "đoạn nối",
    "#EXT-X-GAP": "đoạn trống", "#EXT-X-PART": "đoạn độ trễ thấp", "#EXT-X-PRELOAD-HINT": "đoạn độ trễ thấp",
}


@dataclass(frozen=True)
class Variant:
    uri: str
    bandwidth: int
    width: int | None
    height: int | None
    codecs: str | None
    audio: str | None


@dataclass(frozen=True)
class Segment:
    index: int
    uri: str
    duration: float


@dataclass(frozen=True)
class MasterPlaylist:
    url: str
    variants: tuple[Variant, ...]
    audio_groups_with_uri: frozenset[str]
    declined: str | None = None  # a session key BiliFlow leaves to yt-dlp, once the variant shows no DRM


@dataclass(frozen=True)
class MediaPlaylist:
    url: str
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class HlsPlan:
    segments: tuple[Segment, ...]
    strip_png: bool = False


def _bad(message: str) -> SourceError:
    return SourceError("BAD_PLAYLIST", message)


def _attributes(line: str) -> dict[str, str]:
    body = line.split(":", 1)[1] if ":" in line else ""
    return {match.group(1): match.group(2).strip('"') for match in _ATTRIBUTE.finditer(body)}


def _absolute(uri: str, base: str) -> str:
    try:
        link = urljoin(base, uri.strip())
        scheme = urlsplit(link).scheme.lower()
    except ValueError:
        raise _bad("Playlist có link hỏng; không tải.") from None
    if scheme not in ("http", "https"):
        raise _bad("Playlist có link không phải http(s); không tải.")
    if scheme == "http" and urlsplit(base).scheme.lower() == "https":
        raise SourceError("DOWNGRADE", "Playlist https trỏ tới link http (không mã hóa); không tải.")
    return link


def _key_decline(line: str) -> str | None:
    """None for no encryption, the reason to leave AES-128 to yt-dlp, SourceError DRM for anything else."""
    attributes = _attributes(line)
    method = attributes.get("METHOD", "NONE").upper()
    if method == "NONE":
        return None
    keyformat = attributes.get("KEYFORMAT", "identity").lower()
    if method == "AES-128" and keyformat == "identity":
        return "luồng HLS mã hóa AES-128"
    raise SourceError("DRM", "Luồng HLS có DRM (khóa giải mã riêng); không hỗ trợ.")


def _duration(value: str) -> float:
    try:
        seconds = float(value.split(",", 1)[0])
    except ValueError:
        raise _bad("Thời lượng một đoạn HLS không đọc được.") from None
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_SEGMENT_SECONDS:
        raise _bad("Thời lượng một đoạn HLS không hợp lệ.")
    return seconds


def _resolution(value: str | None) -> tuple[int | None, int | None]:
    match = re.fullmatch(r"(\d{1,5})x(\d{1,5})", value or "")
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _variant(attributes: Mapping[str, str], uri: str, base: str) -> Variant:
    try:
        bandwidth = int(attributes.get("BANDWIDTH", ""))
    except ValueError:
        raise _bad("Biến thể HLS thiếu BANDWIDTH.") from None
    if bandwidth <= 0:
        raise _bad("Biến thể HLS không hợp lệ.")
    width, height = _resolution(attributes.get("RESOLUTION"))
    return Variant(_absolute(uri, base), bandwidth, width, height, attributes.get("CODECS"), attributes.get("AUDIO"))


def _parse_master(lines: Sequence[str], url: str) -> MasterPlaylist:
    """One pass: a variant's link is the first line after its EXT-X-STREAM-INF that is not a tag."""
    variants: list[Variant] = []
    with_uri: set[str] = set()
    declined: str | None = None
    pending: dict[str, str] | None = None
    for line in lines:
        name = line.split(":", 1)[0]
        if not line.startswith("#"):
            if pending is not None:
                variants.append(_variant(pending, line, url))
                pending = None
                if len(variants) > MAX_VARIANTS:
                    raise _bad(f"Playlist có hơn {MAX_VARIANTS} biến thể; không tải.")
        elif name == "#EXT-X-SESSION-KEY":
            declined = _key_decline(line) or declined  # DRM raises whatever came before
        elif name == "#EXT-X-MEDIA":
            attributes = _attributes(line)
            if attributes.get("TYPE", "").upper() == "AUDIO" and attributes.get("URI"):
                with_uri.add(attributes.get("GROUP-ID", ""))
        elif name == "#EXT-X-STREAM-INF":
            if pending is not None:
                raise _bad("Biến thể HLS không hợp lệ.")
            pending = _attributes(line)
    if pending is not None:
        raise _bad("Biến thể HLS không hợp lệ.")
    if not variants:
        raise _bad("Playlist HLS không có biến thể nào.")
    return MasterPlaylist(url, tuple(variants), frozenset(with_uri), declined)


def _parse_media(lines: Sequence[str], url: str) -> MediaPlaylist:
    """The whole playlist is read before a form is declined: a DRM key or a live stream after a declined
    tag is still refused here, never handed to yt-dlp."""
    segments: list[Segment] = []
    duration: float | None = None
    ended = False
    declined: str | None = None
    for line in lines[1:]:
        if line.startswith("#"):
            name, _, value = line.partition(":")
            if name in _DECLINED_TAGS:
                declined = declined or f"luồng HLS dùng {_DECLINED_TAGS[name]}"
            elif name == "#EXT-X-KEY":
                declined = _key_decline(line) or declined
            elif name == "#EXT-X-ENDLIST":
                ended = True
            elif name == "#EXTINF":
                duration = _duration(value)
            continue
        if duration is None:
            raise _bad("Đoạn HLS thiếu EXTINF.")
        segments.append(Segment(len(segments) + 1, _absolute(line, url), duration))
        duration = None
        if len(segments) > MAX_SEGMENTS:
            raise _bad(f"Playlist có hơn {MAX_SEGMENTS} đoạn; không tải.")
    if not ended:
        raise SourceError("LIVE", "Luồng HLS đang phát trực tiếp hoặc chưa kết thúc (thiếu EXT-X-ENDLIST); không tải.")
    if declined:
        raise SourceDeclined(declined)
    if not segments:
        raise _bad("Playlist HLS không có đoạn nào.")
    return MediaPlaylist(url, tuple(segments))


def parse_playlist(text: str, url: str) -> MasterPlaylist | MediaPlaylist:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0].lstrip("\ufeff") != "#EXTM3U":
        raise _bad("Link không trả về playlist HLS hợp lệ.")
    if any(len(line) > MAX_LINE_CHARS for line in lines):
        raise _bad(f"Playlist có dòng dài quá {MAX_LINE_CHARS} ký tự; không tải.")
    if any(line.startswith("#EXT-X-STREAM-INF") for line in lines):
        return _parse_master(lines, url)
    return _parse_media(lines, url)


def variant_key(variant: Variant) -> dict[str, Any]:
    resolution = f"{variant.width}x{variant.height}" if variant.height else None
    return {"url": stable_url(variant.uri), "bandwidth": variant.bandwidth, "resolution": resolution}


def _codec_rank(codecs: str | None) -> int:
    text = (codecs or "").lower()
    has_h264 = "avc1" in text or "avc3" in text
    if has_h264 and "mp4a" in text:
        return 0
    return 1 if has_h264 or not text else 2


def select_variant(master: MasterPlaylist, previous: Mapping[str, Any] | None = None) -> Variant:
    """The variant chosen before (a refresh never changes it), else the policy of the module docstring."""
    if previous:
        for variant in master.variants:
            if variant_key(variant) == dict(previous):
                return variant
        raise SourceChanged("chất lượng đã chọn")
    known = [variant for variant in master.variants if variant.height]
    if known:
        below = [variant for variant in known if variant.height <= MAX_HEIGHT]
        height = max(v.height for v in below) if below else min(v.height for v in known)
        pool = [variant for variant in known if variant.height == height]
    else:
        pool = list(master.variants)
    return min(pool, key=lambda variant: (_codec_rank(variant.codecs), -variant.bandwidth))


def _text(body: bytes) -> str:
    try:
        return body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _bad("Playlist HLS không phải văn bản UTF-8.") from None


def _probe_first_segment(segment: Segment, ctx: ResolveContext, headers: Mapping[str, str],
                         strip_png: bool = False) -> tuple[dict[str, Any], int]:
    """PROBING only: the first segment (bounded) must be MPEG-TS with video and audio; its streams and size."""
    body, _ = ctx.http.fetch(segment.uri, ctx.control, limit=MAX_PROBE_SEGMENT_BYTES, headers=headers)
    checker = TsChecker(segment.index)
    try:
        if strip_png:
            body = b"".join(ts_payload([body]))
        checker.feed(body)
        checker.finish()
    except HttpError:
        raise SourceError("SEGMENT_NOT_TS", "Đoạn HLS đầu tiên không phải MPEG-TS (dạng chưa hỗ trợ); "
                          "không tải.") from None
    sample = ctx.task_dir / PROBE_SEGMENT_NAME
    sample.write_bytes(body)
    try:
        probe = probe_streams(ctx.ffprobe, sample, input_format="mpegts")
    finally:
        sample.unlink(missing_ok=True)
    if probe is None:
        raise SourceError("BAD_SEGMENT", "FFprobe không đọc được đoạn HLS đầu tiên; không tải.")
    summary = stream_summary(probe)
    require_audio_video(summary, "Luồng HLS")
    return summary, len(body)


def resolve_hls(url: str, ctx: ResolveContext, *, provider: str, label: str,
                headers: Mapping[str, str] | None = None, strip_png: bool = False) -> ResolvedSource:
    """Read the playlist(s) of ``url``; at PROBING (``ctx.ffprobe`` set) also the first segment."""
    extra = dict(headers or {})
    body, response = ctx.http.fetch(url, ctx.control, limit=MAX_PLAYLIST_BYTES, headers=extra)
    playlist = parse_playlist(_text(body), response.url)
    variant = None
    if isinstance(playlist, MasterPlaylist):
        variant = select_variant(playlist, (ctx.previous or {}).get("variant"))
        if variant.audio and variant.audio in playlist.audio_groups_with_uri:
            raise SourceDeclined("luồng HLS có âm thanh tách riêng")
        body, response = ctx.http.fetch(variant.uri, ctx.control, limit=MAX_PLAYLIST_BYTES, headers=extra)
        media = parse_playlist(_text(body), response.url)  # DRM or live in the variant: refused first
        if not isinstance(media, MediaPlaylist):
            raise _bad("Playlist HLS lồng nhau quá sâu.")
        if playlist.declined:
            raise SourceDeclined(playlist.declined)
    else:
        media = playlist
    total = sum(segment.duration for segment in media.segments)
    listing = json.dumps([[stable_url(segment.uri), round(segment.duration, 3)] for segment in media.segments])
    identity = {"kind": "hls", "variant": variant_key(variant) if variant else None,
                "segments": hashlib.sha256(listing.encode("utf-8")).hexdigest(),
                "count": len(media.segments), "duration": round(total, 3)}
    if strip_png:
        identity["segment_prefix"] = "png"
    video_codec, audio_codec = codec_names(variant.codecs if variant else None)
    width, height = (variant.width, variant.height) if variant else (None, None)
    estimated = int(variant.bandwidth * total / 8) if variant else None
    if ctx.ffprobe is not None:
        first = media.segments[0]
        summary, size = _probe_first_segment(first, ctx, extra, strip_png)
        video_codec, audio_codec = summary["video_codec"], summary["audio_codec"]
        width, height = summary["width"] or width, summary["height"] or height
        estimated = estimated or int(size / first.duration * total)
    return ResolvedSource(
        provider=provider, transport="hls", media_url=media.url, identity=identity,
        title=title_from_url(url), label=label, headers=extra, duration_seconds=total,
        estimated_bytes=estimated, video_codec=video_codec, audio_codec=audio_codec, width=width, height=height,
        fragments=len(media.segments), plan=HlsPlan(media.segments, strip_png=strip_png),
    )


class TsChecker:
    """MPEG-TS as it streams in: a sync byte 0x47 at every 188-byte packet, whole packets only."""

    def __init__(self, index: int):
        self.index = index
        self.size = 0

    def _error(self) -> HttpError:
        # Retryable: a CDN may send one error page; after the retries it ends the task (not resumable).
        return HttpError("SEGMENT_NOT_TS", f"Đoạn {self.index} không phải MPEG-TS (máy chủ trả trang lỗi hoặc "
                         "dạng chưa hỗ trợ); không ghép.", retryable=True)

    def feed(self, chunk: bytes) -> None:
        marks = chunk[(-self.size) % TS_PACKET::TS_PACKET]
        if marks.count(0x47) != len(marks):
            raise self._error()
        self.size += len(chunk)

    def finish(self) -> None:
        if self.size == 0 or self.size % TS_PACKET:
            raise self._error()


def segment_name(index: int) -> str:
    return f"{index:06d}.ts"


class SegmentManifest:
    """Size and SHA-256 of every finished segment of one source identity (``seg/manifest.json``)."""

    def __init__(self, seg_dir: Path, identity: str):
        self.seg_dir = seg_dir
        self.path = seg_dir / MANIFEST_NAME
        self.identity = identity
        self._lock = threading.Lock()
        self._entries: dict[int, tuple[int, str]] = {}
        self._saved_at = 0.0
        data = read_json(self.path)
        if data.get("identity") == identity:
            for key, value in (data.get("segments") or {}).items():
                if (str(key).isdigit() and isinstance(value, list) and len(value) == 2
                        and isinstance(value[0], int) and isinstance(value[1], str)):
                    self._entries[int(key)] = (value[0], value[1])
        else:
            self._clear_files(keep=set())  # segments of another source are never reused
        self.save()

    def _clear_files(self, keep: set[int]) -> None:
        for path in self.seg_dir.iterdir():
            if _SEGMENT_FILE.fullmatch(path.name) and int(path.name[:6]) not in keep:
                path.unlink(missing_ok=True)

    def verified(self, count: int, control: Interruptible) -> dict[int, int]:
        """Index -> size of the segments whose file still matches its record; the others are deleted.
        Hashing a long film's segments takes a while: a stop or a cancel ends it between two files."""
        good: dict[int, int] = {}
        with self._lock:
            for index, (size, digest) in sorted(self._entries.items()):
                if control.requested:
                    raise Cancelled()
                path = self.seg_dir / segment_name(index)
                if (1 <= index <= count and path.is_file() and path.stat().st_size == size
                        and sha256_file(path) == digest):
                    good[index] = size
            self._entries = {index: self._entries[index] for index in good}
        self._clear_files(keep=set(good))
        self.save()
        return good

    def add(self, index: int, size: int, digest: str) -> None:
        with self._lock:
            self._entries[index] = (size, digest)
            due = time.monotonic() - self._saved_at >= MANIFEST_SAVE_SECONDS
        if due:
            self.save()

    def save(self) -> None:
        with self._lock:
            data = {"version": 1, "identity": self.identity,
                    "segments": {str(index): [size, digest] for index, (size, digest) in self._entries.items()}}
            self._saved_at = time.monotonic()
            write_json(self.path, data)


@dataclass
class _Wave:
    """What one wave of parallel segments ended with (see ``HlsTransfer._wave``)."""

    scope: Scope
    expired: list[tuple[Segment, str]] = field(default_factory=list)
    error: BaseException | None = None
    sending: bool = True

    def fail(self, failure: BaseException) -> None:
        self.error = self.error or failure
        self.sending = False
        self.scope.abort()


class HlsTransfer:
    """Fetches one resolved ``hls`` source into ``media.mp4`` of the task folder (see the module docstring)."""

    def __init__(self, http: SafeHttp, ffmpeg: Path, ffprobe: Path | None, *, workers: int = SEGMENT_WORKERS,
                 retries: int = SEGMENT_RETRIES, max_refreshes: int = MAX_REFRESHES):
        self.http = http
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.workers = max(1, workers)
        self.retries = retries
        self.max_refreshes = max_refreshes

    def run(self, source: ResolvedSource, refresh: Callable[[], ResolvedSource], task_dir: Path,
            scope: Interruptible, meter: ProgressMeter, log: LogBuffer, guard: SizeGuard | None,
            on_start: Callable[[int, float], None] | None) -> Path:
        seg_dir = task_dir / SEG_DIR
        seg_dir.mkdir(parents=True, exist_ok=True)
        segments = source.plan.segments
        manifest = SegmentManifest(seg_dir, source.identity_key)
        done = manifest.verified(len(segments), scope)
        meter.reuse(sum(done.values()), len(done))
        if done:
            log(f"Dùng lại {len(done)}/{len(segments)} đoạn đã tải (đã kiểm tra kích thước và SHA-256).")
        pending = [segment for segment in segments if segment.index not in done]
        try:
            self._fetch_all(source, refresh, pending, seg_dir, manifest, scope, meter, log, guard)
        finally:
            manifest.save()
        if scope.requested:
            raise Cancelled()
        return self._join(segments, seg_dir, task_dir, scope, meter, log, on_start)

    def _fetch_all(self, source: ResolvedSource, refresh: Callable[[], ResolvedSource], pending: list[Segment],
                   seg_dir: Path, manifest: SegmentManifest, scope: Interruptible, meter: ProgressMeter,
                   log: LogBuffer, guard: SizeGuard | None) -> None:
        """Waves until every segment is there. A refused segment link (expired token) makes the playlist
        resolve again; a segment refused again after ``max_refreshes`` fresh playlists ends the transfer,
        however many other segments still come (links that expire every few minutes keep working)."""
        current, refused, refreshes = source, Counter(), 0
        while pending:
            expired, unsent = self._wave(current, pending, seg_dir, manifest, scope, meter, log, guard)
            if not expired:
                return
            for segment, code in expired:
                refused[segment.index] += 1
                if refused[segment.index] > self.max_refreshes:
                    raise HttpError(code, f"Đoạn {segment.index} vẫn bị máy chủ từ chối sau {self.max_refreshes} lần "
                                    "lấy lại playlist; nguồn không còn cho tải.")
            refreshes += 1
            log(f"Link đoạn hết hạn hoặc bị từ chối; lấy lại playlist (lần {refreshes}).")
            current = refresh()
            fresh = {segment.index: segment for segment in current.plan.segments}
            pending = [fresh[segment.index] for segment in sorted([item for item, _ in expired] + unsent,
                                                                  key=lambda segment: segment.index)]

    def _join(self, segments: Sequence[Segment], seg_dir: Path, task_dir: Path, scope: Interruptible,
              meter: ProgressMeter, log: LogBuffer, on_start: Callable[[int, float], None] | None) -> Path:
        ordered = [seg_dir / segment_name(segment.index) for segment in segments]  # playlist order
        first = probe_streams(self.ffprobe, ordered[0], input_format="mpegts")
        if first is not None:
            require_audio_video(stream_summary(first), "Đoạn HLS đầu tiên")
        meter.stage("remuxing")
        log(f"Đã đủ {len(ordered)} đoạn; ghép thành MP4 theo thứ tự playlist (FFmpeg chép luồng, không mã hóa lại).")
        output = task_dir / "media.mp4.part"
        remux_to_mp4(self.ffmpeg, ordered, output, scope, on_start=on_start, log=log)
        if scope.requested:
            raise Cancelled()
        final = task_dir / "media.mp4"
        replace_held(output, final, scope)
        try:
            safe_remove_tree(task_dir, seg_dir)
        except (OSError, UnsafePathError):
            # The MP4 is whole; a segment still held is removed with the task folder after publishing.
            log("Chưa xóa được thư mục đoạn (file đang bị giữ); sẽ dọn cùng thư mục tạm của lượt.")
        return final

    def _wave(self, source: ResolvedSource, pending: Sequence[Segment], seg_dir: Path, manifest: SegmentManifest,
              scope: Interruptible, meter: ProgressMeter, log: LogBuffer,
              guard: SizeGuard | None) -> tuple[list[tuple[Segment, str]], list[Segment]]:
        """Fetch ``pending`` in parallel (at most twice the workers queued). Returns the segments refused for
        an expired link (with the code) and those not sent after it; raises the first other error. Every
        segment thread has ended when this returns."""
        wave = _Wave(Scope(scope))
        queue: Iterator[Segment] = iter(pending)
        try:
            with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="download-segment") as pool:
                running: dict[Future, Segment] = {}

                def fill() -> None:
                    while wave.sending and not scope.requested and len(running) < self.workers * 2:
                        segment = next(queue, None)
                        if segment is None:
                            return
                        running[pool.submit(self._segment, source, segment, seg_dir, wave.scope, meter, log)] = segment
                try:
                    fill()
                    while running:
                        finished, _ = wait(running, return_when=FIRST_COMPLETED)
                        for future in finished:
                            self._settle(future, running.pop(future), wave, manifest, meter, guard)
                        fill()
                except BaseException:
                    wave.scope.abort()  # a failure here never waits for the running segments to finish
                    raise
            unsent = list(queue)
        finally:
            wave.scope.close()
        if wave.error is not None:
            raise wave.error
        if scope.requested:
            raise Cancelled()
        return wave.expired, unsent

    @staticmethod
    def _settle(future: Future, segment: Segment, wave: _Wave, manifest: SegmentManifest, meter: ProgressMeter,
                guard: SizeGuard | None) -> None:
        try:
            size, digest = future.result()
        except Cancelled:
            return
        except HttpError as failure:
            if failure.code in REFRESH_CODES and failure.status is not None:
                wave.expired.append((segment, failure.code))
                wave.sending = False  # the next wave starts from a fresh playlist
            else:
                wave.fail(failure)
            return
        except BaseException as failure:  # noqa: BLE001 - raised by _wave after every thread has ended
            wave.fail(failure)
            return
        manifest.add(segment.index, size, digest)
        meter.commit(segment.index, size)
        if guard is not None and guard.over(meter.snapshot()):
            wave.fail(SourceError("TOO_LARGE", guard.message()))

    def _segment(self, source: ResolvedSource, segment: Segment, seg_dir: Path, scope: Interruptible,
                 meter: ProgressMeter, log: LogBuffer) -> tuple[int, str]:
        final = seg_dir / segment_name(segment.index)
        part = seg_dir / (segment_name(segment.index) + ".part")

        def attempt() -> tuple[int, str]:
            digest, checker = hashlib.sha256(), TsChecker(segment.index)
            try:
                with self.http.open(segment.uri, scope, headers=source.headers) as response, \
                        part.open("wb") as handle:
                    chunks = ts_payload(response.chunks()) if source.plan.strip_png else response.chunks()
                    for chunk in chunks:
                        if checker.size + len(chunk) > MAX_SEGMENT_BYTES:
                            raise SourceError("SEGMENT_TOO_LARGE", f"Đoạn {segment.index} lớn quá "
                                              f"{MAX_SEGMENT_BYTES // 2**20} MB; dừng để tránh tải nhầm nội dung.")
                        checker.feed(chunk)
                        digest.update(chunk)
                        handle.write(chunk)
                        meter.advance(segment.index, len(chunk))
                checker.finish()
            except BaseException:
                meter.drop(segment.index)
                part.unlink(missing_ok=True)
                raise
            return checker.size, digest.hexdigest()

        size, digest = with_retries(
            attempt, scope, attempts=self.retries,
            on_retry=lambda number, failure: log(f"Đoạn {segment.index}: {failure.message} "
                                                 f"Thử lại {number}/{self.retries}."))
        try:
            if scope.requested:  # checked before the rename: a stopped transfer leaves no new segment
                raise Cancelled()
            replace_held(part, final, scope)
        except BaseException:
            meter.drop(segment.index)
            part.unlink(missing_ok=True)
            raise
        return size, digest
