"""Session D: the review queue and preview images of real jobs on a test Control Center (temporary root, own port).

    make   python -I copy_jobs.py make --src <tree>/src --from <install> --root <install>/temp/<new folder> --jobs 145 134
    serve  python -I copy_jobs.py serve --src <tree>/src --root <same folder> --port <port, never 8765>

make reads the install's state/control-center.sqlite3 read-only (mode=ro) and, for each job, its active review queue
and the images that queue names in its report folder. It writes only into the new root under <install>/temp: the
queue with that folder renamed reports/jobs/job-<id>, the images, and a JobStore with each job WAITING_REVIEW, its
source a file that does not exist (input/job-<id>.mp4: no video of the install is opened, the cards show their
report previews) and the queue paused. Nothing of the install is written. The copies keep the real texts and
pictures: they never leave <install>/temp (screenshots go to reports/evidence, which is not in Git).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

# The install folder: the outermost folder above this script with the install marker (a worktree in its temp has one too).
INSTALL = [p for p in Path(__file__).resolve().parents if (p / "biliflow-install.json").is_file()][-1]
IMAGES = {".jpg", ".jpeg", ".png", ".webp"}


def _checked_root(root: Path, new: bool) -> Path:
    root = root.resolve()
    temp = (INSTALL / "temp").resolve()
    if temp not in root.parents:
        raise SystemExit(f"refused: the root must be a folder under {temp}")
    if new and root.exists() and any(root.iterdir()):
        raise SystemExit("refused: the root must be a new, empty folder")
    return root


def _rewrite(value, old: str, new: str):
    """The queue with every string (keys too) that names the report folder renamed."""
    if isinstance(value, str):
        return value.replace(old, new).replace(old.replace("/", "\\"), new)
    if isinstance(value, list):
        return [_rewrite(item, old, new) for item in value]
    if isinstance(value, dict):
        return {_rewrite(key, old, new): _rewrite(item, old, new) for key, item in value.items()}
    return value


def _images(value, prefix: str) -> set[str]:
    """Relative paths of the images the queue names inside its report folder."""
    found: set[str] = set()
    if isinstance(value, str):
        text = value.replace("\\", "/")
        if text.startswith(prefix) and Path(text).suffix.lower() in IMAGES:
            found.add(text)
    elif isinstance(value, list):
        for item in value:
            found |= _images(item, prefix)
    elif isinstance(value, dict):
        for key, item in value.items():
            found |= _images(key, prefix) | _images(item, prefix)
    return found


def make(install: Path, root: Path, job_ids: list[int]) -> None:
    from biliflow.job_store import JobStore
    root, install = _checked_root(root, new=True), install.resolve()
    database = install / "state" / "control-center.sqlite3"
    source_db = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    source_db.row_factory = sqlite3.Row
    for name in ("input", "output", "state", "reports/jobs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    store, summary = JobStore(root / "state" / "control-center.sqlite3"), []
    try:
        for job_id in job_ids:
            row = source_db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None or not row["active_queue_path"]:
                raise SystemExit(f"job {job_id}: no active review queue")
            queue_file = (install / row["active_queue_path"]).resolve()
            folder = queue_file.parent.relative_to((install / "reports" / "jobs").resolve()).as_posix()
            old, new = f"reports/jobs/{folder}/", f"reports/jobs/job-{job_id}/"
            queue = json.loads(queue_file.read_text(encoding="utf-8"))
            copied = 0
            for relative in sorted(_images(queue, old)):
                image = install / relative
                if image.is_file():
                    target = root / (new + relative[len(old):])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(image.read_bytes())
                    copied += 1
            queue = _rewrite(queue, old, new)
            missing = root / "input" / f"job-{job_id}.mp4"
            queue["source"] = {**queue.get("source", {}), "path": str(missing)}
            queue_path = root / new / "review-queue.json"
            queue_path.parent.mkdir(parents=True, exist_ok=True)
            queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
            job = store.upsert_job(job_key=f"job-{job_id}", source_path=missing, source_sha256=row["source_sha256"],
                                   source_size_bytes=int(row["source_size_bytes"] or 0), source_mtime_ns=int(row["source_mtime_ns"] or 0),
                                   duration_seconds=row["duration_seconds"], content_style=row["content_style"] or "unknown", state="WAITING_REVIEW")
            store.update_job(int(job["id"]), active_queue_path=f"{new}review-queue.json", active_revision=int(row["active_revision"] or 1), progress=1.0)
            summary.append({"job": job_id, "test_job": int(job["id"]), "items": len(queue["items"]), "advisory": len(queue.get("advisory_items") or []),
                            "images": copied, "queue_sha256": hashlib.sha256(queue_path.read_bytes()).hexdigest()})
        store.set_setting("scheduler_paused", True)  # nothing of this root is ever scanned or exported
    finally:
        store.close()
        source_db.close()
    print(json.dumps(summary))


def serve(root: Path, port: int) -> None:
    from biliflow.control_center import ControlCenter
    if port == 8765:
        raise SystemExit("refused: 8765 is the real Control Center")
    root = _checked_root(root, new=False)
    ControlCenter(root, port=port, stable_seconds=3600.0, import_existing=False).serve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["make", "serve"])
    parser.add_argument("--src", required=True, type=Path, help="the src folder of the tree to run")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--from", dest="install", type=Path, default=INSTALL, help="the install whose jobs are copied (read only)")
    parser.add_argument("--jobs", type=int, nargs="*", default=[])
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    sys.path.insert(0, str(args.src.resolve()))
    if args.command == "make":
        make(args.install, args.root, args.jobs)
    else:
        serve(args.root, args.port)


if __name__ == "__main__":
    main()
