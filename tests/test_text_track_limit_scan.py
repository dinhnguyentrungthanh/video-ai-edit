"""The text scan's track limit with scripted OCR (item 6, 2026-10-16): evidence is never left out.

Ten plain scene-text reads fill a limit of four tracks; after them come the site's address under
the subtitles ("EXAMPLE.COM") and a reading of it the address rule does not read
("xem tai examplecom nhe", only the reviewer's memory knows it). The address track must stay in the
report with its preview; the other tracks past the limit stay in ``omitted_tracks`` without one,
and the review build takes the remembered one back only with the user's memories on.
Needs only the project FFmpeg (no models): found next to this checkout or under
``BILIFLOW_TEST_INSTALL_ROOT``.
"""
import json
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
LIMIT = 4
PLAIN = 10  # one read each at 0, 3, ... 27 s, side by side in the middle of the frame
ADDRESS, ADDRESS_AT, ADDRESS_BOX = "EXAMPLE.COM", (105.0, 108.0), [400, 330, 560, 350]
REMEMBERED, REMEMBERED_AT, REMEMBERED_BOX = "xem tai examplecom nhe", 111.0, [20, 20, 220, 40]


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
        index = round(timestamp / SAMPLE_EVERY)
        if index < PLAIN:
            return [(_points([40 + index * 90, 180, 120 + index * 90, 200]), f"Cua hang so {index} ben duong", 0.9)]
        if timestamp in ADDRESS_AT:
            return [(_points(ADDRESS_BOX), ADDRESS, 0.9)]
        if timestamp == REMEMBERED_AT:
            return [(_points(REMEMBERED_BOX), REMEMBERED, 0.9)]
        return []


class SceneTextClassifier:
    """Every reading looks like scene text: no track is a review candidate by itself."""

    device = "cpu"
    method = "scripted"

    def classify_many(self, texts):
        return [{"advertisement": 0.05, "subtitle": 0.1, "credits": 0.1, "scene_text": 0.7, "other": 0.05}
                for _ in texts]


def _texts(tracks):
    return [text for track in tracks for text in track["sample_text"]]


@unittest.skipUnless(FFMPEG.exists() and FFPROBE.exists(), "project FFmpeg is required")
class TrackLimitScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        (cls.root / "reports").mkdir()
        (cls.root / "state").mkdir()
        cls.video = cls.root / "input" / "film.mkv"
        cls.video.parent.mkdir()
        subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"color=c=black:s={WIDTH}x{HEIGHT}:r=1:d={DURATION}", "-c:v", "ffv1", str(cls.video)],
                       check=True)
        cls.report_dir = cls.root / "reports" / "film" / "text"
        cls.scan = cls._scan(cls.report_dir)
        cls.report = json.loads((cls.report_dir / "text-scan.json").read_text(encoding="utf-8"))

    @classmethod
    def _scan(cls, report_dir):
        from biliflow.textscan import scan_text

        return scan_text(
            project_root=cls.root, input_path=cls.video, report_dir=report_dir, model_dir=cls.root / "models",
            ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE, sample_every=SAMPLE_EVERY, device_name="cpu",
            semantic_classifier=SceneTextClassifier(), policy_path=ROOT / "config" / "text_review_policy.json",
            reader=ScriptedReader(), max_report_tracks=LIMIT,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _build(self, *, memories):
        from biliflow.review_workflow import build_review_queue

        (self.root / "state" / "ad-text-memory.json").write_text(json.dumps({"schema_version": 1, "records": [
            {"key": "examplecom", "first_remembered_at": "2026-10-16T10:00:00+07:00",
             "last_remembered_at": "2026-10-16T10:00:00+07:00", "blur_decisions": 1},
        ]}), encoding="utf-8")
        return build_review_queue(
            project_root=self.root, report_paths=[self.report_dir / "text-scan.json"],
            queue_path=self.root / "reports" / "film" / f"review-queue-{memories}.json", ffmpeg_path=FFMPEG,
            use_studio_logo_memory=memories,
        )

    def test_the_address_past_the_limit_stays_in_the_report_with_its_preview(self):
        tracks = self.report["tracks"]
        self.assertEqual(len(tracks), LIMIT + 1)
        self.assertIn(ADDRESS, _texts(tracks))
        self.assertNotIn(REMEMBERED, _texts(tracks))
        for track in tracks:
            self.assertTrue((self.report_dir / track["preview"]).is_file(), track["track_id"])
        self.assertEqual(len(list((self.report_dir / "text-previews").iterdir())), LIMIT + 1)

    def test_the_other_tracks_past_the_limit_are_kept_without_previews(self):
        omitted = self.report["omitted_tracks"]
        self.assertEqual(self.report["tracks_before_limit"], PLAIN + 2)
        self.assertEqual(self.report["tracks_omitted"], PLAIN - LIMIT + 1)
        self.assertEqual(len(omitted), PLAIN - LIMIT + 1)
        self.assertEqual(self.report["omitted_tracks_unrecorded"], 0)
        self.assertIn(REMEMBERED, _texts(omitted))
        self.assertFalse(any("preview" in track for track in omitted))
        self.assertEqual(self.scan["omitted_tracks"], omitted)

    def test_omitted_tracks_past_their_own_bound_are_counted_not_listed(self):
        from unittest import mock

        import biliflow.textscan as textscan

        report_dir = self.root / "reports" / "bounded" / "text"
        with mock.patch.object(textscan, "MAX_OMITTED_TRACK_RECORDS", 2):
            self._scan(report_dir)
        report = json.loads((report_dir / "text-scan.json").read_text(encoding="utf-8"))
        self.assertEqual(report["tracks_omitted"], PLAIN - LIMIT + 1)
        self.assertEqual(report["omitted_tracks"], self.report["omitted_tracks"][:2])
        self.assertEqual(report["omitted_tracks_unrecorded"], PLAIN - LIMIT + 1 - 2)
        self.assertEqual(report["tracks"], self.report["tracks"], "the listed tracks do not depend on the bound")

    def test_the_review_build_takes_back_a_remembered_address_only_with_the_users_memories(self):
        def remembered_cards(queue):
            return [item for item in queue["items"] if item["category"] == "text"
                    and item["start_seconds"] <= REMEMBERED_AT <= item["end_seconds"]
                    and (item.get("web_address") or {}).get("remembered") == ["examplecom"]
                    and item["suggested_region_source_pixels"]["y"] < 100]

        [card] = remembered_cards(self._build(memories=True))
        self.assertEqual(card["suggested_decision"], "BLUR")
        self.assertEqual(remembered_cards(self._build(memories=False)), [])


if __name__ == "__main__":
    unittest.main()
