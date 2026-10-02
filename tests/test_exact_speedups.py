"""Background source hashing in the safety scanners (exact-output speedup T5a).

Each test drives the real scanner with fake decoders/models: the reported
input_sha256 must be the file's SHA-256 without a serial re-hash after the scan,
and the model inputs keep their serial batch boundaries.
"""
import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import torch
from PIL import Image

PREFETCH_THREAD = "biliflow-iterator-prefetch"


class _PackedFfmpeg:
    """Fake FFmpeg stdout for scan_live_safety: packed [gore | violence] 256x256 halves."""

    def __init__(self, count: int):
        self.frames = [self.packed(index) for index in range(count)]
        self.stdout = io.BytesIO(b"".join(frame.tobytes() for frame in self.frames))
        self.stderr = io.BytesIO(b"")

    @staticmethod
    def packed(index: int) -> np.ndarray:
        rng = np.random.default_rng(1000 + index)
        frame = rng.integers(0, 256, (256, 512, 3), dtype=np.uint8)
        base = (index * 37) % 200  # gore halves differ in brightness, so scores straddle 0.5
        frame[:, :256, :] = rng.integers(base, base + 56, (256, 256, 3), dtype=np.uint8)
        return frame

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


class _FakeGoreScorer:
    """score(images) on the main thread, as content_scanner's gore scorer is called."""

    def __init__(self):
        self.batches = []

    @staticmethod
    def image_score(image):
        return float(torch.sigmoid(torch.tensor((np.asarray(image).mean() - 128.0) / 20.0)))

    def __call__(self, images):
        self.batches.append(([np.asarray(image).copy() for image in images], threading.current_thread().name))
        values = [self.image_score(image) for image in images]
        return values, ["NSFL"] * len(values)


class _FakeViolenceModel:
    def to(self, device):
        return self

    def eval(self):
        return self

    def load_state_dict(self, state, strict=False):
        return SimpleNamespace(missing_keys=[], unexpected_keys=[])

    def __call__(self, pixels):
        mean = pixels.mean(dim=(1, 2, 3))
        return torch.stack([torch.zeros_like(mean), mean * 4.0], dim=1)


class LiveSafetyHashTests(unittest.TestCase):
    PACKED_FRAMES = 160  # 20 s at 8 fps: 40 gore frames in batches of 16, 16 and 8

    def run_live(self, scorer, ffmpeg, violence_model):
        """Run scan_live_safety on fakes; returns (result or error, input digest)."""
        from biliflow.live_safety_scanner import scan_live_safety

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            gore_dir, violence_dir = root / "gore-model", root / "violence-model"
            gore_dir.mkdir()
            violence_dir.mkdir()
            (gore_dir / "manifest.json").write_text(json.dumps({"model": "gore"}), encoding="utf-8")
            (violence_dir / "manifest.json").write_text(json.dumps({
                "backend": "timm_frame_video", "architecture": "fake", "label_names": ["No", "Violent"],
                "positive_index": 1, "aggregation_top_k": 5,
            }), encoding="utf-8")
            video = root / "input.mp4"
            video.write_bytes(b"live action source bytes" * 1000)
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                (root / name).write_bytes(b"x")
            with mock.patch("biliflow.live_safety_scanner._load_classifier",
                            return_value=(scorer, ["Normal", "NSFW", "NSFL"], [2])), \
                    mock.patch("timm.create_model", return_value=violence_model), \
                    mock.patch("biliflow.live_safety_scanner.load_file", return_value={}), \
                    mock.patch("biliflow.live_safety_scanner.require_capacity"), \
                    mock.patch("biliflow.live_safety_scanner.probe_video", return_value={}), \
                    mock.patch("biliflow.live_safety_scanner.duration_seconds", return_value=20.0), \
                    mock.patch("biliflow.scanner.sha256_file",
                               side_effect=AssertionError("no serial re-hash after the scan")), \
                    mock.patch("biliflow.live_safety_scanner.subprocess.Popen", return_value=ffmpeg):
                try:
                    result = scan_live_safety(
                        project_root=root, input_path=video, report_dir=root / "reports" / "job",
                        gore_model_path=gore_dir, violence_model_path=violence_dir,
                        ffmpeg_path=root / "ffmpeg.exe", ffprobe_path=root / "ffprobe.exe",
                        device_name="cpu",
                    )
                except RuntimeError as error:
                    result = error
            digest = hashlib.sha256(video.read_bytes()).hexdigest()
        return result, digest

    def test_reports_carry_the_file_digest_and_serial_gore_batches(self):
        from biliflow.intervals import group_hits
        from biliflow.scanner import _score_summary

        scorer = _FakeGoreScorer()
        ffmpeg = _PackedFfmpeg(self.PACKED_FRAMES)
        result, digest = self.run_live(scorer, ffmpeg, _FakeViolenceModel())

        gore_images = [np.ascontiguousarray(frame[:, :256, :]) for frame in ffmpeg.frames[::4]]
        self.assertEqual([len(batch) for batch, _ in scorer.batches], [16, 16, 8])
        received = [image for batch, _ in scorer.batches for image in batch]
        self.assertTrue(all(np.array_equal(got, want) for got, want in zip(received, gore_images, strict=True)))
        self.assertEqual({thread for _, thread in scorer.batches}, {"MainThread"})

        gore = result["gore"]
        expected_scores = [_FakeGoreScorer.image_score(Image.fromarray(image)) for image in gore_images]
        self.assertEqual(gore["frames_scanned"], 40)
        self.assertEqual(gore["score_summary"], _score_summary(expected_scores))
        hits = [{"frame_index": index, "timestamp_seconds": round(index / 2.0, 3), "score": score}
                for index, score in enumerate(expected_scores) if score >= 0.5]
        self.assertTrue(0 < len(hits) < 40)
        self.assertEqual([(i["start_seconds"], i["end_seconds"]) for i in gore["intervals"]],
                         [(i["start_seconds"], i["end_seconds"]) for i in group_hits(hits, 2.0, 1.0, 20.0)])
        self.assertEqual(gore["input_sha256"], digest)
        self.assertEqual(result["violence"]["input_sha256"], digest)
        self.assertEqual(result["violence"]["frames_scanned"], self.PACKED_FRAMES)
        self.assertEqual(result["violence"]["windows_scored"], (self.PACKED_FRAMES - 16) // 8 + 1)
        self.assertIn("source_hash", gore["metrics"]["performance"]["phases"])

    def test_model_failure_raises_after_the_hash_and_stops_the_producer(self):
        class FailingViolenceModel(_FakeViolenceModel):
            calls = 0

            def __call__(self, pixels):
                FailingViolenceModel.calls += 1
                if FailingViolenceModel.calls == 3:
                    raise RuntimeError("CUDA error: device-side assert")
                return super().__call__(pixels)

        error, _ = self.run_live(_FakeGoreScorer(), _PackedFfmpeg(self.PACKED_FRAMES), FailingViolenceModel())
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("device-side assert", str(error))
        self.assertFalse(any(thread.name in (PREFETCH_THREAD, "source-sha256") for thread in threading.enumerate()))


class _RawFfmpeg:
    def __init__(self, frames):
        self.stdout = io.BytesIO(b"".join(frame.tobytes() for frame in frames))
        self.stderr = io.BytesIO(b"")

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


class AnimationSafetyHashTests(unittest.TestCase):
    def test_reports_carry_the_file_digest_without_a_serial_rehash(self):
        from biliflow import animation_policy as policy
        from biliflow.animation_safety_scanner import scan_animation_safety

        labels = sorted(set(policy.GORE_LABELS + policy.GORE_CONTEXT_LABELS + policy.DIRECT_VIOLENCE_LABELS
                            + policy.DANGER_LABELS + policy.ADULT_EXPLICIT_LABELS)) + ["general"]

        class Tagger:
            def to(self, device):
                return self

            def eval(self):
                return self

            def load_state_dict(self, state):
                return None

            def __call__(self, pixels):
                return pixels.mean(dim=(1, 2, 3)).unsqueeze(1).repeat(1, len(labels)) - 3.0

        frames = [np.random.default_rng(index).integers(0, 256, (256, 256, 3), dtype=np.uint8)
                  for index in range(12)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            model_dir = root / "tagger"
            model_dir.mkdir()
            (model_dir / "config.json").write_text(json.dumps({
                "architecture": "fake", "num_classes": len(labels),
                "pretrained_cfg": {"input_size": [3, 32, 32], "interpolation": "bicubic", "crop_pct": 1.0,
                                   "mean": [0.5, 0.5, 0.5], "std": [0.5, 0.5, 0.5]},
            }), encoding="utf-8")
            (model_dir / "manifest.json").write_text(json.dumps({"model": "tagger"}), encoding="utf-8")
            (model_dir / "selected_tags.csv").write_text(
                "tag_id,name,category,count\n" + "".join(f"{i},{name},0,1\n" for i, name in enumerate(labels)),
                encoding="utf-8")
            video = root / "anime.mkv"
            video.write_bytes(b"animation source bytes" * 100)
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                (root / name).write_bytes(b"x")
            with mock.patch("biliflow.animation_safety_scanner.timm.create_model", return_value=Tagger()), \
                    mock.patch("biliflow.animation_safety_scanner.load_file", return_value={}), \
                    mock.patch("biliflow.animation_safety_scanner.require_capacity"), \
                    mock.patch("biliflow.animation_safety_scanner.probe_video", return_value={}), \
                    mock.patch("biliflow.animation_safety_scanner.duration_seconds", return_value=12.0), \
                    mock.patch("biliflow.scanner.sha256_file",
                               side_effect=AssertionError("no serial re-hash after the scan")), \
                    mock.patch("biliflow.animation_safety_scanner.subprocess.Popen",
                               return_value=_RawFfmpeg(frames)):
                summary = scan_animation_safety(
                    project_root=root, input_path=video, report_dir=root / "reports" / "job",
                    model_path=model_dir, ffmpeg_path=root / "ffmpeg.exe", ffprobe_path=root / "ffprobe.exe",
                    sample_fps=1.0, batch_size=4, device_name="cpu",
                )
            reports = {category: json.loads(Path(item["report"]).read_text(encoding="utf-8"))
                       for category, item in summary["reports"].items()}
            digest = hashlib.sha256(video.read_bytes()).hexdigest()
        self.assertEqual(set(reports), {"adult", "gore", "violence"})
        self.assertEqual(summary["input_sha256"], digest)
        self.assertEqual({category: report["input_sha256"] for category, report in reports.items()},
                         {category: digest for category in reports})
        self.assertEqual({report["frames_scanned"] for report in reports.values()}, {12})


if __name__ == "__main__":
    unittest.main()
