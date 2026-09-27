from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def project_resource_lock(
    project_root: Path, name: str, *, poll_seconds: float = 0.25,
) -> Iterator[None]:
    """Cross-process one-slot lock stored with the project on drive E."""
    if not name or poll_seconds <= 0:
        raise ValueError("Invalid project resource lock")
    state = project_root.resolve(strict=True) / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / f"{name}.lock"
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            while not locked:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                except OSError:
                    time.sleep(poll_seconds)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
