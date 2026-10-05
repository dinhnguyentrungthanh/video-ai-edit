"""Failure paths of the download worker found in the D1 review (video download plan, D1).

A failed database write, a task restarted while its last thread still ends, a temp
file another program holds, a file the indexer holds while it moves into input,
two downloads sharing the free space and a source removed after the link was queued.
Temporary roots and the fake yt-dlp only.
"""
import json
import os
import shutil
import time
import unittest
from unittest import mock

from biliflow import download_worker
from biliflow.download_runner import ProcessControl
from biliflow.download_worker import DownloadActionError
from tests.test_download_worker import CLIP, GB, WorkerCase, video, wait_for

OTHER = "https://clips.example/v/2"
PUBLIC_IP = "93.184.215.14"


class _Outcome:
    ok = True
    code = message = None
    returncode = 0

    def __init__(self, final_path):
        self.final_path = final_path


class _Entries(list):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SettleTests(WorkerCase):
    def test_a_failed_database_write_frees_the_slot_and_the_task_is_settled_later(self):
        def broken_probe(*args, **kwargs):
            raise RuntimeError("probe crashed")

        def broken_write(*args, **kwargs):
            raise RuntimeError("database or disk is full")
        task, = self.add()
        with mock.patch.object(self.worker.runner, "probe", broken_probe), \
                mock.patch.object(self.worker, "_fail", broken_write), \
                mock.patch.object(self.worker, "_settle", broken_write):
            self.worker.dispatch()
            self.assertTrue(self.worker.wait_idle(10))
        self.assertEqual(self.worker.running_ids(), set())
        self.assertIn("database or disk is full", self.worker.last_error)
        self.assertEqual(self.state(task["id"]), "PROBING")
        self.worker.dispatch()  # the next pass settles the orphaned running state
        self.assertEqual(self.state(task["id"]), "INTERRUPTED")

    def test_a_task_whose_last_thread_still_ends_is_not_started_twice(self):
        self.scenario(probe={"json": video()})
        task, = self.add()
        self.worker._controls[task["id"]] = ProcessControl()  # its previous thread has not left yet
        self.assertEqual(self.worker.dispatch(), [])
        self.assertEqual(self.state(task["id"]), "QUEUED")
        del self.worker._controls[task["id"]]
        self.assertEqual(self.worker.dispatch(), [task["id"]])
        self.assertTrue(self.worker.wait_idle(20))
        self.assertEqual(self.state(task["id"]), "COMPLETED")

    def test_after_shutdown_dispatch_starts_nothing(self):
        self.add()
        self.worker.shutdown(5)
        self.assertEqual(self.worker.dispatch(), [])


class HeldFileTests(WorkerCase):
    def held_temp(self, task_id):
        folder = self.root / "temp" / "downloads" / str(task_id)
        folder.mkdir(parents=True)
        handle = (folder / "abc.mp4.part").open("wb")  # Windows refuses to delete an open file
        self.addCleanup(handle.close)
        return folder, handle

    def test_a_cancel_waits_until_the_temp_folder_is_really_gone(self):
        self.worker.cancel_retry_seconds = 0.0
        task, = self.add()
        self.worker.stop(task["id"])
        folder, handle = self.held_temp(task["id"])
        cancelled = self.worker.cancel(task["id"])
        self.assertEqual(cancelled["state"], "CANCELLING")
        self.assertIn("chưa xóa được file tạm", cancelled["error_message"])
        self.worker.dispatch()
        self.assertEqual(self.state(task["id"]), "CANCELLING")
        handle.close()
        self.worker.dispatch()
        self.assertEqual(self.state(task["id"]), "CANCELLED")
        self.assertFalse(folder.exists())

    def test_retry_and_cleanup_refuse_while_old_part_files_cannot_be_deleted(self):
        task, = self.add()
        self.worker.stop(task["id"])
        _, handle = self.held_temp(task["id"])
        with self.assertRaises(DownloadActionError):
            self.worker.retry(task["id"])
        self.assertEqual(self.store.get(task["id"])["attempt"], 1)
        self.assertEqual(self.worker.cleanup_temp()["tasks"], 0)
        self.assertEqual(self.state(task["id"]), "STOPPED")
        handle.close()
        self.assertEqual(self.worker.retry(task["id"])["attempt"], 2)


class PublishTests(WorkerCase):
    def setUp(self):
        super().setUp()
        self.worker.publish_wait_seconds = 0.01
        self.real_rename = os.rename

    def rename_failing(self, times):
        calls = []

        def rename(source, target):
            calls.append(target)
            if len(calls) <= times:
                raise PermissionError(32, "The process cannot access the file")
            return self.real_rename(source, target)
        return mock.patch.object(download_worker.os, "rename", rename), calls

    def test_a_briefly_held_file_still_moves_into_input(self):
        self.scenario(probe={"json": video("Phim")}, download={"id": "abc"})
        task, = self.add()
        patch, calls = self.rename_failing(2)
        with patch:
            self.run_all()
        self.assertEqual(self.state(task["id"]), "COMPLETED")
        self.assertEqual(len(calls), 3)
        self.assertTrue((self.root / "input" / "Phim.mp4").is_file())

    def test_a_file_held_too_long_keeps_the_verified_temp_file(self):
        self.scenario(probe={"json": video("Phim")}, download={"id": "abc"})
        task, = self.add()
        patch, _ = self.rename_failing(100)
        with patch:
            self.run_all()
        held = self.store.get(task["id"])
        self.assertEqual((held["state"], held["error_code"], held["output_path"]),
                         ("INTERRUPTED", "PUBLISH_BLOCKED", None))
        self.assertTrue((self.root / "temp" / "downloads" / str(task["id"]) / "abc.mp4").is_file())
        self.assertEqual(list((self.root / "input").iterdir()), [])

    def test_another_container_keeps_its_extension(self):
        self.scenario(probe={"json": video("Phim")})
        task, = self.add()

        def mkv_download(url, task_dir, control, **kwargs):
            final = task_dir / "abc.mkv"
            final.write_bytes(b"fake video")
            return _Outcome(final)
        with mock.patch.object(self.worker.runner, "download", mkv_download):
            self.run_all()
        self.assertEqual(self.state(task["id"]), "COMPLETED")
        self.assertTrue((self.root / "input" / "Phim.mkv").is_file())


class SharedSpaceTests(WorkerCase):
    def test_a_second_download_counts_the_space_the_first_one_still_needs(self):
        # Room for one 10 GB video (22 GB with the merge) above the 100 GB reserve, not two.
        self.space = [130 * GB, 100 * GB]
        pid_file = self.root / "first.pid"
        self.scenario(probe={"json": video(size=10 * GB)},
                      by_url={CLIP: {"download": {"hang": True, "pid_file": str(pid_file)}},
                              OTHER: {"download": {"id": "second"}}})
        first, = self.add(CLIP)
        self.worker.dispatch()
        self.assertTrue(wait_for(lambda: pid_file.exists() and self.state(first["id"]) == "DOWNLOADING"))
        second, = self.add(OTHER)
        self.worker.dispatch()
        self.assertTrue(wait_for(lambda: self.state(second["id"]) == "WAITING_SPACE"))
        self.assertIn("dành cho lượt đang tải", self.store.get(second["id"])["error_message"])
        self.worker.cancel(first["id"])
        self.assertTrue(self.worker.wait_idle(20))
        self.assertEqual(self.state(second["id"]), "COMPLETED")


class AllowlistRecheckTests(WorkerCase):
    def test_a_source_removed_after_queueing_is_never_contacted(self):
        task, = self.add()
        (self.root / "config" / "download_sources.json").write_text(json.dumps(
            {"version": 1, "sources": [{"id": "movies", "label": "Movies", "domains": ["movies.example"]}]}),
            encoding="utf-8")
        self.run_all()
        failed = self.store.get(task["id"])
        self.assertEqual((failed["state"], failed["error_code"]), ("FAILED", "SOURCE_REMOVED"))
        self.assertEqual(self.calls(), [])

    def test_a_host_that_now_resolves_inside_the_network_is_not_downloaded(self):
        answers = []

        def resolver(host, port):
            answers.append(host)  # add, probe check, download check
            return [PUBLIC_IP] if len(answers) < 3 else ["192.168.1.20"]
        self.worker.resolver = resolver
        self.scenario(probe={"json": video()})
        task, = self.add()
        self.run_all()
        failed = self.store.get(task["id"])
        self.assertEqual((failed["state"], failed["error_code"]), ("FAILED", "PRIVATE_ADDRESS"))
        self.assertEqual((len(self.calls("probe")), self.calls("download")), (1, []))


class SweepAndSummaryTests(WorkerCase):
    def test_a_failing_sweep_still_waits_for_the_next_hour(self):
        def broken(root):
            raise OSError("cache locked")
        self.worker.cache_pruner = broken
        with self.assertRaises(OSError):
            self.worker.sweep()
        self.assertIsNotNone(self.worker._last_sweep)

    def test_vanishing_part_folders_do_not_break_the_temp_summary(self):
        task = self.root / "temp" / "downloads" / "7"
        (task / "frag").mkdir(parents=True)
        (task / "frag" / "a.part").write_bytes(b"x" * 10)
        (task / "b.part").write_bytes(b"x" * 5)
        real_scandir = os.scandir

        def scandir(path):
            entries = list(real_scandir(path))
            if os.path.normcase(str(path)) == os.path.normcase(str(task)):
                shutil.rmtree(task / "frag")  # yt-dlp removed its fragment folder meanwhile
            return _Entries(entries)
        with mock.patch("biliflow.download_files.os.scandir", scandir):
            self.assertEqual(self.worker.temp_summary()["total_temp_bytes"], 5)


class StartAndReconcileTests(WorkerCase):
    """D2 review: a failing start-up step or one stuck task never stops the queue."""

    def test_the_loop_starts_even_when_recovery_and_the_sweep_fail(self):
        def broken_sweep(now=None):
            self.worker._last_sweep = time.monotonic()  # like sweep(): the next try is an hour later
            raise PermissionError(5, "Access is denied")
        self.scenario(probe={"json": video()})
        task, = self.add()
        self.worker.sweep = broken_sweep
        self.worker.poll_seconds = 0.05
        with mock.patch.object(self.worker, "recover", mock.Mock(side_effect=RuntimeError("bad state row"))):
            self.worker.start()
        self.assertTrue(wait_for(lambda: self.state(task["id"]) == "COMPLETED"))
        # The passes after the failing sweep succeed; they keep the latest error (and its time).
        self.assertTrue(wait_for(lambda: "Access is denied" in (self.worker.last_error or "")))
        time.sleep(0.3)
        self.assertIn("Access is denied", self.worker.last_error)
        self.assertTrue(self.worker.last_error_at)

    def test_one_task_that_cannot_be_settled_does_not_block_the_others(self):
        stuck, queued = self.add(CLIP, OTHER)
        temp = self.root / "temp" / "downloads" / str(stuck["id"]) / "abc.mp4"
        temp.parent.mkdir(parents=True)
        temp.write_bytes(b"y" * 10)
        self.store.transition(stuck["id"], {"QUEUED"}, "PUBLISHING", output_path=str(self.root / "input" / "x.mp4"),
                              output_size=10, output_sha256="0" * 64, temp_file=str(temp))
        (self.root / "input" / "x.mp4").write_bytes(b"x" * 10)
        self.scenario(probe={"json": video()}, download={"id": "other"})

        def held(path):
            raise PermissionError(32, "The process cannot access the file")
        with mock.patch("biliflow.download_upkeep.sha256_file", held):
            self.assertEqual(self.worker.recover(), {})  # logged, not raised
            self.assertIn("Lượt " + str(stuck["id"]), self.worker.last_error)
            self.worker.dispatch()
            self.assertTrue(self.worker.wait_idle(20))
        self.assertEqual(self.state(queued["id"]), "COMPLETED")
        self.assertEqual(self.state(stuck["id"]), "PUBLISHING")
        self.worker.dispatch()  # the file is readable again: settled from the files
        self.assertEqual(self.state(stuck["id"]), "INTERRUPTED")

    def test_remove_keeps_the_row_while_its_temp_folder_is_held(self):
        task, = self.add()
        self.worker.stop(task["id"])
        folder = self.root / "temp" / "downloads" / str(task["id"])
        folder.mkdir(parents=True)
        handle = (folder / "a.part").open("wb")
        self.addCleanup(handle.close)
        with self.assertRaises(DownloadActionError):
            self.worker.remove(task["id"])
        self.assertIsNotNone(self.store.get(task["id"]))
        handle.close()
        self.assertTrue(self.worker.remove(task["id"])["removed"])
        self.assertIsNone(self.store.get(task["id"]))


if __name__ == "__main__":
    unittest.main()
