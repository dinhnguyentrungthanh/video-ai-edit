"""HTTP layer of the video downloader, called by the Control Center handler.

GET  /api/downloads                       snapshot (tasks, slots, space, temp)
GET  /api/downloads/<id>                  one task with its events and masked log
GET  /api/storage-summary[?refresh=1]     the read-only "Dung lượng" panel (5-minute cache)
POST /api/downloads                       {urls[], rights_confirmed: true}
POST /api/downloads/<id>/rename           {name}
POST /api/downloads/<id>/choose           {entry_index}
POST /api/downloads/<id>/<action>         stop | resume | cancel | retry | remove
POST /api/downloads/settings              {slots: 1..3}
POST /api/downloads/cleanup-temp          {confirm: true, ids?: [task ids the confirmation listed]}

Source accounts (docs/SOURCE_ACCOUNTS_PLAN.md 9.14; the phone may use these like the other download actions):
GET  /api/downloads/<id>/episodes                 the stored episode list of a series page, its draft and plan
POST /api/downloads/<id>/episodes/draft           {selection, fingerprint, revision}
POST /api/downloads/<id>/episodes/confirm         {selection, fingerprint, idempotency_key, confirm_scope?,
                                                   skip_existing?}  ("Tải N tập")
GET  /api/downloads/groups/<group id>             a group and its members in order
POST /api/downloads/groups/<group id>/<action>    stop | resume | cancel | retry | remove
The sign-in routes of the accounts are PC only and live in download_account_api (/api/download-accounts/…);
their status is the snapshot's ``accounts``.

Token, Host and (phone) Origin checks stay in the Control Center handler.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from biliflow.download_account_api import AccountRuntime
from biliflow.download_groups import GroupError
from biliflow.download_links import DownloadBatchError
from biliflow.download_runner import mask_line
from biliflow.download_source_types import is_volatile_query_name
from biliflow.download_sources import default_registry
from biliflow.download_store import DownloadStore
from biliflow.download_worker import MAX_SLOTS, DownloadActionError, DownloadWorker
from biliflow.storage_summary import BinReader, Cleanable, StorageSummaryCache

TASK_ACTION = re.compile(r"/api/downloads/(\d+)/(rename|choose|stop|resume|cancel|retry|remove)")
TASK_DETAIL = re.compile(r"/api/downloads/(\d+)")
EPISODES = re.compile(r"/api/downloads/(\d+)/episodes")
EPISODE_ACTION = re.compile(r"/api/downloads/(\d+)/episodes/(draft|confirm)")
GROUP_DETAIL = re.compile(r"/api/downloads/groups/(\d+)")
GROUP_ACTION = re.compile(r"/api/downloads/groups/(\d+)/(stop|resume|cancel|retry|remove)")
# Every route as a regex; tests/test_dashboard_v2_phone_hardening.py classifies each POST for the phone
# and tests/test_dashboard_v2_contract.py matches the Dashboard V2 contract against both lists.
GET_ROUTES = (r"/api/downloads", TASK_DETAIL.pattern, r"/api/storage-summary", EPISODES.pattern,
              GROUP_DETAIL.pattern)
POST_ROUTES = (r"/api/downloads", r"/api/downloads/settings", r"/api/downloads/cleanup-temp",
               TASK_ACTION.pattern, EPISODE_ACTION.pattern, GROUP_ACTION.pattern)
UNAVAILABLE_MESSAGE = "Tính năng tải video chưa sẵn sàng: {error}"
CLOSE_POLL_SECONDS = 0.5  # after a stop that timed out: how often the closer checks that the last thread ended
_PUBLIC_FIELDS = (
    "id", "url", "state", "attempt", "desired_name", "original_title", "duration_seconds",
    "estimated_bytes", "downloaded_bytes", "total_bytes", "speed", "eta", "error_code", "error_message",
    "chosen_entry", "name_locked", "created_at", "state_since", "finished_at", "queued_at",
    "progress_basis", "fragments_done", "fragments_total", "transfer_stage", "group_id", "login_source",
    "login_reason",
)
_MEMBER_FIELDS = ("id", "group_id", "ordinal", "season_number", "season_label", "episode_number", "episode_label",
                  "special", "variant_label", "code", "status", "task_id", "last_state", "task_state",
                  "downloaded_bytes", "total_bytes", "estimated_bytes", "output_size", "desired_name", "error_code",
                  "error_message", "login_source", "login_reason")

Response = tuple[int, Any]


MAX_ID_DIGITS = 12  # a longer id is not a task (and would overflow SQLite): 404
# A path part that looks like a token: long hex, or 32+ characters mixing upper case, lower case and digits
# (a slug such as "episode-12-final-cut" is kept).
_TOKEN_PART = re.compile(r"[0-9a-fA-F]{32,}|(?=[^/]*[A-Z])(?=[^/]*[a-z])(?=[^/]*[0-9])[A-Za-z0-9_\-+=~.]{32,}")
MAX_CLEANUP_IDS = 1000


def owns(path: str) -> bool:
    """True for the paths this module answers (GET or POST)."""
    return path in ("/api/downloads", "/api/storage-summary") or path.startswith("/api/downloads/")


def path_scrubber(root: Path) -> Callable[[Any], Any]:
    """Replace the install folder in free text (errors, events, yt-dlp log lines) with <BiliFlow>.

    The pages (also the phone) never need the local path; yt-dlp and Windows errors print it.
    """
    spellings = {str(root), str(root).replace("\\", "/"), str(root.resolve()), str(root.resolve()).replace("\\", "/")}
    pattern = re.compile("|".join(re.escape(item) for item in sorted(spellings, key=len, reverse=True)), re.IGNORECASE)

    def scrub(value: Any) -> Any:
        return pattern.sub("<BiliFlow>", value) if isinstance(value, str) else value
    return scrub


def public_url(url: str) -> str:
    """The pasted link as a page (also the phone) shows it: no account part or fragment, and the values of
    signature-like query names and token-like path parts replaced by ***. The database keeps the link."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return mask_line(url)
    path = "/".join("***" if _TOKEN_PART.fullmatch(piece) else piece for piece in parts.path.split("/"))
    pairs = []
    for pair in parts.query.split("&") if parts.query else []:
        name, sep, _value = pair.partition("=")
        pairs.append(f"{name}=***" if sep and is_volatile_query_name(urllib.parse.unquote(name)) else pair)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], path, "&".join(pairs), ""))


def public_task(task: dict[str, Any], scrub: Callable[[Any], Any] = lambda value: value, *,
                place: dict[str, Any] | None = None, preview: dict[str, Any] | None = None) -> dict[str, Any]:
    """What a page needs; never the temp folder, the full local output path or a token of the link. ``place``:
    an episode's place in its group (ordinal, code, planned name); ``preview``: the summary of a series page's
    stored episode list (the list itself is ``GET …/episodes``)."""
    item = {name: scrub(task.get(name)) for name in _PUBLIC_FIELDS}
    item["url"] = public_url(task["url"]) if task.get("url") else task.get("url")
    item["title"] = task.get("desired_name") or task.get("original_title")
    item["output_name"] = Path(task["output_path"]).name if task.get("output_path") else None
    item["entries"] = task.get("entries") if task.get("state") == "NEEDS_CHOICE" else None
    item["entry_count"] = len(task.get("entries") or [])
    probe, verify = task.get("probe") or {}, task.get("verify") or {}
    item["media"] = {
        "extractor": probe.get("extractor"), "height": verify.get("height") or probe.get("height"),
        "video_codec": verify.get("video_codec") or probe.get("video_codec"),
        "audio_codec": verify.get("audio_codec") or probe.get("audio_codec"),
        # A source provider's public part (download_source_types.ResolvedSource.public); None for yt-dlp.
        "provider": probe.get("provider"), "transport": probe.get("transport"),
        "source_label": probe.get("source_label"),
    }
    item["choice_kind"] = probe.get("choice_kind") if task.get("state") == "NEEDS_CHOICE" else None
    item["episodes"] = preview if task.get("state") == "NEEDS_CHOICE" else None
    item["group"] = place
    return item


def public_member(member: dict[str, Any], scrub: Callable[[Any], Any] = lambda value: value) -> dict[str, Any]:
    """One episode of a group for a page: its place, labels, state and progress (ids of the source only)."""
    item = {name: scrub(member.get(name)) for name in _MEMBER_FIELDS}
    item["special"] = bool(member.get("special"))
    return item


def _state_error(error: BaseException) -> Response:
    """A database or disk error of a download route: a fixed message and the error's kind, never its text (it
    can name a table, a constraint or a local path), on the PC as on the phone."""
    if isinstance(error, sqlite3.Error):
        return 503, {"error": "Bộ tải chưa đọc/ghi được cơ sở dữ liệu (đang bận hoặc lỗi); thử lại sau.",
                     "code": "DOWNLOAD_STATE_ERROR", "kind": type(error).__name__}
    return 500, {"error": "Bộ tải gặp lỗi ổ đĩa hoặc tệp; thử lại sau.", "code": "DOWNLOAD_DISK_ERROR",
                 "kind": type(error).__name__}


class DownloadService:
    def __init__(self, root: Path, *, cleanable: Cleanable, bin_reader: BinReader | None,
                 store: DownloadStore | None = None, worker: DownloadWorker | None = None,
                 storage: StorageSummaryCache | None = None, accounts: AccountRuntime | None = None):
        self.root = root
        self.store = store or DownloadStore(root / "state" / "downloads.sqlite3")
        # Source accounts: the root's manager (none without a configured source) and the sign-in windows; the
        # worker's registry hands every link of an account source to its provider with that manager.
        self.accounts = accounts if accounts is not None or worker is not None else AccountRuntime(root)
        if worker is None:
            manager = self.accounts.manager if self.accounts is not None else None
            worker = DownloadWorker(root, self.store, sources=default_registry(root, accounts=manager))
        self.worker = worker
        if self.accounts is not None:
            self.accounts.attach(wake=self.worker.wake_logins, registry=self.worker.sources,
                                 waiting=self._waiting_logins,
                                 forget=getattr(self.worker, "forget_source_tickets", None))
        self.storage = storage or StorageSummaryCache(root, cleanable=cleanable, bin_reader=bin_reader)
        self.start_error: str | None = None
        self.recovered: dict[str, int] = {}
        self.scrub = path_scrubber(root)
        self.close_error: str | None = None
        self._stop_lock = threading.Lock()  # one stop at a time; a repeated stop only checks the close
        self._stop_started = False
        self._close_guard = threading.Lock()
        self._closed = threading.Event()
        self._closer: threading.Thread | None = None

    def _scrub_tree(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._scrub_tree(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._scrub_tree(item) for item in value]
        return self.scrub(value)

    def _public(self, task: dict[str, Any]) -> dict[str, Any]:
        return public_task(task, self.scrub)

    def start(self) -> None:
        """The accounts settle first (interrupted sign-ins, left browser profiles), then the worker recovers its
        tasks and starts dispatching. Nothing opens a sign-in window."""
        try:
            if self.accounts is not None:
                self.accounts.start()
            self.recovered = self.worker.start()
        except Exception as error:  # noqa: BLE001 - the dashboard shows it; scans keep running
            self.start_error = f"{type(error).__name__}: {error}"

    def stop(self) -> None:
        """Bounded: the worker stopped first (nothing new starts, its hidden runs end), then no new sign-in and the
        open windows closed, each step even when the other failed. The account manager and the store are closed
        only when no task thread, dispatch loop or sign-in window of this service can still use them: at once
        when every one ended in time, else by one closer thread as soon as the last one ends (``close_when_idle``,
        never forced). A repeated or concurrent stop waits for the first one and never closes anything twice."""
        with self._stop_lock:
            if self._stop_started:
                self.close_when_idle()
                return
            self._stop_started = True
            try:
                try:
                    self.worker.shutdown()
                finally:
                    if self.accounts is not None:
                        self.accounts.stop()
            finally:
                self._close_now_or_later()

    def _idle(self) -> bool:
        return self.worker.finished() and (self.accounts is None or not self.accounts.busy())

    def close_when_idle(self) -> bool:
        """The final close after ``stop``, idempotent: this service's account manager, then its store, once, and
        only when nothing of it can still use them. True once they are closed."""
        if self._closed.is_set():
            return True
        if not self._stop_started or not self._idle():
            return False
        with self._close_guard:
            if self._closed.is_set():
                return True
            try:
                try:
                    if self.accounts is not None:
                        self.accounts.close()
                finally:
                    self.store.close()
            finally:
                self._closed.set()
        return True

    def wait_closed(self, timeout: float) -> bool:
        return self._closed.wait(timeout)

    def _close_now_or_later(self) -> None:
        if self.close_when_idle():
            return
        with self._close_guard:
            if self._closer is None and not self._closed.is_set():
                self._closer = threading.Thread(target=self._close_later, name="biliflow-download-close",
                                                daemon=True)
                self._closer.start()

    def _close_later(self) -> None:
        """A task, the dispatch loop or a sign-in window outlived the bounded stop: wait for it, then close."""
        while not self._closed.wait(CLOSE_POLL_SECONDS):
            try:
                if self.close_when_idle():
                    return
            except Exception as error:  # noqa: BLE001 - kept; a close that began still closed the store (finally)
                self.close_error = f"{type(error).__name__}: {error}"
                return

    def _waiting_logins(self) -> dict[str, int]:
        """Per source, the tasks waiting for a sign-in that this Windows account's session can wake: a task added
        under another account waits for that account's own session (download_account_tasks), so it is not counted."""
        manager = self.accounts.manager if self.accounts is not None else None
        owner = manager.account_sid if manager is not None else None
        counts: Counter[str] = Counter(task.get("login_source") or "" for task in self.store.tasks_in({"WAITING_LOGIN"})
                                       if owner is None or task.get("account_owner") == owner)
        return dict(counts)

    def snapshot(self) -> dict[str, Any]:
        groups = self.worker.groups
        places, previews = groups.names_by_task(), groups.preview_summaries()
        tasks = [public_task(task, self.scrub, place=places.get(task["id"]), preview=previews.get(task["id"]))
                 for task in self.store.list_tasks()]
        try:
            free, reserve = self.worker.space_probe(self.root)
            space: dict[str, Any] = {"free_bytes": free, "reserve_bytes": reserve}
        except OSError as error:
            space = {"error": str(error)}
        return {
            "tasks": tasks,
            "counts": dict(Counter(task["state"] for task in tasks)),
            "settings": {"slots": self.worker.slots(), "max_slots": MAX_SLOTS},
            "space": self._scrub_tree(space),
            "temp": self.worker.temp_summary(),
            "running": sorted(self.worker.running_ids()),
            "worker_error": self.scrub(self.start_error or self.worker.last_error),
            "worker_error_at": None if self.start_error else self.worker.last_error_at,
            "groups": self._scrub_tree(groups.summaries()),
            "accounts": self._scrub_tree(self.accounts.status()) if self.accounts is not None else None,
        }

    def task_detail(self, task_id: int) -> Response:
        task = self.store.get(task_id)
        if task is None:
            return 404, {"error": "Không thấy lượt tải."}
        return 200, {"task": self._public(task), "events": self._scrub_tree(self.store.events(task_id)),
                     "log": [self.scrub(line) for line in self.store.log_lines(task_id)]}

    def handle_get(self, path: str, query: str) -> Response | None:
        try:
            return self._get(path, query)
        except GroupError as error:
            return error.status, self._scrub_tree(error.public())
        except DownloadActionError as error:
            return error.status, {"error": self.scrub(str(error))}
        except (sqlite3.Error, OSError) as error:
            return _state_error(error)

    def _get(self, path: str, query: str) -> Response | None:
        if match := EPISODES.fullmatch(path):
            if len(match.group(1)) > MAX_ID_DIGITS:
                return 404, {"error": "Không thấy lượt tải."}
            return 200, self._scrub_tree(self.worker.episodes(int(match.group(1))))
        if match := GROUP_DETAIL.fullmatch(path):
            if len(match.group(1)) > MAX_ID_DIGITS:
                return 404, {"error": "Không thấy nhóm tập."}
            detail = self.worker.group_detail(int(match.group(1)))
            return 200, {"group": self._scrub_tree(detail["group"]),
                         "members": [public_member(member, self.scrub) for member in detail["members"]]}
        if path == "/api/downloads":
            return 200, self.snapshot()
        if path == "/api/storage-summary":
            refresh = urllib.parse.parse_qs(query).get("refresh", [""])[0] == "1"
            return 200, self._scrub_tree(self.storage.get(refresh=refresh))
        if match := TASK_DETAIL.fullmatch(path):
            if len(match.group(1)) > MAX_ID_DIGITS:
                return 404, {"error": "Không thấy lượt tải."}
            return self.task_detail(int(match.group(1)))
        return None

    def handle_post(self, path: str, body: dict[str, Any]) -> Response | None:
        try:
            return self._post(path, body)
        except DownloadBatchError as error:
            return 400, {"error": str(error), "code": "BATCH_REJECTED", "errors": error.errors}
        except GroupError as error:
            return error.status, self._scrub_tree(error.public())
        except DownloadActionError as error:
            return error.status, {"error": self.scrub(str(error))}
        except (sqlite3.Error, OSError) as error:
            return _state_error(error)

    def _post(self, path: str, body: dict[str, Any]) -> Response | None:
        worker = self.worker
        if path == "/api/downloads":
            urls = body.get("urls")
            if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
                return 400, {"error": "urls phải là danh sách link."}
            tasks = worker.add(urls, rights_confirmed=body.get("rights_confirmed") is True)
            return 200, {"tasks": [self._public(task) for task in tasks]}
        if path == "/api/downloads/settings":
            return 200, {"slots": worker.set_slots(body.get("slots"))}
        if path == "/api/downloads/cleanup-temp":
            if body.get("confirm") is not True:
                return 400, {"error": "Cần xác nhận trước khi dọn file tạm."}
            ids = body.get("ids")
            if ids is not None and not (isinstance(ids, list) and len(ids) <= MAX_CLEANUP_IDS and all(
                    type(item) is int and 0 < item < 10 ** MAX_ID_DIGITS for item in ids)):
                return 400, {"error": "ids phải là danh sách id lượt tải."}
            return 200, worker.cleanup_temp(ids)
        if match := EPISODE_ACTION.fullmatch(path):
            if len(match.group(1)) > MAX_ID_DIGITS:
                return 404, {"error": "Không thấy lượt tải."}
            task_id = int(match.group(1))
            if match.group(2) == "draft":
                return 200, self._scrub_tree(worker.save_episode_draft(task_id, body))
            return 200, self._scrub_tree(worker.confirm_episodes(task_id, body))
        if match := GROUP_ACTION.fullmatch(path):
            if len(match.group(1)) > MAX_ID_DIGITS:
                return 404, {"error": "Không thấy nhóm tập."}
            return 200, self._scrub_tree(worker.group_action(int(match.group(1)), match.group(2)))
        match = TASK_ACTION.fullmatch(path)
        if match is None:
            return None
        if len(match.group(1)) > MAX_ID_DIGITS:
            return 404, {"error": "Không thấy lượt tải."}
        task_id, action = int(match.group(1)), match.group(2)
        handlers: dict[str, Callable[[], Any]] = {
            "rename": lambda: worker.rename(task_id, body.get("name")),
            "choose": lambda: worker.choose(task_id, body.get("entry_index")),
            "stop": lambda: worker.stop(task_id),
            "resume": lambda: worker.resume(task_id),
            "cancel": lambda: worker.cancel(task_id),
            "retry": lambda: worker.retry(task_id),
            "remove": lambda: worker.remove(task_id),
        }
        result = handlers[action]()
        return 200, result if action == "remove" else {"task": self._public(result)}
