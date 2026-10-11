"""Crash after the last stage COMPLETED, before _after_success: full restart as ControlCenter.__init__ runs it."""
import hashlib, json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from biliflow.job_import import import_existing_project
from biliflow.job_pipeline import PipelineStage
from biliflow.job_store import JobStore
from biliflow.review_workflow import review_export_paths
from biliflow.scheduler import JobScheduler

def setup(tmp):
    root = Path(tmp).resolve()
    for n in ("input", "reports/jobs/a", "output", "state", "work", "logs"):
        (root / n).mkdir(parents=True, exist_ok=True)
    src = root / "input/movie.mp4"; src.write_bytes(b"movie")
    digest = hashlib.sha256(b"movie").hexdigest()
    (root / "reports/jobs/a/scan.json").write_text(json.dumps({"content_style": "animation"}))
    q = {"status": "READY_FOR_EDIT_PLAN", "updated_at": "2026-10-01T00:00:00+00:00",
         "source": {"path": str(src), "sha256": digest, "duration_seconds": 5.0},
         "reports": ["reports/jobs/a/scan.json"], "items": [{"id": "x", "decision": "BLUR", "start_seconds": 1, "end_seconds": 2}]}
    (root / "reports/jobs/a/review-queue.json").write_text(json.dumps(q))
    store = JobStore(root / "state/cc.sqlite3")
    import_existing_project(root, store)
    return root, store, int(store.list_jobs()[0]["id"]), q, digest

def restart(root, store):
    store.close()
    store = JobStore(root / "state/cc.sqlite3")
    store.recover_interrupted(); import_existing_project(root, store)
    sch = JobScheduler(root, store)
    print("  recover_finished_stages ->", sch.recover_finished_stages())
    return store, sch

STAGES = [PipelineStage("scan", "SCANNING_TEXT", tuple()), PipelineStage("build_review", "BUILDING_REVIEW", tuple())]
with patch("biliflow.scheduler.pipeline_stages", side_effect=lambda **_: list(STAGES)):
    print("== build_review crash")
    with TemporaryDirectory() as tmp:
        root, store, jid, q, digest = setup(tmp)
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
        print("  before crash:", {k: store.get_job(jid)[k] for k in ("state", "current_stage", "active_revision")})
        store, sch = restart(root, store)
        j = store.get_job(jid)
        print("  after restart:", {k: j[k] for k in ("state", "current_stage", "active_revision", "error", "queue_seq")},
              "queue_order:", sch.queue_order())
        sch.rerun(jid); print("  rerun accepted:", store.get_job(jid)["state"])
        store.close()
with patch("biliflow.scheduler.pipeline_stages", return_value=[]):
    print("== render crash")
    with TemporaryDirectory() as tmp:
        root, store, jid, q, digest = setup(tmp)
        sch = JobScheduler(root, store)
        plan, out, _ = review_export_paths(root, q)
        sch.queue_render(jid, plan_path=plan, output_path=out)
        job, stage, d = sch._select(); store.claim_queued(jid, d.job_state, d.name)
        out.write_bytes(b"rendered")
        out.with_suffix(".mp4.manifest.json").write_text(json.dumps({"status": "COMPLETED", "source": {"sha256": digest},
            "output": {"path": out.relative_to(root).as_posix()}}))
        store.update_stage(jid, "render", state="COMPLETED")
        store, sch = restart(root, store)
        j = store.get_job(jid)
        print("  after restart:", j["state"], j["current_stage"], j["error"], "order:", sch.queue_order(),
              "final_output:", [a["path"] for a in store.artifacts(jid) if a["kind"] == "final_output"])
        store.close()
