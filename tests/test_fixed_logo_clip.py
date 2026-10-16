"""The real text scan on a self-made clip: a site's fixed logo just below its scrolling line (2026-10-16).

FFmpeg's drawtext scrolls ``EXAMPLE.COM chia sẻ …`` through the top band six times while the
site's logo ``Example.com`` stays just below it, at the right, for the whole clip (job 145's
layout, example text). EasyOCR and the semantic model run on the CPU. Needs the project FFmpeg,
the installed models and the semantic seed: found next to this checkout or under
``BILIFLOW_TEST_INSTALL_ROOT`` (a worktree has none, so the test is skipped there unless the
variable names the install folder).
"""
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
INSTALL = Path(os.environ.get("BILIFLOW_TEST_INSTALL_ROOT") or ROOT)
FFMPEG = INSTALL / "tools/ffmpeg/bin/ffmpeg.exe"
FFPROBE = INSTALL / "tools/ffmpeg/bin/ffprobe.exe"
OCR_MODEL = INSTALL / "models/easyocr"
SEMANTIC_MODEL = INSTALL / "models/multilingual_minilm_text_semantics"
SEMANTIC_SEED = INSTALL / "annotations/text_semantics_seed_v1.json"
FONT = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
REQUIRED = (FFMPEG, FFPROBE, OCR_MODEL, SEMANTIC_MODEL, SEMANTIC_SEED, FONT)

LINE = ("EXAMPLE.COM chia sẻ dữ liệu phim, hoạt hình miễn phí - Cập nhật nhanh, chất lượng cao, "
        "ổn định và lâu dài.")
LOGO = "Example.com"
CROSSINGS = (3, 21, 39, 57, 75, 93)  # each crossing takes 15 s
WIDTH, HEIGHT, DURATION = 1280, 536, 120
LOGO_FILTER = "fontsize=40:fontcolor=white@0.85:borderw=2:bordercolor=black@0.6:x=865:y=52"


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")


def _logo_filter(folder: Path) -> str:
    text = folder / "logo.txt"
    text.write_text(LOGO, encoding="utf-8")
    return f"drawtext=fontfile='{_filter_path(FONT)}':textfile='{_filter_path(text)}':{LOGO_FILTER}"


def make_clip(folder: Path) -> Path:
    line = folder / "line.txt"
    line.write_text(LINE, encoding="utf-8")
    filters = [
        f"drawtext=fontfile='{_filter_path(FONT)}':textfile='{_filter_path(line)}':fontsize=30:fontcolor=white:"
        f"box=1:boxcolor=black@0.45:boxborderw=4:y=22:x='w-(t-{start})*(w+tw)/15':"
        f"enable='between(t,{start},{start + 15})'"
        for start in CROSSINGS
    ]
    filters.append(_logo_filter(folder))
    clip = folder / "input" / "logo.mp4"
    clip.parent.mkdir(parents=True)
    subprocess.run([
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r=25:d={DURATION}", "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(clip),
    ], check=True)
    return clip


def logo_pixels(folder: Path) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the drawn logo, measured on a black frame."""
    frame = subprocess.run([
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", f"color=c=black:s={WIDTH}x{HEIGHT}:d=1", "-vf", _logo_filter(folder),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ], capture_output=True, check=True).stdout
    ys, xs = np.nonzero(np.frombuffer(frame, dtype=np.uint8).reshape((HEIGHT, WIDTH)) > 40)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


@unittest.skipUnless(all(path.exists() for path in REQUIRED),
                     "project FFmpeg, installed OCR and semantic models and the semantic seed required")
class FixedLogoClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from biliflow.review_workflow import build_review_queue
        from biliflow.textscan import scan_text

        cls.temporary = TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        (cls.root / "reports").mkdir()
        clip = make_clip(cls.root)
        cls.logo = logo_pixels(cls.root)
        report_dir = cls.root / "reports" / "clip" / "text"
        cls.scan = scan_text(
            project_root=cls.root, input_path=clip, report_dir=report_dir, model_dir=OCR_MODEL,
            ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE, sample_every=3.0, device_name="cpu",
            semantic_model_dir=SEMANTIC_MODEL, policy_path=ROOT / "config" / "text_review_policy.json",
            semantic_seed_path=SEMANTIC_SEED,
        )
        cls.queue = build_review_queue(
            project_root=cls.root, report_paths=[report_dir / "text-scan.json"],
            queue_path=cls.root / "reports" / "clip" / "review-queue.json", ffmpeg_path=FFMPEG,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _covers(self, region: tuple[float, float, float, float]) -> bool:
        # Within 2 px of the drawn glyphs (OCR boxes are rounded to the analysis frame).
        left, top, right, bottom = self.logo
        return region[0] <= left + 2 and region[1] <= top + 2 and region[2] >= right - 2 and region[3] >= bottom - 2

    def test_logo_box_stays_in_the_report_for_the_whole_clip(self):
        scale = WIDTH / self.scan["analysis_size"][0]
        holders = [
            track for track in self.scan["tracks"]
            if self._covers(tuple(value * scale for value in track["union_box"]))
            and track["start_seconds"] <= 3.0 and track["end_seconds"] >= DURATION - 3.0
        ]
        self.assertTrue(holders, "no report track holds the logo's box for the whole clip")

    def test_one_main_blur_card_covers_the_logo_for_the_whole_clip(self):
        cards = []
        for item in self.queue["items"]:
            region = item.get("suggested_region_source_pixels")
            if (item.get("suggested_decision") == "BLUR" and isinstance(region, dict)
                    and item["start_seconds"] <= 3.0 and item["end_seconds"] >= DURATION - 3.0
                    and self._covers((region["x"], region["y"], region["x"] + region["width"],
                                      region["y"] + region["height"]))):
                cards.append(item)
        self.assertEqual(len(cards), 1, [item.get("candidate_type") for item in cards])


if __name__ == "__main__":
    unittest.main()
