"""NVDEC decode selection: verified streams only, decided before any frame."""
import subprocess
import unittest
from pathlib import Path

from biliflow import nvdec

H264 = {"streams": [{"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"}]}


class Result:
    def __init__(self, code, stderr=b""):
        self.returncode, self.stderr = code, stderr


class NvdecTests(unittest.TestCase):
    def resolve(self, requested="nvdec", probe=H264, runner=None):
        calls = []

        def default_runner(command, **kwargs):
            calls.append(command)
            return Result(0)
        effective = nvdec.resolve_backend(requested, probe=probe, ffmpeg_path=Path("ffmpeg"),
                                          input_path=Path("movie.mp4"), start_seconds=12.5,
                                          runner=runner or default_runner)
        return effective, calls

    def test_cpu_filter_is_exactly_the_previous_software_chain(self):
        self.assertEqual(nvdec.sampling_filter(1 / 3, 960, 540, "cpu"),
                         f"fps={1 / 3},scale=960:540:flags=bilinear")
        self.assertEqual(nvdec.input_arguments("cpu"), ())

    def test_nvdec_filter_drops_frames_on_gpu_before_download(self):
        chain = nvdec.sampling_filter(0.5, 320, 180, "nvdec")
        self.assertTrue(chain.startswith("fps=0.5,hwdownload,"))
        self.assertTrue(chain.endswith("format=yuv420p,scale=320:180:flags=bilinear"))
        self.assertEqual(nvdec.input_arguments("nvdec")[:2], ("-hwaccel", "cuda"))

    def test_invalid_backend_rejected(self):
        for value in ("gpu", None, "", 1):
            with self.assertRaises(ValueError):
                nvdec.validate_backend(value)

    def test_cpu_request_never_probes(self):
        (backend, reason), calls = self.resolve("cpu")
        self.assertEqual((backend, reason, calls), ("cpu", None, []))

    def test_verified_stream_probes_one_frame_and_selects_nvdec(self):
        (backend, reason), calls = self.resolve()
        self.assertEqual((backend, reason), ("nvdec", None))
        self.assertIn("-frames:v", calls[0])
        self.assertEqual(calls[0][calls[0].index("-ss") + 1], "12.5")

    def test_unverified_streams_fall_back_without_probing(self):
        for codec, pix in (("hevc", "yuv420p"), ("h264", "yuv420p10le"), ("ffv1", "yuv444p")):
            probe = {"streams": [{"codec_type": "video", "codec_name": codec, "pix_fmt": pix}]}
            (backend, reason), calls = self.resolve(probe=probe)
            self.assertEqual(backend, "cpu")
            self.assertIn("not verified", reason)
            self.assertEqual(calls, [])

    def test_probe_failures_fall_back_to_cpu(self):
        outcomes = [lambda *a, **k: Result(1, b"line\nNo device available\n"),
                    lambda *a, **k: (_ for _ in ()).throw(OSError("missing")),
                    lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffmpeg", 120))]
        for runner in outcomes:
            (backend, reason), _ = self.resolve(runner=runner)
            self.assertEqual(backend, "cpu")
            self.assertIn("NVDEC probe failed", reason)

    def test_logo_frame_command_unchanged_for_cpu_and_gpu_side_for_nvdec(self):
        from unittest import mock
        from biliflow import visual_logo_scanner as scanner
        seen = []

        class Stop(Exception):
            pass

        def fake_popen(command, **kwargs):
            seen.append(command)
            raise Stop
        for backend in ("cpu", "nvdec"):
            with mock.patch.object(scanner.subprocess, "Popen", fake_popen), self.assertRaises(Stop):
                next(scanner._iter_frames(ffmpeg_path=Path("ffmpeg"), input_path=Path("m.mp4"), start=12.0,
                                          duration=30.0, sample_every=2.0, width=320, height=180,
                                          decode_backend=backend))
        cpu, gpu = seen
        self.assertEqual(cpu[cpu.index("-vf") + 1], "fps=0.5,scale=320:180:flags=bilinear")
        self.assertEqual(cpu[3:9], ["error", "-ss", "12.000", "-i", "m.mp4", "-t"])
        self.assertEqual(gpu[4:8], ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
        self.assertTrue(gpu[gpu.index("-vf") + 1].startswith("fps=0.5,hwdownload,"))

    def test_logo_cli_decode_defaults_to_cpu(self):
        from biliflow.cli import build_parser
        args = ["scan-visual-logo", "--input", "movie.mp4", "--report-dir", "reports/x"]
        self.assertEqual(build_parser().parse_args(args).decode, "cpu")

    def test_cli_decode_defaults_to_cpu(self):
        from biliflow.cli import build_parser
        args = ["scan-text", "--input", "movie.mp4", "--report-dir", "reports/x"]
        parser = build_parser()
        self.assertEqual(parser.parse_args(args).decode, "cpu")
        self.assertEqual(parser.parse_args(args + ["--decode", "nvdec"]).decode, "nvdec")


if __name__ == "__main__":
    unittest.main()
