"""Independent verification of the batch-1 fix round (temp roots only)."""
import hashlib, json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from biliflow.job_import import import_existing_project
from biliflow.job_pipeline import PipelineStage
from biliflow.job_store import JobStore
from biliflow.review_workflow import review_export_paths
from biliflow.scheduler import JobScheduler

results = []
def check(name, ok, info=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name + (f" :: {info}" if info else ""))

def setup(tmp, n_sources=1):
    root = Path(tmp).resolve()
    for n in ("input", "reports/jobs/a", "output", "state", "work", "logs"):
        (root / n).mkdir(parents=True, exist_ok=True)
    qs = []
    for i in range(n_sources):
        body = f"movie{i}".encode()
        src = root / f"input/movie{i}.mp4"; src.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        d = root / f"reports/jobs/a{i}"; d.mkdir(parents=True, exist_ok=True)
        (d / "scan.json").write_text(json.dumps({"content_style": "animation"}))
        q = {"status": "READY_FOR_EDIT_PLAN", "updated_at": "2026-10-01T00:00:00+00:00",
             "source": {"path": str(src), "sha256": digest, "duration_seconds": 5.0},
             "reports": [f"reports/jobs/a{i}/scan.json"],
             "items": [{"id": "x", "decision": "BLUR", "start_seconds": 1, "end_seconds": 2}]}
        (d / "review-queue.json").write_text(json.dumps(q))
        qs.append((q, digest))
    store = JobStore(root / "state/cc.sqlite3")
    import_existing_project(root, store)
    ids = sorted(int(j["id"]) for j in store.list_jobs())
    return root, store, ids, qs

def restart(root, store, full=True):
    store.close()
    store = JobStore(root / "state/cc.sqlite3")
    store.recover_interrupted(); import_existing_project(root, store)
    sch = JobScheduler(root, store)
    if full:
        sch.recover_finished_stages()
    return store, sch

STAGES = [PipelineStage("scan", "SCANNING_TEXT", tuple()), PipelineStage("build_review", "BUILDING_REVIEW", tuple())]

# S1: render crash, restart WITHOUT recover_finished_stages, then Tiếp tục -> resume finishes it.
with patch("biliflow.scheduler.pipeline_stages", return_value=[]):
    with TemporaryDirectory() as tmp:
        root, store, (jid,), [(q, digest)] = setup(tmp)
        sch = JobScheduler(root, store)
        plan, out, _ = review_export_paths(root, q)
        sch.queue_render(jid, plan_path=plan, output_path=out)
        job, stage, d = sch._select(); store.claim_queued(jid, d.job_state, d.name)
        out.write_bytes(b"rendered")
        out.with_suffix(".mp4.manifest.json").write_text(json.dumps({"status": "COMPLETED", "source": {"sha256": digest},
            "output": {"path": out.relative_to(root).as_posix()}}))
        store.update_stage(jid, "render", state="COMPLETED")
        store, sch = restart(root, store, full=False)
        check("S1 render crash -> INTERRUPTED after partial restart", store.get_job(jid)["state"] == "INTERRUPTED_RECOVERABLE")
        sch.resume(jid); j = store.get_job(jid)
        check("S1 Tiếp tục finishes render crash -> COMPLETED", j["state"] == "COMPLETED" and j["queue_seq"] is None,
              (j["state"], j["queue_seq"]))
        # S5: output deleted -> fallback: render stage pending, runnable export
        store.close()
    with TemporaryDirectory() as tmp:
        root, store, (jid,), [(q, digest)] = setup(tmp)
        sch = JobScheduler(root, store)
        plan, out, _ = review_export_paths(root, q)
        sch.queue_render(jid, plan_path=plan, output_path=out)
        job, stage, d = sch._select(); store.claim_queued(jid, d.job_state, d.name)
        store.update_stage(jid, "render", state="COMPLETED")  # but no output written
        store, sch = restart(root, store)
        j = store.get_job(jid)
        st = {s["name"]: s["state"] for s in store.stages(jid)}
        check("S5 render w/o output -> INTERRUPTED with render PENDING", j["state"] == "INTERRUPTED_RECOVERABLE" and st.get("render") == "PENDING", (j["state"], st))
        sch.resume(jid)
        check("S5 resume -> export runnable", sch.queue_order() == [{"job_id": jid, "position": 1, "kind": "export"}], sch.queue_order())
        store.close()

with patch("biliflow.scheduler.pipeline_stages", side_effect=lambda **_: list(STAGES)):
    # S2: pause landed during build_review _after_success, then crash -> PAUSED + COMPLETED stage.
    with TemporaryDirectory() as tmp:
        root, store, (jid,), [(q, digest)] = setup(tmp)
        sch = JobScheduler(root, store)
        sch.rerun(jid)
        for name in ("scan", "build_review"):
            job, stage, d = sch._select(); store.claim_queued(jid, d.job_state, d.name)
            store.update_stage(jid, name, state="COMPLETED")
            if name == "scan":
                sch._after_success(jid, name, d)
        qp = root / "reports/jobs" / sch._pipeline_key(store.get_job(jid)) / "review-queue.json"
        qp.parent.mkdir(parents=True, exist_ok=True)
        qp.write_text(json.dumps(dict(q, status="REVIEW_REQUIRED", updated_at="2026-10-02T00:00:00+00:00",
                                      items=[{"id": "n", "decision": None, "start_seconds": 1, "end_seconds": 2}])))
        sch.pause_now(jid)
        store, sch = restart(root, store)
        j = store.get_job(jid)
        check("S2 paused-in-after_success stays PAUSED on restart", j["state"] == "PAUSED", j["state"])
        sch.resume(jid); j = store.get_job(jid)
        check("S2 Tiếp tục records the review -> WAITING_REVIEW rev2", j["state"] == "WAITING_REVIEW" and j["active_revision"] == 2,
              (j["state"], j["active_revision"]))
        store.close()

    # S3: resume() on an in-process BUILDING_REVIEW job (worker still inside _after_success).
    with TemporaryDirectory() as tmp:
        root, store, (jid,), [(q, digest)] = setup(tmp)
        sch = JobScheduler(root, store)
        sch.rerun(jid)
        for name in ("scan", "build_review"):
            job, stage, d = sch._select(); store.claim_queued(jid, d.job_state, d.name)
            store.update_stage(jid, name, state="COMPLETED")
            if name == "scan":
                sch._after_success(jid, name, d)
        qp = root / "reports/jobs" / sch._pipeline_key(store.get_job(jid)) / "review-queue.json"
        qp.parent.mkdir(parents=True, exist_ok=True)
        qp.write_text(json.dumps(dict(q, status="REVIEW_REQUIRED", updated_at="2026-10-02T00:00:00+00:00",
                                      items=[{"id": "n", "decision": None, "start_seconds": 1, "end_seconds": 2}])))
        before = len(store.events(jid)) if hasattr(store, "events") else None
        try:
            sch.resume(jid); outcome = store.get_job(jid)["state"]
        except ValueError as e:
            outcome = f"refused: {e}"
        print("INFO S3 resume() on live BUILDING_REVIEW with stage COMPLETED ->", outcome)
        store.close()

    # S4: FIFO across start/rerun/export, resume keeps place, heartbeat no reorder, repeat finalize keeps place.
with patch("biliflow.scheduler.pipeline_stages", side_effect=lambda **_: list(STAGES)):
    with TemporaryDirectory() as tmp:
        root, store, ids, qs = setup(tmp, 4)
        a, b, c, dd = ids
        sch = JobScheduler(root, store)
        sch.rerun(b); sch.rerun(a)
        plan, out, _ = review_export_paths(root, qs[ids.index(c)][0])
        sch.queue_render(c, plan_path=plan, output_path=out)
        sch.rerun(dd)
        order = [x["job_id"] for x in sch.queue_order()]
        check("S4 FIFO click order b,a,c(export),d", order == [b, a, c, dd], order)
        kinds = {x["job_id"]: x["kind"] for x in sch.queue_order()}
        check("S4 kinds", kinds[c] == "export" and kinds[a] == "scan", kinds)
        sch.stop_after_stage(a)  # queued -> PAUSED (keeps place)
        sch.queue_render(c, plan_path=plan, output_path=out)  # repeat finalize
        sch.resume(a)
        order = [x["job_id"] for x in sch.queue_order()]
        check("S4 resume keeps place; repeat finalize keeps place", order == [b, a, c, dd], order)
        # run b's first stage with heartbeats
        job, stage, d = sch._select(); store.claim_queued(job["id"], d.job_state, d.name)
        store.update_stage(job["id"], d.name, heartbeat_at="x", progress=0.5)
        store.update_stage(job["id"], d.name, state="COMPLETED"); sch._after_success(job["id"], d.name, d)
        order = [x["job_id"] for x in sch.queue_order()]
        check("S4 b back to QUEUED after stage keeps head", order == [b, a, c, dd], order)
        store.close()
        # migration-independent: reopen keeps order
        store = JobStore(root / "state/cc.sqlite3"); sch = JobScheduler(root, store)
        check("S4 reopen keeps order", [x["job_id"] for x in sch.queue_order()] == [b, a, c, dd])
        store.close()

print(f"{sum(results)} pass, {len(results) - sum(results)} fail")
