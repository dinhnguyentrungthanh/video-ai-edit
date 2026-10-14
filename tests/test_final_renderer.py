import hashlib
import json
import os
import re
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.export_encoder import NVENC_CQ, STOP_GRACE_SECONDS, EncoderChoice
from biliflow.export_identity import manifest_problem, read_manifest
from biliflow.final_renderer import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TARGET_OUTPUT_BYTES,
    EXPORT_LANE_ENVIRONMENT,
    MAX_VIDEO_MAXRATE,
    PARALLEL_EXPORT_LANE,
    ExportDeferred,
    approve_previews,
    authorize_final_from_resolved_review,
    build_final_filter_graph,
    expected_output_duration,
    export_deferral_path,
    normalize_output_size_policy,
    read_render_progress,
    render_final_output,
    render_graph_path,
    render_progress_path,
)

# Largest value FFmpeg accepts for -maxrate and -bufsize (a signed 32-bit int).
INT_MAX = 2**31 - 1
# An edit plan's setting that keeps its export on x264 (perf plan A3; "auto" is the default).
FORCED_X264 = {"video_encoder": "libx264"}


class FinalRendererTests(unittest.TestCase):
    def test_ffmpeg_progress_reports_percent_speed_and_eta(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output" / "movie.mp4"
            path = render_progress_path(root, output)
            path.parent.mkdir(parents=True)
            path.write_text(
                "frame=120\nfps=24.0\ntotal_size=123456\n"
                "out_time_us=50000000\nspeed=1.25x\nprogress=continue\n",
                encoding="utf-8",
            )
            value = read_render_progress(path, expected_duration_seconds=100)
            self.assertEqual(value["state"], "RENDERING")
            self.assertEqual(value["percent"], 50.0)
            self.assertEqual(value["speed"], 1.25)
            self.assertEqual(value["eta_seconds"], 40.0)
            self.assertEqual(value["total_size_bytes"], 123456)

            path.write_text(
                "out_time_us=100000000\nspeed=0.9x\nprogress=end\n",
                encoding="utf-8",
            )
            done = read_render_progress(path, expected_duration_seconds=100)
            self.assertEqual(done["state"], "VERIFYING")
            self.assertEqual(done["percent"], 100.0)

    def test_expected_output_duration_accounts_for_merged_cuts(self):
        value = expected_output_duration(
            operations=[
                {"type": "cut", "start_seconds": 10, "end_seconds": 20},
                {"type": "cut", "start_seconds": 15, "end_seconds": 25},
            ],
            duration=100,
        )
        self.assertEqual(value, 85)

    def test_output_size_policy_supports_default_custom_and_unlimited(self):
        default = normalize_output_size_policy()
        self.assertEqual(default["maximum_output_bytes"], DEFAULT_MAX_OUTPUT_BYTES)
        self.assertEqual(default["target_output_bytes"], DEFAULT_TARGET_OUTPUT_BYTES)

        custom = normalize_output_size_policy("custom", 5.25)
        self.assertEqual(custom["maximum_output_bytes"], 5_250_000_000)
        self.assertLess(custom["target_output_bytes"], custom["maximum_output_bytes"])

        unlimited = normalize_output_size_policy("unlimited")
        self.assertIsNone(unlimited["maximum_output_bytes"])
        self.assertIsNone(unlimited["target_output_bytes"])

        with self.assertRaisesRegex(ValueError, "0.05"):
            normalize_output_size_policy("custom", 0)

    def test_resolved_review_can_be_the_single_export_gate(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "work").mkdir()
            (root / "reports" / "job").mkdir(parents=True)
            plan_path = root / "work" / "plan.json"
            queue_path = root / "reports" / "job" / "review-queue.json"
            source = {"path": str(root / "source.mp4"), "sha256": "abc"}
            queue_path.write_text(json.dumps({
                "status": "READY_FOR_EDIT_PLAN", "updated_at": "now",
                "source": source,
                "items": [{"id": "review-1", "decision": "CUT"}],
            }), encoding="utf-8")
            plan_path.write_text(json.dumps({
                "status": "READY_FOR_PREVIEW", "source": source,
                "review_queue": "reports/job/review-queue.json",
                "final_export_requested": False, "final_export_allowed": False,
            }), encoding="utf-8")
            plan = authorize_final_from_resolved_review(
                project_root=root, plan_path=plan_path, actor="tester",
            )
            self.assertEqual(plan["status"], "READY_FOR_FINAL_RENDER")
            self.assertTrue(plan["final_export_allowed"])
            self.assertEqual(plan["preview_approval"]["scope"], "RESOLVED_REVIEW_QUEUE")

    def test_filter_graph_removes_cut_and_keeps_blur(self):
        graph, expected = build_final_filter_graph(
            operations=[
                {"id": "cut", "type": "cut", "start_seconds": 10, "end_seconds": 12},
                {
                    "id": "blur", "type": "blur", "start_seconds": 20,
                    "end_seconds": 22, "region_source_pixels": "FULL_FRAME",
                },
            ],
            duration=30,
            has_audio=True,
        )
        self.assertEqual(expected, 28)
        self.assertIn("trim=start=0.000000:end=10.000000", graph)
        self.assertIn("trim=start=12.000000:end=30.000000", graph)
        self.assertIn("gblur=sigma=28", graph)
        self.assertIn("concat=n=2:v=1:a=1", graph)

    def test_regional_blur_uses_tight_region_and_soft_edge_mask(self):
        graph, expected = build_final_filter_graph(
            operations=[{
                "id": "blur", "type": "blur", "start_seconds": 2,
                "end_seconds": 4,
                "region_source_pixels": {"x": 0, "y": 56, "width": 1280, "height": 64},
                "blur": {
                    "sigma": 28, "edge_feather_pixels": 6,
                    "edge_feather_mode": "vertical_only",
                },
            }],
            duration=10,
            has_audio=False,
        )
        self.assertEqual(expected, 10)
        self.assertIn("crop=1280:64:0:56", graph)
        self.assertIn("gblur=sigma=28", graph)
        self.assertIn("geq=lum=", graph)
        self.assertIn("/6", graph)
        self.assertIn("min(Y\\,H-1-Y)", graph)
        self.assertNotIn("min(min(X\\,W-1-X)", graph)
        self.assertIn("format=yuv420p", graph)
        self.assertIn("alphamerge", graph)

    def test_cover_method_reads_picture_around_the_region_and_never_merges_with_a_plain_blur(self):
        region = {"x": 377, "y": 203, "width": 538, "height": 108}
        cover = {
            "sigma": 65, "edge_feather_pixels": 0, "edge_feather_mode": "all_edges",
            "method": "delogo_blur_v1",
        }
        graph, _ = build_final_filter_graph(
            operations=[
                {
                    "id": "cover", "type": "blur", "start_seconds": 2, "end_seconds": 8,
                    "region_source_pixels": region, "blur": cover,
                },
                {
                    "id": "plain", "type": "blur", "start_seconds": 3, "end_seconds": 5,
                    "region_source_pixels": dict(region), "blur": {**cover, "method": "gblur"},
                },
            ],
            duration=10, has_audio=False, frame_size=(1280, 534),
        )
        enable = "enable='between(t,2.000000,8.000000)'"
        self.assertIn(
            f"crop=554:124:369:195,delogo=x=8:y=8:w=538:h=108:{enable},crop=538:108:8:8,"
            f"gblur=sigma=65:threads=54:{enable}",
            graph,
        )
        self.assertEqual(graph.count("delogo="), 1)
        self.assertEqual(graph.count("overlay="), 2)

    def test_persistent_region_removes_redundant_short_logo_blurs(self):
        graph, _ = build_final_filter_graph(
            operations=[
                {
                    "id": "persistent", "type": "blur",
                    "start_seconds": 0, "end_seconds": 100,
                    "region_source_pixels": {
                        "x": 105, "y": 160, "width": 174, "height": 54,
                    },
                },
                {
                    "id": "sample-1", "type": "blur",
                    "start_seconds": 10, "end_seconds": 15,
                    "region_source_pixels": {
                        "x": 107, "y": 160, "width": 172, "height": 52,
                    },
                },
                {
                    "id": "sample-2", "type": "blur",
                    "start_seconds": 80, "end_seconds": 85,
                    "region_source_pixels": {
                        "x": 99, "y": 153, "width": 185, "height": 67,
                    },
                },
            ],
            duration=100, has_audio=False,
        )
        self.assertEqual(graph.count("crop="), 1)
        self.assertEqual(graph.count("overlay="), 1)

    def test_same_full_frame_blur_is_one_filter_with_multiple_ranges(self):
        graph, _ = build_final_filter_graph(
            operations=[
                {
                    "id": "first", "type": "blur",
                    "start_seconds": 2, "end_seconds": 4,
                    "region_source_pixels": "FULL_FRAME",
                },
                {
                    "id": "second", "type": "blur",
                    "start_seconds": 7, "end_seconds": 9,
                    "region_source_pixels": "FULL_FRAME",
                },
            ],
            duration=10, has_audio=False,
        )
        self.assertEqual(graph.count("gblur=sigma=28"), 1)
        self.assertIn(
            "between(t,2.000000,4.000000)+between(t,7.000000,9.000000)",
            graph,
        )

    def test_preview_approval_unlocks_final_render(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "work").mkdir()
            (root / "previews" / "job").mkdir(parents=True)
            plan_path = root / "work" / "plan.json"
            manifest_path = root / "previews" / "job" / "preview-manifest.json"
            plan_path.write_text(
                json.dumps(
                    {
                        "status": "READY_FOR_PREVIEW",
                        "approved_operations": [{"id": "op-1", "type": "cut"}],
                        "final_export_requested": False,
                        "final_export_allowed": False,
                    }
                ), encoding="utf-8",
            )
            manifest_path.write_text(
                json.dumps(
                    {
                        "status": "PREVIEW_REVIEW_REQUIRED",
                        "previews": [{"operation_id": "op-1"}],
                    }
                ), encoding="utf-8",
            )
            plan, manifest = approve_previews(
                project_root=root, plan_path=plan_path,
                preview_manifest_path=manifest_path, actor="tester",
            )
            self.assertEqual(plan["status"], "READY_FOR_FINAL_RENDER")
            self.assertTrue(plan["final_export_allowed"])
            self.assertEqual(manifest["status"], "APPROVED")
            self.assertEqual(manifest["approval"]["actor"], "tester")

    def test_sampled_preview_requires_explicit_authorization(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "work").mkdir()
            (root / "previews" / "job").mkdir(parents=True)
            plan_path = root / "work" / "plan.json"
            manifest_path = root / "previews" / "job" / "preview-manifest.json"
            plan = {
                "status": "READY_FOR_PREVIEW",
                "approved_operations": [
                    {"id": "op-1", "type": "blur"},
                    {"id": "op-2", "type": "cut"},
                ],
                "final_export_requested": False,
                "final_export_allowed": False,
            }
            manifest = {
                "status": "PREVIEW_REVIEW_REQUIRED",
                "preview_scope": "SAMPLED",
                "previews": [{"operation_id": "op-1"}],
            }
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "explicit allow_sampled"):
                approve_previews(
                    project_root=root, plan_path=plan_path,
                    preview_manifest_path=manifest_path,
                )
            approved_plan, approved_manifest = approve_previews(
                project_root=root, plan_path=plan_path,
                preview_manifest_path=manifest_path, actor="tester",
                allow_sampled=True,
            )
            self.assertTrue(approved_plan["final_export_allowed"])
            self.assertEqual(approved_manifest["approval"]["scope"], "SAMPLED")
            self.assertEqual(
                approved_plan["preview_approval"]["sampled_operation_ids"],
                ["op-1"],
            )


class RenderCompletionTests(unittest.TestCase):
    """How render_final_output finishes, with FFmpeg and ffprobe replaced (no real render)."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name in ("input", "work", "output", "tools"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "movie.mp4"
        self.source.write_bytes(b"source video " * 64)
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.plan_path = self.root / "work" / "movie-edit-plan.json"
        self.duration = 10.0
        self.write_plan()
        self.output = self.root / "output" / "movie-reviewed.mp4"
        self.partial = self.root / "output" / "movie-reviewed.partial.mp4"
        self.manifest_path = self.root / "output" / "movie-reviewed.mp4.manifest.json"
        self.tools = [self.root / "tools" / "ffmpeg.exe", self.root / "tools" / "ffprobe.exe"]
        for tool in self.tools:
            tool.write_bytes(b"")
        self.during_render = lambda: None
        self.commands = []
        self.audio = False
        self.render_fails = False
        # (exit code, stderr) of a render FFmpeg ends without an output, None for one that works.
        self.render_failure = lambda command: None
        self.graph_seen = None
        self.graph_path = render_graph_path(self.root, self.output)
        self.output_bytes = lambda command: b"rendered video " * 64
        self.pix_fmt = None
        # Exit code of the NVENC trial encode (perf plan A3).
        self.trial_code = 0

    def write_plan(self, operations=(), render_settings=FORCED_X264):
        """An approved edit plan of the source, which lasts ``self.duration`` seconds.

        Its exports are forced onto x264 unless a test asks otherwise; ``None``
        leaves ``render_settings`` out, the "auto" every export gets by default.
        """
        plan = {
            "status": "READY_FOR_FINAL_RENDER", "final_export_allowed": True,
            "source": {"path": str(self.source), "sha256": self.sha, "duration_seconds": self.duration},
            "approved_operations": list(operations),
        }
        if render_settings is not None:
            plan["render_settings"] = render_settings
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def fake_run(self, command, **kwargs):
        self.commands.append(command)
        if "lavfi" in command:  # the NVENC trial encode
            return subprocess.CompletedProcess(command, self.trial_code, stdout="", stderr="")
        if "-/filter_complex" in command:
            # What FFmpeg would read from the graph file while it parses its options.
            self.graph_seen = Path(command[command.index("-/filter_complex") + 1]).read_bytes()
            if self.render_fails:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="render failed")
            failure = self.render_failure(command)
            if failure is not None:
                Path(command[-1]).write_bytes(b"part of a video")  # what FFmpeg wrote before it ended
                return subprocess.CompletedProcess(command, failure[0], stdout="", stderr=failure[1])
            Path(command[-1]).write_bytes(self.output_bytes(command))
            self.during_render()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def render(self, **limits):
        video = {"codec_type": "video", **({"pix_fmt": self.pix_fmt} if self.pix_fmt else {})}
        streams = [video] + ([{"codec_type": "audio"}] if self.audio else [])
        probe = {"format": {"duration": str(self.duration)}, "streams": streams}
        with patch("biliflow.final_renderer.subprocess.run", side_effect=self.fake_run), \
                patch("biliflow.final_renderer.probe_video", return_value=probe), \
                patch("biliflow.final_renderer.require_capacity"):
            return render_final_output(
                project_root=self.root, plan_path=self.plan_path, output_path=self.output,
                ffmpeg_path=self.tools[0], ffprobe_path=self.tools[1], **limits,
            )

    def rate_options(self, duration, audio=False, **limits):
        """-maxrate, -bufsize and -b:v of the FFmpeg render of an output of ``duration`` seconds."""
        self.duration = duration
        self.audio = audio
        self.write_plan()
        self.commands = []
        self.render(**limits)
        self.output.unlink()
        self.manifest_path.unlink()
        command = next(item for item in self.commands if "-/filter_complex" in item)
        return {name: int(command[command.index(name) + 1]) for name in ("-maxrate", "-bufsize", "-b:v")}

    def expected_graph(self) -> bytes:
        graph, _ = build_final_filter_graph(operations=[], duration=self.duration, has_audio=self.audio)
        return graph.encode("utf-8")

    def test_the_graph_reaches_ffmpeg_in_a_file_under_temp_that_is_removed_after(self):
        # Perf phase 2 step 3: on the command line, a plan with many cuts passed Windows'
        # 32,767-character limit and the render could not even start.
        self.render()
        command = next(item for item in self.commands if "-/filter_complex" in item)
        self.assertNotIn("-filter_complex", command)
        self.assertEqual(command[command.index("-/filter_complex") + 1], str(self.graph_path))
        self.assertIn(self.root / "temp", self.graph_path.parents)
        self.assertEqual(self.graph_seen, self.expected_graph())
        self.assertFalse(self.graph_path.exists())

    def test_the_graph_file_is_removed_when_ffmpeg_fails(self):
        self.render_fails = True
        with self.assertRaisesRegex(RuntimeError, "render failed"):
            self.render()
        self.assertEqual(self.graph_seen, self.expected_graph())
        self.assertFalse(self.graph_path.exists())
        self.assertFalse(self.partial.exists())

    def test_a_file_left_at_the_graph_path_is_replaced(self):
        self.graph_path.parent.mkdir(parents=True)
        self.graph_path.write_bytes(b"[0:v]negate[vout]")
        self.render()
        self.assertEqual(self.graph_seen, self.expected_graph())
        self.assertFalse(self.graph_path.exists())

    def test_a_link_at_the_graph_path_is_never_written_through(self):
        target = self.root / "input" / "keep.txt"
        target.write_bytes(b"keep")
        self.graph_path.parent.mkdir(parents=True)
        try:
            self.graph_path.symlink_to(target)
        except OSError as error:
            self.skipTest(f"cannot create a symbolic link here: {error}")
        self.render()
        self.assertEqual(target.read_bytes(), b"keep")
        self.assertEqual(self.graph_seen, self.expected_graph())
        self.assertFalse(os.path.lexists(self.graph_path))

    def test_a_graph_folder_that_leads_out_of_temp_is_refused_before_ffmpeg_runs(self):
        # temp\render-progress turned into a junction to input\: the graph would land there.
        try:
            import _winapi
        except ImportError:
            self.skipTest("directory junctions are Windows only")
        self.graph_path.parent.parent.mkdir()
        _winapi.CreateJunction(str(self.root / "input"), str(self.graph_path.parent))
        before = sorted(path.name for path in (self.root / "input").iterdir())
        with self.assertRaisesRegex(ValueError, "Render graph folder"):
            self.render()
        self.assertFalse(any("-/filter_complex" in item for item in self.commands))
        self.assertEqual(sorted(path.name for path in (self.root / "input").iterdir()), before)

    def test_a_graph_file_that_cannot_be_removed_never_hides_the_render_result(self):
        # A scanner may hold the new file for a moment after FFmpeg has read it.
        original = Path.unlink

        def unlink(path, missing_ok=False):
            if path.suffix == ".filtergraph" and path.exists():
                raise PermissionError(32, "The file is in use", str(path))
            return original(path, missing_ok=missing_ok)

        with patch.object(Path, "unlink", unlink):
            self.assertEqual(self.render()["status"], "COMPLETED")
            # Left behind, it is replaced by the next render of this output.
            self.assertEqual(self.graph_path.read_bytes(), self.expected_graph())
            for path in (self.graph_path, self.output, self.manifest_path):
                original(path)
            self.write_plan()
            self.render_fails = True
            with self.assertRaisesRegex(RuntimeError, "render failed"):
                self.render()
        self.assertTrue(self.graph_path.exists())

    def test_a_graph_with_a_nul_is_refused_before_ffmpeg_runs(self):
        # FFmpeg would silently stop reading the file at the NUL.
        with patch("biliflow.final_renderer.build_final_filter_graph", return_value=("[0:v]null\x00[vout]", 10.0)):
            with self.assertRaisesRegex(ValueError, "NUL"):
                self.render()
        self.assertFalse(any("-/filter_complex" in item for item in self.commands))
        self.assertFalse(self.graph_path.exists())

    @staticmethod
    def encoder_of(command):
        return command[command.index("-c:v") + 1]

    def renders(self):
        return [item for item in self.commands if "-/filter_complex" in item]

    def nvenc_chosen(self):
        """A temporary root where NVENC works (perf plan A3); the choice itself is in test_export_encoder."""
        self.write_plan(render_settings={"video_encoder": "auto"})
        choice = EncoderChoice("h264_nvenc", "auto", "trial_ok")
        return patch("biliflow.final_renderer.choose_video_encoder", return_value=choice)

    def test_a_forced_x264_export_keeps_its_exact_arguments_without_a_trial(self):
        # Perf plan A3: the x264 path gates of export changes compare byte for byte.
        manifest = self.render()
        (command,) = self.renders()
        start = command.index("-map_metadata")
        self.assertEqual(command[start:start + 8],
                         ["-map_metadata", "0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"])
        self.assertEqual(command[command.index("-b:v") + 2:command.index("-b:v") + 4], ["-pix_fmt", "yuv420p"])
        self.assertFalse(any("h264_nvenc" in item for item in self.commands))
        encoding = manifest["encoding"]
        self.assertEqual(encoding["encoder"], "libx264")
        self.assertEqual(encoding["validation_method"], "cpu_full_decode")
        self.assertEqual(encoding["encoder_choice"], {"requested": "libx264", "nvenc_check": "not_asked"})
        self.assertIsNone(encoding["fallback"])
        self.assertFalse(encoding["gpu_used"])
        self.assertEqual(encoding["video"], "libx264 CRF 20 with VBV size guard")
        self.assertEqual(encoding["video_arguments"], command[start + 2:command.index("-pix_fmt")])
        self.assertEqual(manifest["source"]["sha256_after_render"], self.sha)

    def test_a_default_export_uses_nvenc_p5_at_cq_25_where_it_works(self):
        # Perf plan A3, approved 2026-10-12: "auto" for every export, the install folder too.
        self.write_plan(render_settings=None)
        manifest = self.render()
        (command,) = self.renders()
        for option, value in (("-c:v", "h264_nvenc"), ("-preset", "p5"), ("-rc", "vbr"), ("-cq", "25")):
            self.assertEqual(command[command.index(option) + 1], value, option)
        encoding = manifest["encoding"]
        self.assertEqual((encoding["encoder"], encoding["encoder_choice"]),
                         ("h264_nvenc", {"requested": "auto", "nvenc_check": "trial_ok"}))
        self.assertEqual(encoding["video"], "h264_nvenc p5 VBR CQ 25 with VBV size guard")
        self.assertIsNone(encoding["fallback"])
        self.assertTrue(encoding["full_decode_validation_passed"])
        self.assertIsNone(manifest_problem(
            self.root, self.output, read_manifest(self.output), source_sha256=self.sha, render=[],
            source_path=str(self.source),
        ))

    def test_a_default_export_where_nvenc_does_not_work_is_the_x264_export_of_today(self):
        forced = self.render()
        (x264_command,) = self.renders()
        self.output.unlink()
        self.manifest_path.unlink()
        self.commands = []
        self.write_plan(render_settings=None)
        self.trial_code = 1
        manifest = self.render()
        (command,) = self.renders()
        self.assertEqual(command, x264_command)
        self.assertEqual(manifest["encoding"]["encoder_choice"], {"requested": "auto", "nvenc_check": "trial_failed"})
        same = ("video", "encoder", "video_arguments", "fallback", "validation_method", "gpu_used")
        self.assertEqual({key: manifest["encoding"][key] for key in same},
                         {key: forced["encoding"][key] for key in same})

    def test_an_nvenc_export_aims_at_a_constant_quality_under_the_x264_size_guard(self):
        x264 = self.rate_options(10.0)
        self.commands = []
        with self.nvenc_chosen():
            manifest = self.render()
        (command,) = self.renders()
        self.assertEqual(self.encoder_of(command), "h264_nvenc")
        for option, value in (("-rc", "vbr"), ("-cq", str(NVENC_CQ)), ("-b:v", "0"),
                              ("-maxrate", str(x264["-maxrate"])), ("-bufsize", str(x264["-bufsize"]))):
            self.assertEqual(command[command.index(option) + 1], value, option)
        encoding = manifest["encoding"]
        self.assertEqual(encoding["encoder"], "h264_nvenc")
        self.assertEqual(encoding["encoder_choice"], {"requested": "auto", "nvenc_check": "trial_ok"})
        self.assertTrue(encoding["gpu_used"])
        self.assertEqual(encoding["validation_method"], "cpu_full_decode")
        self.assertTrue(encoding["full_decode_validation_passed"])
        self.assertIsNone(manifest_problem(
            self.root, self.output, read_manifest(self.output), source_sha256=self.sha, render=[],
        ))

    def test_an_nvenc_export_over_the_size_limit_is_encoded_again_with_x264(self):
        self.output_bytes = lambda command: b"v" * (600_000 if self.encoder_of(command) == "h264_nvenc" else 1_000)
        with self.nvenc_chosen():
            manifest = self.render(max_output_bytes=500_000, target_output_bytes=400_000)
        self.assertEqual([self.encoder_of(item) for item in self.renders()], ["h264_nvenc", "libx264"])
        encoding = manifest["encoding"]
        self.assertEqual(encoding["encoder"], "libx264")
        self.assertFalse(encoding["gpu_used"])
        self.assertEqual(
            {key: encoding["fallback"][key] for key in ("from", "reason", "bytes")},
            {"from": "h264_nvenc", "reason": "size_limit", "bytes": 600_000},
        )
        self.assertEqual(manifest["output"]["bytes"], 1_000)
        self.assertTrue(manifest["output"]["within_size_limit"])
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.graph_path.exists())

    def test_an_x264_export_over_the_size_limit_still_fails_without_a_second_render(self):
        self.output_bytes = lambda command: b"v" * 600_000
        with self.assertRaisesRegex(RuntimeError, "hard size limit"):
            self.render(max_output_bytes=500_000, target_output_bytes=400_000)
        self.assertEqual(len(self.renders()), 1)
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.output.exists())

    def nvenc_fails(self, returncode, stderr):
        """FFmpeg ends every NVENC render with ``returncode`` and ``stderr``; x264 renders work."""
        self.render_failure = lambda command: (
            (returncode, stderr) if self.encoder_of(command) == "h264_nvenc" else None
        )

    def test_an_nvenc_export_ffmpeg_ends_on_its_own_error_is_encoded_again_with_x264(self):
        # Perf plan A3, approved 2026-10-12: GPU busy, out of memory or an encoder error.
        self.nvenc_fails(1, "[h264_nvenc @ 0000] OpenEncodeSessionEx failed: out of memory (10)\n"
                            "Conversion failed!\n")
        with self.nvenc_chosen(), patch("biliflow.final_renderer.time.sleep") as sleep:
            manifest = self.render()
        self.assertEqual([self.encoder_of(item) for item in self.renders()], ["h264_nvenc", "libx264"])
        sleep.assert_called_once_with(STOP_GRACE_SECONDS)
        encoding = manifest["encoding"]
        self.assertEqual(encoding["encoder"], "libx264")
        self.assertFalse(encoding["gpu_used"])
        self.assertEqual(encoding["video"], "libx264 CRF 20 with VBV size guard")
        self.assertEqual(encoding["encoder_choice"], {"requested": "auto", "nvenc_check": "trial_ok"})
        fallback = encoding["fallback"]
        self.assertEqual({key: fallback[key] for key in ("from", "reason", "returncode")},
                         {"from": "h264_nvenc", "reason": "encoder_error", "returncode": 1})
        self.assertIn("out of memory", fallback["message"])
        self.assertEqual(manifest["source"]["sha256_after_render"], self.sha)
        self.assertIsNone(manifest_problem(
            self.root, self.output, read_manifest(self.output), source_sha256=self.sha, render=[],
        ))
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.graph_path.exists())

    def test_an_nvenc_crash_without_a_message_is_encoded_again_with_x264(self):
        self.nvenc_fails(0xC0000005, "")
        with self.nvenc_chosen(), patch("biliflow.final_renderer.time.sleep"):
            manifest = self.render()
        fallback = manifest["encoding"]["fallback"]
        self.assertEqual((fallback["reason"], fallback["returncode"], fallback["message"]),
                         ("encoder_error", 0xC0000005, None))
        self.assertEqual(manifest["encoding"]["encoder"], "libx264")

    def test_a_stop_that_kills_an_nvenc_render_is_not_an_nvenc_error(self):
        # Tạm dừng and Hủy kill the render tree (taskkill /T /F): FFmpeg ends with
        # exit code 1 before it prints anything, and no x264 render may follow.
        self.nvenc_fails(1, "")
        with self.nvenc_chosen(), patch("biliflow.final_renderer.time.sleep") as sleep, \
                self.assertRaisesRegex(RuntimeError, "Final FFmpeg render failed"):
            self.render()
        self.assertEqual([self.encoder_of(item) for item in self.renders()], ["h264_nvenc"])
        sleep.assert_not_called()
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.output.exists())
        self.assertFalse(self.manifest_path.exists())

    def test_an_x264_error_still_fails_without_a_second_render(self):
        self.render_failure = lambda command: (1, "x264 failed")
        with patch("biliflow.final_renderer.time.sleep") as sleep, \
                self.assertRaisesRegex(RuntimeError, "x264 failed"):
            self.render()
        self.assertEqual(len(self.renders()), 1)
        sleep.assert_not_called()
        self.assertFalse(self.partial.exists())

    # Perf plan A1b: a render of the scheduler's export lane, beside a GPU scan.

    def beside_a_scan(self, *, profile="ok"):
        """Run as the export lane does: its environment variable, the hardware profile's NVENC answer."""
        return (
            patch.dict(os.environ, {EXPORT_LANE_ENVIRONMENT: PARALLEL_EXPORT_LANE}),
            patch("biliflow.export_encoder.profile_nvenc_trial", return_value=profile),
        )

    def deferral(self):
        return json.loads(export_deferral_path(self.root, self.output).read_text(encoding="utf-8"))

    def test_beside_a_scan_an_nvenc_error_hands_the_export_back_without_x264(self):
        self.write_plan(render_settings=None)
        self.nvenc_fails(1, "[h264_nvenc @ 0000] OpenEncodeSessionEx failed: out of memory (10)\n")
        environment, profile = self.beside_a_scan()
        with environment, profile, patch("biliflow.final_renderer.time.sleep") as sleep, \
                self.assertRaises(ExportDeferred):
            self.render()
        # x264 never runs beside a scan: the main lane encodes it later.
        self.assertEqual([self.encoder_of(item) for item in self.renders()], ["h264_nvenc"])
        self.assertFalse(any("lavfi" in item for item in self.commands))  # no trial encode either
        sleep.assert_called_once_with(STOP_GRACE_SECONDS)  # a stop still wins before the record
        record = self.deferral()
        self.assertEqual((record["reason"], record["encoder_choice"]),
                         ("encoder_error", {"requested": "auto", "nvenc_check": "profile_ok"}))
        self.assertEqual({key: record["fallback"][key] for key in ("from", "reason", "returncode")},
                         {"from": "h264_nvenc", "reason": "encoder_error", "returncode": 1})
        self.assertEqual((record["plan"], record["output"]),
                         ("work/movie-edit-plan.json", "output/movie-reviewed.mp4"))
        for path in (self.partial, self.output, self.manifest_path, self.graph_path):
            self.assertFalse(path.exists(), path)

    def test_beside_a_scan_an_nvenc_export_over_the_size_limit_is_handed_back(self):
        self.write_plan(render_settings=None)
        self.output_bytes = lambda command: b"v" * 600_000
        environment, profile = self.beside_a_scan()
        with environment, profile, self.assertRaises(ExportDeferred):
            self.render(max_output_bytes=500_000, target_output_bytes=400_000)
        self.assertEqual([self.encoder_of(item) for item in self.renders()], ["h264_nvenc"])
        record = self.deferral()
        self.assertEqual((record["reason"], record["fallback"]["reason"], record["fallback"]["bytes"]),
                         ("size_limit", "size_limit", 600_000))
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.output.exists())

    def test_beside_a_scan_an_export_that_would_not_use_nvenc_stops_before_any_encode(self):
        for render_settings, profile in ((FORCED_X264, "ok"), (None, None), (None, "failed")):
            with self.subTest(render_settings=render_settings, profile=profile):
                self.commands = []
                export_deferral_path(self.root, self.output).unlink(missing_ok=True)
                self.write_plan(render_settings=render_settings)
                environment, answer = self.beside_a_scan(profile=profile)
                with environment, answer, patch("biliflow.final_renderer._sha256") as digest, \
                        self.assertRaises(ExportDeferred):
                    self.render()
                self.assertEqual(self.commands, [])  # no x264, no trial, no probe of the output
                digest.assert_not_called()  # decided before the source is read
                record = self.deferral()
                self.assertEqual((record["reason"], record["fallback"], record["encoder_choice"]),
                                 ("not_nvenc", None, None))

    def test_beside_a_scan_an_export_already_handed_back_is_never_encoded(self):
        self.write_plan(render_settings=None)
        self.nvenc_fails(1, "out of memory")
        environment, profile = self.beside_a_scan()
        with environment, profile, patch("biliflow.final_renderer.time.sleep"):
            with self.assertRaises(ExportDeferred):
                self.render()
            first = self.deferral()
            self.commands = []
            with self.assertRaises(ExportDeferred):
                self.render()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.deferral(), first)

    def test_a_stop_beside_a_scan_leaves_no_record(self):
        self.write_plan(render_settings=None)
        self.nvenc_fails(1, "")  # taskkill /T /F: exit code 1, nothing printed
        environment, profile = self.beside_a_scan()
        with environment, profile, patch("biliflow.final_renderer.time.sleep"), \
                self.assertRaisesRegex(RuntimeError, "Final FFmpeg render failed") as raised:
            self.render()
        self.assertNotIsInstance(raised.exception, ExportDeferred)
        self.assertFalse(os.path.lexists(export_deferral_path(self.root, self.output)))

    def test_an_nvenc_export_beside_a_scan_records_its_lane(self):
        self.write_plan(render_settings=None)
        environment, profile = self.beside_a_scan()
        with environment, profile:
            manifest = self.render()
        encoding = manifest["encoding"]
        self.assertEqual((encoding["encoder"], encoding["lane"], encoding["fallback"]),
                         ("h264_nvenc", "parallel", None))
        self.assertEqual(encoding["encoder_choice"], {"requested": "auto", "nvenc_check": "profile_ok"})
        self.assertFalse(any("lavfi" in item for item in self.commands))  # the profile answered
        self.assertFalse(os.path.lexists(export_deferral_path(self.root, self.output)))
        self.assertIsNone(manifest_problem(
            self.root, self.output, read_manifest(self.output), source_sha256=self.sha, render=[],
        ))

    def test_the_main_lane_finishes_a_handed_back_export_with_the_x264_of_a3(self):
        # The x264 command of a forced-x264 export of the same plan: the byte-identical path.
        self.render()
        (x264_command,) = self.renders()
        for path in (self.output, self.manifest_path):
            path.unlink()
        self.commands = []
        self.write_plan(render_settings=None)
        self.nvenc_fails(1, "[h264_nvenc @ 0000] out of memory\n")
        environment, profile = self.beside_a_scan()
        with environment, profile, patch("biliflow.final_renderer.time.sleep"), self.assertRaises(ExportDeferred):
            self.render()
        nvenc_seconds = self.deferral()["nvenc_elapsed_seconds"]
        self.commands = []
        # The main lane: no lane variable. NVENC is not tried again; x264 as A3 would have run it.
        with patch("biliflow.final_renderer.choose_video_encoder") as choose, \
                patch("biliflow.final_renderer.time.sleep") as sleep:
            manifest = self.render()
        choose.assert_not_called()
        sleep.assert_not_called()
        (command,) = self.renders()
        self.assertEqual(command, x264_command)
        encoding = manifest["encoding"]
        self.assertEqual((encoding["encoder"], encoding["lane"], encoding["gpu_used"]), ("libx264", "main", False))
        self.assertEqual(encoding["encoder_choice"], {"requested": "auto", "nvenc_check": "profile_ok"})
        self.assertEqual({key: encoding["fallback"][key] for key in ("from", "reason", "returncode", "deferred")},
                         {"from": "h264_nvenc", "reason": "encoder_error", "returncode": 1, "deferred": True})
        self.assertGreaterEqual(encoding["elapsed_seconds"], round(nvenc_seconds, 3))
        self.assertFalse(os.path.lexists(export_deferral_path(self.root, self.output)))
        self.assertIsNone(manifest_problem(
            self.root, self.output, read_manifest(self.output), source_sha256=self.sha, render=[],
        ))

    def test_a_record_of_another_export_is_never_used(self):
        self.write_plan(render_settings=None)
        path = export_deferral_path(self.root, self.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        fallback = {"from": "h264_nvenc", "reason": "encoder_error", "returncode": 1, "message": "x",
                    "elapsed_seconds": 1.0}
        stale = {"schema": 1, "created_at": "2026-10-14T10:00:00+07:00", "plan": "work/other-edit-plan.json",
                 "output": "output/movie-reviewed.mp4", "reason": "encoder_error",
                 "encoder_choice": {"requested": "auto", "nvenc_check": "profile_ok"},
                 "fallback": fallback, "nvenc_elapsed_seconds": 1.0}
        for record in (stale, {**stale, "plan": "work/movie-edit-plan.json", "schema": 2}, "not json"):
            with self.subTest(record=record):
                self.commands = []
                for leftover in (self.output, self.manifest_path):
                    leftover.unlink(missing_ok=True)
                path.write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
                with self.nvenc_chosen():
                    manifest = self.render()
                self.assertEqual(manifest["encoding"]["encoder"], "h264_nvenc")
                self.assertIsNone(manifest["encoding"]["fallback"])

    def test_when_the_x264_retry_fails_too_its_own_error_is_reported(self):
        self.render_failure = lambda command: (1, f"{self.encoder_of(command)} failed")
        with self.nvenc_chosen(), patch("biliflow.final_renderer.time.sleep"), \
                self.assertRaisesRegex(RuntimeError, "^libx264 failed$"):
            self.render()
        self.assertEqual([self.encoder_of(item) for item in self.renders()], ["h264_nvenc", "libx264"])
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.output.exists())

    def test_a_yuv410p_source_caps_gblur_at_its_chroma_rows_and_a_420_source_keeps_its_graph(self):
        thin = {
            "id": "blur-1", "type": "blur", "start_seconds": 1.0, "end_seconds": 2.0,
            "region_source_pixels": {"x": 0, "y": 0, "width": 40, "height": 24},
            "blur": {"sigma": 28, "edge_feather_pixels": 0, "edge_feather_mode": "all_edges", "method": "gblur"},
        }
        for pix_fmt, threads in ((None, 12), ("yuv420p", 12), ("yuv410p", 6)):
            with self.subTest(pix_fmt=pix_fmt):
                self.pix_fmt = pix_fmt
                self.write_plan(operations=[thin])
                self.render()
                self.assertEqual(re.findall(rb"gblur=sigma=28:threads=(\d+):", self.graph_seen),
                                 [str(threads).encode()])
                self.output.unlink()
                self.manifest_path.unlink()
        expected, _ = build_final_filter_graph(operations=[thin], duration=self.duration, has_audio=False)
        self.pix_fmt = "yuv420p"
        self.write_plan(operations=[thin])
        self.render()
        self.assertEqual(self.graph_seen, expected.encode("utf-8"))

    def test_a_short_output_stays_within_the_encoder_limits(self):
        # FFmpeg takes -maxrate and -bufsize as 32-bit numbers: the size budget of
        # a 6 s or 20 s output exceeded them and FFmpeg refused to start.
        for duration, audio in ((6.0, False), (20.0, False), (20.0, True)):
            with self.subTest(duration=duration, audio=audio):
                options = self.rate_options(duration, audio=audio)
                self.assertEqual(options["-maxrate"], MAX_VIDEO_MAXRATE)
                self.assertLessEqual(options["-bufsize"], INT_MAX)
                self.assertLessEqual(options["-b:v"], options["-maxrate"])

    def test_a_large_custom_limit_stays_within_the_encoder_limits(self):
        policy = normalize_output_size_policy("custom", 1000)
        options = self.rate_options(3600.0, max_output_bytes=policy["maximum_output_bytes"],
                                    target_output_bytes=policy["target_output_bytes"])
        self.assertEqual(options["-maxrate"], MAX_VIDEO_MAXRATE)
        self.assertLessEqual(options["-bufsize"], INT_MAX)

    def test_every_output_ffmpeg_accepted_keeps_its_exact_rates(self):
        # The budget of the default limit (less 384 kbit/s of audio), unchanged
        # wherever its -bufsize fitted: a 24.5 s clip (just above the cap's
        # boundary) and a one-hour episode.
        for duration, audio, rate in ((24.5, False, 1_045_224_489), (3600.0, False, 7_113_333),
                                      (3600.0, True, 6_729_333)):
            with self.subTest(duration=duration, audio=audio):
                self.assertLessEqual(rate * 2, INT_MAX)
                self.assertEqual(self.rate_options(duration, audio=audio),
                                 {"-maxrate": rate, "-bufsize": rate * 2, "-b:v": rate})

    def test_the_cap_is_the_highest_rate_whose_buffer_fits(self):
        self.assertLessEqual(2 * MAX_VIDEO_MAXRATE, INT_MAX)
        self.assertGreater(2 * (MAX_VIDEO_MAXRATE + 1), INT_MAX)

    def test_a_budget_at_the_cap_is_unchanged_and_one_above_is_capped(self):
        cap = 1_073_741_823
        # A 10 s output without audio whose budget is cap - 1, cap and cap + 1.
        for target, rate in ((1_383_687_915, cap - 1), (1_383_687_917, cap), (1_383_687_918, cap)):
            with self.subTest(target=target):
                options = self.rate_options(10.0, max_output_bytes=target + 1, target_output_bytes=target)
                self.assertEqual((options["-maxrate"], options["-bufsize"]), (rate, rate * 2))

    def test_a_completed_render_is_proven_by_its_manifest(self):
        manifest = self.render()
        self.assertEqual(manifest["output"]["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertIsNone(manifest_problem(
            self.root, self.output, read_manifest(self.output), source_sha256=self.sha, render=[],
        ))
        self.assertFalse(self.partial.exists())

    def test_a_file_that_appears_at_the_output_during_the_render_is_never_replaced(self):
        self.during_render = lambda: self.output.write_bytes(b"someone else's file")
        with self.assertRaises(FileExistsError):
            self.render()
        self.assertEqual(self.output.read_bytes(), b"someone else's file")
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.manifest_path.exists())

    def test_a_link_at_the_output_is_refused_before_ffmpeg_runs(self):
        # exists() is False for a dangling link; the render would only fail at its end.
        # (A link that points out of output/ is already refused as outside it.)
        try:
            self.output.symlink_to(self.root / "output" / "missing.mp4")
        except OSError as error:
            self.skipTest(f"cannot create a symbolic link here: {error}")
        calls = []
        self.during_render = lambda: calls.append("render")
        with self.assertRaises(FileExistsError):
            self.render()
        self.assertEqual(calls, [])
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(self.partial.exists())

    def test_a_source_changed_during_the_render_leaves_no_export(self):
        # Checked before the file takes the export's name: no export without a manifest.
        self.during_render = lambda: self.source.write_bytes(b"edited source " * 64)
        with self.assertRaisesRegex(RuntimeError, "Source checksum changed during render"):
            self.render()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.manifest_path.exists())
        self.assertFalse(self.graph_path.exists())


if __name__ == "__main__":
    unittest.main()
