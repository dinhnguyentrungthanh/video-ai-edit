"""hardware_manager and hardware_status (docs/HARDWARE_PROFILE.md 6.1, 6.3; section 9 "E2, manager").

Fakes only: popen, observe and kill_tree are stand-ins, so no PowerShell, measurement, GPU or NVML
runs here. Every root is a temporary folder under <install>/temp, and every mutex name is
Local\\BiliFlowTest-<uuid>, passed as a constructor or function parameter (I10).
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from biliflow import hardware_manager as hm
from biliflow import hardware_profile as hp
from biliflow import hardware_schema as hs
from biliflow import hardware_status, hardware_table
from biliflow.export_guards import ActionConflict
from biliflow.job_store import JobStore
from biliflow.scheduler import JobScheduler
from tests.hardware_fixtures import (
    AMPERE, BLACKWELL, TORCH, TURING, TempRootCase, observation, profile_document, rehash,
)

NOW = datetime(2026, 10, 11, 9, 30, tzinfo=timezone(timedelta(hours=7)))
IDLE = hm.QueueView(paused=True, active=False, busy=False, in_process=False)
FFMPEG_PATH = "C:/example/tools/ffmpeg/bin/ffmpeg.exe"  # detection.ffmpeg.path of the fixtures
STAMP = r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$"
WINDOWS = sys.platform == "win32"


def fresh_mutex_name() -> str:
    return f"Local\\BiliFlowTest-{uuid.uuid4()}"


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeProcess:
    """Stands in for the PowerShell child: it ends when the test sets ``code`` (or a fake kill does)."""

    def __init__(self, pid: int) -> None:
        self.pid, self.code, self.signals = pid, None, []

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        if not wait_for(lambda: self.code is not None, timeout or 0):
            raise subprocess.TimeoutExpired("powershell", timeout)
        return self.code

    def terminate(self):
        self.signals.append("terminate")

    def kill(self):
        self.signals.append("kill")


class FakePopen:
    def __init__(self) -> None:
        self.calls, self.processes = [], []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        process = FakeProcess(4242 + len(self.processes))
        self.processes.append(process)
        return process


class HeldMutex:
    """Owns a named mutex on a helper thread: a Windows mutex is recursive for the thread that owns it,
    so the code under test (on this thread) would not see it held if this thread took it."""

    def __init__(self, name: str, *, owned: bool = True) -> None:
        self.name, self.owned = name, owned
        self._ready, self._release = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._hold, daemon=True)

    def _hold(self) -> None:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.CreateMutexW(None, self.owned, self.name)
        try:
            self._ready.set()
            self._release.wait(10)
        finally:
            if self.owned:
                kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)

    def __enter__(self) -> "HeldMutex":
        self._thread.start()
        if not self._ready.wait(5):
            raise AssertionError("the helper thread did not create the mutex")
        return self

    def release(self) -> None:
        self._release.set()
        self._thread.join(5)

    def __exit__(self, *_exc) -> None:
        self.release()


def take_and_release(name: str) -> int:
    """WaitForSingleObject(0) on a fresh thread: 0 (taken, then released) or 0x102 (held elsewhere)."""
    import ctypes
    from ctypes import wintypes
    result = []

    def run():
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenMutexW.restype = wintypes.HANDLE
        kernel32.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenMutexW(0x00100000 | 0x0001, False, name)
        waited = kernel32.WaitForSingleObject(handle, 0)
        if waited == 0:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)
        result.append(waited)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(5)
    return result[0]


def strings(value):
    """Every string of a JSON-like value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


class ManagerCase(TempRootCase):
    def setUp(self) -> None:
        super().setUp()
        self.events, self.observed_calls, self.kills, self.managers = [], [], [], []
        self.queue = IDLE
        self.popen = FakePopen()
        self.observation = observation()
        self.script = self.base / "code" / "scripts" / "hardware-measure.ps1"
        self.python = str(self.base / "py" / "python.exe")
        # Runs before the temporary root is removed: every fake child ends, every watcher closes its log.
        self.addCleanup(self.end_measurements)

    def fake_observe(self, root, *, ffmpeg_exe):
        self.observed_calls.append((root, ffmpeg_exe))
        if isinstance(self.observation, Exception):
            raise self.observation
        return self.observation

    def kill(self, pid: int) -> None:
        self.kills.append(pid)
        for process in self.popen.processes:
            if process.pid == pid:
                process.code = 1  # what taskkill /F leaves

    def manager(self, root: Path | None = None, **overrides) -> hm.HardwareManager:
        options = {
            "store_event": lambda kind, message, payload: self.events.append((kind, message, payload)),
            "scheduler_view": lambda: self.queue, "popen": self.popen, "observe": self.fake_observe,
            "kill_tree": self.kill, "mutex_name": fresh_mutex_name(), "python": self.python,
            "script": self.script, "now": lambda: NOW, "poll_seconds": 0.01,
        }
        options.update(overrides)
        manager = hm.HardwareManager(root or self.root, **options)
        self.managers.append(manager)
        self.addCleanup(manager.close)
        return manager

    def end_measurements(self) -> None:
        for process in self.popen.processes:
            if process.code is None:
                process.code = 1
        for manager in self.managers:
            wait_for(lambda: manager._run is None)

    def wait_task(self, manager: hm.HardwareManager) -> dict:
        self.assertTrue(wait_for(lambda: not (manager.task() or {}).get("running", False)), manager.task())
        return manager.task()

    def stamp(self, index: int = -1) -> str:
        command = self.popen.calls[index][0]
        return command[command.index("-Stamp") + 1]


class CanRemeasureTests(ManagerCase):
    def test_remeasure_needs_a_paused_queue_with_nothing_running(self):
        manager = self.manager()
        cases = {
            "not paused": (hm.QueueView(False, False, False, False), "not_paused", hm.NOT_PAUSED_MESSAGE),
            "a stage runs": (hm.QueueView(True, True, False, False), "not_idle", hm.NOT_IDLE_MESSAGE),
            "a job is busy": (hm.QueueView(True, False, True, False), "not_idle", hm.NOT_IDLE_MESSAGE),
            "a job renders": (hm.QueueView(True, False, False, True), "not_idle", hm.NOT_IDLE_MESSAGE),
        }
        for name, (view, reason, text) in cases.items():
            with self.subTest(case=name):
                self.queue = view
                self.assertEqual(manager.can_remeasure(), {"ok": False, "reason": reason, "text": text})
                with self.assertRaises(ActionConflict) as caught:
                    manager.start("remeasure")
                self.assertEqual((caught.exception.code, str(caught.exception)), (reason, text))
        self.assertEqual(self.popen.calls, [])
        self.queue = IDLE
        self.assertEqual(manager.can_remeasure(), {"ok": True, "reason": None, "text": None})
        self.assertEqual(hm.NOT_PAUSED_MESSAGE, "Hãy tạm dừng hàng đợi trước")

    def test_a_queue_that_cannot_be_read_is_never_idle(self):
        for view in (RuntimeError("database is locked"), "odd"):
            with self.subTest(view=view):
                def scheduler_view(view=view):
                    if isinstance(view, Exception):
                        raise view
                    return view
                manager = self.manager(scheduler_view=scheduler_view)
                self.assertEqual(manager.can_remeasure(),
                                 {"ok": False, "reason": "not_idle", "text": hm.QUEUE_UNKNOWN_MESSAGE})
                with self.assertRaises(ActionConflict) as caught:
                    manager.start("remeasure")
                self.assertEqual(caught.exception.code, "not_idle")
        self.assertEqual(self.popen.calls, [])

    def test_only_remeasure_is_an_action(self):
        manager = self.manager()
        for action in ("cpu-only", "Remeasure", None, 1):
            with self.subTest(action=action), self.assertRaises(ValueError):
                manager.start(action)
        self.assertEqual(self.popen.calls, [])

    def test_queue_view_reads_a_real_store_and_scheduler(self):
        store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(store.close)
        scheduler = JobScheduler(self.root, store)  # never started
        self.assertEqual(hm.queue_view(store, scheduler), hm.QueueView(False, False, False, False))
        store.set_setting("scheduler_paused", True)
        self.assertEqual(hm.queue_view(store, scheduler), IDLE)
        source = self.root / "input" / "clip-1.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"x" * 64)
        store.upsert_job(job_key="job-1", source_path=source, source_sha256="a" * 64, source_size_bytes=64,
                         source_mtime_ns=source.stat().st_mtime_ns, state="RENDERING")
        self.assertEqual(hm.queue_view(store, scheduler), hm.QueueView(True, False, False, True))

    def test_queue_view_asks_the_public_is_busy_for_every_job(self):
        asked = []

        class Scheduler:
            active = None

            def is_busy(self, job_id):
                asked.append(job_id)
                return job_id == 2

        class Store:
            def list_jobs(self):
                return [{"id": 1, "state": "QUEUED"}, {"id": 2, "state": "WAITING_REVIEW"}]

            def setting(self, key, default=None):
                return True if key == "scheduler_paused" else default

        self.assertEqual(hm.queue_view(Store(), Scheduler()), hm.QueueView(True, False, True, False))
        self.assertEqual(asked, [1, 2])


class MeasurementTests(ManagerCase):
    def test_remeasure_runs_the_contract_command_once_at_a_time(self):
        manager = self.manager()
        answer = manager.start("remeasure")
        self.assertEqual(len(self.popen.calls), 1)
        command, kwargs = self.popen.calls[0]
        stamp = self.stamp()
        self.assertRegex(stamp, STAMP)
        self.assertTrue(command[0].lower().endswith("\\windowspowershell\\v1.0\\powershell.exe"), command[0])
        self.assertEqual(command[1:], ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                                       str(self.script), "-ProjectRoot", str(self.root), "-GuardRoot", str(self.root),
                                       "-Python", self.python, "-Stamp", stamp, "-LaunchedBy", "control-center"])
        self.assertIs(kwargs["shell"], False)
        self.assertEqual((kwargs["stdin"], kwargs["stderr"], kwargs["cwd"]),
                         (subprocess.DEVNULL, subprocess.STDOUT, str(self.root)))
        self.assertTrue(kwargs["creationflags"] & getattr(subprocess, "CREATE_NO_WINDOW", 0) or not WINDOWS)
        log = self.root / "logs" / "control-center" / f"hardware-measure-{stamp}.log"
        self.assertTrue(log.is_file())
        self.assertEqual(kwargs["stdout"].name, str(log))
        self.assertEqual((answer["task"]["running"], answer["task"]["log"]),
                         (True, f"logs/control-center/hardware-measure-{stamp}.log"))
        self.assertEqual(answer["can_remeasure"]["reason"], "busy")
        with self.assertRaises(ActionConflict) as caught:
            manager.start("remeasure")
        self.assertEqual(caught.exception.code, "busy")
        self.assertEqual(len(self.popen.calls), 1)
        self.assertEqual(self.events[0][0], "HARDWARE_MEASURE_STARTED")
        self.popen.processes[0].code = 0
        task = self.wait_task(manager)
        self.assertEqual((task["exit"], task["outcome"]), (0, "done"))
        self.assertTrue(kwargs["stdout"].closed, "the parent's copy of the log handle is closed")
        manager.start("remeasure")
        self.assertEqual(len(self.popen.calls), 2, "a finished measurement frees the slot")

    def test_the_script_is_this_checkouts_and_a_test_root_also_guards_the_install(self):
        self.assertEqual(hm.MEASURE_SCRIPT, Path(hm.__file__).resolve().parents[2] / "scripts" / "hardware-measure.ps1")
        install = self.base / "install"
        (install / "state").mkdir(parents=True)
        (install / "state" / "control-center.sqlite3").write_bytes(b"")
        python = str(install / ".venv" / "Scripts" / "python.exe")
        self.manager(python=python, script=None).start("remeasure")
        command = self.popen.calls[0][0]
        self.assertEqual(command[command.index("-File") + 1], str(hm.MEASURE_SCRIPT))
        guards = [command[i + 1] for i, item in enumerate(command) if item == "-GuardRoot"]
        self.assertEqual(guards, [str(self.root), str(install)])
        self.assertEqual(command[command.index("-Python") + 1], python)

    def test_the_install_is_guarded_only_when_its_venv_runs_us_and_it_has_a_queue(self):
        install = self.base / "install"
        python = str(install / ".venv" / "Scripts" / "python.exe")
        self.assertIsNone(hm.guard_install(python, self.root), "no state/control-center.sqlite3 there")
        (install / "state").mkdir(parents=True)
        (install / "state" / "control-center.sqlite3").write_bytes(b"")
        self.assertEqual(hm.guard_install(python, self.root), install)
        self.assertIsNone(hm.guard_install(python, install), "the root itself is already guarded")
        self.assertIsNone(hm.guard_install(str(install / "python.exe"), self.root), "not a .venv python")

    def test_a_child_that_cannot_start_is_reported_and_frees_the_slot(self):
        def broken(command, **kwargs):
            kwargs["stdout"].write(b"")
            raise OSError(f"cannot run {self.root}\\x")

        manager = self.manager(popen=broken)
        with self.assertRaises(RuntimeError) as caught:
            manager.start("remeasure")
        self.assertIn("<root>", str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))
        task = manager.task()
        self.assertEqual((task["running"], task["outcome"]), (False, "start_failed"))
        self.assertTrue(manager.can_remeasure()["ok"])

    def test_progress_comes_from_the_run_folder_and_a_bad_file_keeps_the_last_value(self):
        manager = self.manager()
        manager.start("remeasure")
        run_dir = self.root / "temp" / f"hardware-measure-{self.stamp()}"
        run_dir.mkdir(parents=True)
        progress = run_dir / "progress.json"
        progress.write_text(json.dumps({"step": "torch_probe", "index": 3, "total": 9,
                                        "started_at": "2026-10-11T09:30:00+07:00"}), encoding="utf-8")
        self.assertTrue(wait_for(lambda: manager.task()["step"] == "torch_probe"))
        self.assertEqual((manager.task()["index"], manager.task()["total"]), (3, 9))
        for bad in (b'{"step": "ffm', b'{"step": "Bad Step", "index": 4, "total": 9}', b"[1, 2]",
                    b'{"step": "ffmpeg", "index": 10, "total": 9}', b" " * 5000):
            progress.write_bytes(bad)
            time.sleep(0.05)
            self.assertEqual(manager.task()["step"], "torch_probe", bad[:20])
        self.popen.processes[0].code = 0
        self.wait_task(manager)


@unittest.skipUnless(WINDOWS and hm.MEASURE_SCRIPT.is_file(), "the real hardware-measure.ps1 on Windows")
class RealScriptRefusalTests(ManagerCase):
    def test_the_real_script_binds_the_managers_arguments_and_refuses_a_missing_python(self):
        # A refusal path only: the -Python check fails after every other argument was accepted, so the
        # script exits 64 before env.ps1, Python, a lock or a write (section 6.3, 9).
        # A fake install with a queue database: the command carries a second -GuardRoot, as on a test
        # Control Center; its .venv has no python.exe.
        install = self.base / "install"
        (install / "state").mkdir(parents=True)
        (install / "state" / "control-center.sqlite3").write_bytes(b"")
        python = str(install / ".venv" / "Scripts" / "python.exe")
        manager = self.manager(popen=None, script=None, python=python, poll_seconds=0.05)
        manager.start("remeasure")
        self.assertEqual(manager._command("20261011-093000-abcd").count("-GuardRoot"), 2)
        self.assertTrue(wait_for(lambda: not manager.task()["running"], timeout=60), manager.task())
        task = manager.task()
        self.assertEqual((task["exit"], task["outcome"]), (64, "refused"))
        log = (self.root / task["log"]).read_bytes()
        self.assertIn(b"-Python must be an existing", log)
        self.assertFalse((self.root / "temp").exists(), "nothing was written into the root")
        self.assertFalse(hs.profile_path(self.root).exists())


class LimitTests(ManagerCase):
    def test_past_the_limit_the_cancel_file_comes_first_then_the_grace_then_the_kill(self):
        seen = {}

        def kill(pid):
            seen["kill"] = (time.monotonic(), self.cancel_file().is_file())
            self.kill(pid)

        manager = self.manager(kill_tree=kill, limit_seconds=0.05, grace_seconds=0.4)
        manager.start("remeasure")
        self.assertTrue(wait_for(lambda: self.cancel_file().is_file()))
        cancel_seen = time.monotonic()
        task = self.wait_task(manager)
        self.assertEqual(self.kills, [self.popen.processes[0].pid])
        killed_at, cancel_present = seen["kill"]
        self.assertTrue(cancel_present, "the cancel file is written before the tree is killed")
        self.assertGreaterEqual(killed_at - cancel_seen, 0.2, "the grace period comes between them")
        self.assertEqual((task["outcome"], task["exit"]), ("killed", 1))
        self.assertIn("15 phút", task["message"])
        self.assertFalse(self.cancel_file().parent.exists(), "the manager removes only its own empty cancel file")
        self.assertEqual(self.events[-1][2]["ok"], False)

    def test_a_measurement_that_stops_within_the_grace_is_never_killed(self):
        manager = self.manager(limit_seconds=0.05, grace_seconds=5)
        manager.start("remeasure")
        self.assertTrue(wait_for(lambda: self.cancel_file().is_file()))
        self.popen.processes[0].code = 75  # the measurement saw the cancel file and released its locks
        task = self.wait_task(manager)
        self.assertEqual(self.kills, [])
        self.assertEqual((task["outcome"], task["exit"]), ("busy", 75))

    def test_the_limit_is_fifteen_minutes_and_the_grace_sixty_seconds(self):
        self.assertEqual((hm.MEASURE_LIMIT_SECONDS, hm.CANCEL_GRACE_SECONDS), (900, 60))

    def test_a_clock_jump_past_limit_and_grace_still_waits_the_grace_after_the_cancel_file(self):
        clock = FakeClock()
        manager = self.manager(clock=clock, limit_seconds=900, grace_seconds=60)
        manager.start("remeasure")
        clock.advance(5000)  # e.g. the PC slept: both marks are passed at once
        self.assertTrue(wait_for(lambda: self.cancel_file().is_file()))
        time.sleep(0.2)  # many polls at the same clock value
        self.assertEqual(self.kills, [], "the grace counts from the cancel file, not from the limit")
        clock.advance(59)
        time.sleep(0.2)
        self.assertEqual(self.kills, [])
        clock.advance(2)
        self.assertTrue(wait_for(lambda: self.kills == [self.popen.processes[0].pid]))
        self.assertEqual(self.wait_task(manager)["outcome"], "killed")

    def cancel_file(self) -> Path:
        return self.root / "temp" / f"hardware-measure-{self.stamp(0)}" / "cancel"


class CloseTests(ManagerCase):
    def test_close_only_writes_the_cancel_file(self):
        manager = self.manager(limit_seconds=0.05, grace_seconds=0.05)
        manager.start("remeasure")
        process = self.popen.processes[0]
        manager.close()
        cancel = self.root / "temp" / f"hardware-measure-{self.stamp()}" / "cancel"
        self.assertTrue(cancel.is_file())
        time.sleep(0.3)  # well past limit + grace: after close() the tree is never killed
        self.assertEqual((self.kills, process.signals, process.code), ([], [], None))
        self.assertTrue(manager.task()["running"])
        with self.assertRaises(ActionConflict) as caught:
            manager.start("remeasure")
        self.assertEqual(caught.exception.code, "busy")
        process.code = 75
        self.assertEqual(self.wait_task(manager)["outcome"], "busy")

    def test_close_never_raises_and_writes_nothing_without_a_measurement(self):
        idle = self.manager()
        self.assertIsNone(idle.close())
        self.assertFalse((self.root / "temp").exists())
        manager = self.manager()
        manager.start("remeasure")
        (self.root / "temp").write_bytes(b"")  # the run folder cannot be made now
        self.assertIsNone(manager.close())
        broken = mock.Mock(poll=mock.Mock(side_effect=OSError("gone")))
        with manager._lock:
            manager._run = hm._Run(broken, "x", self.root / "temp" / "x", "log", mock.Mock(), 0.0, ("missing", None))
        self.assertIsNone(manager.close())
        with manager._lock:
            manager._run = None
        self.popen.processes[0].code = 75
        self.wait_task(manager)
        (self.root / "temp").unlink()

    def test_a_close_while_start_spawns_still_gets_the_cancel_file(self):
        holder = {}

        def popen(command, **kwargs):
            process = self.popen(command, **kwargs)
            holder["manager"].close()  # close() runs before start() records the run: it sees none
            return process

        manager = holder["manager"] = self.manager(popen=popen, limit_seconds=0.05, grace_seconds=0.05)
        manager.start("remeasure")
        cancel = self.root / "temp" / f"hardware-measure-{self.stamp()}" / "cancel"
        self.assertTrue(cancel.is_file())
        time.sleep(0.3)  # past limit + grace: a closed manager never kills
        self.assertEqual(self.kills, [])
        with self.assertRaises(ActionConflict):
            manager.start("remeasure")
        self.popen.processes[0].code = 75
        self.assertEqual(self.wait_task(manager)["outcome"], "busy")


class KillProcessTreeTests(unittest.TestCase):
    def test_taskkill_of_system32_ends_the_whole_tree_without_a_shell(self):
        with mock.patch.object(hm.subprocess, "run") as run:
            hm.kill_process_tree(1234)
        (command,), options = run.call_args
        self.assertEqual(Path(command[0]).name.lower(), "taskkill.exe")
        self.assertEqual(Path(command[0]).parent.name.lower(), "system32")
        self.assertEqual(command[1:], ["/PID", "1234", "/T", "/F"])
        self.assertEqual((options["shell"], options["check"], options["timeout"]), (False, False, 30.0))
        self.assertIs(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))


@unittest.skipUnless(WINDOWS, "Windows named mutexes")
class PreviousRunTests(ManagerCase):
    def test_a_measurement_left_by_an_earlier_start_is_seen_through_the_mutex(self):
        name, clock = fresh_mutex_name(), FakeClock()
        with HeldMutex(name) as held:
            manager = self.manager(mutex_name=name, clock=clock)
            status = manager.status()
            task = status["task"]
            self.assertEqual((task["running"], task["previous_run"], task["message"]),
                             (True, True, "Đang đo (từ lần chạy trước)"))
            self.assertEqual(status["can_remeasure"], {"ok": False, "reason": "busy",
                                                       "text": "Đang đo (từ lần chạy trước)"})
            with self.assertRaises(ActionConflict) as caught:
                manager.start("remeasure")
            self.assertEqual(caught.exception.code, "busy")
            clock.advance(10)
            self.assertTrue(manager.task()["running"], "still held")
            held.release()
        clock.advance(10)
        task = manager.task()
        self.assertEqual((task["running"], task["outcome"]), (False, "previous_done"))
        self.assertEqual(self.popen.calls, [])
        self.assertIs(hm.measurement_running(name), False, "the probe never created the mutex")

    def test_start_sees_a_measurement_that_began_after_the_manager(self):
        name = fresh_mutex_name()
        manager = self.manager(mutex_name=name)
        self.assertIsNone(manager.task())
        with HeldMutex(name):
            with self.assertRaises(ActionConflict) as caught:
                manager.start("remeasure")
            self.assertEqual((caught.exception.code, str(caught.exception)), ("busy", hm.PREVIOUS_RUN_MESSAGE))
            self.assertTrue(manager.task()["previous_run"])
        self.assertEqual(self.popen.calls, [])

    def test_the_probe_opens_never_creates_and_releases_at_once(self):
        name = fresh_mutex_name()
        self.assertIs(hm.measurement_running(name), False)
        self.assertIs(hm.measurement_running(name), False, "still no such mutex: the probe did not create it")
        with HeldMutex(name, owned=False):  # exists, nobody owns it
            self.assertIs(hm.measurement_running(name), False)
            self.assertEqual(take_and_release(name), 0, "the probe released it: another thread takes it at once")
        with HeldMutex(name):
            self.assertIs(hm.measurement_running(name), True)


class ExitCodeTests(ManagerCase):
    def test_each_exit_code_has_its_outcome_and_the_files_are_read_again(self):
        cases = {0: "done", 10: "failed", 64: "refused", 70: "error", 75: "busy", 124: "timeout", 1: "unknown",
                 3: "unknown"}
        for code, outcome in cases.items():
            with self.subTest(code=code):
                manager = self.manager()
                manager.start("remeasure")
                self.popen.processes[-1].code = code
                task = self.wait_task(manager)
                self.assertEqual((task["exit"], task["outcome"]), (code, outcome))
                if outcome == "unknown":
                    self.assertTrue(task["message"].startswith(f"Lỗi không rõ (mã {code})."), task["message"])
                self.assertIn("Chưa có hồ sơ phần cứng.", task["message"])
                kind, _message, payload = self.events[-1]
                self.assertEqual((kind, payload["ok"], payload["exit"]), ("HARDWARE_MEASURE_FINISHED", code == 0, code))

    def test_a_failed_attempt_names_its_failure_and_a_new_profile_is_noticed(self):
        manager = self.manager()
        manager.start("remeasure")
        self.write("main", profile_document("failed"))
        self.popen.processes[0].code = 10
        task = self.wait_task(manager)
        self.assertIn(hs.FAILURE_MESSAGES["cuda_unavailable"], task["message"])
        self.assertIn("kết quả failed", task["message"])
        manager.start("remeasure")
        self.popen.processes[1].code = 0
        self.assertIn("Hồ sơ không đổi.", self.wait_task(manager)["message"])


class SwitchTests(ManagerCase):
    def test_cpu_only_writes_both_switches_and_keeps_the_untested_one(self):
        switch = hs.UntestedFast(AMPERE.name, AMPERE.compute_capability, "2026-10-11T09:00:00+07:00")
        self.switch(untested_fast=switch)
        manager = self.manager()
        answer = manager.set_cpu_only(True)
        read = hs.read_override(self.root)
        self.assertEqual((read.state, read.cpu_only, read.untested_fast), ("ok", True, switch))
        self.assertEqual(answer["cpu_only"], {"value": True, "valid": True})
        kind, message, payload = self.events[-1]
        self.assertEqual((kind, payload["cpu_only"], payload["untested_fast"]), ("HARDWARE_CPU_ONLY_CHANGED", True, True))
        self.assertIn("Đợt 2", message)
        manager.set_cpu_only(False)
        read = hs.read_override(self.root)
        self.assertEqual((read.cpu_only, read.untested_fast), (False, switch))

    def test_both_switches_are_written_under_the_managers_lock(self):
        manager = self.manager()
        seen, original = [], hm.write_override

        def spy(root, **kwargs):
            seen.append(manager._override_lock.locked())
            return original(root, **kwargs)

        with mock.patch.object(hm, "write_override", spy):
            manager.set_cpu_only(True)
            manager.set_untested_fast(False)
        self.assertEqual(seen, [True, True])

    def test_the_untested_switch_turns_on_only_in_ready_untested_and_binds_the_card(self):
        self.write("main", profile_document("cuda", gpus=(AMPERE,)))
        self.observation = observation((AMPERE,))
        self.switch(cpu_only=False)
        manager = self.manager()
        before = manager.status()
        self.assertEqual(before["status"], "ready_untested")
        self.assertEqual(before["families"]["fast"], {"family": "standard", "capped": True, "tf32": "off",
                                                      "refusal": None})
        answer = manager.set_untested_fast(True)
        read = hs.read_override(self.root)
        self.assertEqual((read.untested_fast.gpu_name, read.untested_fast.compute_capability, read.cpu_only),
                         (AMPERE.name, "8.6", False))
        self.assertEqual(read.untested_fast.enabled_at, NOW.isoformat(timespec="seconds"))
        self.assertEqual(answer["untested_fast"], {"value": True, "applies": True, "gpu": {
            "name": AMPERE.name, "compute_capability": "8.6", "enabled_at": NOW.isoformat(timespec="seconds")}})
        self.assertEqual((answer["families"]["fast"]["family"], answer["families"]["fast"]["tf32"]), ("fast", "off"))
        pairs = [(item["code"], item["text"]) for item in answer["warnings"]]
        self.assertEqual(len(pairs), len(set(pairs)), "each warning once")
        self.assertEqual([code for code, _ in pairs].count("untested_fast_on"), 1)
        kind, message, payload = self.events[-1]
        self.assertEqual((kind, payload["enabled"], payload["gpu_name"]),
                         ("HARDWARE_UNTESTED_FAST_CHANGED", True, AMPERE.name))
        self.assertIn(AMPERE.name, message)

    def test_the_untested_switch_is_refused_outside_ready_untested_and_nothing_is_written(self):
        cases = {
            "ready": (profile_document(), observation(), False),
            "needs_measurement": (None, observation(), False),
            "arch_unsupported": (profile_document("cuda", gpus=(BLACKWELL,)), observation((BLACKWELL,)), False),
            "override_invalid": (profile_document("cuda", gpus=(AMPERE,)), observation((AMPERE,)), True),
        }
        for status, (document, seen, broken) in cases.items():
            with self.subTest(status=status):
                root = self.new_root(status)
                if document is not None:
                    self.write("main", document, root)
                if broken:
                    self.broken_switch(root)
                before = hs.override_path(root).read_bytes() if broken else None
                self.observation = seen
                manager = self.manager(root)
                self.assertEqual(manager.status()["status"], status)
                with self.assertRaises(ActionConflict) as caught:
                    manager.set_untested_fast(True)
                self.assertEqual(caught.exception.code, "not_untested")
                self.assertIn(status, str(caught.exception))
                after = hs.override_path(root).read_bytes() if broken else hs.override_path(root).exists()
                self.assertEqual(after, before if broken else False)
        self.assertEqual(self.events, [])

    def test_turning_the_untested_switch_off_is_always_allowed(self):
        self.write("main", profile_document())
        self.switch(cpu_only=True, untested_fast=hs.UntestedFast(AMPERE.name, "8.6", "2026-10-11T09:00:00+07:00"))
        manager = self.manager()
        answer = manager.set_untested_fast(False)
        read = hs.read_override(self.root)
        self.assertEqual((read.cpu_only, read.untested_fast), (True, None))
        self.assertEqual(answer["untested_fast"], {"value": False, "gpu": None, "applies": False})
        self.assertEqual(self.events[-1][2]["enabled"], False)

    def test_a_broken_switch_file_shows_override_invalid_and_a_switch_rewrites_it(self):
        self.write("main", profile_document())
        self.broken_switch()
        manager = self.manager()
        status = manager.status()
        self.assertEqual((status["status"], status["cpu_only"]), ("override_invalid", {"value": False, "valid": False}))
        answer = manager.set_cpu_only(True)
        read = hs.read_override(self.root)
        self.assertEqual((read.state, read.cpu_only, read.untested_fast), ("ok", True, None))
        self.assertEqual(answer["cpu_only"], {"value": True, "valid": True})
        self.assertIs(self.events[-1][2]["replaced_invalid"], True)

    def test_a_switch_value_must_be_a_bool(self):
        manager = self.manager()
        for value in (1, 0, "true", None, [True]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    manager.set_cpu_only(value)
                with self.assertRaises(ValueError):
                    manager.set_untested_fast(value)
        self.assertFalse(hs.override_path(self.root).exists())
        self.assertEqual(self.events, [])


class StatusTests(ManagerCase):
    def test_a_ready_profile_gives_the_shape_of_section_6_1(self):
        self.write("main", profile_document())
        status = self.manager().status()
        json.dumps(status)
        self.assertEqual((status["status"], status["applies_to_jobs"]), ("ready", False))
        self.assertEqual(status["row"], {"id": "nvidia-sm75-torch2.14.0+cu126", "status": "gated",
                                         "table_version": hardware_table.TABLE_VERSION})
        self.assertEqual(list(status["families"]), ["fast", "standard_ocr8", "standard"])
        self.assertEqual(status["families"]["fast"], {"family": "fast", "capped": False, "tf32": "default",
                                                      "refusal": None, "logo_decode": "nvdec"})
        self.assertEqual(status["families"]["standard"], {"family": "standard", "capped": False, "tf32": "default",
                                                          "refusal": None})
        self.assertEqual(status["reasons"][0]["code"], "row_match")
        codes = [item["code"] for item in status["warnings"]]
        self.assertEqual(codes.count("c1_off_fp32"), 1, "the same family note once")
        self.assertIn("no_golden_gate", codes)
        self.assertEqual(status["cpu_only"], {"value": False, "valid": True})
        self.assertEqual(status["untested_fast"], {"value": False, "gpu": None, "applies": False})
        profile = status["profile"]
        self.assertEqual((profile["present"], profile["source"], profile["result"], profile["failure"]),
                         (True, "main", "ok", None))
        self.assertEqual(profile["power"], {"battery_present": True, "ac_online": True})
        detection = status["detection"]
        self.assertEqual((detection["gpu"], detection["compute_capability"], detection["vram_mib"],
                          detection["driver"], detection["torch"], detection["ram_gib"]),
                         (TURING.name, "7.5", 6144, "617.42", TORCH, 16.0))
        self.assertEqual(detection["ffmpeg"]["encoders"], {"libx264": "ok"})
        self.assertEqual(detection["ffmpeg"]["nvdec"], {"trial": "ok", "framemd5_equal_cpu": True})
        self.assertEqual(detection["ffmpeg"]["nvdec_codecs"], {"h264": "ok", "hevc": "ok", "av1": "ok"})
        self.assertEqual(status["observed"]["gpus"][0]["name"], TURING.name)
        self.assertEqual((status["task"], status["can_remeasure"]), (None, {"ok": True, "reason": None, "text": None}))
        self.assertEqual(self.observed_calls, [(self.root, FFMPEG_PATH)])
        self.assertNotIn("token", set(strings(status)))

    def test_no_profile_gives_needs_measurement_without_detection(self):
        status = self.manager().status()
        self.assertEqual(status["status"], "needs_measurement")
        self.assertEqual((status["profile"]["present"], status["detection"], status["row"]), (False, None, None))
        self.assertEqual(status["families"], {name: {"family": None, "refusal": "needs_measurement"}
                                              for name in hp.FAMILIES})
        self.assertEqual(self.observed_calls, [(self.root, None)])

    def test_the_last_good_profile_gives_its_ffmpeg_path_when_the_main_file_is_broken(self):
        self.write("main", b"{broken")
        self.write("last_good", profile_document())
        status = self.manager().status()
        self.assertEqual((status["status"], status["profile"]["source"]), ("ready_last_good", "last_good"))
        self.assertEqual(self.observed_calls[-1], (self.root, FFMPEG_PATH))

    def test_the_observation_is_kept_for_thirty_seconds(self):
        clock = FakeClock()
        manager = self.manager(clock=clock)
        manager.status()
        manager.status()
        clock.advance(29)
        manager.status()
        self.assertEqual(len(self.observed_calls), 1)
        clock.advance(2)
        manager.status()
        self.assertEqual(len(self.observed_calls), 2)
        self.write("main", profile_document())  # another ffmpeg.exe to observe: asked again at once
        manager.status()
        self.assertEqual(self.observed_calls[-1], (self.root, FFMPEG_PATH))

    def test_status_never_raises(self):
        def check(manager, expected):
            answer = manager.status()
            json.dumps(answer)
            self.assertEqual(answer["status"], expected)
            self.assertIn("task", answer)
            self.assertIn("can_remeasure", answer)
            return answer

        self.write("main", profile_document())
        self.observation = RuntimeError("NVML is gone")
        answer = check(self.manager(), "ready")
        self.assertIsNone(answer["observed"])
        self.assertIn("not_observed", [item["code"] for item in answer["warnings"]])
        self.observation = observation()
        broken = self.new_root("broken")
        self.write("main", b"\xff\xfe not json", broken)
        check(self.manager(broken), "invalid")
        switch = self.new_root("switch")
        self.broken_switch(switch)
        check(self.manager(switch), "override_invalid")
        with mock.patch.object(hp, "resolve", side_effect=RuntimeError("bug")):
            self.assertIn("RuntimeError", check(self.manager(), "invalid")["message"])
        with mock.patch.object(hardware_status, "payload", side_effect=KeyError("detection")):
            check(self.manager(), "invalid")

        def no_queue():
            raise RuntimeError("store closed")

        answer = check(self.manager(scheduler_view=no_queue), "ready")
        self.assertEqual(answer["can_remeasure"], {"ok": False, "reason": "not_idle",
                                                   "text": hm.QUEUE_UNKNOWN_MESSAGE})

    def test_paths_and_the_user_folder_are_replaced_in_every_text(self):
        install = self.base / "install"
        home = Path.home()
        document = copy.deepcopy(profile_document("failed"))
        document["attempt"]["failure"]["message"] = (f"không mở được {self.root}\\state\\x.json; "
                                                     f"{install}\\tools\\y; {home}\\z")
        self.write("main", rehash(document))
        manager = self.manager(python=str(install / ".venv" / "Scripts" / "python.exe"))
        status = manager.status()
        message = status["profile"]["failure"]["message"]
        for label in ("<root>", "<install>", "<user>"):
            self.assertIn(label, message)
        for text in strings(status):
            for path in (self.root, install, home):
                self.assertNotIn(str(path).lower(), text.lower())
                self.assertNotIn(str(path).replace("\\", "/").lower(), text.lower())


if __name__ == "__main__":
    unittest.main()
