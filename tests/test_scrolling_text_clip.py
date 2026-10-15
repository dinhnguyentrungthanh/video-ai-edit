"""The real text scan on a self-made clip with a scrolling site line (session D, 2026-10-15).

FFmpeg's drawtext scrolls ``EXAMPLE.COM chia sẻ …`` and, later, the same line without
its dot (``EXAMPLECOM``) through the top band, each crossing the frame in 15 s, over
changing subtitles at the bottom. EasyOCR and the semantic model run on the CPU.
Needs the project FFmpeg, the installed models and the semantic seed: found next to
this checkout or under ``BILIFLOW_TEST_INSTALL_ROOT`` (a worktree has none, so the
test is skipped there unless the variable names the install folder).
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
OCR_MODEL = INSTALL / "models/easyocr"
SEMANTIC_MODEL = INSTALL / "models/multilingual_minilm_text_semantics"
SEMANTIC_SEED = INSTALL / "annotations/text_semantics_seed_v1.json"
FONT = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf"
REQUIRED = (FFMPEG, FFPROBE, OCR_MODEL, SEMANTIC_MODEL, SEMANTIC_SEED, FONT)

LINE = "chia sẻ dữ liệu phim, hoạt hình miễn phí - Cập nhật nhanh, chất lượng cao, ổn định và lâu dài."
# (site text, first second the line is on screen); each crosses the frame in 15 s.
TICKERS = (("EXAMPLE.COM", 4.5), ("EXAMPLECOM", 34.5))
CROSSING_SECONDS = 15.0
SUBTITLES = ("Anh đi đâu vậy?", "Tôi phải về nhà ngay.", "Chờ đã, nghe tôi nói.",
             "Không còn thời gian nữa.", "Mẹ đang đợi ở bệnh viện.", "Để tôi đưa anh đi.",
             "Cảm ơn anh nhiều lắm.", "Đi thôi.")
WIDTH, HEIGHT, DURATION = 1280, 640, 54


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")


def make_clip(folder: Path) -> Path:
    font = _filter_path(FONT)
    filters = []
    for index, (site, start) in enumerate(TICKERS):
        text = folder / f"ticker-{index}.txt"
        text.write_text(f"{site} {LINE}", encoding="utf-8")
        filters.append(
            f"drawtext=fontfile='{font}':textfile='{_filter_path(text)}':fontsize=34:fontcolor=white:"
            f"box=1:boxcolor=black@0.45:boxborderw=6:y=30:"
            f"x='w-(t-{start})*(w+tw)/{CROSSING_SECONDS}':"
            f"enable='between(t,{start},{start + CROSSING_SECONDS})'"
        )
    for index, line in enumerate(SUBTITLES):
        text = folder / f"subtitle-{index}.txt"
        text.write_text(line, encoding="utf-8")
        for repeat in (0, 30):
            start = 2 + index * 3.5 + repeat
            filters.append(
                f"drawtext=fontfile='{font}':textfile='{_filter_path(text)}':fontsize=30:"
                f"fontcolor=white:borderw=2:bordercolor=black:x=(w-tw)/2:y=h-70:"
                f"enable='between(t,{start},{start + 3.2})'"
            )
    clip = folder / "input" / "ticker.mp4"
    clip.parent.mkdir(parents=True)
    subprocess.run([
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r=25:d={DURATION}", "-vf", ",".join(filters),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(clip),
    ], check=True)
    return clip


@unittest.skipUnless(all(path.exists() for path in REQUIRED),
                     "project FFmpeg, installed OCR and semantic models and the semantic seed required")
class ScrollingTextClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from biliflow.review_workflow import build_review_queue
        from biliflow.textscan import scan_text

        cls.temporary = TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        (cls.root / "reports").mkdir()
        clip = make_clip(cls.root)
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

    @staticmethod
    def _top_band_text(items, start, end):
        return [
            item for item in items
            if item["category"] == "text"
            and float(item["start_seconds"]) < end and float(item["end_seconds"]) > start
            and isinstance(item.get("suggested_region_source_pixels"), dict)
            and item["suggested_region_source_pixels"]["y"] < HEIGHT * 0.3
        ]

    def test_each_crossing_is_one_main_card_blurred_from_before_its_first_read(self):
        for site, start in TICKERS:
            end = start + CROSSING_SECONDS
            with self.subTest(site=site):
                [card] = self._top_band_text(self.queue["items"], start, end)
                self.assertEqual(card["candidate_type"], "scrolling_text")
                self.assertEqual(card["suggested_decision"], "BLUR")
                # The whole time the line is on screen, the moments between reads included.
                self.assertLessEqual(card["start_seconds"], start)
                self.assertGreaterEqual(card["end_seconds"], end)
                region = card["suggested_region_source_pixels"]
                self.assertEqual((region["x"], region["width"]), (0, WIDTH))
                self.assertLessEqual(region["y"], 34)
                self.assertGreaterEqual(region["y"] + region["height"], 64)
                self.assertEqual(self._top_band_text(self.queue["advisory_items"], start, end), [])

    def test_subtitles_never_become_scrolling_text(self):
        for item in [*self.queue["items"], *self.queue["advisory_items"]]:
            if item.get("candidate_type") == "scrolling_text":
                self.assertLess(item["suggested_region_source_pixels"]["y"], HEIGHT * 0.3)


if __name__ == "__main__":
    unittest.main()
