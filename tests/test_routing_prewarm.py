"""Routing prewarm during OCR: child lifecycle, CLI failure handling, routing-only scans."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from biliflow.routing_prewarm import RoutingPrewarm, prewarm_command

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "tools/ffmpeg/bin/ffprobe.exe"


class FakeProcess:
    def __init__(self, code=0, running=False):
        self.code, self.running, self.waited = code, running, False

    def wait(self, timeout=None):
        self.waited = True
        return self.code

    def poll(self):
        return None if self.running else self.code


class RoutingPrewarmTests(unittest.TestCase):
    def test_command_is_routing_only_logo_scan_with_given_settings(self):
        argv = prewarm_command(input_path=Path("m.mp4"), report_dir=Path("reports/x/text"), python="py",
                               settings={"--sample-every": 2.0, "--routing-workers": 3,
                                         "--source-sha256": None, "--decode": "nvdec"})
        self.assertEqual(argv[:4], ["py", "-m", "biliflow", "scan-visual-logo"])
        self.assertIn("--routing-only", argv)
        self.assertEqual(argv[argv.index("--sample-every") + 1], "2.0")
        self.assertEqual(argv[argv.index("--routing-workers") + 1], "3")
        self.assertNotIn("--source-sha256", argv)
        with self.assertRaises(ValueError):
            prewarm_command(input_path=Path("m"), report_dir=Path("r"), settings={"--exhaustive": True})

    def test_finish_waits_and_stop_kills_running_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "prewarm.log"
            process = FakeProcess(code=3)
            options = {}
            prewarm = RoutingPrewarm(["x"], cwd=Path(directory), log_path=log,
                                     popen=lambda *a, **k: options.update(k) or process)
            self.assertEqual(prewarm.finish(), 3)
            self.assertNotIn("creationflags", options)  # normal priority (phase F1 rejected)
            running = FakeProcess(running=True)
            killed = []
            prewarm = RoutingPrewarm(["x"], cwd=Path(directory), log_path=log, popen=lambda *a, **k: running)
            prewarm.stop(terminate=killed.append)
            self.assertEqual(killed, [running])

    def run_cli(self, scan_effect, child_code=0):
        from biliflow import cli
        started, stopped, finished = [], [], []

        class FakePrewarm:
            def __init__(self, argv, **kwargs):
                started.append(argv)

            def finish(self):
                finished.append(True)
                return child_code

            def stop(self):
                stopped.append(True)
        args = ["scan-text", "--input", str(ROOT / "README.md"), "--report-dir", str(ROOT / "reports/x"),
                "--prewarm-logo-routing", "--logo-sample-every", "2.0", "--logo-routing-workers", "3"]
        with mock.patch.object(sys, "argv", ["biliflow", *args]), \
                mock.patch.object(cli, "ensure_model_allowed"), \
                mock.patch.object(cli, "scan_text", side_effect=scan_effect), \
                mock.patch("biliflow.routing_prewarm.RoutingPrewarm", FakePrewarm):
            try:
                code = cli.main()
            except BaseException as error:
                code = error
        return code, started, stopped, finished

    def test_cli_waits_for_child_and_tolerates_its_failure(self):
        payload = {"frames_scanned": 1, "tracks": [],
                   "metrics": {"elapsed_seconds": 1.0, "video_seconds_per_processing_second": 1.0},
                   "review_candidate_count": 0, "routing_counts": {}, "semantic_routing": False}
        code, started, stopped, finished = self.run_cli(lambda **k: payload, child_code=1)
        self.assertEqual(code, 0)
        self.assertEqual((len(started), stopped, finished), (1, [], [True]))
        self.assertIn("--routing-only", started[0])

    def test_cli_stops_child_when_ocr_fails_or_is_interrupted(self):
        for error in (RuntimeError("ocr failed"), KeyboardInterrupt()):
            def fail(**kwargs):
                raise error
            code, _, stopped, finished = self.run_cli(fail)
            self.assertIs(type(code), type(error))
            self.assertEqual((stopped, finished), ([True], []))


@unittest.skipUnless(FFMPEG.exists() and FFPROBE.exists(), "project FFmpeg is required")
class RoutingOnlyScanTests(unittest.TestCase):
    def test_routing_only_writes_cache_not_reports_and_then_hits(self):
        from biliflow.visual_logo_scanner import scan_visual_logos
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            (root / "models/vlm").mkdir(parents=True)
            video = root / "clip.mkv"
            subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=size=320x180:rate=5:duration=12", "-c:v", "ffv1", str(video)], check=True)
            report_dir = root / "reports/job/visual-logo"
            common = dict(project_root=root, input_path=video, report_dir=report_dir,
                          model_path=root / "models/vlm", ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE,
                          boundary_seconds=2.0, routing_only=True)
            with mock.patch("transformers.AutoModelForImageTextToText.from_pretrained",
                            side_effect=AssertionError("VLM must not load")):
                first = scan_visual_logos(**common)
                second = scan_visual_logos(**common)
            self.assertTrue(first["routing_only"])
            self.assertFalse(first["routing_cache"]["hit"])
            self.assertTrue(second["routing_cache"]["hit"])
            self.assertEqual(first["routing_cache"]["key"], second["routing_cache"]["key"])
            self.assertGreater(first["frames_scanned"], 0)
            self.assertTrue((root / first["routing_cache"]["path"]).exists())
            self.assertFalse(report_dir.exists())
            with self.assertRaisesRegex(ValueError, "routing_only"):
                scan_visual_logos(**common, exhaustive=True)

    def test_background_source_check_blocks_cache_on_mismatch(self):
        import hashlib
        from biliflow.visual_logo_scanner import scan_visual_logos
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            (root / "models/vlm").mkdir(parents=True)
            video = root / "clip.mkv"
            subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=size=320x180:rate=5:duration=8", "-c:v", "ffv1", str(video)], check=True)
            common = dict(project_root=root, input_path=video, report_dir=root / "reports/job/visual-logo",
                          model_path=root / "models/vlm", ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE,
                          boundary_seconds=2.0, routing_only=True)
            with self.assertRaisesRegex(ValueError, "Source checksum changed"):
                scan_visual_logos(**common, source_sha256="0" * 64)
            self.assertFalse(list((root / "cache").rglob("routing-*")) if (root / "cache").exists() else [])
            actual = hashlib.sha256(video.read_bytes()).hexdigest()
            result = scan_visual_logos(**common, source_sha256=actual.upper())
            self.assertFalse(result["routing_cache"]["hit"])
            self.assertIn(actual, result["routing_cache"]["path"])
            with self.assertRaisesRegex(ValueError, "64 hexadecimal"):
                scan_visual_logos(**common, source_sha256="xyz")


if __name__ == "__main__":
    unittest.main()
