"""scan_text with cross-frame recognition must commit the same tracks/previews."""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "tools/ffmpeg/bin/ffmpeg.exe"
FFPROBE = ROOT / "tools/ffmpeg/bin/ffprobe.exe"


def fake_get_text(character, imgH, imgW, recognizer, converter, image_list, *args):
    # Deterministic per crop: confidence depends only on crop pixels.
    return [(box, "BANNER TEXT", 0.5 + float(crop.mean()) / 1000.0) for box, crop in image_list]


class FakeReader:
    model_lang = "latin"
    character = "abc"
    lang_char = "abc"
    recognizer = converter = None
    device = "cuda"

    def __init__(self):
        self.frames_detected = 0

    def detect(self, color, reformat=False):
        self.frames_detected += 1
        mask = color[..., 0] > 128
        if not mask.any():
            return [[]], [[]]
        ys, xs = np.nonzero(mask)
        boxes = [[int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1]]
        # A second, narrower box in the top-left adds a different width bucket.
        return [boxes + [[4, 40, 4, 20]]], [[]]

    def readtext(self, image, **options):
        from biliflow.ocr_batch_experiment import recognize_same_width
        from easyocr.utils import reformat_input
        color, grey = reformat_input(image)
        horizontal, free = self.detect(color)
        return recognize_same_width(self, grey, horizontal[0], free[0], batch_size=1)


def normalize(value):
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items() if k not in {"metrics", "created_at"}}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


@unittest.skipUnless(FFMPEG.exists() and FFPROBE.exists(), "project FFmpeg is required")
class ScanTextCrossFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        (cls.root / "reports").mkdir()
        cls.video = cls.root / "moving.mkv"
        # Lossless: a bright box moves and briefly disappears (empty frames).
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x180:r=1:d=11",
            "-f", "lavfi", "-i", "color=c=white:s=96x24:r=1:d=11", "-filter_complex",
            "[0][1]overlay=x='40+t*8':y=120:enable='not(between(t,5,6))'",
            "-c:v", "ffv1", str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def scan(self, name, **kwargs):
        from biliflow.textscan import scan_text
        reader = FakeReader()
        with mock.patch("easyocr.recognition.get_text", fake_get_text):
            report = scan_text(project_root=self.root, input_path=self.video,
                report_dir=self.root / "reports" / name, model_dir=self.root / "models",
                ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE, sample_every=1.0, analysis_width=320,
                reader=reader, **kwargs)
        previews = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((self.root / "reports" / name / "text-previews").glob("*.jpg"))}
        return report, previews, reader

    def test_cross_frame_windows_match_serial_tracks_and_preview_frames(self):
        serial, serial_previews, _ = self.scan("serial")
        self.assertEqual(serial["frames_scanned"], 11)
        from biliflow.textscan import _sha256_file
        self.assertEqual(serial["input_sha256"], _sha256_file(self.video))
        self.assertTrue(serial["tracks"])
        self.assertTrue(serial_previews)
        self.assertIsNone(serial["metrics"]["cross_frame_recognition"])
        for window in (1, 3, 4, 8):
            report, previews, reader = self.scan(f"window-{window}", recognition_batch_size=8,
                                                 recognition_frame_window=window)
            self.assertEqual(normalize(report), normalize(serial), window)
            self.assertEqual(previews, serial_previews, window)
            self.assertEqual(reader.frames_detected, 11)
            self.assertEqual(report["metrics"]["recognition_frame_window"], window)
            stats = report["metrics"]["cross_frame_recognition"]
            if window == 1:
                self.assertIsNone(stats)
            else:
                self.assertEqual(stats["frames_emitted"], 11)
                self.assertEqual(stats["max_group_frames"], min(window, 11))

    def test_unverified_stream_requested_as_nvdec_falls_back_to_identical_cpu_scan(self):
        serial, serial_previews, _ = self.scan("serial-for-decode")
        report, previews, _ = self.scan("nvdec-fallback", decode_backend="nvdec")
        self.assertEqual(normalize(report), normalize(serial))
        self.assertEqual(previews, serial_previews)
        decode = report["metrics"]["decode"]
        self.assertEqual((decode["requested"], decode["effective"]), ("nvdec", "cpu"))
        self.assertIn("not verified for ffv1", decode["fallback_reason"])
        self.assertEqual(serial["metrics"]["decode"],
                         {"requested": "cpu", "effective": "cpu", "fallback_reason": None})

    def test_frame_window_validation(self):
        from biliflow.textscan import scan_text
        common = dict(project_root=self.root, input_path=self.video, report_dir=self.root / "reports/x",
                      model_dir=self.root, ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE)
        for window in (0, 9, True, 2.0):
            with self.assertRaisesRegex(ValueError, "recognition_frame_window"):
                scan_text(**common, recognition_batch_size=8, recognition_frame_window=window)
        with self.assertRaisesRegex(ValueError, "requires recognition_batch_size"):
            scan_text(**common, recognition_frame_window=4)

    def test_cli_frame_window_defaults_to_one(self):
        from biliflow.cli import build_parser
        parser = build_parser()
        args = ["scan-text", "--input", "movie.mp4", "--report-dir", "reports/test"]
        self.assertEqual(parser.parse_args(args).recognition_frame_window, 1)
        self.assertEqual(parser.parse_args(args + ["--recognition-frame-window", "4"]).recognition_frame_window, 4)
        with self.assertRaises(SystemExit):
            parser.parse_args(args + ["--recognition-frame-window", "9"])


if __name__ == "__main__":
    unittest.main()
