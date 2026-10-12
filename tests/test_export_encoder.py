"""The export's video encoder (perf plan A3): "auto" by default, NVENC (p5, -cq 25) where it works, x264 otherwise."""
import contextlib
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.export_encoder import (
    AUTO,
    H264_NVENC,
    LIBX264,
    NOT_ASKED,
    NVENC_CQ,
    NVENC_OPTIONS,
    NVENC_PRESET,
    PROFILE_FAILED,
    PROFILE_OK,
    TRIAL_DID_NOT_FINISH,
    TRIAL_FAILED,
    TRIAL_OK,
    choose_video_encoder,
    error_fallback,
    failed_by_itself,
    profile_nvenc_trial,
    requested_encoder,
    size_fallback,
    trial_command,
    trial_nvenc,
    video_arguments,
    video_description,
)
from biliflow.hardware_schema import ProfileRead
from biliflow.install_paths import MARKER_NAME

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = Path(os.environ.get("BILIFLOW_FFMPEG") or ROOT / "tools/ffmpeg/bin/ffmpeg.exe")
GPU_SLOT = "Local\\BiliFlowGpuInference"
GPU_SLOT_WAIT_SECONDS = 120


def marker(folder: Path) -> None:
    (folder / MARKER_NAME).write_text(json.dumps({"biliflow_install": 1}), encoding="utf-8")


class RequestTests(unittest.TestCase):
    def test_a_plan_without_the_setting_asks_for_auto(self):
        # The user's approval of 2026-10-12: every export chooses its encoder.
        for plan in ({}, {"render_settings": {}}, {"render_settings": {"other": 1}}):
            with self.subTest(plan=plan):
                self.assertEqual(requested_encoder(plan), AUTO)

    def test_a_plan_asks_for_x264_or_auto_and_nothing_else(self):
        self.assertEqual(requested_encoder({"render_settings": {"video_encoder": "libx264"}}), LIBX264)
        self.assertEqual(requested_encoder({"render_settings": {"video_encoder": "auto"}}), "auto")
        # NVENC is never forced: "auto" falls back to x264 where NVENC does not work.
        for value in ("h264_nvenc", "nvenc", "", None, 1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "video encoder"):
                    requested_encoder({"render_settings": {"video_encoder": value}})
        with self.assertRaisesRegex(ValueError, "render_settings"):
            requested_encoder({"render_settings": "auto"})


class ProfileTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.ffmpeg = self.root / "ffmpeg.exe"
        self.ffmpeg.write_bytes(b"ffmpeg")
        info = self.ffmpeg.stat()
        self.detection = {
            "ok": True, "path": str(self.ffmpeg), "size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "encoders": {"libx264": {"listed": True, "trial": "ok", "error": None},
                         H264_NVENC: {"listed": True, "trial": "ok", "error": None}},
        }

    def read(self, state="ok", detection=None):
        document = {"detection": {"ffmpeg": self.detection if detection is None else detection}}
        read = ProfileRead(state, document=document if state == "ok" else None)
        with patch("biliflow.hardware_schema.read_profile", return_value=read) as reader:
            result = profile_nvenc_trial(self.root, self.ffmpeg)
        reader.assert_called_once_with(self.root)
        return result

    def test_the_profile_trial_of_this_ffmpeg_is_used(self):
        self.assertEqual(self.read(), "ok")
        self.detection["encoders"][H264_NVENC]["trial"] = "failed"
        self.assertEqual(self.read(), "failed")

    def test_no_result_when_the_profile_has_none_for_this_ffmpeg(self):
        cases = {
            "not run": {**self.detection, "encoders": {H264_NVENC: {"listed": True, "trial": "not_run"}}},
            "not listed": {**self.detection, "encoders": {}},
            "ffmpeg not ok": {**self.detection, "ok": False},
            "another size": {**self.detection, "size": self.detection["size"] + 1},
            "another time": {**self.detection, "mtime_ns": self.detection["mtime_ns"] + 1},
            "another path": {**self.detection, "path": str(self.root / "other" / "ffmpeg.exe")},
            "no path": {key: value for key, value in self.detection.items() if key != "path"},
        }
        for name, detection in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.read(detection=detection))
        for state in ("missing", "invalid"):
            with self.subTest(state=state):
                self.assertIsNone(self.read(state=state))


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.temporary = Path(directory.name).resolve() / "temp" / "bench"
        self.temporary.mkdir(parents=True)
        self.ffmpeg = self.temporary / "ffmpeg.exe"
        self.auto = {"render_settings": {"video_encoder": "auto"}}

    def choose(self, plan, *, root=None, profile=None, trial=None):
        runs = []

        def run(command, **kwargs):
            runs.append(command)
            if isinstance(trial, BaseException):
                raise trial
            return subprocess.CompletedProcess(command, trial, stdout="", stderr="no device")

        with patch("biliflow.export_encoder.profile_nvenc_trial", return_value=profile) as reader, \
                patch("biliflow.export_encoder.subprocess.run", side_effect=run):
            choice = choose_video_encoder(project_root=root or self.temporary, ffmpeg_path=self.ffmpeg, plan=plan)
        return choice, reader.call_count, runs

    def test_a_forced_x264_is_kept_without_reading_the_profile_or_trying_nvenc(self):
        choice, reads, runs = self.choose({"render_settings": {"video_encoder": LIBX264}})
        self.assertEqual((choice.encoder, choice.record()), (LIBX264, {"requested": LIBX264, "nvenc_check": NOT_ASKED}))
        self.assertEqual((reads, runs), (0, []))

    def test_a_plan_without_the_setting_tries_nvenc_in_any_root_the_install_root_too(self):
        marker(self.temporary)  # an install root: the rule "temporary roots only" is gone
        for trial, encoder, check in ((0, H264_NVENC, TRIAL_OK), (1, LIBX264, TRIAL_FAILED)):
            with self.subTest(trial=trial):
                choice, reads, runs = self.choose({}, trial=trial)
                self.assertEqual(choice.record(), {"requested": AUTO, "nvenc_check": check})
                self.assertEqual((choice.encoder, reads, runs), (encoder, 1, [trial_command(self.ffmpeg)]))

    def test_a_profile_result_is_used_without_a_second_trial(self):
        for recorded, encoder, check in (("ok", H264_NVENC, PROFILE_OK), ("failed", LIBX264, PROFILE_FAILED)):
            with self.subTest(recorded=recorded):
                choice, reads, runs = self.choose(self.auto, profile=recorded)
                self.assertEqual((choice.encoder, choice.nvenc_check, reads, runs), (encoder, check, 1, []))

    def test_without_a_profile_result_one_trial_encode_decides(self):
        for trial, encoder, check in (
            (0, H264_NVENC, TRIAL_OK), (1, LIBX264, TRIAL_FAILED),
            (subprocess.TimeoutExpired("ffmpeg", 60), LIBX264, TRIAL_DID_NOT_FINISH),
            (OSError("cannot start"), LIBX264, TRIAL_DID_NOT_FINISH),
        ):
            with self.subTest(trial=trial):
                choice, reads, runs = self.choose(self.auto, trial=trial)
                self.assertEqual((choice.encoder, choice.nvenc_check, reads), (encoder, check, 1))
                self.assertEqual(runs, [trial_command(self.ffmpeg)])

    def test_the_trial_encodes_one_second_through_nvenc_and_writes_nothing(self):
        command = trial_command(self.ffmpeg)
        self.assertEqual(command[command.index("-c:v") + 1], H264_NVENC)
        self.assertEqual(command[-3:], ["-f", "null", "-"])
        self.assertIn("lavfi", command)


class ArgumentTests(unittest.TestCase):
    def test_nvenc_defaults_to_the_approved_p5_and_cq_25(self):
        # The user's approval of 2026-10-12 (docs/evidence/perf-export, A3).
        self.assertEqual((NVENC_PRESET, NVENC_CQ), ("p5", 25))
        arguments = video_arguments(H264_NVENC, maxrate=None, target_bitrate=None)
        self.assertEqual(arguments[:10], ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "25", "-b:v", "0"])
        self.assertEqual(video_description(H264_NVENC, size_limited=False),
                         "h264_nvenc p5 VBR CQ 25 without output size cap")

    def test_x264_keeps_the_export_arguments(self):
        self.assertEqual(
            video_arguments(LIBX264, maxrate=7_000_000, target_bitrate=900_000),
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-maxrate", "7000000", "-bufsize", "14000000", "-b:v", "900000"],
        )
        self.assertEqual(video_arguments(LIBX264, maxrate=None, target_bitrate=None),
                         ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])

    def test_nvenc_aims_at_a_constant_quality_under_the_same_size_guard(self):
        self.assertEqual(
            video_arguments(H264_NVENC, maxrate=7_000_000, target_bitrate=900_000),
            ["-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-rc", "vbr", "-cq", str(NVENC_CQ), "-b:v", "0",
             "-maxrate", "7000000", "-bufsize", "14000000", *NVENC_OPTIONS],
        )
        unlimited = video_arguments(H264_NVENC, maxrate=None, target_bitrate=None, cq=19, preset="p7")
        self.assertEqual(unlimited[:10], ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "vbr", "-cq", "19", "-b:v", "0"])
        self.assertNotIn("-maxrate", unlimited)
        with self.assertRaisesRegex(ValueError, "video encoder"):
            video_arguments("libx265", maxrate=None, target_bitrate=None)

    def test_the_descriptions_keep_the_x264_wording(self):
        self.assertEqual(video_description(LIBX264, size_limited=True), "libx264 CRF 20 with VBV size guard")
        self.assertEqual(video_description(LIBX264, size_limited=False), "libx264 CRF 20 without output size cap")
        self.assertEqual(video_description(H264_NVENC, size_limited=True),
                         f"h264_nvenc {NVENC_PRESET} VBR CQ {NVENC_CQ} with VBV size guard")


class FallbackTests(unittest.TestCase):
    def test_a_stop_is_not_an_error_of_ffmpeg_itself(self):
        # taskkill /F: exit code 1 and nothing printed.
        self.assertFalse(failed_by_itself(1, ""))
        self.assertFalse(failed_by_itself(1, " \r\n"))
        self.assertFalse(failed_by_itself(0, "a warning"))

    def test_ffmpeg_errors_and_crashes_are(self):
        self.assertTrue(failed_by_itself(1, "Error while opening encoder"))
        self.assertTrue(failed_by_itself(0xFFFFFFEA, ""))  # main() returned AVERROR(EINVAL)
        self.assertTrue(failed_by_itself(0xC0000005, ""))  # access violation
        self.assertFalse(failed_by_itself(0xBFFFFFFF, ""))

    def test_the_error_record_keeps_ffmpeg_first_lines_only(self):
        # FFmpeg prints the cause first; the lines after it report its threads ending.
        stderr = "\n\n" + "\n".join(f"line {index}" for index in range(6)) + "\n"
        self.assertEqual(
            error_fallback(1, stderr, 12.34567),
            {"from": "h264_nvenc", "reason": "encoder_error", "returncode": 1,
             "message": "line 0 | line 1 | line 2", "elapsed_seconds": 12.346},
        )
        self.assertEqual(len(error_fallback(1, "x" * 2000, 1.0)["message"]), 500)
        self.assertIsNone(error_fallback(0xC0000005, "", 1.0)["message"])

    def test_the_size_record_is_the_one_exports_wrote(self):
        self.assertEqual(size_fallback(600_000, 2.0004),
                         {"from": "h264_nvenc", "reason": "size_limit", "bytes": 600_000, "elapsed_seconds": 2.0})


@contextlib.contextmanager
def gpu_slot(timeout_seconds: float):
    """Hold the shared GPU slot like every GPU job; yields False when it stays busy (never cut in)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.CreateMutexW(None, False, GPU_SLOT)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
    try:
        result = kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000))
        if result not in (0x0, 0x80):  # WAIT_OBJECT_0, WAIT_ABANDONED
            yield False
            return
        try:
            yield True
        finally:
            kernel32.ReleaseMutex(handle)
    finally:
        kernel32.CloseHandle(handle)


@unittest.skipUnless(sys.platform == "win32" and FFMPEG.exists(), "project FFmpeg on Windows is required")
class NvencRenderTests(unittest.TestCase):
    """A real default export in a temporary root, inside the GPU slot: NVENC p5 -cq 25 and every export check."""

    def test_a_default_export_uses_nvenc_at_cq_25_and_passes_the_checks_of_cleanup_and_archive(self):
        from biliflow.export_identity import manifest_problem, read_manifest, render_identity
        from biliflow.final_renderer import render_final_output

        with gpu_slot(GPU_SLOT_WAIT_SECONDS) as held:
            if not held:
                self.skipTest("the GPU slot stayed busy")
            if trial_nvenc(FFMPEG) != TRIAL_OK:
                self.skipTest("NVENC does not work on this machine")
            with TemporaryDirectory() as directory:
                root = Path(directory).resolve() / "temp" / "nvenc"
                for name in ("input", "work", "output"):
                    (root / name).mkdir(parents=True)
                source = root / "input" / "source.mp4"
                subprocess.run(
                    [str(FFMPEG), "-hide_banner", "-loglevel", "error",
                     "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=2",
                     "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=2",
                     "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                    capture_output=True, timeout=120, check=True,
                )
                sha = hashlib.sha256(source.read_bytes()).hexdigest()
                operations = [{
                    "id": "blur-1", "type": "blur", "start_seconds": 0.2, "end_seconds": 1.6,
                    "region_source_pixels": {"x": 100, "y": 50, "width": 160, "height": 64},
                    "blur": {"sigma": 28, "edge_feather_pixels": 4, "edge_feather_mode": "all_edges",
                             "method": "gblur"},
                }]
                plan_path = root / "work" / "plan.json"
                plan_path.write_text(json.dumps({  # no render_settings: the default every export gets
                    "status": "READY_FOR_FINAL_RENDER", "final_export_allowed": True,
                    "source": {"path": str(source), "sha256": sha, "duration_seconds": 2.0},
                    "approved_operations": operations,
                }), encoding="utf-8")
                output = root / "output" / "source-reviewed.mp4"
                manifest = render_final_output(
                    project_root=root, plan_path=plan_path, output_path=output,
                    ffmpeg_path=FFMPEG, ffprobe_path=FFMPEG.with_name("ffprobe.exe"),
                )
                encoding = manifest["encoding"]
                self.assertEqual((encoding["encoder"], encoding["encoder_choice"]),
                                 (H264_NVENC, {"requested": AUTO, "nvenc_check": TRIAL_OK}))
                arguments = encoding["video_arguments"]
                self.assertEqual((arguments[arguments.index("-preset") + 1], arguments[arguments.index("-cq") + 1]),
                                 ("p5", "25"))
                self.assertIsNone(encoding["fallback"])
                self.assertTrue(encoding["gpu_used"])
                # The check after the export: every frame decoded on the CPU.
                self.assertEqual(encoding["validation_method"], "cpu_full_decode")
                self.assertTrue(encoding["full_decode_validation_passed"])
                self.assertEqual(manifest["source"]["sha256_after_render"], sha)
                # The manifest check "Xóa video gốc" and "Lưu trữ" run (source_cleanup.assess_job):
                # the export proves the review's operations, with the same arguments they pass.
                self.assertIsNone(manifest_problem(
                    root, output, read_manifest(output), source_sha256=sha,
                    render=render_identity(operations), source_path=str(source),
                ))


if __name__ == "__main__":
    unittest.main()
