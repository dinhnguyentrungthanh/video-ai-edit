"""A direct media file (MP4, MOV, MKV, WebM or MPEG-TS) fetched over checked HTTP, resumable with Range.

Resolve (PROBING): one GET of the first ``SNIFF_BYTES`` only, never the whole file.
- The answer must look like a video container (its first bytes), else the link goes back to yt-dlp
  (``SourceDeclined``) like any page; an HLS playlist behind the link is handed to ``download_hls``.
- Size (Content-Range or Content-Length), Range support and a validator (strong ETag or Last-Modified).
- When the header carries the stream list (an MP4 with its index first, MKV, WebM), ffprobe reads that
  local sample: a file without video or without audio fails before it is downloaded.

Download: ``media.part`` in the task folder, continued with ``Range`` + ``If-Range`` after a stop, a
restart or a network error. ``media-source.json`` ties the part to the source identity and to the
validator of its bytes (the one of the answer that started it at byte 0, else the probe's), so a part is
only continued for the same file: a server that answers the
range with the whole file (a new version, or no range support) restarts it from byte 0. Any other 2xx to a
range (a 203 from a proxy…) does not show where its bytes start: the run ends BAD_RESPONSE and nothing of it
is written. An MPEG-TS file is copied into MP4 by the project's FFmpeg (no re-encode).

A plan with ``strict_versions`` (a source account's file, whose link is a ticket that changes every time)
asks for proof that the bytes on disk and a fresh link are one version of the file before it continues the
part: it continues only with a validator, and with a fresh link (a refresh within the run, or the link the
run started with) only when that link's own validator (from its probe) is the part's, or when the link's
answer, under another validator, starts with every byte of the part: the file is then asked from byte 0
without If-Range and the whole part is compared with the answer as it arrives (read back from disk at most
``OVERLAP_CHECK_BYTES`` at a time, never written, not counted as progress; transfer stage "comparing"). Only
when every byte of the part is equal does the part's validator become that answer's and the rest of the same
answer get appended (no request of its own: a ticket good for a few requests is not used up); an answer that
ends or is cancelled first leaves the part and its validator as they were. This is for a host whose nodes
give each copy of a file its own ETag (nginx's modification time and size; measured on the first real source
in 2026-10). The cost: the part's bytes come over the network again, at download speed, and a comparison cut
before its end starts again from byte 0 (a connection that never carries the whole part ends the run by the count
of retries, the part kept for Tiếp tục; it is never dropped for that). Avoiding that while
keeping the proof needs a version id or a content hash the source vouches for, which that host does not give
(comparing only the part's last bytes was tried and dropped: it let an old head be joined to a new tail).
Different bytes are another version (below). Without such proof it
starts again from byte 0, also after a network error within the run; a 401 from the file server then asks
for a fresh link like a 403. A range answer (206) is also checked against the part before any byte of it is
written: when its own version marks show another version (``other_version``: an ETag that differs from the
part's ETag, a weak one included, or a Last-Modified that differs from the part's Last-Modified), as from a
server that ignores If-Range, nothing is appended; the part is dropped and the file starts again from byte 0
once in a run, and a second such answer in the same run ends it (SOURCE_CHANGED). Marks of the other kind
or none at all show nothing, and the request's If-Range decides as for every server. The validator is
never changed to fit such an answer; it is only set from an answer that starts the part at byte 0 (the
whole part is then that answer's version). A refresh whose link shows another version than a part with
bytes drops the part too and counts with those answers, whatever the progress in between (a host with an
ETag per ticket would otherwise start again after every refresh). Other plans keep the rules above.

On every plan, a failed try starts the count of retries (``FILE_RETRIES``) again only when it leaves the part
longer than any part kept before in the run, from the part the run resumed (a strict part without a validator
is never kept). That mark goes back to 0 only when the part is dropped for another version: a version restart
(bounded above), or a 200 answer to a range whose own marks show another version than the part
(``other_version``), at most ``MAX_NEW_VERSION_RESETS`` times in a run. A 200 of the same version or without
marks restarts the part from byte 0 and keeps the mark, so cuts that fall back and rise in turn cannot keep a
run going, while a file that really changed is fetched again with a fresh count. A file that changes again in
the same run (or nodes of one host that give their own ETags in turn) gets no further reset: such a run ends
by the count of retries, and Tiếp tục starts a new run.

A strict plan's log says, in fixed words and never a value or a hash, which version marks each link and each
answer carried (strong or weak ETag, Last-Modified or not, which mark is usable), what each request sent (If-Range
and the kind of its mark, or the comparison from byte 0) and every change of the part's saved mark and why: one
answer without a usable mark is reported as that answer's, never as the source's (after the user's real test in
2026-10 showed one ticket whose part had no saved mark, its headers unknown).
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path
from email.utils import parsedate_tz
from typing import Any, BinaryIO, Callable, Iterator, Mapping
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
MAX_VERSION_RESTARTS = 1  # strict plans: restarts of the file for another version (206 or refresh), in one run
MAX_NEW_VERSION_RESETS = 1  # every plan: 200 answers of another version that set the progress mark back, in one run
# Strict plans: a link whose validator is not the part's is asked from byte 0, and every byte of the part must come
# back identical before anything is appended (no request of its own: the same answer goes on). The part is read
# back at most this many bytes at a time, so the comparison's memory stays bounded whatever the part's size.
OVERLAP_CHECK_BYTES = 1024 * 1024
PART_NAME = "media.part"
STATE_NAME = "media-source.json"
SAMPLE_NAME = "sniff.bin"
CONTAINER_SUFFIX = {"mp4": ".mp4", "mov": ".mov", "matroska": ".mkv", "webm": ".webm", "mpegts": ".mp4"}
PROBE_FORMAT = {"mp4": "mov", "mov": "mov", "matroska": "matroska", "webm": "matroska", "mpegts": "mpegts"}
CONTAINER_LABEL = {"mp4": "MP4", "mov": "MOV", "matroska": "MKV", "webm": "WebM", "mpegts": "MPEG-TS"}
_COMPARE_NOTE = ("Link mới có dấu phiên bản khác phần đã tải; nhận lại file từ byte 0 để so toàn bộ phần đã tải "
                 "trước khi tải nối (các byte này không tính là tải mới).")
_COMPARE_CUT = ("Kết nối ngắt khi đang so phần đã tải với link mới (chưa so hết, chưa nối gì); phần đã tải vẫn được "
                "giữ và lần sau so lại từ byte 0.")
_HLS_TYPES = ("mpegurl",)
_MP4_BOXES = (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip", b"pnot")
_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)
_FILENAME_STAR = re.compile(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)")
_FILENAME = re.compile(r'filename\s*=\s*"?([^";]+)"?')


def validator_of(etag: str, modified: str) -> str | None:
    """A strong ETag, else Last-Modified: what If-Range may carry (never a value with a line break)."""
    value = etag if etag and not etag.startswith("W/") else (modified or None)
    return value if value and "\r" not in value and "\n" not in value else None


def etag_kind(etag: str) -> str | None:
    return ("weak" if etag.startswith("W/") else "strong") if etag else None


def mark_kind(validator: str | None) -> str:
    """Fixed words for the kind of a usable mark (never its value): how ``other_version`` reads it."""
    if not validator:
        return "không có"
    return "ETag" if validator.startswith('"') or parsedate_tz(validator) is None else "Last-Modified"


def answer_marks(kind: str | None, has_modified: bool | None, validator: str | None) -> str:
    """Fixed words for the version marks of one answer: its ETag (strong, weak, none), its Last-Modified (present or
    not) and the usable mark (``validator_of``); None for what is not known (a plan made without the probe's)."""
    words = []
    if kind is not None or has_modified is not None:
        words.append({"strong": "ETag mạnh", "weak": "ETag yếu"}.get(kind or "", "không có ETag"))
        words.append("có Last-Modified" if has_modified else "không có Last-Modified")
    words.append(f"dấu dùng được: {mark_kind(validator)}")
    return ", ".join(words)


def other_version(validator: str, etag: str, modified: str) -> bool:
    """True when a range answer's own marks (its ``etag`` and ``modified`` headers) show another version of
    the file than ``validator``, the part's (from ``validator_of``: a strong ETag, else a Last-Modified).
    A mark equal to the validator names the same version. Otherwise, against an ETag (quoted, or any value
    that is not a date): the answer's ETag, a weak one by its tag (a weak tag never proves the same bytes,
    but one that differs proves other ones); against a Last-Modified (a date): the answer's Last-Modified.
    Marks of the other kind, or none, show nothing."""
    tag = etag[2:] if etag.startswith("W/") else etag
    if validator in (tag, modified):
        return False
    if validator.startswith('"') or parsedate_tz(validator) is None:  # an entity tag
        return bool(tag)
    return bool(modified)


def _same_bytes(kept: BinaryIO, data: bytes) -> bool:
    """True when the next bytes of ``kept`` are ``data``, read back at most OVERLAP_CHECK_BYTES at a time."""
    for start in range(0, len(data), OVERLAP_CHECK_BYTES):
        piece = data[start:start + OVERLAP_CHECK_BYTES]
        if kept.read(len(piece)) != piece:
            return False
    return True


class PlaylistLink(Exception):
    """The link answered with an HLS playlist: ``download_hls`` takes it."""


class _OtherVersion(Exception):
    """A range answer of a strict plan shows another version than the part (nothing of it was written)."""

    message = "Máy chủ trả đoạn byte của một phiên bản file khác phần đã tải; tải lại từ đầu."


class _OverlapMismatch(_OtherVersion):
    """A strict part's bytes, asked again from byte 0 of a link with another validator, came back different
    (nothing of that answer was written): another version, restarted and counted like one."""

    message = "Link mới cho nội dung khác phần đã tải (file khác phiên bản); tải lại từ đầu."


@dataclass(frozen=True)
class FilePlan:
    container: str
    total: int | None
    ranges: bool
    validator: str | None
    strict_versions: bool = False  # see the module docstring (source accounts)
    # What the probe's answer carried, for the log only (``answer_marks``): "strong", "weak" or None; Last-Modified.
    # Not part of a plan's equality: two plans of the same file and version are the same plan.
    etag_kind: str | None = field(default=None, compare=False)
    has_modified: bool | None = field(default=None, compare=False)


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
        plan=FilePlan(container, total, ranges, validator, etag_kind=etag_kind(etag), has_modified=bool(modified)),
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
        if plan.strict_versions:
            held = part.stat().st_size if part.is_file() else 0
            log(f"Link của lượt này: {answer_marks(plan.etag_kind, plan.has_modified, plan.validator)}; "
                + (f"phần đã tải lưu dấu: {mark_kind(saved.get('validator'))}." if held else "chưa có phần đã tải."))
        total = plan.total
        offset = self._resume_offset(plan, part, saved, state_path, log)
        meter.total_bytes = total
        meter.reuse(offset)
        if offset:
            log(f"Tải nối từ {offset} byte đã có.")
        strict = plan.strict_versions
        # The link changed (this run's, or a refresh's) and shows another validator than the part: an answer from
        # byte 0 must bring every byte of the part (``_receive``, ``verify``) before the part takes its validator.
        unverified = bool(strict and offset and plan.validator and plan.validator != saved.get("validator"))
        failures, refreshed, other_versions, new_versions = 0, False, 0, 0
        longest = offset  # a try moves the file forward only past the longest part kept in this run
        refresh_codes = {"FORBIDDEN", "LOGIN_REQUIRED"} if strict else {"FORBIDDEN"}

        def new_version() -> None:  # a 200 to the range brought another version: its bytes are new progress
            nonlocal longest, new_versions
            if new_versions < MAX_NEW_VERSION_RESETS:
                new_versions += 1
                longest = 0
        while True:
            if total is not None and offset >= total:
                break  # every byte is there (a stop came during the remux or just before it): never ask past the end
            if strict and offset and not saved.get("validator"):
                offset = self._restart(part, saved, state_path, meter, log, None, "Không có dấu phiên bản file để "
                                       "nối phần đã tải; tải lại từ đầu.")
            # A comparison is over once bytes follow it or the validator changed (``_adopt``, after the whole part).
            asked_from, marked = offset, saved.get("validator")
            try:
                offset, total = self._receive(source, part, offset, total, saved, state_path, scope, meter, log,
                                              guard, strict=strict, on_new_version=new_version,
                                              verify=unverified and offset > 0)
                unverified = unverified and offset == asked_from and saved.get("validator") == marked
                if total is None or offset >= total:
                    break
                raise HttpError("NETWORK", "Máy chủ ngắt kết nối trước khi gửi hết file.", retryable=True)
            except Cancelled:
                raise
            except _OtherVersion as other:  # an _OverlapMismatch too
                unverified = False
                other_versions += 1
                if other_versions > MAX_VERSION_RESTARTS:
                    raise SourceChanged("phiên bản file") from None
                offset = longest = self._restart(part, saved, state_path, meter, log, None, other.message)
                refreshed = False  # byte 0 of the new version may need a fresh ticket (bounded by the count above)
            except HttpError as error:
                offset = part.stat().st_size if part.is_file() else 0
                # Cut while comparing: compared again. Cut after it (bytes appended, or another validator): not.
                unverified = unverified and offset == asked_from and saved.get("validator") == marked
                kept = not (strict and not saved.get("validator"))  # a strict part without a validator is dropped
                if offset > longest and kept:  # the connection moved the file forward: count again from zero
                    longest, failures, refreshed = offset, 0, False
                if error.code in refresh_codes and not refreshed:
                    refreshed = True
                    log("Máy chủ từ chối link (có thể đã hết hạn); lấy lại nguồn rồi tải tiếp.")
                    source = refresh()
                    if strict:
                        plan_now = source.plan
                        log("Link mới: " + (answer_marks(plan_now.etag_kind, plan_now.has_modified, plan_now.validator)
                                            if isinstance(plan_now, FilePlan) else "không rõ") + ".")
                    if strict and offset and self._link_validator(source) is None:
                        other_versions += 1  # the part is dropped (no mark to go on with): a restart like a 206's
                        if other_versions > MAX_VERSION_RESTARTS:
                            raise SourceChanged("phiên bản file") from None
                    held = offset
                    offset = self._same_version(source, strict, part, saved, state_path, meter, log, offset)
                    if offset < held:
                        longest = offset  # dropped for another version (counted above): it starts again
                    fresh = self._link_validator(source)
                    unverified = bool(strict and offset and fresh and fresh != saved.get("validator"))
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

    @staticmethod
    def _resume_offset(plan: FilePlan, part: Path, saved: dict[str, Any], state_path: Path, log: LogBuffer) -> int:
        """The bytes of the part a new run may continue: none past the probed size, none without a validator
        and, on a strict plan, none of another version than the one the link shows."""
        offset = part.stat().st_size if part.is_file() else 0
        if plan.total is not None and offset > plan.total:
            part.unlink()
            return 0
        if offset and not saved.get("validator"):
            # No ETag or Last-Modified: a replaced file of the same size could not be told apart. Start over.
            # Said of this part only: another answer or ticket of the same source may carry a mark.
            log("Phần đã tải không có dấu phiên bản (trả lời đã tải nó không cho ETag/Last-Modified dùng được); "
                "tải lại từ đầu thay vì tải nối.")
            part.unlink()
            return 0
        if plan.strict_versions and offset and plan.validator and plan.validator != saved.get("validator"):
            log(_COMPARE_NOTE)
            return offset  # the run's first answer is compared with the whole part (``_receive``, ``verify``)
        if plan.strict_versions and offset and plan.validator != saved.get("validator"):
            log("Link mới không cho thấy cùng phiên bản file với phần đã tải; tải lại từ đầu thay vì tải nối.")
            part.unlink()
            saved["validator"] = plan.validator
            write_json(state_path, saved)
            log(f"Dấu lưu cho phần đã tải: {mark_kind(plan.validator)} (link của lượt này, phần cũ bỏ).")
            return 0
        return offset

    @staticmethod
    def _link_validator(source: ResolvedSource) -> str | None:
        """The validator a (refreshed) source's link showed at its probe."""
        return source.plan.validator if isinstance(source.plan, FilePlan) else None

    @staticmethod
    def _adopt(saved: dict[str, Any], state_path: Path, log: LogBuffer, validator: str | None) -> None:
        """The link served every byte of the part: the part goes on, and its mark becomes the one this link's
        answer showed (the next range's If-Range)."""
        log("Đã so xong: link mới cho cùng nội dung với toàn bộ phần đã tải; tải nối tiếp.")
        saved["validator"] = validator
        write_json(state_path, saved)
        log(f"Dấu lưu cho phần đã tải: {mark_kind(validator)} (sau khi so xong).")

    @classmethod
    def _same_version(cls, source: ResolvedSource, strict: bool, part: Path, saved: dict[str, Any],
                      state_path: Path, meter: ProgressMeter, log: LogBuffer, offset: int) -> int:
        """After a refresh: on a strict plan, a link that shows another validator keeps the part for the
        comparison of the next answer with the whole part (``_receive``, ``verify``), a link without one drops
        it; with no byte yet, only the saved validator changes."""
        fresh = cls._link_validator(source)
        if not strict or fresh == saved.get("validator"):
            return offset
        if fresh is not None and offset:
            log(_COMPARE_NOTE)
            return offset
        return cls._restart(part, saved, state_path, meter, log, fresh, "Link mới không cho thấy cùng phiên bản "
                            "file với phần đã tải; tải lại từ đầu." if offset else "")

    @staticmethod
    def _restart(part: Path, saved: dict[str, Any], state_path: Path, meter: ProgressMeter, log: LogBuffer,
                 validator: str | None, why: str) -> int:
        """Drop the part (its version cannot be shown to be the link's) and go on from byte 0."""
        if why:
            log(why)
        part.unlink(missing_ok=True)
        meter.reset(0)
        saved["validator"] = validator
        write_json(state_path, saved)
        log(f"Dấu lưu cho phần đã tải: {mark_kind(validator)} (bỏ phần cũ, nhận lại từ byte 0).")
        return 0

    def _receive(self, source: ResolvedSource, part: Path, offset: int, total: int | None,
                 saved: dict[str, Any], state_path: Path, scope: Interruptible, meter: ProgressMeter,
                 log: LogBuffer, guard: SizeGuard | None, *, strict: bool = False,
                 on_new_version: Callable[[], None] | None = None, verify: bool = False) -> tuple[int, int | None]:
        """One answer appended to the part. ``verify`` (a strict part and a link with another validator): the
        file is asked from byte 0 without If-Range, and every byte of the part must equal the answer's first
        ones (``_compare_part``; else _OverlapMismatch) before the part's validator becomes the answer's (or the
        link's) and the rest of the same answer is appended. An answer that ends first appends nothing and
        leaves the validator as it was."""
        headers = dict(source.headers)
        overlap = offset if verify else 0  # the part's bytes the answer brings again: all of them
        if overlap:
            headers["Range"] = "bytes=0-"  # no If-Range: the bytes themselves are compared
        elif offset:
            headers["Range"] = f"bytes={offset}-"
            if saved.get("validator"):
                headers["If-Range"] = saved["validator"]
        with self.http.open(source.media_url, scope, headers=headers) as response:
            etag, modified = response.header("ETag"), response.header("Last-Modified")
            if strict:
                sent = ("so lại từ byte 0, không gửi If-Range" if overlap else
                        f"từ byte {offset}, If-Range: {mark_kind(saved.get('validator'))}" if offset else "từ byte 0")
                log(f"Trả lời HTTP {response.status} ({sent}): "
                    f"{answer_marks(etag_kind(etag), bool(modified), validator_of(etag, modified))}.")
            if offset and not overlap and response.status == 200:
                log("Máy chủ gửi lại cả file (không tải nối được hoặc file đã đổi); tải lại từ đầu.")
                if on_new_version and saved.get("validator") and other_version(saved["validator"], etag, modified):
                    on_new_version()
                offset = 0
                part.unlink(missing_ok=True)
                meter.reset(0)
                saved["validator"] = validator_of(etag, modified)
                write_json(state_path, saved)
                if strict:
                    log(f"Dấu lưu cho phần đã tải: {mark_kind(saved['validator'])} (trả lời cả file từ byte 0).")
            elif response.status == 206:
                if strict and offset and not overlap and other_version(saved["validator"], etag, modified):
                    raise _OtherVersion()  # never appended, whatever its range says
                ranged = content_range(response.header("Content-Range"))
                if ranged is None or ranged[0] != offset - overlap:
                    raise SourceError("BAD_RESPONSE", f"{response.host} trả sai đoạn byte khi tải nối.")
                if total is not None and ranged[2] not in (None, total):
                    raise SourceChanged("dung lượng")
                total = total if total is not None else ranged[2]
            elif offset and response.status != 200:  # another 2xx to the range: nothing shows where its bytes start
                raise SourceError("BAD_RESPONSE", f"{response.host} trả HTTP {response.status} thay vì đoạn byte "
                                  "khi tải nối.")  # (a 200 here answers the comparison: the whole file from byte 0)
            elif total is None and response.content_length is not None:
                total = response.content_length
            if total is not None and response.content_length not in (None, total - offset + overlap):
                raise SourceChanged("dung lượng")  # a shorter or longer body than the file the probe measured
            shown = validator_of(etag, modified)
            if not offset and shown is not None and shown != saved.get("validator"):
                saved["validator"] = shown  # the whole part comes from this answer: its version is the part's
                write_json(state_path, saved)
                if strict:
                    log(f"Dấu lưu cho phần đã tải: {mark_kind(shown)} (trả lời từ byte 0).")
            meter.total_bytes = total
            chunks: Iterator[bytes] = iter(response.chunks())
            if overlap:  # the whole part first: compared, never written nor counted
                rest = self._compare_part(chunks, part, overlap, scope, meter)
                if rest is None:  # nothing appended, the validator unchanged; never a finished file, whatever the size
                    raise HttpError("NETWORK", _COMPARE_CUT, retryable=True)
                self._adopt(saved, state_path, log, shown or self._link_validator(source))
                chunks = itertools.chain((rest,), chunks) if rest else chunks
            written = 0
            try:
                with part.open("ab" if offset else "wb") as handle:
                    for chunk in chunks:
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

    @staticmethod
    def _compare_part(chunks: Iterator[bytes], part: Path, size: int, scope: Interruptible,
                      meter: ProgressMeter) -> bytes | None:
        """The answer's first ``size`` bytes compared with the part's as they arrive (transfer stage
        "comparing": nothing is written or counted as progress). Returns the rest of the piece that ends them,
        or None when the answer ends first; a different byte raises _OverlapMismatch, a stop Cancelled, a cut
        connection a retryable NETWORK error that says the part is kept."""
        meter.stage("comparing")
        try:
            with part.open("rb") as kept:
                for chunk in chunks:
                    if scope.requested:
                        raise Cancelled()
                    head = chunk[:size]
                    if not _same_bytes(kept, head):
                        raise _OverlapMismatch()
                    size -= len(head)
                    if not size:
                        return chunk[len(head):]
                    meter.emit()  # the same numbers, so the task row shows the comparison is alive
            return None
        except Cancelled:
            raise
        except HttpError as error:
            raise HttpError(error.code, _COMPARE_CUT, status=error.status, retryable=error.retryable) from None
        finally:
            meter.stage("downloading")

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
