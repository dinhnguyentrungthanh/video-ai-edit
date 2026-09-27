from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

GIB = 1024**3


@dataclass(frozen=True)
class StorageStatus:
    total_gb: float
    used_gb: float
    free_gb: float
    free_percent: float
    state: str
    required_free_gb: float

    def as_dict(self) -> dict:
        return asdict(self)


def storage_status(
    root: Path,
    warning_free_gb: float = 200,
    pause_free_gb: float = 150,
    hard_stop_free_gb: float = 100,
    minimum_free_percent: float = 20,
) -> StorageStatus:
    usage = shutil.disk_usage(root)
    total_gb = usage.total / GIB
    free_gb = usage.free / GIB
    free_percent = usage.free * 100 / usage.total
    required_free_gb = max(hard_stop_free_gb, total_gb * minimum_free_percent / 100)

    if free_gb < required_free_gb:
        state = "HARD_STOP"
    elif free_gb < pause_free_gb:
        state = "PAUSE_NEW_JOBS"
    elif free_gb < warning_free_gb:
        state = "WARNING"
    else:
        state = "OK"
    return StorageStatus(total_gb, (usage.total - usage.free) / GIB, free_gb, free_percent, state, required_free_gb)


def require_capacity(root: Path, estimated_job_gb: float) -> StorageStatus:
    status = storage_status(root)
    remaining = status.free_gb - estimated_job_gb
    if status.state == "HARD_STOP" or remaining < status.required_free_gb:
        raise RuntimeError(
            f"Insufficient safe disk space: free={status.free_gb:.1f} GiB, "
            f"estimated_job={estimated_job_gb:.1f} GiB, "
            f"required_reserve={status.required_free_gb:.1f} GiB"
        )
    return status

