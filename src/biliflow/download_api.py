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

Token, Host and (phone) Origin checks stay in the Control Center handler.
"""
from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from biliflow.download_links import DownloadBatchError
from biliflow.download_runner import mask_line
from biliflow.download_source_types import is_volatile_query_name
from biliflow.download_store import DownloadStore
from biliflow.download_worker import MAX_SLOTS, DownloadActionError, DownloadWorker
from biliflow.storage_summary import BinReader, Cleanable, StorageSummaryCache

TASK_ACTION = re.compile(r"/api/downloads/(\d+)/(rename|choose|stop|resume|cancel|retry|remove)")
TASK_DETAIL = re.compile(r"/api/downloads/(\d+)")
# Every route as a regex; tests/test_dashboard_v2_phone_hardening.py classifies each POST for the phone
# and tests/test_dashboard_v2_contract.py matches the Dashboard V2 contract against both lists.
GET_ROUTES = (r"/api/downloads", TASK_DETAIL.pattern, r"/api/storage-summary")
POST_ROUTES = (r"/api/downloads", r"/api/downloads/settings", r"/api/downloads/cleanup-temp",
               TASK_ACTION.pattern)
UNAVAILABLE_MESSAGE = "Tính năng tải video chưa sẵn sàng: {error}"
_PUBLIC_FIELDS = (
    "id", "url", "state", "attempt", "desired_name", "original_title", "duration_seconds",
    "estimated_bytes", "downloaded_bytes", "total_bytes", "speed", "eta", "error_code", "error_message",
    "chosen_entry", "name_locked", "created_at", "state_since", "finished_at", "queued_at",
    "progress_basis", "fragments_done", "fragments_total", "transfer_stage",
)

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


def public_task(task: dict[str, Any], scrub: Callable[[Any], Any] = lambda value: value) -> dict[str, Any]:
    """What a page needs; never the temp folder, the full local output path or a token of the link."""
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
    return item


class DownloadService:
    def __init__(self, root: Path, *, cleanable: Cleanable, bin_reader: BinReader | None,
                 store: DownloadStore | None = None, worker: DownloadWorker | None = None,
                 storage: StorageSummaryCache | None = None):
        self.root = root
        self.store = store or DownloadStore(root / "state" / "downloads.sqlite3")
        self.worker = worker or DownloadWorker(root, self.store)
        self.storage = storage or StorageSummaryCache(root, cleanable=cleanable, bin_reader=bin_reader)
        self.start_error: str | None = None
        self.recovered: dict[str, int] = {}
        self.scrub = path_scrubber(root)

    def _scrub_tree(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._scrub_tree(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._scrub_tree(item) for item in value]
        return self.scrub(value)

    def _public(self, task: dict[str, Any]) -> dict[str, Any]:
        return public_task(task, self.scrub)

    def start(self) -> None:
        try:
            self.recovered = self.worker.start()
        except Exception as error:  # noqa: BLE001 - the dashboard shows it; scans keep running
            self.start_error = f"{type(error).__name__}: {error}"

    def stop(self) -> None:
        try:
            self.worker.shutdown()
        finally:
            self.store.close()

    def snapshot(self) -> dict[str, Any]:
        tasks = [self._public(task) for task in self.store.list_tasks()]
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
        }

    def task_detail(self, task_id: int) -> Response:
        task = self.store.get(task_id)
        if task is None:
            return 404, {"error": "Không thấy lượt tải."}
        return 200, {"task": self._public(task), "events": self._scrub_tree(self.store.events(task_id)),
                     "log": [self.scrub(line) for line in self.store.log_lines(task_id)]}

    def handle_get(self, path: str, query: str) -> Response | None:
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
        except DownloadActionError as error:
            return error.status, {"error": self.scrub(str(error))}

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
