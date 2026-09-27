from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


FILE_CACHE_POLICIES = {
    "cache/visual-logo": {
        "max_age": timedelta(days=30),
        "max_bytes": 4 * 1024**3,
    },
    "cache/ad_candidate_pipeline": {
        "max_age": timedelta(days=30),
        "max_bytes": 2 * 1024**3,
    },
}


def cleanup_candidates(project_root: Path, now: datetime | None = None) -> list[dict]:
    project_root = project_root.resolve(strict=True)
    now = now or datetime.now(timezone.utc)
    policies = {
        "temp": timedelta(hours=24),
        "work": timedelta(days=7),
        "previews": timedelta(days=30),
        # Routing caches are already gzip-compressed. They remain useful for
        # retries, then become dry-run cleanup candidates after one month.
        "cache/visual-logo": timedelta(days=30),
        "cache/ad_candidate_pipeline": timedelta(days=30),
        "cache/stage-results": timedelta(days=14),
    }
    candidates = []
    for directory, retention in policies.items():
        raw_base = project_root / directory
        if not raw_base.exists():
            continue
        base = raw_base.resolve(strict=True)
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve(strict=True)
            if base not in resolved.parents:
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if now - modified >= retention:
                candidates.append(
                    {
                        "path": str(resolved),
                        "bytes": path.stat().st_size,
                        "modified_at": modified.isoformat(),
                        "reason": f"older_than_{retention}",
                    }
                )
    return candidates


def _remove_empty_directories(base: Path) -> None:
    directories = sorted(
        (path for path in base.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts), reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass


def prune_file_caches(
    project_root: Path, *, now: datetime | None = None,
) -> dict[str, int]:
    """Bound independent file caches by both age and total bytes."""
    project_root = project_root.resolve(strict=True)
    now = now or datetime.now(timezone.utc)
    removed_files = 0
    removed_bytes = 0
    kept_bytes = 0
    for relative, policy in FILE_CACHE_POLICIES.items():
        raw_base = project_root / relative
        if not raw_base.exists():
            continue
        base = raw_base.resolve(strict=True)
        files: list[tuple[float, int, Path]] = []
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve(strict=True)
            if base not in resolved.parents:
                continue
            stat = path.stat()
            files.append((stat.st_mtime, stat.st_size, path))
        cutoff = now - policy["max_age"]
        retained: list[tuple[float, int, Path]] = []
        for modified, size, path in files:
            if datetime.fromtimestamp(modified, timezone.utc) < cutoff:
                path.unlink(missing_ok=True)
                removed_files += 1
                removed_bytes += size
            else:
                retained.append((modified, size, path))
        total = sum(size for _, size, _ in retained)
        for _modified, size, path in sorted(retained):
            if total <= int(policy["max_bytes"]):
                break
            path.unlink(missing_ok=True)
            removed_files += 1
            removed_bytes += size
            total -= size
        kept_bytes += max(0, total)
        _remove_empty_directories(base)
    return {
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "kept_bytes": kept_bytes,
    }
