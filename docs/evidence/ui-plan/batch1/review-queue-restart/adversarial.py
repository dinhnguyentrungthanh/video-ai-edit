"""Adversarial queue/restart checks for batch 1 (scratch; temp roots only)."""
from __future__ import annotations

import hashlib
import json
import sys
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.control_center import ControlCenter
from biliflow.job_pipeline import PipelineStage
from biliflow.job_store import JobStore
from biliflow.review_workflow import review_export_paths
from biliflow.scheduler import JobScheduler

SCRATCH = Path(__file__).resolve().parent
RESULTS: list[tuple[str, bool, str]] = []
STAGES = [PipelineStage("scan", "SCANNING_TEXT", tuple()),
          PipelineStage("build_review", "BUILDING_REVIEW", tuple())]


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)))
    print(("PASS " if ok else "FAIL ") + name + (" :: " + str(detail) if detail else ""))


def make_root(tmp: str) -> Path:
    root = Path(tmp).resolve()
    for name in ("input", "reports/jobs", "logs", "config", "scripts", "work", "output", "state",
                 "tools/ffmpeg/bin"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def add_source(root: Path, name: str, *, with_queue: bool, status="REVIEW_REQUIRED",
               items=None, updated="2026-10-01T00:00:00+00:00", key=None):
    source = root / "input" / f"{name}.mp4"
    if not source.exists():
        source.write_bytes(name.encode() * 10)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    queue = None
    if with_queue:
        folder = root / "reports" / "jobs" / (key or name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "scan.json").write_text(json.dumps({"content_style": "live_action"}), encoding="utf-8")
        queue = {"status": status, "updated_at": updated,
                 "source": {"path": str(source), "sha256": digest, "duration_seconds": 10.0},
                 "reports": [f"reports/jobs/{key or name}/scan.json"],
                 "items": items if items is not None else [
                     {"id": "x", "decision": "BLUR", "start_seconds": 1, "end_seconds": 2}]}
        (folder / "review-queue.json").write_text(json.dumps(queue), encoding="utf-8")
    return source, digest, queue


def manifest(root: Path, output: Path, digest: str):
    output.write_bytes(b"rendered")
    output.with_suffix(output.suffix + ".manifest.json").write_text(json.dumps({
        "status": "COMPLETED", "source": {"sha256": digest},
        "output": {"path": output.relative_to(root).as_posix()}}), encoding="utf-8")


def fake_probe(*_a, **_k):
    return {}


def scenario_fifo():
    with TemporaryDirectory() as tmp, patch("biliflow.scheduler.pipeline_stages",
                                            side_effect=lambda **_: list(STAGES)):
        root = make_root(tmp)
        store = JobStore(root / "state" / "control-center.sqlite3")
        sch = JobScheduler(root, store)
        ids = {}
        for n in "ABCD":
            src, dig, _ = add_source(root, n, with_queue=False)
            ids[n] = int(store.upsert_job(job_key=n, source_path=src, source_sha256=dig,
                                          source_size_bytes=src.stat().st_size,
                                          source_mtime_ns=src.stat().st_mtime_ns,
                                          content_style="live_action", state="NEEDS_METADATA")["id"])
        A, B, C, D = (ids[n] for n in "ABCD")
        for j in (A, B, C):
            sch.start_job(j, content_style="live_action", profile="careful", detector_groups=["advertising"])
        # rerun of a waiting A is refused
        try:
            sch.rerun(A)
            check("fifo: rerun of queued A refused", False, "accepted")
        except ValueError as e:
            check("fifo: rerun of queued A refused", True, e)
        # A is head: run both of its stages (scan, then build_review replaced by manual finish)
        job, stage, d = sch._select()
        check("fifo: A is head", job["id"] == A, job["id"])
        assert store.claim_queued(A, d.job_state, d.name)
        store.update_stage(A, stage["name"], state="COMPLETED")
        sch._after_success(A, stage["name"], d)
        check("fifo: A stays head between stages", sch.queue_order()[0]["job_id"] == A, sch.queue_order())
        # finish A's review build manually
        job, stage, d = sch._select()
        assert store.claim_queued(A, d.job_state, d.name)
        store.update_stage(A, stage["name"], state="COMPLETED")
        store.update_job(A, state="WAITING_REVIEW", current_stage=None, progress=1.0,
                         active_queue_path="reports/jobs/A/review-queue.json", active_revision=1)
        # D reviewed elsewhere
        store.replace_stages(D, ["scan", "build_review"])
        for s in ("scan", "build_review"):
            store.update_stage(D, s, state="COMPLETED")
        store.update_job(D, state="READY_TO_EXPORT", progress=1.0)
        # heartbeat noise on B/C
        store.update_stage(B, "scan", heartbeat_at="2030-01-01T00:00:00")
        store.update_job(C, progress=0.5)
        store.update_job(B, progress=0.1, error=None)
        sch.rerun(A)
        sch.queue_render(D, plan_path=root / "work" / "d.json", output_path=root / "output" / "d.mp4")
        order = [(x["job_id"], x["kind"], x["position"]) for x in sch.queue_order()]
        check("fifo: order B,C,A,D", [o[0] for o in order] == [B, C, A, D], order)
        check("fifo: kinds scan,scan,scan,export",
              [o[1] for o in order] == ["scan", "scan", "scan", "export"], order)
        # double finalize on queued D: does it move D?
        before = store.get_job(D)["queue_seq"]
        sch.queue_render(D, plan_path=root / "work" / "d.json", output_path=root / "output" / "d.mp4")
        after = store.get_job(D)["queue_seq"]
        check("fifo: repeated queue_render of an already queued export keeps seq (info)",
              before == after, f"{before}->{after}")
        # pause/resume B keeps place
        sch.pause_now(B)
        sch.resume(B)
        check("fifo: pause/resume B keeps head",
              [x["job_id"] for x in sch.queue_order()] == [B, C, A, D], sch.queue_order())
        # stop_after_stage on queued C then resume keeps place
        sch.stop_after_stage(C)
        sch.resume(C)
        check("fifo: stop-after-stage/resume C keeps place",
              [x["job_id"] for x in sch.queue_order()] == [B, C, A, D], sch.queue_order())
        # scheduler paused setting: order unchanged
        store.set_setting("scheduler_paused", True)
        check("fifo: _select None while paused", sch._select() is None)
        store.set_setting("scheduler_paused", False)
        check("fifo: order after scheduler pause/resume",
              [x["job_id"] for x in sch.queue_order()] == [B, C, A, D])
        # ControlCenter.status on same root
        store.close()
        cc = ControlCenter(root, import_existing=False)
        try:
            st = cc.status()
            m = {j["id"]: (j["queue_position"], j["queue_kind"]) for j in st["jobs"]}
            check("fifo: status positions", m[B] == (1, "scan") and m[C] == (2, "scan")
                  and m[A] == (3, "scan") and m[D] == (4, "export"), m)
            check("fifo: status queue length", st["queue"]["length"] == 4, st["queue"])
        finally:
            cc.store.close(); cc.lock.close()


def scenario_restart():
    with TemporaryDirectory() as tmp, \
            patch("biliflow.job_import.probe_video", fake_probe), \
            patch("biliflow.job_import.duration_seconds", lambda _p: 10.0), \
            patch("biliflow.scheduler.pipeline_stages", side_effect=lambda **_: list(STAGES)):
        root = make_root(tmp)
        names = ["rerun", "export", "paused", "interrupted", "building", "building_done",
                 "rendering", "rereviewed", "fresh"]
        queues = {}
        digests = {}
        for n in names:
            status = "READY_FOR_EDIT_PLAN" if n in {"export", "rendering", "rerun", "rereviewed"} else "REVIEW_REQUIRED"
            _, digests[n], queues[n] = add_source(root, n, with_queue=(n != "fresh"), status=status)
        cc = ControlCenter(root, import_existing=True)
        store, sch = cc.store, cc.scheduler
        ids = {j["source_path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1][:-4]: int(j["id"]) for j in store.list_jobs()}
        # baseline states after first import
        base = {n: store.get_job(ids[n])["state"] for n in names}
        print("first import:", base)
        # old exports for rerun job and re-reviewed job
        manifest(root, review_export_paths(root, queues["rerun"])[1], digests["rerun"])
        manifest(root, review_export_paths(root, queues["rereviewed"])[1], digests["rereviewed"])
        # rerun job: queued rerun
        sch.rerun(ids["rerun"])
        # queued export
        plan, out, _ = review_export_paths(root, queues["export"])
        sch.queue_render(ids["export"], plan_path=plan, output_path=out)
        # paused mid-scan
        sch.rerun(ids["paused"]); sch.pause_now(ids["paused"])
        # interrupted
        sch.rerun(ids["interrupted"])
        store.update_job(ids["interrupted"], state="INTERRUPTED_RECOVERABLE", stop_mode="PAUSED")
        # building review in progress
        sch.rerun(ids["building"])
        store.update_stage(ids["building"], "scan", state="COMPLETED")
        assert store.claim_queued(ids["building"], "BUILDING_REVIEW", "build_review")
        store.update_stage(ids["building"], "build_review", state="RUNNING", pid=999)
        # building review whose stage completed but _after_success (audit) did not finish
        sch.rerun(ids["building_done"])
        store.update_stage(ids["building_done"], "scan", state="COMPLETED")
        assert store.claim_queued(ids["building_done"], "BUILDING_REVIEW", "build_review")
        store.update_stage(ids["building_done"], "build_review", state="COMPLETED")
        # rendering
        plan, out, _ = review_export_paths(root, queues["rendering"])
        sch.queue_render(ids["rendering"], plan_path=plan, output_path=out)
        assert store.claim_queued(ids["rendering"], "RENDERING", "render")
        store.update_stage(ids["rendering"], "render", state="RUNNING", pid=998)
        # re-reviewed: rerun completed with a NEW review (rev2) and old export exists
        sch.rerun(ids["rereviewed"])
        newq = dict(queues["rereviewed"], updated_at="2026-10-02T00:00:00+00:00", status="REVIEW_REQUIRED",
                    items=[{"id": "y", "decision": None, "start_seconds": 3, "end_seconds": 4}])
        folder = root / "reports" / "jobs" / "rereviewed-run-2"
        folder.mkdir(parents=True)
        (folder / "review-queue.json").write_text(json.dumps(newq), encoding="utf-8")
        rev = store.add_revision(ids["rereviewed"], "reports/jobs/rereviewed-run-2/review-queue.json",
                                 "REVIEW_REQUIRED", newq["updated_at"])
        store.activate_revision(ids["rereviewed"], rev)
        for s in ("scan", "build_review"):
            store.update_stage(ids["rereviewed"], s, state="COMPLETED")
        store.update_job(ids["rereviewed"], state="WAITING_REVIEW", current_stage=None)
        before = {n: store.get_job(ids[n]) for n in names}
        order_before = sch.queue_order()
        cc.store.close(); cc.lock.close()
        # restart
        cc2 = ControlCenter(root, import_existing=True)
        try:
            store, sch = cc2.store, cc2.scheduler
            after = {n: store.get_job(ids[n]) for n in names}
            for n in names:
                print(f"  {n}: {before[n]['state']}/{before[n]['current_stage']}/seq{before[n]['queue_seq']}"
                      f"/rev{before[n]['active_revision']} -> {after[n]['state']}/{after[n]['current_stage']}"
                      f"/seq{after[n]['queue_seq']}/rev{after[n]['active_revision']}")
            check("restart: queued rerun stays QUEUED, no revision",
                  after["rerun"]["state"] == "QUEUED" and after["rerun"]["active_revision"] is None)
            check("restart: queued export stays QUEUED/render",
                  (after["export"]["state"], after["export"]["current_stage"]) == ("QUEUED", "render"))
            check("restart: paused stays PAUSED", after["paused"]["state"] == "PAUSED")
            check("restart: interrupted stays", after["interrupted"]["state"] == "INTERRUPTED_RECOVERABLE")
            check("restart: building recovered", after["building"]["state"] == "INTERRUPTED_RECOVERABLE")
            check("restart: rendering recovered", after["rendering"]["state"] == "INTERRUPTED_RECOVERABLE")
            check("restart: rereviewed stays WAITING_REVIEW on rev2",
                  after["rereviewed"]["state"] == "WAITING_REVIEW"
                  and after["rereviewed"]["active_revision"] == before["rereviewed"]["active_revision"],
                  (after["rereviewed"]["state"], after["rereviewed"]["active_revision"]))
            check("restart: seqs unchanged", all(after[n]["queue_seq"] == before[n]["queue_seq"] for n in names))
            order_after = sch.queue_order()
            check("restart: queue order unchanged", order_after == order_before, (order_before, order_after))
            st = {j["id"]: (j["queue_position"], j["queue_kind"]) for j in cc2.status()["jobs"]}
            check("restart: export kind", st[ids["export"]][1] == "export", st[ids["export"]])
            # resume everything that is resumable and check they all become runnable
            for n in ("paused", "interrupted", "building", "building_done", "rendering"):
                sch.resume(ids[n])
            runnable = {x["job_id"]: x for x in sch.queue_order()}
            for n in ("paused", "interrupted", "building", "building_done", "rendering"):
                j = store.get_job(ids[n])
                check(f"restart+resume: {n} runnable", ids[n] in runnable,
                      f"state={j['state']} stage={j['current_stage']} stages="
                      f"{[(s['name'], s['state']) for s in store.stages(ids[n])]}")
            check("restart+resume: rendering is export kind",
                  runnable.get(ids["rendering"], {}).get("kind") == "export")
        finally:
            cc2.store.close(); cc2.lock.close()


def scenario_building_done_old_behaviour():
    """Same crash window on the old code (f997dde) for comparison is done by a separate script."""


def main():
    for fn in (scenario_fifo, scenario_restart):
        try:
            fn()
        except Exception:
            traceback.print_exc()
            check(fn.__name__ + " crashed", False)
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)} pass, {len(fails)} fail")
    (SCRATCH / "adversarial-results.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1),
                                                      encoding="utf-8")


if __name__ == "__main__":
    main()
