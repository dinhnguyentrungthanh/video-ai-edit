"""job_purge: what removing one job deletes, what it protects, and JobStore.purge_job.

Every root is a temporary folder under ``<install>/temp`` (the deleting
functions refuse any other root); nothing here touches the project's
input, output, reports, archive or state.
"""

import hashlib
import json
import os
import shutil
import stat
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import job_purge, recycle_bin
from biliflow.job_store import JOB_ROW_TABLES, JOB_SETTING_NAMES, JobStore


TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
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


class PurgeFixture(unittest.TestCase):
    """A temporary project root under <install>/temp with a real JobStore."""

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "reports/jobs", "output", "work", "state", "logs/control-center"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.store.close)
        job_purge.clear_caches()
        self.addCleanup(job_purge.clear_caches)

    def make_job(self, name, *, state="COMPLETED", source=True, key=None):
        path = self.root / "input" / f"{name}.mp4"
        data = name.encode() * 512
        digest = hashlib.sha256(data).hexdigest()
        if source:
            path.write_bytes(data)
        job = self.store.upsert_job(
            job_key=key or f"{name}-{digest[:8]}", source_path=path, source_sha256=digest,
            source_size_bytes=len(data), source_mtime_ns=path.stat().st_mtime_ns if source else 1,
            content_style="animation", state=state,
        )
        return job

    def make_report(self, job, folder, *, record=True, files=("review-queue.json", "frames/a.jpg")):
        directory = self.root / "reports" / "jobs" / folder
        for relative in files:
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * 100)
        if record:
            self.store.add_revision(job["id"], f"reports/jobs/{folder}/review-queue.json", "WAITING_REVIEW", None)
        return directory

    def populate(self, job):
        """One row in every table that names the job, its settings and its watcher row."""
        job_id = int(job["id"])
        self.store.add_revision(job_id, f"reports/jobs/{job['job_key']}/review-queue.json", "READY", None)
        self.store.add_artifact(job_id, stage_name="render", kind="final_output", path=f"output/{job_id}.mp4")
        self.store.ensure_stage(job_id, "render")
        self.store.add_event(job_id, "TEST_EVENT", "an event")
        for name in JOB_SETTING_NAMES:
            self.store.set_setting(f"{name}:{job_id}", {"job": job_id})
        cleanup = self.store.add_source_cleanup(
            job_id=job_id, kind="EXPORTED", source_path=job["source_path"],
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"], mtime_ns=1,
        )
        self.store.finish_source_cleanup(cleanup, state="RECYCLED", verified=False)
        self.store.add_recycle_check(
            kind="SOURCE_CLEANUP", subject_id=cleanup, job_id=job_id, path=job["source_path"],
            size_bytes=job["source_size_bytes"], found=False, recycle_record=None, actor="test",
        )
        archive = self.store.add_source_archive(
            job_id=job_id, kind="EXPORTED", source_path=job["source_path"],
            archive_path=f"archive/sources/{job['job_key']}/a.mp4",
            manifest_path=f"archive/sources/{job['job_key']}/archive-manifest.json",
            source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"], mtime_ns=1,
            queue_path=f"reports/jobs/{job['job_key']}/review-queue.json",
        )
        self.store.finish_source_archive(archive, state="FAILED", error="test")
        path = Path(job["source_path"])
        self.store.observe_file(path, int(job["source_size_bytes"]), 5)
        self.store.mark_file_imported(path, job_id)

    def counts(self, job_id):
        tables = (
            "jobs", "job_revisions", "stages", "artifacts", "events", "source_cleanups",
            "source_archives", "recycle_checks",
        )
        result = {}
        with self.store._lock:
            for table in tables:
                column = "id" if table == "jobs" else "job_id"
                result[table] = self.store._connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (job_id,)
                ).fetchone()[0]
        return result

    def watcher_row(self, path):
        with self.store._lock:
            row = self.store._connection.execute(
                "SELECT * FROM watcher_files WHERE path=?", (str(Path(path).resolve()),)
            ).fetchone()
        return None if row is None else dict(row)


class PurgeJobRowsTests(PurgeFixture):
    def test_any_error_mid_purge_rolls_every_row_back(self):
        job = self.make_job("alpha")
        self.populate(job)
        before = self.counts(job["id"])

        class Broken:
            def __iter__(self):
                raise RuntimeError("boom")

        # The settings are deleted after the rows of every job table: a transaction is open by then.
        with patch("biliflow.job_store.JOB_SETTING_NAMES", Broken()), self.assertRaises(RuntimeError):
            self.store.purge_job(job["id"])
        self.assertFalse(self.store._connection.in_transaction)
        self.store.add_event(job["id"], "AFTER", "a later commit never completes half a removal")
        after = self.counts(job["id"])
        self.assertEqual({**after, "events": after["events"] - 1}, before)
        self.assertTrue(self.store.purge_job(job["id"]))
        self.assertEqual(set(self.counts(job["id"]).values()), {0})

    def test_a_source_path_that_cannot_be_resolved_still_purges(self):
        job = self.make_job("alpha")
        self.populate(job)
        with patch.object(Path, "resolve", side_effect=RuntimeError("Symlink loop")):
            self.assertTrue(self.store.purge_job(job["id"]))
        self.assertFalse(self.store._connection.in_transaction)
        self.assertEqual(set(self.counts(job["id"]).values()), {0})
        row = self.watcher_row(job["source_path"])
        self.assertEqual((row["size_bytes"], row["mtime_ns"], row["imported_job_id"]), (-1, -1, None))

    def test_every_table_with_a_key_to_jobs_is_purged(self):
        """A table added later (another branch) that names jobs(id) must be listed or cascade."""
        with self.store._lock:
            connection = self.store._connection
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            naming = {
                table: key["on_delete"]
                for table in tables
                for key in connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
                if key["table"] == "jobs"
            }
        self.assertTrue(set(JOB_ROW_TABLES) <= set(naming), naming)
        for table, on_delete in naming.items():
            with self.subTest(table=table):
                self.assertTrue(table in JOB_ROW_TABLES or on_delete in ("CASCADE", "SET NULL"), on_delete)

    def test_every_row_of_the_job_goes_and_nothing_else_changes(self):
        alpha, beta = self.make_job("alpha"), self.make_job("beta")
        self.populate(alpha)
        self.populate(beta)
        self.store.set_setting("scheduler_paused", True)
        before_beta = self.counts(beta["id"])
        beta_watcher = self.watcher_row(beta["source_path"])

        self.assertTrue(self.store.purge_job(alpha["id"]))

        self.assertEqual(set(self.counts(alpha["id"]).values()), {0})
        self.assertEqual(self.counts(beta["id"]), before_beta)
        for name in JOB_SETTING_NAMES:
            self.assertIsNone(self.store.setting(f"{name}:{alpha['id']}"))
            self.assertEqual(self.store.setting(f"{name}:{beta['id']}"), {"job": beta["id"]})
        self.assertTrue(self.store.setting("scheduler_paused"))
        reset = self.watcher_row(alpha["source_path"])
        self.assertIsNotNone(reset, "the watcher row is reset, never deleted")
        self.assertEqual((reset["size_bytes"], reset["mtime_ns"], reset["imported_job_id"]), (-1, -1, None))
        self.assertEqual(self.watcher_row(beta["source_path"]), beta_watcher)
        with self.assertRaises(KeyError):
            self.store.get_job(alpha["id"])
        self.assertEqual([job["id"] for job in self.store.list_jobs()], [beta["id"]])

    def test_an_unknown_job_is_false_and_writes_nothing(self):
        alpha = self.make_job("alpha")
        self.assertFalse(self.store.purge_job(alpha["id"] + 100))
        self.assertEqual(self.counts(alpha["id"])["jobs"], 1)

    def test_a_watcher_row_another_job_imported_at_that_path_is_kept(self):
        alpha = self.make_job("alpha", source=False)
        beta = self.make_job("beta")
        # beta's file now sits at the path alpha recorded (alpha's source is gone).
        path = Path(alpha["source_path"])
        path.write_bytes(b"beta now")
        self.store.observe_file(path, 8, 9)
        self.store.mark_file_imported(path, beta["id"])
        row = self.watcher_row(path)
        self.assertTrue(self.store.purge_job(alpha["id"]))
        self.assertEqual(self.watcher_row(path), row)

    def test_ids_are_never_reused_after_a_purge(self):
        alpha = self.make_job("alpha")
        self.store.purge_job(alpha["id"])
        again = self.make_job("alpha")
        self.assertGreater(again["id"], alpha["id"])
        self.assertEqual(again["job_key"], alpha["job_key"])


class GoldenProtectionTests(PurgeFixture):
    def write_golden(self, name, sources):
        path = self.root / "annotations" / "golden" / name / "segments.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "sources": sources, "segments": []}), encoding="utf-8")
        return path

    def test_no_golden_folder_protects_nothing(self):
        job = self.make_job("alpha")
        self.assertIsNone(job_purge.protected_reason(self.root, job))

    def test_a_golden_source_is_protected_by_job_id_or_by_sha256(self):
        by_id, by_sha, free = self.make_job("alpha"), self.make_job("beta"), self.make_job("gamma")
        self.write_golden("v1", {"a": {"job_id": by_id["id"], "sha256": "0" * 64, "path": "input/x.mp4"}})
        self.write_golden("v2", {"b": {"job_id": 999, "sha256": by_sha["source_sha256"], "path": "input/y.mp4"}})
        self.assertEqual(job_purge.protected_reason(self.root, by_id), job_purge.REASON_GOLDEN)
        self.assertEqual(job_purge.protected_reason(self.root, by_sha), job_purge.REASON_GOLDEN)
        self.assertIsNone(job_purge.protected_reason(self.root, free))

    def test_an_unreadable_golden_file_locks_every_delete(self):
        job = self.make_job("alpha")
        cases = (
            "{not json",
            json.dumps({"sources": []}),
            json.dumps({"sources": {"a": {"job_id": "1", "sha256": "0" * 64}}}),
            json.dumps({"sources": {"a": {"job_id": 1, "sha256": 5}}}),
        )
        for text in cases:
            with self.subTest(text=text):
                path = self.root / "annotations" / "golden" / "v9" / "segments.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                os.utime(path, ns=(path.stat().st_mtime_ns + 10**9,) * 2)
                reason = job_purge.protected_reason(self.root, job)
                self.assertEqual(
                    reason, job_purge.REASON_GOLDEN_UNREADABLE.format(path="annotations/golden/v9/segments.json"),
                )

    def test_the_index_follows_a_changed_file(self):
        job = self.make_job("alpha")
        path = self.write_golden("v1", {"a": {"job_id": 999, "sha256": "0" * 64, "path": "input/x.mp4"}})
        self.assertIsNone(job_purge.protected_reason(self.root, job))
        path.write_text(json.dumps({"sources": {"a": {"job_id": job["id"], "sha256": "1" * 64}}}), encoding="utf-8")
        os.utime(path, ns=(path.stat().st_mtime_ns + 10**9,) * 2)
        self.assertEqual(job_purge.protected_reason(self.root, job), job_purge.REASON_GOLDEN)


class OwnedReportDirsTests(PurgeFixture):
    def names(self, job):
        return [path.name for path in job_purge.owned_report_dirs(self.root, self.store, job)]

    def test_only_the_jobs_own_folders_in_reports_jobs(self):
        job = self.make_job("alpha", key="alpha-1234abcd")
        self.make_report(job, "alpha-1234abcd")
        self.make_report(job, "alpha-1234abcd-run-20261005-101010", record=False)  # a run with no revision yet
        self.make_report(job, "alpha-1234abcd-other", record=False)
        self.make_report(job, "alpha-1234abcdx", record=False)
        marked = self.make_report(job, "alpha-1234abcd-run-20261005-111111", record=False)
        (marked / ".biliflow-benchmark").write_text("Isolated trial; never auto-import", encoding="utf-8")
        legacy = self.root / "reports" / "alpha-review-v1"
        legacy.mkdir()
        (legacy / "review-queue.json").write_text("{}", encoding="utf-8")
        self.store.add_revision(job["id"], "reports/alpha-review-v1/review-queue.json", "READY", None)
        (self.root / "reports" / "jobs" / "alpha-1234abcd-run-file").write_text("a file", encoding="utf-8")

        self.assertEqual(self.names(job), ["alpha-1234abcd", "alpha-1234abcd-run-20261005-101010"])

    def test_a_folder_another_job_still_names_is_kept(self):
        alpha = self.make_job("alpha", key="alpha-1234abcd")
        beta = self.make_job("beta", key="beta-5678ef01")
        self.make_report(alpha, "alpha-1234abcd")
        self.make_report(alpha, "alpha-1234abcd-run-20261005-101010", record=False)
        self.store.add_artifact(
            beta["id"], stage_name="import", kind="review_queue",
            path="reports/jobs/alpha-1234abcd-run-20261005-101010/review-queue.json",
        )
        self.assertEqual(self.names(alpha), ["alpha-1234abcd"])

    def test_the_longest_job_key_owns_a_folder(self):
        short = self.make_job("alpha", key="a-11111111")
        long = self.make_job("beta", key="a-11111111-run-x-22222222")
        self.make_report(short, "a-11111111")
        self.make_report(long, "a-11111111-run-x-22222222")
        self.make_report(long, "a-11111111-run-x-22222222-run-20261005-101010", record=False)
        self.assertEqual(self.names(short), ["a-11111111"])
        self.assertEqual(
            self.names(long), ["a-11111111-run-x-22222222", "a-11111111-run-x-22222222-run-20261005-101010"],
        )

    @unittest.skipUnless(sys.platform == "win32", "directory junctions are a Windows feature")
    def test_a_linked_folder_is_never_owned(self):
        import _winapi

        job = self.make_job("alpha", key="alpha-1234abcd")
        target = self.root / "elsewhere"
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        _winapi.CreateJunction(str(target), str(self.root / "reports" / "jobs" / "alpha-1234abcd"))
        self.assertEqual(self.names(job), [])


class LogFilesTests(PurgeFixture):
    def test_only_this_jobs_logs(self):
        job = self.make_job("alpha")
        logs = self.root / "logs" / "control-center"
        own = [f"job-{job['id']}-text-attempt-1.log", f"job-{job['id']}-render-attempt-2.log"]
        other = [f"job-{job['id']}0-text-attempt-1.log", f"job-{job['id']}.log", "control-center-1.out.log"]
        for name in own + other:
            (logs / name).write_text("log", encoding="utf-8")
        self.assertEqual(sorted(path.name for path in job_purge.job_log_files(self.root, job["id"])), sorted(own))

    def test_no_log_folder_is_no_log(self):
        job = self.make_job("alpha")
        (self.root / "logs" / "control-center").rmdir()
        self.assertEqual(job_purge.job_log_files(self.root, job["id"]), [])


class RemoveJobFilesTests(PurgeFixture):
    def test_folders_and_logs_are_removed(self):
        job = self.make_job("alpha", key="alpha-1234abcd")
        folder = self.make_report(job, "alpha-1234abcd")
        log = self.root / "logs" / "control-center" / f"job-{job['id']}-text-attempt-1.log"
        log.write_text("log", encoding="utf-8")
        self.assertEqual(job_purge.files_bytes([folder, log]), 203)
        self.assertEqual(job_purge.remove_job_files(self.root, [folder, log]), [])
        self.assertFalse(folder.exists())
        self.assertFalse(log.exists())
        self.assertEqual(job_purge.remove_job_files(self.root, [folder, log]), [], "already gone is not an error")

    def test_a_failure_is_reported_and_the_rest_still_goes(self):
        if sys.platform != "win32":
            self.skipTest("open files block a delete only on Windows")
        job = self.make_job("alpha", key="alpha-1234abcd")
        stuck = self.make_report(job, "alpha-1234abcd")
        other = self.make_report(job, "alpha-1234abcd-run-20261005-101010", record=False)
        locked = stuck / "frames" / "a.jpg"
        handle = open(locked, "rb")  # Windows refuses to delete a file opened without delete sharing
        self.addCleanup(handle.close)
        errors = job_purge.remove_job_files(self.root, [stuck, other])
        self.assertEqual(len(errors), 1)
        self.assertIn("alpha-1234abcd", errors[0])
        self.assertTrue(locked.exists())
        self.assertFalse(other.exists())

    def test_paths_outside_reports_jobs_and_logs_are_refused(self):
        job = self.make_job("alpha")
        for path in (self.root / "output", self.root / "input" / "alpha.mp4", self.root / "reports" / "x",
                     self.root / "reports" / "jobs", self.root / "logs" / "control-center"):
            with self.subTest(path=path):
                with self.assertRaises(job_purge.DeleteRefused):
                    job_purge.remove_job_files(self.root, [path])
        self.assertTrue(Path(job["source_path"]).is_file())

    def test_a_benchmark_run_is_refused_even_when_listed(self):
        bench = self.root / "reports" / "jobs" / "alpha-1234abcd-run-bench"
        bench.mkdir()
        (bench / job_purge.BENCHMARK_MARKER).write_text("", encoding="utf-8")
        with self.assertRaises(job_purge.DeleteRefused):
            job_purge.remove_job_files(self.root, [bench])
        self.assertTrue((bench / job_purge.BENCHMARK_MARKER).is_file())

    def test_a_root_outside_the_install_temp_is_refused(self):
        folder = self.root / "reports" / "jobs" / "alpha"
        folder.mkdir()
        elsewhere = self.root / "another-install"
        elsewhere.mkdir()
        with patch.object(job_purge, "INSTALL_ROOT", elsewhere):
            self.assertFalse(job_purge.allowed_project_root(self.root))
            with self.assertRaises(job_purge.DeleteRefused):
                job_purge.remove_job_files(self.root, [folder])
        self.assertTrue(folder.is_dir())

    def test_the_allowed_roots(self):
        self.assertTrue(job_purge.allowed_project_root(recycle_bin.INSTALL_ROOT))
        self.assertTrue(job_purge.allowed_project_root(self.root))
        self.assertFalse(job_purge.allowed_project_root(recycle_bin.INSTALL_ROOT / "temp"))
        self.assertFalse(job_purge.allowed_project_root(recycle_bin.INSTALL_ROOT / "input"))
        self.assertFalse(job_purge.allowed_project_root(recycle_bin.INSTALL_ROOT.parent))


class RemoveJobTests(PurgeFixture):
    def test_files_first_then_every_row(self):
        alpha, beta = self.make_job("alpha", key="alpha-1234abcd"), self.make_job("beta", key="beta-5678ef01")
        own = self.make_report(alpha, "alpha-1234abcd")
        kept = self.make_report(beta, "beta-5678ef01")
        log = self.root / "logs" / "control-center" / f"job-{alpha['id']}-text-attempt-1.log"
        log.write_text("log", encoding="utf-8")
        self.populate(alpha)
        planned = job_purge.job_files(self.root, self.store, alpha)
        self.assertEqual(planned, [own, log])

        self.assertEqual(job_purge.remove_job(self.root, self.store, alpha), [])

        self.assertFalse(own.exists())
        self.assertFalse(log.exists())
        self.assertTrue(kept.is_dir())
        self.assertEqual(set(self.counts(alpha["id"]).values()), {0})
        self.assertEqual(self.counts(beta["id"])["jobs"], 1)

    def test_an_archive_or_a_pending_legacy_cleanup_locks_the_job(self):
        for kind in ("PENDING", "ARCHIVED", "RESTORING", "CLEANUP_PENDING"):
            with self.subTest(kind=kind):
                key = f"locked-{kind.lower().replace('_', '-')}-1234abcd"
                job = self.make_job(key.rsplit("-", 1)[0], key=key)
                own = self.make_report(job, key)
                if kind == "CLEANUP_PENDING":
                    self.store.add_source_cleanup(
                        job_id=job["id"], kind="EXPORTED", source_path=job["source_path"],
                        source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"], mtime_ns=1,
                    )
                else:
                    row = self.store.add_source_archive(
                        job_id=job["id"], kind="EXPORTED", source_path=job["source_path"],
                        archive_path=f"archive/sources/{key}/a.mp4",
                        manifest_path=f"archive/sources/{key}/archive-manifest.json",
                        source_sha256=job["source_sha256"], size_bytes=job["source_size_bytes"], mtime_ns=1,
                        queue_path=f"reports/jobs/{key}/review-queue.json",
                    )
                    if kind != "PENDING":
                        self.store.finish_source_archive(row, state="ARCHIVED")
                    if kind == "RESTORING":
                        self.store.begin_archive_restore(row)
                with self.assertRaises(job_purge.DeleteRefused) as caught:
                    job_purge.remove_job(self.root, self.store, job)
                self.assertEqual(str(caught.exception), job_purge.LOCKED_MESSAGE)
                self.assertTrue(own.is_dir())
                self.assertEqual(self.counts(job["id"])["jobs"], 1)

    @unittest.skipUnless(sys.platform == "win32", "open files block a delete only on Windows")
    def test_a_file_that_stays_keeps_every_row(self):
        alpha = self.make_job("alpha", key="alpha-1234abcd")
        own = self.make_report(alpha, "alpha-1234abcd")
        handle = open(own / "frames" / "a.jpg", "rb")
        self.addCleanup(handle.close)
        errors = job_purge.remove_job(self.root, self.store, alpha)
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.store.get_job(alpha["id"])["id"], alpha["id"])
        handle.close()
        self.assertEqual(job_purge.remove_job(self.root, self.store, alpha), [], "a second try finishes")
        with self.assertRaises(KeyError):
            self.store.get_job(alpha["id"])


class DeleteInputFileTests(PurgeFixture):
    def test_deletes_a_file_in_input(self):
        job = self.make_job("alpha")
        path = Path(job["source_path"])
        job_purge.delete_input_file(path, allowed_root=self.root / "input", expected_size=path.stat().st_size)
        self.assertFalse(path.exists())

    def test_refusals_leave_the_file(self):
        job = self.make_job("alpha")
        path = Path(job["source_path"])
        outside = self.root / "output" / "alpha.mp4"
        outside.write_bytes(b"x" * 10)
        cases = (
            (outside, self.root / "input", 10),
            (path, self.root / "input", path.stat().st_size + 1),
            (path, self.root / "output", path.stat().st_size),
            (self.root / "input" / "missing.mp4", self.root / "input", 1),
        )
        for target, allowed, size in cases:
            with self.subTest(target=target.name, allowed=allowed.name, size=size):
                with self.assertRaises(job_purge.DeleteRefused):
                    job_purge.delete_input_file(target, allowed_root=allowed, expected_size=size)
        self.assertTrue(path.is_file())
        self.assertTrue(outside.is_file())

    def test_an_input_folder_outside_the_install_temp_is_refused(self):
        job = self.make_job("alpha")
        path = Path(job["source_path"])
        elsewhere = self.root / "another-install"
        elsewhere.mkdir()
        with patch.object(job_purge, "INSTALL_ROOT", elsewhere):
            with self.assertRaises(job_purge.DeleteRefused):
                job_purge.delete_input_file(path, allowed_root=self.root / "input", expected_size=path.stat().st_size)
            with self.assertRaises(job_purge.DeleteRefused):
                job_purge.delete_export_manifest(self.root, "output/alpha-reviewed.mp4")
        self.assertTrue(path.is_file())

    @unittest.skipUnless(sys.platform == "win32", "open files block a delete only on Windows")
    def test_a_file_in_use_is_a_failure_and_stays(self):
        job = self.make_job("alpha")
        path = Path(job["source_path"])
        with open(path, "rb"):
            with self.assertRaises(job_purge.DeleteFailed) as caught:
                job_purge.delete_input_file(path, allowed_root=self.root / "input", expected_size=path.stat().st_size)
        self.assertEqual(str(caught.exception), job_purge.IN_USE_MESSAGE)
        self.assertTrue(path.is_file())

    def test_a_read_only_file_is_a_failure_and_stays(self):
        job = self.make_job("alpha")
        path = Path(job["source_path"])
        os.chmod(path, stat.S_IREAD)
        self.addCleanup(os.chmod, path, stat.S_IREAD | stat.S_IWRITE)
        with self.assertRaises(job_purge.DeleteFailed):
            job_purge.delete_input_file(path, allowed_root=self.root / "input", expected_size=path.stat().st_size)
        self.assertTrue(path.is_file())


@unittest.skipUnless(sys.platform == "win32", "directory junctions are a Windows feature")
class LinkedFolderTests(PurgeFixture):
    """A folder of the install that is a junction: nothing is deleted through it."""

    def junction(self, name):
        """Replace ``root/name`` with a junction to a folder beside it; return that folder."""
        import _winapi

        target = self.root / ("elsewhere-" + name.replace("/", "-"))
        target.mkdir()
        link = self.root / name
        shutil.rmtree(link)
        _winapi.CreateJunction(str(target), str(link))
        return target

    def test_a_junctioned_input_is_refused_and_its_file_stays(self):
        target = self.junction("input")
        video = target / "alpha.mp4"
        video.write_bytes(b"x" * 10)
        for path in (self.root / "input" / "alpha.mp4", video):
            with self.subTest(path=path), self.assertRaises(job_purge.DeleteRefused) as caught:
                job_purge.delete_input_file(path, allowed_root=self.root / "input", expected_size=10)
            self.assertEqual(str(caught.exception), job_purge.FOLDER_LINK_MESSAGE.format(name="input"))
        self.assertTrue(video.is_file())

    def test_a_junctioned_output_keeps_every_manifest(self):
        target = self.junction("output")
        manifest = target / "alpha-reviewed.mp4.manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        self.assertEqual(job_purge.delete_export_manifest(self.root, "output/alpha-reviewed.mp4"),
                         job_purge.FOLDER_LINK_MESSAGE.format(name="output"))
        self.assertTrue(manifest.is_file())

    def test_junctioned_report_or_log_folders_give_nothing_to_delete(self):
        job = self.make_job("alpha", key="alpha-1234abcd")
        logs = self.junction("logs/control-center")
        log = logs / f"job-{job['id']}-text-attempt-1.log"
        log.write_text("log", encoding="utf-8")
        self.assertEqual(job_purge.job_log_files(self.root, job["id"]), [])
        with self.assertRaises(job_purge.DeleteRefused):
            job_purge.remove_job_files(self.root, [self.root / "logs" / "control-center" / log.name])
        reports = self.junction("reports")
        folder = reports / "jobs" / "alpha-1234abcd"
        folder.mkdir(parents=True)
        self.assertEqual(job_purge.owned_report_dirs(self.root, self.store, job), [])
        with self.assertRaises(job_purge.DeleteRefused):
            job_purge.remove_job_files(self.root, [self.root / "reports" / "jobs" / "alpha-1234abcd"])
        self.assertEqual(job_purge.remove_job(self.root, self.store, job), [])  # the rows only
        self.assertTrue(log.is_file())
        self.assertTrue(folder.is_dir())

    def test_a_link_inside_an_owned_folder_is_removed_without_following_it(self):
        import _winapi

        job = self.make_job("alpha", key="alpha-1234abcd")
        own = self.make_report(job, "alpha-1234abcd")
        target = self.root / "elsewhere"
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        _winapi.CreateJunction(str(target), str(own / "frames" / "linked"))
        self.assertEqual(job_purge.remove_job(self.root, self.store, job), [])
        self.assertFalse(own.exists())
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep")
        # A link given as the folder itself is refused, never followed.
        link = self.root / "reports" / "jobs" / "beta-5678ef01"
        _winapi.CreateJunction(str(target), str(link))
        self.assertEqual(job_purge.remove_job_files(self.root, [link]),
                         [f"reports/jobs/beta-5678ef01: {job_purge.LINK_MESSAGE}"])
        self.assertTrue((target / "keep.txt").is_file())


class DeleteExportManifestTests(PurgeFixture):
    def test_deletes_only_the_manifest_of_an_export_in_output(self):
        export = self.root / "output" / "alpha-1234abcd-0011-reviewed.mp4"
        export.write_bytes(b"video")
        manifest = export.with_name(export.name + ".manifest.json")
        manifest.write_text("{}", encoding="utf-8")
        self.assertIsNone(job_purge.delete_export_manifest(self.root, "output/" + export.name))
        self.assertFalse(manifest.exists())
        self.assertTrue(export.is_file())

    def test_a_missing_manifest_is_not_an_error(self):
        self.assertIsNone(job_purge.delete_export_manifest(self.root, "output/alpha-reviewed.mp4"))

    def test_other_names_and_folders_are_refused(self):
        sub = self.root / "output" / "sub"
        sub.mkdir()
        nested = sub / "alpha-reviewed.mp4.manifest.json"
        nested.write_text("{}", encoding="utf-8")
        plain = self.root / "output" / "alpha.mp4.manifest.json"
        plain.write_text("{}", encoding="utf-8")
        for relative in ("output/sub/alpha-reviewed.mp4", "output/alpha.mp4", "input/alpha-reviewed.mp4", ""):
            with self.subTest(relative=relative):
                self.assertIsNotNone(job_purge.delete_export_manifest(self.root, relative))
        self.assertTrue(nested.is_file())
        self.assertTrue(plain.is_file())


class ModuleContractTests(unittest.TestCase):
    def test_import_never_loads_the_control_center_or_the_scheduler(self):
        import subprocess

        code = (
            "import sys\n"
            "import biliflow.job_purge\n"
            "print('biliflow.control_center' in sys.modules, 'biliflow.scheduler' in sys.modules,"
            " 'biliflow.source_cleanup' in sys.modules)\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True)
        self.assertEqual(result.stdout.split(), ["False", "False", "False"])

    def test_job_setting_names_match_every_per_job_setting_in_the_code(self):
        import re

        source = recycle_bin.INSTALL_ROOT / "src" / "biliflow"
        found = set()
        for path in source.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            found.update(re.findall(r"setting\(f[\"']([a-z_]+):\{", text))
            found.update(re.findall(r"f[\"']([a-z_]+):\{job_id\}", text))
        self.assertEqual(found, set(JOB_SETTING_NAMES))


if __name__ == "__main__":
    unittest.main()
