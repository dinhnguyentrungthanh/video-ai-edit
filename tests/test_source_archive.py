"""source_archive: "Lưu trữ" and "Khôi phục bản xuất" (batch 4d).

Every test runs on a temporary project root under ``<install>/temp`` with a real
JobStore and JobScheduler (never the project's state, never ``<install>/input``,
``output`` or ``archive``). The recycler is always a fake that moves the file
into a folder outside the root, and ``bin_info`` is a fake BinInfo with the real
numbers of drive E:. The module setup also replaces ``recycle_bin._shell_delete``
with a function that fails the test, so nothing here can reach the real
Recycle Bin.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import (
    export_guards, recycle_bin, source_archive, source_archive_files, source_archive_restore, source_cleanup,
)
from biliflow.export_guards import (
    SOURCE_ARCHIVED_MEDIA_MESSAGE,
    SOURCE_ARCHIVED_MESSAGE,
    SOURCE_ARCHIVED_REVIEW_REFUSAL,
    SOURCE_ARCHIVED_STOP_REFUSAL,
    ActionConflict,
    review_summary,
)
from biliflow.job_import import import_existing_project
from biliflow.job_pipeline import safe_job_key
from biliflow.job_store import JobStore, now_iso, sha256_file
from biliflow.recycle_bin import BinInfo, RecycleFailed, RecycleRefused, RecycleResult, RecycleTimeout
from biliflow.review_evidence import ReviewMediaError
from biliflow.export_identity import legacy_export_paths
from biliflow.review_workflow import approved_operations, review_export_paths
from biliflow.scheduler import InputWatcher, JobScheduler
from biliflow.source_archive import (
    ARCHIVED_EXPORT_MESSAGE,
    ARCHIVED_MESSAGE,
    archive_folder,
    archive_hint,
    archive_row_summary,
    assess_archive,
    execute_archive,
    preview_archive,
)
from biliflow.source_archive_restore import reconcile_pending_archives, restore_archive


GUID = "{2fd9f59c-d156-40e6-b5c9-93b787892ee9}"
MAX_BYTES = 52_157_218_816  # MaxCapacity 49741 MiB
USED_BYTES = 11_823_971_925
ITEMS = 7
NEAR_FULL = MAX_BYTES - recycle_bin.CAPACITY_MARGIN_BYTES
NOW = datetime.now(timezone.utc)
FRESH_PREVIEW = object()
TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
RECORD = "E:\\$Recycle.Bin\\S-1-5-21-1000\\$IREC123.mp4"

_SHELL_PATCH = None


def _refuse_real_shell(*args, **kwargs):
    raise AssertionError("real Recycle Bin call in a test")


def setUpModule():
    global _SHELL_PATCH
    _SHELL_PATCH = patch.object(recycle_bin, "_shell_delete", new=_refuse_real_shell)
    _SHELL_PATCH.start()


def tearDownModule():
    global _SHELL_PATCH
    if _SHELL_PATCH is not None:
        _SHELL_PATCH.stop()
        _SHELL_PATCH = None


def iso(moment):
    return moment.astimezone().isoformat()


def item(item_id, decision="KEEP", decided_at=None):
    return {
        "id": item_id, "category": "adult", "start_seconds": 1.0, "end_seconds": 2.0,
        "decision": decision, "decided_at": decided_at, "decision_note": "ok", "decision_actor": "control_center_user",
    }


def blur_item(item_id, *, decided_at=None):
    return {
        "id": item_id, "category": "advertising", "start_seconds": 3.0, "end_seconds": 4.5,
        "decision": "BLUR", "decided_at": decided_at, "reasons": ["logo"], "evidence": [],
        "decision_region_source_pixels": {"x": 10, "y": 20, "width": 200, "height": 96},
        "decision_blur_edge_mode": "vertical_only", "detected_interval": {"start_seconds": 2.5, "end_seconds": 4.0},
    }


def tree_digest(path):
    digest = hashlib.sha256()
    for file in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


class FakeBin:
    def __init__(self, used=USED_BYTES, error=None):
        self.used, self.error = used, error
        self.calls = []

    def __call__(self, path):
        self.calls.append(Path(path))
        if self.error is not None:
            raise self.error
        return BinInfo("E:", "E:\\", GUID, MAX_BYTES, self.used, ITEMS)


class FakeRecycler:
    """Moves the file into a bin folder outside the root; modes by file name (or a callable)."""

    def __init__(self, bin_dir):
        self.bin_dir = Path(bin_dir)
        self.calls = []
        self.modes = {}
        self.callbacks = []

    def __call__(self, path, *, allowed_root, expected_size, timeout, on_late_result):
        self.calls.append({"path": Path(path), "allowed_root": Path(allowed_root),
                           "expected_size": expected_size, "timeout": timeout})
        mode = self.modes.get(Path(path).name, "move")
        if callable(mode):
            mode = mode(Path(path))
        if mode == "fail":
            raise RecycleFailed(recycle_bin.SHARING_MESSAGE)
        if mode == "refused":
            raise RecycleRefused(recycle_bin.EXPORT_WORDING.size)
        if mode == "timeout":
            self.callbacks.append(on_late_result)
            raise RecycleTimeout(recycle_bin.TIMEOUT_MESSAGE)
        token = uuid.uuid4().hex[:6].upper()
        os.replace(path, self.bin_dir / f"$R{token}{Path(path).suffix}")
        record = self.bin_dir / f"$I{token}{Path(path).suffix}"
        record.write_bytes(b"record")
        verified = mode != "unverified"
        return RecycleResult(str(path), expected_size, verified, str(record) if verified else None, 0.01)


class ArchiveFixture(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "reports/jobs", "output", "work", "state", "logs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        bin_temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(bin_temp.cleanup)
        self.bin_dir = Path(bin_temp.name).resolve()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        self.scheduler = JobScheduler(self.root, self.store)
        patcher = patch("biliflow.scheduler.pipeline_stages", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        source_cleanup._CACHE.clear()
        self.addCleanup(source_cleanup._CACHE.clear)
        self.recycler = FakeRecycler(self.bin_dir)
        self.bin = FakeBin()
        self.hashed = []

    def legacy_cleanup(self, job_id):
        """What the old "Dọn video gốc" left (jobs 40-60): the source in the bin and a RECYCLED row."""
        job = self.store.get_job(job_id)
        source = Path(job["source_path"])
        row_id = self.store.add_source_cleanup(
            job_id=job_id, kind="EXPORTED", source_path=str(source.resolve()),
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"],
            mtime_ns=job["source_mtime_ns"],
        )
        token = uuid.uuid4().hex[:6].upper()
        os.replace(source, self.bin_dir / f"$R{token}{source.suffix}")
        record = self.bin_dir / f"$I{token}{source.suffix}"
        record.write_bytes(b"record")
        return source_cleanup._settle_recycled(self.store, row_id, verified=True, record=str(record))

    def hasher(self, path):
        self.hashed.append(Path(path))
        return sha256_file(Path(path))

    # ----------------------------------------------------------------- jobs
    def make_job(self, name, *, state, items, status="READY_FOR_EDIT_PLAN", source_name=None, job_key=None):
        source = self.root / "input" / (source_name or f"{name}.mp4")
        source.write_bytes(name.encode() * 512)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        folder = self.root / "reports" / "jobs" / name
        folder.mkdir(parents=True)
        queue = {
            "status": status, "updated_at": iso(NOW),
            "source": {"path": str(source), "sha256": digest, "duration_seconds": 60.0},
            "reports": [], "items": items, "advisory_items": [],
        }
        (folder / "review-queue.json").write_text(json.dumps(queue), encoding="utf-8")
        info = source.stat()
        job = self.store.upsert_job(
            job_key=job_key or name, source_path=source, source_sha256=digest,
            source_size_bytes=info.st_size, source_mtime_ns=info.st_mtime_ns,
            content_style="animation", state=state,
        )
        self.store.update_job(job["id"], active_queue_path=f"reports/jobs/{name}/review-queue.json",
                              active_revision=1, progress=1.0)
        return int(job["id"])

    def queue_file(self, job_id):
        return self.root / self.store.get_job(job_id)["active_queue_path"]

    def queue(self, job_id):
        return json.loads(self.queue_file(job_id).read_text(encoding="utf-8"))

    def output_of(self, job_id):
        return review_export_paths(self.root, self.queue(job_id))[1]

    def manifest_of(self, job_id):
        output = self.output_of(job_id)
        return output.with_name(output.name + ".manifest.json")

    def make_exported_job(self, name, *, items=None, operations=False, source_name=None, job_key=None,
                          legacy_name=False):
        decided = iso(NOW - timedelta(hours=1))
        job_id = self.make_job(name, state="COMPLETED", source_name=source_name, job_key=job_key,
                               items=items or [item("a", decided_at=decided), item("b", decided_at=decided)])
        job = self.store.get_job(job_id)
        paths = legacy_export_paths if legacy_name else review_export_paths
        plan_path, output, _ = paths(self.root, self.queue(job_id))
        output.write_bytes(b"OUTPUT" + name.encode() * 300)
        output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        manifest = {
            "schema_version": 1, "status": "COMPLETED", "created_at": iso(NOW),
            "edit_plan": plan_path.relative_to(self.root).as_posix(),
            "source": {"path": job["source_path"], "sha256": job["source_sha256"], "duration_seconds": 60.0,
                       "sha256_after_render": job["source_sha256"], "modified": False},
            "output": {"path": output.relative_to(self.root).as_posix(), "bytes": output.stat().st_size,
                       "sha256": output_sha},
            "encoding": {"full_decode_validation_passed": True},
        }
        if operations:
            manifest["operations"] = approved_operations(self.queue(job_id))
        output.with_name(output.name + ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        plan_path.write_text(json.dumps({"status": "FINAL_RENDER_COMPLETED",
                                         "review_queue": job["active_queue_path"]}), encoding="utf-8")
        self.store.add_artifact(job_id, stage_name="render", kind="final_output",
                                path=output.relative_to(self.root).as_posix(), sha256=output_sha,
                                bytes_count=output.stat().st_size)
        return job_id

    def make_skipped_job(self, name, **kwargs):
        job_id = self.make_job(name, state="SKIPPED", items=[item("a", decided_at=iso(NOW))], **kwargs)
        job = self.store.get_job(job_id)
        summary = review_summary(self.queue(job_id))
        self.store.set_setting(f"skip:{job_id}", {
            "queue_path": job["active_queue_path"], "revision": job["active_revision"],
            "main_items": summary["main_items"], "advisory_items": summary["advisory_items"],
            "decisions": summary["decisions"], "skipped_at": now_iso(), "actor": "control_center_user",
        })
        return job_id

    def source_of(self, job_id):
        return Path(self.store.get_job(job_id)["source_path"])

    def target_of(self, job_id):
        job = self.store.get_job(job_id)
        return archive_folder(self.root, job["job_key"]) / self.source_of(job_id).name

    def preview(self, ids, **kwargs):
        kwargs.setdefault("bin_info", self.bin)
        return preview_archive(self.root, self.store, self.scheduler, ids, **kwargs)

    def archive(self, ids, preview_id=FRESH_PREVIEW, **kwargs):
        if preview_id is FRESH_PREVIEW:
            preview_id = self.preview(ids)["preview_id"]
        kwargs.setdefault("recycler", self.recycler)
        kwargs.setdefault("bin_info", self.bin)
        kwargs.setdefault("hasher", self.hasher)
        return execute_archive(self.root, self.store, self.scheduler, ids, preview_id, **kwargs)

    def restore(self, job_id, **kwargs):
        kwargs.setdefault("hasher", self.hasher)
        return restore_archive(self.root, self.store, self.scheduler, job_id, **kwargs)

    def reason(self, job_id):
        preview = self.preview([job_id])
        self.assertEqual(preview["eligible"], [], preview)
        return preview["ineligible"][0]["reason"]

    def events(self, job_id, kind):
        return [event for event in self.store.events(job_id) if event["event_type"] == kind]

    def watcher_row(self, path):
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT * FROM watcher_files WHERE path=?", (str(Path(path).resolve()),)
            ).fetchone()
        return None if row is None else dict(row)

    def stub_center(self):
        from biliflow.control_center import ControlCenter

        center = ControlCenter.__new__(ControlCenter)
        center.root, center.store, center.scheduler = self.root, self.store, self.scheduler
        center._stopping = threading.Event()
        return center


class ArchiveTests(ArchiveFixture):
    def test_exported_job_archived_manifest_written_export_and_manifest_recycled(self):
        job_id = self.make_exported_job("tap12")
        source = self.source_of(job_id)
        size, mtime = source.stat().st_size, source.stat().st_mtime_ns
        data = source.read_bytes()
        self.store.observe_file(source, size, mtime)
        self.store.mark_file_imported(source, job_id)
        output, manifest = self.output_of(job_id), self.manifest_of(job_id)
        output_bytes, manifest_bytes = output.stat().st_size, manifest.stat().st_size
        exported_at = json.loads(manifest.read_text(encoding="utf-8"))["created_at"]
        kept = {"reports": tree_digest(self.root / "reports"), "work": tree_digest(self.root / "work")}
        preview = self.preview([job_id])
        self.assertEqual(preview["eligible"], [{
            "job_id": job_id, "name": "tap12.mp4", "file_name": "tap12.mp4", "kind": "EXPORTED",
            "size_bytes": size, "archive_path": "archive/sources/tap12/tap12.mp4", "output_name": output.name,
            "output_bytes": output_bytes, "manifest_bytes": manifest_bytes, "exported_at": exported_at,
            "skipped_at": None,
        }])
        freed = output_bytes + manifest_bytes
        self.assertEqual((preview["count"], preview["archive_bytes"], preview["freed_bytes"], preview["blocked"]),
                         (1, size, freed, None))
        self.assertEqual(preview["recycle_bin"], {"volume": "E:", "used_bytes": USED_BYTES, "items": ITEMS,
                                                  "max_bytes": MAX_BYTES, "after_bytes": USED_BYTES + freed})
        self.assertEqual((self.hashed, self.recycler.calls), ([], []))

        result = self.archive([job_id], preview["preview_id"])

        target = self.target_of(job_id)
        self.assertEqual(result, {
            "results": [{"job_id": job_id, "name": "tap12.mp4", "status": "ARCHIVED",
                         "message": ARCHIVED_EXPORT_MESSAGE, "size_bytes": size, "output_bytes": output_bytes,
                         "manifest_bytes": manifest_bytes}],
            "archived_count": 1, "archived_bytes": size, "freed_bytes": freed, "failed_count": 0, "pending": 0,
        })
        self.assertEqual(self.recycler.calls, [
            {"path": output, "allowed_root": self.root / "output", "expected_size": output_bytes, "timeout": 60.0},
            {"path": manifest, "allowed_root": self.root / "output", "expected_size": manifest_bytes,
             "timeout": 60.0},
        ])
        self.assertFalse(source.exists())
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), (data, mtime))
        self.assertFalse(output.exists() or manifest.exists())
        self.assertTrue((target.parent / "archive-manifest.json").is_file())
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ["archive-manifest.json", "tap12.mp4"])
        self.assertEqual(self.hashed, [source.resolve(), output, target])
        row = self.store.latest_source_archive(job_id)
        self.assertEqual(
            {key: row[key] for key in ("state", "phase", "kind", "source_verified", "export_recycled",
                                       "export_verified", "manifest_recycled", "error", "archive_path",
                                       "manifest_path", "output_bytes", "output_manifest_bytes")},
            {"state": "ARCHIVED", "phase": "MANIFEST_RECYCLING", "kind": "EXPORTED", "source_verified": True,
             "export_recycled": True, "export_verified": True, "manifest_recycled": True, "error": None,
             "archive_path": str(target), "manifest_path": str(target.parent / "archive-manifest.json"),
             "output_bytes": output_bytes, "output_manifest_bytes": manifest_bytes},
        )
        self.assertTrue(row["export_record"].startswith(str(self.bin_dir)))
        self.assertTrue(row["archived_at"])
        [event] = self.events(job_id, "SOURCE_ARCHIVED")
        self.assertEqual((event["message"], event["level"]), (ARCHIVED_EXPORT_MESSAGE, "INFO"))
        self.assertEqual(event["payload"]["archive_path"], str(target))
        watcher = self.watcher_row(source)
        self.assertEqual((watcher["size_bytes"], watcher["mtime_ns"], watcher["imported_job_id"]), (-1, -1, None))
        # Reports and work are byte for byte the same; the job keeps its state and is locked.
        self.assertEqual({"reports": tree_digest(self.root / "reports"), "work": tree_digest(self.root / "work")},
                         kept)
        self.assertEqual(self.store.get_job(job_id)["state"], "COMPLETED")
        self.assertEqual(self.store.source_lock(job_id), "archived")
        self.assertEqual(set(self.bin.calls), {self.root / "output"})
        self.assertEqual(self.reason(job_id), "Video gốc đã được lưu trữ")
        cleanup = source_cleanup.preview_cleanup(self.root, self.store, self.scheduler, [job_id])
        self.assertEqual(cleanup["ineligible"][0]["reason"], "Video gốc đang ở kho lưu trữ")
        summary = archive_row_summary(row)
        self.assertEqual(
            {key: summary[key] for key in ("state", "kind", "file_name", "output_name", "export_recycled",
                                           "export_verified", "export_verified_at_archive",
                                           "export_verified_later_at", "export_rechecked_at", "warning", "error")},
            {"state": "ARCHIVED", "kind": "EXPORTED", "file_name": "tap12.mp4", "output_name": output.name,
             "export_recycled": True, "export_verified": True, "export_verified_at_archive": True,
             "export_verified_later_at": None, "export_rechecked_at": None, "warning": None, "error": None},
        )

    def test_an_export_named_before_the_operations_hash_is_archived(self):
        # Exports made before the operations hash keep their decision-hash
        # name; their manifest operations prove them, and that file is recycled.
        job_id = self.make_exported_job("tap14", operations=True, legacy_name=True)
        output = legacy_export_paths(self.root, self.queue(job_id))[1]
        manifest = output.with_name(output.name + ".manifest.json")
        self.assertNotEqual(output, self.output_of(job_id))
        preview = self.preview([job_id])
        self.assertEqual(preview["eligible"][0]["output_name"], output.name)
        result = self.archive([job_id], preview["preview_id"])
        self.assertEqual((result["archived_count"], result["failed_count"]), (1, 0))
        self.assertEqual([call["path"] for call in self.recycler.calls], [output, manifest])
        self.assertFalse(output.exists() or manifest.exists())
        self.assertTrue(self.target_of(job_id).is_file())

    def test_skipped_job_moves_only_the_source(self):
        job_id = self.make_skipped_job("tap30")
        record = self.store.setting(f"skip:{job_id}")
        source = self.source_of(job_id)
        preview = self.preview([job_id])
        entry = preview["eligible"][0]
        self.assertEqual((entry["kind"], entry["output_name"], entry["output_bytes"], entry["manifest_bytes"],
                          entry["skipped_at"]), ("SKIPPED", None, None, None, record["skipped_at"]))
        self.assertEqual((preview["freed_bytes"], preview["recycle_bin"], preview["blocked"]), (0, None, None))
        result = self.archive([job_id], preview["preview_id"])
        self.assertEqual(result["results"][0]["status"], "ARCHIVED")
        self.assertEqual(result["results"][0]["message"], ARCHIVED_MESSAGE)
        self.assertEqual((result["freed_bytes"], self.recycler.calls, self.bin.calls), (0, [], []))
        target = self.target_of(job_id)
        self.assertTrue(target.is_file())
        self.assertFalse(source.exists())
        self.assertEqual(self.hashed, [source.resolve(), target])
        row = self.store.latest_source_archive(job_id)
        self.assertEqual((row["state"], row["phase"], row["kind"], row["export_recycled"], row["output_path"]),
                         ("ARCHIVED", "SOURCE_VERIFIED", "SKIPPED", False, None))
        self.assertEqual(self.store.get_job(job_id)["state"], "SKIPPED")
        self.assertEqual(self.store.setting(f"skip:{job_id}"), record)

    def test_manifest_contents(self):
        decided = iso(NOW - timedelta(hours=1))
        job_id = self.make_exported_job("tap13", operations=True,
                                        items=[item("a", decided_at=decided), blur_item("b", decided_at=decided)])
        queue_bytes = self.queue_file(job_id).read_bytes()
        export_manifest = json.loads(self.manifest_of(job_id).read_text(encoding="utf-8"))
        plan = json.loads((self.root / export_manifest["edit_plan"]).read_text(encoding="utf-8"))
        job = self.store.get_job(job_id)
        source = self.source_of(job_id)
        self.archive([job_id])
        row = self.store.latest_source_archive(job_id)
        target = self.target_of(job_id)
        document = json.loads((target.parent / "archive-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((document["schema_version"], document["kind"], document["archive_id"]),
                         (1, "biliflow_source_archive", row["id"]))
        self.assertEqual(document["manifest_path"], row["manifest_path"])
        self.assertTrue(document["created_at"])
        self.assertEqual((document["job"]["id"], document["job"]["job_key"], document["job"]["state"],
                          document["job"]["active_queue_path"]), (job_id, "tap13", "COMPLETED",
                                                                  job["active_queue_path"]))
        self.assertEqual(document["source"], {
            "original_path": str(source.resolve()), "archive_path": str(target), "file_name": "tap13.mp4",
            "sha256": job["source_sha256"], "size_bytes": job["source_size_bytes"],
            "mtime_ns": job["source_mtime_ns"], "duration_seconds": job["duration_seconds"],
        })
        review = document["review"]
        self.assertEqual((review["queue_sha256"], review["queue_bytes"], review["status"], review["revision"]),
                         (hashlib.sha256(queue_bytes).hexdigest(), len(queue_bytes), "READY_FOR_EDIT_PLAN", 1))
        self.assertEqual(review["summary"], review_summary(json.loads(queue_bytes)))
        blur = next(entry for entry in review["decisions"] if entry["id"] == "b")
        self.assertEqual(
            {key: blur[key] for key in ("decision", "decision_region_source_pixels", "decision_blur_edge_mode",
                                        "detected_interval", "start_seconds", "end_seconds", "decided_at",
                                        "advisory")},
            {"decision": "BLUR", "decision_region_source_pixels": {"x": 10, "y": 20, "width": 200, "height": 96},
             "decision_blur_edge_mode": "vertical_only",
             "detected_interval": {"start_seconds": 2.5, "end_seconds": 4.0}, "start_seconds": 3.0,
             "end_seconds": 4.5, "decided_at": decided, "advisory": False},
        )
        keep = next(entry for entry in review["decisions"] if entry["id"] == "a")
        self.assertEqual((keep["decision_note"], keep["decision_actor"]), ("ok", "control_center_user"))
        export = document["export"]
        self.assertEqual(export["manifest"], export_manifest)
        self.assertEqual(export["edit_plan"], {"path": export_manifest["edit_plan"], "missing": False,
                                               "content": plan})
        self.assertEqual((export["output_sha256"], export["output_bytes"], export["manifest_bytes"]),
                         (export_manifest["output"]["sha256"], export_manifest["output"]["bytes"],
                          row["output_manifest_bytes"]))
        self.assertEqual((document["skip"], document["render_request"]), (None, None))
        # A skipped job keeps its skip record; a missing edit plan is recorded as missing.
        skipped = self.make_skipped_job("tap31")
        self.archive([skipped])
        document = json.loads((self.target_of(skipped).parent / "archive-manifest.json").read_text(encoding="utf-8"))
        self.assertIsNone(document["export"])
        self.assertEqual(document["skip"], self.store.setting(f"skip:{skipped}"))
        planless = self.make_exported_job("tap32")
        plan_path = self.root / json.loads(self.manifest_of(planless).read_text(encoding="utf-8"))["edit_plan"]
        plan_path.unlink()
        self.archive([planless])
        document = json.loads((self.target_of(planless).parent / "archive-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(document["export"]["edit_plan"], {"path": plan_path.relative_to(self.root).as_posix(),
                                                           "missing": True})


class EligibilityTests(ArchiveFixture):
    def test_eligibility_reuses_cleanup_checks_and_maps_reasons(self):
        ready = self.make_job("ready", state="READY_TO_EXPORT", items=[item("a")])
        self.assertEqual(self.reason(ready), "Chỉ lưu trữ được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)")
        self.assertIsNone(archive_hint(self.root, self.store, self.scheduler, self.store.get_job(ready),
                                       cleanup_row=None, archive_row=None))
        older = self.make_exported_job("older", items=[item("a", decided_at=iso(NOW + timedelta(minutes=5)))])
        self.assertEqual(self.reason(older), "Bản xuất hiện có không khớp quyết định duyệt hiện tại "
                                             "(mở “Duyệt cảnh” và xuất lại trước khi lưu trữ)")
        stale = self.make_exported_job("stale")
        plan = self.root / json.loads(self.manifest_of(stale).read_text(encoding="utf-8"))["edit_plan"]
        plan.write_text(json.dumps({"review_queue": "reports/jobs/x/review-queue.json"}), encoding="utf-8")
        self.assertEqual(self.reason(stale), "Bản xuất hiện có không ứng với lần duyệt mới nhất "
                                             "(mở “Duyệt cảnh” và xuất lại trước khi lưu trữ)")
        missing = self.make_exported_job("missing")
        self.source_of(missing).unlink()
        self.assertEqual(self.reason(missing), "Video gốc không còn trong thư mục input")
        linked = self.make_exported_job("linked")
        with patch.object(recycle_bin, "path_refusal", return_value=recycle_bin.LINK_MESSAGE):
            self.assertEqual(self.reason(linked), "Video gốc là liên kết (symlink/junction); không lưu trữ.")
        busy = self.make_exported_job("busy")
        with patch.object(self.scheduler, "is_busy", side_effect=lambda job_id: job_id == busy):
            self.assertEqual(self.reason(busy), "Video đang chạy hoặc đang xếp hàng")
        # One cleanup assessment per hint, none when the caller passes it (status shares it).
        good = self.make_exported_job("good")
        job = self.store.get_job(good)
        with patch.object(source_cleanup, "assess_job", wraps=source_cleanup.assess_job) as assess:
            hint = archive_hint(self.root, self.store, self.scheduler, job, cleanup_row=None, archive_row=None)
            self.assertEqual(assess.call_count, 1)
            base = source_cleanup.assess_job(self.root, self.store, self.scheduler, job, latest_row=None,
                                             archive_row=None)
            again = archive_hint(self.root, self.store, self.scheduler, job, cleanup_row=None, archive_row=None,
                                 base=base)
            self.assertEqual(assess.call_count, 2)
        self.assertEqual(hint, again)
        self.assertEqual(hint, {
            "eligible": True, "kind": "EXPORTED", "reason": None, "size_bytes": job["source_size_bytes"],
            "output_name": self.output_of(good).name, "output_bytes": self.output_of(good).stat().st_size,
            "manifest_bytes": self.manifest_of(good).stat().st_size, "exported_at": base.exported_at,
            "skipped_at": None,
        })
        self.assertEqual(self.hashed, [])

    def test_cleaned_job_needs_bin_restore_first(self):
        job_id = self.make_exported_job("tap14")
        self.legacy_cleanup(job_id)
        self.assertEqual(self.store.source_lock(job_id), "cleaned")
        self.assertEqual(self.reason(job_id),
                         "Video gốc đang ở Thùng rác (đã dọn); khôi phục nó về input trước khi lưu trữ")
        pending = self.make_exported_job("tap15")
        self.store.add_source_cleanup(job_id=pending, kind="EXPORTED", source_path=str(self.source_of(pending)),
                                      source_sha256="ab" * 32, size_bytes=1, mtime_ns=1)
        self.assertEqual(self.reason(pending), "Đang chuyển video gốc này vào Thùng rác")

    def test_target_exists_long_path_link_other_volume_refused(self):
        taken = self.make_exported_job("taken")
        target = self.target_of(taken)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"an older archive")
        self.assertEqual(self.reason(taken), "Kho lưu trữ đã có file “taken.mp4” của video này; BiliFlow không ghi đè")
        # The source path itself fits in 259 characters, its archive path does not.
        room = 250 - len(str(self.root / "input")) - 1
        long_name = ("x" * (room - 4)) + ".mp4"
        long_job = self.make_skipped_job("long", source_name=long_name, job_key="long-" + "k" * 40)
        self.assertLessEqual(len(str(self.source_of(long_job))), 259)
        self.assertEqual(self.reason(long_job),
                         "Đường dẫn trong kho lưu trữ sẽ dài hơn 259 ký tự; không lưu trữ được video này")
        linked = self.make_skipped_job("linked")
        real_is_link = source_archive_files.is_link
        sources = self.root / "archive" / "sources"
        with patch.object(source_archive_files, "is_link",
                          side_effect=lambda path: path == sources or real_is_link(path)):
            self.assertEqual(self.reason(linked), "Thư mục “archive/sources” của kho lưu trữ là liên kết "
                                                  "(symlink/junction) hoặc không phải thư mục; không lưu trữ")
        input_device = os.stat(self.root / "input").st_dev
        with patch.object(source_archive_files, "device",
                          side_effect=lambda path: input_device if path == self.root / "input" else input_device + 1):
            self.assertEqual(self.reason(linked), "Kho lưu trữ không cùng ổ đĩa với thư mục input; không lưu trữ")
        exported = self.make_exported_job("exportlink")
        real_refusal = recycle_bin.path_refusal

        def refusal(path, *, allowed_root, wording=recycle_bin.SOURCE_WORDING):
            if wording is recycle_bin.EXPORT_WORDING:
                return wording.link
            return real_refusal(path, allowed_root=allowed_root, wording=wording)

        with patch.object(recycle_bin, "path_refusal", side_effect=refusal):
            self.assertEqual(self.reason(exported), "Bản xuất là liên kết (symlink/junction); không chuyển vào Thùng rác.")
        bad_key = self.make_skipped_job("badkey", job_key="bad key/..")
        self.assertEqual(self.reason(bad_key), "Mã video không dùng được làm tên thư mục lưu trữ")
        self.assertEqual(self.hashed, [])

    def test_preview_totals_and_id(self):
        first, second = self.make_exported_job("a1"), self.make_exported_job("a2")
        skipped = self.make_skipped_job("a3")
        blocked = self.make_job("a4", state="WAITING_REVIEW", items=[item("a")])
        preview = self.preview(f"{blocked},{skipped},{second},{first},{first}")
        self.assertEqual([entry["job_id"] for entry in preview["eligible"]], [first, second, skipped])
        self.assertEqual([entry["job_id"] for entry in preview["ineligible"]], [blocked])
        sizes = [self.source_of(job_id).stat().st_size for job_id in (first, second, skipped)]
        freed = sum(self.output_of(job_id).stat().st_size + self.manifest_of(job_id).stat().st_size
                    for job_id in (first, second))
        self.assertEqual((preview["count"], preview["archive_bytes"], preview["freed_bytes"]), (3, sum(sizes), freed))
        self.assertEqual(self.preview([first, second, skipped])["preview_id"], preview["preview_id"])
        source = self.source_of(first)
        info = source.stat()
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        self.assertNotEqual(self.preview([first, second, skipped])["preview_id"], preview["preview_id"])
        full = self.preview([second], bin_info=FakeBin(used=NEAR_FULL))
        self.assertTrue(full["blocked"].startswith("Không thể lưu trữ: Thùng rác của ổ E: đang chứa "), full["blocked"])
        refused = self.preview([second], bin_info=FakeBin(error=RecycleRefused("Ổ E: không phải ổ cứng cố định.")))
        self.assertEqual((refused["recycle_bin"], refused["blocked"]), (None, "Ổ E: không phải ổ cứng cố định."))
        unknown = self.preview([999])
        self.assertEqual(unknown["ineligible"], [{"job_id": 999, "name": "", "reason": "Không tìm thấy video #999"}])
        for value in ("", "a", [True], list(range(1, 52)), 7):
            with self.subTest(ids=value), self.assertRaises(ValueError) as caught:
                self.preview(value)
            self.assertEqual(str(caught.exception), "Chọn từ 1 đến 50 video mỗi lần lưu trữ.")
        self.assertEqual(self.hashed, [])


class ExecuteTests(ArchiveFixture):
    def rows(self):
        with self.store._lock:
            return [dict(row) for row in self.store._connection.execute("SELECT * FROM source_archives").fetchall()]

    def test_execute_conflicts(self):
        job_id = self.make_exported_job("tap12")
        old = self.preview([job_id])["preview_id"]
        source = self.source_of(job_id)
        info = source.stat()
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        with self.assertRaises(ActionConflict) as caught:
            self.archive([job_id], old)
        self.assertEqual((caught.exception.code, str(caught.exception)),
                         ("preview_changed", "Danh sách đã thay đổi, hãy xem lại."))
        self.assertNotEqual(caught.exception.preview["preview_id"], old)
        for value in (None, "", "abc", "A" * 64, 12):
            with self.subTest(preview_id=value), self.assertRaises(ValueError) as error:
                self.archive([job_id], value)
            self.assertEqual(str(error.exception), "Thiếu mã xem trước; hãy mở lại hộp thoại lưu trữ.")
        busy = "Đang dọn, lưu trữ hoặc khôi phục video gốc; chờ lượt trước xong rồi thử lại."
        self.assertTrue(source_cleanup.SOURCE_FILE_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(ActionConflict) as caught:
                self.archive([job_id])
        finally:
            source_cleanup.SOURCE_FILE_LOCK.release()
        self.assertEqual((caught.exception.code, str(caught.exception)), ("busy", busy))
        with patch.object(recycle_bin, "operations_in_progress", return_value=frozenset({"E:\\x.mp4"})):
            with self.assertRaises(ActionConflict) as caught:
                self.archive([job_id], "b" * 64)
        self.assertEqual(caught.exception.code, "busy")
        unavailable = FakeBin(error=RecycleRefused("Không đọc được dung lượng Thùng rác của ổ E:; không dọn được."))
        with self.assertRaises(ActionConflict) as caught:
            self.archive([job_id], self.preview([job_id], bin_info=unavailable)["preview_id"], bin_info=unavailable)
        self.assertEqual(caught.exception.code, "bin_unavailable")
        full = FakeBin(used=NEAR_FULL)
        with self.assertRaises(ActionConflict) as caught:
            self.archive([job_id], bin_info=full)
        self.assertEqual(caught.exception.code, "bin_capacity")
        self.assertTrue(str(caught.exception).startswith("Không thể lưu trữ:"))
        nothing = self.make_job("nothing", state="WAITING_REVIEW", items=[item("a")])
        with self.assertRaises(ValueError) as error:
            self.archive([nothing])
        self.assertEqual(str(error.exception), "Không có video nào lưu trữ được trong danh sách đã chọn.")
        with self.assertRaises(ValueError) as error:
            self.archive("1,x", "a" * 64)
        self.assertEqual(str(error.exception), "Chọn từ 1 đến 50 video mỗi lần lưu trữ.")
        self.assertEqual((self.recycler.calls, self.hashed, self.rows()), ([], [], []))
        self.assertTrue(source.is_file())
        self.assertFalse((self.root / "archive").exists())

    def test_skipped_only_batch_ignores_unavailable_bin(self):
        first, second = self.make_skipped_job("tap40"), self.make_skipped_job("tap41")
        unavailable = FakeBin(error=RecycleRefused("Chưa cấu hình Thùng rác cho Control Center này."))
        preview = self.preview([first, second], bin_info=unavailable)
        self.assertEqual((preview["recycle_bin"], preview["blocked"], preview["count"]), (None, None, 2))
        result = self.archive([first, second], preview["preview_id"], bin_info=unavailable)
        self.assertEqual([entry["status"] for entry in result["results"]], ["ARCHIVED", "ARCHIVED"])
        self.assertEqual((unavailable.calls, self.recycler.calls), ([], []))

    def test_locked_source_rename_fails_cleanly(self):
        job_id = self.make_exported_job("tap42")
        source = self.source_of(job_id).resolve()
        folder = self.target_of(job_id).parent
        real_rename = os.rename
        seen = []

        def rename(src, dst, *args, **kwargs):
            if Path(src) == source:
                # The manifest is written (fsync) before the source moves, under its own .tmp name.
                seen.append(sorted(p.name for p in folder.iterdir()))
                seen.append(json.loads((folder / "archive-manifest.json.tmp").read_text(encoding="utf-8"))["job"]["id"])
                raise PermissionError(13, "The process cannot access the file", str(src), 32)
            return real_rename(src, dst, *args, **kwargs)

        with patch("os.rename", side_effect=rename):
            result = self.archive([job_id])
        self.assertEqual(seen, [["archive-manifest.json.tmp"], job_id])
        message = ("Video gốc đang được mở (ví dụ đang phát trong trang duyệt). Đóng trang duyệt của video này "
                   "rồi thử lại.")
        self.assertEqual((result["results"][0]["status"], result["results"][0]["message"]), ("FAILED", message))
        self.assertTrue(source.is_file())
        self.assertFalse(self.target_of(job_id).exists())
        self.assertTrue(self.output_of(job_id).is_file())
        self.assertEqual(self.recycler.calls, [])
        row = self.store.latest_source_archive(job_id)
        self.assertEqual((row["state"], row["phase"], row["error"]), ("FAILED", "PREPARING", message))
        self.assertIsNone(self.store.source_lock(job_id))
        [event] = self.events(job_id, "SOURCE_ARCHIVE_FAILED")
        self.assertEqual((event["level"], event["payload"]["stage"]), ("WARNING", "move"))
        # Nothing moved, so no manifest describes it: only its own unfinished .tmp was written, and removed.
        self.assertEqual(list(folder.iterdir()), [])
        result = self.archive([job_id])
        self.assertEqual(result["results"][0]["status"], "ARCHIVED")
        self.assertEqual(self.store.latest_source_archive(job_id)["manifest_path"],
                         str(folder / "archive-manifest.json"))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["archive-manifest.json", "tap42.mp4"])

    def test_any_manifest_write_error_fails_cleanly(self):
        # A lone surrogate in a decision note cannot be written as UTF-8 (not an OSError).
        job_id = self.make_job("tap63", state="SKIPPED",
                               items=[{**item("a", decided_at=iso(NOW)), "decision_note": "bad \ud800 note"}])
        job = self.store.get_job(job_id)
        summary = review_summary(self.queue(job_id))
        self.store.set_setting(f"skip:{job_id}", {
            "queue_path": job["active_queue_path"], "revision": job["active_revision"],
            "main_items": summary["main_items"], "advisory_items": summary["advisory_items"],
            "decisions": summary["decisions"], "skipped_at": now_iso(), "actor": "control_center_user",
        })
        result = self.archive([job_id])
        status, message = result["results"][0]["status"], result["results"][0]["message"]
        self.assertEqual(status, "FAILED")
        self.assertTrue(message.startswith("Không ghi được archive-manifest.json vào kho lưu trữ:"), message)
        row = self.store.latest_source_archive(job_id)
        self.assertEqual((row["state"], row["phase"], row["error"]), ("FAILED", "PREPARING", message))
        self.assertIsNone(self.store.source_lock(job_id))
        self.assertTrue(self.source_of(job_id).is_file())
        self.assertEqual(list(self.target_of(job_id).parent.iterdir()), [])
        [event] = self.events(job_id, "SOURCE_ARCHIVE_FAILED")
        self.assertEqual((event["level"], event["payload"]["stage"]), ("WARNING", "manifest"))

    def test_manifest_naming_failure_after_a_good_archive_is_a_warning(self):
        # The manifest gets its final name only once the archive succeeded; a failure there keeps the
        # complete .tmp beside the verified source (nothing is rolled back, nothing is lost).
        job_id = self.make_skipped_job("tap64")
        folder = self.target_of(job_id).parent
        real_rename = os.rename

        def rename(src, dst, *args, **kwargs):
            if Path(src).name.endswith(".tmp"):
                raise PermissionError(13, "Access is denied", str(dst), 5)
            return real_rename(src, dst, *args, **kwargs)

        with patch("os.rename", side_effect=rename):
            result = self.archive([job_id])
        self.assertEqual(result["results"][0]["status"], "ARCHIVED")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["archive-manifest.json.tmp", "tap64.mp4"])
        document = json.loads((folder / "archive-manifest.json.tmp").read_text(encoding="utf-8"))
        row = self.store.latest_source_archive(job_id)
        self.assertEqual(document["archive_id"], row["id"])
        self.assertEqual(row["state"], "ARCHIVED")
        self.assertTrue(row["error"].startswith("Chưa đặt được tên archive-manifest.json cho manifest trong kho "
                                                "lưu trữ ("), row["error"])
        self.assertTrue(row["error"].endswith("); nội dung vẫn ở archive-manifest.json.tmp."), row["error"])
        self.assertEqual(archive_row_summary(row)["warning"], row["error"])

    def test_stop_between_verify_and_export_rolls_back(self):
        job_id = self.make_exported_job("tap69")
        answers = iter([False, False, True])  # the batch loop, before the move, before the export
        result = self.archive([job_id], should_stop=lambda: next(answers))
        self.assertEqual((result["results"][0]["status"], result["results"][0]["message"]),
                         ("NOT_RUN", "BiliFlow đang tắt; đã đưa video gốc về input, chưa lưu trữ."))
        self.assertTrue(self.source_of(job_id).is_file())
        self.assertTrue(self.output_of(job_id).is_file())
        self.assertEqual(list(self.target_of(job_id).parent.iterdir()), [])
        self.assertEqual((self.recycler.calls, self.store.latest_source_archive(job_id)["state"]), ([], "FAILED"))

    def test_rearchive_after_restore_keeps_the_first_manifest(self):
        job_id = self.make_skipped_job("tap70")
        folder = self.target_of(job_id).parent
        self.assertEqual(self.archive([job_id])["results"][0]["status"], "ARCHIVED")
        first = (folder / "archive-manifest.json").read_bytes()
        self.restore(job_id)
        self.assertEqual(self.archive([job_id])["results"][0]["status"], "ARCHIVED")
        self.assertEqual((folder / "archive-manifest.json").read_bytes(), first)
        self.assertEqual(self.store.latest_source_archive(job_id)["manifest_path"],
                         str(folder / "archive-manifest-2.json"))
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["archive-manifest-2.json", "archive-manifest.json", "tap70.mp4"])

    def test_unreadable_archived_copy_is_not_called_a_mismatch(self):
        job_id = self.make_skipped_job("tap65")

        def hasher(path):
            if "archive" in Path(path).parts:
                raise PermissionError(13, "The process cannot access the file", str(path), 32)
            return sha256_file(Path(path))

        result = self.archive([job_id], hasher=hasher)
        status, message = result["results"][0]["status"], result["results"][0]["message"]
        self.assertEqual(status, "FAILED")
        self.assertTrue(message.startswith("Không đọc được video gốc trong kho lưu trữ để kiểm tra SHA-256 ("),
                        message)
        self.assertTrue(message.endswith("); đã đưa video gốc về input."), message)
        self.assertTrue(self.source_of(job_id).is_file())
        self.assertFalse(self.target_of(job_id).exists())
        self.assertEqual(list(self.target_of(job_id).parent.iterdir()), [])  # no manifest of a failed archive
        row = self.store.latest_source_archive(job_id)
        self.assertEqual((row["state"], row["phase"]), ("FAILED", "SOURCE_MOVED"))

    def test_bin_is_read_outside_the_review_and_job_locks(self):
        job_id = self.make_exported_job("tap67")
        held = []

        def probe(path):
            free = []

            def check():
                for lock in (export_guards.REVIEW_QUEUE_IO, self.scheduler.job_action_lock):
                    got = lock.acquire(blocking=False)
                    free.append(got)
                    if got:
                        lock.release()

            thread = threading.Thread(target=check)
            thread.start()
            thread.join()
            held.append(not all(free))
            return self.bin(path)

        result = self.archive([job_id], bin_info=probe)
        self.assertEqual(result["results"][0]["status"], "ARCHIVED")
        self.assertEqual(held, [False, False])  # the fresh preview, then this video's reading
        # The capacity is still compared under the locks: a bin that filled up meanwhile refuses.
        other = self.make_exported_job("tap68")
        readings = iter([USED_BYTES, NEAR_FULL])

        def filling(path):
            return BinInfo("E:", "E:\\", GUID, MAX_BYTES, next(readings), ITEMS)

        result = self.archive([other], bin_info=filling)
        self.assertEqual(result["results"][0]["status"], "FAILED")
        self.assertTrue(result["results"][0]["message"].startswith("Không thể lưu trữ:"))
        self.assertTrue(self.source_of(other).is_file())
        self.assertIsNone(self.store.latest_source_archive(other))
        [event] = self.events(other, "SOURCE_ARCHIVE_FAILED")
        self.assertEqual(event["payload"]["stage"], "recheck")

    def test_sha_mismatch_rolls_back(self):
        job_id = self.make_exported_job("tap43")
        source = self.source_of(job_id).resolve()
        data = source.read_bytes()

        def hasher(path):
            self.hashed.append(Path(path))
            return "0" * 64 if "archive" in Path(path).parts else sha256_file(Path(path))

        result = self.archive([job_id], hasher=hasher)
        message = "Video gốc trong kho lưu trữ không khớp SHA-256; đã đưa video gốc về input."
        self.assertEqual((result["results"][0]["status"], result["results"][0]["message"]), ("FAILED", message))
        self.assertEqual(source.read_bytes(), data)
        self.assertFalse(self.target_of(job_id).exists())
        # Security review (L2): no manifest may claim a file the archive does not hold.
        self.assertEqual(list(self.target_of(job_id).parent.iterdir()), [])
        self.assertEqual(self.recycler.calls, [])
        row = self.store.latest_source_archive(job_id)
        self.assertEqual((row["state"], row["phase"], row["source_verified"]), ("FAILED", "SOURCE_MOVED", False))
        self.assertIsNone(self.store.source_lock(job_id))

    def test_failed_export_recycle_rolls_back(self):
        failing, fine = self.make_exported_job("tap44"), self.make_exported_job("tap45")
        refused = self.make_exported_job("tap46")
        self.recycler.modes[self.output_of(failing).name] = "fail"
        self.recycler.modes[self.output_of(refused).name] = "refused"
        result = self.archive([failing, fine, refused])
        statuses = {entry["job_id"]: (entry["status"], entry["message"]) for entry in result["results"]}
        self.assertEqual(statuses[failing], ("FAILED", "Không chuyển được bản xuất vào Thùng rác ("
                                             f"{recycle_bin.SHARING_MESSAGE}); đã đưa video gốc về input."))
        self.assertEqual(statuses[refused], ("FAILED", "Không chuyển được bản xuất vào Thùng rác (Bản xuất không "
                                             "còn đúng dung lượng đã kiểm tra.); đã đưa video gốc về input."))
        self.assertEqual(statuses[fine][0], "ARCHIVED")
        for job_id in (failing, refused):
            with self.subTest(job=job_id):
                self.assertTrue(self.source_of(job_id).is_file())
                self.assertFalse(self.target_of(job_id).exists())
                self.assertTrue(self.output_of(job_id).is_file())
                row = self.store.latest_source_archive(job_id)
                self.assertEqual((row["state"], row["phase"]), ("FAILED", "EXPORT_RECYCLING"))
                self.assertIsNone(self.store.source_lock(job_id))
                # Rolled back: only its own .tmp had been written, and it is gone.
                self.assertEqual(list(self.target_of(job_id).parent.iterdir()), [])
        # A retry that succeeds keeps one manifest, under the first free name.
        folder = self.target_of(failing).parent
        del self.recycler.modes[self.output_of(failing).name]
        self.assertEqual(self.archive([failing])["results"][0]["status"], "ARCHIVED")
        self.assertEqual(self.store.latest_source_archive(failing)["manifest_path"],
                         str(folder / "archive-manifest.json"))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["archive-manifest.json", "tap44.mp4"])
        # The input path was taken meanwhile: the source cannot go back; the row stays PENDING.
        blocked = self.make_exported_job("tap47")
        source = self.source_of(blocked)

        def occupy(path):
            source.write_bytes(b"another file")
            return "fail"

        self.recycler.modes[self.output_of(blocked).name] = occupy
        result = self.archive([blocked])
        self.assertEqual(result["results"][0]["status"], "PENDING")
        self.assertIn("Không đưa được video gốc về input", result["results"][0]["message"])
        self.assertTrue(self.target_of(blocked).is_file())
        self.assertEqual(self.store.latest_source_archive(blocked)["state"], "PENDING")
        self.assertEqual(self.store.source_lock(blocked), "archived")
        self.assertEqual(self.events(blocked, "SOURCE_ARCHIVE_FAILED")[-1]["level"], "ERROR")

    def test_export_gone_before_recycle_rolls_back(self):
        # The export left output/ on its own while the archived copy was hashed. The recycler refuses
        # before touching anything, so the source goes back now instead of parking the row PENDING.
        job_id = self.make_exported_job("tap62")
        output = self.output_of(job_id)

        def vanish(path):
            os.replace(path, self.bin_dir / "moved-by-user.mp4")
            return "refused"

        self.recycler.modes[output.name] = vanish
        result = self.archive([job_id])
        message = ("Không chuyển được bản xuất vào Thùng rác (Bản xuất không còn đúng dung lượng đã kiểm tra.); "
                   "đã đưa video gốc về input.")
        self.assertEqual((result["results"][0]["status"], result["results"][0]["message"]), ("FAILED", message))
        self.assertTrue(self.source_of(job_id).is_file())
        self.assertFalse(self.target_of(job_id).exists())
        row = self.store.latest_source_archive(job_id)
        self.assertEqual((row["state"], row["phase"], row["export_recycled"]), ("FAILED", "EXPORT_RECYCLING", False))
        self.assertIsNone(self.store.source_lock(job_id))
        [event] = self.events(job_id, "SOURCE_ARCHIVE_FAILED")
        self.assertEqual((event["level"], event["payload"]["stage"]), ("WARNING", "recycle"))
        self.assertEqual(len(self.recycler.calls), 1)  # the export's manifest was never sent
        self.assertEqual(list(self.target_of(job_id).parent.iterdir()), [])

    def test_timeout_pending_stops_batch_and_settles_late(self):
        slow, next_job = self.make_exported_job("tap48"), self.make_exported_job("tap49")
        output = self.output_of(slow)
        self.recycler.modes[output.name] = "timeout"
        result = self.archive([slow, next_job])
        self.assertEqual([(entry["job_id"], entry["status"], entry["message"]) for entry in result["results"]], [
            (slow, "PENDING", recycle_bin.TIMEOUT_MESSAGE),
            (next_job, "NOT_RUN", "Chưa chạy: lần chuyển trước chưa xong."),
        ])
        self.assertEqual(result["pending"], 1)
        row = self.store.latest_source_archive(slow)
        self.assertEqual((row["state"], row["phase"]), ("PENDING", "EXPORT_RECYCLING"))
        self.assertEqual(self.store.source_lock(slow), "archived")
        self.assertEqual(len(self.events(slow, "SOURCE_ARCHIVE_PENDING")), 1)
        self.assertTrue(self.source_of(next_job).is_file())
        folder = self.target_of(slow).parent
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["archive-manifest.json.tmp", "tap48.mp4"])
        # Windows answers later: the row settles ARCHIVED (and only now its manifest is named);
        # the export's own manifest was never sent.
        os.replace(output, self.bin_dir / "$RLATE.mp4")
        self.recycler.callbacks[0](RecycleResult(str(output), 1, True, RECORD, 61.0), None)
        row = self.store.latest_source_archive(slow)
        self.assertEqual((row["state"], row["export_recycled"], row["export_verified"], row["export_record"],
                          row["manifest_recycled"]), ("ARCHIVED", True, True, RECORD, False))
        self.assertEqual(row["error"], source_archive.MANIFEST_LATE_MESSAGE)
        self.assertTrue(self.manifest_of(slow).is_file())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["archive-manifest.json", "tap48.mp4"])
        # A late failure: the export is still in output, so the source goes back (and no manifest stays).
        late = self.make_exported_job("tap50")
        self.recycler.modes[self.output_of(late).name] = "timeout"
        self.archive([late])
        self.recycler.callbacks[-1](None, RecycleFailed(recycle_bin.EXPORT_WORDING.aborted))
        row = self.store.latest_source_archive(late)
        self.assertEqual(row["state"], "FAILED")
        self.assertTrue(self.source_of(late).is_file())
        self.assertEqual(list(self.target_of(late).parent.iterdir()), [])

    def test_manifest_recycle_failure_is_a_warning(self):
        job_id = self.make_exported_job("tap51")
        manifest = self.manifest_of(job_id)
        output_bytes = self.output_of(job_id).stat().st_size
        self.recycler.modes[manifest.name] = "fail"
        result = self.archive([job_id])
        self.assertEqual(result["results"][0], {
            "job_id": job_id, "name": "tap51.mp4", "status": "ARCHIVED", "message": ARCHIVED_EXPORT_MESSAGE,
            "size_bytes": self.store.get_job(job_id)["source_size_bytes"], "output_bytes": output_bytes,
            "manifest_bytes": 0,
        })
        self.assertEqual(result["freed_bytes"], output_bytes)
        row = self.store.latest_source_archive(job_id)
        warning = f"Bản xuất đã vào Thùng rác nhưng manifest của nó thì chưa: {recycle_bin.SHARING_MESSAGE}"
        self.assertEqual((row["state"], row["manifest_recycled"], row["error"]), ("ARCHIVED", False, warning))
        self.assertTrue(manifest.is_file())
        self.assertEqual(archive_row_summary(row)["warning"], warning)
        # A manifest that times out stops the batch (Windows may be asking).
        first, second = self.make_exported_job("tap52"), self.make_exported_job("tap53")
        self.recycler.modes[self.manifest_of(first).name] = "timeout"
        result = self.archive([first, second])
        self.assertEqual([entry["status"] for entry in result["results"]], ["ARCHIVED", "NOT_RUN"])

    def test_unverified_export_still_archived(self):
        job_id = self.make_exported_job("tap54")
        output = self.output_of(job_id)
        self.recycler.modes[output.name] = "unverified"
        result = self.archive([job_id])
        self.assertEqual((result["results"][0]["status"], result["results"][0]["message"]),
                         ("UNVERIFIED", source_archive.UNVERIFIED_MESSAGE))
        self.assertEqual(result["archived_count"], 1)
        row = self.store.latest_source_archive(job_id)
        self.assertEqual((row["state"], row["export_verified"], row["export_record"]), ("ARCHIVED", False, None))
        [event] = self.events(job_id, "SOURCE_ARCHIVE_UNVERIFIED")
        self.assertEqual(event["level"], "WARNING")
        # "Kiểm tra lại Thùng rác" for the export: appends a check, never changes the row.
        calls = []

        def finder(volume_root, original, size, *, since, until=None):
            calls.append((volume_root, original, size, until))
            return RECORD

        checked = source_cleanup.recheck_recycle_record(self.root, self.store, "archive_export", row["id"],
                                                        finder=finder)
        self.assertEqual((checked["found"], checked["record"], checked["job_id"]), (True, RECORD, job_id))
        # Security review (L5): nothing written after this archive settled can answer for it.
        archived = datetime.fromisoformat(row["archived_at"]).timestamp()
        self.assertEqual(calls[0][:3], (output.anchor, str(output), row["output_bytes"]))
        self.assertAlmostEqual(calls[0][3], archived + source_cleanup.RECHECK_UNTIL_SLACK_SECONDS, places=3)
        self.assertEqual(self.store.latest_source_archive(job_id), row)
        summary = archive_row_summary(row, self.store.recycle_check_summary("ARCHIVE_EXPORT")[row["id"]])
        self.assertEqual((summary["export_verified"], summary["export_verified_at_archive"],
                          summary["export_verified_later_at"]), (True, False, checked["checked_at"]))


class LockTests(ArchiveFixture):
    def test_archived_job_locks_edits_finalize_media_skip_rerun(self):
        exported, skipped = self.make_exported_job("tap55"), self.make_skipped_job("tap56")
        self.archive([exported, skipped])
        center = self.stub_center()
        queue_bytes = self.queue_file(exported).read_bytes()
        with self.assertRaises(ValueError) as caught:
            center.ensure_review_editable(exported)
        self.assertEqual(str(caught.exception), SOURCE_ARCHIVED_REVIEW_REFUSAL)
        with patch("biliflow.control_center.build_edit_plan") as plan:
            with self.assertRaises(ValueError) as caught:
                center.finalize(exported)
        self.assertEqual(str(caught.exception), SOURCE_ARCHIVED_MESSAGE)
        plan.assert_not_called()
        with self.assertRaises(ReviewMediaError) as caught:
            center.review_source(exported, self.queue(exported))
        self.assertEqual((caught.exception.status, str(caught.exception)), (404, SOURCE_ARCHIVED_MEDIA_MESSAGE))
        for action, job_id, expected in (
            (center.skip_export, exported, SOURCE_ARCHIVED_MESSAGE),
            (center.unskip_export, skipped, SOURCE_ARCHIVED_MESSAGE),
            (self.scheduler.rerun, exported, SOURCE_ARCHIVED_MESSAGE),
            (self.scheduler.resume, exported, SOURCE_ARCHIVED_MESSAGE),
            (self.scheduler.retry, exported, SOURCE_ARCHIVED_MESSAGE),
            (self.scheduler.cancel, exported, SOURCE_ARCHIVED_STOP_REFUSAL),
        ):
            with self.subTest(action=action.__name__, job=job_id), self.assertRaises(ValueError) as caught:
                action(job_id)
            self.assertEqual(str(caught.exception), expected)
        self.assertEqual(self.queue_file(exported).read_bytes(), queue_bytes)
        self.assertEqual((self.store.get_job(exported)["state"], self.store.get_job(skipped)["state"]),
                         ("COMPLETED", "SKIPPED"))
        facts = export_guards.control_center_job_facts(self.root, self.store.get_job(exported)["source_sha256"])
        self.assertTrue(facts["archived"])
        self.assertEqual(export_guards.standalone_edit_refusal(self.root, self.queue(exported)),
                         SOURCE_ARCHIVED_REVIEW_REFUSAL)


class ReconcileTests(ArchiveFixture):
    def row(self, job_id, phase="PREPARING", *, moved=False, export_gone=False, manifest_gone=False, temp=None,
            **flags):
        """A PENDING row as an interruption left it; ``temp`` (default: ``moved``, as in a real run,
        where the manifest .tmp is written before the source moves) writes its unfinished manifest."""
        temp = moved if temp is None else temp
        job = self.store.get_job(job_id)
        source, target = self.source_of(job_id), self.target_of(job_id)
        exported = job["state"] == "COMPLETED"
        output, manifest = (self.output_of(job_id), self.manifest_of(job_id)) if exported else (None, None)
        row_id = self.store.add_source_archive(
            job_id=job_id, kind="EXPORTED" if exported else "SKIPPED", source_path=str(source),
            archive_path=str(target), manifest_path=str(target.parent / "archive-manifest.json"),
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"], mtime_ns=job["source_mtime_ns"],
            queue_path=job["active_queue_path"], revision=1,
            output_path=output.relative_to(self.root).as_posix() if exported else None,
            output_bytes=output.stat().st_size if exported else None,
            output_manifest_path=manifest.relative_to(self.root).as_posix() if exported else None,
            output_manifest_bytes=manifest.stat().st_size if exported else None,
        )
        if phase != "PREPARING":
            self.assertTrue(self.store.set_source_archive_phase(row_id, phase, **flags))
        if temp:
            target.parent.mkdir(parents=True, exist_ok=True)
            (target.parent / "archive-manifest.json.tmp").write_text(json.dumps({"archive_id": row_id}),
                                                                     encoding="utf-8")
        if moved:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
        for gone, path in ((export_gone, output), (manifest_gone, manifest)):
            if gone:
                os.replace(path, self.bin_dir / f"$R{uuid.uuid4().hex[:6]}{path.suffix}")
        return row_id

    def test_reconcile_each_phase(self):
        jobs = {name: (self.make_skipped_job(name) if name.startswith("s") else self.make_exported_job(name))
                for name in ("e1", "e2", "e3", "s4", "s5", "e6", "e7", "e8", "s9", "s10", "e11", "e12")}
        rows = {
            "e1": self.row(jobs["e1"]),
            "e2": self.row(jobs["e2"], "SOURCE_MOVED", moved=True),
            "e3": self.row(jobs["e3"], "EXPORT_RECYCLING", moved=True, export_gone=True, source_verified=True),
            "s4": self.row(jobs["s4"], "SOURCE_VERIFIED", moved=True, source_verified=True),
            "s5": self.row(jobs["s5"], "SOURCE_MOVED", moved=True),
            "e6": self.row(jobs["e6"]),
            "e7": self.row(jobs["e7"], "MANIFEST_RECYCLING", moved=True, export_gone=True, manifest_gone=True,
                           export_recycled=True, export_verified=True, export_record=RECORD),
            "e11": self.row(jobs["e11"], "SOURCE_VERIFIED", moved=True),
            "e12": self.row(jobs["e12"], "EXPORT_RECYCLING", moved=True),
        }
        # Neither in input nor in the archive.
        os.replace(self.source_of(jobs["e6"]), self.bin_dir / "lost.mp4")
        # The input path of e11 was taken by another file: its rollback cannot run.
        self.source_of(jobs["e11"]).write_bytes(b"another file")
        # Restores: e8 had renamed the file back, s9 had not started, s10 lost it.
        for name, place in (("e8", "input"), ("s9", "archive"), ("s10", "none")):
            row_id = self.row(jobs[name], "SOURCE_VERIFIED", moved=True, source_verified=True)
            self.store.finish_source_archive(row_id, state="ARCHIVED")
            self.store.begin_archive_restore(row_id)
            if place == "input":
                os.replace(self.target_of(jobs[name]), self.source_of(jobs[name]))
            elif place == "none":
                os.replace(self.target_of(jobs[name]), self.bin_dir / f"{name}.mp4")
            rows[name] = row_id
        calls = []

        def finder(volume_root, original, size, *, since):
            calls.append(Path(original).name)
            return RECORD if original.endswith("-reviewed.mp4") else None

        e12_output = str(self.output_of(jobs["e12"]).resolve())
        with patch.object(recycle_bin, "operations_in_progress", return_value=frozenset({e12_output})):
            settled = reconcile_pending_archives(self.root, self.store, finder=finder)
        self.assertEqual(sorted(settled),
                         sorted(row_id for name, row_id in rows.items() if name not in ("e6", "e11", "e12")))
        latest = {name: self.store.latest_source_archive(jobs[name]) for name in rows}
        self.assertEqual((latest["e1"]["state"], latest["e1"]["error"]),
                         ("FAILED", "Bị gián đoạn trước khi lưu trữ; video gốc vẫn còn trong input."))
        for name in ("e2", "s5"):
            self.assertEqual((latest[name]["state"], latest[name]["error"]),
                             ("FAILED", "Bị gián đoạn trước khi lưu trữ xong; đã đưa video gốc về input."))
            self.assertTrue(self.source_of(jobs[name]).is_file())
            self.assertFalse(self.target_of(jobs[name]).exists())
        self.assertEqual((latest["e3"]["state"], latest["e3"]["export_recycled"], latest["e3"]["export_verified"],
                          latest["e3"]["export_record"], latest["e3"]["error"]),
                         ("ARCHIVED", True, True, RECORD, "Bị gián đoạn; manifest của bản xuất vẫn còn trong output."))
        self.assertEqual(latest["s4"]["state"], "ARCHIVED")
        # Security review (L1): in neither place, the row is not settled: the job stays locked.
        self.assertEqual((latest["e6"]["state"], self.store.source_lock(jobs["e6"])), ("PENDING", "archived"))
        event = self.events(jobs["e6"], "SOURCE_ARCHIVE_FAILED")[-1]
        self.assertEqual((event["level"], event["message"], event["payload"]["places"]), (
            "ERROR", source_archive_restore.NOWHERE_MESSAGE, {"input": "absent", "archive": "absent",
                                                              "output": "present", "output_manifest": "present"}))
        self.assertEqual((latest["e7"]["state"], latest["e7"]["manifest_recycled"], latest["e7"]["manifest_record"],
                          latest["e7"]["error"]), ("ARCHIVED", True, None, None))
        self.assertEqual(latest["e11"]["state"], "PENDING")
        self.assertEqual(self.events(jobs["e11"], "SOURCE_ARCHIVE_FAILED")[-1]["level"], "ERROR")
        self.assertEqual(latest["e12"]["state"], "PENDING")
        self.assertEqual((latest["e8"]["state"], self.store.get_job(jobs["e8"])["state"]), ("RESTORED", "READY_TO_EXPORT"))
        self.assertEqual(self.watcher_row(self.source_of(jobs["e8"]))["imported_job_id"], jobs["e8"])
        self.assertEqual((latest["s9"]["state"], latest["s9"]["error"]),
                         ("ARCHIVED", "Lần khôi phục trước bị gián đoạn; video gốc vẫn ở kho lưu trữ."))
        self.assertEqual(latest["s10"]["state"], "ARCHIVED")
        self.assertEqual(self.events(jobs["s10"], "SOURCE_ARCHIVE_RESTORE_FAILED")[-1]["level"], "ERROR")
        # Only the exports that left output/ were looked up; nothing was recycled or hashed.
        self.assertEqual(sorted(calls), sorted([self.output_of(jobs["e3"]).name, self.manifest_of(jobs["e7"]).name]))
        self.assertEqual((self.recycler.calls, self.hashed), ([], []))

    def test_reconcile_never_settles_what_it_cannot_read(self):
        # Security review (L1): an lstat that fails for another reason than "not there" decides nothing:
        # the row keeps its lock, an ERROR event says why, and the next start looks again.
        moved, export, back = self.make_skipped_job("u1"), self.make_exported_job("u2"), self.make_skipped_job("u3")
        rows = [self.row(moved, "SOURCE_MOVED", moved=True),
                self.row(export, "EXPORT_RECYCLING", moved=True, source_verified=True)]
        restore = self.row(back, "SOURCE_VERIFIED", moved=True, source_verified=True)
        self.store.finish_source_archive(restore, state="ARCHIVED")
        self.store.begin_archive_restore(restore)
        os.replace(self.target_of(back), self.source_of(back))  # the restore had renamed it back
        unreadable = {os.path.normcase(str(path)) for path in (
            self.target_of(moved), self.output_of(export), self.source_of(back))}
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            if os.path.normcase(str(path)) in unreadable:
                raise PermissionError(13, "The process cannot access the file", str(path), 32)
            return real_lstat(path, *args, **kwargs)

        def finder(*args, **kwargs):
            raise AssertionError("nothing is looked up while a file cannot be read")

        with patch("os.lstat", side_effect=lstat):
            settled = reconcile_pending_archives(self.root, self.store, finder=finder)
        self.assertEqual(settled, [])
        for job_id, state in ((moved, "PENDING"), (export, "PENDING"), (back, "RESTORING")):
            with self.subTest(job=job_id):
                self.assertEqual(self.store.latest_source_archive(job_id)["state"], state)
                self.assertEqual(self.store.source_lock(job_id), "archived")
        self.assertTrue(self.target_of(moved).is_file())  # nothing was renamed on a guess
        self.assertTrue(self.output_of(export).is_file())
        event = self.events(moved, "SOURCE_ARCHIVE_FAILED")[-1]
        self.assertEqual((event["level"], event["message"], event["payload"]["places"]),
                         ("ERROR", source_archive_restore.UNREADABLE_MESSAGE, {"input": "absent", "archive": "unreadable"}))
        self.assertEqual(self.events(export, "SOURCE_ARCHIVE_FAILED")[-1]["payload"]["places"]["output"], "unreadable")
        event = self.events(back, "SOURCE_ARCHIVE_RESTORE_FAILED")[-1]
        self.assertEqual((event["level"], event["message"]), ("ERROR", source_archive_restore.UNREADABLE_MESSAGE))
        # Readable again at the next start: each row is settled the usual way.
        settled = reconcile_pending_archives(self.root, self.store, finder=lambda *args, **kwargs: None)
        self.assertEqual(sorted(settled), sorted([*rows, restore]))
        self.assertEqual([self.store.latest_source_archive(job_id)["state"] for job_id in (moved, export, back)],
                         ["FAILED", "FAILED", "RESTORED"])
        self.assertTrue(all(self.source_of(job_id).is_file() for job_id in (moved, export, back)))
        self.assertEqual(self.hashed, [])

    def test_reconcile_names_or_removes_the_unfinished_manifest(self):
        # Security review (L2): a manifest only ever describes a file the archive holds.
        kept, rolled, early, exported = (self.make_skipped_job("t1"), self.make_skipped_job("t2"),
                                         self.make_skipped_job("t3"), self.make_exported_job("t4"))
        kept_row = self.row(kept, "SOURCE_VERIFIED", moved=True, source_verified=True)
        self.row(rolled, "SOURCE_MOVED", moved=True)
        self.row(early, temp=True)  # stopped after the .tmp was written, before the rename
        self.row(exported, "MANIFEST_RECYCLING", moved=True, export_gone=True, manifest_gone=True,
                 source_verified=True, export_recycled=True, export_verified=True, export_record=RECORD)
        reconcile_pending_archives(self.root, self.store, finder=lambda *args, **kwargs: None)
        for job_id, state, names in (
            (kept, "ARCHIVED", ["archive-manifest.json", "t1.mp4"]),
            (rolled, "FAILED", []),
            (early, "FAILED", []),
            (exported, "ARCHIVED", ["archive-manifest.json", "t4.mp4"]),
        ):
            with self.subTest(job=job_id):
                row = self.store.latest_source_archive(job_id)
                self.assertEqual((row["state"], row["error"] if state == "ARCHIVED" else None), (state, None))
                self.assertEqual(sorted(p.name for p in self.target_of(job_id).parent.iterdir()), names)
        document = json.loads((self.target_of(kept).parent / "archive-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(document, {"archive_id": kept_row})


class RestoreTests(ArchiveFixture):
    def test_restore_sets_ready_to_export(self):
        job_id = self.make_exported_job("tap57")
        source = self.source_of(job_id)
        data, mtime = source.read_bytes(), source.stat().st_mtime_ns
        self.archive([job_id])
        target = self.target_of(job_id)
        self.hashed.clear()
        result = self.restore(job_id)
        self.assertEqual(result, {
            "job_id": job_id, "status": "RESTORED", "state": "READY_TO_EXPORT",
            "message": f"Đã đưa video gốc của #{job_id} về input (SHA-256 khớp). Video về mục “Đang chờ duyệt” để "
                       "xuất lại; bản xuất cũ vẫn ở Thùng rác.",
        })
        self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), (data, mtime))
        self.assertFalse(target.exists())
        self.assertTrue((target.parent / "archive-manifest.json").is_file())
        self.assertEqual(self.hashed, [target])
        row = self.store.latest_source_archive(job_id)
        self.assertEqual((row["state"], row["restored_mtime_ns"]), ("RESTORED", mtime))
        self.assertTrue(row["restored_at"])
        self.assertIsNone(self.store.source_lock(job_id))
        self.assertEqual(self.watcher_row(source)["imported_job_id"], job_id)
        self.assertEqual(len(self.events(job_id, "SOURCE_ARCHIVE_RESTORED")), 1)
        # The export went to the bin: the job exports again from "Đang chờ duyệt".
        self.assertIsNone(archive_hint(self.root, self.store, self.scheduler, self.store.get_job(job_id),
                                       cleanup_row=None, archive_row=row))
        # A review that is not finished goes back to WAITING_REVIEW.
        other = self.make_exported_job("tap58")
        self.archive([other])
        queue = self.queue(other)
        queue["status"] = "REVIEW_REQUIRED"
        self.queue_file(other).write_text(json.dumps(queue), encoding="utf-8")
        self.assertEqual(self.restore(other)["state"], "WAITING_REVIEW")

    def test_restore_keeps_skipped(self):
        job_id = self.make_skipped_job("tap59")
        self.archive([job_id])
        result = self.restore(job_id)
        self.assertEqual((result["state"], result["message"]), (
            "SKIPPED", f"Đã đưa video gốc của #{job_id} về input (SHA-256 khớp). Video vẫn ở mục “Hoàn tất” "
                       "(đã bỏ qua).",
        ))
        self.assertEqual(self.store.get_job(job_id)["state"], "SKIPPED")
        hint = archive_hint(self.root, self.store, self.scheduler, self.store.get_job(job_id), cleanup_row=None,
                            archive_row=self.store.latest_source_archive(job_id))
        self.assertTrue(hint["eligible"])

    def test_restore_refusals(self):
        never = self.make_skipped_job("tap60")
        with self.assertRaises(ActionConflict) as caught:
            self.restore(never)
        self.assertEqual((caught.exception.code, str(caught.exception)),
                         ("not_archived", f"Video #{never} không có video gốc trong kho lưu trữ để khôi phục."))
        with self.assertRaises(KeyError):
            self.restore(999)
        for value in (0, -1, True, "1", None, 2**31):
            with self.subTest(job_id=value), self.assertRaises(ValueError):
                self.restore(value)
        job_id = self.make_skipped_job("tap61")
        self.archive([job_id])
        source, target = self.source_of(job_id), self.target_of(job_id)
        source.write_bytes(b"a new file with the same name")
        with self.assertRaises(ActionConflict) as caught:
            self.restore(job_id)
        self.assertEqual((caught.exception.code, str(caught.exception)), (
            "target_exists", "Trong input đã có file “tap61.mp4”. BiliFlow không ghi đè: dời file đó ra khỏi input "
                             "rồi bấm “Khôi phục bản xuất” lại."))
        self.assertEqual(source.read_bytes(), b"a new file with the same name")
        source.unlink()
        original = target.read_bytes()
        target.write_bytes(b"X" * len(original))  # same size, other content
        with self.assertRaises(ActionConflict) as caught:
            self.restore(job_id)
        self.assertEqual(caught.exception.code, "archive_changed")
        row = self.store.latest_source_archive(job_id)
        self.assertEqual((row["state"], row["error"]), ("ARCHIVED", str(caught.exception)))
        self.assertTrue(target.is_file())
        self.assertFalse(source.exists())
        os.replace(target, self.bin_dir / "elsewhere.mp4")
        with self.assertRaises(ActionConflict) as caught:
            self.restore(job_id)
        self.assertEqual(caught.exception.code, "archive_missing")
        self.assertTrue(source_cleanup.SOURCE_FILE_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(ActionConflict) as caught:
                self.restore(job_id)
        finally:
            source_cleanup.SOURCE_FILE_LOCK.release()
        self.assertEqual(caught.exception.code, "busy")
        self.assertEqual(self.store.latest_source_archive(job_id)["state"], "ARCHIVED")

    def test_restore_unreadable_archive_is_not_called_changed(self):
        job_id = self.make_skipped_job("tap66")
        self.archive([job_id])

        def unreadable(path):
            raise PermissionError(13, "The process cannot access the file", str(path), 32)

        with self.assertRaises(ActionConflict) as caught:
            self.restore(job_id, hasher=unreadable)
        self.assertEqual(caught.exception.code, "restore_failed")
        self.assertTrue(str(caught.exception).startswith(
            "Không đọc được video gốc “tap66.mp4” trong kho lưu trữ để kiểm tra SHA-256 ("), str(caught.exception))
        row = self.store.latest_source_archive(job_id)
        self.assertEqual((row["state"], row["error"]), ("ARCHIVED", str(caught.exception)))
        self.assertTrue(self.target_of(job_id).is_file())
        self.assertFalse(self.source_of(job_id).exists())
        self.assertEqual(self.restore(job_id)["status"], "RESTORED")


class WatcherAndImportTests(ArchiveFixture):
    def watcher(self):
        return InputWatcher(self.root, self.store, stable_seconds=0)

    def test_watcher_after_restore_no_new_job_no_rehash(self):
        job_id = self.make_exported_job("tap62")
        source = self.source_of(job_id)
        self.store.observe_file(source, source.stat().st_size, source.stat().st_mtime_ns)
        self.store.mark_file_imported(source, job_id)
        self.archive([job_id])
        self.assertEqual(self.watcher().scan_once(), 0)
        self.restore(job_id)
        jobs_before = len(self.store.list_jobs())
        with patch("biliflow.scheduler.sha256_file", side_effect=AssertionError("re-hashed")), \
                patch("biliflow.scheduler.probe_video", side_effect=AssertionError("probed")):
            self.assertEqual(self.watcher().scan_once(), 0)
        self.assertEqual(len(self.store.list_jobs()), jobs_before)
        self.assertEqual(self.store.get_job(job_id)["source_path"], str(source.resolve()))

    def test_different_file_at_archived_path_becomes_new_job_and_restore_refuses(self):
        job_id = self.make_skipped_job("tap63")
        self.archive([job_id])
        source, target = self.source_of(job_id), self.target_of(job_id)
        source.write_bytes(b"a different video" * 64)
        with patch("biliflow.scheduler.probe_video", return_value={}), \
                patch("biliflow.scheduler.duration_seconds", return_value=60.0):
            self.assertEqual(self.watcher().scan_once(), 1)
        new = self.store.find_by_sha(hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertNotEqual(new["id"], job_id)
        self.assertEqual((new["state"], new["source_path"]), ("NEEDS_METADATA", str(source.resolve())))
        archived = self.store.get_job(job_id)
        self.assertEqual((archived["state"], archived["source_path"]), ("SKIPPED", str(source.resolve())))
        self.assertEqual(self.store.source_lock(job_id), "archived")
        with self.assertRaises(ActionConflict) as caught:
            self.restore(job_id)
        self.assertEqual(caught.exception.code, "target_exists")
        self.assertTrue(target.is_file())
        self.assertEqual(self.store.latest_source_archive(job_id)["state"], "ARCHIVED")

    def test_import_ignores_archived_jobs(self):
        job_id = self.make_exported_job("tap64")
        self.archive([job_id])
        source, target = self.source_of(job_id), self.target_of(job_id)
        # The same video under another name, and another video at the archived path.
        (self.root / "input" / "copy.mp4").write_bytes(target.read_bytes())
        source.write_bytes(b"another video" * 64)
        before = {job["id"]: (job["source_path"], job["state"]) for job in self.store.list_jobs()}
        with patch("biliflow.job_import.probe_video", side_effect=AssertionError("probed")):
            import_existing_project(self.root, self.store)
        after = {job["id"]: (job["source_path"], job["state"]) for job in self.store.list_jobs()}
        self.assertEqual(after, before)
        self.assertEqual(self.store.source_lock(job_id), "archived")

    def test_tap10_30_path_bin_restore_then_archive(self):
        # Jobs 10-30: "Dọn video gốc", the user restores the file from the bin, then "Lưu trữ".
        source = self.root / "input" / "Thám Tử Lừng Danh Conan - Tập 10.mp4"
        source.write_bytes(b"conan 10" * 256)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        job_key = safe_job_key(source, digest)
        job_id = self.make_exported_job("tap10", source_name=source.name, job_key=job_key)
        self.assertEqual(self.legacy_cleanup(job_id)["state"], "RECYCLED")
        self.assertEqual(self.reason(job_id),
                         "Video gốc đang ở Thùng rác (đã dọn); khôi phục nó về input trước khi lưu trữ")
        recycled = next(self.bin_dir.glob("$R*.mp4"))
        os.replace(recycled, self.source_of(job_id))  # "Khôi phục" in the Recycle Bin
        self.watcher().scan_once()
        self.assertEqual(self.store.latest_source_cleanup(job_id)["state"], "RESTORED")
        result = self.archive([job_id])
        self.assertEqual(result["results"][0]["status"], "ARCHIVED")
        target = self.target_of(job_id)
        self.assertEqual(target, self.root / "archive" / "sources" / job_key / source.name)
        self.assertTrue(target.is_file())
        # On the real install the archive path of these jobs stays far below 259 characters.
        real = Path("E:/DungChung/BiliFlow") / "archive" / "sources" / job_key / source.name
        self.assertLess(len(str(real)), 140)


class ModuleTests(ArchiveFixture):
    def test_module_imports_no_control_center_or_scheduler(self):
        code = (
            "import sys\n"
            "import biliflow.source_archive, biliflow.source_archive_files, biliflow.source_archive_restore\n"
            "print('biliflow.control_center' in sys.modules, 'biliflow.scheduler' in sys.modules)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                                check=True)
        self.assertEqual(result.stdout.split(), ["False", "False"])
        self.assertEqual(source_archive_restore.PHASE_ORDER["MANIFEST_RECYCLING"], 5)

    def test_archive_root_comes_from_given_root(self):
        self.assertEqual(archive_folder(self.root, "k"), self.root / "archive" / "sources" / "k")
        job_id = self.make_skipped_job("tap65")
        real_folder = recycle_bin.INSTALL_ROOT / "archive" / "sources" / "tap65"
        with patch.object(recycle_bin, "INSTALL_ROOT", self.bin_dir / "not-the-install"):
            result = self.archive([job_id])
        self.assertEqual(result["results"][0]["status"], "ARCHIVED")
        self.assertTrue(self.target_of(job_id).is_relative_to(self.root))
        self.assertFalse(real_folder.exists())
        self.assertFalse((self.bin_dir / "not-the-install").exists())
        assessment = assess_archive(self.root, self.store, self.scheduler, self.store.get_job(job_id))
        self.assertEqual(assessment.reason, "Video gốc đã được lưu trữ")


if __name__ == "__main__":
    unittest.main()
