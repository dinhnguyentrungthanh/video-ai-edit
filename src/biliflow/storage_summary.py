"""Read-only numbers of the "Dung lượng" panel.

Folder sizes, the drive's free space and reserve, how much of ``input`` the
existing "Dọn video gốc" action could clean, and the Recycle Bin of the
project drive. Nothing here deletes, moves or empties anything; cleaning stays
the user's existing actions. Sizes are computed in a background thread and
kept for five minutes.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from biliflow.download_files import tree_size
from biliflow.storage import GIB, storage_status

FOLDERS = ("input", "output", "reports", "cache", "temp")
CACHE_SECONDS = 300.0

Cleanable = Callable[[], tuple[int, int]]
BinReader = Callable[[Path], Any]


def compute_summary(root: Path, *, cleanable: Cleanable, bin_reader: BinReader | None) -> dict[str, Any]:
    status = storage_status(root)
    folders: dict[str, int | None] = {}
    for name in FOLDERS:
        try:
            folders[name] = tree_size(root / name)
        except OSError:
            folders[name] = None
    try:
        jobs, cleanable_bytes = cleanable()
        cleanable_part: dict[str, Any] = {"jobs": int(jobs), "bytes": int(cleanable_bytes)}
    except Exception as error:  # noqa: BLE001 - one bad job must not hide the panel
        cleanable_part = {"jobs": None, "bytes": None, "error": f"Không tính được: {error}"}
    recycle: dict[str, Any]
    if bin_reader is None:
        recycle = {"error": "Không đọc được Thùng rác trên máy này."}
    else:
        try:
            info = bin_reader(root)
            recycle = {"used_bytes": int(info.used_bytes), "items": int(info.items),
                       "max_bytes": int(info.max_bytes), "volume": str(info.volume)}
        except Exception as error:  # noqa: BLE001 - shown in the panel instead
            recycle = {"error": f"Không đọc được Thùng rác: {error}"}
    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "drive": {
            "total_bytes": int(status.total_gb * GIB),
            "free_bytes": int(status.free_gb * GIB),
            "reserve_bytes": int(status.required_free_gb * GIB),
            "state": status.state,
        },
        "folders": folders,
        "cleanable": cleanable_part,
        "recycle_bin": recycle,
    }


class StorageSummaryCache:
    """Serve the last summary at once; recompute in the background when older than 5 minutes."""

    def __init__(self, root: Path, *, cleanable: Cleanable, bin_reader: BinReader | None,
                 max_age: float = CACHE_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.root = root
        self.cleanable = cleanable
        self.bin_reader = bin_reader
        self.max_age = max_age
        self.clock = clock
        self._lock = threading.Lock()
        self._value: dict[str, Any] | None = None
        self._stamp: float | None = None
        self._worker: threading.Thread | None = None
        self._error: str | None = None
        self._again = False

    def get(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            stale = self._stamp is None or refresh or self.clock() - self._stamp >= self.max_age
            running = self._worker is not None and self._worker.is_alive()
            if stale and not running:
                self._start()
                running = True
            elif refresh and running:
                self._again = True  # the running round may have started before the user's change
            return {"summary": self._value, "computing": running, "error": self._error}

    def _start(self) -> None:
        self._worker = threading.Thread(target=self._compute, name="biliflow-storage-summary", daemon=True)
        self._worker.start()

    def wait(self, timeout: float = 30.0) -> None:
        """Until no round runs (a refresh asked meanwhile starts one more round)."""
        deadline = time.monotonic() + timeout
        while True:
            worker = self._worker
            if worker is None:
                return
            worker.join(max(0.0, deadline - time.monotonic()))
            if worker is self._worker or time.monotonic() >= deadline:
                return

    def _compute(self) -> None:
        try:
            value = compute_summary(self.root, cleanable=self.cleanable, bin_reader=self.bin_reader)
            error = None
        except Exception as caught:  # noqa: BLE001 - reported to the panel
            value, error = None, f"{type(caught).__name__}: {caught}"
        with self._lock:
            if value is not None:
                self._value = value
            self._error = error
            self._stamp = self.clock()
            if self._again:
                self._again = False
                self._start()
