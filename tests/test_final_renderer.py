import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.export_identity import manifest_problem, read_manifest
from biliflow.final_renderer import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TARGET_OUTPUT_BYTES,
    MAX_VIDEO_MAXRATE,
    approve_previews,
    authorize_final_from_resolved_review,
    build_final_filter_graph,
    expected_output_duration,
    normalize_output_size_policy,
    read_render_progress,
    render_final_output,
    render_progress_path,
)

# Largest value FFmpeg accepts for -maxrate and -bufsize (a signed 32-bit int).
INT_MAX = 2**31 - 1


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

    def write_plan(self):
        """An approved edit plan of the source, which lasts ``self.duration`` seconds."""
        self.plan_path.write_text(json.dumps({
            "status": "READY_FOR_FINAL_RENDER", "final_export_allowed": True,
            "source": {"path": str(self.source), "sha256": self.sha, "duration_seconds": self.duration},
            "approved_operations": [],
        }), encoding="utf-8")

    def fake_run(self, command, **kwargs):
        self.commands.append(command)
        if "-filter_complex" in command:
            Path(command[-1]).write_bytes(b"rendered video " * 64)
            self.during_render()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def render(self, **limits):
        streams = [{"codec_type": "video"}] + ([{"codec_type": "audio"}] if self.audio else [])
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
        command = next(item for item in self.commands if "-filter_complex" in item)
        return {name: int(command[command.index(name) + 1]) for name in ("-maxrate", "-bufsize", "-b:v")}

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


if __name__ == "__main__":
    unittest.main()
