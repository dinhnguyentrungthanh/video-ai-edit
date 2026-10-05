import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import psutil

from biliflow.download_runner import (
    MAX_PROBE_ENTRIES,
    PROGRESS_PREFIX,
    ProcessControl,
    ProgressTracker,
    SizeGuard,
    YtDlpRunner,
    kill_process_tree,
    mask_line,
)


FAKE = Path(__file__).resolve().with_name("fake_yt_dlp.py")
URL = "https://clips.example/watch?v=abc&list=x"


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.scenario_path = self.root / "scenario.json"
        self.log_path = self.root / "calls.jsonl"
        self.deno = self.root / "tools" / "deno" / "deno.exe"
        self.deno.parent.mkdir(parents=True)
        self.deno.write_bytes(b"fake")
        self.runner = YtDlpRunner(
            self.root, command_prefix=[sys.executable, str(FAKE)], deno_path=self.deno,
            env_extra={"FAKE_YTDLP_SCENARIO": str(self.scenario_path),
                       "FAKE_YTDLP_LOG": str(self.log_path)},
            probe_timeout=20,
        )
        self.task_dir = self.root / "temp" / "downloads" / "1"

    def tearDown(self):
        self.directory.cleanup()

    def scenario(self, **payload):
        self.scenario_path.write_text(json.dumps(payload), encoding="utf-8")

    def calls(self):
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]


class CommandTests(RunnerCase):
    def test_probe_command_is_a_list_with_the_url_after_the_separator(self):
        command = self.runner.probe_command(URL)
        self.assertEqual(command[:2], [sys.executable, str(FAKE)])
        self.assertEqual(command[-2:], ["--", URL])
        for flag in ("--ignore-config", "--no-plugin-dirs", "--no-cookies", "--no-cookies-from-browser",
                     "--no-update", "--no-playlist", "--dump-single-json", "--skip-download",
                     "--no-warnings", "--no-remote-components"):
            self.assertIn(flag, command)
        runtimes = command.index("--no-js-runtimes")
        self.assertEqual(command[runtimes + 1:runtimes + 3], ["--js-runtimes", f"deno:{self.deno}"])
        self.assertEqual(command[command.index("--cache-dir") + 1], str(self.root / "cache" / "yt-dlp"))
        self.assertEqual(command[command.index("-S") + 1], "res:1080,vcodec:h264,acodec:aac")
        self.assertNotIn("--cookies", command)
        # A playlist or a channel pasted by mistake is not read video by video until the timeout.
        self.assertEqual(command[command.index("--playlist-end") + 1], str(MAX_PROBE_ENTRIES))
        self.assertEqual(MAX_PROBE_ENTRIES, 10)

    def test_download_command(self):
        command = self.runner.download_command(URL, self.task_dir, playlist_item=4, max_filesize=123)
        self.assertEqual(command[-2:], ["--", URL])
        self.assertEqual(command[command.index("--playlist-items") + 1], "4")
        self.assertEqual(command[command.index("--max-filesize") + 1], "123")
        homes = [command[index + 1] for index, item in enumerate(command) if item == "-P"]
        self.assertEqual(homes, [f"home:{self.task_dir}", f"temp:{self.task_dir / 'frag'}"])
        template = command[command.index("--progress-template") + 1]
        self.assertTrue(template.startswith(f"download:{PROGRESS_PREFIX} "))
        for flag in ("--continue", "--newline", "--no-mtime", "--restrict-filenames", "--no-write-subs",
                     "--no-write-auto-subs", "--no-embed-subs", "--no-write-thumbnail"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--merge-output-format") + 1], "mp4")
        self.assertEqual(command[command.index("--remux-video") + 1], "mp4")
        plain = self.runner.download_command(URL, self.task_dir)
        self.assertNotIn("--playlist-items", plain)
        self.assertNotIn("--max-filesize", plain)

    def test_without_the_project_deno_no_other_runtime_is_used(self):
        runner = YtDlpRunner(self.root, command_prefix=["x"], deno_path=self.root / "missing.exe")
        command = runner.probe_command(URL)
        self.assertIn("--no-js-runtimes", command)
        self.assertNotIn("--js-runtimes", command)

    def test_environment_keeps_caches_and_temp_on_the_project_drive(self):
        env = self.runner.environment(self.task_dir)
        self.assertEqual(env["DENO_DIR"], str(self.root / "cache" / "deno"))
        self.assertEqual(env["DENO_NO_UPDATE_CHECK"], "1")
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertEqual(env["TEMP"], str(self.task_dir / "tmp"))
        self.assertEqual(env["TMP"], str(self.task_dir / "tmp"))


class ProbeTests(RunnerCase):
    def test_probe_returns_the_parsed_json(self):
        self.scenario(probe={"json": {"id": "abc", "title": "Tiếng Việt 中文", "duration": 61}})
        outcome = self.runner.probe(URL, self.task_dir, ProcessControl())
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.info["title"], "Tiếng Việt 中文")
        call, = self.calls()
        self.assertEqual(call["argv"][-2:], ["--", URL])
        self.assertEqual(call["env"]["PYTHONUTF8"], "1")

    def test_probe_errors_are_classified(self):
        self.scenario(probe={"exit": 1, "stderr": "ERROR: [youtube] abc: Sign in to confirm your age"})
        outcome = self.runner.probe(URL, self.task_dir, ProcessControl())
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.code, "AGE_RESTRICTED")

    def test_unreadable_json_fails(self):
        self.scenario(probe={"raw": "{not json"})
        outcome = self.runner.probe(URL, self.task_dir, ProcessControl())
        self.assertEqual(outcome.code, "PROBE_FAILED")

    def test_a_hanging_probe_times_out_and_dies(self):
        self.scenario(probe={"hang": True})
        runner = YtDlpRunner(self.root, command_prefix=[sys.executable, str(FAKE)], deno_path=None,
                             env_extra={"FAKE_YTDLP_SCENARIO": str(self.scenario_path),
                                        "FAKE_YTDLP_LOG": str(self.log_path)}, probe_timeout=1.5)
        outcome = runner.probe(URL, self.task_dir, ProcessControl())
        self.assertEqual(outcome.code, "PROBE_TIMEOUT")
        pid = self.calls()[0]["pid"]
        self.assertTrue(wait_for(lambda: not psutil.pid_exists(pid)))

    def test_a_probe_can_be_stopped(self):
        self.scenario(probe={"hang": True})
        control = ProcessControl()
        result = {}
        thread = threading.Thread(target=lambda: result.update(
            outcome=self.runner.probe(URL, self.task_dir, control)))
        thread.start()
        self.assertTrue(wait_for(lambda: self.log_path.exists()))
        control.request("stop")
        thread.join(15)
        self.assertEqual(result["outcome"].code, "STOPPED")


class DownloadTests(RunnerCase):
    def run_download(self, control=None, **kwargs):
        progress, logs = [], []
        outcome = self.runner.download(
            URL, self.task_dir, control or ProcessControl(),
            on_progress=progress.append, on_log=logs.extend, **kwargs,
        )
        return outcome, progress, logs

    def test_a_download_reports_progress_and_the_final_file(self):
        steps = [[0, 1000, None, 100.0, 10, "a"], [500, 1000, None, 100.0, 5, "a"],
                 [1000, 1000, None, 100.0, 0, "a"], [200, 200, None, 50.0, 0, "b"]]
        self.scenario(download={"id": "abc", "progress": steps,
                                "lines": ["[download] https://cdn.example/v.mp4?token=SECRET&sig=XYZ ok"]})
        outcome, progress, logs = self.run_download(expected_files=2, estimated_bytes=1300)
        self.assertTrue(outcome.ok, outcome)
        self.assertEqual(outcome.final_path, self.task_dir / "abc.mp4")
        self.assertTrue(outcome.final_path.is_file())
        self.assertEqual(progress[-1].downloaded_bytes, 1200)
        self.assertEqual(progress[-1].total_bytes, 1200)
        self.assertTrue(any("token=***" in line and "sig=***" in line for line in logs))
        self.assertFalse(any("SECRET" in line for line in logs))
        self.assertFalse(any(line.startswith(PROGRESS_PREFIX) for line in logs))

    def test_progress_without_any_total_has_no_percent_or_eta(self):
        self.scenario(download={"progress": [[100, None, None, 10.0, None], [300, None, None, 10.0, None]]})
        outcome, progress, _ = self.run_download(expected_files=1, estimated_bytes=None)
        self.assertTrue(outcome.ok)
        self.assertEqual(progress[-1].downloaded_bytes, 300)
        self.assertIsNone(progress[-1].total_bytes)
        self.assertIsNone(progress[-1].eta)

    def test_the_final_file_is_found_without_the_print_file(self):
        self.scenario(download={"id": "zz", "skip_print": True})
        outcome, _, _ = self.run_download()
        self.assertEqual(outcome.final_path, self.task_dir / "zz.mp4")

    def test_a_download_that_writes_more_than_it_may_is_ended(self):
        # The page said 100 bytes; yt-dlp reports more than the free space allows, then would go on.
        self.scenario(download={"id": "big", "hang": True,
                                "progress": [[100, 100, None, 10.0, 1], [5000, None, None, 10.0, None]]})
        guard = SizeGuard(self.task_dir, 1000, 2200)
        started = time.monotonic()
        outcome, _, _ = self.run_download(estimated_bytes=100, guard=guard)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual((outcome.ok, outcome.code), (False, "TOO_LARGE"))
        self.assertIn("chỗ trống", outcome.message)

    def test_a_silent_download_is_ended_by_its_folder_size(self):
        # No progress line after the first part: only the folder watcher sees it grow.
        self.scenario(download={"id": "quiet", "hang": True, "progress": [[None, None, None, None, None]]})
        guard = SizeGuard(self.task_dir, None, 8, interval=0.1)
        outcome, _, _ = self.run_download(guard=guard)
        self.assertEqual(outcome.code, "TOO_LARGE")

    def test_a_download_within_its_limits_is_not_ended(self):
        self.scenario(download={"id": "ok", "progress": [[500, 1000, None, 10.0, 1], [1000, 1000, None, 10.0, 0]]})
        outcome, _, _ = self.run_download(guard=SizeGuard(self.task_dir, 1000, 10_000_000, interval=0.05))
        self.assertTrue(outcome.ok, outcome)

    def test_download_errors_are_classified(self):
        self.scenario(download={"exit": 1, "stderr": "ERROR: [Errno 28] No space left on device"})
        outcome, _, _ = self.run_download()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.code, "DISK_FULL")

    def test_stop_kills_the_whole_tree_and_keeps_the_partial_file(self):
        child_file = self.root / "child.pid"
        pid_file = self.root / "parent.pid"
        self.scenario(download={"hang": True, "spawn_child": True, "child_pid_file": str(child_file),
                                "pid_file": str(pid_file), "progress": [[16, 100, None, 1.0, 5]]})
        control = ProcessControl()
        started = []
        result = {}
        thread = threading.Thread(target=lambda: result.update(outcome=self.runner.download(
            URL, self.task_dir, control, on_progress=lambda item: None, on_log=lambda lines: None,
            on_start=lambda pid, created: started.append(pid))))
        thread.start()
        self.assertTrue(wait_for(lambda: child_file.exists() and pid_file.exists()))
        parent = int(pid_file.read_text())
        child = int(child_file.read_text())
        control.request("stop")
        thread.join(20)
        self.assertEqual(result["outcome"].code, "STOPPED")
        # The venv python.exe is a launcher: the recorded pid is the root of the tree.
        self.assertEqual(len(started), 1)
        self.assertTrue(wait_for(lambda: not any(psutil.pid_exists(pid) for pid in (*started, parent, child))))
        self.assertTrue((self.task_dir / "vid1.mp4.part").exists())

    def test_a_second_run_continues_the_partial_file(self):
        self.scenario(download={"exit": 1, "progress": [[16, 100, None, 1.0, 5]],
                                "stderr": "ERROR: Connection reset by peer"})
        first, _, _ = self.run_download()
        self.assertEqual(first.code, "NETWORK")
        self.scenario(download={"progress": [[32, 100, None, 1.0, 5]]})
        second, _, logs = self.run_download()
        self.assertTrue(second.ok)
        self.assertTrue(any("resumed=True" in line for line in logs))

    def test_a_request_before_start_kills_the_new_process(self):
        self.scenario(download={"hang": True})
        control = ProcessControl()
        control.request("cancel")
        outcome, _, _ = self.run_download(control=control)
        self.assertEqual(outcome.code, "CANCELLED")

    def test_a_failing_progress_write_never_leaves_yt_dlp_running(self):
        child_file = self.root / "child.pid"
        self.scenario(download={"hang": True, "spawn_child": True, "child_pid_file": str(child_file),
                                "progress": [[16, 100, None, 1.0, 5]]})
        started = []

        def failing_progress(item):
            raise RuntimeError("database or disk is full")
        with self.assertRaises(RuntimeError):
            self.runner.download(URL, self.task_dir, ProcessControl(), on_progress=failing_progress,
                                 on_log=lambda lines: None, on_start=lambda pid, created: started.append(pid))
        self.assertTrue(child_file.exists())
        pids = (*started, int(child_file.read_text()))
        self.assertTrue(wait_for(lambda: not any(psutil.pid_exists(pid) for pid in pids)))

    def test_a_failing_start_record_kills_the_new_process(self):
        pid_file = self.root / "parent.pid"
        started = []

        def failing_start(pid, created):
            started.append(pid)
            raise RuntimeError("database is locked")
        for name, call in (
            ("probe", lambda: self.runner.probe(URL, self.task_dir, ProcessControl(), on_start=failing_start)),
            ("download", lambda: self.runner.download(URL, self.task_dir, ProcessControl(),
                                                      on_progress=lambda item: None, on_log=lambda lines: None,
                                                      on_start=failing_start)),
        ):
            with self.subTest(step=name):
                self.scenario(probe={"hang": True}, download={"hang": True, "pid_file": str(pid_file)})
                with self.assertRaises(RuntimeError):
                    call()
                self.assertTrue(wait_for(lambda: not psutil.pid_exists(started[-1])))


class DefaultCommandTests(unittest.TestCase):
    def test_the_task_folder_is_never_on_the_module_path(self):
        with TemporaryDirectory() as directory:
            runner = YtDlpRunner(Path(directory), deno_path=None)
        self.assertEqual(runner.command_prefix, [sys.executable, "-P", "-m", "yt_dlp"])


class HelperTests(unittest.TestCase):
    def test_mask_line(self):
        self.assertEqual(mask_line("GET https://x.example/a?Signature=abc&id=1&access_token=q"),
                         "GET https://x.example/a?Signature=***&id=1&access_token=***")
        self.assertEqual(mask_line("Cookie: SID=abc; HSID=def"), "Cookie: ***")
        self.assertEqual(mask_line("Authorization: Bearer abc.def"), "Authorization: ***")
        self.assertEqual(mask_line("blob " + "A" * 48), "blob ***")
        self.assertEqual(mask_line("[download] 10% of 3.00MiB"), "[download] 10% of 3.00MiB")
        self.assertLessEqual(len(mask_line("x " * 1000)), 500)

    def test_progress_tracker_uses_the_estimate_until_every_file_is_known(self):
        tracker = ProgressTracker(expected_files=2, estimated_bytes=1000)
        first = tracker.update("v", 400, 800, None, 100.0)
        self.assertEqual((first.downloaded_bytes, first.total_bytes), (400, 1000))
        self.assertAlmostEqual(first.eta, 6.0)
        tracker.update("v", 800, 800, None, 100.0)
        last = tracker.update("a", 100, 300, None, 100.0)
        self.assertEqual((last.downloaded_bytes, last.total_bytes), (900, 1100))
        self.assertEqual(ProgressTracker(1, None).update("v", 5, None, 50, 1.0).total_bytes, 50)

    def test_kill_process_tree_of_a_dead_pid_is_quiet(self):
        import subprocess
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait()
        self.assertEqual(kill_process_tree(process.pid), [])


if __name__ == "__main__":
    unittest.main()
