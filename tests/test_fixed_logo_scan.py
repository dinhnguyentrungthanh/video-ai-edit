"""The text scan with scripted OCR shaped on job 145 (2026-10-16): a fixed logo under a scrolling line.

The OCR reads come from the shape of job 145's real reads (example text): the site's logo is read
in every sample at one box just below the line and, a few times, together with the line above it;
the line itself is read in short pieces. The scan joins the pieces with the logo track (same text,
same corner). The group's box must keep the logo, and the review card built from it must cover it.
Needs only the project FFmpeg (no models): found next to this checkout or under
``BILIFLOW_TEST_INSTALL_ROOT``.
"""
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
INSTALL = Path(os.environ.get("BILIFLOW_TEST_INSTALL_ROOT") or ROOT)
FFMPEG = INSTALL / "tools/ffmpeg/bin/ffmpeg.exe"
FFPROBE = INSTALL / "tools/ffmpeg/bin/ffprobe.exe"

WIDTH, HEIGHT, DURATION, SAMPLE_EVERY = 1280, 536, 120, 3.0  # analysis frame 960 x 402
LOGO_BOX = [644, 38, 862, 68]
# The glyphs of that OCR read, measured on job 145's exports (the OCR box adds padding below).
LOGO_GLYPHS = (865, 55, 1146, 84)
JOINED_BOX = [642, 22, 934, 71]  # the logo read together with the line above it
LINE_BOX = [700, 18, 960, 43]
JOINED_AT, LINE_AT = (30.0, 60.0), (15.0, 45.0, 75.0, 105.0)


def _points(box):
    left, top, right, bottom = box
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


class ScriptedReader:
    """EasyOCR's readtext, one scripted frame per call, in sampling order."""

    def __init__(self):
        self.calls = 0

    def readtext(self, image, **options):
        timestamp = self.calls * SAMPLE_EVERY
        self.calls += 1
        reads = [(_points(JOINED_BOX), "Example.com chia", 0.6) if timestamp in JOINED_AT
                 else (_points(LOGO_BOX), "Example.com", 0.9)]
        if timestamp in LINE_AT:
            reads.append((_points(LINE_BOX), "EXAMPLE.COM chia se phim", 0.95))
        return reads


class ScriptedClassifier:
    """The line reads as an advertisement, the logo alone as credits (as on job 145)."""

    device = "cpu"
    method = "scripted"

    def classify_many(self, texts):
        return [
            {"advertisement": 0.8, "subtitle": 0.0, "credits": 0.1, "scene_text": 0.1, "other": 0.0}
            if "chia" in text else
            {"advertisement": 0.2, "subtitle": 0.0, "credits": 0.75, "scene_text": 0.05, "other": 0.0}
            for text in texts
        ]


@unittest.skipUnless(FFMPEG.exists() and FFPROBE.exists(), "project FFmpeg is required")
class FixedLogoScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from biliflow.review_workflow import build_review_queue
        from biliflow.textscan import scan_text

        cls.temporary = TemporaryDirectory()
        root = Path(cls.temporary.name)
        (root / "reports").mkdir()
        video = root / "input" / "film.mkv"
        video.parent.mkdir()
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c=black:s={WIDTH}x{HEIGHT}:r=1:d={DURATION}", "-c:v", "ffv1", str(video)],
                       check=True)
        report_dir = root / "reports" / "film" / "text"
        cls.reader = ScriptedReader()
        cls.scan = scan_text(
            project_root=root, input_path=video, report_dir=report_dir, model_dir=root / "models",
            ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE, sample_every=SAMPLE_EVERY, device_name="cpu",
            semantic_classifier=ScriptedClassifier(), policy_path=ROOT / "config" / "text_review_policy.json",
            reader=cls.reader,
        )
        cls.queue = build_review_queue(project_root=root, report_paths=[report_dir / "text-scan.json"],
                                       queue_path=root / "reports" / "film" / "review-queue.json", ffmpeg_path=FFMPEG)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_the_scan_joins_the_line_pieces_with_the_logo_track(self):
        self.assertEqual(self.reader.calls, DURATION / SAMPLE_EVERY)
        [group] = [track for track in self.scan["tracks"] if track.get("grouped_track_ids")]
        self.assertEqual(len(group["grouped_track_ids"]), 1 + len(LINE_AT))
        self.assertEqual(group["suggested_decision"], "BLUR")

    def test_the_group_box_keeps_the_logo(self):
        [group] = [track for track in self.scan["tracks"] if track.get("grouped_track_ids")]
        box = group["union_box"]
        self.assertTrue(box[0] <= LOGO_BOX[0] and box[1] <= LOGO_BOX[1]
                        and box[2] >= LOGO_BOX[2] and box[3] >= LOGO_BOX[3], box)
        self.assertEqual(group["overlay_box_sources"]["consensus_box"], LINE_BOX)

    def test_one_blur_card_covers_the_logo_for_the_whole_film(self):
        left, top, right, bottom = LOGO_GLYPHS
        cards = []
        for item in self.queue["items"]:
            region = item.get("suggested_region_source_pixels")
            if (item.get("suggested_decision") == "BLUR" and isinstance(region, dict)
                    and item["start_seconds"] <= SAMPLE_EVERY and item["end_seconds"] >= DURATION - SAMPLE_EVERY
                    and region["x"] <= left and region["y"] <= top
                    and region["x"] + region["width"] >= right and region["y"] + region["height"] >= bottom):
                cards.append(item)
        self.assertEqual(len(cards), 1)


if __name__ == "__main__":
    unittest.main()
