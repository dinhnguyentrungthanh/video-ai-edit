"""Platform-logo memory (batch 4a, 2026-10-03): records, conversion, seed and studio isolation."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import cv2
import numpy as np

from biliflow.brand_memory import (
    STUDIO_LOGO_MEMORY_PATH,
    compare_studio_logo,
    load_studio_logo_memory,
    match_studio_logo,
    perceptual_hash,
    prepare_studio_logo_frames,
    refresh_studio_logo_masks,
    remember_studio_logo,
    studio_logo_frame_signature,
    studio_logo_grid,
    upgrade_studio_logo_memory,
)
from biliflow.platform_logos import _SignedFrames, platform_memory_records, record_logo_frames
from biliflow import platform_memory
from biliflow.platform_memory import (
    MemoryChanged,
    backup_memory,
    convert_logo_memory_class,
    convert_plan,
    forget_remembered_logo,
    prepare_platform_logo_frames,
    remember_platform_logo,
    seed_platform_logo,
)
from biliflow.review_workflow import build_review_queue

ROOT = Path(__file__).resolve().parents[1]
# Real-data tests only read the project's memory and reports; a worktree points them at the
# main tree with BILIFLOW_TEST_DATA_ROOT.
DATA_ROOT = Path(os.environ.get("BILIFLOW_TEST_DATA_ROOT") or ROOT)
SIZE = (134, 320)


def _logo(box=(129, 49, 187, 83), shade=0):
    """A small striped logo on black; ``shade`` changes its colour (identical JPEGs are stored once)."""
    frame = np.zeros(SIZE + (3,), dtype=np.uint8)
    x0, y0, x1, y1 = box
    frame[y0:y1:2, x0:x1] = (90, 230 - 8 * shade, 120)
    frame[y0:y1, x0:x0 + 3] = (240, 240, 240)
    return frame


def _wide_logo():
    """Sparse bright rows over most of the frame: a 'logo' box far over a quarter of it."""
    frame = np.zeros(SIZE + (3,), dtype=np.uint8)
    frame[10:120:6, 20:300] = (60, 200, 90)
    return frame


def _licence():
    frame = np.full(SIZE + (3,), 60, dtype=np.uint8)
    cv2.putText(frame, "LICENCE 2026", (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    return frame


def _jpeg(frame):
    ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    assert ok
    return encoded.tobytes()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _MemoryRoot(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "reports").mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def studio_record(self, item_id, frames, *, sha="film", regions=()):
        """A schema-2 studio-logo KEEP record whose window frames are ``frames`` [(t, rgb)]."""
        queue = {"source": {"sha256": sha, "path": str(self.root / "missing.mp4")}, "items": []}
        item = {"id": item_id, "category": "visual_logo", "candidate_type": "opening_boundary",
                "decision": "KEEP", "start_seconds": frames[0][0], "end_seconds": frames[-1][0] + 0.04,
                "preview_images": [], "suggested_region_source_pixels": None, "labels": ["logo"]}
        window = {"frames": [(moment, _jpeg(frame)) for moment, frame in frames],
                  "frames_source": "source_video", "reason": None,
                  "pipeline": {"analysis_size": [SIZE[1], SIZE[0]], "fps": 25.0}}
        prepared = prepare_studio_logo_frames(self.root, queue, item, window, ignored_regions=list(regions))
        signature = {"preview": "x", "phash": perceptual_hash(frames[0][1]), "grid": studio_logo_grid(frames[0][1])}
        return remember_studio_logo(self.root, queue, item, [signature],
                                    window_text={"covered": True, "texts": []}, frames=prepared)

    def ident_frames(self, start=10.0):
        """Two black frames, six logo frames, then the next picture."""
        frames = [(round(start + index * 0.04, 3), np.zeros(SIZE + (3,), np.uint8)) for index in range(2)]
        frames += [(round(start + (2 + index) * 0.04, 3), _logo(shade=index)) for index in range(6)]
        return frames + [(round(start + 0.32, 3), _licence())]

    @property
    def memory(self):
        return self.root / STUDIO_LOGO_MEMORY_PATH


class ConversionTests(_MemoryRoot):
    def test_dry_run_by_default(self):
        record = self.studio_record("review-ident", self.ident_frames())
        before = _sha(self.memory)
        report = convert_logo_memory_class(self.root, [record["key"]], to="platform_logo", platform="iqiyi")
        self.assertTrue(report["dry_run"])
        [entry] = report["records"]
        self.assertEqual((entry["status"], entry["logo_frames"]), ("convertible", 6))
        x, y, width, height = entry["blur_region"]["box"]
        self.assertAlmostEqual(x, (129 - 9.6) / 320, places=4)
        self.assertAlmostEqual(width, (187 - 129 + 19.2) / 320, places=4)
        self.assertEqual(report["memory_sha256"], before)
        self.assertEqual(_sha(self.memory), before, "a dry run writes nothing")
        self.assertFalse((self.root / "state" / "backups").exists())

    def test_apply_backs_up_then_flips_class(self):
        record = self.studio_record("review-ident", self.ident_frames())
        snapshot = self.memory.read_bytes()
        report = convert_logo_memory_class(self.root, [record["key"]], to="platform_logo", platform="iqiyi",
                                           apply=True)
        self.assertEqual((report["converted"], report["records"][0]["status"]), (1, "converted"))
        self.assertEqual((self.root / report["backup"]).read_bytes(), snapshot)
        [converted] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual((converted["memory_class"], converted["decision"]), ("platform_logo", "BLUR"))
        self.assertEqual(converted["platform"], {"key": "iqiyi", "name": "iQIYI"})
        self.assertEqual(len(converted["logo_frame_times"]), 6)
        self.assertEqual(converted["converted_from"]["memory_class"], "studio_logo")
        self.assertEqual(converted["converted_from"]["by"], "platform-logo-convert")
        for name in ("signatures", "frames", "stored_frames", "frames_folder", "window", "ignored_regions"):
            self.assertEqual(converted[name], record[name], name)
        self.assertEqual(len(record_logo_frames(self.root, converted)), 6)
        self.assertEqual(platform_memory_records(load_studio_logo_memory(self.root)), [converted])
        back = convert_logo_memory_class(self.root, [record["key"]], to="studio_logo", apply=True)
        self.assertEqual(back["converted"], 1)
        self.assertNotEqual(back["backup"], report["backup"], "a second backup never overwrites the first")
        [restored] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual((restored["memory_class"], restored["decision"]), ("studio_logo", "KEEP"))
        self.assertFalse({"platform", "blur_region", "logo_frame_times"} & set(restored))

    def test_record_without_logo_frames_is_refused(self):
        licence = self.studio_record("review-licence", [(0.0, _licence()), (0.04, _licence())])
        big = self.studio_record("review-big", [(5.0, _wide_logo())])
        report = convert_logo_memory_class(
            self.root, [licence["key"], big["key"], "film:missing"], to="platform_logo", platform="iqiyi",
            apply=True)
        self.assertEqual([(entry["status"], entry["reason"]) for entry in report["records"]],
                         [("refused", "no_logo_frames"), ("refused", "box_too_large"),
                          ("missing", "no_record_with_this_key")])
        self.assertIsNone(report["backup"])
        self.assertFalse((self.root / "state" / "backups").exists())
        v1 = {"key": "film:v1", "memory_class": "studio_logo", "decision": "KEEP", "signatures": []}
        self.assertEqual(convert_plan(self.root, v1, "platform_logo")["reason"], "no_stored_frames")
        with self.assertRaisesRegex(ValueError, "platform must be one of"):
            convert_logo_memory_class(self.root, [licence["key"]], to="platform_logo", platform="netflix")

    def test_changed_memory_aborts(self):
        record = self.studio_record("review-ident", self.ident_frames())
        original = convert_plan

        def plan_then_remember(*args, **kwargs):
            plan = original(*args, **kwargs)
            self.studio_record("review-other", self.ident_frames(20.0))  # a remember lands meanwhile
            return plan

        with mock.patch("biliflow.platform_memory.convert_plan", plan_then_remember):
            report = convert_logo_memory_class(self.root, [record["key"]], to="platform_logo",
                                               platform="iqiyi", apply=True)
        self.assertEqual(report["aborted"], "memory_changed")
        self.assertEqual((report["backup"], report["converted"]), (None, 0))
        records = load_studio_logo_memory(self.root)["records"]
        self.assertEqual([value["memory_class"] for value in records], ["studio_logo", "studio_logo"])

    def test_convert_script_is_a_dry_run_by_default(self):
        record = self.studio_record("review-ident", self.ident_frames())
        spec = importlib.util.spec_from_file_location("platform_logo_convert", ROOT / "scripts" / "platform_logo_convert.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        before = _sha(self.memory)
        with mock.patch("builtins.print") as printed:
            code = script.main(["--project-root", str(self.root), "convert", "--key", record["key"],
                                "--platform", "iqiyi"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(printed.call_args[0][0])["dry_run"])
        self.assertEqual(_sha(self.memory), before)
        with mock.patch("builtins.print"):
            self.assertEqual(script.main(["--project-root", str(self.root), "convert", "--key", record["key"],
                                          "--platform", "iqiyi", "--apply"]), 0)
        self.assertEqual(load_studio_logo_memory(self.root)["records"][0]["memory_class"], "platform_logo")

    def test_convert_script_exit_codes(self):
        script = _convert_script()
        empty = self.root / "empty"
        empty.mkdir()
        with mock.patch("builtins.print") as printed:
            code = script.main(["--project-root", str(empty), "convert", "--key", "film:x", "--platform", "iqiyi"])
        self.assertEqual((code, json.loads(printed.call_args[0][0])["error"]), (1, "memory_missing"))
        licence = self.studio_record("review-licence", [(0.0, _licence()), (0.04, _licence())])
        with mock.patch("builtins.print"):
            self.assertEqual(script.main(["--project-root", str(self.root), "convert", "--key", licence["key"],
                                          "--key", "film:none", "--platform", "iqiyi"]), 1, "nothing to convert")
        with mock.patch("builtins.print") as printed:
            code = script.main(["--project-root", str(self.root), "seed", "--video", str(self.root / "none.mp4"),
                                "--start", "1", "--end", "2", "--platform", "iqiyi"])
        self.assertEqual(code, 2)
        self.assertIn("none.mp4", json.loads(printed.call_args[0][0])["error"])

    def test_repeated_keys_convert_once(self):
        record = self.studio_record("review-ident", self.ident_frames())
        report = convert_logo_memory_class(self.root, [record["key"], record["key"]], to="platform_logo",
                                           platform="iqiyi", apply=True)
        self.assertEqual((report["converted"], len(report["records"])), (1, 1))
        [converted] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual(converted["converted_from"]["memory_class"], "studio_logo")

    def test_memory_changed_after_the_backup_aborts(self):
        record = self.studio_record("review-ident", self.ident_frames())

        def backup_then_remember(root, snapshot):
            path = backup_memory(root, snapshot)
            self.studio_record("review-other", self.ident_frames(20.0))  # a remember lands meanwhile
            return path

        with mock.patch("biliflow.platform_memory.backup_memory", backup_then_remember):
            report = convert_logo_memory_class(self.root, [record["key"]], to="platform_logo", platform="iqiyi",
                                               apply=True)
        self.assertEqual((report["aborted"], report["converted"]), ("memory_changed", 0))
        records = load_studio_logo_memory(self.root)["records"]
        self.assertEqual([value["memory_class"] for value in records], ["studio_logo", "studio_logo"])

    def test_prepare_tolerates_two_frames_at_one_time(self):
        stored = [(10.0, "a.jpg", _jpeg(_logo(shade=0))), (10.0, "b.jpg", _jpeg(_logo(shade=1))),
                  (10.04, "c.jpg", _jpeg(_logo(shade=2)))]
        studio = {"stored": stored, "ignored_regions": [], "frames_source": "source_video", "window": [10.0, 10.08]}
        with mock.patch("biliflow.platform_memory.prepare_studio_logo_frames", return_value=studio):
            prepared = prepare_platform_logo_frames(self.root, {}, {}, None, None)
        self.assertIsNone(prepared["platform"]["reason"])
        self.assertEqual(prepared["platform"]["logo_frame_times"][0], 10.0)


IQIYI = {"key": "iqiyi", "name": "iQIYI"}


class SafeRememberForgetTests(_MemoryRoot):
    """Security review 2026-10-03: a review decision never deletes frames or rewrites the memory unbacked."""

    def setUp(self):
        super().setUp()
        self.queue = {"source": {"sha256": "film", "path": str(self.root / "missing.mp4")}, "items": []}
        self.item = {"id": "review-plat", "category": "visual_logo", "candidate_type": "platform_logo",
                     "decision": "BLUR", "start_seconds": 10.0, "end_seconds": 10.36, "preview_images": [],
                     "labels": ["Logo nền tảng iQIYI"],
                     "suggested_region_source_pixels": {"x": 1, "y": 1, "width": 5, "height": 5}}
        window = {"frames": [(moment, _jpeg(frame)) for moment, frame in self.ident_frames()],
                  "frames_source": "source_video", "reason": None,
                  "pipeline": {"analysis_size": [SIZE[1], SIZE[0]], "fps": 25.0}}
        self.prepared = prepare_platform_logo_frames(self.root, self.queue, self.item, window, ignored_regions=[])

    def remember(self):
        return remember_platform_logo(self.root, self.queue, self.item, self.prepared, platform=IQIYI)

    @property
    def backups(self):
        return self.root / "state" / "backups"

    def frames_folders(self):
        return sorted(path.name for path in (self.root / "state" / "studio-logo-frames").iterdir())

    def moved(self, folder):
        return [path for path in self.backups.glob(f"studio-logo-frames-*/{Path(folder).name}") if path.is_dir()]

    def memory_backups(self):
        return [path.read_bytes() for path in sorted(self.backups.glob("studio-logo-memory-*.json"))]

    def test_re_remember_keeps_the_old_frames_in_backups(self):
        first = self.remember()
        old = self.root / first["frames_folder"]
        files = sorted(path.name for path in old.iterdir())
        snapshot = self.memory.read_bytes()
        second = self.remember()
        self.assertNotEqual(second["frames_folder"], first["frames_folder"], "new frames go to a fresh folder")
        [record] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual(record["frames_folder"], second["frames_folder"])
        for entry in record["stored_frames"] + record["frames"]:
            self.assertTrue((self.root / entry["image"]).is_file(), entry["image"])
        self.assertEqual(len(record_logo_frames(self.root, record)), len(record["logo_frame_times"]))
        self.assertFalse(old.exists())
        [moved] = self.moved(first["frames_folder"])
        self.assertEqual(sorted(path.name for path in moved.iterdir()), files, "moved, never deleted")
        self.assertIn(snapshot, self.memory_backups())

    def test_forget_backs_up_the_memory_and_moves_the_frames_of_either_class(self):
        record = self.remember()
        studio = self.studio_record("review-studio", self.ident_frames(20.0))
        folder = self.root / record["frames_folder"]
        files = sorted(path.name for path in folder.iterdir())
        snapshot = self.memory.read_bytes()
        self.assertFalse(forget_remembered_logo(self.root, self.queue, "review-none"))
        self.assertEqual(self.memory.read_bytes(), snapshot)
        self.assertEqual(self.memory_backups(), [], "nothing to forget: nothing written, no backup")
        self.assertTrue(forget_remembered_logo(self.root, self.queue, self.item["id"]))
        self.assertEqual([value["key"] for value in load_studio_logo_memory(self.root)["records"]], [studio["key"]])
        self.assertFalse(folder.exists())
        [moved] = self.moved(record["frames_folder"])
        self.assertEqual(sorted(path.name for path in moved.iterdir()), files)
        self.assertEqual(self.memory_backups(), [snapshot])
        self.assertTrue((self.root / studio["frames_folder"]).is_dir())
        self.assertTrue(forget_remembered_logo(self.root, self.queue, "review-studio"), "a studio record too")
        self.assertEqual(load_studio_logo_memory(self.root)["records"], [])
        self.assertEqual(len(self.moved(studio["frames_folder"])), 1)
        self.assertFalse(forget_remembered_logo(self.root, {"source": {"sha256": "other"}}, "review-plat"))

    def test_a_concurrent_change_aborts_without_writing(self):
        record = self.remember()
        folder = self.root / record["frames_folder"]
        files = sorted(path.name for path in folder.iterdir())

        def backup_then_remember(root, snapshot):
            path = backup_memory(root, snapshot)
            self.studio_record(f"review-other-{len(self.memory_backups())}", self.ident_frames(30.0))  # meanwhile
            return path

        for call in (self.remember, lambda: forget_remembered_logo(self.root, self.queue, self.item["id"])):
            before = self.frames_folders()
            with self.subTest(call=call), mock.patch("biliflow.platform_memory.backup_memory", backup_then_remember):
                with self.assertRaises(MemoryChanged):
                    call()
            records = load_studio_logo_memory(self.root)["records"]
            [kept] = [value for value in records if value["key"] == "film:review-plat"]
            self.assertEqual(kept["frames_folder"], record["frames_folder"], "our record is unchanged")
            self.assertEqual(sorted(path.name for path in folder.iterdir()), files, "its frames are untouched")
            self.assertEqual(len(self.frames_folders()), len(before) + 1, "only the other writer's folder is new")
            self.assertEqual(self.moved(record["frames_folder"]), [])
        # A change after the new frames were written: they are removed again, nothing else changes.
        original = platform_memory._write_fresh_frames

        def write_then_change(root, prepared):
            written = original(root, prepared)
            self.studio_record("review-late", self.ident_frames(40.0))
            return written

        before = self.frames_folders()
        with mock.patch("biliflow.platform_memory._write_fresh_frames", write_then_change):
            with self.assertRaises(MemoryChanged):
                self.remember()
        self.assertEqual(self.frames_folders(), sorted(before + ["film-review-late"]))
        [kept] = [value for value in load_studio_logo_memory(self.root)["records"] if value["key"] == "film:review-plat"]
        self.assertEqual(kept["frames_folder"], record["frames_folder"])

    def test_a_failed_memory_write_leaves_the_old_record_and_frames(self):
        record = self.remember()
        snapshot = self.memory.read_bytes()
        before = self.frames_folders()
        with mock.patch("biliflow.platform_memory._write_studio_logo_memory", side_effect=OSError(28, "disk full")):
            with self.assertRaises(OSError):
                self.remember()
            with self.assertRaises(OSError):
                forget_remembered_logo(self.root, self.queue, self.item["id"])
        self.assertEqual(self.memory.read_bytes(), snapshot)
        self.assertEqual(self.frames_folders(), before, "the new frames are removed, the old ones stay")
        self.assertTrue(all((self.root / entry["image"]).is_file() for entry in record["stored_frames"]))


def _convert_script():
    spec = importlib.util.spec_from_file_location("platform_logo_convert", ROOT / "scripts" / "platform_logo_convert.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


class StudioIsolationTests(_MemoryRoot):
    def test_studio_matching_ignores_platform_records(self):
        record = self.studio_record("review-ident", self.ident_frames())
        preview = self.root / "reports" / "logo-10.100s.jpg"
        preview.write_bytes(_jpeg(_logo()))
        card = {"id": "review-new", "category": "visual_logo", "candidate_type": "opening_boundary",
                "decision": None, "suggested_region_source_pixels": None,
                "preview_images": ["reports/logo-10.100s.jpg"]}
        self.assertIsNotNone(match_studio_logo(self.root, card, [record]))
        convert_logo_memory_class(self.root, [record["key"]], to="platform_logo", platform="iqiyi", apply=True)
        records = load_studio_logo_memory(self.root)["records"]
        self.assertIsNone(match_studio_logo(self.root, card, records))
        self.assertIsNone(compare_studio_logo(self.root, card, records))
        report = upgrade_studio_logo_memory(self.root, ffmpeg_path=self.root / "ffmpeg.exe")
        self.assertEqual(report["records"][0]["reason"], "not_a_studio_logo_record")

    def test_mask_refresh_keeps_platform_fields(self):
        record = self.studio_record("review-ident", self.ident_frames())
        convert_logo_memory_class(self.root, [record["key"]], to="platform_logo", platform="iqiyi", apply=True)
        [before] = load_studio_logo_memory(self.root)["records"]
        watermark = {"id": "wm-1", "category": "text", "candidate_type": "persistent_overlay", "decision": "BLUR",
                     "start_seconds": 0.0, "end_seconds": 100.0, "source_frame_size": [1280, 534],
                     "suggested_region_source_pixels": {"x": 20, "y": 20, "width": 200, "height": 50}}
        queue = {"source": {"sha256": "film"}, "items": [watermark]}
        self.assertEqual(refresh_studio_logo_masks(self.root, queue), 1)
        [after] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual([region["item_id"] for region in after["ignored_regions"]], ["wm-1"])
        for name in ("memory_class", "decision", "platform", "blur_region", "logo_frame_times", "converted_from"):
            self.assertEqual(after[name], before[name], name)


def _ffmpeg():
    found = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if all(found) and Path(found[0]).parent == Path(found[1]).parent:
        return Path(found[0])
    tools = ROOT / "tools" / "ffmpeg" / "bin"
    return tools / "ffmpeg.exe" if (tools / "ffprobe.exe").is_file() else None


def _clip(ffmpeg, path, black=(3, 7), logo=(4, 6.5)):
    drawing = (
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='gte(t,{black[0]})*lt(t,{black[1]})',"
        f"drawbox=x=260:y=100:w=120:h=40:color=0x5AE678:t=fill:enable='gte(t,{logo[0]})*lt(t,{logo[1]})'"
    )
    result = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "color=c=0x808080:s=640x268:r=25:d=10", "-vf", drawing, "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-g", "50", str(path)], capture_output=True, timeout=120)
    if result.returncode != 0:
        raise unittest.SkipTest("ffmpeg could not encode the test clip")
    return path


@unittest.skipUnless(_ffmpeg(), "ffmpeg is not installed")
class SeedTests(_MemoryRoot):
    def test_seed_builds_a_record_from_a_video_window(self):
        clip = _clip(_ffmpeg(), self.root / "export.mp4")
        report = seed_platform_logo(self.root, video=clip, start=3.0, end=7.0, platform="iqiyi",
                                    ffmpeg_path=_ffmpeg())
        self.assertEqual(report["status"], "seedable")
        # A static logo: identical JPEGs are stored once, like a studio record's frames.
        self.assertLess(report["stored_frames"], report["decoded_frames"])
        self.assertGreaterEqual(report["logo_frames"], 1)
        self.assertFalse(self.memory.exists(), "a dry run writes nothing")
        report = seed_platform_logo(self.root, video=clip, start=3.0, end=7.0, platform="iqiyi",
                                    ffmpeg_path=_ffmpeg(), apply=True)
        self.assertEqual(report["status"], "seeded")
        [record] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual((record["memory_class"], record["decision"]), ("platform_logo", "BLUR"))
        self.assertEqual(record["seeded_from"]["start"], 3.0)
        self.assertEqual(record["seeded_from"]["sha256"], hashlib.sha256(clip.read_bytes()).hexdigest())
        self.assertEqual(len(record_logo_frames(self.root, record)), len(record["logo_frame_times"]))
        again = seed_platform_logo(self.root, video=clip, start=3.0, end=7.0, platform="iqiyi",
                                   ffmpeg_path=_ffmpeg(), apply=True)
        self.assertEqual((again["status"], again["reason"]), ("refused", "already_seeded"))

    def test_seeded_record_finds_the_ident_without_ocr(self):
        seed_platform_logo(self.root, video=_clip(_ffmpeg(), self.root / "export.mp4"), start=3.0, end=7.0,
                           platform="iqiyi", ffmpeg_path=_ffmpeg(), apply=True)
        source = _clip(_ffmpeg(), self.root / "next-episode.mp4", black=(1, 5), logo=(2, 4.5))
        report = self.root / "reports" / "job" / "text" / "text-scan.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(source), "duration_seconds": 10.0,
            "scan_start_seconds": 0.0, "scan_duration_seconds": 10.0, "sample_every_seconds": 3.0,
            "source_size": [640, 268], "analysis_size": [960, 402], "tracks": [],
        }), encoding="utf-8")
        queue_path = self.root / "reports" / "job" / "review-queue.json"
        without = build_review_queue(project_root=self.root, report_paths=[report], queue_path=queue_path,
                                     ffmpeg_path=_ffmpeg())
        self.assertEqual(without["items"], [], "benchmarks never read the memory")
        queue = build_review_queue(project_root=self.root, report_paths=[report], queue_path=queue_path,
                                   ffmpeg_path=_ffmpeg(), use_studio_logo_memory=True)
        [card] = queue["items"]
        self.assertEqual(card["candidate_type"], "platform_logo")
        self.assertEqual((card["start_seconds"], card["end_seconds"]), (1.0, 4.52))
        self.assertEqual([value["kind"] for value in card["platform_logo"]["detections"]], ["platform_memory"])
        self.assertEqual(card["platform_logo"]["region"]["method"], "logo_pixels")
        self.assertIn("bạn đã nhớ", card["reasons"][0])
        self.assertEqual(queue["platform_logos"]["memory"]["runs"], 1)
        self.assertEqual(queue["studio_logo_memory"]["confirmed_logos"], 0, "platform records are not studio logos")


def _real_memory():
    path = DATA_ROOT / STUDIO_LOGO_MEMORY_PATH
    if not path.is_file():
        raise unittest.SkipTest("the project's studio-logo memory is not available")
    return path, json.loads(path.read_text(encoding="utf-8"))


def _real_record(records, item_id):
    record = next((value for value in records if str(value.get("key", "")).endswith(f":{item_id}")), None)
    if record is None:
        raise unittest.SkipTest(f"record {item_id} is not in the project's memory")
    return record


class RealMemoryTests(unittest.TestCase):
    """Read-only: the 2026-10-03 memory (3 iQIYI idents, 3 licence cards); nothing is written."""

    IQIYI = ("review-dd6d551760b5", "review-571a51c78be4", "review-bfbb9423ac85")
    LICENCE = ("review-9445dc481911", "review-f2cfc08075db", "review-fa81506bc042")

    def test_three_iqiyi_records_give_the_measured_box(self):
        path, memory = _real_memory()
        before = _sha(path)
        for item_id in self.IQIYI:
            with self.subTest(item_id=item_id):
                record = _real_record(memory["records"], item_id)
                if record.get("memory_class") != "studio_logo":
                    self.skipTest("already converted")
                plan = convert_plan(DATA_ROOT, record, "platform_logo")
                self.assertEqual(plan["status"], "convertible", plan["reason"])
                self.assertGreaterEqual(len(plan["derived"]["logo_frame_times"]), 55)
                x, y, width, height = plan["derived"]["blur_region"]["box"]
                box = (x * 1280, y * 534, width * 1280, height * 534)
                for value, expected in zip(box, (478, 179, 313, 168)):
                    self.assertAlmostEqual(value, expected, delta=12)
        self.assertEqual(_sha(path), before)

    def test_licence_records_are_not_platform_logos(self):
        path, memory = _real_memory()
        before = _sha(path)
        for item_id in self.LICENCE:
            with self.subTest(item_id=item_id):
                plan = convert_plan(DATA_ROOT, _real_record(memory["records"], item_id), "platform_logo")
                self.assertEqual((plan["status"], plan["reason"]), ("refused", "no_logo_frames"))
        self.assertEqual(_sha(path), before)

    def test_new_episode_opening_audit_frames_match_end_frames_do_not(self):
        path, memory = _real_memory()
        audit = DATA_ROOT / "reports" / "jobs" / "nhất-âu-xuân-tập-17-bae453af" / "visual-logo" / "audit-thumbnails"
        opening, ending = audit / "window-0003-10.500s.jpg", audit / "window-0193-2700.143s.jpg"
        if not opening.is_file() or not ending.is_file():
            self.skipTest("Tập 17 audit frames are not available")
        record = _real_record(memory["records"], self.IQIYI[0])
        plan = convert_plan(DATA_ROOT, record, "platform_logo")
        if plan["status"] != "convertible":
            self.skipTest(plan["reason"])
        converted = dict(record, logo_frame_times=plan["derived"]["logo_frame_times"])
        regions = record.get("ignored_regions") or []
        signed = _SignedFrames([studio_logo_frame_signature(image, regions)
                                for _, image in record_logo_frames(DATA_ROOT, converted)])

        def best(path):
            image = cv2.cvtColor(cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            return signed.best(studio_logo_frame_signature(image, regions))

        self.assertIsNotNone(best(opening), "the 10.5 s opening audit frame repeats the remembered ident")
        self.assertIsNone(best(ending), "the ending layout differs: it needs its own (seeded) record")


if __name__ == "__main__":
    unittest.main()
