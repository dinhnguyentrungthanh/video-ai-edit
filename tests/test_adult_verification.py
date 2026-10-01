import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from biliflow import adult_verification
from biliflow.adult_verification import (
    ADULT_TRIAGE_LEVEL,
    ADULT_TRIAGE_LEVELS,
    VERIFIER_CALIBRATED_REVISION,
    _ActiveProcess,
    _interval_frame_batches,
    decode_command,
    verification_is_calibrated,
    verify_adult_report,
)
from biliflow.cli import build_parser


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"
REVISION = VERIFIER_CALIBRATED_REVISION


def _image(value: int) -> Image.Image:
    return Image.new("RGB", (448, 448), (value, value, value))


class _Project:
    """Minimal project tree: source video, nsfw scan report, model manifest."""

    def __init__(self, intervals, **report_extra):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "input" / "movie.mp4"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"source video bytes")
        self.ffmpeg = self.root / "tools" / "ffmpeg.exe"
        self.ffmpeg.parent.mkdir(parents=True)
        self.ffmpeg.write_bytes(b"")
        self.model = self.root / "models" / "image_safety_classifier_m"
        self.model.mkdir(parents=True)
        (self.model / "manifest.json").write_text(json.dumps({
            "model": "safety", "repo_id": "OwenElliott/image-safety-classifier-m",
            "revision": REVISION, "backend": "timm", "license_spdx": "MIT",
            "approval_status": "APPROVED",
        }), encoding="utf-8")
        self.report_dir = self.root / "reports" / "jobs" / "job" / "adult"
        self.report_dir.mkdir(parents=True)
        self.report = self.report_dir / "scan.json"
        self.output = self.report_dir / "scan-verified.json"
        payload = {
            "schema_version": 1, "scan_type": "nsfw", "status": "COMPLETED",
            "input": str(self.source),
            "input_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "duration_seconds": 100.0, "sample_fps": 2.0, "threshold": 0.95,
            "content_style": "live_action", "intervals": intervals,
            "metrics": {"elapsed_seconds": 1.0},
        }
        payload.update(report_extra)
        self.report.write_text(json.dumps(payload), encoding="utf-8")

    def verify(self, **extra):
        options = dict(
            project_root=self.root, report_path=self.report, output_path=self.output,
            model_path=self.model, ffmpeg_path=self.ffmpeg, device_name="cpu",
        )
        options.update(extra)
        return verify_adult_report(**options)

    def cleanup(self):
        self.temp.cleanup()


def _intervals():
    return [
        {"start_seconds": 10.0, "end_seconds": 12.0, "max_score": 0.97, "sample_count": 1,
         "predicted_label": "porn", "strongest_frame": "thumbnails/a.jpg"},
        {"start_seconds": 40.0, "end_seconds": 49.5, "max_score": 0.99, "sample_count": 9,
         "predicted_label": "porn", "strongest_frame": "thumbnails/b.jpg",
         "sequence_context": {"applied": False}},
        {"start_seconds": 80.0, "end_seconds": 81.0, "max_score": 0.96, "sample_count": 1,
         "predicted_label": "hentai"},
    ]


def _fake_batches(plan):
    """Replacement for _interval_frame_batches driven by {index: [pixel values] | "fail"}.

    The fake scorer returns pixel / 255, so a value v scores v / 255.
    """

    def generator(ffmpeg_path, video_path, intervals, batch_size, holder, cancelled):
        for index, _interval in enumerate(intervals):
            outcome = plan.get(index, [25])
            if outcome == "fail":
                yield ("frames", index, [_image(200)])  # partial frames before the error
                yield ("failed", index, "FFmpeg failed with exit code 1: broken")
                continue
            images = [_image(value) for value in outcome]
            for start in range(0, len(images), batch_size):
                yield ("frames", index, images[start:start + batch_size])
            yield ("done", index, len(images))

    return generator


def _fake_scorer(images):
    return [image.getpixel((0, 0))[0] / 255 for image in images]


def _load_fake_scorer(model_path, device_name):
    manifest = json.loads((model_path / "manifest.json").read_text(encoding="utf-8"))
    return _fake_scorer, manifest, None


class VerifyAdultReportTests(unittest.TestCase):
    def setUp(self):
        self.project = _Project(_intervals())
        self.addCleanup(self.project.cleanup)

    def run_with(self, plan, **extra):
        with patch.object(adult_verification, "_load_scorer", _load_fake_scorer), \
                patch.object(adult_verification, "_interval_frame_batches", _fake_batches(plan)):
            return self.project.verify(**extra)

    def test_writes_a_scored_copy_and_never_changes_intervals(self):
        source = json.loads(self.project.report.read_text(encoding="utf-8"))
        payload = self.run_with({0: [51, 153, 102], 1: [230] * 20, 2: [13]}, batch_size=8)
        written = json.loads(self.project.output.read_text(encoding="utf-8"))
        self.assertEqual(written, payload)
        self.assertEqual(json.loads(self.project.report.read_text(encoding="utf-8")), source)
        self.assertEqual(len(payload["intervals"]), len(source["intervals"]))
        for before, after in zip(source["intervals"], payload["intervals"]):
            verification = after.pop("adult_verification")
            self.assertEqual(before, after)  # every original field untouched, same order
            self.assertEqual(verification["state"], "SCORED")
            self.assertTrue(verification_is_calibrated(verification))
            for key in ("nsfw_max", "nsfw_frames", "nsfw_scores", "sample_fps", "frame_size",
                        "preprocessing", "model", "revision", "target_label", "decoded_span_seconds"):
                self.assertIn(key, verification)
        scores = [interval["adult_verification"] for interval in written["intervals"]]
        self.assertEqual([value["nsfw_max"] for value in scores],
                         [round(153 / 255, 6), round(230 / 255, 6), round(13 / 255, 6)])
        self.assertEqual(scores[0]["nsfw_scores"], [0.2, 0.6, 0.4])
        self.assertEqual([value["nsfw_frames"] for value in scores], [3, 20, 1])
        self.assertEqual(scores[0]["decoded_span_seconds"], [10.0, 12.0])
        self.assertEqual(scores[2]["decoded_span_seconds"], [80.0, 81.0])
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(payload["scan_type"], "nsfw")
        summary = payload["adult_verification"]
        self.assertEqual(summary["state"], "COMPLETED")
        self.assertEqual(summary["source_report"], "reports/jobs/job/adult/scan.json")
        self.assertEqual(summary["source_report_sha256"],
                         hashlib.sha256(self.project.report.read_bytes()).hexdigest())
        self.assertEqual((summary["scored_interval_count"], summary["failed_interval_count"],
                          summary["frames_scored"]), (3, 0, 24))
        self.assertEqual(summary["model_manifest"]["revision"], REVISION)
        self.assertIn("verification_performance", payload["metrics"])
        self.assertEqual(payload["metrics"]["elapsed_seconds"], 1.0)  # scan metrics kept

    def test_failed_interval_is_kept_and_marked_failed(self):
        payload = self.run_with({0: [51], 1: "fail", 2: [77]})
        self.assertEqual(len(payload["intervals"]), 3)
        failed = payload["intervals"][1]["adult_verification"]
        self.assertEqual(failed["state"], "FAILED")
        self.assertIsNone(failed["nsfw_max"])  # partial frames before the error are discarded
        self.assertEqual(failed["nsfw_frames"], 0)
        self.assertIn("broken", failed["error"])
        self.assertFalse(verification_is_calibrated(failed))
        self.assertEqual(payload["intervals"][1]["start_seconds"], 40.0)
        self.assertEqual(payload["adult_verification"]["state"], "PARTIAL")
        self.assertEqual(payload["adult_verification"]["failed_interval_count"], 1)

    def test_interval_without_frames_is_failed_not_dropped(self):
        payload = self.run_with({0: [], 1: [128], 2: [128]})
        self.assertEqual(payload["intervals"][0]["adult_verification"]["state"], "FAILED")
        self.assertEqual(len(payload["intervals"]), 3)

    def test_every_interval_failing_still_writes_a_failed_verification(self):
        payload = self.run_with({0: "fail", 1: "fail", 2: "fail"})
        self.assertEqual(payload["adult_verification"]["state"], "FAILED")
        self.assertEqual(len(payload["intervals"]), 3)

    def test_changed_source_raises_without_writing(self):
        self.project.source.write_bytes(b"another video")
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            self.run_with({})
        self.assertFalse(self.project.output.exists())

    def test_model_failure_raises_without_writing(self):
        def broken(model_path, device_name):
            raise RuntimeError("CUDA out of memory")

        with patch.object(adult_verification, "_load_scorer", broken), \
                self.assertRaisesRegex(RuntimeError, "out of memory"):
            self.project.verify()
        self.assertFalse(self.project.output.exists())

    def test_scorer_failure_mid_run_stops_cleanly_without_writing(self):
        def failing_scorer(images):
            raise RuntimeError("device lost")

        def load(model_path, device_name):
            return failing_scorer, {"revision": REVISION}, None

        with patch.object(adult_verification, "_load_scorer", load), \
                patch.object(adult_verification, "_interval_frame_batches", _fake_batches({})), \
                self.assertRaisesRegex(RuntimeError, "device lost"):
            self.project.verify()
        self.assertFalse(self.project.output.exists())

    def test_empty_report_needs_no_model(self):
        empty = _Project([])
        self.addCleanup(empty.cleanup)

        def unexpected(model_path, device_name):
            raise AssertionError("model must not load for an empty report")

        with patch.object(adult_verification, "_load_scorer", unexpected):
            payload = empty.verify()
        self.assertEqual(payload["intervals"], [])
        self.assertEqual(payload["adult_verification"]["state"], "COMPLETED")

    def test_rejects_wrong_inputs(self):
        with self.assertRaisesRegex(ValueError, "beside"):
            self.run_with({}, output_path=self.project.root / "reports" / "elsewhere.json")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.run_with({}, output_path=self.project.report)
        gore = _Project(_intervals(), scan_type="gore")
        self.addCleanup(gore.cleanup)
        with self.assertRaisesRegex(ValueError, "nsfw"):
            gore.verify()
        self.run_with({})
        verified = self.project.output
        with self.assertRaisesRegex(ValueError, "already verified"):
            self.run_with({}, report_path=verified,
                          output_path=verified.with_name("scan-verified-2.json"))
        with self.assertRaisesRegex(ValueError, "inside"):
            self.run_with({}, output_path=self.project.root / "outside.json")


class CalibrationTests(unittest.TestCase):
    def scored(self, **extra):
        value = {"state": "SCORED", "nsfw_max": 0.5, "nsfw_frames": 4, "sample_fps": 2.0,
                 "frame_size": 448, "model": "image_safety_classifier_m",
                 "target_label": "NSFW", "revision": REVISION}
        value.update(extra)
        return value

    def test_only_a_measurement_like_the_calibration_counts(self):
        self.assertTrue(verification_is_calibrated(self.scored()))
        for change in ({"state": "FAILED"}, {"revision": "other"}, {"sample_fps": 1.0},
                       {"frame_size": 256}, {"target_label": "NSFL"}, {"nsfw_max": None},
                       {"nsfw_max": True}, {"nsfw_max": 1.5}, {"nsfw_frames": 0},
                       {"model": "other"}, {"sample_fps": "x"}):
            with self.subTest(change=change):
                self.assertFalse(verification_is_calibrated(self.scored(**change)))
        self.assertFalse(verification_is_calibrated(None))

    def test_levels_and_default(self):
        self.assertEqual(ADULT_TRIAGE_LEVEL, "conservative")
        self.assertEqual(ADULT_TRIAGE_LEVELS["conservative"]["two_signal"], {"k": 2, "t": 0.7})
        self.assertEqual(ADULT_TRIAGE_LEVELS["balanced"]["two_signal"], {"k": 5, "t": 0.7})
        self.assertIsNone(ADULT_TRIAGE_LEVELS["off"]["two_signal"])
        self.assertFalse(ADULT_TRIAGE_LEVELS["off"]["credits_rule"])

    def test_decode_command_matches_the_measured_preprocessing(self):
        argv = decode_command(Path("ffmpeg.exe"), Path("movie.mp4"), 12.25, 0.5)
        self.assertEqual(argv[argv.index("-ss") + 1], "12.250")
        self.assertEqual(argv[argv.index("-t") + 1], "0.500")
        self.assertLess(argv.index("-ss"), argv.index("-i"))  # input seek, as in task C
        self.assertEqual(
            argv[argv.index("-vf") + 1],
            "fps=2,scale=448:448:force_original_aspect_ratio=decrease,pad=448:448:(ow-iw)/2:(oh-ih)/2",
        )
        self.assertEqual(argv[-5:], ["-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])

    def test_cli_accepts_verify_and_triage_options(self):
        parser = build_parser()
        args = parser.parse_args(["verify-adult", "--report", "a.json", "--output", "b.json", "--device", "cpu"])
        self.assertEqual(args.model.name, "image_safety_classifier_m")
        self.assertEqual(args.batch_size, 16)
        args = parser.parse_args(["build-review", "--report", "a.json", "--queue", "q.json",
                                  "--content-style", "live_action", "--adult-triage-level", "balanced"])
        self.assertEqual((args.content_style, args.adult_triage_level), ("live_action", "balanced"))
        args = parser.parse_args(["build-review", "--report", "a.json", "--queue", "q.json"])
        self.assertIsNone(args.content_style)
        self.assertIsNone(args.adult_triage_level)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(["build-review", "--report", "a.json", "--queue", "q.json",
                               "--adult-triage-level", "aggressive"])


MODEL = ROOT / "models/image_safety_classifier_m"


@unittest.skipUnless((MODEL / "model.safetensors").exists(), "installed safety classifier required")
class RealVerifierModelTests(unittest.TestCase):
    def test_scorer_returns_the_softmax_nsfw_class_and_gore_keeps_nsfl(self):
        import torch

        from biliflow.content_scanner import _load_classifier

        scorer, manifest, _device = adult_verification._load_scorer(MODEL, "cpu")
        self.assertEqual(manifest["revision"], REVISION)
        images = [_image(30), Image.new("RGB", (448, 448), (220, 160, 140))]
        nsfw = scorer(images)
        gore, labels, indices = _load_classifier("gore", MODEL, torch.device("cpu"))
        self.assertEqual([labels[index] for index in indices], ["NSFL"])
        both, _labels, _indices = _load_classifier(
            "gore", MODEL, torch.device("cpu"), target_labels=("NSFL", "NSFW", "SFW"),
        )
        nsfl = gore(images)[0]
        total = both(images)[0]
        for index in range(len(images)):
            self.assertTrue(0.0 <= nsfw[index] <= 1.0)
            self.assertNotAlmostEqual(nsfw[index], nsfl[index], places=6)
            self.assertAlmostEqual(total[index], 1.0, places=5)  # softmax over the three classes


@unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
class IntervalDecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.video = Path(cls.temp.name) / "clip.mkv"
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=6",
                        "-c:v", "ffv1", str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def events(self, intervals, batch_size=3):
        holder = _ActiveProcess()
        events = list(_interval_frame_batches(FFMPEG, self.video, intervals, batch_size, holder,
                                              threading.Event()))
        self.assertIsNone(holder.process)
        return events

    def test_each_interval_is_decoded_at_two_fps_into_448_letterboxed_frames(self):
        events = self.events([
            {"start_seconds": 1.0, "end_seconds": 3.0},
            {"start_seconds": 4.0, "end_seconds": 4.1},  # shorter than 0.5 s: decoded as 0.5 s
            {"start_seconds": 3.0, "end_seconds": 2.0},  # invalid: reported, others still run
            {"start_seconds": 50.0, "end_seconds": 52.0},  # past the end: no frames
        ])
        frames = {}
        finals = {}
        for kind, index, value in events:
            if kind == "frames":
                self.assertTrue(all(image.size == (448, 448) and image.mode == "RGB" for image in value))
                self.assertLessEqual(len(value), 3)
                frames[index] = frames.get(index, 0) + len(value)
            else:
                finals[index] = (kind, value)
        self.assertEqual(finals[0], ("done", 4))
        self.assertEqual(frames[0], 4)
        self.assertEqual(finals[1], ("done", 1))
        self.assertEqual(finals[2][0], "failed")
        self.assertEqual(finals[3], ("done", 0))
        letterbox = next(value for kind, index, value in events if kind == "frames")[0]
        self.assertEqual(letterbox.getpixel((224, 2)), (0, 0, 0))  # 16:9 source padded top/bottom

    def test_ffmpeg_error_fails_only_that_interval_with_its_message(self):
        broken = Path(self.temp.name) / "broken.mp4"
        broken.write_bytes(b"not a video" * 100)
        holder = _ActiveProcess()
        events = list(_interval_frame_batches(FFMPEG, broken, [{"start_seconds": 0.0, "end_seconds": 1.0}] * 2,
                                              16, holder, threading.Event()))
        self.assertEqual([event[0] for event in events], ["failed", "failed"])
        self.assertIn("FFmpeg failed with exit code", events[0][2])
        self.assertGreater(len(events[0][2]), len("FFmpeg failed with exit code 1: "))  # stderr kept

    def test_cancel_stops_before_the_next_interval(self):
        cancelled = threading.Event()
        holder = _ActiveProcess()
        generator = _interval_frame_batches(
            FFMPEG, self.video, [{"start_seconds": 0.0, "end_seconds": 1.0}] * 3, 16, holder, cancelled,
        )
        first = [next(generator), next(generator)]
        self.assertEqual([event[0] for event in first], ["frames", "done"])
        cancelled.set()
        self.assertEqual(list(generator), [])
        self.assertIsNone(holder.process)


if __name__ == "__main__":
    unittest.main()
