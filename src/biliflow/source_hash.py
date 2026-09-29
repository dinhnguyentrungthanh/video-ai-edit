"""SHA-256 of a source file computed on a background thread.

Scanners use it to overlap the ~20 s hash of a feature-length file with decoding
and inference. Callers must obtain ``result()`` (and compare it where an
expected value exists) before writing any artifact, so a changed source still
never produces a report or cache entry. The digest equals hashing the file
sequentially; chunking does not affect SHA-256.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

CHUNK_BYTES = 1024 * 1024


class BackgroundSha256:
    def __init__(self, path: Path):
        self._path = path
        self._stop = threading.Event()
        self._digest: str | None = None
        self._error: BaseException | None = None
        # Daemon: an abandoned hash never keeps a failed scan's process alive.
        self._thread = threading.Thread(target=self._run, name="source-sha256", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            digest = hashlib.sha256()
            with self._path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
                    if self._stop.is_set():
                        return
                    digest.update(chunk)
            self._digest = digest.hexdigest()
        except BaseException as error:  # re-raised to the caller by result()
            self._error = error

    def result(self) -> str:
        self._thread.join()
        if self._error is not None:
            raise self._error
        if self._digest is None:
            raise RuntimeError("Source hashing was cancelled")
        return self._digest

    def cancel(self) -> None:
        self._stop.set()
