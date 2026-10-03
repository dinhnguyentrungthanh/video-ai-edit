"""source_cleanup: eligibility, preview, the three-section execute and startup reconciliation.

A temporary root with a real JobStore and JobScheduler (never the project's
state). The recycler is always a fake that moves the file into a folder outside
the root, and ``bin_info`` is a fake BinInfo with the real numbers of drive E:.
The module setup also replaces ``recycle_bin._shell_delete`` with a function
that fails the test, so nothing here can reach the real Recycle Bin.
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import recycle_bin, source_cleanup
from biliflow.export_guards import (
    REVIEW_QUEUE_IO,
    SOURCE_CLEANED_MESSAGE,
    SOURCE_CLEANED_STOP_REFUSAL,
    ActionConflict,
    review_summary,
)
from biliflow.job_store import JobStore, now_iso, sha256_file
from biliflow.recycle_bin import (
    BinInfo,
    RecycleFailed,
    RecycleRefused,
    RecycleResult,
    RecycleTimeout,
)
from biliflow.review_workflow import approved_operations, review_export_paths
from biliflow.scheduler import JobScheduler
from biliflow.source_cleanup import (
    SOURCE_FILE_LOCK,
    CleanupConflict,
    cleanup_hint,
    cleanup_row_summary,
    execute_cleanup,
    parse_job_ids,
    preview_cleanup,
    recheck_recycle_record,
    reconcile_pending_cleanups,
)


GUID = "{2fd9f59c-d156-40e6-b5c9-93b787892ee9}"
MAX_BYTES = 52_157_218_816  # MaxCapacity 49741 MiB
USED_BYTES = 11_823_971_925
ITEMS = 7
NEAR_FULL = MAX_BYTES - recycle_bin.CAPACITY_MARGIN_BYTES
NOW = datetime.now(timezone.utc)
FRESH_PREVIEW = object()  # execute() helper: compute the preview id first

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
        "decision": decision, "decided_at": decided_at,
    }


def blur_item(item_id, *, edge_mode=None, decided_at=None):
    """A BLUR decision with the fields build_edit_plan reads."""
    return {
        "id": item_id, "category": "advertising", "start_seconds": 3.0, "end_seconds": 4.5,
        "decision": "BLUR", "decided_at": decided_at, "reasons": ["logo"], "evidence": [],
        "decision_region_source_pixels": {"x": 10, "y": 20, "width": 200, "height": 96},
        "decision_blur_edge_mode": edge_mode,
    }


def tree_digest(path):
    digest = hashlib.sha256()
    for file in sorted(p for p in Path(path).rglob("*") if p.is_file()):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FakeBin:
    """bin_info(path) -> BinInfo of drive E: (used bytes per call, or an error)."""

    def __init__(self, used=USED_BYTES, error=None, sequence=None):
        self.used, self.error, self.sequence = used, error, list(sequence or [])
        self.calls = []

    def __call__(self, path):
        self.calls.append(Path(path))
        if self.error is not None:
            raise self.error
        used = self.sequence.pop(0) if self.sequence else self.used
        return BinInfo("E:", "E:\\", GUID, MAX_BYTES, used, ITEMS)


class FakeRecycler:
    """Moves the file into a bin folder outside the root and records every call."""

    def __init__(self, bin_dir):
        self.bin_dir = Path(bin_dir)
        self.calls = []
        self.modes = {}
        self.callbacks = []

    def move(self, path):
        token = uuid.uuid4().hex[:6].upper()
        os.replace(path, self.bin_dir / f"$R{token}{Path(path).suffix}")
        record = self.bin_dir / f"$I{token}{Path(path).suffix}"
        record.write_bytes(b"record")
        return str(record)

    def __call__(self, path, *, allowed_root, expected_size, timeout, on_late_result):
        self.calls.append({
            "path": path, "allowed_root": allowed_root, "expected_size": expected_size,
            "timeout": timeout,
        })
        mode = self.modes.get(Path(path).name, "move")
        if isinstance(mode, threading.Event):
            mode.wait(10)
            mode = "move"
        if mode == "fail":
            raise RecycleFailed(recycle_bin.SHARING_MESSAGE)
        if mode == "refused":
            raise RecycleRefused(recycle_bin.SIZE_MESSAGE)
        if mode == "timeout":
            self.callbacks.append(on_late_result)
            raise RecycleTimeout(recycle_bin.TIMEOUT_MESSAGE)
        if mode == "unexpected":
            raise RuntimeError("boom")
        record = self.move(path)
        if mode == "unexpected-after-move":
            raise RuntimeError("boom after the move")
        verified = mode != "unverified"
        return RecycleResult(str(path), expected_size, verified, record if verified else None, 0.01)


class CleanupFixture(unittest.TestCase):
    """A temp root with a real JobStore and JobScheduler (never the project's state)."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "reports/jobs", "output", "work", "state", "logs"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        bin_temp = TemporaryDirectory()
        self.addCleanup(bin_temp.cleanup)
        self.bin_dir = Path(bin_temp.name).resolve()
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        self.scheduler = JobScheduler(self.root, self.store)
        # No scan stages: rerun queues a job without running anything.
        patcher = patch("biliflow.scheduler.pipeline_stages", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        source_cleanup._CACHE.clear()
        self.addCleanup(source_cleanup._CACHE.clear)
        self.recycler = FakeRecycler(self.bin_dir)
        self.bin = FakeBin()
        self.hashed = []

    def hasher(self, path):
        self.hashed.append(Path(path))
        return sha256_file(Path(path))

    def make_job(self, name, *, state, items, status="READY_FOR_EDIT_PLAN", source=None):
        source = source or self.root / "input" / f"{name}.mp4"
        source.parent.mkdir(parents=True, exist_ok=True)
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
            job_key=name, source_path=source, source_sha256=digest,
            source_size_bytes=info.st_size, source_mtime_ns=info.st_mtime_ns,
            content_style="animation", state=state,
        )
        self.store.update_job(
            job["id"], active_queue_path=f"reports/jobs/{name}/review-queue.json",
            active_revision=1, progress=1.0,
        )
        return int(job["id"])

    def queue_file(self, job_id):
        return self.root / self.store.get_job(job_id)["active_queue_path"]

    def queue(self, job_id):
        return json.loads(self.queue_file(job_id).read_text(encoding="utf-8"))

    def make_exported_job(self, name, *, decided_at=None, created_at=None, manifest=None, plan_queue=None,
                          items=None, operations=False):
        """A COMPLETED job with an export of its current review.

        ``operations=True`` writes the manifest's ``operations`` the way the
        renderer does (the edit plan's approved operations of the queue).
        """
        decided = iso(NOW - timedelta(hours=1)) if decided_at is None else decided_at
        if items is None:
            items = [item("a", decided_at=decided), item("b", decided_at=decided)]
        job_id = self.make_job(name, state="COMPLETED", items=items)
        job = self.store.get_job(job_id)
        plan_path, output, _ = review_export_paths(self.root, self.queue(job_id))
        output.write_bytes(b"OUTPUT" + name.encode() * 300)
        output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        value = {
            "schema_version": 1, "status": "COMPLETED",
            "created_at": iso(NOW) if created_at is None else created_at,
            "edit_plan": plan_path.relative_to(self.root).as_posix(),
            "source": {
                "path": job["source_path"], "sha256": job["source_sha256"], "duration_seconds": 60.0,
                "sha256_after_render": job["source_sha256"], "modified": False,
            },
            "output": {
                "path": output.relative_to(self.root).as_posix(), "bytes": output.stat().st_size,
                "sha256": output_sha,
            },
            "encoding": {"full_decode_validation_passed": True},
        }
        if operations:
            value["operations"] = approved_operations(self.queue(job_id))
        if manifest is not None:
            manifest(value)
        output.with_suffix(output.suffix + ".manifest.json").write_text(json.dumps(value), encoding="utf-8")
        plan_path.write_text(json.dumps({
            "status": "FINAL_RENDER_COMPLETED",
            "review_queue": job["active_queue_path"] if plan_queue is None else plan_queue,
        }), encoding="utf-8")
        self.store.add_artifact(
            job_id, stage_name="render", kind="final_output",
            path=output.relative_to(self.root).as_posix(), sha256=output_sha,
            bytes_count=output.stat().st_size,
        )
        return job_id

    def output_of(self, job_id):
        return review_export_paths(self.root, self.queue(job_id))[1]

    def skip_record(self, job_id, **overrides):
        """The skip:{id} record exactly as ControlCenter.skip_export writes it."""
        job = self.store.get_job(job_id)
        summary = review_summary(self.queue(job_id))
        record = {
            "queue_path": job.get("active_queue_path"),
            "revision": job.get("active_revision"),
            "main_items": summary["main_items"],
            "advisory_items": summary["advisory_items"],
            "decisions": summary["decisions"],
            "skipped_at": now_iso(),
            "actor": "control_center_user",
        }
        record.update(overrides)
        self.store.set_setting(f"skip:{job_id}", record)
        return record

    def make_skipped_job(self, name, *, record=True, **overrides):
        job_id = self.make_job(name, state="SKIPPED", items=[item("a", decided_at=iso(NOW))])
        if record:
            self.skip_record(job_id, **overrides)
        return job_id

    def source_of(self, job_id):
        return Path(self.store.get_job(job_id)["source_path"])

    def preview(self, ids, **kwargs):
        kwargs.setdefault("bin_info", self.bin)
        return preview_cleanup(self.root, self.store, self.scheduler, ids, **kwargs)

    def execute(self, ids, preview_id=FRESH_PREVIEW, **kwargs):
        if preview_id is FRESH_PREVIEW:
            preview_id = self.preview(ids)["preview_id"]
        kwargs.setdefault("recycler", self.recycler)
        kwargs.setdefault("bin_info", self.bin)
        kwargs.setdefault("hasher", self.hasher)
        return execute_cleanup(self.root, self.store, self.scheduler, ids, preview_id, **kwargs)

    def reason(self, job_id):
        preview = self.preview([job_id])
        self.assertEqual(preview["eligible"], [], preview)
        return preview["ineligible"][0]["reason"]

    def events(self, job_id, kind):
        return [event for event in self.store.events(job_id) if event["event_type"] == kind]

    def all_rows(self):
        with self.store._lock:
            rows = self.store._connection.execute("SELECT * FROM source_cleanups ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def watcher_row(self, path):
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT * FROM watcher_files WHERE path=?", (str(Path(path).resolve()),)
            ).fetchone()
        return None if row is None else dict(row)

    def add_row(self, job_id, *, state):
        source = self.source_of(job_id)
        row_id = self.store.add_source_cleanup(
            job_id=job_id, kind="EXPORTED", source_path=str(source.resolve()),
            source_sha256=self.store.get_job(job_id)["source_sha256"],
            size_bytes=self.store.get_job(job_id)["source_size_bytes"], mtime_ns=1,
        )
        if state != "PENDING":
            self.store.finish_source_cleanup(row_id, state=state, verified=True)
        return row_id


class ParseJobIdsTests(unittest.TestCase):
    def test_accepts_a_list_or_a_comma_string_dedupes_and_sorts(self):
        self.assertEqual(parse_job_ids([60, 42, 42]), [42, 60])
        self.assertEqual(parse_job_ids("60, 42,42"), [42, 60])
        self.assertEqual(parse_job_ids((7,)), [7])
        self.assertEqual(parse_job_ids(list(range(1, 51)) + [50, 1]), list(range(1, 51)))
        self.assertEqual(parse_job_ids([2**31 - 1]), [2**31 - 1])

    def test_rejects_everything_else(self):
        cases = [
            [], "", "abc", "0", "1,,2", "1,a", "-1", "1.0", "١", "99999999999", [0], [-3], [2**31],
            [True], [1, False], [1.0], ["1"], None, 7, {"1": 1}, list(range(1, 52)),
            ",".join(str(value) for value in range(1, 52)),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                parse_job_ids(value)
            self.assertEqual(str(caught.exception), "Chọn từ 1 đến 50 video mỗi lần dọn.")


class ModuleContractTests(unittest.TestCase):
    def test_cleanup_conflict_is_not_a_value_error(self):
        error = CleanupConflict("busy", "Đang dọn video gốc; chờ lần dọn trước xong rồi thử lại.")
        self.assertNotIsInstance(error, ValueError)
        self.assertEqual(str(error), "Đang dọn video gốc; chờ lần dọn trước xong rồi thử lại.")
        self.assertEqual((error.code, error.preview), ("busy", None))
        changed = CleanupConflict("preview_changed", "Danh sách đã thay đổi, hãy xem lại.", {"count": 0})
        self.assertEqual(changed.preview, {"count": 0})

    def test_import_never_loads_the_control_center_or_the_scheduler(self):
        code = (
            "import sys\n"
            "import biliflow.source_cleanup\n"
            "print('biliflow.control_center' in sys.modules, 'biliflow.scheduler' in sys.modules)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True,
        )
        self.assertEqual(result.stdout.split(), ["False", "False"])

    def test_constants(self):
        self.assertEqual(source_cleanup.MAX_CLEANUP_JOBS, 50)
        self.assertEqual(source_cleanup.ELIGIBLE_STATES, frozenset({"COMPLETED", "SKIPPED"}))

    def test_row_summary(self):
        self.assertIsNone(cleanup_row_summary(None))
        row = {
            "id": 3, "job_id": 9, "kind": "SKIPPED", "source_path": "E:\\x\\input\\Tập 1.mp4",
            "size_bytes": 10, "state": "RECYCLED", "verified": 1, "error": None,
            "created_at": "a", "finished_at": "b", "restored_at": None,
        }
        self.assertEqual(cleanup_row_summary(row), {
            "id": 3, "state": "RECYCLED", "kind": "SKIPPED", "size_bytes": 10, "file_name": "Tập 1.mp4",
            "source_path": "E:\\x\\input\\Tập 1.mp4", "created_at": "a", "finished_at": "b",
            "restored_at": None, "verified": True, "verified_at_cleanup": True,
            "verified_later_at": None, "rechecked_at": None, "error": None,
        })
        # Batch 4: a later "Kiểm tra lại Thùng rác" that found the record makes it verified;
        # the row itself still says what the cleanup saw.
        unverified = {**row, "verified": 0}
        self.assertEqual(
            {key: cleanup_row_summary(unverified, {"found": True, "checked_at": "c", "record": "r"})[key]
             for key in ("verified", "verified_at_cleanup", "verified_later_at", "rechecked_at")},
            {"verified": True, "verified_at_cleanup": False, "verified_later_at": "c", "rechecked_at": "c"},
        )
        self.assertEqual(
            {key: cleanup_row_summary(unverified, {"found": False, "checked_at": "d", "record": None})[key]
             for key in ("verified", "verified_at_cleanup", "verified_later_at", "rechecked_at")},
            {"verified": False, "verified_at_cleanup": False, "verified_later_at": None, "rechecked_at": "d"},
        )


class EligibleCleanupTests(CleanupFixture):
    def test_an_exported_video_goes_to_the_bin_and_nothing_else_changes(self):
        job_id = self.make_exported_job("tap12")
        source = self.source_of(job_id)
        size = source.stat().st_size
        self.store.observe_file(source, size, source.stat().st_mtime_ns)
        self.store.mark_file_imported(source, job_id)
        before = {
            "reports": tree_digest(self.root / "reports"), "output": tree_digest(self.root / "output"),
            "work": tree_digest(self.root / "work"), "queue": self.queue_file(job_id).read_bytes(),
        }
        output = self.output_of(job_id)
        preview = self.preview([job_id])
        self.assertEqual(preview["ineligible"], [])
        self.assertEqual(preview["count"], 1)
        self.assertEqual(preview["total_bytes"], size)
        self.assertIsNone(preview["blocked"])
        self.assertEqual(preview["recycle_bin"], {
            "volume": "E:", "used_bytes": USED_BYTES, "items": ITEMS, "max_bytes": MAX_BYTES,
            "after_bytes": USED_BYTES + size,
        })
        self.assertRegex(preview["preview_id"], r"^[0-9a-f]{64}$")
        manifest = json.loads(output.with_suffix(".mp4.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(preview["eligible"], [{
            "job_id": job_id, "name": "tap12.mp4", "file_name": "tap12.mp4",
            "source_path": str(source.resolve()), "size_bytes": size, "kind": "EXPORTED",
            "output_path": output.relative_to(self.root).as_posix(), "output_name": output.name,
            "output_bytes": output.stat().st_size, "exported_at": manifest["created_at"],
            "skipped_at": None,
        }])
        self.assertEqual(self.hashed, [])  # the preview never hashes

        result = self.execute([job_id], preview["preview_id"])

        self.assertEqual(self.recycler.calls, [{
            "path": source.resolve(), "allowed_root": self.root / "input", "expected_size": size,
            "timeout": 60.0,
        }])
        self.assertEqual(result, {
            "results": [{
                "job_id": job_id, "name": "tap12.mp4", "status": "RECYCLED",
                "message": "Đã chuyển video gốc vào Thùng rác", "size_bytes": size,
            }],
            "recycled_count": 1, "recycled_bytes": size, "failed_count": 0, "pending": 0,
        })
        self.assertFalse(source.exists())
        self.assertEqual(self.hashed, [source.resolve(), self.root / output.relative_to(self.root)])
        row = self.store.latest_source_cleanup(job_id)
        job = self.store.get_job(job_id)
        self.assertEqual(
            {key: row[key] for key in (
                "state", "verified", "kind", "source_path", "source_sha256", "size_bytes", "output_path",
                "output_sha256", "output_bytes", "exported_at", "skipped_at", "error",
            )},
            {
                "state": "RECYCLED", "verified": True, "kind": "EXPORTED",
                "source_path": str(source.resolve()), "source_sha256": job["source_sha256"],
                "size_bytes": size, "output_path": output.relative_to(self.root).as_posix(),
                "output_sha256": manifest["output"]["sha256"], "output_bytes": manifest["output"]["bytes"],
                "exported_at": manifest["created_at"], "skipped_at": None, "error": None,
            },
        )
        self.assertTrue(row["recycle_record"].startswith(str(self.bin_dir)))
        self.assertTrue(row["finished_at"])
        events = self.events(job_id, "SOURCE_RECYCLED")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["message"], "Đã chuyển video gốc vào Thùng rác")
        self.assertEqual(events[0]["level"], "INFO")
        payload = events[0]["payload"]
        self.assertEqual(
            (payload["path"], payload["size_bytes"], payload["sha256"], payload["kind"], payload["verified"]),
            (str(source.resolve()), size, job["source_sha256"], "EXPORTED", True),
        )
        self.assertTrue(payload["recycled_at"])
        # Only the source left: reports, output, work and the queue are byte for byte the same.
        self.assertEqual({
            "reports": tree_digest(self.root / "reports"), "output": tree_digest(self.root / "output"),
            "work": tree_digest(self.root / "work"), "queue": self.queue_file(job_id).read_bytes(),
        }, before)
        watcher = self.watcher_row(source)
        self.assertEqual((watcher["size_bytes"], watcher["mtime_ns"], watcher["imported_job_id"]), (-1, -1, None))
        self.assertTrue(self.store.source_cleaned(job_id))
        self.assertEqual(set(self.bin.calls), {self.root / "input"})
        # Every action on the job is now locked, and it is not offered again.
        with self.assertRaises(ValueError) as caught:
            self.scheduler.rerun(job_id)
        self.assertEqual(str(caught.exception), SOURCE_CLEANED_MESSAGE)
        self.assertEqual(self.reason(job_id), "Video gốc đã được dọn trước đó")

    def test_a_skipped_video_with_a_matching_record_is_cleaned_without_hashing_an_output(self):
        job_id = self.make_skipped_job("tap30")
        record = self.store.setting(f"skip:{job_id}")
        preview = self.preview([job_id])
        entry = preview["eligible"][0]
        self.assertEqual(
            (entry["kind"], entry["output_path"], entry["output_name"], entry["output_bytes"],
             entry["exported_at"], entry["skipped_at"]),
            ("SKIPPED", None, None, None, None, record["skipped_at"]),
        )
        result = self.execute([job_id], preview["preview_id"])
        self.assertEqual(result["results"][0]["status"], "RECYCLED")
        self.assertEqual(self.hashed, [self.source_of(job_id).resolve()])
        row = self.store.latest_source_cleanup(job_id)
        self.assertEqual(
            (row["kind"], row["output_path"], row["output_sha256"], row["skipped_at"], row["exported_at"]),
            ("SKIPPED", None, None, record["skipped_at"], None),
        )

    def test_a_missing_edit_plan_is_not_a_refusal(self):
        job_id = self.make_exported_job("tap20")
        for plan in (self.root / "work").glob("*-edit-plan.json"):
            plan.unlink()
        self.assertEqual(self.preview([job_id])["count"], 1)

    def test_an_unverified_move_is_reported_and_still_locks_the_job(self):
        job_id = self.make_exported_job("tap21")
        self.recycler.modes["tap21.mp4"] = "unverified"
        result = self.execute([job_id])
        self.assertEqual(result["results"][0]["status"], "UNVERIFIED")
        self.assertEqual(
            result["results"][0]["message"],
            "Video gốc đã rời thư mục input nhưng không tìm thấy bản ghi trong Thùng rác; hãy kiểm tra Thùng rác.",
        )
        self.assertEqual((result["recycled_count"], result["failed_count"]), (1, 0))
        row = self.store.latest_source_cleanup(job_id)
        self.assertEqual((row["state"], row["verified"], row["recycle_record"]), ("RECYCLED", False, None))
        events = self.events(job_id, "SOURCE_RECYCLE_UNVERIFIED")
        self.assertEqual((len(events), events[0]["level"]), (1, "WARNING"))
        self.assertTrue(self.store.source_cleaned(job_id))


class IneligibleTests(CleanupFixture):
    STALE = "Bản xuất hiện có không ứng với lần duyệt mới nhất (mở “Duyệt cảnh” và xuất lại trước khi dọn)"
    MANIFEST = "Manifest xuất không khớp video gốc"
    OLDER = (
        "Bản xuất hiện có không khớp quyết định duyệt hiện tại "
        "(dời bản xuất cũ ra khỏi thư mục output rồi xuất lại trước khi dọn)"
    )
    MOVED = "Không thấy bản xuất trong thư mục output (đã bị dời hoặc đổi tên?)"

    def test_manifest_and_output_mismatches(self):
        def other_output(value):
            value["output"]["path"] = "output/other-reviewed.mp4"

        cases = [
            ("status", lambda value: value.update(status="FAILED"), self.MANIFEST),
            ("source sha", lambda value: value["source"].update(sha256="0" * 64), self.MANIFEST),
            ("modified", lambda value: value["source"].update(modified=True), self.MANIFEST),
            ("sha after render", lambda value: value["source"].update(sha256_after_render="1" * 64), self.MANIFEST),
            ("output path", other_output, self.MANIFEST),
            ("naive created_at", lambda value: value.update(created_at="2026-10-03T10:00:00"), self.MANIFEST),
            ("bad created_at", lambda value: value.update(created_at="hôm qua"), self.MANIFEST),
            ("no output sha", lambda value: value["output"].pop("sha256"), self.MANIFEST),
            ("no decode check", lambda value: value.pop("encoding"), "Bản xuất chưa qua kiểm tra giải mã toàn bộ"),
            ("decode failed", lambda value: value["encoding"].update(full_decode_validation_passed=False),
             "Bản xuất chưa qua kiểm tra giải mã toàn bộ"),
            ("output bytes", lambda value: value["output"].update(bytes=value["output"]["bytes"] + 1),
             "Video xuất đã thay đổi so với manifest"),
        ]
        for index, (label, change, expected) in enumerate(cases):
            with self.subTest(label):
                job_id = self.make_exported_job(f"m{index}", manifest=change)
                self.assertEqual(self.reason(job_id), expected)

    def test_an_export_older_than_the_latest_decision_or_another_review(self):
        # A manifest without "operations" falls back to the decision timestamps.
        older = self.make_exported_job("older", decided_at=iso(NOW + timedelta(minutes=5)))
        self.assertEqual(self.reason(older), self.OLDER)
        unparseable = self.make_exported_job("unparseable", decided_at="không rõ")
        self.assertEqual(self.reason(unparseable), self.reason(older))
        other_plan = self.make_exported_job("otherplan", plan_queue="reports/jobs/x/review-queue.json")
        self.assertEqual(self.reason(other_plan), self.STALE)

    def test_an_export_of_the_same_decisions_ignores_their_timestamps(self):
        # Fix pass: re-recording a decision after the export (an undo re-posts it)
        # moves decided_at, and finalize then keeps the existing export without
        # rendering. The renderer's manifest lists the operations it applied, so
        # the export still matches the review and stays cleanable.
        later = iso(NOW + timedelta(minutes=5))
        same = self.make_exported_job("again", decided_at=later, operations=True)
        blurred = self.make_exported_job(
            "blurred", items=[item("a", decided_at=later), blur_item("b", decided_at=later)], operations=True,
        )
        self.assertEqual(len(approved_operations(self.queue(blurred))), 1)
        preview = self.preview([same, blurred])
        self.assertEqual(([x["job_id"] for x in preview["eligible"]], preview["ineligible"]), ([same, blurred], []))
        # The blur's code constants are not decisions (an export made by an older build).
        constants = self.make_exported_job(
            "constants", items=[blur_item("b", decided_at=later)], operations=True,
            manifest=lambda value: value["operations"][0]["blur"].update(
                sigma=12, edge_feather_pixels=9, region_policy="ocr_union_v1"),
        )
        self.assertEqual(self.preview([constants])["count"], 1)

    def test_a_rerun_with_the_same_decisions_keeps_the_export_cleanable(self):
        # Recheck residual: a rerun whose new revision has the same items and
        # decisions maps to the same export path, so finalize keeps the export
        # without rebuilding the plan, which still names the previous queue.
        # The manifest's operations prove the export matches the review.
        job_id = self.make_exported_job(
            "rerun", operations=True, plan_queue="reports/jobs/rerun/review-queue-r1.json",
        )
        self.assertEqual(self.preview([job_id])["count"], 1)
        # Without operations the plan check still refuses it.
        legacy = self.make_exported_job(
            "legacy", plan_queue="reports/jobs/legacy/review-queue-r1.json",
        )
        self.assertEqual(self.reason(legacy), self.STALE)

    def test_an_export_rendered_from_other_decisions_at_the_same_path(self):
        decided = iso(NOW - timedelta(hours=1))
        job_id = self.make_exported_job(
            "edge", items=[item("a", decided_at=decided), blur_item("b", decided_at=decided)], operations=True,
        )
        output = self.output_of(job_id)
        self.assertEqual(self.preview([job_id])["count"], 1)
        # review_export_paths does not hash the blur edge mode: the same output
        # path, but the export was rendered with all edges feathered.
        queue = self.queue(job_id)
        queue["items"][1]["decision_blur_edge_mode"] = "vertical_only"
        self.queue_file(job_id).write_text(json.dumps(queue), encoding="utf-8")
        self.assertEqual(self.output_of(job_id), output)
        self.assertEqual(self.reason(job_id), self.OLDER)
        # Back to the exported edge mode: cleanable again.
        queue["items"][1]["decision_blur_edge_mode"] = "all_edges"
        self.queue_file(job_id).write_text(json.dumps(queue), encoding="utf-8")
        self.assertEqual(self.preview([job_id])["count"], 1)
        # An export without the blur the review now has.
        missing = self.make_exported_job(
            "noblur", items=[blur_item("b", decided_at=decided)], operations=True,
            manifest=lambda value: value.update(operations=[]),
        )
        self.assertEqual(self.reason(missing), self.OLDER)
        # Operations that are not a list of objects are a broken manifest.
        for index, broken in enumerate(("x", [1], {"id": "op-a"})):
            with self.subTest(operations=broken):
                bad = self.make_exported_job(
                    f"badops{index}", operations=True,
                    manifest=lambda value, broken=broken: value.update(operations=broken),
                )
                self.assertEqual(self.reason(bad), self.MANIFEST)

    def test_missing_outputs_like_jobs_37_38_and_without_any_export(self):
        # Jobs 37/38 (read-only check, fix pass): the export of the current
        # review is recorded, but the file is no longer in output/.
        moved = self.make_exported_job("tap37")
        self.output_of(moved).unlink()
        self.assertEqual(self.reason(moved), self.MOVED)
        # Only an older export of the job (another review) is recorded.
        stale = self.make_exported_job("tap36")
        self.output_of(stale).unlink()
        with self.store._lock:
            self.store._connection.execute("DELETE FROM artifacts WHERE job_id=?", (stale,))
            self.store._connection.commit()
        self.store.add_artifact(stale, stage_name="render", kind="final_output",
                                path="output/tap36-old-reviewed.mp4")
        self.assertEqual(self.reason(stale), self.STALE)
        never = self.make_exported_job("tap38")
        output = self.output_of(never)
        output.unlink()
        with self.store._lock:
            self.store._connection.execute("DELETE FROM artifacts WHERE job_id=?", (never,))
            self.store._connection.commit()
        self.assertEqual(self.reason(never), "Chưa có video xuất hợp lệ")

    def test_source_problems(self):
        missing = self.make_exported_job("tap1")
        self.source_of(missing).unlink()
        self.assertEqual(self.reason(missing), "Video gốc không còn trong thư mục input")
        outside = self.make_job(
            "outside", state="SKIPPED", items=[], source=self.root / "reports" / "outside.mp4",
        )
        self.skip_record(outside)
        self.assertEqual(self.reason(outside), "Video gốc không nằm trong thư mục input")
        grown = self.make_exported_job("grown")
        with self.source_of(grown).open("ab") as handle:
            handle.write(b"more")
        self.assertEqual(self.reason(grown), "Video gốc đã thay đổi so với lúc quét")
        linked = self.make_exported_job("linked")
        with patch.object(recycle_bin, "path_refusal", return_value=recycle_bin.LINK_MESSAGE):
            self.assertEqual(self.reason(linked), "Video gốc là liên kết (symlink/junction); không dọn.")

    def test_states_busy_jobs_and_unfinished_exports(self):
        state_reason = "Chỉ dọn được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)"
        for state in ("WAITING_REVIEW", "READY_TO_EXPORT", "QUEUED", "RENDERING", "CANCELLED", "FAILED"):
            with self.subTest(state):
                job_id = self.make_job(f"state-{state.lower()}", state=state, items=[item("a")])
                self.assertEqual(self.reason(job_id), state_reason)
        busy = self.make_exported_job("busy")
        with patch.object(self.scheduler, "is_busy", side_effect=lambda job_id: job_id == busy):
            self.assertEqual(self.reason(busy), "Video đang chạy hoặc đang xếp hàng")
        render = self.make_exported_job("render")
        self.store.ensure_stage(render, "render")
        self.store.update_stage(render, "render", state="PENDING")
        self.assertEqual(self.reason(render), "Còn lệnh xuất video chưa xong")

    def test_rows_already_recycled_or_still_pending(self):
        recycled = self.make_exported_job("recycled")
        self.add_row(recycled, state="RECYCLED")
        self.assertEqual(self.reason(recycled), "Video gốc đã được dọn trước đó")
        pending = self.make_exported_job("pending")
        self.add_row(pending, state="PENDING")
        self.assertEqual(self.reason(pending), "Đang chuyển video gốc này vào Thùng rác")
        failed = self.make_exported_job("failed")
        self.add_row(failed, state="FAILED")
        self.assertEqual(self.preview([failed])["count"], 1)

    def test_skip_records_that_do_not_match_the_current_review(self):
        reason = "Bản ghi bỏ qua không ứng với lần duyệt hiện tại"
        no_record = self.make_skipped_job("norecord", record=False)
        other_queue = self.make_skipped_job("otherqueue", queue_path="reports/jobs/x/review-queue.json")
        other_revision = self.make_skipped_job("otherrevision", revision=2)
        other_counts = self.make_skipped_job("othercounts", decisions={"KEEP": 2})
        edited = self.make_skipped_job("edited")
        queue = self.queue(edited)
        queue["items"][0]["decision"] = "BLUR"  # edited straight in the file
        self.queue_file(edited).write_text(json.dumps(queue), encoding="utf-8")
        for job_id in (no_record, other_queue, other_revision, other_counts, edited):
            with self.subTest(job_id=job_id):
                self.assertEqual(self.reason(job_id), reason)

    def test_unreadable_queues_and_unknown_jobs(self):
        corrupt = self.make_exported_job("corrupt")
        self.queue_file(corrupt).write_text("{not json", encoding="utf-8")
        self.assertEqual(self.reason(corrupt), "Không đọc được danh sách duyệt của video")
        escaped = self.make_exported_job("escaped")
        self.store.update_job(escaped, active_queue_path="work/review-queue.json")
        self.assertEqual(self.reason(escaped), "Không đọc được danh sách duyệt của video")
        preview = self.preview([999])
        self.assertEqual(preview["ineligible"], [{"job_id": 999, "name": "", "reason": "Không tìm thấy video #999"}])
        self.assertEqual((preview["count"], preview["total_bytes"], preview["blocked"]), (0, 0, None))
        with self.assertRaises(ValueError) as caught:
            self.execute([999], preview["preview_id"])
        self.assertEqual(str(caught.exception), "Không có video nào dọn được trong danh sách đã chọn.")
        self.assertEqual(self.recycler.calls, [])

    def test_preview_lists_are_sorted_and_counted(self):
        first = self.make_exported_job("a1")
        second = self.make_skipped_job("a2")
        blocked = self.make_job("a3", state="WAITING_REVIEW", items=[item("a")])
        preview = self.preview(f"{blocked},{second},{first},{first}")
        self.assertEqual([entry["job_id"] for entry in preview["eligible"]], [first, second])
        self.assertEqual([entry["job_id"] for entry in preview["ineligible"]], [blocked])
        self.assertEqual(preview["count"], 2)
        self.assertEqual(
            preview["total_bytes"],
            self.source_of(first).stat().st_size + self.source_of(second).stat().st_size,
        )


class ExecuteTests(CleanupFixture):
    def test_a_stale_preview_id_is_a_conflict_with_the_new_preview(self):
        job_id = self.make_exported_job("tap12")
        old = self.preview([job_id])["preview_id"]
        source = self.source_of(job_id)
        info = source.stat()
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        with self.assertRaises(CleanupConflict) as caught:
            self.execute([job_id], old)
        self.assertEqual(caught.exception.code, "preview_changed")
        self.assertEqual(str(caught.exception), "Danh sách đã thay đổi, hãy xem lại.")
        self.assertNotEqual(caught.exception.preview["preview_id"], old)
        self.assertEqual(caught.exception.preview["count"], 1)
        self.assertEqual((self.recycler.calls, self.hashed, self.all_rows()), ([], [], []))

    def test_bad_preview_ids_and_a_missing_recycler(self):
        job_id = self.make_exported_job("tap12")
        for value in (None, "", "abc", "A" * 64, "g" * 64, 12):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                self.execute([job_id], value)
            self.assertEqual(str(caught.exception), "Thiếu mã xem trước; hãy mở lại hộp thoại dọn video gốc.")
        for ids in ([], list(range(1, 52))):
            with self.assertRaises(ValueError):
                self.execute(ids, "0" * 64)
        with self.assertRaises(TypeError):
            execute_cleanup(self.root, self.store, self.scheduler, [job_id], "0" * 64, bin_info=self.bin)
        self.assertEqual(self.recycler.calls, [])

    def test_a_changed_output_or_source_fails_before_any_row(self):
        changed_output = self.make_exported_job("tap12")
        output = self.output_of(changed_output)
        data = bytearray(output.read_bytes())
        data[0] ^= 0xFF  # same size, other content
        output.write_bytes(bytes(data))
        changed_source = self.make_exported_job("tap13")
        source = self.source_of(changed_source)
        data = bytearray(source.read_bytes())
        data[-1] ^= 0xFF
        source.write_bytes(bytes(data))
        result = self.execute([changed_output, changed_source])
        self.assertEqual([(item["status"], item["message"]) for item in result["results"]], [
            ("FAILED", "Video xuất đã thay đổi so với manifest"),
            ("FAILED", "Video gốc đã thay đổi so với lúc quét"),
        ])
        self.assertEqual((result["failed_count"], result["recycled_count"], result["pending"]), (2, 0, 0))
        self.assertEqual((self.recycler.calls, self.all_rows()), ([], []))
        self.assertTrue(self.source_of(changed_output).is_file())
        for job_id, reason in ((changed_output, "Video xuất đã thay đổi so với manifest"),
                               (changed_source, "Video gốc đã thay đổi so với lúc quét")):
            events = self.events(job_id, "SOURCE_CLEANUP_FAILED")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["level"], "WARNING")
            self.assertEqual(events[0]["payload"], {"stage": "precheck", "reason": reason})

    def test_a_source_changed_while_hashing_is_refused(self):
        job_id = self.make_exported_job("tap12")
        source = self.source_of(job_id)

        def hasher(path):
            digest = sha256_file(Path(path))
            if Path(path) == source.resolve():
                info = source.stat()
                os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            return digest

        result = self.execute([job_id], hasher=hasher)
        self.assertEqual(result["results"][0]["message"], "Video gốc đã thay đổi trong lúc kiểm tra SHA-256")
        self.assertEqual((self.recycler.calls, self.all_rows()), ([], []))

    def test_one_failure_does_not_stop_the_other_videos(self):
        ids = [self.make_exported_job(f"tap{index}") for index in (1, 2, 3)]
        self.recycler.modes["tap2.mp4"] = "fail"
        result = self.execute(ids)
        self.assertEqual([item["status"] for item in result["results"]], ["RECYCLED", "FAILED", "RECYCLED"])
        self.assertEqual(result["results"][1]["message"], recycle_bin.SHARING_MESSAGE)
        self.assertEqual((result["recycled_count"], result["failed_count"], result["pending"]), (2, 1, 0))
        self.assertEqual(
            result["recycled_bytes"],
            sum(self.store.get_job(job_id)["source_size_bytes"] for job_id in (ids[0], ids[2])),
        )
        self.assertFalse(self.source_of(ids[0]).exists())
        self.assertTrue(self.source_of(ids[1]).is_file())
        self.assertFalse(self.source_of(ids[2]).exists())
        row = self.store.latest_source_cleanup(ids[1])
        self.assertEqual((row["state"], row["error"]), ("FAILED", recycle_bin.SHARING_MESSAGE))
        self.assertFalse(self.store.source_cleaned(ids[1]))
        events = self.events(ids[1], "SOURCE_CLEANUP_FAILED")
        self.assertEqual(events[0]["level"], "ERROR")
        job = self.store.get_job(ids[1])
        self.assertEqual(events[0]["payload"], {
            "stage": "recycle", "path": str(self.source_of(ids[1]).resolve()),
            "size_bytes": job["source_size_bytes"], "sha256": job["source_sha256"],
            "error": recycle_bin.SHARING_MESSAGE,
        })
        # The failed video is offered again.
        self.assertEqual(self.preview([ids[1]])["count"], 1)

    def test_unexpected_errors_fail_or_stay_pending_depending_on_the_file(self):
        still_there = self.make_exported_job("tap1")
        moved = self.make_exported_job("tap2")
        self.recycler.modes.update({"tap1.mp4": "unexpected", "tap2.mp4": "unexpected-after-move"})
        result = self.execute([still_there, moved])
        self.assertEqual([(item["status"], item["message"]) for item in result["results"]], [
            ("FAILED", "Lỗi không mong đợi: boom"),
            ("PENDING", "Lỗi không mong đợi: boom after the move"),
        ])
        self.assertEqual((result["failed_count"], result["pending"]), (1, 1))
        self.assertEqual(self.store.latest_source_cleanup(still_there)["state"], "FAILED")
        self.assertEqual(self.store.latest_source_cleanup(moved)["state"], "PENDING")
        for job_id in (still_there, moved):
            self.assertEqual(self.events(job_id, "SOURCE_CLEANUP_FAILED")[0]["level"], "ERROR")

    def test_a_timeout_leaves_the_row_pending_and_stops_the_batch(self):
        first = self.make_exported_job("tap1")
        second = self.make_exported_job("tap2")
        self.recycler.modes["tap1.mp4"] = "timeout"
        result = self.execute([first, second])
        self.assertEqual([(item["status"], item["message"]) for item in result["results"]], [
            ("PENDING", recycle_bin.TIMEOUT_MESSAGE),
            ("NOT_RUN", "Chưa chạy: lần chuyển trước chưa xong."),
        ])
        self.assertEqual((result["pending"], result["recycled_count"], result["failed_count"]), (1, 0, 0))
        self.assertEqual(len(self.recycler.calls), 1)
        row = self.store.latest_source_cleanup(first)
        self.assertEqual(row["state"], "PENDING")
        self.assertIsNone(self.store.latest_source_cleanup(second))
        events = self.events(first, "SOURCE_CLEANUP_PENDING")
        self.assertEqual(
            (events[0]["level"], events[0]["message"]),
            ("WARNING", "Windows chưa trả lời; video gốc đang chờ xác nhận chuyển vào Thùng rác"),
        )
        self.assertEqual(set(events[0]["payload"]), {"path", "size_bytes", "sha256"})
        # PENDING locks every action on the job.
        with self.assertRaises(ValueError) as caught:
            self.scheduler.rerun(first)
        self.assertEqual(str(caught.exception), SOURCE_CLEANED_MESSAGE)
        with self.assertRaises(ValueError) as caught:
            self.scheduler.cancel(first)
        self.assertEqual(str(caught.exception), SOURCE_CLEANED_STOP_REFUSAL)
        self.assertEqual(self.reason(first), "Đang chuyển video gốc này vào Thùng rác")
        # Windows answers later: the callback settles the row like a normal move.
        source = self.source_of(first)
        record = self.recycler.move(source)
        self.recycler.callbacks[0](RecycleResult(str(source), row["size_bytes"], True, record, 61.0), None)
        row = self.store.latest_source_cleanup(first)
        self.assertEqual((row["state"], row["verified"], row["recycle_record"]), ("RECYCLED", True, record))
        self.assertEqual(len(self.events(first, "SOURCE_RECYCLED")), 1)
        self.assertEqual(self.watcher_row(source), None)  # never observed: nothing to reset

    def test_a_late_failure_and_a_closed_store_in_the_callback(self):
        job_id = self.make_exported_job("tap1")
        self.recycler.modes["tap1.mp4"] = "timeout"
        self.execute([job_id])
        callback = self.recycler.callbacks[0]
        callback(None, RecycleFailed(recycle_bin.ABORTED_MESSAGE))
        row = self.store.latest_source_cleanup(job_id)
        self.assertEqual((row["state"], row["error"]), ("FAILED", recycle_bin.ABORTED_MESSAGE))
        self.assertEqual(self.events(job_id, "SOURCE_CLEANUP_FAILED")[0]["level"], "ERROR")
        # A second answer for the same row changes nothing, and a closed store is swallowed.
        callback(RecycleResult("x", 1, True, None, 1.0), None)
        self.assertEqual(self.store.latest_source_cleanup(job_id)["state"], "FAILED")
        self.store.close()
        callback(RecycleResult("x", 1, True, None, 1.0), None)

    def test_a_full_or_unavailable_bin_blocks_before_anything_runs(self):
        job_id = self.make_exported_job("tap12")
        size = self.source_of(job_id).stat().st_size
        self.bin = FakeBin(used=NEAR_FULL)
        preview = self.preview([job_id])
        capacity = recycle_bin.capacity_refusal(BinInfo("E:", "E:\\", GUID, MAX_BYTES, NEAR_FULL, ITEMS), size)
        self.assertEqual(preview["blocked"], capacity)
        self.assertTrue(capacity.startswith("Không thể dọn: Thùng rác của ổ E: đang chứa 48,5 GB, giới hạn 48,6 GB;"))
        with self.assertRaises(CleanupConflict) as caught:
            self.execute([job_id], preview["preview_id"])
        self.assertEqual((caught.exception.code, str(caught.exception)), ("bin_capacity", capacity))
        self.assertEqual(caught.exception.preview["blocked"], capacity)
        self.bin = FakeBin(error=RecycleRefused(recycle_bin.NUKE_MESSAGE.format(volume="E:")))
        preview = self.preview([job_id])
        self.assertIsNone(preview["recycle_bin"])
        self.assertEqual(preview["blocked"], recycle_bin.NUKE_MESSAGE.format(volume="E:"))
        with self.assertRaises(CleanupConflict) as caught:
            self.execute([job_id], preview["preview_id"])
        self.assertEqual(caught.exception.code, "bin_unavailable")
        self.assertEqual(str(caught.exception), recycle_bin.NUKE_MESSAGE.format(volume="E:"))
        self.assertEqual((self.recycler.calls, self.hashed, self.all_rows()), ([], [], []))

    def test_the_bin_is_checked_again_for_each_video(self):
        ids = [self.make_exported_job(f"tap{index}") for index in (1, 2)]
        # Preview, execute preview and video 1's re-check see room; video 2's does not.
        self.bin = FakeBin(sequence=[USED_BYTES, USED_BYTES, USED_BYTES, NEAR_FULL])
        result = self.execute(ids)
        self.assertEqual([item["status"] for item in result["results"]], ["RECYCLED", "FAILED"])
        self.assertTrue(result["results"][1]["message"].startswith("Không thể dọn: Thùng rác của ổ E:"))
        events = self.events(ids[1], "SOURCE_CLEANUP_FAILED")
        self.assertEqual(events[0]["payload"]["stage"], "recheck")
        self.assertIsNone(self.store.latest_source_cleanup(ids[1]))
        self.assertTrue(self.source_of(ids[1]).is_file())

    def test_a_rerun_between_hashing_and_the_lock_is_caught_by_the_recheck(self):
        job_id = self.make_exported_job("tap12")
        calls = []

        def hasher(path):
            digest = sha256_file(Path(path))
            if not calls:
                calls.append(path)
                self.scheduler.rerun(job_id)
            return digest

        result = self.execute([job_id], hasher=hasher)
        self.assertEqual(result["results"][0]["status"], "FAILED")
        self.assertEqual(
            result["results"][0]["message"], "Chỉ dọn được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)",
        )
        self.assertEqual((self.recycler.calls, self.all_rows()), ([], []))
        self.assertTrue(self.source_of(job_id).is_file())
        events = self.events(job_id, "SOURCE_CLEANUP_FAILED")
        self.assertEqual(events[0]["payload"]["stage"], "recheck")
        self.assertEqual(self.store.get_job(job_id)["state"], "QUEUED")

    def test_shutdown_marks_the_remaining_videos_not_run(self):
        ids = [self.make_exported_job(f"tap{index}") for index in (1, 2)]
        stop = threading.Event()

        def recycler(path, **kwargs):
            stop.set()  # BiliFlow starts shutting down during the first move
            return self.recycler(path, **kwargs)

        result = self.execute(ids, recycler=recycler, should_stop=stop.is_set)
        self.assertEqual([(item["status"], item["message"]) for item in result["results"]], [
            ("RECYCLED", "Đã chuyển video gốc vào Thùng rác"),
            ("NOT_RUN", "BiliFlow đang tắt; video này chưa được dọn."),
        ])
        self.assertEqual(len(self.recycler.calls), 1)

    def test_a_second_execute_while_one_runs_is_busy(self):
        first = self.make_exported_job("tap1")
        second = self.make_exported_job("tap2")
        release = threading.Event()
        self.recycler.modes["tap1.mp4"] = release
        first_id = self.preview([first])["preview_id"]
        second_id = self.preview([second])["preview_id"]
        outcome = {}
        worker = threading.Thread(target=lambda: outcome.update(result=self.execute([first], first_id)))
        worker.start()
        try:
            self.assertTrue(wait_until(lambda: len(self.recycler.calls) == 1))
            self.assertTrue(source_cleanup.cleanup_running())
            self.assertFalse(source_cleanup.wait_idle(0.1))
            with self.assertRaises(CleanupConflict) as caught:
                self.execute([second], second_id)
            self.assertEqual(
                (caught.exception.code, str(caught.exception), caught.exception.preview),
                ("busy", "Đang dọn video gốc; chờ lần dọn trước xong rồi thử lại.", None),
            )
        finally:
            release.set()
            worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome["result"]["recycled_count"], 1)
        self.assertTrue(source_cleanup.wait_idle(1.0))
        self.assertFalse(source_cleanup.cleanup_running())
        with patch.object(recycle_bin, "operations_in_progress", return_value=frozenset({"E:\\x.mp4"})):
            self.assertTrue(source_cleanup.cleanup_running())
            with self.assertRaises(CleanupConflict) as caught:
                self.execute([second], second_id)
            self.assertEqual(caught.exception.code, "busy")
        self.assertEqual(self.execute([second], second_id)["recycled_count"], 1)

    def test_no_deadlock_with_finalize_rerun_and_status_hints(self):
        cleaned = self.make_exported_job("tap1")
        rerun_job = self.make_exported_job("tap2")
        hint_job = self.make_exported_job("tap3")
        entered, release = threading.Event(), threading.Event()
        original = self.store.add_source_cleanup

        def blocking(**kwargs):
            entered.set()
            release.wait(10)
            return original(**kwargs)

        outcome = {}
        with patch.object(self.store, "add_source_cleanup", side_effect=blocking):
            cleaner = threading.Thread(target=lambda: outcome.update(result=self.execute([cleaned])))
            cleaner.start()
            self.assertTrue(entered.wait(10))

            def finalize_like():
                with REVIEW_QUEUE_IO, self.scheduler.job_action_lock:
                    outcome["finalize"] = True

            def rerun():
                outcome["rerun"] = self.scheduler.rerun(rerun_job)["state"]

            def hint():
                job = self.store.get_job(hint_job)
                outcome["hint"] = cleanup_hint(self.root, self.store, self.scheduler, job, latest_row=None)

            others = [threading.Thread(target=target) for target in (finalize_like, rerun, hint)]
            for thread in others:
                thread.start()
            time.sleep(0.2)
            # All three wait behind the locked section (lock order, not a deadlock) ...
            self.assertEqual(set(outcome), set())
            release.set()
            released = time.monotonic()
            for thread in [cleaner, *others]:
                thread.join(5)
            self.assertLess(time.monotonic() - released, 5)
        self.assertFalse(any(thread.is_alive() for thread in [cleaner, *others]))
        # ... and all of them finish once it is released.
        self.assertEqual(outcome["result"]["recycled_count"], 1)
        self.assertTrue(outcome["finalize"])
        self.assertEqual(outcome["rerun"], "QUEUED")
        self.assertTrue(outcome["hint"]["eligible"])


class HintAndReconcileTests(CleanupFixture):
    def test_hints_never_hash_and_parse_each_queue_version_once(self):
        job_id = self.make_exported_job("tap12")
        job = self.store.get_job(job_id)
        output = self.output_of(job_id)
        with patch.object(source_cleanup, "sha256_file", side_effect=AssertionError("hashed")), \
                patch("biliflow.job_store.sha256_file", side_effect=AssertionError("hashed")), \
                patch.object(source_cleanup, "_parse_queue_facts", wraps=source_cleanup._parse_queue_facts) as parse:
            hints = [
                cleanup_hint(self.root, self.store, self.scheduler, job, latest_row=None) for _ in range(3)
            ]
            self.assertEqual(parse.call_count, 1)
            queue_file = self.queue_file(job_id)
            info = queue_file.stat()
            os.utime(queue_file, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            cleanup_hint(self.root, self.store, self.scheduler, job, latest_row=None)
            self.assertEqual(parse.call_count, 2)
        manifest = json.loads(output.with_suffix(".mp4.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(hints[0], {
            "eligible": True, "kind": "EXPORTED", "reason": None,
            "size_bytes": job["source_size_bytes"], "output_name": output.name,
            "output_bytes": manifest["output"]["bytes"], "exported_at": manifest["created_at"],
            "skipped_at": None,
        })
        self.assertEqual(hints[0], hints[2])

    def test_hints_exist_only_for_finished_jobs_and_follow_the_state_kind(self):
        waiting = self.make_job("waiting", state="READY_TO_EXPORT", items=[item("a")])
        self.assertIsNone(cleanup_hint(
            self.root, self.store, self.scheduler, self.store.get_job(waiting), latest_row=None,
        ))
        skipped = self.make_skipped_job("skipped", record=False)
        hint = cleanup_hint(self.root, self.store, self.scheduler, self.store.get_job(skipped), latest_row=None)
        self.assertEqual(
            (hint["eligible"], hint["kind"], hint["reason"], hint["output_name"]),
            (False, "SKIPPED", "Bản ghi bỏ qua không ứng với lần duyệt hiện tại", None),
        )
        recycled = self.make_exported_job("recycled")
        row_id = self.add_row(recycled, state="RECYCLED")
        row = self.store.latest_source_cleanup(recycled)
        hint = cleanup_hint(self.root, self.store, self.scheduler, self.store.get_job(recycled), latest_row=row)
        self.assertEqual((hint["eligible"], hint["kind"], hint["reason"]),
                         (False, "EXPORTED", "Video gốc đã được dọn trước đó"))
        self.assertEqual(row["id"], row_id)

    def test_reconcile_settles_pending_rows_after_a_crash(self):
        present = self.make_exported_job("present")
        gone = self.make_exported_job("gone")
        unverified = self.make_exported_job("unverified")
        broken = self.make_exported_job("broken")
        running = self.make_exported_job("running")
        rows = {job_id: self.add_row(job_id, state="PENDING")
                for job_id in (present, gone, unverified, broken, running)}
        for job_id in (gone, unverified, broken, running):
            self.recycler.move(self.source_of(job_id))
        calls = []

        def finder(volume_root, original, size, *, since):
            calls.append((volume_root, original, size, since))
            name = Path(original).name
            if name == "broken.mp4":
                raise PermissionError("Access is denied")
            return "E:\\$Recycle.Bin\\S-1\\$IABC.mp4" if name == "gone.mp4" else None

        running_path = str(self.source_of(running).resolve())
        with patch.object(recycle_bin, "operations_in_progress", return_value=frozenset({running_path})):
            settled = reconcile_pending_cleanups(self.root, self.store, finder=finder)
        self.assertEqual(sorted(settled), sorted(rows[job_id] for job_id in (present, gone, unverified, broken)))
        row = self.store.latest_source_cleanup(present)
        self.assertEqual((row["state"], row["error"]), ("FAILED", "Bị gián đoạn trước khi chuyển; video gốc vẫn còn."))
        event = self.events(present, "SOURCE_CLEANUP_FAILED")[0]
        self.assertEqual((event["level"], event["payload"]["stage"]), ("WARNING", "reconcile"))
        row = self.store.latest_source_cleanup(gone)
        self.assertEqual((row["state"], row["verified"], row["recycle_record"]),
                         ("RECYCLED", True, "E:\\$Recycle.Bin\\S-1\\$IABC.mp4"))
        self.assertEqual(len(self.events(gone, "SOURCE_RECYCLED")), 1)
        for job_id in (unverified, broken):
            row = self.store.latest_source_cleanup(job_id)
            self.assertEqual((row["state"], row["verified"]), ("RECYCLED", False))
            self.assertEqual(len(self.events(job_id, "SOURCE_RECYCLE_UNVERIFIED")), 1)
        self.assertEqual(self.store.latest_source_cleanup(running)["state"], "PENDING")
        gone_row = self.store.latest_source_cleanup(gone)
        created = datetime.fromisoformat(gone_row["created_at"]).timestamp()
        call = next(item for item in calls if Path(item[1]).name == "gone.mp4")
        self.assertEqual(call[0], Path(gone_row["source_path"]).anchor)
        self.assertEqual((call[1], call[2]), (gone_row["source_path"], gone_row["size_bytes"]))
        self.assertAlmostEqual(call[3], created - 5, places=3)
        # Once its shell call is over, the last row is settled too; then nothing is left.
        self.assertEqual(reconcile_pending_cleanups(self.root, self.store, finder=finder), [rows[running]])
        self.assertEqual(self.store.latest_source_cleanup(running)["state"], "RECYCLED")
        self.assertEqual(reconcile_pending_cleanups(self.root, self.store, finder=finder), [])


class RecycleRecheckTests(CleanupFixture):
    """"Kiểm tra lại Thùng rác" (jobs 43, 47): reads the bin, appends a row, never edits the cleanup."""

    RECORD = "E:\\$Recycle.Bin\\S-1-5-21-1000\\$I4RHHWK.mp4"

    def unverified(self, name="a"):
        job_id = self.make_exported_job(name)
        self.recycler.modes[f"{name}.mp4"] = "unverified"
        self.assertEqual(self.execute([job_id])["results"][0]["status"], "UNVERIFIED")
        return job_id, self.store.latest_source_cleanup(job_id)

    def recheck(self, subject_id, finder, kind="source_cleanup"):
        return recheck_recycle_record(self.root, self.store, kind, subject_id, finder=finder)

    def test_recheck_appends_a_row_and_never_changes_the_cleanup_row(self):
        job_id, row = self.unverified()
        calls = []

        def missing(volume_root, original, size, *, since, until=None):
            calls.append((volume_root, original, size, since))
            return None

        result = self.recheck(row["id"], missing)
        self.assertEqual((result["found"], result["record"], result["id"], result["job_id"]),
                         (False, None, row["id"], job_id))
        self.assertEqual(result["message"], source_cleanup.RECHECK_MISSING_MESSAGE.format(what="video gốc"))
        self.assertIsNotNone(result["checked_at"])
        created = datetime.fromisoformat(row["created_at"]).timestamp()
        self.assertEqual(calls[0][:3], (Path(row["source_path"]).anchor, row["source_path"], row["size_bytes"]))
        self.assertAlmostEqual(calls[0][3], created - 5, places=3)
        found = self.recheck(row["id"], lambda *args, **kwargs: self.RECORD)
        self.assertEqual((found["found"], found["record"]), (True, self.RECORD))
        self.assertEqual(found["message"], source_cleanup.RECHECK_FOUND_MESSAGE.format(what="video gốc"))
        # The cleanup row is exactly what the cleanup recorded.
        self.assertEqual(self.store.latest_source_cleanup(job_id), row)
        checks = self.store.recycle_checks("SOURCE_CLEANUP", row["id"])
        self.assertEqual([(item["found"], item["recycle_record"], item["actor"]) for item in checks],
                         [(False, None, "control_center_user"), (True, self.RECORD, "control_center_user")])
        self.assertEqual((checks[0]["path"], checks[0]["size_bytes"], checks[0]["job_id"]),
                         (row["source_path"], row["size_bytes"], job_id))
        still = self.events(job_id, "SOURCE_RECYCLE_STILL_UNVERIFIED")
        verified = self.events(job_id, "SOURCE_RECYCLE_VERIFIED")
        self.assertEqual((len(still), still[0]["level"], len(verified), verified[0]["level"]),
                         (1, "WARNING", 1, "INFO"))
        self.assertEqual(verified[0]["payload"], {
            "kind": "SOURCE_CLEANUP", "subject_id": row["id"], "path": row["source_path"],
            "size_bytes": row["size_bytes"], "recycle_record": self.RECORD, "check_id": checks[1]["id"],
        })
        summary = cleanup_row_summary(row, self.store.recycle_check_summary("SOURCE_CLEANUP")[row["id"]])
        self.assertEqual((summary["verified"], summary["verified_at_cleanup"], summary["verified_later_at"]),
                         (True, False, checks[1]["checked_at"]))
        # Found once: nothing is left to check and nothing more is written.
        with self.assertRaises(ActionConflict) as caught:
            self.recheck(row["id"], missing)
        self.assertEqual(caught.exception.code, "already_verified")
        self.assertEqual(len(self.store.recycle_checks("SOURCE_CLEANUP", row["id"])), 2)
        self.assertEqual(len(calls), 1)

    def test_recheck_ignores_a_later_record_of_the_same_file(self):
        # Security review (L5): the same path and size moved to the bin again later (by hand, by
        # another job, after a re-export) must not verify this older move.
        job_id, row = self.unverified()
        finished = datetime.fromisoformat(row["finished_at"]).timestamp()
        records, seen = [], []

        def finder(volume_root, original, size, *, since, until=None):
            seen.append((since, until))
            hits = [entry for entry in records if entry[0] >= since and (until is None or entry[0] <= until)]
            return max(hits)[1] if hits else None

        later = "E:\\$Recycle.Bin\\S-1-5-21-1000\\$ILATER1.mp4"
        records.append((finished + 3600, later))
        result = self.recheck(row["id"], finder)
        self.assertEqual((result["found"], result["record"]), (False, None))
        slack = source_cleanup.RECHECK_UNTIL_SLACK_SECONDS
        self.assertEqual(slack, 60)
        self.assertAlmostEqual(seen[0][1], finished + slack, places=3)
        # This move's own record (written up to a minute after the row settled) still verifies it.
        records.append((finished + 30, self.RECORD))
        result = self.recheck(row["id"], finder)
        self.assertEqual((result["found"], result["record"]), (True, self.RECORD))
        self.assertEqual(self.store.latest_source_cleanup(job_id), row)

    def test_recheck_refusals(self):
        job_id, row = self.unverified()

        def never(*args, **kwargs):
            raise AssertionError("the bin must not be read")

        for kind, subject in (("SOURCE_CLEANUP", row["id"]), ("cleanup", row["id"]), (None, row["id"]),
                              ("source_cleanup", 0), ("source_cleanup", "1"), ("source_cleanup", True),
                              ("source_cleanup", None), ("source_cleanup", 2**31)):
            with self.subTest(kind=kind, subject=subject), self.assertRaises(ValueError):
                recheck_recycle_record(self.root, self.store, kind, subject, finder=never)
        with self.assertRaises(ValueError) as caught:
            self.recheck(9999, never)
        self.assertEqual(str(caught.exception), "Không tìm thấy bản ghi #9999.")
        with self.assertRaises(ValueError):
            self.recheck(row["id"], never, kind="archive_export")
        # Busy: another cleanup, archive, restore or re-check holds the one lock.
        self.assertTrue(SOURCE_FILE_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(ActionConflict) as caught:
                self.recheck(row["id"], never)
        finally:
            SOURCE_FILE_LOCK.release()
        self.assertEqual((caught.exception.code, str(caught.exception)), ("busy", source_cleanup.SOURCE_BUSY_MESSAGE))
        # An unreadable bin writes nothing.
        with self.assertRaises(ActionConflict) as caught:
            self.recheck(row["id"], lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")))
        self.assertEqual(caught.exception.code, "bin_unavailable")
        self.assertEqual(self.store.recycle_checks("SOURCE_CLEANUP", row["id"]), [])
        self.assertEqual(self.events(job_id, "SOURCE_RECYCLE_STILL_UNVERIFIED"), [])
        # Verified at cleanup, failed, restored, or no longer the job's latest row: nothing to check.
        verified_job = self.make_exported_job("verified")
        self.execute([verified_job])
        failed_job = self.make_exported_job("failed")
        self.recycler.modes["failed.mp4"] = "fail"
        self.execute([failed_job])
        cases = {
            "already_verified": self.store.latest_source_cleanup(verified_job)["id"],
            "not_recheckable": self.store.latest_source_cleanup(failed_job)["id"],
        }
        for code, subject in cases.items():
            with self.subTest(code=code), self.assertRaises(ActionConflict) as caught:
                self.recheck(subject, never)
            self.assertEqual(caught.exception.code, code)
        self.store.mark_source_restored(row["id"], mtime_ns=5)
        with self.assertRaises(ActionConflict) as caught:
            self.recheck(row["id"], never)
        self.assertEqual(caught.exception.code, "not_recheckable")
        self.assertEqual(self.store.recycle_checks("SOURCE_CLEANUP", row["id"]), [])


class ArchiveInteractionTests(CleanupFixture):
    """Batch 4: "Lưu trữ" locks a job against "Dọn video gốc", and both share one lock."""

    def add_archive(self, job_id):
        job = self.store.get_job(job_id)
        output = self.output_of(job_id)
        target = self.root / "archive" / "sources" / job["job_key"] / self.source_of(job_id).name
        return self.store.add_source_archive(
            job_id=job_id, kind="EXPORTED", source_path=job["source_path"], archive_path=str(target),
            manifest_path=str(target.parent / "archive-manifest.json"), source_sha256=job["source_sha256"],
            size_bytes=job["source_size_bytes"], mtime_ns=job["source_mtime_ns"],
            queue_path=job["active_queue_path"], revision=1,
            output_path=output.relative_to(self.root).as_posix(), output_bytes=output.stat().st_size,
            output_manifest_path=output.relative_to(self.root).as_posix() + ".manifest.json",
            output_manifest_bytes=1,
        )

    def test_an_archived_job_cannot_be_cleaned(self):
        job_id = self.make_exported_job("tap20")
        job = self.store.get_job(job_id)
        reason = "Video gốc đang ở kho lưu trữ"
        row_id = self.add_archive(job_id)
        # The latest archive row is the lock, wherever the file is (here it is still in input/).
        for step in ("PENDING", "ARCHIVED", "RESTORING"):
            with self.subTest(state=step):
                if step == "ARCHIVED":
                    self.store.set_source_archive_phase(row_id, "SOURCE_VERIFIED", source_verified=True)
                    self.store.finish_source_archive(row_id, state="ARCHIVED")
                elif step == "RESTORING":
                    self.store.begin_archive_restore(row_id)
                self.assertEqual(self.store.latest_source_archive(job_id)["state"], step)
                self.assertEqual(self.store.source_lock(job_id), "archived")
                self.assertEqual(self.reason(job_id), reason)
                hint = cleanup_hint(self.root, self.store, self.scheduler, self.store.get_job(job_id),
                                    latest_row=None)
                self.assertEqual((hint["eligible"], hint["reason"]), (False, reason))
                with self.assertRaises(ValueError):  # nothing eligible
                    self.execute([job_id])
        self.assertEqual((self.hashed, self.recycler.calls, self.all_rows()), ([], [], []))
        # Restored: the job may be cleaned again (and a FAILED archive never locked it).
        self.store.finish_archive_restore(row_id, mtime_ns=job["source_mtime_ns"], job_state=None)
        self.assertIsNone(self.store.source_lock(job_id))
        self.assertEqual(self.preview([job_id])["count"], 1)
        failed = self.make_exported_job("tap21")
        self.store.finish_source_archive(self.add_archive(failed), state="FAILED", error="x")
        self.assertEqual(self.preview([failed])["count"], 1)
        result = self.execute([job_id, failed])
        self.assertEqual([entry["status"] for entry in result["results"]], ["RECYCLED", "RECYCLED"])

    def test_cleanup_and_archive_share_one_lock(self):
        from biliflow.source_archive import execute_archive, preview_archive
        from biliflow.source_archive_restore import restore_archive

        cleaned = self.make_exported_job("tap22")
        skipped = self.make_skipped_job("tap23")
        archive_id = preview_archive(self.root, self.store, self.scheduler, [skipped], bin_info=self.bin)["preview_id"]
        release = threading.Event()
        self.recycler.modes["tap22.mp4"] = release
        cleanup_id = self.preview([cleaned])["preview_id"]
        outcome = {}
        worker = threading.Thread(target=lambda: outcome.update(cleanup=self.execute([cleaned], cleanup_id)))
        worker.start()
        try:
            self.assertTrue(wait_until(lambda: len(self.recycler.calls) == 1))
            for action in (
                lambda: execute_archive(self.root, self.store, self.scheduler, [skipped], archive_id,
                                        recycler=self.recycler, bin_info=self.bin, hasher=self.hasher),
                lambda: restore_archive(self.root, self.store, self.scheduler, skipped),
            ):
                with self.assertRaises(ActionConflict) as caught:
                    action()
                self.assertEqual((caught.exception.code, str(caught.exception)),
                                 ("busy", source_cleanup.SOURCE_BUSY_MESSAGE))
        finally:
            release.set()
            worker.join(10)
        self.assertEqual(outcome["cleanup"]["recycled_count"], 1)
        # An archive holds the lock while it hashes: a cleanup is refused, never queued.
        hashing, resume = threading.Event(), threading.Event()
        other = self.make_exported_job("tap24")
        other_id = self.preview([other])["preview_id"]

        def slow_hasher(path):
            hashing.set()
            resume.wait(10)
            return self.hasher(path)

        archiver = threading.Thread(target=lambda: outcome.update(archive=execute_archive(
            self.root, self.store, self.scheduler, [skipped], archive_id, recycler=self.recycler,
            bin_info=self.bin, hasher=slow_hasher)))
        archiver.start()
        try:
            self.assertTrue(hashing.wait(5))
            self.assertTrue(source_cleanup.cleanup_running())
            with self.assertRaises(CleanupConflict) as caught:
                self.execute([other], other_id)
            self.assertEqual(caught.exception.code, "busy")
        finally:
            resume.set()
            archiver.join(10)
        self.assertFalse(archiver.is_alive())
        self.assertEqual(outcome["archive"]["archived_count"], 1)
        self.assertEqual(self.store.source_lock(skipped), "archived")
        self.assertEqual(self.execute([other], other_id)["recycled_count"], 1)


if __name__ == "__main__":
    unittest.main()
