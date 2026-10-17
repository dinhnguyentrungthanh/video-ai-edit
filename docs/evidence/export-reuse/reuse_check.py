"""Session D: an export whose operations render the same is reused after "Dựng lại danh sách duyệt", on a test Control Center.

    run       python -I reuse_check.py run --job <id> --port <port, never 8765> --out <folder>
    previews  python -I reuse_check.py previews --root <install>/temp/er-check-<time> --port <port> --out <folder>
    import    python -I reuse_check.py import --root <install>/temp/er-check-<time> --out <folder>
    remove    python -I reuse_check.py remove --root <install>/temp/er-check-<time>

run builds a test root <install>/temp/er-check-<time> as docs/evidence/rebuild-keep/rebuild_check.py does (its helpers:
.venv a junction, models and tools hard links, src, scripts, config and dashboard_v2 copied from this tree). It copies,
never links, the job's source video, the report folder of the newest revision whose export sits under that revision's
own name in output/ (job 143: revision 3, before the user's rebuild), that export with its manifest and the stage cache
entries of the source. The test job is that revision, COMPLETED, as the job was before the press. The install is only
read (its database with mode=ro) and the real Control Center is never called: every request goes to --port.

On the test Control Center it then:
1. reads the cleanup and archive previews (GET) and presses "Dựng lại danh sách duyệt (giữ quyết định)" (POST
   rebuild-review);
2. resumes the test queue only while the real queue is idle and waits for the new list;
3. compares the new list's operations with the export's manifest: the same render, other ids, another own name;
4. presses "Xuất video" (POST review/finalize) when no card waits, and checks that it completes at once: no render
   request, no file written in output/ or work/, the export and its manifest unchanged, the job COMPLETED;
5. reads both previews again: they must take the video with that export.

--out gets result.json: counts, states, booleans and timings; no name, path or picture.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rebuild-keep"))
import rebuild_check as rk  # noqa: E402  (its import puts this tree's src and scripts on sys.path)

rk.PREFIX = "er-check-"
INSTALL, log = rk.INSTALL, rk.log


def _real_db() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{(INSTALL / 'state' / 'control-center.sqlite3').as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def copy_exported_revision(root: Path, job_id: int) -> dict:
    """The job as it was before the rebuild: the revision whose export has that revision's own name."""
    from biliflow.export_identity import read_manifest
    from biliflow.job_store import JobStore
    from biliflow.review_workflow import existing_review_export, review_export_paths
    real = _real_db()
    try:
        row = dict(real.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        revisions = [dict(r) for r in real.execute(
            "SELECT * FROM job_revisions WHERE job_id=? ORDER BY revision", (job_id,))]
        settings = {r["key"]: json.loads(r["value_json"]) for r in real.execute(
            "SELECT key, value_json FROM settings WHERE key LIKE ?", (f"%:{job_id}",)) if r["key"].endswith(f":{job_id}")}
    finally:
        real.close()
    chosen = None
    for revision in reversed(revisions):
        queue = json.loads((INSTALL / revision["queue_path"]).read_text(encoding="utf-8"))
        own = review_export_paths(INSTALL, queue)[1]
        export = existing_review_export(INSTALL, queue)
        if queue.get("status") == "READY_FOR_EDIT_PLAN" and export is not None and export[0] == own:
            chosen = (revision, queue, own)
            break
    if chosen is None:
        raise SystemExit(f"job {job_id}: no revision has its export under its own name")
    revision, queue, output = chosen
    source = Path(row["source_path"])
    queue_file = INSTALL / revision["queue_path"]
    key, folder = f"job-{job_id}", queue_file.parent.name
    renames = ((str(INSTALL) + "\\", str(root) + "\\"), (f"reports/jobs/{folder}/", f"reports/jobs/{key}/"),
               (f"reports\\jobs\\{folder}\\", f"reports\\jobs\\{key}\\"))

    def renamed(value):
        for old, new in renames:
            value = rk._rewrite(value, old, new)
        return value
    target = root / "input" / source.name
    rk.shutil.copy2(source, target)
    if rk.sha256(target) != row["source_sha256"]:
        raise SystemExit("the copied source does not match the job's SHA-256")
    rk.shutil.copytree(queue_file.parent, root / "reports" / "jobs" / key)
    copied_queue = root / "reports" / "jobs" / key / queue_file.name
    copied_queue.write_text(json.dumps(renamed(queue), ensure_ascii=False, indent=2), encoding="utf-8")
    for name in (output.name, output.name + ".manifest.json"):
        rk.shutil.copy2(output.with_name(name), root / "output" / name)
    cache, skipped = INSTALL / "cache" / "stage-results" / row["source_sha256"], []
    for entry in sorted(cache.glob("*/*")) if cache.is_dir() else []:
        destination = root / "cache" / "stage-results" / row["source_sha256"] / entry.parent.name / entry.name
        try:
            rk.shutil.copytree(entry, destination)
        except (OSError, rk.shutil.Error):
            rk.shutil.rmtree(destination, ignore_errors=True)
            skipped.append(entry.parent.name)
    relative_queue = f"reports/jobs/{key}/{queue_file.name}"
    store = JobStore(root / "state" / "control-center.sqlite3")
    try:
        info = target.stat()
        job = store.upsert_job(job_key=key, source_path=target, source_sha256=row["source_sha256"],
                               source_size_bytes=info.st_size, source_mtime_ns=info.st_mtime_ns,
                               duration_seconds=row["duration_seconds"], content_style=row["content_style"],
                               profile=row["profile"], state="COMPLETED")
        new_id = int(job["id"])
        store.update_job(new_id, active_queue_path=relative_queue, active_revision=revision["revision"], progress=1.0)
        with store._lock:
            store._connection.execute(
                "INSERT INTO job_revisions(job_id,revision,queue_path,queue_status,queue_updated_at,is_active,created_at)"
                " VALUES (?,?,?,?,?,1,?)",
                (new_id, revision["revision"], relative_queue, revision["queue_status"], revision["queue_updated_at"],
                 revision["created_at"]))
            store._connection.commit()
        for name, value in settings.items():
            if value is None or name.startswith(("pipeline_key:", "render:", "skip:")):
                continue
            store.set_setting(name.rsplit(":", 1)[0] + f":{new_id}", value)
        store.set_setting(f"pipeline_key:{new_id}", key)
        store.set_setting("scheduler_paused", True)
    finally:
        store.close()
    return {"test_job": new_id, "revision": revision["revision"], "source": target, "queue": copied_queue,
            "output": root / "output" / output.name, "manifest": read_manifest(root / "output" / output.name),
            "cache_entries_left_out": sorted(set(skipped))}


def active_queue(root: Path, job_id: int) -> dict:
    test_db = sqlite3.connect(f"file:{(root / 'state' / 'control-center.sqlite3').as_posix()}?mode=ro", uri=True)
    try:
        (relative,) = test_db.execute("SELECT active_queue_path FROM jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        test_db.close()
    return json.loads((root / relative).read_text(encoding="utf-8"))


def test_setting(root: Path, key: str):
    test_db = sqlite3.connect(f"file:{(root / 'state' / 'control-center.sqlite3').as_posix()}?mode=ro", uri=True)
    try:
        row = test_db.execute("SELECT value_json FROM settings WHERE key=?", (key,)).fetchone()
    finally:
        test_db.close()
    return None if row is None else json.loads(row[0])


def files(folder: Path) -> dict:
    return {path.relative_to(folder).as_posix(): rk.sha256(path) for path in sorted(folder.rglob("*")) if path.is_file()}


def check(http: rk.Http, root: Path, copied: dict) -> dict:
    from biliflow.export_identity import render_identity, same_render
    from biliflow.review_workflow import queue_render_identity, review_export_paths
    job_id, result, idle = copied["test_job"], {}, []
    output_before = files(root / "output")
    job = rk.job_of(http, job_id)
    result["before"] = {"state": job["state"], "revision": job["active_revision"], "previews": rk.previews(http, job_id)}
    log(f"before: {result['before']}")
    status, pressed = http.call("POST", f"/api/jobs/{job_id}/rebuild-review", {})
    result["press"] = {"http": status, "state": pressed.get("state"), "error": pressed.get("error"),
                       "decisions": (pressed.get("rebuild") or {}).get("decisions")}
    if status != 200:
        raise SystemExit(f"the press was refused: {result['press']}")
    if not rk.real_queue()["idle"]:
        raise SystemExit("the real queue is busy: the test queue is not started")
    started = time.monotonic()
    http.call("POST", "/api/scheduler", {"paused": False})
    job = rk.wait_settled(http, job_id, 45 * 60, idle)
    result["rebuild_seconds"] = round(time.monotonic() - started, 1)
    http.call("POST", "/api/scheduler", {"paused": True})
    new = active_queue(root, job_id)
    old_operations = copied["manifest"]["operations"]
    render, exported = queue_render_identity(new), render_identity(old_operations)
    own = review_export_paths(root, new)[1]
    carry = new.get("decision_carry") or {}
    result["after_rebuild"] = {
        "state": job["state"], "revision": job["active_revision"], "pending": rk.pending(new),
        "decisions": rk.decided(new),
        "decision_carry": {key: carry.get(key) for key in (
            "from_revision", "previous_decisions", "carried", "restored_unchanged", "conflicts", "restored_reasons")},
        "operations": len(render or []), "same_render_as_the_export": same_render(render, exported),
        "operation_ids_differ": [op["id"] for op in render or []] != [op["id"] for op in exported or []],
        "own_name_differs_from_the_export": own != copied["output"], "own_name_exists": own.exists(),
        "previews": rk.previews(http, job_id),
    }
    log(f"after the rebuild: {result['after_rebuild']}")
    if job["state"] != "READY_TO_EXPORT" or result["after_rebuild"]["pending"]:
        result["export"] = {"skipped": "cards left to review"}
        return result
    work_before = files(root / "work")  # the rebuild's scan steps may write there; the export must not
    started = time.monotonic()
    status, body = http.call("POST", f"/api/jobs/{job_id}/review/finalize", {"size_mode": "default"})
    elapsed = round(time.monotonic() - started, 2)
    job = rk.job_of(http, job_id)
    result["export"] = {
        "http": status, "status": body.get("status"), "error": body.get("error"), "seconds": elapsed,
        "output_is_the_export": body.get("output") == copied["output"].relative_to(root).as_posix(),
        "state": job["state"], "render_request": test_setting(root, f"render:{job_id}") is not None,
        "output_unchanged_since_the_copy": files(root / "output") == output_before,
        "work_unchanged_by_the_export": files(root / "work") == work_before,
        "previews": rk.previews(http, job_id),
    }
    log(f"export: {result['export']}")
    test_db = sqlite3.connect(f"file:{(root / 'state' / 'control-center.sqlite3').as_posix()}?mode=ro", uri=True)
    try:
        events = [t for (t,) in test_db.execute(
            "SELECT event_type FROM events WHERE job_id=? AND id > (SELECT MAX(id) FROM events WHERE job_id=?"
            " AND event_type='REVIEW_REBUILD_QUEUED') ORDER BY id", (job_id, job_id))]
    finally:
        test_db.close()
    result["events_after_the_press"] = sorted(set(events))
    result["render_stage_started"] = any("RENDER" in event for event in events)
    result["real_queue_idle_throughout"] = all(idle)
    return result


def run(args: argparse.Namespace) -> None:
    head = subprocess.run(["git", "-C", str(rk.CODE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    result: dict = {"job": args.job, "code": head.stdout.strip(),
                    "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    http = rk.Http(args.port)
    root, taken = rk.make_root()
    result["taken_from_install_for_line_ends"] = taken
    log(f"test root {root.name}")
    copied = copy_exported_revision(root, args.job)
    result.update(test_job=copied["test_job"], copied_revision=copied["revision"],
                  cache_entries_left_out=copied["cache_entries_left_out"])
    center, center_log = rk.start_center(root, args.port)
    try:
        for _ in range(120):
            try:
                if http.call("GET", "/healthz", timeout=2)[0] == 200:
                    break
            except OSError:
                time.sleep(1)
        http.token = http.call("GET", "/api/session")[1]["token"]
        result.update(check(http, root, copied))
    finally:
        subprocess.run(["taskkill", "/PID", str(center.pid), "/T", "/F"], capture_output=True)
        center_log.close()
        result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result["root"] = root.name
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"result written; the test root is kept: remove it with `remove --root {root}`")


def previews(args: argparse.Namespace) -> None:
    """The cleanup and archive previews (GET) of the job a run left COMPLETED, with this tree's src copied in."""
    root = Path(args.root).resolve()
    rk.check_place(root)
    rk.shutil.rmtree(root / "src")
    rk.shutil.copytree(rk.CODE / "src", root / "src", ignore=rk.IGNORED)
    http = rk.Http(args.port)
    test_db = sqlite3.connect(f"file:{(root / 'state' / 'control-center.sqlite3').as_posix()}?mode=ro", uri=True)
    try:
        (job_id,) = test_db.execute("SELECT id FROM jobs").fetchone()
    finally:
        test_db.close()
    head = subprocess.run(["git", "-C", str(rk.CODE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    result: dict = {"pass": "previews", "code": head.stdout.strip(), "test_job": job_id, "root": root.name}
    center, center_log = rk.start_center(root, args.port)
    try:
        for _ in range(120):
            try:
                if http.call("GET", "/healthz", timeout=2)[0] == 200:
                    break
            except OSError:
                time.sleep(1)
        http.token = http.call("GET", "/api/session")[1]["token"]
        result.update(state=rk.job_of(http, job_id)["state"], previews=rk.previews(http, job_id))
    finally:
        subprocess.run(["taskkill", "/PID", str(center.pid), "/T", "/F"], capture_output=True)
        center_log.close()
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "result-previews.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                              encoding="utf-8")
    log(f"previews: {result}")


def startup_import(args: argparse.Namespace) -> None:
    """The job a run left COMPLETED put back to READY_TO_EXPORT (job 143 on the real Control Center now), then what a
    Control Center start runs (job_import.import_existing_project) on the test root: the export must complete it."""
    from biliflow.job_import import import_existing_project
    from biliflow.job_store import JobStore
    root = Path(args.root).resolve()
    rk.check_place(root)
    store = JobStore(root / "state" / "control-center.sqlite3")
    try:
        job = store.list_jobs()[0]
        job_id = int(job["id"])
        store.update_job(job_id, state="READY_TO_EXPORT", progress=0.0)
        artifacts_before = [a["path"] for a in store.artifacts(job_id) if a["kind"] == "final_output"]
        store.recover_interrupted()
        import_existing_project(root, store)
        after = store.get_job(job_id)
        artifacts = [a["path"] for a in store.artifacts(job_id) if a["kind"] == "final_output"]
    finally:
        store.close()
    exports = sorted(path.relative_to(root).as_posix() for path in (root / "output").glob("*.mp4"))
    head = subprocess.run(["git", "-C", str(rk.CODE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    result = {"pass": "startup import", "code": head.stdout.strip(), "test_job": job_id, "root": root.name,
              "state_before": "READY_TO_EXPORT", "state_after": after["state"], "progress_after": after["progress"],
              "final_output_artifacts_before": len(artifacts_before),
              "final_output_artifact_added_for_the_export": len(artifacts) > len(artifacts_before)
              and artifacts[-1] in exports,
              "exports_in_output": len(exports)}
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "result-import.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"startup import: {result}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["run", "previews", "import", "remove"])
    parser.add_argument("--job", type=int, default=0)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--out", default="")
    parser.add_argument("--root", default="")
    args = parser.parse_args()
    if args.command == "remove":
        rk.remove_root(Path(args.root).resolve())
    elif args.command == "previews":
        previews(args)
    elif args.command == "import":
        startup_import(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
