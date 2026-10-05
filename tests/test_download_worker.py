import hashlib
import json
import os
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import psutil

from biliflow.download_files import VerifyResult
from biliflow.download_runner import YtDlpRunner
from biliflow.download_sources import DownloadBatchError
from biliflow.download_store import DownloadStore
from biliflow.download_worker import DownloadActionError, DownloadWorker

FAKE = Path(__file__).resolve().with_name("fake_yt_dlp.py")
ROOT = Path(__file__).resolve().parents[1]
FFMPEG = Path(os.environ.get("BILIFLOW_FFMPEG") or ROOT / "tools/ffmpeg/bin/ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe.exe")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CLIP = "https://clips.example/v/1"
GB = 1024**3


def video(title="Clip", duration=12.0, video_id="abc", size=1_000_000, **extra):
    formats = [{"vcodec": "avc1", "acodec": "none", "filesize": size}] if size else [{}]
    return {"id": video_id, "title": title, "duration": duration, "live_status": "not_live",
            "requested_formats": formats, "extractor_key": "Generic", **extra}


def page(*entries):
    return {"_type": "playlist", "title": "Page", "extractor_key": "Generic",
            "entries": [dict(entry, playlist_index=index) for index, entry in enumerate(entries, start=1)]}


def ok_verifier(ffprobe, ffmpeg, path, *, expected_duration=None):
    return VerifyResult(True, "OK", "ok", expected_duration, "h264", "aac", 320, 240)


def wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class FakeClock:
    def __init__(self, now=None):
        self.now = now or datetime.now(timezone.utc)

    def __call__(self):
        return self.now


class WorkerCase(unittest.TestCase):
    verifier = staticmethod(ok_verifier)

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "config").mkdir()
        (self.root / "input").mkdir()
        sources = [
            {"id": "clips", "label": "Clips", "domains": ["clips.example"], "min_duration_seconds": 0,
             "allow_multi_entry": False},
            {"id": "movies", "label": "Movies", "domains": ["movies.example"], "min_duration_seconds": 600,
             "allow_multi_entry": True},
        ]
        (self.root / "config" / "download_sources.json").write_text(
            json.dumps({"version": 1, "sources": sources}), encoding="utf-8")
        self.scenario_path = self.root / "scenario.json"
        self.log_path = self.root / "calls.jsonl"
        self.space = [10_000 * GB, 100 * GB]
        self.clock = FakeClock()
        self.store = DownloadStore(self.root / "state" / "downloads.sqlite3", clock=self.clock)
        self.pruned = []
        self.worker = self.make_worker()

    def make_worker(self, verifier=None):
        runner = YtDlpRunner(self.root, command_prefix=[sys.executable, str(FAKE)], deno_path=None,
                             env_extra={"FAKE_YTDLP_SCENARIO": str(self.scenario_path),
                                        "FAKE_YTDLP_LOG": str(self.log_path)})
        return DownloadWorker(self.root, self.store, runner=runner, verifier=verifier or self.verifier,
                              ffmpeg=FFMPEG, ffprobe=FFPROBE, space_probe=lambda root: tuple(self.space),
                              resolver=lambda host, port: ["93.184.215.14"], space_retry_seconds=0.05,
                              cache_pruner=lambda root: self.pruned.append(root) or {"removed_files": 0})

    def tearDown(self):
        for task in self.store.list_tasks():
            try:
                self.worker.cancel(task["id"])
            except DownloadActionError:
                pass
        self.worker.shutdown(10)
        self.store.close()
        self.directory.cleanup()

    def scenario(self, **payload):
        self.scenario_path.write_text(json.dumps(payload), encoding="utf-8")

    def calls(self, kind=None):
        if not self.log_path.exists():
            return []
        calls = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]
        if kind == "probe":
            return [call for call in calls if "--dump-single-json" in call["argv"]]
        if kind == "download":
            return [call for call in calls if "--dump-single-json" not in call["argv"]]
        return calls

    def add(self, *urls, source="clips"):
        return self.worker.add(source, list(urls or [CLIP]), rights_confirmed=True)

    def run_all(self, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.worker.dispatch()
            self.assertTrue(self.worker.wait_idle(timeout))
            if not self.store.next_queued():
                return
        self.fail("tasks did not settle")

    def state(self, task_id):
        return self.store.get(task_id)["state"]


class HappyPathTests(WorkerCase):
    def test_a_download_lands_in_input_under_its_title(self):
        self.scenario(probe={"json": video("Tập 1: Mở đầu")}, download={"id": "abc"})
        task, = self.add()
        self.run_all()
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", done)
        target = self.root / "input" / "Tập 1 Mở đầu.mp4"
        self.assertEqual(Path(done["output_path"]), target)
        self.assertEqual(target.read_bytes(), b"fake video")
        self.assertEqual(done["output_sha256"], hashlib.sha256(b"fake video").hexdigest())
        self.assertTrue(done["name_locked"])
        self.assertFalse((self.root / "temp" / "downloads" / str(task["id"])).exists())
        download, = self.calls("download")
        self.assertIn("--no-playlist", download["argv"])
        self.assertNotIn("--playlist-items", download["argv"])
        kinds = [event["kind"] for event in self.store.events(task["id"])]
        self.assertEqual(kinds[0], "QUEUED")
        self.assertEqual(kinds[-1], "COMPLETED")

    def test_rights_source_and_batch_are_checked(self):
        with self.assertRaises(DownloadActionError) as caught:
            self.worker.add("clips", [CLIP], rights_confirmed=False)
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(DownloadActionError):
            self.worker.add("other", [CLIP], rights_confirmed=True)
        with self.assertRaises(DownloadBatchError):
            self.add("https://movies.example/a")
        self.add()
        with self.assertRaises(DownloadBatchError):
            self.add("https://clips.example/v/2", CLIP)
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_rename_applies_until_the_file_moves_then_locks(self):
        self.scenario(probe={"json": video("Gốc")})
        task, = self.add()
        self.assertEqual(self.worker.rename(task["id"], 'Phim/hay?.mp4')["desired_name"], "Phim hay")
        for bad in ("   ", '<>"|'):
            with self.assertRaises(DownloadActionError) as caught:
                self.worker.rename(task["id"], bad)
            self.assertEqual(caught.exception.status, 400)
        self.run_all()
        self.assertTrue((self.root / "input" / "Phim hay.mp4").is_file())
        with self.assertRaises(DownloadActionError) as caught:
            self.worker.rename(task["id"], "Khác")
        self.assertEqual(caught.exception.status, 409)

    def test_an_existing_file_is_never_overwritten(self):
        existing = self.root / "input" / "Clip.mp4"
        existing.write_bytes(b"user file")
        self.scenario(probe={"json": video("Clip")})
        task, = self.add()
        self.run_all()
        self.assertEqual(existing.read_bytes(), b"user file")
        self.assertEqual(Path(self.store.get(task["id"])["output_path"]).name, "Clip (2).mp4")

    def test_remove_drops_the_row_but_never_the_file_in_input(self):
        self.scenario(probe={"json": video("Giữ")})
        task, = self.add()
        self.run_all()
        self.assertEqual(self.worker.remove(task["id"])["removed"], True)
        self.assertIsNone(self.store.get(task["id"]))
        self.assertTrue((self.root / "input" / "Giữ.mp4").is_file())
        self.assertTrue(self.worker.remove(task["id"])["removed"])
        queued, = self.add("https://clips.example/v/9")
        with self.assertRaises(DownloadActionError):
            self.worker.remove(queued["id"])


class ProbeChoiceTests(WorkerCase):
    def test_a_movie_page_downloads_the_film_and_skips_the_ads(self):
        url = "https://movies.example/phim/1"
        self.scenario(probe={"json": page(video("Ad 1", 30, "a1"), video("Ad 2", 15, "a2"),
                                          video("Ad 3", 65, "a3"), video("Film", 5400, "f"))})
        task, = self.add(url, source="movies")
        self.run_all()
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED")
        self.assertEqual(done["original_title"], "Film")
        argv = self.calls("download")[0]["argv"]
        self.assertEqual(argv[argv.index("--playlist-items") + 1], "4")
        self.assertEqual(len(done["entries"]), 4)

    def test_close_lengths_wait_for_a_choice_and_free_the_slot(self):
        self.scenario(probe={"json": page(video("Part 1", 2700, "p1"), video("Part 2", 3000, "p2"))})
        task, = self.add("https://movies.example/phim/2", source="movies")
        self.run_all()
        waiting = self.store.get(task["id"])
        self.assertEqual(waiting["state"], "NEEDS_CHOICE")
        self.assertEqual([entry["title"] for entry in waiting["entries"]], ["Part 1", "Part 2"])
        with self.assertRaises(DownloadActionError):
            self.worker.choose(task["id"], 99)
        self.assertEqual(self.worker.choose(task["id"], 2)["state"], "QUEUED")
        self.run_all()
        done = self.store.get(task["id"])
        self.assertEqual((done["state"], done["original_title"]), ("COMPLETED", "Part 2"))
        self.assertEqual(len(self.calls("probe")), 1)
        argv = self.calls("download")[0]["argv"]
        self.assertEqual(argv[argv.index("--playlist-items") + 1], "2")

    def test_only_ads_fail_with_the_reason(self):
        self.scenario(probe={"json": page(video("Ad 1", 30), video("Ad 2", 15), video("Ad 3", 65))})
        task, = self.add("https://movies.example/phim/3", source="movies")
        self.run_all()
        failed = self.store.get(task["id"])
        self.assertEqual((failed["state"], failed["error_code"]), ("FAILED", "ONLY_SHORT_ENTRIES"))
        self.assertIn("3 video ngắn", failed["error_message"])
        self.assertEqual(self.calls("download"), [])

    def test_live_drm_and_login_fail_with_their_codes(self):
        urls = {"https://clips.example/live": ({"json": video(live_status="is_live")}, "LIVE"),
                "https://clips.example/drm": ({"exit": 1, "stderr": "ERROR: This video is DRM protected"}, "DRM"),
                "https://clips.example/private": ({"exit": 1, "stderr": "ERROR: This video is private"},
                                                  "LOGIN_REQUIRED")}
        self.scenario(by_url={url: {"probe": probe} for url, (probe, _) in urls.items()})
        tasks = self.add(*urls)
        self.run_all()
        for task, (_, code) in zip(tasks, urls.values()):
            with self.subTest(code=code):
                self.assertEqual(self.store.get(task["id"])["error_code"], code)


class SpaceTests(WorkerCase):
    def test_waits_for_space_then_downloads(self):
        self.space = [150 * GB, 100 * GB]
        self.scenario(probe={"json": video(size=40 * GB)})
        task, = self.add()
        self.worker.dispatch()
        self.assertTrue(wait_for(lambda: self.state(task["id"]) == "WAITING_SPACE"))
        self.assertIn("Chờ chỗ trống", self.store.get(task["id"])["error_message"])
        self.assertEqual(self.calls("download"), [])
        self.space = [300 * GB, 100 * GB]
        self.assertTrue(self.worker.wait_idle(20))
        self.assertEqual(self.state(task["id"]), "COMPLETED")

    def test_unknown_size_caps_the_file_by_the_free_space(self):
        self.space = [122 * GB, 100 * GB]
        self.scenario(probe={"json": video(size=None)})
        self.add()
        self.run_all()
        argv = self.calls("download")[0]["argv"]
        self.assertEqual(int(argv[argv.index("--max-filesize") + 1]), int(22 * GB / 2.2))


class ControlTests(WorkerCase):
    def start_hanging(self, **extra):
        pid_file = self.root / "fake.pid"
        self.scenario(probe={"json": video()},
                      download={"id": "abc", "hang": True, "pid_file": str(pid_file),
                                "progress": [[16, 100, None, 1.0, 5]], **extra})
        task, = self.add()
        self.worker.dispatch()
        self.assertTrue(wait_for(lambda: pid_file.exists() and self.state(task["id"]) == "DOWNLOADING"))
        self.assertTrue(wait_for(lambda: (self.root / "temp" / "downloads" / str(task["id"]) /
                                          "abc.mp4.part").exists()))
        return task, int(pid_file.read_text())

    def test_stop_keeps_the_partial_file_and_resume_continues_it(self):
        task, pid = self.start_hanging()
        stopped = self.worker.stop(task["id"])
        self.assertEqual(stopped["state"], "STOPPED")
        self.assertEqual(self.worker.stop(task["id"])["state"], "STOPPED")
        self.assertTrue(wait_for(lambda: not psutil.pid_exists(pid)))
        self.assertTrue((self.root / "temp" / "downloads" / str(task["id"]) / "abc.mp4.part").exists())
        self.scenario(probe={"json": video()}, download={"id": "abc", "progress": [[32, 100, None, 1.0, 1]]})
        self.assertEqual(self.worker.resume(task["id"])["state"], "QUEUED")
        self.run_all()
        self.assertEqual(self.state(task["id"]), "COMPLETED")
        self.assertEqual(len(self.calls("probe")), 1)
        self.assertTrue(any("resumed=True" in line for line in self.store.log_lines(task["id"])))

    def test_cancel_kills_the_process_and_removes_the_temp_folder(self):
        task, pid = self.start_hanging()
        self.assertEqual(self.worker.cancel(task["id"])["state"], "CANCELLED")
        self.assertTrue(wait_for(lambda: not psutil.pid_exists(pid)))
        self.assertFalse((self.root / "temp" / "downloads" / str(task["id"])).exists())
        self.assertEqual(self.worker.cancel(task["id"])["state"], "CANCELLED")
        self.assertEqual(list((self.root / "input").iterdir()), [])

    def test_retry_starts_a_new_attempt_and_drops_old_events(self):
        self.scenario(probe={"exit": 1, "stderr": "ERROR: Unable to download webpage: timed out"})
        task, = self.add()
        self.run_all()
        self.assertEqual(self.store.get(task["id"])["error_code"], "NETWORK")
        self.scenario(probe={"json": video()})
        retried = self.worker.retry(task["id"])
        self.assertEqual((retried["state"], retried["attempt"]), ("QUEUED", 2))
        self.assertTrue(all(event["attempt"] == 2 for event in self.store.events(task["id"])))
        self.run_all()
        self.assertEqual(self.state(task["id"]), "COMPLETED")

    def test_slots_limit_parallel_tasks_and_lowering_does_not_stop_any(self):
        for bad in (0, 4, True, "2"):
            with self.assertRaises(DownloadActionError):
                self.worker.set_slots(bad)
        self.worker.set_slots(1)
        self.scenario(probe={"json": video()}, download={"hang": True})
        first, second = self.add("https://clips.example/a", "https://clips.example/b")
        self.worker.dispatch()
        self.assertTrue(wait_for(lambda: self.state(first["id"]) == "DOWNLOADING"))
        self.assertEqual(self.state(second["id"]), "QUEUED")
        self.worker.set_slots(2)
        self.worker.dispatch()
        self.assertTrue(wait_for(lambda: self.state(second["id"]) == "DOWNLOADING"))
        self.worker.set_slots(1)
        self.worker.dispatch()
        self.assertEqual({self.state(first["id"]), self.state(second["id"])}, {"DOWNLOADING"})

    def test_an_internal_error_never_leaves_a_running_state(self):
        def broken(*args, **kwargs):
            raise RuntimeError("boom")
        self.worker = self.make_worker(verifier=broken)
        self.scenario(probe={"json": video()})
        task, = self.add()
        self.run_all()
        failed = self.store.get(task["id"])
        self.assertEqual((failed["state"], failed["error_code"]), ("FAILED", "INTERNAL_ERROR"))

    def test_start_and_shutdown(self):
        self.scenario(probe={"json": video()}, download={"hang": True})
        task, = self.add()
        self.worker.start()
        self.assertTrue(wait_for(lambda: self.state(task["id"]) == "DOWNLOADING"))
        self.worker.shutdown(15)
        self.assertEqual(self.state(task["id"]), "INTERRUPTED")


@unittest.skipUnless(FFMPEG.exists() and FFPROBE.exists(), "project FFmpeg is required (BILIFLOW_FFMPEG)")
class RealVerifyTests(WorkerCase):
    verifier = None

    def make_worker(self, verifier=None):
        from biliflow.download_files import verify_video
        return super().make_worker(verifier=verifier or verify_video)

    def clip(self, name, audio=True):
        path = self.root / name
        command = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=12"]
        if audio:
            command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-c:a", "aac", "-shortest"]
        command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
        subprocess.run(command, check=True, creationflags=CREATE_NO_WINDOW)
        return path

    def test_a_real_clip_passes(self):
        self.scenario(probe={"json": video(duration=12.0)}, download={"fixture": str(self.clip("good.mp4"))})
        task, = self.add()
        self.run_all()
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", done["error_message"])
        self.assertEqual((done["verify"]["video_codec"], done["verify"]["audio_codec"]), ("h264", "aac"))

    def test_a_clip_without_audio_fails_and_keeps_its_temp(self):
        self.scenario(probe={"json": video(duration=12.0)},
                      download={"id": "abc", "fixture": str(self.clip("silent.mp4", audio=False))})
        task, = self.add()
        self.run_all()
        failed = self.store.get(task["id"])
        self.assertEqual((failed["state"], failed["error_code"]), ("FAILED", "NO_AUDIO_STREAM"))
        self.assertTrue((self.root / "temp" / "downloads" / str(task["id"]) / "abc.mp4").is_file())
        self.assertEqual(list((self.root / "input").iterdir()), [])


class RecoveryTests(WorkerCase):
    def make(self, state, **fields):
        task, = self.store.add_tasks("clips", [f"https://clips.example/{state.lower()}/{len(self.store.list_tasks())}"])
        folder = self.root / "temp" / "downloads" / str(task["id"])
        folder.mkdir(parents=True, exist_ok=True)
        self.store.transition(task["id"], {"QUEUED"}, state, temp_dir=str(folder), **fields)
        return task["id"], folder

    def test_each_running_state_is_settled_without_restarting(self):
        probing, _ = self.make("PROBING")
        downloading, _ = self.make("DOWNLOADING")
        verifying, _ = self.make("VERIFYING")
        waiting, _ = self.make("WAITING_SPACE")
        cancelling, cancel_dir = self.make("CANCELLING")
        report = self.worker.recover()
        self.assertEqual(self.state(probing), "INTERRUPTED")
        self.assertEqual(self.state(downloading), "INTERRUPTED")
        self.assertEqual(self.state(verifying), "INTERRUPTED")
        self.assertEqual(self.state(waiting), "QUEUED")
        self.assertEqual(self.state(cancelling), "CANCELLED")
        self.assertFalse(cancel_dir.exists())
        self.assertEqual(report["DOWNLOADING->INTERRUPTED"], 1)
        self.assertEqual(self.calls(), [])

    def test_publishing_is_settled_from_the_files_never_guessed(self):
        data = b"video bytes"
        digest = hashlib.sha256(data).hexdigest()
        moved_ok, _ = self.make("PUBLISHING", output_sha256=digest, output_size=len(data))
        target = self.root / "input" / "Done.mp4"
        target.write_bytes(data)
        self.store.update_fields(moved_ok, output_path=str(target))
        temp_left, folder = self.make("PUBLISHING", output_sha256=digest, output_size=len(data))
        (folder / "abc.mp4").write_bytes(data)
        self.store.update_fields(temp_left, temp_file=str(folder / "abc.mp4"),
                                 output_path=str(self.root / "input" / "Missing.mp4"))
        other = self.root / "input" / "Other.mp4"
        other.write_bytes(b"someone else's file")
        lost, _ = self.make("PUBLISHING", output_sha256=digest, output_size=len(data))
        self.store.update_fields(lost, output_path=str(other))
        self.worker.recover()
        self.assertEqual(self.state(moved_ok), "COMPLETED")
        self.assertEqual(self.state(temp_left), "INTERRUPTED")
        self.assertEqual(self.store.get(lost)["error_code"], "PUBLISH_UNKNOWN")
        self.assertEqual(other.read_bytes(), b"someone else's file")

    def test_a_leftover_process_of_the_same_task_is_killed(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                   creationflags=CREATE_NO_WINDOW)
        try:
            created = psutil.Process(process.pid).create_time()
            task_id, _ = self.make("DOWNLOADING", pid=process.pid, pid_created=created)
            self.worker.recover()
            self.assertTrue(wait_for(lambda: process.poll() is not None))
            self.assertEqual(self.state(task_id), "INTERRUPTED")
        finally:
            if process.poll() is None:
                process.kill()

    def test_a_reused_pid_is_left_alone(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                   creationflags=CREATE_NO_WINDOW)
        try:
            self.make("DOWNLOADING", pid=process.pid, pid_created=1.0)
            self.worker.recover()
            time.sleep(0.3)
            self.assertIsNone(process.poll())
        finally:
            process.kill()
            process.wait()


class SweepTests(WorkerCase):
    def age(self, task_id, **delta):
        stamp = (self.clock.now - timedelta(**delta)).isoformat()
        with self.store._lock, self.store._connection:
            self.store._connection.execute("UPDATE download_tasks SET state_since = ? WHERE id = ?",
                                           (stamp, task_id))

    def make(self, state, **delta):
        task, = self.store.add_tasks("clips", [f"https://clips.example/sweep/{len(self.store.list_tasks())}"])
        folder = self.root / "temp" / "downloads" / str(task["id"])
        folder.mkdir(parents=True)
        (folder / "part.bin").write_bytes(b"x" * 10)
        self.store.transition(task["id"], {"QUEUED"}, state)
        self.age(task["id"], **delta)
        return task["id"], folder

    def orphan(self, name, hours):
        folder = self.root / "temp" / "downloads" / name
        folder.mkdir(parents=True)
        (folder / "junk").write_bytes(b"y")
        stamp = (self.clock.now - timedelta(hours=hours)).timestamp()
        os.utime(folder, (stamp, stamp))
        return folder

    def test_expiry_row_retention_orphans_and_caches(self):
        old_stop, old_stop_dir = self.make("STOPPED", days=8)
        new_fail, new_fail_dir = self.make("FAILED", days=3)
        old_done, _ = self.make("COMPLETED", days=31)
        new_cancel, _ = self.make("CANCELLED", days=10)
        old_orphan = self.orphan("999", hours=25)
        new_orphan = self.orphan("998", hours=1)
        keep = self.root / "input" / "keep.mp4"
        keep.write_bytes(b"keep")
        outside = self.root / "temp" / "other.bin"
        outside.write_bytes(b"other")
        summary = self.worker.sweep()
        self.assertEqual(self.state(old_stop), "EXPIRED")
        self.assertFalse(old_stop_dir.exists())
        self.assertEqual(self.state(new_fail), "FAILED")
        self.assertTrue(new_fail_dir.exists())
        self.assertIsNone(self.store.get(old_done))
        self.assertEqual(self.state(new_cancel), "CANCELLED")
        self.assertFalse(old_orphan.exists())
        self.assertTrue(new_orphan.exists())
        self.assertTrue(keep.is_file() and outside.is_file())
        self.assertEqual((summary["expired"], summary["deleted"], summary["orphans"]), (1, 1, 1))
        self.assertEqual(self.pruned, [self.root.resolve()])

    def test_cleanup_temp_expires_stopped_failed_and_interrupted(self):
        stopped, stopped_dir = self.make("STOPPED", hours=1)
        completed, _ = self.make("COMPLETED", hours=1)
        preview = self.worker.temp_summary()
        self.assertEqual((preview["tasks"], preview["bytes"]), (1, 10))
        result = self.worker.cleanup_temp()
        self.assertEqual(result, {"tasks": 1, "freed_bytes": 10})
        self.assertEqual(self.state(stopped), "EXPIRED")
        self.assertFalse(stopped_dir.exists())
        self.assertEqual(self.state(completed), "COMPLETED")

    def test_cleanup_temp_with_ids_spares_a_task_stopped_after_the_confirmation(self):
        shown, shown_dir = self.make("STOPPED", hours=1)
        preview = self.worker.temp_summary()
        self.assertEqual(preview["ids"], [shown])
        later, later_dir = self.make("FAILED", hours=1)  # failed while the dialog was open
        self.assertEqual(self.worker.cleanup_temp(preview["ids"]), {"tasks": 1, "freed_bytes": 10})
        self.assertEqual((self.state(shown), self.state(later)), ("EXPIRED", "FAILED"))
        self.assertFalse(shown_dir.exists())
        self.assertTrue(later_dir.exists())


if __name__ == "__main__":
    unittest.main()
