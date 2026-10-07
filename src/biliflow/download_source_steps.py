"""The steps of a task whose link a source provider claims (``download_sources``); a mixin of DownloadWorker.

PROBING: the provider resolves the link. ``SourceDeclined`` hands it to yt-dlp as before; a
``SourceError`` (refused, DRM, live, login, network policy) ends the task FAILED. The public part of
the source and its stable identity go into ``probe`` (never the media link or a header).

DOWNLOADING: the link is resolved again first (signed links expire), and the fresh identity must equal
the probe's; then the transfer continues what is on disk. After its retries a network error, a failed
lookup, a full disk or a file held by another program ends the task INTERRUPTED ("Tiếp tục" continues
it); a changed source ends FAILED (SOURCE_CHANGED) and its parts are never continued. A file already
finished by an earlier run (stopped while it was checked or moved) goes straight to VERIFYING. The
usual VERIFYING and PUBLISHING steps follow.

A link no provider claims whose host the local config lists for an id the code has no provider for gets
a note (``_note_recognized_host``): recognizing a host is not supporting it, and yt-dlp reads the link.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from biliflow.download_http import Cancelled, HttpError
from biliflow.download_page_sources import SourceNeedsChoice
from biliflow.download_probe import NOTHING_READ_CODES, ProbeEntry, format_duration
from biliflow.download_runner import ProcessControl, mask_line
from biliflow.download_source_types import (
    ResolveContext,
    ResolvedSource,
    SourceChanged,
    SourceDeclined,
    SourceError,
    describe_change,
)
from biliflow.download_sources import LOCAL_CONFIG, RESUMABLE_CODES, SourceProvider
from biliflow.download_transfer import finished_media

SOURCE_DECLINED = object()


def with_reader_note(code: str | None, message: str | None, note: str | None) -> str | None:
    """The reason of a failed yt-dlp probe, followed by ``note`` when yt-dlp found nothing to read."""
    if note and code in NOTHING_READ_CODES:
        return f"{message} {note}" if message else note
    return message


def describe_source(source: ResolvedSource) -> str:
    parts = [f"Nguồn: {source.label}"]
    if source.fragments:
        parts.append(f"{source.fragments} đoạn")
    if source.duration_seconds:
        parts.append(format_duration(source.duration_seconds))
    if source.height:
        parts.append(f"{source.width or '?'}×{source.height}")
    codecs = " + ".join(item for item in (source.video_codec, source.audio_codec) if item)
    if codecs:
        parts.append(codecs)
    if source.estimated_bytes and source.transport == "http_file":
        parts.append(f"{source.estimated_bytes / 1024**2:.0f} MB")
    return " · ".join(parts)


class DownloadSourceSteps:
    """Uses the worker's store, sources, http, transfers, ffprobe and its step helpers."""

    def _source_context(self, task: dict[str, Any], control: ProcessControl, *, probing: bool) -> ResolveContext:
        task_id, attempt = task["id"], task["attempt"]
        probe = task.get("probe") or {}
        return ResolveContext(
            http=self.http, control=control, task_dir=self._task_dir(task_id),
            ffprobe=Path(self.ffprobe) if probing and Path(self.ffprobe).is_file() else None,
            previous=({"selection": probe["selection"]} if probe.get("selection") else None)
                     if probing else probe.get("identity_detail"),
            log=lambda line: self.store.append_log(task_id, attempt, [mask_line(line)]),
        )

    def _probe_source(self, task: dict[str, Any], provider: SourceProvider, control: ProcessControl) -> Any:
        task_id = task["id"]
        try:
            source = provider.resolve(task["url"], self._source_context(task, control, probing=True))
        except SourceNeedsChoice as choice:
            if control.requested:
                return self._end_requested(task_id, control)
            entries = [ProbeEntry(index=index, title=item["title"], duration_seconds=None,
                                  estimated_bytes=None, video_id=None, live_status=None, drm=False,
                                  expected_files=1).as_dict()
                       for index, item in enumerate(choice.choices, 1)]
            page = {"provider": provider.id, "source_label": provider.label, "provider_choice": True, "ready": False,
                    "provider_choices": [item["selection"] for item in choice.choices]}
            moved = self.store.transition(task_id, {"PROBING"}, "NEEDS_CHOICE", entries=entries,
                                          probe=page, error_message=choice.message)
            if moved:
                self._event(moved, "NEEDS_CHOICE", choice.message)
            return None
        except SourceDeclined as declined:
            if control.requested:
                return self._end_requested(task_id, control)
            self._event(task, "SOURCE_DECLINED", f"{provider.label}: {declined.message}; đọc link bằng yt-dlp "
                        "như link thường.")
            return SOURCE_DECLINED
        except (SourceError, HttpError) as error:
            if control.requested or isinstance(error, Cancelled):
                return self._end_requested(task_id, control)
            return self._fail(task_id, {"PROBING"}, error.code, error.message)
        if control.requested:
            return self._end_requested(task_id, control)
        self._event(task, "SOURCE_RESOLVED", describe_source(source))
        entry = ProbeEntry(
            index=task.get("chosen_entry") or 0, title=source.title[:300] or f"video-{task_id}", duration_seconds=source.duration_seconds,
            estimated_bytes=source.estimated_bytes, video_id=None, live_status=None, drm=False, expected_files=1,
            video_codec=source.video_codec, audio_codec=source.audio_codec, height=source.height,
        )
        page = {"extractor": None, "page_title": source.title[:300], "entry_count": 1, **source.public()}
        return self._ready(task_id, {"PROBING"}, entry, [entry.as_dict()], page, "WAITING_SPACE")

    def _note_recognized_host(self, task: dict[str, Any]) -> str | None:
        """When the local config lists this link's host for a provider id the code does not have: an event
        says so before yt-dlp reads the link, and the note returned joins the reason if yt-dlp finds nothing."""
        provider_id = self.sources.recognized_without_provider(task["url"])
        if provider_id is None:
            return None
        note = (f"Tên miền này có trong {LOCAL_CONFIG.as_posix()} cho bộ đọc nguồn \"{provider_id}\", nhưng "
                "BiliFlow chưa có bộ đọc nguồn đó: nhận diện được tên miền chưa có nghĩa là tải được.")
        self._event(task, "SOURCE_NOT_IMPLEMENTED", f"{note} Link được đọc bằng yt-dlp như link thường.",
                    level="WARNING")
        return note

    def _fresh_source(self, task: dict[str, Any], provider: SourceProvider, control: ProcessControl) -> ResolvedSource:
        """The source resolved again (fresh links); SourceChanged when it is no longer the probed one."""
        probe = task.get("probe") or {}
        try:
            source = provider.resolve(task["url"], self._source_context(task, control, probing=False))
        except SourceDeclined as declined:
            raise SourceChanged(f"không còn đọc được như lúc thăm dò: {declined.message}") from None
        if source.identity_key != probe.get("identity"):
            raise SourceChanged(describe_change(probe.get("identity_detail"), source.identity))
        return source

    def _interrupt(self, task_id: int, code: str | None, message: str | None) -> None:
        text = f"{message or 'Lỗi mạng.'} Phần đã tải được giữ; bấm Tiếp tục để tải tiếp phần còn thiếu."
        moved = self.store.transition(task_id, {"DOWNLOADING"}, "INTERRUPTED", error_code=code, error_message=text,
                                      speed=None, eta=None, pid=None, pid_created=None)
        if moved:
            self._event(moved, "INTERRUPTED", text, level="WARNING", payload={"code": code})
        return None

    def _download_source(self, task: dict[str, Any], control: ProcessControl) -> dict[str, Any] | None:
        task_id, attempt = task["id"], task["attempt"]
        probe = task.get("probe") or {}
        provider = self.sources.get(probe.get("provider"))
        if provider is None:
            return self._fail(task_id, {"DOWNLOADING"}, "PROVIDER_MISSING",
                              "Bộ đọc nguồn dùng lúc thăm dò không còn; bấm Thử lại từ đầu.")
        finished = finished_media(self._task_dir(task_id), probe.get("identity") or "")
        if finished is not None:  # stopped while it was checked or moved: no need for the (maybe expired) link
            self._event(task, "DOWNLOADING", f"Dùng lại {finished.name} đã tải xong ở lần trước; kiểm tra lại.")
            return self.store.transition(task_id, {"DOWNLOADING"}, "VERIFYING", temp_file=str(finished),
                                         speed=None, eta=None)
        # No separate link check here: every request of the transfer passes the same policy (SafeHttp), and
        # a failed lookup (the network is down) then ends INTERRUPTED, never FAILED with the parts lost.
        self._event(task, "DOWNLOADING", "Lấy lại nguồn mới rồi bắt đầu tải.")
        try:
            source = self._fresh_source(task, provider, control)
        except (SourceError, HttpError) as error:
            if control.requested or isinstance(error, Cancelled):
                return self._end_requested(task_id, control)
            if isinstance(error, HttpError) and error.code in RESUMABLE_CODES:
                return self._interrupt(task_id, error.code, error.message)
            return self._fail(task_id, {"DOWNLOADING"}, error.code, error.message)
        outcome = self.transfers.download(
            source, lambda: self._fresh_source(task, provider, control), self._task_dir(task_id), control,
            on_progress=self._progress_writer(task_id, attempt),
            on_log=lambda lines: self.store.append_log(task_id, attempt, lines),
            on_start=self._pid_recorder(task_id), guard=self._size_guard(task),
        )
        self.store.update_fields(task_id, pid=None, pid_created=None)
        if control.requested:
            return self._end_requested(task_id, control)
        if not outcome.ok:
            if outcome.resumable:
                return self._interrupt(task_id, outcome.code, outcome.message)
            return self._fail(task_id, {"DOWNLOADING"}, outcome.code, outcome.message)
        return self.store.transition(task_id, {"DOWNLOADING"}, "VERIFYING", temp_file=str(outcome.final_path),
                                     speed=None, eta=None)
