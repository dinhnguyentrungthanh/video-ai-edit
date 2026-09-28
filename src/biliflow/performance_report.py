"""Read-only summaries of completed or active Control Center stages."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path


def summarize_job(root: Path, job_id: int) -> dict:
    root = root.resolve(strict=True)
    database = root / "state" / "control-center.sqlite3"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise ValueError(f"Unknown job: {job_id}")
        stages = connection.execute(
            "SELECT * FROM stages WHERE job_id=? ORDER BY ordinal", (job_id,),
        ).fetchall()
        events = connection.execute(
            "SELECT * FROM events WHERE job_id=? AND event_type='STAGE_CACHE_HIT'",
            (job_id,),
        ).fetchall()
        artifacts = connection.execute(
            "SELECT stage_name,path FROM artifacts WHERE job_id=?", (job_id,),
        ).fetchall()
    summary = []
    active_queue = job["active_queue_path"]
    report_root = (root / active_queue).resolve().parent if active_queue else None
    for stage in stages:
        start, end = stage["started_at"], stage["completed_at"]
        started = datetime.fromisoformat(start) if start else None
        ended = datetime.fromisoformat(end) if end else None
        seconds = (ended - started).total_seconds() if started and ended else None
        cache_hit = any(
            json.loads(event["payload_json"] or "{}").get("stage") == stage["name"]
            and started is not None
            and datetime.fromisoformat(event["created_at"]) >= started
            and (ended is None or datetime.fromisoformat(event["created_at"]) <= ended)
            for event in events
        )
        profiles = []
        seen = set()
        for artifact in artifacts:
            if artifact["stage_name"] != stage["name"] or report_root is None:
                continue
            path = (root / artifact["path"]).resolve()
            if (not path.is_relative_to(root / "reports")
                    or not path.is_relative_to(report_root)
                    or path in seen or path.suffix != ".json" or not path.is_file()):
                continue
            seen.add(path)
            if path.stat().st_size > 32 * 1024**2:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                metrics = payload.get("metrics", {})
                keys = {
                    "localize_logo": ("localization_performance", "grounding_performance"),
                    "confirm_violence": ("confirmation_performance",),
                }.get(stage["name"], ("performance",))
                profiles.append({
                    "report": path.relative_to(root).as_posix(),
                    "performance": {key: metrics[key] for key in keys if key in metrics} or None,
                    "historical_on_cache_hit": cache_hit,
                })
            except (ValueError, OSError, AttributeError):
                continue
        summary.append({
            "stage": stage["name"], "state": stage["state"],
            "attempt": stage["attempt"], "wall_seconds": seconds,
            "cache_hit": cache_hit, "profiles": profiles,
        })
    return {
        "schema_version": 1, "job_id": job_id, "job_state": job["state"],
        "active_queue": active_queue,
        "source_duration_seconds": job["duration_seconds"],
        "profile": job["profile"], "stages": summary,
        "completed_stage_wall_seconds": round(sum(
            stage["wall_seconds"] for stage in summary
            if stage["state"] == "COMPLETED" and stage["wall_seconds"] is not None
        ), 3),
        "notes": [
            "Stage wall time includes startup, resource waits and cache I/O; scan timings are a subset.",
            "Absent performance data means not measured, not zero time.",
            "Cache-restored report timings describe the original scan, not this attempt.",
            "Shared safety reports repeat one profiler snapshot; do not sum their timings.",
            "Snapshot covers current stage attempts; consult events for older attempts.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--job-id", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(summarize_job(args.root, args.job_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
