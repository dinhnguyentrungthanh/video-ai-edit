"""Session D, task (c): "Dựng lại danh sách duyệt (giữ quyết định)" on a copy of one exported job, on a test Control Center.

    run     python -I rebuild_check.py run --job <id> --port <port, never 8765> --out <folder> --shots <folder>
    again   python -I rebuild_check.py again --root <install>/temp/rk-check-<time> --port <port> --out <folder>
    remove  python -I rebuild_check.py remove --root <install>/temp/rk-check-<time>

run builds a new root <install>/temp/rk-check-<time> as scripts/a1b_vram_measure.py does (.venv a junction; models,
tools, runtime and the model caches hard links; src, scripts, config and dashboard_v2 copied from this tree; the brand
and studio logo memory copied from the install). It copies, never links, the job's source video, its active report
folder, its export with the manifest and the stage cache entries of its source into that root. The files the stage cache key
hashes byte for byte come from the install when they differ from this tree's only in line ends (else the run is
refused), so that its cache entries apply. The test job and its
report folder are named job-<id> (real titles make paths too long under temp); a cache entry whose paths would still
be too long is left out, and result.json names its stage. The install is only read: its
database is opened with mode=ro and its files are copied. Nothing of the install is written, and the real Control
Center is never called: every request goes to the test Control Center on --port.

On the test Control Center it then:
1. reads the review list and the cleanup and archive previews (GET), and presses the button (POST rebuild-review);
2. checks that a second press is refused and that both previews refuse the video while it waits;
3. resumes the test queue only while the real queue is idle (its database, mode=ro; the test queue pauses again if
   the real one starts), and waits for the new list;
4. compares the decisions before and after: every decision of the new list has an old source by the carry rules' own
   marks; the old list, the source and the old export are unchanged; both previews still refuse the video;
5. shoots the review dialog and the job drawer with the button and its confirmation in headless Edge ("Hủy", GET only);
6. re-exports when nothing is left to review, and reads both previews again.

again presses the button once more on the job of a root that run kept (now in review), with steps 1-4 and 6. The
stage cache key holds the absolute path of the root's scripts/run.ps1, so a test root never uses the install's
entries; the second press shows the scan steps run by the first one coming back from the root's own cache.

--out gets result.json: counts, states, stage names, the dialog's carry note and booleans; no name, path or picture.
--shots gets the screenshots, which show real frames and titles: give a folder under <install>/reports/evidence (not
in Git). The copies keep real names: they never leave <install>/temp, and `remove` deletes the root (links first,
never entered).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# The install folder: the outermost folder above this script with the install marker (a worktree in its temp has one too).
INSTALL = [p for p in Path(__file__).resolve().parents if (p / "biliflow-install.json").is_file()][-1]
CODE = Path(__file__).resolve().parents[3]
PREFIX = "rk-check-"
REAL_PORT = 8765
LINKED_TREES = ("models", "tools", "runtime", "cache/huggingface", "cache/torch")
FROM_CODE = ("src", "scripts", "config", "dashboard_v2", "pyproject.toml", "requirements.lock.txt")
FROM_INSTALL = ("biliflow-install.json", "annotations/text_semantics_seed_v1.json", "annotations/review-regression-v1.json",
                "state/brand-memory.json", "state/studio-logo-memory.json", "state/studio-logo-frames")
EMPTY = ("input", "work", "previews", "logs", "temp", "output", "reports/jobs", "reports/benchmarks", "state")
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", "*.local.json")
# Files the stage cache key hashes byte for byte (stage_cache._tree_fingerprint). A fresh checkout can end their lines
# differently from the install's (seen on 2026-10-17 with license_policy.json and env.ps1), which changes every key.
FINGERPRINTED = ("config/processing_profiles.json", "config/detection_policy.yaml", "config/text_review_policy.json",
                 "config/license_policy.json", "scripts/run.ps1", "scripts/env.ps1", "pyproject.toml")
BUSY = {"QUEUED", "PREFLIGHT", "SCANNING_SAFETY", "SCANNING_TEXT", "SCANNING_LOGO", "LOCALIZING_REGIONS",
        "BUILDING_REVIEW", "AI_AUDITING", "RENDERING", "VERIFYING"}
PRESERVED = "preserved_decided_from_previous_queue"
BELOW_NORMAL = 0x00004000
sys.path[:0] = [str(CODE / "src"), str(CODE / "scripts")]
from golden_trial_root import _junction, _link_tree, _links  # noqa: E402


def log(message: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {message}", flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- the test root ----------------------------------------------------------------------------------------

def check_place(root: Path) -> None:
    if root.parent != INSTALL / "temp" or not root.name.startswith(PREFIX):
        raise SystemExit(f"refused: {root} is not a {PREFIX}* folder in {INSTALL / 'temp'}")


def same_but_line_ends(first: bytes, second: bytes) -> bool:
    return first.replace(b"\r\n", b"\n") == second.replace(b"\r\n", b"\n")


def make_root() -> tuple[Path, list[str]]:
    """The root and the fingerprinted files taken from the install (same content, other line ends)."""
    root = INSTALL / "temp" / (PREFIX + datetime.now().strftime("%Y%m%d-%H%M%S"))
    check_place(root)
    root.mkdir()
    try:
        _junction(INSTALL / ".venv", root / ".venv")
        for name in LINKED_TREES:
            if (INSTALL / name).is_dir():
                _link_tree(INSTALL / name, root / name)
        for base, names in ((CODE, FROM_CODE), (INSTALL, FROM_INSTALL)):
            for name in names:
                source, target = base / name, root / name
                if source.is_dir():
                    shutil.copytree(source, target, ignore=IGNORED)
                elif source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        for name in EMPTY:
            (root / name).mkdir(parents=True, exist_ok=True)
        taken = []
        for name in FINGERPRINTED:
            ours, theirs = root / name, INSTALL / name
            if ours.is_file() and theirs.is_file() and ours.read_bytes() != theirs.read_bytes():
                if not same_but_line_ends(ours.read_bytes(), theirs.read_bytes()):
                    raise SystemExit(f"refused: {name} differs from the install's in content, not only in line ends")
                shutil.copy2(theirs, ours)
                taken.append(name)
    except BaseException:
        remove_root(root)
        raise
    return root, taken


def remove_root(root: Path) -> None:
    """Take every link down without entering it, then delete the test root."""
    check_place(root)
    if not root.exists():
        return
    for link in _links(root):
        if os.lstat(link).st_file_attributes & 0x10:  # a junction: removes the link itself, never its target
            os.rmdir(link)
        else:
            os.unlink(link)
    if _links(root):
        raise RuntimeError("links left in the test root, nothing deleted")
    shutil.rmtree(root)


def _rewrite(value, old: str, new: str):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_rewrite(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item, old, new) for key, item in value.items()}
    return value


def copy_job(root: Path, job_id: int) -> dict:
    """The job's source, active report folder, export and stage cache into ``root``; a JobStore with the job."""
    from biliflow.review_workflow import existing_review_export
    from biliflow.job_store import JobStore
    real = sqlite3.connect(f"file:{(INSTALL / 'state' / 'control-center.sqlite3').as_posix()}?mode=ro", uri=True)
    real.row_factory = sqlite3.Row
    try:
        row = dict(real.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        revisions = [dict(r) for r in real.execute("SELECT * FROM job_revisions WHERE job_id=? ORDER BY revision", (job_id,))]
        settings = {r["key"]: json.loads(r["value_json"]) for r in real.execute(
            "SELECT key, value_json FROM settings WHERE key LIKE ?", (f"%:{job_id}",)) if r["key"].endswith(f":{job_id}")}
    finally:
        real.close()
    if row["state"] != "COMPLETED" or not row["active_queue_path"]:
        raise SystemExit(f"job {job_id}: not an exported job with a review list")
    source = Path(row["source_path"])
    queue_file = INSTALL / row["active_queue_path"]
    queue = json.loads(queue_file.read_text(encoding="utf-8"))
    export = existing_review_export(INSTALL, queue)
    if export is None:
        raise SystemExit(f"job {job_id}: its export does not match its review")
    # The test job is named job-<id>, its report folder too: real titles make paths too long under temp.
    key, folder = f"job-{job_id}", queue_file.parent.name
    renames = ((str(INSTALL) + "\\", str(root) + "\\"), (f"reports/jobs/{folder}/", f"reports/jobs/{key}/"),
               (f"reports\\jobs\\{folder}\\", f"reports\\jobs\\{key}\\"))

    def renamed(value):
        for old, new in renames:
            value = _rewrite(value, old, new)
        return value
    target = root / "input" / source.name
    shutil.copy2(source, target)
    if sha256(target) != row["source_sha256"]:
        raise SystemExit("the copied source does not match the job's SHA-256")
    shutil.copytree(queue_file.parent, root / "reports" / "jobs" / key)
    copied_queue = root / "reports" / "jobs" / key / "review-queue.json"
    copied_queue.write_text(json.dumps(renamed(queue), ensure_ascii=False, indent=2), encoding="utf-8")
    output = export[0]
    for name in (output.name, output.name + ".manifest.json"):
        shutil.copy2(output.with_name(name), root / "output" / name)
    # Each stage cache entry on its own; an entry whose paths are too long under temp is left out (that stage runs).
    cache, skipped = INSTALL / "cache" / "stage-results" / row["source_sha256"], []
    for entry in sorted(cache.glob("*/*")) if cache.is_dir() else []:
        destination = root / "cache" / "stage-results" / row["source_sha256"] / entry.parent.name / entry.name
        try:
            shutil.copytree(entry, destination)
        except (OSError, shutil.Error):
            shutil.rmtree(destination, ignore_errors=True)
            skipped.append(entry.parent.name)
    store = JobStore(root / "state" / "control-center.sqlite3")
    try:
        info = target.stat()
        job = store.upsert_job(job_key=key, source_path=target, source_sha256=row["source_sha256"],
                               source_size_bytes=info.st_size, source_mtime_ns=info.st_mtime_ns,
                               duration_seconds=row["duration_seconds"], content_style=row["content_style"],
                               profile=row["profile"], state="COMPLETED")
        new_id = int(job["id"])
        store.update_job(new_id, active_queue_path=f"reports/jobs/{key}/review-queue.json", active_revision=row["active_revision"],
                         progress=1.0)
        with store._lock:
            for revision in revisions:
                store._connection.execute(
                    "INSERT INTO job_revisions(job_id,revision,queue_path,queue_status,queue_updated_at,is_active,created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (new_id, revision["revision"], renamed(revision["queue_path"]), revision["queue_status"],
                     revision["queue_updated_at"], revision["is_active"], revision["created_at"]))
            store._connection.commit()
        for name, value in settings.items():
            store.set_setting(name.rsplit(":", 1)[0] + f":{new_id}", key if name.startswith("pipeline_key:") else renamed(value))
        store.set_setting("scheduler_paused", True)
    finally:
        store.close()
    return {"test_job": new_id, "source": target, "queue": copied_queue, "output": root / "output" / output.name,
            "cache_entries_left_out": sorted(set(skipped))}


# --- the test Control Center -------------------------------------------------------------------------------

class Http:
    def __init__(self, port: int) -> None:
        if port == REAL_PORT:
            raise SystemExit("refused: 8765 is the real Control Center")
        self.base, self.token = f"http://127.0.0.1:{port}", None

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 30):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if self.token:
            request.add_header("X-BiliFlow-Token", self.token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")


def real_queue() -> dict:
    """The real Control Center's queue, read from its database (mode=ro); never through its routes."""
    real = sqlite3.connect(f"file:{(INSTALL / 'state' / 'control-center.sqlite3').as_posix()}?mode=ro", uri=True)
    try:
        states = dict(real.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state").fetchall())
    finally:
        real.close()
    busy = {state: count for state, count in states.items() if state in BUSY}
    return {"idle": not busy, "busy": busy}


def job_of(http: Http, job_id: int) -> dict:
    return next(job for job in http.call("GET", "/api/status")[1]["jobs"] if job["id"] == job_id)


def previews(http: Http, job_id: int) -> dict:
    """Whether "Xóa video gốc" and "Lưu trữ" would take the video (their GET previews; never their POST)."""
    found = {}
    for name, path in (("cleanup", "/api/source-cleanup/preview"), ("archive", "/api/source-archive/preview")):
        status, payload = http.call("GET", f"{path}?ids={job_id}")
        refused = [item for item in payload.get("ineligible") or [] if item.get("job_id") == job_id]
        found[name] = {"http": status, "eligible": any(item.get("job_id") == job_id for item in payload.get("eligible") or []),
                       "reason": refused[0]["reason"] if refused else None}
    return found


def decided(queue: dict) -> dict:
    return dict(Counter(str(item["decision"]) for item in queue.get("items") or [] if item.get("decision")))


def pending(queue: dict) -> int:
    return sum(1 for item in queue.get("items") or [] if not item.get("decision"))


def sources_check(old: dict, new: dict) -> dict:
    """Every decision of the new list comes from the old list, by the carry rules' own marks."""
    by_id = {str(item["id"]): item for item in old.get("items") or [] if item.get("decision")}
    marks, invented = Counter(), 0
    for item in new.get("items") or []:
        if not item.get("decision"):
            continue
        if item.get("migration_status") == PRESERVED:
            marks["kept_on_its_old_card"] += 1
            sources = [str(item.get("previous_id") or item["id"])]
        else:
            sources = [str(x) for x in item.get("decision_carried_from") or []]
            marks["carried_to_a_new_card" if sources else "no_mark"] += 1
        if item["decision"] not in {by_id[x]["decision"] for x in sources if x in by_id}:
            invented += 1
    return {"marks": dict(marks), "decisions_without_an_old_source": invented}


def wait_settled(http: Http, job_id: int, limit: float, idle: list) -> dict:
    """Polls the test job until it leaves the queue; the test queue pauses while the real one is busy."""
    deadline, paused = time.monotonic() + limit, False
    while time.monotonic() < deadline:
        job, real = job_of(http, job_id), real_queue()
        idle.append(real["idle"])
        if not real["idle"] and not paused:
            http.call("POST", "/api/scheduler", {"paused": True})
            paused = True
            log(f"real queue busy {real['busy']}: test queue paused")
        elif real["idle"] and paused:
            http.call("POST", "/api/scheduler", {"paused": False})
            paused = False
        if job["state"] not in BUSY:
            return job
        time.sleep(3)
    raise SystemExit("the test job did not settle in time")


def shoot(base: str, job_id: int, shots: Path) -> dict:
    """The review dialog with its carry note, the job drawer and the button's confirmation ("Hủy"), headless Edge."""
    from playwright.sync_api import sync_playwright
    shots.mkdir(parents=True, exist_ok=True)
    seen: dict = {"posts": 0, "carry_note": {}, "confirm_points": None}

    def count_post(request) -> None:
        if request.method == "POST":
            seen["posts"] += 1
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            for width, height, theme in ((1440, 900, "light"), (390, 844, "dark")):
                page = browser.new_page(viewport={"width": width, "height": height})
                page.add_init_script(f"try{{localStorage.setItem('biliflow-v2-theme','{theme}')}}catch(_){{}}")
                page.on("request", count_post)
                page.goto(f"{base}/dashboard-v2/#review/{job_id}/videos")
                page.wait_for_selector("#review-dialog[open] .rv-carry", timeout=60000)
                seen["carry_note"][str(width)] = page.locator("#review-dialog .rv-carry").text_content()
                page.screenshot(path=str(shots / f"review-{width}.png"))
                page.goto(f"{base}/dashboard-v2/#videos")
                page.wait_for_selector("#search")
                page.locator('[data-action="filter"][data-filter="all"]').first.click()
                page.fill("#search", str(job_id))
                page.locator(f'[data-action="detail"][data-id="{job_id}"]').first.click()
                page.wait_for_selector(".drawer")
                page.evaluate("document.querySelector('.drawer details.more-actions')?.setAttribute('open','')")
                button = page.locator('.drawer [data-op="rebuild"]')
                button.scroll_into_view_if_needed()
                page.screenshot(path=str(shots / f"drawer-{width}.png"))
                button.click()
                page.wait_for_selector("#modal[open]")
                seen["confirm_points"] = page.locator("#modal li").all_text_contents()
                page.screenshot(path=str(shots / f"confirm-{width}.png"))
                page.locator('#modal .modal-foot [data-action="close-modal"]').click()
                page.wait_for_timeout(500)
                page.close()
        finally:
            browser.close()
    return seen


def start_center(root: Path, port: int) -> tuple[subprocess.Popen, object]:
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}
    environment.pop("BILIFLOW_EXPORT_LANE", None)
    center_log = (root / "logs" / "test-control-center.out.log").open("ab")
    center = subprocess.Popen(
        [str(root / ".venv" / "Scripts" / "python.exe"), "-m", "biliflow.control_entry", "--project-root", str(root),
         "--host", "127.0.0.1", "--port", str(port), "--stable-seconds", "5", "--no-import-existing"],
        cwd=root, env=environment, stdout=center_log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL,
    )
    return center, center_log


def check(http: Http, root: Path, copied: dict, shots: Path | None) -> dict:
    job_id, result, idle = copied["test_job"], {}, []
    hashes = {name: sha256(copied[name]) for name in ("source", "queue", "output")}
    old = http.call("GET", f"/api/jobs/{job_id}/review/queue")[1]
    job = job_of(http, job_id)
    result["before"] = {"state": job["state"], "revision": job["active_revision"], "decisions": decided(old),
                        "pending": pending(old), "previews": previews(http, job_id)}
    log(f"before: {result['before']}")
    status, pressed = http.call("POST", f"/api/jobs/{job_id}/rebuild-review", {})
    rebuild = pressed.get("rebuild") or {}
    result["press"] = {"http": status, "state": pressed.get("state"), "error": pressed.get("error"),
                       "decisions": rebuild.get("decisions"), "from_revision": rebuild.get("from_revision")}
    if status != 200:
        raise SystemExit(f"the press was refused: {result['press']}")
    request_file = root / "reports" / "jobs" / rebuild["pipeline_key"] / "decision-carry-request.json"
    second = http.call("POST", f"/api/jobs/{job_id}/rebuild-review", {})
    result["while_queued"] = {"request_written": request_file.is_file(), "second_press_http": second[0],
                              "second_press_refusal": second[1].get("error"), "previews": previews(http, job_id),
                              "old_export_present": copied["output"].is_file()}
    log(f"pressed: {result['press']} / while queued: {result['while_queued']}")
    if not real_queue()["idle"]:
        raise SystemExit("the real queue is busy: the test queue is not started")
    started = time.monotonic()
    http.call("POST", "/api/scheduler", {"paused": False})
    job = wait_settled(http, job_id, 45 * 60, idle)
    result["rebuild_seconds"] = round(time.monotonic() - started, 1)
    new = http.call("GET", f"/api/jobs/{job_id}/review/queue")[1]
    carry = {key: value for key, value in (new.get("decision_carry") or {}).items() if key != "from_queue"}
    result["after"] = {"state": job["state"], "revision": job["active_revision"], "decisions": decided(new),
                       "pending": pending(new), "decision_carry": carry,
                       "audit_last": (new.get("audit_log") or [{}])[-1].get("action"),
                       "sources": sources_check(old, new), "previews": previews(http, job_id),
                       "old_list_unchanged": sha256(copied["queue"]) == hashes["queue"],
                       "source_unchanged": sha256(copied["source"]) == hashes["source"],
                       "old_export_unchanged": copied["output"].is_file() and sha256(copied["output"]) == hashes["output"]}
    log(f"after: {result['after']}")
    test_db = sqlite3.connect(f"file:{(root / 'state' / 'control-center.sqlite3').as_posix()}?mode=ro", uri=True)
    try:
        rows = test_db.execute(
            "SELECT event_type, payload_json FROM events WHERE job_id=? AND id > (SELECT MAX(id) FROM events"
            " WHERE job_id=? AND event_type='REVIEW_REBUILD_QUEUED') ORDER BY id", (job_id, job_id)).fetchall()
    finally:
        test_db.close()
    result["stages"] = {"cache_hit": [json.loads(p or "{}").get("stage") for t, p in rows if t == "STAGE_CACHE_HIT"],
                        "events": sorted(set(t for t, _ in rows))}
    if shots is not None:
        result["browser"] = shoot(http.base, job_id, shots)
    if job["state"] == "READY_TO_EXPORT" and result["after"]["pending"] == 0:
        status, _ = http.call("POST", f"/api/jobs/{job_id}/review/finalize", {"size_mode": "default"})
        exported = wait_settled(http, job_id, 45 * 60, idle)
        outputs = sorted(path.name for path in (root / "output").glob("*.mp4"))
        result["reexport"] = {"http": status, "state": exported["state"], "outputs": len(outputs),
                              "only_the_old_file": outputs == [copied["output"].name], "previews": previews(http, job_id)}
        log(f"re-export: {result['reexport']}")
    else:
        result["reexport"] = {"skipped": "cards left to review"}
    result["real_queue_idle_throughout"] = all(idle)
    return result


def run(args: argparse.Namespace) -> None:
    head = subprocess.run(["git", "-C", str(CODE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    result: dict = {"job": args.job, "code": head.stdout.strip(),
                    "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    http = Http(args.port)
    root, taken = make_root()
    result["taken_from_install_for_line_ends"] = taken
    log(f"test root {root.name}; from the install for their line ends: {taken}")
    copied = copy_job(root, args.job)
    result["test_job"] = copied["test_job"]
    result["cache_entries_left_out"] = copied["cache_entries_left_out"]
    log(f"copied; cache entries left out for {copied['cache_entries_left_out']}")
    center, center_log = start_center(root, args.port)
    try:
        for _ in range(120):
            try:
                if http.call("GET", "/healthz", timeout=2)[0] == 200:
                    break
            except OSError:
                time.sleep(1)
        http.token = http.call("GET", "/api/session")[1]["token"]
        result.update(check(http, root, copied, Path(args.shots)))
    finally:
        subprocess.run(["taskkill", "/PID", str(center.pid), "/T", "/F"], capture_output=True)
        center_log.close()
        result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result["root"] = root.name
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"result written; the test root is kept: remove it with `remove --root {root}`")


def again(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    check_place(root)
    http = Http(args.port)
    test_db = sqlite3.connect(f"file:{(root / 'state' / 'control-center.sqlite3').as_posix()}?mode=ro", uri=True)
    try:
        job_id, source, queue = test_db.execute("SELECT id, source_path, active_queue_path FROM jobs").fetchone()
    finally:
        test_db.close()
    outputs = sorted((root / "output").glob("*.mp4"))
    copied = {"test_job": job_id, "source": Path(source), "queue": root / queue, "output": outputs[0]}
    head = subprocess.run(["git", "-C", str(CODE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    result: dict = {"pass": "again", "code": head.stdout.strip(), "test_job": job_id, "root": root.name,
                    "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    center, center_log = start_center(root, args.port)
    try:
        for _ in range(120):
            try:
                if http.call("GET", "/healthz", timeout=2)[0] == 200:
                    break
            except OSError:
                time.sleep(1)
        http.token = http.call("GET", "/api/session")[1]["token"]
        result.update(check(http, root, copied, None))
    finally:
        subprocess.run(["taskkill", "/PID", str(center.pid), "/T", "/F"], capture_output=True)
        center_log.close()
        result["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "result-again.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log("result-again written")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["run", "again", "remove"])
    parser.add_argument("--job", type=int, default=0)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--out", default="")
    parser.add_argument("--shots", default="")
    parser.add_argument("--root", default="")
    args = parser.parse_args()
    if args.command == "remove":
        remove_root(Path(args.root).resolve())
    elif args.command == "again":
        again(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
