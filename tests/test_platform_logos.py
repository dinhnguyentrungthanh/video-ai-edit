"""Platform idents (batch 4a, 2026-10-03): names, span/region probe, memory runs, cards."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np

from biliflow.brand_memory import studio_logo_window_frames
from biliflow.codex_supervisor import queue_quality_findings
from biliflow.platform_cards import _groups, _platform_card, build_platform_cards
from biliflow.platform_logos import (
    DECODE_MAX_FRAMES,
    PLATFORM_MEMORY_CLASS,
    _memory_windows,
    ident_span,
    load_frame_image,
    logo_region,
    platform_memory_runs,
    scale_box,
)
from biliflow.platform_names import PLATFORMS, match_platform_text
from biliflow.review_workflow import build_review_queue

DURATION = 2703.893  # Tập 17 (job 47)
PROMO_TERMS = ("quảng bá", "promotional", "promotion", "advertisement", "branded intro")

FPS = 25.0
SIZE = (134, 320)  # analysis frame (height, width) of a 1280x534 source
LOGO = (129, 49, 187, 83)  # x0, y0, x1, y1 of the fully formed logo
OCR_BOX = (126.0, 52.3, 191.3, 77.1)  # track 1 "iOlYI" in analysis pixels


def _times(start, end, step=1 / FPS):
    count = int(round((end - start) / step))
    return [round(start + index * step, 3) for index in range(count)]


def _picture(seed=0, colour=None):
    if colour is not None:
        frame = np.zeros(SIZE + (3,), dtype=np.uint8)
        frame[:] = colour
        return frame
    rng = np.random.default_rng(seed)
    base = np.linspace(70, 210, SIZE[1], dtype=np.float32)[None, :, None]
    noise = rng.integers(-20, 20, size=SIZE + (3,))
    return np.clip(base + noise, 0, 255).astype(np.uint8)


def _black():
    return np.zeros(SIZE + (3,), dtype=np.uint8)


def _logo(frame=None, box=LOGO, fill=0.5, colour=(90, 230, 120)):
    """Stripes inside ``box`` so about ``fill`` of its pixels are bright."""
    frame = _black() if frame is None else frame.copy()
    x0, y0, x1, y1 = box
    period = max(2, round(1 / fill))
    for y in range(y0, y1):
        if (y - y0) % period == 0:
            frame[y, x0:x1] = colour
    frame[y0:y1, x0] = colour
    frame[y0:y1, x1 - 1] = colour
    return frame


def _particle(x, y=55, frame=None):
    frame = _black() if frame is None else frame.copy()
    frame[y:y + 18, x:x + 5] = (255, 255, 255)
    return frame


def _opening_ident():
    """Tập 17 opening at 25 fps: cut to black 8.00, particles, logo 9.60–12.96, black, picture."""
    frames = []
    for moment in _times(6.0, 16.0):
        if moment < 8.0:
            frame = _picture(1)
        elif moment < 8.2:
            frame = _black()
        elif moment < 8.84:
            frame = _particle(259)
        elif moment < 8.92:
            frame = _particle(202)
        elif moment < 9.6:
            frame = _particle(150, y=56)
        elif moment < 11.6:
            frame = _logo(box=(129, 55, 129 + int(10 + (moment - 9.6) * 24), 73))
        elif moment < 13.0:
            frame = _logo()
        elif moment < 13.52:
            frame = _black()
        else:
            frame = _picture(2)
        frames.append((moment, frame))
    return frames


class PlatformNameTests(unittest.TestCase):
    def test_iqiyi_ocr_variants_match(self):
        for text in (
            "iOlYI", "iOIYI", "iOly '", "iQlYI", "iQIYI", "IQIYI", "i0IYI",
            "iQ|YI", "i Qiyi", "iQIYI 爱奇艺", "爱奇艺出品", "YES | iQIYI",
        ):
            with self.subTest(text=text):
                match = match_platform_text(text)
                self.assertIsNotNone(match)
                self.assertEqual(match["key"], "iqiyi")
                self.assertEqual(match["name"], "iQIYI")
                self.assertEqual(match["text"], text.strip())
        self.assertEqual(match_platform_text("爱奇艺出品")["rule"], "chinese")
        self.assertEqual(match_platform_text("iOlYI")["rule"], "latin")
        self.assertEqual(PLATFORMS["iqiyi"]["name"], "iQIYI")

    def test_other_platforms_match(self):
        cases = (
            ("Youku", "youku", "latin"), ("优酷独播", "youku", "chinese"),
            ("Tencent Video", "tencent_video", "latin"),
            ("TENCENT VIDE0", "tencent_video", "latin"),
            ("Tencent Vdeo", "tencent_video", "latin_near"),
            ("WeTV", "tencent_video", "latin"),
            ("v.qq.com", "tencent_video", "latin"),
            ("腾讯视频", "tencent_video", "chinese"),
            ("Mango TV", "mango_tv", "latin"), ("MangoTV", "mango_tv", "latin"),
            ("芒果TV", "mango_tv", "chinese"),
            ("SOHU", "sohu", "latin"), ("Sohu Video", "sohu", "latin"),
            ("搜狐视频", "sohu", "chinese"),
            ("PPTV", "pptv", "latin"), ("PP视频", "pptv", "chinese"),
        )
        for text, key, rule in cases:
            with self.subTest(text=text):
                match = match_platform_text(text)
                self.assertIsNotNone(match)
                self.assertEqual((match["key"], match["rule"]), (key, rule))

    def test_bilibili_and_short_words_never_match(self):
        for text in (
            "DEN", "42M", "EHHA LAU", "bilibili", "BiliBili 哔哩哔哩", "B站",
            "哔哩哔哩", "iQIYI bilibili", "mango", "Mango juice", "Tencent",
            "video", "TV", "PP", "iQ", "QIY", "Wet", "", "   ", None,
        ):
            with self.subTest(text=text):
                self.assertIsNone(match_platform_text(text))

    def test_letter_b_and_look_alike_words(self):
        # "B站" is excluded as text, not as the letter b (review of 2026-10-03).
        for text, key in (("Yes, iQIYI logo on black background", "iqiyi"), ("YOUKU brand", "youku"),
                          ("iQIYI and a bird", "iqiyi"), ("Tencent Video banner", "tencent_video")):
            with self.subTest(text=text):
                self.assertEqual(match_platform_text(text)["key"], key)
        for text in ("B站 iQIYI", "芒果", "新鲜芒果汁", "ZOO COM", "Z00.COM"):
            with self.subTest(text=text):
                self.assertIsNone(match_platform_text(text), "the fruit and domain look-alikes are no platform")
        self.assertEqual(match_platform_text("芒果TV 独播")["key"], "mango_tv")


class IdentProbeTests(unittest.TestCase):
    def test_span_snaps_to_cut_and_logo_end(self):
        span = ident_span(_opening_ident(), 10.5, OCR_BOX, (), 2703.893, bounds=(7.5, 13.5))
        self.assertEqual((span["start"], span["end"]), (8.0, 13.0))
        self.assertEqual(span["method"], "dark_run")
        self.assertTrue(span["start_cut"])
        self.assertIsNone(span["reason"])
        # The ending ident runs to the last frame: the span ends one frame after it.
        ending = [(moment, _picture(3) if moment < 2699.68 else _logo(box=(103, 55, 218, 74)))
                  for moment in _times(2698.0, 2703.68)]
        span = ident_span(ending, 2701.5, (133.3, 52.3, 198.7, 77.1), (), 2703.893)
        self.assertEqual((span["start"], span["end"], span["method"]), (2699.68, 2703.68, "dark_run"))

    def test_fade_and_particles_stay_out_of_region(self):
        frames = _opening_ident()
        # A sparse sparkle pattern over the whole frame inside the span (a fade-in has the same
        # low pixel density) and the fade-in picture after the span never join the region.
        sparse = _black()
        sparse[::9, ::9] = (200, 200, 200)
        frames = [(moment, sparse if abs(moment - 12.0) < 1e-6 else frame) for moment, frame in frames]
        region = logo_region(frames, (8.0, 13.0))
        self.assertEqual(region["method"], "logo_pixels")
        x0, y0, x1, y1 = region["box"]
        self.assertAlmostEqual(x0, 129 - 9.6, places=3)
        self.assertAlmostEqual(y0, 49 - 4.02, places=3)
        self.assertAlmostEqual(x1, 187 + 9.6, places=3)
        self.assertAlmostEqual(y1, 83 + 4.02, places=3)
        self.assertNotIn(12.0, region["frames"])
        self.assertTrue(all(9.6 <= moment < 13.0 for moment in region["frames"]),
                        "particles at 8.2-9.6 s stay out (far away, or a tenth of the logo)")
        self.assertIsNone(logo_region([(13.6, _picture(2)), (14.0, np.full(SIZE + (3,), 30, np.uint8))]))

    def test_logo_over_picture_uses_hard_cuts_then_ocr_bounds(self):
        shots = []
        for moment in _times(0.0, 8.0):
            if moment < 2.0:
                frame = _picture(colour=(200, 60, 60))
            elif moment < 6.0:
                frame = _logo(_picture(colour=(60, 60, 200)))
            else:
                frame = _picture(colour=(60, 200, 60))
            shots.append((moment, frame))
        span = ident_span(shots, 3.5, OCR_BOX, (), 100.0, bounds=(2.0, 6.5))
        self.assertEqual((span["start"], span["end"], span["method"]), (2.0, 6.0, "hard_cuts"))
        self.assertEqual(span["reason"], "hint_frame_not_dark")
        static = [(moment, _logo(_picture(colour=(60, 60, 200)))) for moment in _times(0.0, 8.0)]
        span = ident_span(static, 3.5, OCR_BOX, (), 100.0, bounds=(2.0, 6.5))
        self.assertEqual((span["start"], span["end"], span["method"]), (2.0, 6.5, "ocr_bounds"))
        self.assertEqual(ident_span([], 3.5, OCR_BOX, bounds=(2.0, 6.5))["reason"], "no_frames")


class _FakeProbe:
    reason = None
    decodes = 0
    decoded_frames = 0

    def __init__(self, frames):
        self._frames = frames

    def info(self):
        return {"rate": FPS, "source_size": (1280, 534), "analysis_size": (SIZE[1], SIZE[0])}

    def frames(self, start, end, step=None):
        return [(moment, frame) for moment, frame in self._frames if start <= moment < end]


class _RecordingProbe(_FakeProbe):
    """A probe at another frame rate that remembers the windows asked for."""

    def __init__(self, frames, rate):
        super().__init__(frames)
        self.rate = rate
        self.windows = []

    def info(self):
        return {**super().info(), "rate": self.rate}

    def frames(self, start, end, step=None):
        self.windows.append((start, end))
        return super().frames(start, end, step)


def _memory_run(start, end, key="film:a", box=(0.373, 0.336, 0.244, 0.314), similarity=0.97):
    return {"platform": "iqiyi", "name": "iQIYI", "record_key": key, "start": start, "end": end,
            "hint": round((start + end) / 2, 3), "similarity": similarity, "cell_difference": 8, "frames": 3,
            "box_relative": list(box)}


class PlatformMemoryRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def record(self, frames, regions=()):
        folder = Path("state/studio-logo-frames/film-review-logo")
        (self.root / folder).mkdir(parents=True, exist_ok=True)
        stored = []
        for moment, frame in frames:
            name = f"{moment:.3f}.jpg"
            ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                                       [cv2.IMWRITE_JPEG_QUALITY, 88])
            self.assertTrue(ok)
            (self.root / folder / name).write_bytes(encoded.tobytes())
            stored.append({"t": moment, "image": (folder / name).as_posix()})
        return {"key": "film:review-logo", "memory_class": PLATFORM_MEMORY_CLASS, "decision": "BLUR",
                "platform": {"key": "iqiyi", "name": "iQIYI"},
                "blur_region": {"box": [0.373, 0.336, 0.244, 0.314]},
                "stored_frames": stored, "logo_frame_times": [moment for moment, _ in frames],
                "ignored_regions": list(regions)}

    def runs(self, record, frames, **kwargs):
        return platform_memory_runs(self.root, [record], _FakeProbe(frames), duration=100.0, **kwargs)

    def test_any_frame_match_gives_one_run(self):
        record = self.record([(12.0, _logo())])
        other = _logo(box=(20, 20, 80, 60))
        frames = [(moment, _logo() if moment in (10.4, 11.2, 12.0) else other if moment < 13 else _black())
                  for moment in _times(0.0, 30.0, 0.2)]
        frames.append((80.0, _logo()))
        runs, diagnostics = self.runs(record, frames)
        self.assertEqual([(run["start"], run["end"], run["frames"]) for run in runs],
                         [(10.4, 12.2, 3), (80.0, 80.2, 1)])
        self.assertEqual(runs[0]["hint"], 11.2)
        self.assertEqual((runs[0]["platform"], runs[0]["name"]), ("iqiyi", "iQIYI"))
        self.assertGreaterEqual(runs[0]["similarity"], 0.95)
        self.assertEqual(diagnostics["runs"], 2)

    def test_black_and_fade_frames_never_match(self):
        record = self.record([(12.0, _logo())])
        fades = [(moment, np.full(SIZE + (3,), int(moment * 2), np.uint8)) for moment in _times(0.0, 20.0, 0.2)]
        runs, _ = self.runs(record, [(moment, _black()) for moment in _times(20.0, 30.0, 0.2)] + fades)
        self.assertEqual(runs, [])
        black_record = self.record([(12.0, _black())])
        runs, _ = self.runs(black_record, [(moment, _black()) for moment in _times(0.0, 30.0, 0.2)])
        self.assertEqual(runs, [], "a black stored frame matches nothing either")

    def test_queue_watermark_boxes_are_masked_on_both_sides(self):
        marked = _logo()
        marked[4:22, 4:80] = (255, 255, 255)  # the episode's own site watermark
        record = self.record([(12.0, marked)])
        clean = [(moment, _logo()) for moment in _times(10.0, 11.0, 0.2)]
        runs, _ = self.runs(record, clean)
        self.assertEqual(runs, [], "the remembered frame still carries the watermark")
        watermark = [{"box": [4 / 320, 4 / 134, 76 / 320, 18 / 134]}]
        runs, _ = self.runs(record, clean, queue_regions=watermark)
        self.assertEqual([(run["start"], run["frames"]) for run in runs], [(10.0, 5)])
        masked_record = self.record([(12.0, marked)], regions=watermark)
        runs, _ = self.runs(masked_record, clean)
        self.assertEqual(len(runs), 1, "the record's own ignored region masks both sides too")

    def test_short_videos_are_scanned_whole(self):
        self.assertEqual(_memory_windows(20.0), [(0.0, 20.0)])
        self.assertEqual(_memory_windows(45.0), [(0.0, 45.0)], "30–45 s is the tail of a 45 s video")
        self.assertEqual(_memory_windows(60.0), [(0.0, 60.0)])
        self.assertEqual(_memory_windows(100.0), [(0.0, 30.0), (70.0, 100.0)])

    def test_unreadable_stored_frames_are_skipped(self):
        record = self.record([(12.0, _logo())])
        folder = "state/studio-logo-frames/film-review-logo"
        for name, data in (("13.000.jpg", b""), ("14.000.jpg", b"not a jpeg")):
            (self.root / folder / name).write_bytes(data)
            self.assertIsNone(load_frame_image(self.root, f"{folder}/{name}"), name)
            record["stored_frames"].append({"t": float(name[:6]), "image": f"{folder}/{name}"})
            record["logo_frame_times"].append(float(name[:6]))
        runs, _ = self.runs(record, [(moment, _logo()) for moment in _times(10.0, 11.0, 0.2)])
        self.assertEqual([(run["start"], run["frames"]) for run in runs], [(10.0, 5)],
                         "a broken frame never stops the readable ones from matching")


class PlatformGroupTests(unittest.TestCase):
    """Readings and remembered-logo runs become one group per ident (``_groups``)."""

    @staticmethod
    def _starts(groups):
        return [([hit["window"][0] for hit in group["hits"]], [run["start"] for run in group["runs"]])
                for group in groups]

    def test_runs_without_a_reading_group_by_ident(self):
        runs = [_memory_run(2700.0, 2703.6), _memory_run(10.4, 12.2), _memory_run(11.0, 12.6, "film:b")]
        self.assertEqual(self._starts(_groups([], runs)), [([], [10.4, 11.0]), ([], [2700.0])])
        hit = {"platform": "iqiyi", "window": [7.5, 13.5]}
        runs = [_memory_run(2700.0, 2702.0), _memory_run(2702.2, 2703.6, "film:b")]
        self.assertEqual(self._starts(_groups([hit], runs)), [([7.5], []), ([], [2700.0, 2702.2])])

    def test_runs_without_a_reading_build_one_card_per_ident(self):
        runs = [_memory_run(10.4, 12.2), _memory_run(11.0, 12.6, "film:b"), _memory_run(2700.0, 2703.6)]
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            cards, diagnostics = build_platform_cards(
                root, [], [], {}, source=None, duration=DURATION, frame_size=(1280, 534),
                queue_dir=root / "reports" / "job", masks=[], memory_runs=runs,
                probe=_FakeProbe(_opening_ident()))
        self.assertEqual(diagnostics["skipped"], [])
        self.assertEqual([[value["kind"] for value in card["platform_logo"]["detections"]] for card in cards],
                         [["platform_memory", "platform_memory"], ["platform_memory"]])
        self.assertLess(cards[0]["end_seconds"], 20.0)
        self.assertGreater(cards[1]["start_seconds"], 2690.0)
        self.assertEqual([card["platform_logo"]["region"]["method"] for card in cards],
                         ["logo_pixels", "platform_memory"])


class PlatformCardRegionTests(unittest.TestCase):
    """The blur region of one platform card when a remembered logo also matched."""

    FRAME = (1280, 534)
    # Tập 17's outro layout, seeded into memory; its dark centred ident also matched one
    # opening frame (11.4 s, similarity 0.96875) in the e2e run of 2026-10-03.
    ENDING_LAYOUT = [0.291875, 0.380448, 0.4225, 0.201791]

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

    def tearDown(self):
        self.temporary.cleanup()

    def _hit(self):
        x, y = self.FRAME[0] / SIZE[1], self.FRAME[1] / SIZE[0]
        return {"platform": "iqiyi", "kind": "ocr_text", "text": "iOlYI", "rule": "ocr",
                "label_seconds": 9.0, "observed_seconds": 10.5, "last_observed_seconds": 10.5,
                "window": [7.5, 13.5], "box_source_px": scale_box(OCR_BOX, x, y), "confidence": 0.72,
                "ref": "reports/job/text/text-scan.json#track:1", "report": "reports/job/text/text-scan.json",
                "preview": None}

    def _run(self):
        return {**_memory_run(11.4, 11.6, "film:seed-ending", box=self.ENDING_LAYOUT, similarity=0.96875),
                "hint": 11.4, "cell_difference": 16, "frames": 1}

    def _region(self, frames, hits, runs):
        card, reason = _platform_card(
            self.root, {"platform": "iqiyi", "hits": hits, "runs": runs}, probe=_FakeProbe(frames),
            duration=DURATION, frame_size=self.FRAME, queue_dir=self.root / "reports" / "job", regions=[])
        self.assertIsNone(reason)
        region = card["suggested_region_source_pixels"]
        box = (region["x"], region["y"], region["x"] + region["width"], region["y"] + region["height"])
        return card["platform_logo"]["region"]["method"], box

    def test_measured_logo_pixels_win_over_a_remembered_box(self):
        method, measured = self._region(_opening_ident(), [self._hit()], [])
        self.assertEqual(method, "logo_pixels")
        method, box = self._region(_opening_ident(), [self._hit()], [self._run()])
        self.assertEqual(method, "logo_pixels")
        self.assertEqual(box, measured, "a record from another layout must not widen the blur")
        method, box = self._region(_opening_ident(), [], [self._run()])
        self.assertEqual((method, box), ("logo_pixels", measured), "the run only points the probe")

    def test_probe_window_fits_the_decode_cap_at_high_frame_rates(self):
        windows = {}
        for rate in (25.0, 60.0):
            probe = _RecordingProbe(_opening_ident(), rate)
            card, _ = _platform_card(
                self.root, {"platform": "iqiyi", "hits": [self._hit()], "runs": []}, probe=probe,
                duration=DURATION, frame_size=self.FRAME, queue_dir=self.root / "reports" / "job", regions=[])
            self.assertIsNotNone(card)
            [windows[rate]] = probe.windows
        self.assertEqual(windows[25.0], (0.0, 21.5), "25 fps keeps the whole window")
        start, end = windows[60.0]
        self.assertLessEqual((end - start) * 60.0, DECODE_MAX_FRAMES, "60 fps: never cut off by the frame cap")
        self.assertLessEqual(start, 7.5)
        self.assertGreaterEqual(end, 13.5)

    def test_remembered_box_fills_in_when_no_logo_pixels_were_measured(self):
        method, box = self._region([], [], [self._run()])
        self.assertEqual((method, box), ("platform_memory", (373, 203, 915, 311)))
        method, box = self._region([], [self._hit()], [self._run()])
        self.assertEqual(method, "ocr_box_padded+platform_memory")
        self.assertEqual((box[0], box[2]), (373, 915), "the padded OCR box joined with the record box")


def _ocr_track(track_id, start, text, box, **extra):
    track = {
        "track_id": track_id, "start_seconds": start, "end_seconds": start + 3.0,
        "recommended_blur_end_seconds": start + 3.5, "observations": 1, "zone": "middle-center",
        "persistent": False, "review_priority": "low", "max_confidence": 0.72, "union_box": box,
        "sample_text": [text], "ad_probability": 0.003, "review_candidate": False,
        "routing": "LIKELY_SCENE_TEXT", "preview": f"text-previews/track-{track_id:05d}.jpg",
    }
    track.update(extra)
    return track


TAP17_TRACKS = [
    _ocr_track(1, 9.0, "iOlYI", [378, 156, 574, 230]),
    _ocr_track(136, 2700.0, "iOIYI", [400, 156, 596, 230], max_confidence=0.66, routing="LIKELY_CREDITS"),
]


class PlatformCardQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        for name in ("reports", "input"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "t17.mp4"
        self.source.write_bytes(b"source")
        self.no_ffmpeg = self.root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"

    def tearDown(self):
        self.temporary.cleanup()

    def _text_report(self, tracks):
        path = self.root / "reports" / "job" / "text" / "text-scan.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source), "input_sha256": "film",
            "duration_seconds": DURATION, "scan_start_seconds": 0.0, "scan_duration_seconds": DURATION,
            "sample_every_seconds": 3.0, "source_size": [1280, 534], "analysis_size": [960, 400],
            "tracks": tracks,
        }, ensure_ascii=False), encoding="utf-8")
        return path

    def _visual_report(self, intervals, rejected=()):
        directory = self.root / "reports" / "job" / "visual-logo"
        (directory / "thumbnails").mkdir(parents=True, exist_ok=True)
        for index, interval in enumerate([*intervals, *rejected]):
            name = f"thumbnails/logo-{index:04d}-{float(interval['start_seconds']) + 0.143:.3f}s.jpg"
            (directory / name).write_bytes(b"image")
            interval.setdefault("strongest_frame", name)
        path = directory / "scan-localized.json"
        path.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "scan_type": "visual_logo", "input": str(self.source),
            "input_sha256": "film", "duration_seconds": DURATION, "analysis_size": [320, 134],
            "scan_start_seconds": 0.0, "scan_duration_seconds": DURATION,
            "intervals": intervals, "rejected_windows": list(rejected),
        }), encoding="utf-8")
        return path

    def _build(self, *reports, **kwargs):
        kwargs.setdefault("ffmpeg_path", self.no_ffmpeg)
        return build_review_queue(project_root=self.root, report_paths=list(reports),
                                  queue_path=self.root / "reports" / "job" / "review-queue.json", **kwargs)

    @staticmethod
    def _platform(queue, key="items"):
        return [item for item in queue[key] if item.get("candidate_type") == "platform_logo"]

    def test_ocr_hit_becomes_main_blur_card_with_padded_region_without_source(self):
        queue = self._build(self._text_report(TAP17_TRACKS))
        opening, ending = self._platform(queue)
        self.assertEqual((opening["start_seconds"], opening["end_seconds"]), (7.5, 13.5))
        self.assertEqual((ending["start_seconds"], ending["end_seconds"]), (2698.5, DURATION))
        self.assertEqual(opening["suggested_region_source_pixels"],
                         {"x": 373, "y": 173, "width": 523, "height": 169},
                         "the OCR box padded by half its width and a third of its height")
        for card in (opening, ending):
            self.assertEqual(card["suggested_decision"], "BLUR")
            self.assertEqual(card["priority"], "high")
            self.assertEqual(
                (card["review_kind"], card["region_classification"]), ("platform_logo", "platform_logo"))
            self.assertEqual(card["model_evidence"],
                             {"vlm_source": "platform_rule", "region_sources": ["platform_probe"]})
            self.assertEqual(card["source_candidate_refs"], [])
            self.assertEqual(card["source_frame_size"], [1280, 534])
            self.assertEqual(card["platform_logo"]["snap"],
                             {"method": "ocr_bounds", "start_cut": False, "reason": "ffmpeg_missing"})
            self.assertEqual(card["platform_logo"]["region"]["method"], "ocr_box_padded")
            self.assertIsNone(card.get("advisory"))
        self.assertEqual(opening["labels"], ["Logo nền tảng iQIYI", "iOlYI"])
        self.assertEqual(opening["preview_images"], ["reports/job/text/text-previews/track-00001.jpg"])
        [detection] = opening["platform_logo"]["detections"]
        self.assertEqual((detection["kind"], detection["text"], detection["observed_seconds"]),
                         ("ocr_text", "iOlYI", 10.5))
        self.assertIn("OCR đọc “iOlYI” lúc 0:10 — tên nền tảng video iQIYI", opening["reasons"][0])
        self.assertIn("đề xuất Làm mờ vùng logo 0:07–0:13, không cắt cảnh", opening["reasons"][0])
        self.assertEqual(queue["platform_logos"]["cards"], 2)
        self.assertEqual(queue["platform_logos"]["probe_reason"], "ffmpeg_missing")
        self.assertFalse(queue["platform_logos"]["ending_card"], "no visual-logo scan, no ending card")
        self.assertEqual([item["category"] for item in queue["items"]], ["visual_logo", "visual_logo"])

    def test_platform_card_survives_quarantine_revalidation_and_rebuild(self):
        report = self._text_report(TAP17_TRACKS)
        first = self._build(report)
        second = self._build(report)
        self.assertEqual([card["id"] for card in self._platform(second)],
                         [card["id"] for card in self._platform(first)])
        self.assertEqual(len(second["items"]), 2, "the previous queue's cards are not restored twice")
        self.assertEqual(self._platform(second, "advisory_items"), [])
        self.assertTrue(all(card.get("migration_status") is None for card in second["items"]))

    def test_overlay_absorbed_hit_reuses_the_overlay_card(self):
        mark = _ocr_track(7, 0.0, "WeTV", [800, 20, 930, 70], end_seconds=DURATION, zone="top-right",
                          persistent=True, observations=880, max_confidence=0.5)
        queue = self._build(self._text_report([mark]))
        self.assertEqual(self._platform(queue), [])
        self.assertEqual(queue["platform_logos"]["hits"], 0)
        [overlay] = queue["items"]
        self.assertEqual((overlay["category"], overlay["candidate_type"]), ("text", "persistent_overlay"))
        self.assertEqual(overlay["suggested_decision"], "BLUR")
        self.assertIn("tên nền tảng video: Tencent Video (WeTV)", " ".join(overlay["reasons"]))

    def test_platform_wording_passes_structure_audit(self):
        queue = self._build(self._text_report(TAP17_TRACKS))
        for card in self._platform(queue):
            wording = " ".join(card["labels"] + card["reasons"]).casefold()
            self.assertFalse([term for term in PROMO_TERMS if term in wording])
            card.update(decision="BLUR", decision_region_source_pixels=card["suggested_region_source_pixels"])
        findings, actions = queue_quality_findings(queue)
        self.assertEqual((findings, actions), ([], []))

    def test_whole_scene_cards_covered_by_a_platform_card_suggest_keep(self):
        visual = self._visual_report(
            [{
                "start_seconds": 2700.0, "end_seconds": DURATION, "max_score": 0.9,
                "predicted_label": "Visual brand/logo candidate",
                "visual_logo_confirmation": {"state": "CONFIRMED", "answer": "YES",
                                             "confirmation_source": "qwen_local"},
                "region_localization": {"frame_size": [1280, 534], "proposals": [{
                    "blur_region_px": [518, 190, 154, 132], "sources": ["grounding_dino"],
                    "labels": ["brand logo sponsor logo"], "region_classification": "unknown",
                }]},
            }],
            rejected=[{"start_seconds": 2695.0, "end_seconds": 2700.0, "max_score": 0.3,
                       "visual_logo_confirmation": {"state": "REJECTED", "answer": "NO"}}],
        )
        queue = self._build(visual, self._text_report(TAP17_TRACKS))
        ending_ident = next(card for card in self._platform(queue) if card["start_seconds"] > 2000)
        [ending] = [item for item in queue["items"] if item.get("candidate_type") == "ending_boundary"]
        self.assertEqual((ending["start_seconds"], ending["end_seconds"]), (round(DURATION - 6.0, 3), DURATION))
        self.assertEqual(ending["suggested_decision"], "KEEP")
        self.assertEqual(ending["platform_logo_link"]["card_id"], ending_ident["id"])
        self.assertEqual(ending["platform_logo_link"]["platform"], "iQIYI")
        self.assertEqual([window["answer"] for window in ending["model_evidence"]["tail_windows"]], ["NO", "YES"])
        [box] = ending["evidence_regions"]
        self.assertEqual(box["covered_by"], ending_ident["id"])
        self.assertEqual(box["covered_by_label"], "Logo nền tảng iQIYI")
        [quarantined] = [item for item in queue["advisory_items"]
                         if item.get("candidate_type") == "uncorroborated_logo_region"]
        self.assertEqual(quarantined["covered_by"], ending_ident["id"])
        self.assertTrue(queue["platform_logos"]["ending_card"])


def _ffmpeg():
    found = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if all(found) and Path(found[0]).parent == Path(found[1]).parent:
        return Path(found[0])
    tools = Path(__file__).resolve().parents[1] / "tools" / "ffmpeg" / "bin"
    return tools / "ffmpeg.exe" if (tools / "ffprobe.exe").is_file() else None


@unittest.skipUnless(_ffmpeg(), "ffmpeg is not installed")
class PlatformProbeVideoTests(unittest.TestCase):
    """A generated 10 s clip: grey picture, cut to black at 3 s, logo 4.00–6.48 s, picture from 7 s."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "reports" / "job" / "text").mkdir(parents=True)
        (self.root / "input").mkdir()
        self.source = self.root / "input" / "ident.mp4"
        drawing = (
            "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='gte(t,3)*lt(t,7)',"
            "drawbox=x=260:y=100:w=120:h=40:color=0x5AE678:t=fill:enable='gte(t,4)*lt(t,6.5)'"
        )
        result = subprocess.run(
            [str(_ffmpeg()), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "color=c=0x808080:s=640x268:r=25:d=10", "-vf", drawing, "-pix_fmt", "yuv420p",
             "-c:v", "libx264", "-g", "50", str(self.source)],
            capture_output=True, timeout=120)
        if result.returncode != 0:
            raise unittest.SkipTest("ffmpeg could not encode the test clip")
        report = self.root / "reports" / "job" / "text" / "text-scan.json"
        report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source), "duration_seconds": 10.0,
            "scan_start_seconds": 0.0, "scan_duration_seconds": 10.0, "sample_every_seconds": 3.0,
            "source_size": [640, 268], "analysis_size": [960, 402],
            "tracks": [_ocr_track(1, 3.0, "iQIYI", [390, 150, 570, 210])],
        }), encoding="utf-8")
        self.queue = build_review_queue(
            project_root=self.root, report_paths=[report], ffmpeg_path=_ffmpeg(),
            queue_path=self.root / "reports" / "job" / "review-queue.json")

    def tearDown(self):
        self.temporary.cleanup()

    def test_span_and_region_come_from_the_decoded_frames(self):
        [card] = self.queue["items"]
        self.assertEqual((card["start_seconds"], card["end_seconds"]), (3.0, 6.52))
        self.assertEqual(card["platform_logo"]["snap"], {"method": "dark_run", "start_cut": True, "reason": None})
        self.assertEqual(card["platform_logo"]["region"]["method"], "logo_pixels")
        region = card["suggested_region_source_pixels"]
        self.assertLessEqual(region["x"], 260)
        self.assertLessEqual(region["y"], 100)
        self.assertGreaterEqual(region["x"] + region["width"], 380)
        self.assertGreaterEqual(region["y"] + region["height"], 140)
        self.assertLess(region["width"], 120 + 2 * 0.04 * 640, "3 % padding, not the OCR fallback")
        self.assertEqual(len(card["preview_images"]), 3)
        self.assertTrue(all(name.startswith("reports/job/platform-logo/iqiyi-") for name in card["preview_images"]))

    def test_previews_pass_the_window_self_check(self):
        [card] = self.queue["items"]
        window = studio_logo_window_frames(self.root, self.queue, card, _ffmpeg())
        self.assertEqual(window["frames_source"], "source_video", window["reason"])
        self.assertEqual(window["pipeline"]["self_checked_previews"], 3)


if __name__ == "__main__":
    unittest.main()
