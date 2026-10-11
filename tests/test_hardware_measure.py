"""The "Đo lại" measurement process (docs/HARDWARE_PROFILE.md 3.2, 5.5, 6.3 and 9 "E2, đo").

Arguments, exit codes, a whole run on a fake machine with a fake FFmpeg, the measurement history
and hysteresis_choice. Mutexes are ``Local\\BiliFlowTest-<uuid>`` passed through ``Dependencies``
(I10); no GPU, torch, NVML, NVENC or NVDEC is touched, and every root lives in the worktree's temp.
"""
from __future__ import annotations

import contextlib
import copy
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import hardware_measure as hm
from biliflow import hardware_profile as hp
from biliflow import hardware_table, recycle_bin
from biliflow.hardware_idle import NOT_PAUSED, IdleReport, RootIdle
from biliflow.hardware_locks import unique_roots
from biliflow.hardware_schema import (
    MAX_MEASUREMENTS, profile_path, read_profile, validate_profile, write_json_atomic,
)
from biliflow.hardware_system import CommandResult, PowerInfo
from tests.hardware_fixtures import profile_document, rehash
from tests.test_hardware_detect import NOT_AVAILABLE, FakeSystem, probe_line
from tests.test_hardware_measure_locks import FileHolder, Holder, free_elsewhere, lock_file_free, unique_names
from tests.test_hardware_measure_trials import FakeClock, FakeTools

TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
CODE = Path(__file__).resolve().parents[1]
SRC = CODE / "src"
STAMP = "20261011-100000-ab12"
ZONE = timezone(timedelta(hours=7))
FINGERPRINT = "f" * 64


def fixed_now() -> datetime:
    return datetime(2026, 10, 11, 10, 0, 0, tzinfo=ZONE)


def no_database(*_args, **_kwargs):
    raise AssertionError("no database may be opened while no Control Center runs")


def idle_report(root: Path, *, reason: str | None = None, downloads: int = 0) -> IdleReport:
    return IdleReport((RootIdle(root, reason is not None or downloads > 0, reason, downloads),))


def run_child(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run([sys.executable, *args], capture_output=True, timeout=120, env=env,
                          stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class MeasureCase(unittest.TestCase):
    def setUp(self) -> None:
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "root"
        (self.root / "state").mkdir(parents=True)
        self.ffmpeg_dir = self.base / "ffmpeg"
        self.ffmpeg_dir.mkdir()
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            (self.ffmpeg_dir / name).write_bytes(b"")  # never run: every command goes to FakeTools
        self.names = unique_names()
        self.clock = FakeClock()
        self.logs: list[str] = []

    def settings(self, *guards: Path, wait_minutes: int = 0) -> hm.Settings:
        return hm.Settings(self.root, unique_roots([self.root, *guards]), self.ffmpeg_dir, STAMP, wait_minutes,
                           "control-center")

    def tools(self, **kwargs) -> FakeTools:
        return FakeTools(root=self.root, clock=self.clock, **kwargs)

    def deps(self, *, system=None, tools: FakeTools | None = None, **changes) -> hm.Dependencies:
        base = hm.Dependencies(names=self.names, system=system or FakeSystem(), popen=tools or self.tools(),
                               kill=lambda process: process.kill(), connect=no_database, alive=lambda pid: True,
                               clock=self.clock, sleep=self.clock.advance, now=fixed_now,
                               python_exe=self.base / ".venv" / "Scripts" / "python.exe", lock_seconds=0.2,
                               log=self.logs.append)
        return replace(base, **changes)

    def measure(self, *guards: Path, settings: hm.Settings | None = None, **kwargs) -> int:
        return hm.run_measurement(settings or self.settings(*guards), self.deps(**kwargs))

    def profile(self, which: str = "main") -> dict:
        read = read_profile(self.root, which)
        self.assertEqual(read.state, "ok", read.error)
        return read.document

    def assert_nothing_written(self) -> None:
        self.assertEqual(sorted(path.name for path in (self.root / "state").iterdir()), [])
        self.assertFalse(self.settings().run_dir.exists())

    def assert_locks_free(self) -> None:
        self.assertTrue(free_elsewhere(self.names.own))
        self.assertTrue(free_elsewhere(self.names.gpu))
        self.assertTrue(free_elsewhere(self.names.render))


class ArgumentTests(MeasureCase):
    def quiet_main(self) -> contextlib.ExitStack:
        """main() would reconfigure and write to this process's stderr; keep its lines instead."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(hm, "_utf8_stderr", lambda: None))
        stack.enter_context(mock.patch.object(hm, "_stderr_line", self.logs.append))
        return stack

    def check(self, *extra: str, root: Path | None = None, allowed=lambda path: True, stamp=lambda: STAMP,
              code_dir: Path | None = None) -> hm.Settings:
        argv = ["measure", "--project-root", str(root or self.root), "--ffmpeg-dir", str(self.ffmpeg_dir), *extra]
        options = hm.parse_arguments(argv)
        return hm.check_arguments(options, allowed=allowed, stamp=stamp, code_dir=code_dir or CODE)

    def test_valid_arguments_give_settings_with_the_project_root_guarded_first(self):
        other = self.base / "install"
        other.mkdir()
        settings = self.check("--guard-root", str(other), "--guard-root", str(self.root), "--stamp", STAMP,
                              "--wait-minutes", "5", "--launched-by", "control-center")
        self.assertEqual(settings.guard_roots, (self.root, other))
        self.assertEqual((settings.stamp, settings.wait_minutes, settings.launched_by),
                         (STAMP, 5, "control-center"))
        self.assertEqual(settings.run_dir, self.root / "temp" / f"hardware-measure-{STAMP}")
        self.assertFalse(settings.run_dir.exists(), "argument checks write nothing")

    def test_a_missing_stamp_is_made_and_matches_the_pattern(self):
        settings = hm.check_arguments(hm.parse_arguments(["measure", "--project-root", str(self.root),
                                                          "--ffmpeg-dir", str(self.ffmpeg_dir)]),
                                      allowed=lambda path: True)
        self.assertRegex(settings.stamp, r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
        self.assertEqual(settings.launched_by, "script")

    def test_the_default_ffmpeg_folder_is_the_code_folder_tools_even_when_empty_or_missing(self):
        code = self.base / "code"
        options = hm.parse_arguments(["measure", "--project-root", str(self.root)])
        settings = hm.check_arguments(options, allowed=lambda path: True, code_dir=code)
        self.assertEqual(settings.ffmpeg_dir, code / "tools" / "ffmpeg" / "bin")
        partial = self.base / "partial"
        partial.mkdir()
        (partial / "ffprobe.exe").write_bytes(b"")
        self.assertEqual(self.check("--ffmpeg-dir", str(partial)).ffmpeg_dir, partial,
                         "the folder's content is detection's to record, never a usage error")

    def test_a_machine_without_ffmpeg_is_measured_and_the_profile_says_so(self):
        code = self.measure(system=FakeSystem(ffmpeg_present=False))
        self.assertEqual(code, hm.EXIT_OK)
        ffmpeg = self.profile()["detection"]["ffmpeg"]
        self.assertFalse(ffmpeg["ok"])
        self.assertIsNotNone(ffmpeg["error"])
        self.assertEqual({entry["trial"] for entry in ffmpeg["encoders"].values()}, {"not_run"})
        self.assertEqual(ffmpeg["nvdec"]["trial"], "not_run")

    def test_bad_arguments_are_usage_errors(self):
        file_path = self.base / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        cases = {
            "not measure": lambda: hm.parse_arguments(["scan", "--project-root", str(self.root)]),
            "no root": lambda: hm.parse_arguments(["measure"]),
            "unknown option": lambda: hm.parse_arguments(["measure", "--project-root", str(self.root),
                                                          "--gpu-mutex", "Local\\x"]),
            "abbreviation": lambda: hm.parse_arguments(["measure", "--project", str(self.root)]),
            "relative root": lambda: self.check(root=Path("temp") / "x"),
            "drive-relative root": lambda: self.check(root=Path("C:temp")),
            "missing root": lambda: self.check(root=self.base / "missing"),
            "root is a file": lambda: self.check(root=file_path),
            "refused root": lambda: self.check(allowed=lambda path: False),
            "guard root missing": lambda: self.check("--guard-root", str(self.base / "missing")),
            "guard root relative": lambda: self.check("--guard-root", "relative"),
            "guard root is a file": lambda: self.check("--guard-root", str(file_path)),
            "ffmpeg folder missing": lambda: self.check("--ffmpeg-dir", str(self.base / "missing")),
            "ffmpeg folder relative": lambda: self.check("--ffmpeg-dir", "tools"),
            "ffmpeg folder is a file": lambda: self.check("--ffmpeg-dir", str(file_path)),
        }
        for stamp in ("2026-10-11", "20261011-100000-AB12", "20261011-100000-ab1", "２０２６１０１１-100000-ab12",
                      "20261011-100000-ab12\n", ""):
            cases[f"stamp {stamp!r}"] = lambda stamp=stamp: self.check("--stamp", stamp)
        for wait in ("61", "-1", "x", "١", "100", ""):
            cases[f"wait {wait!r}"] = lambda wait=wait: self.check("--wait-minutes", wait)
        cases["launched by installer"] = lambda: self.check("--launched-by", "installer")
        for name, call in cases.items():
            with self.subTest(case=name), self.assertRaises(hm.UsageError):
                call()
        self.assertEqual([path.name for path in self.root.iterdir()], ["state"])

    def test_no_option_names_a_mutex_or_a_lock_file(self):
        options = [option for action in hm.build_parser()._actions for option in action.option_strings]
        self.assertEqual(sorted(options), ["--ffmpeg-dir", "--guard-root", "--launched-by", "--project-root",
                                           "--stamp", "--wait-minutes"])
        for option in options:
            self.assertNotIn("mutex", option)
            self.assertNotIn("lock", option)

    def test_a_run_folder_may_already_hold_only_the_cancel_and_progress_files(self):
        run_dir = self.root / "temp" / f"hardware-measure-{STAMP}"
        run_dir.mkdir(parents=True)
        (run_dir / "cancel").write_bytes(b"")
        (run_dir / "progress.json").write_text("{}", encoding="utf-8")
        self.check("--stamp", STAMP)
        (run_dir / "clip.mp4").write_bytes(b"old")
        with self.assertRaises(hm.UsageError):
            self.check("--stamp", STAMP)

    def test_main_refuses_a_root_outside_this_checkout_before_any_lock(self):
        refused = CODE / "docs"  # exists; allowed_project_root refuses it (not the install, not in temp)
        with mock.patch.object(hm, "run_measurement", side_effect=AssertionError("must not run")), \
                mock.patch.object(hm, "NamedMutex", side_effect=AssertionError("no lock")), self.quiet_main():
            code = hm.main(["measure", "--project-root", str(refused), "--ffmpeg-dir", str(self.ffmpeg_dir)])
        self.assertEqual(code, hm.EXIT_USAGE)
        self.assertFalse((refused / "temp").exists())
        self.assertFalse((refused / "state").exists())

    def test_main_turns_every_usage_error_into_64(self):
        for argv in ([], ["measure"], ["measure", "--project-root", "relative"], ["--help"]):
            with self.subTest(argv=argv), self.quiet_main(), mock.patch.object(
                    hm, "run_measurement", side_effect=AssertionError("must not run")):
                self.assertEqual(hm.main(argv), hm.EXIT_USAGE)


class RunTests(MeasureCase):
    def test_an_ok_run_writes_main_then_last_good_and_removes_the_run_folder(self):
        code = self.measure()
        self.assertEqual(code, hm.EXIT_OK)
        main, last_good = self.profile("main"), self.profile("last_good")
        self.assertEqual(main, last_good)
        self.assertEqual(main["attempt"]["result"], "ok")
        self.assertEqual(main["attempt"]["budget_seconds"], 600)
        self.assertEqual(main["writer"], {"biliflow_version": hm.__version__, "mode": "measure",
                                          "launched_by": "control-center"})
        self.assertEqual(main["history"], {"had_cuda": True, "first_cuda_at": "2026-10-11T10:00:00+07:00"})
        ffmpeg = main["detection"]["ffmpeg"]
        self.assertEqual(ffmpeg["encoders"]["libx264"]["trial"], "ok")
        self.assertEqual(ffmpeg["encoders"]["h264_qsv"]["trial"], "not_run")
        self.assertEqual(ffmpeg["nvdec"], {"listed": True, "trial": "ok", "framemd5_equal_cpu": True,
                                           "chain": "logo_routing"})
        records = main["measurements"]
        self.assertEqual([record["id"] for record in records], [f"m-{STAMP}-{n}" for n in range(1, 6)])
        self.assertEqual({record["quality"] for record in records}, {"clean"})
        self.assertTrue(all(record["clip"].startswith("syn-1080p30-20s:") and len(record["clip"]) == 80
                            for record in records))
        self.assertTrue(all(record["fingerprint_sha256"] == main["fingerprint"]["sha256"] for record in records))
        self.assertFalse(self.settings().run_dir.exists())
        self.assertEqual(sorted(path.name for path in (self.root / "state").iterdir()),
                         ["hardware-profile.json", "hardware-profile.last-good.json"])
        self.assertTrue(any("Bước 3/" in line and "nvml" in line for line in self.logs))
        self.assert_locks_free()

    def test_the_written_profile_resolves_to_ready_on_the_same_machine(self):
        self.measure()
        observed = hp.Observation("ok", (hp.GpuInfo("NVIDIA GeForce RTX 2060", "7.5", 6144),), "617.42", True,
                                  "2.14.0+cu126", hp.FileStat(102856192, 1786512472000000000),
                                  "Intel(R) Core(TM) i7-10750H CPU @ 2.60GHz")
        self.assertEqual(hp.resolve(self.root, observed=observed).status, "ready")

    def test_a_failed_attempt_writes_only_the_main_file(self):
        system = FakeSystem(probe=CommandResult(0, probe_line(**NOT_AVAILABLE)))
        code = self.measure(system=system)
        self.assertEqual(code, hm.EXIT_FAILED)
        main = self.profile("main")
        self.assertEqual(main["attempt"]["failure"]["code"], "cuda_unavailable")
        self.assertEqual(main["attempt"]["failure"]["step"], "torch_probe")
        self.assertIn("cuda_not_available", main["attempt"]["failure"]["message"])
        self.assertEqual(read_profile(self.root, "last_good").state, "missing")
        self.assertFalse(main["history"]["had_cuda"])

    def test_a_failed_attempt_keeps_the_last_good_file_and_the_cuda_history(self):
        earlier = profile_document("cuda")
        write_json_atomic(profile_path(self.root, "main"), earlier)
        write_json_atomic(profile_path(self.root, "last_good"), earlier)
        before = profile_path(self.root, "last_good").read_bytes()
        code = self.measure(system=FakeSystem(probe=CommandResult(0, probe_line(**NOT_AVAILABLE))))
        self.assertEqual(code, hm.EXIT_FAILED)
        self.assertEqual(profile_path(self.root, "last_good").read_bytes(), before)
        main = self.profile("main")
        self.assertEqual(main["history"], {"had_cuda": True, "first_cuda_at": earlier["history"]["first_cuda_at"]})

    def test_a_last_good_copy_that_cannot_be_written_keeps_the_old_one_and_exits_0(self):
        earlier = profile_document("cuda")
        write_json_atomic(profile_path(self.root, "last_good"), earlier)
        before = profile_path(self.root, "last_good").read_bytes()
        real = hm.write_json_atomic

        def refuse_last_good(path, document):
            if path == profile_path(self.root, "last_good"):
                raise PermissionError(13, "held by another program")
            real(path, document)

        with mock.patch.object(hm, "write_json_atomic", side_effect=refuse_last_good):
            code = self.measure()
        self.assertEqual(code, hm.EXIT_OK, "the main file holds the new profile: never 70 after a write")
        self.assertEqual(self.profile("main")["attempt"]["result"], "ok")
        self.assertEqual(profile_path(self.root, "last_good").read_bytes(), before)
        self.assertTrue(any("last-good" in line and "PermissionError" in line for line in self.logs))
        self.assert_locks_free()

    def test_trial_failures_never_fail_the_attempt(self):
        tools = self.tools(fail={"encode:h264_nvenc", "encode:hevc_nvenc", "nvdec_probe", "decode:h264:nvdec"})
        code = self.measure(tools=tools)
        self.assertEqual(code, hm.EXIT_OK)
        ffmpeg = self.profile()["detection"]["ffmpeg"]
        self.assertTrue(ffmpeg["ok"])
        self.assertIsNone(ffmpeg["error"])
        self.assertEqual(ffmpeg["encoders"]["h264_nvenc"]["trial"], "failed")
        self.assertEqual(ffmpeg["nvdec"]["trial"], "failed")

    def test_downloads_or_battery_mark_the_measurements(self):
        cases = (
            ("contended", {}, lambda roots, **kw: idle_report(self.root, downloads=1)),
            ("battery", {"power": lambda: PowerInfo(True, False)}, None),
        )
        for quality, overrides, check in cases:
            with self.subTest(quality=quality):
                for path in (self.root / "state").iterdir():
                    path.unlink()
                system = FakeSystem()
                for name, function in overrides.items():
                    setattr(system, name, function)
                with mock.patch.object(hm, "check_idle", check) if check else contextlib.nullcontext():
                    self.assertEqual(self.measure(system=system), hm.EXIT_OK)
                self.assertEqual({record["quality"] for record in self.profile()["measurements"]}, {quality})


class ExitTests(MeasureCase):
    def test_a_running_measurement_makes_another_exit_75_without_touching_anything(self):
        with Holder(self.names.own):
            code = self.measure()
        self.assertEqual(code, hm.EXIT_BUSY)
        self.assert_nothing_written()

    def test_a_held_gpu_or_render_mutex_exits_75_and_releases_what_was_taken(self):
        for which in ("gpu", "render"):
            with self.subTest(mutex=which):
                with Holder(getattr(self.names, which)):
                    code = self.measure()
                self.assertEqual(code, hm.EXIT_BUSY)
                self.assert_nothing_written()
                self.assert_locks_free()

    def test_a_held_export_lock_file_of_any_guard_root_exits_75(self):
        install = self.base / "install"
        (install / "state").mkdir(parents=True)
        lock = install / "state" / "final-render.lock"
        lock.write_bytes(b"0")
        with FileHolder(lock):
            code = self.measure(install)
        self.assertEqual(code, hm.EXIT_BUSY)
        self.assertEqual(lock.read_bytes(), b"0")
        self.assert_nothing_written()
        self.assert_locks_free()

    def test_a_missing_export_lock_file_is_skipped_and_never_created(self):
        install = self.base / "install"
        (install / "state").mkdir(parents=True)
        self.assertEqual(self.measure(install), hm.EXIT_OK)
        self.assertEqual(list((install / "state").iterdir()), [])
        self.assertFalse((self.root / "state" / "final-render.lock").exists())

    def test_a_busy_queue_exits_75_before_any_export_lock(self):
        with mock.patch.object(hm, "check_idle", lambda roots, **kw: idle_report(self.root, reason=NOT_PAUSED)), \
                mock.patch.object(hm, "acquire_export_locks", side_effect=AssertionError("no export lock")):
            code = self.measure()
        self.assertEqual(code, hm.EXIT_BUSY)
        self.assert_nothing_written()
        self.assertTrue(any("Hàng đợi chưa tạm dừng" in line for line in self.logs))

    def test_the_queue_is_checked_again_after_locking(self):
        reports = [idle_report(self.root), idle_report(self.root, reason="job_running")]
        held_at_recheck = []

        def check(roots, **kw):
            if len(reports) == 1:  # the second check: the GPU and render mutexes must be held by now
                held_at_recheck.append((free_elsewhere(self.names.gpu), free_elsewhere(self.names.render)))
            return reports.pop(0)

        with mock.patch.object(hm, "check_idle", check):
            code = self.measure()
        self.assertEqual(code, hm.EXIT_BUSY)
        self.assertEqual(reports, [])
        self.assertEqual(held_at_recheck, [(False, False)])
        self.assert_nothing_written()
        self.assert_locks_free()

    def test_a_cancel_file_written_before_the_start_exits_75(self):
        run_dir = self.settings().run_dir
        run_dir.mkdir(parents=True)
        (run_dir / "cancel").write_bytes(b"")
        code = self.measure()
        self.assertEqual(code, hm.EXIT_BUSY)
        self.assert_nothing_written()

    def test_a_cancel_during_the_trials_releases_the_locks_and_writes_nothing(self):
        run_dir = self.settings().run_dir

        def cancel_at(kind: str) -> None:
            if kind == "encode:h264_nvenc":
                (run_dir / "cancel").write_bytes(b"")
        tools = self.tools(on_start=cancel_at)
        code = self.measure(tools=tools)
        self.assertEqual(code, hm.EXIT_BUSY)
        self.assertNotIn("encode:hevc_nvenc", tools.kinds)
        self.assert_nothing_written()
        self.assert_locks_free()

    def test_a_cancel_while_a_command_waits_kills_it(self):
        run_dir = self.settings().run_dir
        tools = self.tools(hang={"clip"}, on_wait=lambda kind: (run_dir / "cancel").write_bytes(b""))
        self.assertEqual(self.measure(tools=tools), hm.EXIT_BUSY)
        self.assertEqual(tools.killed, ["clip"])
        self.assert_nothing_written()
        self.assert_locks_free()

    def test_the_time_budget_ends_with_124_releases_the_locks_and_writes_nothing(self):
        install = self.base / "install"
        (install / "state").mkdir(parents=True)
        lock = install / "state" / "final-render.lock"
        lock.write_bytes(b"0")
        for name, tools, budget in (("slow steps", self.tools(seconds=200.0), 600),
                                    ("stuck clip", self.tools(hang={"clip"}), 100)):
            with self.subTest(case=name):
                held_during = []
                tools.on_start = lambda kind: held_during.append(lock_file_free(lock)) if kind == "clip" else None
                code = self.measure(install, tools=tools, budget_seconds=budget)
                self.assertEqual(code, hm.EXIT_TIMEOUT)
                self.assert_nothing_written()
                self.assert_locks_free()
                self.assertIn(False, held_during, "the export lock file was held while the trials ran")
                self.assertTrue(lock_file_free(lock), "and is released after 124")
                self.assertEqual(lock.read_bytes(), b"0")

    def test_a_cancel_releases_the_export_lock_file(self):
        install = self.base / "install"
        (install / "state").mkdir(parents=True)
        lock = install / "state" / "final-render.lock"
        lock.write_bytes(b"0")
        run_dir = self.settings().run_dir
        tools = self.tools(hang={"clip"}, on_wait=lambda kind: (run_dir / "cancel").write_bytes(b""))
        self.assertEqual(self.measure(install, tools=tools), hm.EXIT_BUSY)
        self.assertTrue(lock_file_free(lock))
        self.assert_locks_free()

    def test_an_internal_error_ends_with_70_and_writes_nothing(self):
        system = FakeSystem()
        system.nvml = mock.Mock(side_effect=RuntimeError("broken binding"))
        code = self.measure(system=system)
        self.assertEqual(code, hm.EXIT_INTERNAL)
        self.assert_nothing_written()
        self.assert_locks_free()
        self.assertTrue(any("Lỗi nội bộ: RuntimeError" in line for line in self.logs))


class WaitTests(MeasureCase):
    def test_waiting_rechecks_every_30_seconds_holding_no_export_lock(self):
        calls: list[tuple[float, bool, bool]] = []

        def check(roots, **kwargs):
            calls.append((self.clock.now, free_elsewhere(self.names.gpu), free_elsewhere(self.names.render)))
            return idle_report(self.root, reason=NOT_PAUSED) if len(calls) < 3 else idle_report(self.root)
        with mock.patch.object(hm, "check_idle", check):
            code = self.measure(settings=self.settings(wait_minutes=2))
        self.assertEqual(code, hm.EXIT_OK)
        self.assertEqual([round(now - calls[0][0]) for now, _gpu, _render in calls[:3]], [0, 30, 60])
        self.assertTrue(all(gpu and render for _now, gpu, render in calls[:3]))

    def test_a_queue_that_stays_busy_ends_the_wait_with_75(self):
        with mock.patch.object(hm, "check_idle", lambda roots, **kw: idle_report(self.root, reason=NOT_PAUSED)):
            code = self.measure(settings=self.settings(wait_minutes=1))
        self.assertEqual(code, hm.EXIT_BUSY)
        self.assert_nothing_written()

    def test_a_cancel_file_ends_the_wait(self):
        run_dir = self.settings().run_dir

        def check(roots, **kwargs):
            (run_dir / "cancel").write_bytes(b"")
            return idle_report(self.root, reason=NOT_PAUSED)
        with mock.patch.object(hm, "check_idle", check):
            code = self.measure(settings=self.settings(wait_minutes=10))
        self.assertEqual(code, hm.EXIT_BUSY)
        self.assertLess(self.clock.now - 1000.0, 2.0)
        self.assert_nothing_written()


class HistoryTests(MeasureCase):
    def record(self, index: int, fingerprint: str = FINGERPRINT) -> dict:
        return {"id": f"m-old-{index}", "kind": "decode", "subject": "cpu:logo_routing",
                "at": "2026-10-10T10:00:00+07:00", "fingerprint_sha256": fingerprint, "clip": None,
                "wall_seconds": 1.0, "cpu_seconds": None, "value": 50.0, "unit": "x_realtime", "ok": True,
                "error": None, "quality": "clean"}

    def test_records_of_the_same_fingerprint_are_carried_and_the_newest_32_kept(self):
        previous = [self.record(index) for index in range(30)] + [self.record(99, "e" * 64)]
        new = [{**self.record(100 + index), "id": f"m-new-{index}"} for index in range(6)]
        merged = hm.merge_measurements(previous, new, FINGERPRINT)
        self.assertEqual(len(merged), MAX_MEASUREMENTS)
        self.assertEqual(merged[0]["id"], "m-old-4")
        self.assertEqual(merged[-1]["id"], "m-new-5")
        self.assertNotIn("m-old-99", [record["id"] for record in merged])
        self.assertEqual(previous[0]["id"], "m-old-0", "inputs are never changed")

    def test_a_second_run_carries_the_first_runs_records(self):
        self.assertEqual(self.measure(), hm.EXIT_OK)
        second = replace(self.settings(), stamp="20261011-110000-cd34")
        self.assertEqual(hm.run_measurement(second, self.deps()), hm.EXIT_OK)
        ids = [record["id"] for record in self.profile()["measurements"]]
        self.assertEqual(ids[:5], [f"m-{STAMP}-{n}" for n in range(1, 6)])
        self.assertEqual(ids[5:], [f"m-20261011-110000-cd34-{n}" for n in range(1, 6)])

    def test_a_new_fingerprint_starts_the_history_again(self):
        earlier = copy.deepcopy(profile_document("cuda", driver="576.80"))
        earlier["measurements"] = [self.record(index, earlier["fingerprint"]["sha256"]) for index in range(3)]
        earlier = rehash(earlier)
        validate_profile(earlier)
        write_json_atomic(profile_path(self.root, "main"), earlier)
        self.assertEqual(self.measure(), hm.EXIT_OK)
        ids = [record["id"] for record in self.profile()["measurements"]]
        self.assertEqual(ids, [f"m-{STAMP}-{n}" for n in range(1, 6)])


def timing(run: str, field: str, value: object, speed: float, *, quality: str = "clean",
           fingerprint: str = FINGERPRINT, ok: bool = True, unit: str = "x_realtime") -> dict:
    return {"id": f"m-{run}-{value}", "kind": "free_field", "subject": f"{field}={value}",
            "at": "2026-10-11T10:00:00+07:00", "fingerprint_sha256": fingerprint, "clip": None,
            "wall_seconds": 20.0 / speed, "cpu_seconds": None, "value": speed, "unit": unit, "ok": ok,
            "error": None, "quality": quality}


def run_pair(run: str, old: float, new: float, **kwargs) -> list[dict]:
    """One run timing the field at 1 (current) and 3 (candidate)."""
    return [timing(run, "workers", 1, old, **kwargs), timing(run, "workers", 3, new, **kwargs)]


class HysteresisTests(unittest.TestCase):
    def choose(self, history: list[dict], current: object = 1, candidates=(1, 3)) -> object:
        return hm.hysteresis_choice(history, "workers", current, candidates)

    def test_two_consecutive_clean_runs_that_are_clearly_faster_change_the_value(self):
        history = run_pair("a", 1.0, 1.25) + run_pair("b", 1.0, 1.3)
        self.assertEqual(self.choose(history), 3)

    def test_one_run_is_never_enough(self):
        self.assertEqual(self.choose(run_pair("a", 1.0, 2.0)), 1)

    def test_less_than_ten_percent_never_changes(self):
        history = run_pair("a", 1.0, 1.10) + run_pair("b", 1.0, 1.10)  # 9.1% less time
        self.assertEqual(self.choose(history), 1)

    def test_less_than_a_minute_per_reference_film_never_changes(self):
        history = run_pair("a", 200.0, 400.0) + run_pair("b", 200.0, 400.0)  # 50% faster, 22.5 s per film
        self.assertEqual(self.choose(history), 1)
        self.assertEqual(hm.REFERENCE_FILM_SECONDS, 9000)

    def test_contended_and_battery_runs_are_ignored(self):
        clean = run_pair("a", 1.0, 1.5)
        noisy = run_pair("b", 1.0, 1.5, quality="contended") + run_pair("c", 1.0, 1.5, quality="battery")
        self.assertEqual(self.choose(clean + noisy), 1)
        self.assertEqual(self.choose(clean + noisy + run_pair("d", 1.0, 1.5)), 3)

    def test_failed_records_or_other_units_do_not_count(self):
        history = run_pair("a", 1.0, 1.5) + run_pair("b", 1.0, 1.5, ok=False) + run_pair("c", 1.0, 1.5, unit="fps")
        self.assertEqual(self.choose(history), 1)

    def test_a_new_fingerprint_counts_again_from_the_start(self):
        history = run_pair("a", 1.0, 1.5) + run_pair("b", 1.0, 1.5, fingerprint="e" * 64)
        self.assertEqual(self.choose(history), 1)
        self.assertEqual(self.choose(history + run_pair("c", 1.0, 1.5, fingerprint="e" * 64)), 3)

    def test_the_last_run_must_agree_with_the_one_before(self):
        history = run_pair("a", 1.0, 1.5) + run_pair("b", 1.0, 1.05)
        self.assertEqual(self.choose(history), 1)

    def test_changing_back_needs_the_same_margin(self):
        back = run_pair("a", 1.5, 1.0) + run_pair("b", 1.5, 1.0)  # 1 is now the faster value
        self.assertEqual(self.choose(back, current=3), 1)
        close = run_pair("a", 1.05, 1.0) + run_pair("b", 1.05, 1.0)
        self.assertEqual(self.choose(close, current=3), 3)

    def test_an_empty_free_field_never_changes(self):
        history = run_pair("a", 1.0, 2.0) + run_pair("b", 1.0, 2.0)
        gated = next(row for row in hardware_table.ROWS if row.status == "gated")
        self.assertEqual(dict(gated.free_fields), {})
        self.assertEqual(self.choose(history, candidates=gated.free_fields.get("workers", ())), 1)
        self.assertEqual(self.choose(history, current=2), 2)  # a value outside the candidates stays


class ModuleTests(unittest.TestCase):
    def test_importing_the_measurement_loads_no_torch_nvml_psutil_and_starts_no_thread(self):
        code = ("import sys, threading\n"
                "before = threading.active_count()\n"
                "import biliflow.hardware_profile\n"
                "print('biliflow.hardware_measure' in sys.modules)\n"
                "import biliflow.hardware_measure\n"
                "print(threading.active_count() - before)\n"
                "print(*(name in sys.modules for name in ('torch', 'pynvml', 'psutil', 'ctypes',"
                " 'biliflow.job_purge', 'biliflow.visual_logo_scanner')))\n")
        result = run_child("-c", code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode().split(), ["False", "0", "False", "False", "False", "False", "False",
                                                          "False"])

    def test_no_scan_stage_key_reaches_a_hardware_module(self):
        from biliflow.cache_dependencies import stage_source_paths
        from biliflow.stage_cache import CACHEABLE_STAGES
        for stage in sorted(CACHEABLE_STAGES):
            with self.subTest(stage=stage):
                names = {path.name for path in stage_source_paths(CODE, stage)}
                self.assertEqual({name for name in names if name.startswith("hardware_")}, set())

    def test_the_module_command_refuses_bad_arguments_with_64(self):
        cases = (
            ("-m", "biliflow.hardware_profile"),
            ("-m", "biliflow.hardware_profile", "measure"),
            ("-m", "biliflow.hardware_profile", "measure", "--project-root", "relative"),
            ("-m", "biliflow.hardware_profile", "measure", "--project-root", str(CODE / "docs")),
            ("-m", "biliflow.hardware_profile", "measure", "--project-root", str(CODE / "docs"), "--gpu-mutex", "x"),
        )
        for args in cases:
            with self.subTest(args=args[2:]):
                result = run_child(*args)
                self.assertEqual(result.returncode, hm.EXIT_USAGE, result.stderr.decode("utf-8", "replace"))
        self.assertFalse((CODE / "docs" / "temp").exists())
        self.assertFalse((CODE / "docs" / "state").exists())

    def test_the_module_entry_is_a_hand_off_to_hardware_measure(self):
        text = (SRC / "biliflow" / "hardware_profile.py").read_text(encoding="utf-8")
        block = text.split('if __name__ == "__main__":', 1)[1]
        self.assertIn("from biliflow import hardware_measure", block)
        self.assertIn("raise SystemExit(hardware_measure.main())", block)
        self.assertNotIn("import hardware_measure", text.split('if __name__ == "__main__":', 1)[0])


if __name__ == "__main__":
    unittest.main()
