"""FFmpeg trials and the child-process runner of the hardware measurement (HARDWARE_PROFILE.md 6.3, 9).

Every FFmpeg here is a fake (``FakeTools``): no encode, no NVENC, no NVDEC, no GPU. The fake checks
that each command is an argv list started with ``shell=False``, ``stdin=DEVNULL`` and
``CREATE_NO_WINDOW``, and writes the output file a real FFmpeg would. The routing chain is compared
with the argv ``visual_logo_scanner._iter_frames`` builds in production.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import hardware_tools as tools
from biliflow import hardware_trials as ht
from biliflow import nvdec, recycle_bin
from biliflow.hardware_detect import ENCODERS
from biliflow.hardware_schema import NVDEC_CODECS
from biliflow.hardware_system import NOT_FOUND, CommandResult

TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
LISTING = (
    "Encoders:\n V..... = Video\n ------\n"
    " V....D libx264              libx264 H.264 / AVC (codec h264)\n"
    " V....D libx265              libx265 H.265 / HEVC (codec hevc)\n"
    " V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)\n"
    " V....D hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)\n"
    " V....D av1_nvenc            NVIDIA NVENC av1 encoder (codec av1)\n"
)
PROBE = {"streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "width": 1920,
                      "height": 1080}], "format": {"duration": "20.000000"}}
FRAMES = ("0,          0,          0,        1,   172800, 2d91b057004c4ec547f19f1fb580cfd1",
          "0,          1,          1,        1,   172800, 5eb49c8b24e78df116536bdfe9f82cfa")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProcess:
    def __init__(self, tools_: "FakeTools", argv: list[str], returncode: int, stdout: bytes, stderr: bytes,
                 hang: int, seconds: float):
        self.tools, self.argv = tools_, argv
        self.pid = 4242
        self.returncode: int | None = None
        self._result = (returncode, stdout, stderr)
        self.hang, self.seconds = hang, seconds
        self.killed = False

    def communicate(self, timeout=None):
        if self.killed:
            self.returncode = 1
            return b"", b""
        if self.hang:
            self.hang -= 1
            self.tools.clock.advance(timeout or 0)
            self.tools.waiting(self)
            raise subprocess.TimeoutExpired(self.argv, timeout)
        self.tools.clock.advance(self.seconds)
        self.returncode = self._result[0]
        return self._result[1], self._result[2]

    def kill(self) -> None:
        self.killed = True
        self.tools.killed.append(self.tools.kind(self.argv))


class FakeTools:
    """A scripted FFmpeg/ffprobe: ``fail`` kinds exit 1, ``hang`` kinds never finish on their own."""

    def __init__(self, *, root: Path, clock: FakeClock | None = None, fail=(), hang=(), differ=(),
                 listing: str = LISTING, probe: dict | None = None, on_start=None, on_wait=None,
                 seconds: float = 2.0):
        self.root, self.clock = root, clock or FakeClock()
        self.fail, self.hang, self.differ = set(fail), set(hang), set(differ)
        self.listing, self.probe = listing, PROBE if probe is None else probe
        self.on_start, self.on_wait, self.seconds = on_start, on_wait, seconds
        self.calls: list[list[str]] = []
        self.kinds: list[str] = []
        self.killed: list[str] = []

    @staticmethod
    def kind(argv: list[str]) -> str:
        program = PureWindowsPath(argv[0]).name.lower()
        out = PureWindowsPath(argv[-1]).name
        if program == "ffprobe.exe":
            return "probe"
        if "-encoders" in argv:
            return "listing"
        if "lavfi" in argv:
            return "clip"
        if "-frames:v" in argv:
            return "nvdec_probe"
        if out.endswith(".framemd5"):
            prefix, *rest = out[:-len(".framemd5")].split("-")
            return f"{'routing' if prefix == 'routing' else 'decode'}:{rest[0]}:{rest[1]}"
        if argv[-3:-1] == ["-f", "null"]:
            return f"validate:{PureWindowsPath(argv[argv.index('-i') + 1]).stem.split('-', 1)[1]}"
        encoder = argv[argv.index("-c:v") + 1]
        return f"{'encode' if out.startswith('encode-') else 'codec_clip'}:{encoder}"

    def waiting(self, process: FakeProcess) -> None:
        if self.on_wait is not None:
            self.on_wait(self.kind(process.argv))

    def __call__(self, argv, **kwargs):
        assert type(argv) is list and all(type(part) is str for part in argv), "an argv list of strings"
        assert kwargs.get("shell") is False and kwargs.get("stdin") == subprocess.DEVNULL
        assert kwargs.get("stdout") == subprocess.PIPE and kwargs.get("stderr") == subprocess.PIPE
        assert kwargs.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert "-y" not in argv and "-nostdin" not in argv
        kind = self.kind(argv)
        self.calls.append(list(argv))
        self.kinds.append(kind)
        if self.on_start is not None:
            self.on_start(kind)
        if kind in self.fail:
            message = f"[out#0] Error opening {Path(self.root) / 'temp' / 'x.mp4'}: no device for {kind}\n"
            return FakeProcess(self, argv, 1, b"", f"first line\n{message}".encode(), 0, self.seconds)
        return FakeProcess(self, argv, 0, self._output(kind, argv), b"", 999 if kind in self.hang else 0,
                           self.seconds)

    def _output(self, kind: str, argv: list[str]) -> bytes:
        if kind == "probe":
            return json.dumps(self.probe).encode()
        if kind == "listing":
            return self.listing.encode()
        if kind.startswith(("routing:", "decode:")):
            lines = list(FRAMES)
            if kind.endswith(":nvdec") and kind.rsplit(":", 1)[0] in self.differ:
                lines[-1] = lines[-1][:-4] + "ffff"
            Path(argv[-1]).write_text("#format: frame checksums\n#software: Lavf\n" + "\n".join(lines) + "\n",
                                      encoding="utf-8")
        elif kind in ("clip",) or kind.startswith(("encode:", "codec_clip:")):
            Path(argv[-1]).write_bytes(b"synthetic " + kind.encode())
        return b""


def ffmpeg_detection(*, ok: bool = True, hwaccels=("cuda", "dxva2", "qsv"),
                     listed=("libx264", "h264_nvenc", "hevc_nvenc", "av1_nvenc", "h264_qsv")) -> dict:
    return {
        "ok": ok, "error": None if ok else "ffmpeg -version: thoát mã 1", "path": "C:/example/ffmpeg.exe",
        "version": "9.0.1", "sha256": "a" * 64, "size": 1, "mtime_ns": 1, "hwaccels_listed": list(hwaccels),
        "encoders": {name: {"listed": name in listed, "trial": "not_run", "error": None} for name in ENCODERS},
        "nvdec": {"listed": "cuda" in hwaccels, "trial": "not_run", "framemd5_equal_cpu": None,
                  "chain": "logo_routing"},
        "nvdec_codecs": {codec: {"trial": "not_run", "error": None} for codec in NVDEC_CODECS},
    }


class TrialCase(unittest.TestCase):
    def setUp(self) -> None:
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "root"
        self.run_dir = self.root / "temp" / "hardware-measure-20261011-100000-ab12"
        self.run_dir.mkdir(parents=True)
        self.ffmpeg_dir = self.root / "tools" / "ffmpeg" / "bin"
        self.steps: list[str] = []
        self.notes: list[str] = []

    def context(self, fake: FakeTools) -> ht.TrialContext:
        guard = tools.RunGuard(self.run_dir, clock=fake.clock)
        runner = tools.ToolRunner(guard, popen=fake, kill=lambda process: process.kill())
        replacements = ((str(self.root), "<root>"),)
        return ht.TrialContext(runner, self.ffmpeg_dir / "ffmpeg.exe", self.ffmpeg_dir / "ffprobe.exe",
                               self.run_dir, replacements, self.steps.append, self.notes.append)

    def fake(self, **kwargs) -> FakeTools:
        return FakeTools(root=self.root, **kwargs)


class ToolRunnerTests(TrialCase):
    def runner(self, fake: FakeTools, **kwargs) -> tools.ToolRunner:
        guard = tools.RunGuard(self.run_dir, clock=fake.clock)
        if "budget" in kwargs:
            guard.start_budget(kwargs.pop("budget"))
        return tools.ToolRunner(guard, popen=fake, kill=lambda process: process.kill(), **kwargs)

    def test_a_finished_command_returns_its_code_and_output(self):
        fake = self.fake()
        result = self.runner(fake).run([str(self.ffmpeg_dir / "ffmpeg.exe"), "-hide_banner", "-encoders"],
                                       timeout=30)
        self.assertEqual((result.returncode, result.ok, result.timed_out), (0, True, False))
        self.assertIn(b"libx264", result.stdout)
        self.assertEqual(fake.killed, [])

    def test_a_command_past_its_own_timeout_is_killed_and_reported(self):
        fake = self.fake(hang={"listing"})
        result = self.runner(fake).run([str(self.ffmpeg_dir / "ffmpeg.exe"), "-encoders"], timeout=5)
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.returncode)
        self.assertEqual(fake.killed, ["listing"])

    def test_the_cancel_file_stops_a_waiting_command_within_a_second(self):
        fake = self.fake(hang={"listing"},
                         on_wait=lambda kind: (self.run_dir / "cancel").write_bytes(b""))
        with self.assertRaises(tools.Cancelled):
            self.runner(fake).run([str(self.ffmpeg_dir / "ffmpeg.exe"), "-encoders"], timeout=60)
        self.assertEqual(fake.killed, ["listing"])
        self.assertLessEqual(fake.clock.now - 1000.0, 1.0)

    def test_the_budget_stops_a_waiting_command(self):
        fake = self.fake(hang={"listing"})
        with self.assertRaises(tools.BudgetExceeded):
            self.runner(fake, budget=3).run([str(self.ffmpeg_dir / "ffmpeg.exe"), "-encoders"], timeout=60)
        self.assertEqual(fake.killed, ["listing"])

    def test_an_interrupt_while_waiting_still_kills_the_child_before_it_propagates(self):
        def interrupt(kind):
            raise KeyboardInterrupt  # Ctrl+C in a hand-run script never reaches a CREATE_NO_WINDOW child

        fake = self.fake(hang={"listing"}, on_wait=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            self.runner(fake).run([str(self.ffmpeg_dir / "ffmpeg.exe"), "-encoders"], timeout=60)
        self.assertEqual(fake.killed, ["listing"])

    def test_nothing_starts_after_a_cancel(self):
        fake = self.fake()
        (self.run_dir / "cancel").write_bytes(b"")
        with self.assertRaises(tools.Cancelled):
            self.runner(fake).run([str(self.ffmpeg_dir / "ffmpeg.exe"), "-encoders"], timeout=30)
        self.assertEqual(fake.calls, [])

    def test_a_missing_program_is_reported_not_raised(self):
        def missing(argv, **kwargs):
            raise FileNotFoundError(2, "missing")
        guard = tools.RunGuard(self.run_dir)
        result = tools.ToolRunner(guard, popen=missing).run(["C:/nowhere/ffmpeg.exe"], timeout=5)
        self.assertEqual(result.error, NOT_FOUND)

    def test_a_shell_string_or_an_endless_command_is_refused(self):
        runner = self.runner(self.fake())
        with self.assertRaises(TypeError):
            runner.run("ffmpeg -encoders", timeout=5)
        for timeout in (0, float("inf"), None):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                runner.run(["ffmpeg.exe", "-encoders"], timeout=timeout)

    def test_the_guarded_system_answers_like_windows_system(self):
        fake = self.fake(hang={"probe"})
        system = tools.GuardedSystem(mock.Mock(cpu=lambda: "inner cpu"), self.runner(fake))
        listing = system.run([str(self.ffmpeg_dir / "ffmpeg.exe"), "-encoders"], timeout=30)
        stuck = system.run([str(self.ffmpeg_dir / "ffprobe.exe"), "x"], timeout=3)
        self.assertIsInstance(listing, CommandResult)
        self.assertEqual(listing.returncode, 0)
        self.assertIn("libx264", listing.stdout)
        self.assertEqual((stuck.returncode, stuck.timed_out), (None, True))
        self.assertEqual(system.cpu(), "inner cpu")

    @unittest.skipUnless(sys.platform == "win32", "Windows process tree")
    def test_kill_process_tree_ends_the_launcher_and_its_child(self):
        import psutil
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                   stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.addCleanup(lambda: process.poll() is None and process.kill())
        children: list = []
        for _attempt in range(50):  # the venv launcher starts the real interpreter a moment later
            children = psutil.Process(process.pid).children(recursive=True)
            if children or Path(sys.executable).parent.name.lower() != "scripts":
                break
            time.sleep(0.1)
        tools.kill_process_tree(process)
        process.wait(10)
        _gone, alive = psutil.wait_procs(children, timeout=10)
        if Path(sys.executable).parent.name.lower() == "scripts":
            self.assertNotEqual(children, [], "the venv python.exe starts the interpreter as its child")
        self.assertEqual(alive, [])


class CommandTests(TrialCase):
    def test_the_routing_chain_is_the_scanner_argv_with_a_framemd5_output(self):
        from biliflow import visual_logo_scanner as scanner
        seen: list[list[str]] = []

        class Stop(Exception):
            pass

        def capture(command, **kwargs):
            seen.append([str(part) for part in command])
            raise Stop
        clip, out = self.run_dir / "clip.mp4", self.run_dir / "out.framemd5"
        for backend in ("cpu", "nvdec"):
            with self.subTest(backend=backend):
                with mock.patch.object(scanner.subprocess, "Popen", capture), self.assertRaises(Stop):
                    next(scanner._iter_frames(ffmpeg_path=Path("ffmpeg.exe"), input_path=clip, start=7.0,
                                              duration=10.0, sample_every=0.25, width=320, height=180,
                                              decode_backend=backend))
                production = seen[-1]
                measured = ht.routing_command(Path("ffmpeg.exe"), clip, out, start=7.0, duration=10.0,
                                              sample_every=0.25, backend=backend)
                self.assertEqual(measured[:-5], production[:-5])
                self.assertEqual(production[-5:], ["-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
                self.assertEqual(measured[-5:], ["-f", "framemd5", "-pix_fmt", "rgb24", str(out)])

    def test_the_libx264_trial_uses_the_export_settings(self):
        # The export's x264 arguments live in export_encoder since perf plan A3.
        from biliflow.export_encoder import video_arguments

        export = video_arguments("libx264", maxrate=None, target_bitrate=None)
        self.assertEqual(export, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
        command = ht.encode_command(Path("ffmpeg.exe"), Path("clip.mp4"), Path("out.mp4"), "libx264")
        start = command.index("-c:v")
        self.assertEqual(command[start:start + 6], export)
        self.assertEqual(command[command.index("-t") + 1], "5.000")

    def test_the_clip_is_the_fixed_synthetic_source(self):
        command = ht.clip_command(Path("ffmpeg.exe"), self.run_dir / "clip.mp4")
        source = command[command.index("-i") + 1]
        self.assertEqual(source, "testsrc2=size=1920x1080:rate=30:duration=20")
        self.assertIn("noise=", command[command.index("-vf") + 1])
        for pair in (["-c:v", "libx264"], ["-pix_fmt", "yuv420p"], ["-threads", "1"]):
            self.assertEqual(command[command.index(pair[0]):command.index(pair[0]) + 2], pair)
        self.assertEqual(command[-1], str(self.run_dir / "clip.mp4"))
        self.assertNotIn("input", Path(command[-1]).parts)

    def test_codec_decodes_use_the_production_nvdec_input_arguments(self):
        command = ht.codec_decode_command(Path("ffmpeg.exe"), Path("c.mkv"), Path("o.framemd5"), "nvdec")
        self.assertEqual(tuple(command[4:8]), nvdec.NVDEC_INPUT_ARGS)
        self.assertEqual(command[command.index("-vf") + 1], "hwdownload,format=nv12,format=yuv420p")
        self.assertNotIn("-hwaccel", ht.codec_decode_command(Path("ffmpeg.exe"), Path("c.mkv"), Path("o"), "cpu"))

    def test_framemd5_comparison_reads_data_lines_only(self):
        first, second = self.run_dir / "a.framemd5", self.run_dir / "b.framemd5"
        first.write_text("#software: Lavf61\n" + "\n".join(FRAMES) + "\n", encoding="utf-8")
        second.write_text("#software: other build\n" + "\n".join(FRAMES) + "\n", encoding="utf-8")
        self.assertIsNone(ht.first_difference(ht.framemd5_lines(first), ht.framemd5_lines(second)))
        self.assertEqual(ht.first_difference(list(FRAMES), [FRAMES[0], "different"]), 1)
        self.assertEqual(ht.first_difference(list(FRAMES), [FRAMES[0]]), 1)
        self.assertEqual(ht.first_difference([], []), 0)
        self.assertEqual(ht.framemd5_lines(self.run_dir / "missing.framemd5"), [])

    def test_failure_text_is_the_last_line_without_local_paths(self):
        result = tools.ToolResult(1, stderr=f"line one\nError in {self.root}\\temp\\clip.mp4\n".encode())
        text = ht.failure_text(result, ((str(self.root), "<root>"),))
        self.assertEqual(text, "Error in <root>\\temp\\clip.mp4")
        self.assertEqual(ht.failure_text(tools.ToolResult(3), ()), "thoát mã 3")
        self.assertEqual(ht.failure_text(tools.ToolResult(None, timed_out=True), ()), "quá giờ")
        self.assertIsNone(ht.failure_text(tools.ToolResult(0), ()))
        long = tools.ToolResult(1, stderr=("x" * 500 + " https://build.example/a").encode())
        self.assertLessEqual(len(ht.failure_text(long, ())), 300)


class NvdecProbeRunnerTests(TrialCase):
    def resolve(self, fake: FakeTools):
        context = self.context(fake)
        return nvdec.resolve_backend("nvdec", probe=PROBE, ffmpeg_path=context.ffmpeg,
                                     input_path=self.run_dir / "clip.mp4", start_seconds=0.0,
                                     runner=ht.nvdec_probe_runner(context.runner))

    def test_the_production_probe_runs_through_the_runner(self):
        fake = self.fake()
        self.assertEqual(self.resolve(fake), ("nvdec", None))
        self.assertEqual(fake.kinds, ["nvdec_probe"])
        probe = fake.calls[0]
        self.assertEqual(probe[probe.index("-ss") + 1], "0.0")

    def test_a_failing_probe_falls_back_with_ffmpeg_last_line(self):
        backend, reason = self.resolve(self.fake(fail={"nvdec_probe"}))
        self.assertEqual(backend, "cpu")
        self.assertIn("NVDEC probe failed: [out#0] Error opening", reason)

    def test_a_probe_past_its_timeout_falls_back(self):
        backend, reason = self.resolve(self.fake(hang={"nvdec_probe"}))
        self.assertEqual((backend, reason), ("cpu", "NVDEC probe failed: TimeoutExpired"))

    def test_a_cancel_passes_through_resolve_backend(self):
        fake = self.fake(hang={"nvdec_probe"}, on_wait=lambda kind: (self.run_dir / "cancel").write_bytes(b""))
        with self.assertRaises(tools.Cancelled):
            self.resolve(fake)


class TrialTests(TrialCase):
    def run_trials(self, fake: FakeTools, detection: dict | None = None) -> ht.TrialReport:
        return ht.run_trials(self.context(fake), detection or ffmpeg_detection())

    def test_a_full_run_records_every_trial(self):
        fake = self.fake(fail={"encode:av1_nvenc"})
        report = self.run_trials(fake)
        self.assertEqual(self.steps, ["clip", "encoders", "nvdec_codecs", "logo_routing"])
        self.assertEqual(len(report.clip_sha256), 64)
        trials = {name: entry["trial"] for name, entry in report.encoders.items()}
        self.assertEqual(trials, {"libx264": "ok", "h264_nvenc": "ok", "hevc_nvenc": "ok", "av1_nvenc": "failed",
                                  "h264_qsv": "not_run", "hevc_qsv": "not_run", "h264_amf": "not_run",
                                  "hevc_amf": "not_run"})
        self.assertIn("<root>", report.encoders["av1_nvenc"]["error"])
        self.assertNotIn(str(self.root), report.encoders["av1_nvenc"]["error"])
        self.assertEqual({codec: entry["trial"] for codec, entry in report.nvdec_codecs.items()},
                         {"h264": "ok", "hevc": "ok", "av1": "not_run"})
        self.assertIn("libsvtav1, libaom-av1, librav1e", report.nvdec_codecs["av1"]["error"])
        self.assertEqual(report.nvdec, {"listed": True, "trial": "ok", "framemd5_equal_cpu": True,
                                        "chain": "logo_routing"})
        subjects = [(timing.kind, timing.subject, timing.ok) for timing in report.timings]
        self.assertEqual(subjects, [("encode", "libx264:veryfast:crf20", True), ("encode", "h264_nvenc", True),
                                    ("encode", "hevc_nvenc", True), ("encode", "av1_nvenc", False),
                                    ("decode", "cpu:logo_routing", True), ("decode", "nvdec:logo_routing", True)])
        self.assertEqual(report.timings[0].unit, "fps")
        self.assertAlmostEqual(report.timings[0].value, 150 / 2.0)
        self.assertAlmostEqual(report.timings[4].value, 50 / 6.0, places=3)

    def test_the_hevc_clip_is_made_with_libx265_and_av1_with_a_listed_software_encoder(self):
        listing = LISTING + " V....D libaom-av1           libaom AV1 (codec av1)\n"
        fake = self.fake(listing=listing)
        report = self.run_trials(fake)
        self.assertIn("codec_clip:libx265", fake.kinds)
        self.assertIn("codec_clip:libaom-av1", fake.kinds)
        self.assertEqual(report.nvdec_codecs["av1"], {"trial": "ok", "error": None})

    def test_an_nvdec_decode_that_differs_from_the_cpu_fails_that_codec_only(self):
        report = self.run_trials(self.fake(differ={"decode:hevc"}))
        self.assertEqual(report.nvdec_codecs["hevc"]["trial"], "failed")
        self.assertIn("framemd5", report.nvdec_codecs["hevc"]["error"])
        self.assertEqual(report.nvdec_codecs["h264"]["trial"], "ok")

    def test_an_nvdec_decode_error_fails_and_a_cpu_decode_error_does_not_run(self):
        report = self.run_trials(self.fake(fail={"decode:h264:nvdec", "decode:hevc:cpu"}))
        self.assertEqual(report.nvdec_codecs["h264"]["trial"], "failed")
        self.assertEqual(report.nvdec_codecs["hevc"]["trial"], "not_run")

    def test_routing_frames_that_differ_give_a_trial_ok_with_framemd5_false(self):
        report = self.run_trials(self.fake(differ={"routing:seek"}))
        self.assertEqual((report.nvdec["trial"], report.nvdec["framemd5_equal_cpu"]), ("ok", False))

    def test_a_failing_nvdec_probe_fails_the_self_check_without_nvdec_decodes(self):
        fake = self.fake(fail={"nvdec_probe"})
        report = self.run_trials(fake)
        self.assertEqual((report.nvdec["trial"], report.nvdec["framemd5_equal_cpu"]), ("failed", None))
        self.assertFalse(any(kind.startswith("routing:") and kind.endswith(":nvdec") for kind in fake.kinds))
        self.assertTrue(any("Tự kiểm NVDEC lỗi" in note for note in self.notes))

    def test_a_clip_that_is_not_h264_yuv420p_never_tests_nvdec(self):
        probe = {"streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv444p"}]}
        fake = self.fake(probe=probe)
        report = self.run_trials(fake)
        self.assertEqual(report.nvdec["trial"], "not_run")
        self.assertNotIn("nvdec_probe", fake.kinds)

    def test_no_cuda_hwaccel_runs_no_nvdec_command(self):
        fake = self.fake()
        report = self.run_trials(fake, ffmpeg_detection(hwaccels=("dxva2",)))
        self.assertEqual(report.nvdec["trial"], "not_run")
        self.assertEqual({entry["trial"] for entry in report.nvdec_codecs.values()}, {"not_run"})
        self.assertFalse(any(any(part == "cuda" for part in call) for call in fake.calls))
        self.assertEqual([timing.subject for timing in report.timings if timing.kind == "decode"],
                         ["cpu:logo_routing"])

    def test_a_failed_clip_leaves_every_trial_not_run_with_the_reason(self):
        fake = self.fake(fail={"clip"})
        report = self.run_trials(fake)
        self.assertIsNone(report.clip_sha256)
        self.assertEqual(report.timings, ())
        self.assertEqual(fake.kinds, ["clip"])
        self.assertEqual({entry["trial"] for entry in report.encoders.values()}, {"not_run"})
        self.assertIn("clip tổng hợp", report.encoders["libx264"]["error"])
        self.assertIsNone(report.encoders["hevc_qsv"]["error"])
        self.assertEqual(report.nvdec["trial"], "not_run")

    def test_no_usable_ffmpeg_runs_nothing(self):
        fake = self.fake()
        report = self.run_trials(fake, ffmpeg_detection(ok=False))
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.steps, [])
        self.assertEqual(report.timings, ())

    def test_an_encoder_that_is_not_listed_is_not_tried(self):
        fake = self.fake()
        report = self.run_trials(fake, ffmpeg_detection(listed=("libx264",)))
        self.assertEqual(report.encoders["av1_nvenc"], {"listed": False, "trial": "not_run", "error": None})
        self.assertFalse(any(kind.startswith("encode:") and kind != "encode:libx264" for kind in fake.kinds))

    def test_a_failed_validation_fails_the_encoder(self):
        report = self.run_trials(self.fake(fail={"validate:h264_nvenc"}))
        self.assertEqual(report.encoders["h264_nvenc"]["trial"], "failed")
        self.assertIn("kiểm tra giải mã", report.encoders["h264_nvenc"]["error"])


if __name__ == "__main__":
    unittest.main()
