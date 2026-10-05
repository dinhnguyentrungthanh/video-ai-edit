"""HTTP layer of the video downloader, called by the Control Center handler.

GET  /api/downloads                       snapshot (tasks, slots, sources, space, temp)
GET  /api/downloads/<id>                  one task with its events and masked log
GET  /api/storage-summary[?refresh=1]     the read-only "Dung lượng" panel (5-minute cache)
POST /api/downloads                       {source_id, urls[], rights_confirmed: true}
POST /api/downloads/<id>/rename           {name}
POST /api/downloads/<id>/choose           {entry_index}
POST /api/downloads/<id>/<action>         stop | resume | cancel | retry | remove
POST /api/downloads/settings              {slots: 1..3}
POST /api/downloads/cleanup-temp          {confirm: true}

Token, Host and (phone) Origin checks stay in the Control Center handler.
"""
from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from biliflow.download_sources import DownloadBatchError, DownloadSourceError, load_sources
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
    "id", "source_id", "url", "state", "attempt", "desired_name", "original_title", "duration_seconds",
    "estimated_bytes", "downloaded_bytes", "total_bytes", "speed", "eta", "error_code", "error_message",
    "chosen_entry", "name_locked", "created_at", "state_since", "finished_at", "queued_at",
)

Response = tuple[int, Any]


MAX_ID_DIGITS = 12  # a longer id is not a task (and would overflow SQLite): 404


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


def public_task(task: dict[str, Any], scrub: Callable[[Any], Any] = lambda value: value) -> dict[str, Any]:
    """What a page needs; never the temp folder or the full local output path."""
    item = {name: scrub(task.get(name)) for name in _PUBLIC_FIELDS}
    item["title"] = task.get("desired_name") or task.get("original_title")
    item["output_name"] = Path(task["output_path"]).name if task.get("output_path") else None
    item["entries"] = task.get("entries") if task.get("state") == "NEEDS_CHOICE" else None
    item["entry_count"] = len(task.get("entries") or [])
    probe, verify = task.get("probe") or {}, task.get("verify") or {}
    item["media"] = {
        "extractor": probe.get("extractor"), "height": verify.get("height") or probe.get("height"),
        "video_codec": verify.get("video_codec") or probe.get("video_codec"),
        "audio_codec": verify.get("audio_codec") or probe.get("audio_codec"),
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
        warnings: list[str] = []
        try:
            catalog = load_sources(self.root)
            sources = [{"id": item.id, "label": item.label, "domains": list(item.domains),
                        "local": item.local} for item in catalog.sources]
            warnings.extend(catalog.warnings)
        except DownloadSourceError as error:
            sources = []
            warnings.append(f"Danh sách nguồn lỗi: {error}")
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
            "sources": sources,
            "warnings": [self.scrub(item) for item in warnings],
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
        except DownloadSourceError as error:
            return 500, {"error": self.scrub(f"Danh sách nguồn lỗi: {error}")}

    def _post(self, path: str, body: dict[str, Any]) -> Response | None:
        worker = self.worker
        if path == "/api/downloads":
            urls = body.get("urls")
            if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
                return 400, {"error": "urls phải là danh sách link."}
            tasks = worker.add(str(body.get("source_id", "")), urls,
                               rights_confirmed=body.get("rights_confirmed") is True)
            return 200, {"tasks": [self._public(task) for task in tasks]}
        if path == "/api/downloads/settings":
            return 200, {"slots": worker.set_slots(body.get("slots"))}
        if path == "/api/downloads/cleanup-temp":
            if body.get("confirm") is not True:
                return 400, {"error": "Cần xác nhận trước khi dọn file tạm."}
            return 200, worker.cleanup_temp()
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
