"""The steps of a task whose link a source provider claims (``download_sources``); a mixin of DownloadWorker.

PROBING: the provider resolves the link. ``SourceDeclined`` hands it to yt-dlp as before; a
``SourceError`` (refused, DRM, live, login, network policy) ends the task FAILED. The public part of
the source and its stable identity go into ``probe`` (never the media link or a header).

A source account (M4): ``SourceLoginRequired`` at PROBING or DOWNLOADING (also from a ticket refreshed in
the middle of a transfer) moves the task to WAITING_LOGIN with its part kept (download_account_tasks), and
a series page (``SourceNeedsEpisodes``) waits in NEEDS_CHOICE of the "episodes" kind. An episode task of a
group is probed with its chosen file (``probe.account_file``). A hidden run whose driver had to be ended
leaves a SOURCE_RUN_HUNG event (its phase and module:function:line, never the error's text).

DOWNLOADING: the link is resolved again first (signed links expire), and the fresh identity must equal
the probe's; then the transfer continues what is on disk. A source account's file is the exception
(download_account_tickets, Codex's TICKET-REUSE prompt): its link stays in memory for the task, so the first
transfer right after the probe uses the probe's ticket, and a later run (Tiếp tục after Dừng or a network error)
first checks the kept link with the bounded HTTP probe (no browser, no ticket; the transfer then decides by the
answer's validator as for any link: the same one continues the part, another one compares the whole part, none
starts again). Only a kept link the file server refuses or no longer serves as the probed file is replaced, through
the same bounded resolve as before; a network error during that check ends INTERRUPTED like any other, without a
ticket or a sign-in. Every fresh or refreshed link of the run replaces the kept one. After its retries a network error, a failed
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

from biliflow.download_account_sources import SourceNeedsEpisodes
from biliflow.download_http import Cancelled, HttpError
from biliflow.download_media_file import FilePlan, answer_marks
from biliflow.download_page_sources import SourceNeedsChoice
from biliflow.download_probe import NOTHING_READ_CODES, ProbeEntry, format_duration
from biliflow.download_runner import ProcessControl, mask_line
from biliflow.download_source_types import (
    ResolveContext,
    ResolvedSource,
    SourceChanged,
    SourceDeclined,
    SourceError,
    SourceLoginRequired,
    describe_change,
)
from biliflow.download_sources import LOCAL_CONFIG, SourceProvider, resumable_error
from biliflow.download_transfer import finished_media

SOURCE_DECLINED = object()
RUN_ENDED = object()  # _kept_source: the run already ended (stopped, or INTERRUPTED by a network error)
MAX_HANG_FRAMES = 8
# What Tiếp tục does with a kept part, without promising more: a part is continued only for the same file version
# (a part without a version mark, or of another version, starts again from byte 0).
RESUME_NOTE = "bấm Tiếp tục để tải nối nếu nguồn còn đúng phiên bản file, nếu không thì tải lại từ đầu."


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
        if probing:  # an episode of a group names its file; a film's choice names its variant
            previous = ({"account_file": probe["account_file"]} if probe.get("account_file")
                        else {"selection": probe["selection"]} if probe.get("selection") else None)
        else:
            previous = probe.get("identity_detail")
        return ResolveContext(
            http=self.http, control=control, task_dir=self._task_dir(task_id),
            ffprobe=Path(self.ffprobe) if probing and Path(self.ffprobe).is_file() else None,
            previous=previous,
            log=lambda line: self.store.append_log(task_id, attempt, [mask_line(line)]),
            notice=lambda code, message: self._event(task, code, mask_line(message)[:500], level="WARNING"),
        )

    def _note_hang(self, task: dict[str, Any], error: BaseException) -> None:
        """A hidden run whose driver had to be ended: its phase and where it waited (module:function:line only)."""
        hang = getattr(error, "hang", None)
        if hang is None:
            return
        phase = str(getattr(hang, "phase", "") or "?")[:40]
        frames = [str(frame)[:160] for frame in tuple(getattr(hang, "stack", ()) or ())[:MAX_HANG_FRAMES]]
        self._event(task, "SOURCE_RUN_HUNG", f"Trình duyệt ẩn của nguồn bị treo ở bước {phase}; BiliFlow đã kết thúc "
                    "đúng tiến trình của lượt này.", level="WARNING", payload={"phase": phase, "at": frames})

    def _probe_source(self, task: dict[str, Any], provider: SourceProvider, control: ProcessControl) -> Any:
        task_id = task["id"]
        token = self.tickets.begin()  # a revoke from now on (cancel, remove, retry…) refuses this probe's link
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
        except SourceNeedsEpisodes as needs:
            if control.requested:
                return self._end_requested(task_id, control)
            return self._needs_episodes(task, provider, needs.listing)
        except SourceLoginRequired as needed:
            if control.requested:
                return self._end_requested(task_id, control)
            return self._login_needed(task, {"PROBING"}, provider, needed)
        except (SourceError, HttpError) as error:
            self._note_hang(task, error)
            if control.requested or isinstance(error, Cancelled):
                return self._end_requested(task_id, control)
            return self._fail(task_id, {"PROBING"}, error.code, error.message)
        if control.requested:
            return self._end_requested(task_id, control)
        self._log_marks(task, "Thăm dò link tải", source)
        self._event(task, "SOURCE_RESOLVED", describe_source(source))
        entry = ProbeEntry(
            index=task.get("chosen_entry") or 0, title=source.title[:300] or f"video-{task_id}", duration_seconds=source.duration_seconds,
            estimated_bytes=source.estimated_bytes, video_id=None, live_status=None, drm=False, expected_files=1,
            video_codec=source.video_codec, audio_codec=source.audio_codec, height=source.height,
        )
        page = {"extractor": None, "page_title": source.title[:300], "entry_count": 1, **source.public()}
        moved = self._ready(task_id, {"PROBING"}, entry, [entry.as_dict()], page, "WAITING_SPACE")
        if moved is not None:  # kept only once the task really moved on (a source account's file only)
            self.tickets.put(task_id, moved["attempt"], source, token, fresh=True)
        return moved

    def _log_marks(self, task: dict[str, Any], what: str, source: ResolvedSource) -> None:
        """The version marks a strict file link's answer carried (a source account's file), in fixed words only:
        kinds, never a value, a hash, a link or a host (download_media_file.answer_marks)."""
        plan = source.plan
        if isinstance(plan, FilePlan) and plan.strict_versions:
            self.store.append_log(task["id"], task["attempt"], [
                f"{what}: {answer_marks(plan.etag_kind, plan.has_modified, plan.validator)}."])

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

    def _fresh_kept(self, task: dict[str, Any], provider: SourceProvider, control: ProcessControl, token: int,
                    began: int) -> ResolvedSource:
        """``_fresh_source``, kept for the task (download_account_tickets; ``token``/``began`` as for ``put``). A
        source account's link whose session changed while it was fetched (Ngắt kết nối, a new sign-in: the cache
        refuses it) is never used: it is fetched once more, and that resolve meets the sign-in gate
        (SourceLoginRequired) or gets the new session's ticket. Changed again: INTERRUPTED (ACCOUNT_STATE_ERROR)."""
        for again in (False, True):
            source = self._fresh_source(task, provider, control)
            if source.issuer is None or self.tickets.put(task["id"], task["attempt"], source, token, fresh=False,
                                                         began=began):
                return source
            if control.requested:
                raise Cancelled()
            if again:
                break
            self.store.append_log(task["id"], task["attempt"], ["Phiên của nguồn đổi trong lúc lấy link tải; không "
                                                                "dùng link vừa lấy, lấy lại."])
            began = self.tickets.begin()
        raise SourceError("ACCOUNT_STATE_ERROR", "Phiên của tài khoản nguồn đổi trong lúc lấy link tải; bấm Tiếp tục "
                          "để thử lại.")

    def _kept_source(self, task: dict[str, Any], provider: SourceProvider, control: ProcessControl,
                     token: int) -> Any:
        """The link kept for this task (download_account_tickets) when it may serve this run, else None (a new
        ticket follows), or RUN_ENDED when the run ended here (a stop, a network error: INTERRUPTED, part kept).

        It must be the same task attempt, probed file, manager, source and session generation (read from the
        account's row: a disconnect, a new sign-in, a rejected or expired session never matches). The probe's own
        result serves the first transfer as it is; any later run checks the link first with the bounded HTTP probe
        (``recheck``: no browser, no ticket, through the same cookie-free client and checks)."""
        manager = getattr(provider, "manager", None)
        recheck = getattr(provider, "recheck", None)
        if manager is None or recheck is None:
            return None
        task_id, attempt = task["id"], task["attempt"]
        identity = (task.get("probe") or {}).get("identity") or ""
        kept = self.tickets.take(task_id, attempt=attempt, identity=identity, owner=manager.owner,
                                 source_id=provider.id, generation=provider.usable_generation())
        if kept is None:
            return None
        if kept.fresh:
            self._event(task, "DOWNLOADING", "Dùng link tải vừa lấy lúc thăm dò; bắt đầu tải (không lấy vé lần hai).")
            return kept.source
        self._event(task, "DOWNLOADING", "Kiểm lại link tải còn giữ của lượt này (một yêu cầu tới máy chủ file; không "
                    "mở trình duyệt, không lấy vé mới).")
        try:
            source = recheck(kept.source, self._source_context(task, control, probing=False))
        except (SourceError, HttpError) as error:
            if control.requested or isinstance(error, Cancelled):
                self._end_requested(task_id, control)
                return RUN_ENDED
            if isinstance(error, HttpError) and resumable_error(error):
                if error.status is not None:  # the server answered (429, 5xx): the next Tiếp tục gets a new ticket
                    self.tickets.discard(task_id)
                self._interrupt(task_id, error.code, error.message)  # no answer at all: the link stays kept
                return RUN_ENDED
            self.tickets.discard(task_id)  # refused (401/403…) or no longer the video: one new ticket
            self.store.append_log(task_id, attempt, [f"Link tải đã giữ không còn dùng được ({error.code}); lấy vé mới."])
            return None
        self._log_marks(task, "Kiểm lại link đã giữ", source)
        if source.identity_key != identity:  # another size than the probe's: the new ticket's resolve decides
            self.tickets.discard(task_id)
            self.store.append_log(task_id, attempt, ["Link tải đã giữ trả về file khác dung lượng lúc thăm dò; lấy vé "
                                                     "mới."])
            return None
        # The check took a request: the session may have changed (Ngắt kết nối, a new sign-in, its age) meanwhile.
        issued = kept.source.issuer.generation if kept.source.issuer is not None else None
        if provider.usable_generation() != issued or not self.tickets.put(task_id, attempt, source, token, fresh=False):
            self.tickets.discard(task_id)
            if control.requested:
                self._end_requested(task_id, control)
                return RUN_ENDED
            self.store.append_log(task_id, attempt, ["Phiên của nguồn đổi trong lúc kiểm lại link tải đã giữ; không "
                                                     "dùng link đó."])
            return None
        return source

    def _interrupt(self, task_id: int, code: str | None, message: str | None) -> None:
        text = f"{message or 'Lỗi mạng.'} Phần đã tải được giữ; {RESUME_NOTE}"
        moved = self.store.transition(task_id, {"DOWNLOADING"}, "INTERRUPTED", error_code=code, error_message=text,
                                      speed=None, eta=None, pid=None, pid_created=None)
        if moved:
            self._event(moved, "INTERRUPTED", text, level="WARNING", payload={"code": code})
        return None

    def _download_source(self, task: dict[str, Any], control: ProcessControl) -> dict[str, Any] | None:
        task_id, attempt = task["id"], task["attempt"]
        probe = task.get("probe") or {}
        finished = finished_media(self._task_dir(task_id), probe.get("identity") or "")
        if finished is not None and not self.input_dir.is_dir():  # still no input: say so before a long new check
            return self._no_input(task_id, {"DOWNLOADING"})
        if finished is not None:  # stopped while it was checked or moved: no link, no provider needed
            self._event(task, "DOWNLOADING", f"Dùng lại {finished.name} đã tải xong ở lần trước; kiểm tra lại.")
            moved = self.store.transition(task_id, {"DOWNLOADING"}, "VERIFYING", temp_file=str(finished),
                                          speed=None, eta=None)
            if moved is not None:
                moved["reused_finished"] = True  # _verify: still the bytes an earlier check passed
            return moved
        provider = self.sources.get(probe.get("provider"))
        if provider is None:
            return self._fail(task_id, {"DOWNLOADING"}, "PROVIDER_MISSING",
                              "Bộ đọc nguồn dùng lúc thăm dò không còn; bấm Thử lại từ đầu.")
        token = self.tickets.begin()  # a revoke from now on refuses every link this run would keep
        source = self._kept_source(task, provider, control, token)
        if source is RUN_ENDED:
            return None
        if source is None:
            # No separate link check here: every request of the transfer passes the same policy (SafeHttp), and
            # a failed lookup (the network is down) then ends INTERRUPTED, never FAILED with the parts lost.
            self._event(task, "DOWNLOADING", "Lấy lại nguồn mới rồi bắt đầu tải.")
            # Shown until the transfer writes its own progress: a source account's fresh link takes a hidden
            # browser run (about half a minute), with no byte moving meanwhile.
            self.store.update_progress(task_id, attempt, downloaded_bytes=int(task.get("downloaded_bytes") or 0),
                                       total_bytes=task.get("total_bytes"), speed=None, eta=None,
                                       progress_basis=task.get("progress_basis"),
                                       fragments_done=task.get("fragments_done"),
                                       fragments_total=task.get("fragments_total"), transfer_stage="resolving")
            try:
                source = self._fresh_kept(task, provider, control, token, token)
            except SourceLoginRequired as needed:
                if control.requested:
                    return self._end_requested(task_id, control)
                return self._login_needed(task, {"DOWNLOADING"}, provider, needed)
            except (SourceError, HttpError) as error:
                self._note_hang(task, error)
                if control.requested or isinstance(error, Cancelled):
                    return self._end_requested(task_id, control)
                if resumable_error(error):  # e.g. a network error, or a hidden browser that did not run: part kept
                    return self._interrupt(task_id, error.code, error.message)
                return self._fail(task_id, {"DOWNLOADING"}, error.code, error.message)
        login: list[SourceLoginRequired] = []

        def refresh() -> ResolvedSource:
            """A fresh ticket in the middle of the transfer; a sign-in it needs is kept for the outcome. The kept
            link is the refused one: it goes first, and the fresh one replaces it once it is there."""
            self.tickets.discard(task_id)
            began = self.tickets.begin()  # a sign-in before this refresh does not refuse its link
            try:
                return self._fresh_kept(task, provider, control, token, began)
            except SourceLoginRequired as needed:
                login.append(needed)
                raise
            except Exception as error:
                self._note_hang(task, error)
                raise

        outcome = self.transfers.download(
            source, refresh, self._task_dir(task_id), control,
            on_progress=self._progress_writer(task_id, attempt),
            on_log=lambda lines: self.store.append_log(task_id, attempt, lines),
            on_start=self._pid_recorder(task_id), guard=self._size_guard(task),
        )
        self.store.update_fields(task_id, pid=None, pid_created=None)
        if control.requested:
            return self._end_requested(task_id, control)
        if not outcome.ok:
            if login:  # the part stays; the task waits for the user's sign-in, then continues it
                return self._login_needed(task, {"DOWNLOADING"}, provider, login[-1])
            if outcome.resumable:
                if outcome.code == "SERVER_BUSY":  # the file server answered 429/5xx: never pin that link
                    self.tickets.discard(task_id)
                return self._interrupt(task_id, outcome.code, outcome.message)
            return self._fail(task_id, {"DOWNLOADING"}, outcome.code, outcome.message)
        return self.store.transition(task_id, {"DOWNLOADING"}, "VERIFYING", temp_file=str(outcome.final_path),
                                     speed=None, eta=None)
