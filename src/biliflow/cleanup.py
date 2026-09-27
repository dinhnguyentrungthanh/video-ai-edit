from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


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
