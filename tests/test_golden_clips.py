"""Golden Set v2 clips (docs/handoff/G-golden-clips.md): plan, frame gate, time shift and migration."""
import os
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from biliflow import golden_clips as gc
from biliflow.golden_scoring import score_sets
from biliflow.golden_set import canonical_sha256, load_golden_sets, source_file, validate_manifest, write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = Path(os.environ.get("BILIFLOW_FFMPEG") or ROOT / "tools/ffmpeg/bin/ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe.exe" if FFMPEG.suffix == ".exe" else "ffprobe")
CAREFUL = {"careful": {"animation_sample_fps": 2.0, "adult_sample_fps": 2.0, "gore_sample_fps": 2.0,
                       "violence_sample_fps": 8.0, "text_sample_every": 3.0, "logo_sample_every": 2.0,
                       "logo_boundary_sample_every": 0.25}}
PERIODS = gc.sampling_periods(CAREFUL)
L_INDEX = gc.VideoIndex(Fraction(1, 25000), [second * 25000 for second in range(0, 3600)])  # keyframe every second
H_INDEX = gc.VideoIndex(Fraction(1, 90000), [round(n * 3753.75) for n in range(0, 172800, 250)])  # 23.976 fps


def window(source, start, end, segment_id=None, scored=True, job_id=39):
    return {"source": source, "job_id": job_id, "start": start, "end": end, "segment_id": segment_id, "scored": scored}


class SamplingGridTest(unittest.TestCase):
    def test_periods_of_the_careful_profile(self):
        self.assertEqual(PERIODS, (Fraction(1, 8), Fraction(1, 4), Fraction(1, 2), Fraction(2), Fraction(3)))
        self.assertTrue(gc.on_grid(Fraction(816), PERIODS))
        self.assertFalse(gc.on_grid(Fraction(817), PERIODS))  # not a multiple of 2 s
        self.assertEqual(gc.grid_offsets(Fraction(817), PERIODS)["2"], 1.0)


class PlanTest(unittest.TestCase):
    def test_live_action_origins_sit_on_every_grid_before_the_pre_roll(self):
        clips = gc.plan_clips([window("l", 855.0, 1110.0, "T5")], {"l": L_INDEX}, {"l": 3600.0}, PERIODS)
        self.assertEqual(len(clips), 1)
        clip = clips[0]
        self.assertEqual((clip["origin_seconds"], clip["end_seconds"], clip["key"]), (816, 1146, "j39-00816"))
        self.assertTrue(clip["on_grid"])

    def test_animation_origin_is_the_latest_keyframe_before_the_pre_roll(self):
        clip = gc.plan_clips([window("h", 1300.0, 1600.0, "C20C", job_id=38)], {"h": H_INDEX}, {"h": 7000.0},
                             PERIODS)[0]
        self.assertEqual(clip["origin_pts"], max(k for k in H_INDEX.keyframes if k / 90000 <= 1265))
        self.assertGreaterEqual(clip["end_seconds"], 1636)
        self.assertFalse(clip["on_grid"])

    def test_segment_at_the_start_and_one_at_the_end_keep_the_film_edges(self):
        clips = gc.plan_clips([window("l", 0.0, 300.0, "T1"), window("l", 3400.0, 3599.5, "T4")],
                              {"l": L_INDEX}, {"l": 3599.5}, PERIODS)
        self.assertEqual([(c["origin_pts"], c["end_pts"]) for c in clips], [(0, 336 * 25000), (3360 * 25000, None)])

    def test_overlapping_clips_share_one_and_an_off_grid_neighbour_joins_an_on_grid_clip(self):
        clips = gc.plan_clips([window("l", 0.0, 300.0, "T1"), window("l", 300.0, 600.0, "T2"),
                               window("h", 0.0, 240.0, "A", job_id=38), window("h", 480.0, 800.0, "B", job_id=38)],
                              {"l": L_INDEX, "h": H_INDEX}, {"l": 3600.0, "h": 7000.0}, PERIODS)
        self.assertEqual([[w["segment_id"] for w in c["windows"]] for c in clips], [["A", "B"], ["T1", "T2"]])
        self.assertTrue(all(c["origin_pts"] == 0 and c["on_grid"] for c in clips))

    def test_on_grid_clips_far_apart_stay_separate(self):
        clips = gc.plan_clips([window("l", 0.0, 300.0, "T1"), window("l", 855.0, 1110.0, "T5")],
                              {"l": L_INDEX}, {"l": 3600.0}, PERIODS)
        self.assertEqual(len(clips), 2)

    def test_an_unscored_window_has_no_margins_and_never_joins_a_segment_clip(self):
        clips = gc.plan_clips([window("l", 5400.0, 5550.0, scored=False), window("l", 5300.0, 5600.0, "S")],
                              {"l": gc.VideoIndex(Fraction(1, 25000), [s * 25000 for s in range(7000)])},
                              {"l": 7000.0}, PERIODS)
        bench = next(c for c in clips if not c["scored"])
        self.assertEqual((bench["origin_seconds"], bench["end_seconds"], len(clips)), (5400, 5550, 2))


class FrameGateTest(unittest.TestCase):
    FILM = [(pts, f"m{pts}") for pts in range(0, 200, 10)]  # frames every 10 ticks

    def test_identical_frames_pass_and_extra_frames_after_the_end_are_allowed(self):
        clip = [(pts - 50, digest) for pts, digest in self.FILM if 50 <= pts <= 160]
        result = gc.compare_frames(clip, self.FILM, origin_pts=50, end_pts=150)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["expected_frames"], result["extra_after_end"]), (10, 2))

    def test_a_changed_missing_or_foreign_frame_fails(self):
        good = [(pts - 50, digest) for pts, digest in self.FILM if 50 <= pts < 150]
        changed = [(p, "x" if p == 30 else d) for p, d in good]
        missing = [row for row in good if row[0] != 40]
        foreign = good + [(5, "y")]
        late_start = good[1:]
        for name, clip in (("changed", changed), ("missing", missing), ("foreign", foreign), ("late", late_start)):
            with self.subTest(name):
                self.assertFalse(gc.compare_frames(clip, self.FILM, 50, 150)["ok"])


@unittest.skipUnless(FFMPEG.exists() and FFPROBE.exists(), "project FFmpeg is required")
class CutAndVerifyTest(unittest.TestCase):
    def test_a_stream_copied_clip_is_bit_identical_and_starts_at_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            film = Path(folder) / "film.mp4"
            subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=160x90:rate=25:duration=12", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=12", "-c:v", "libx264", "-g", "25", "-bf", "2",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(film)], check=True)
            index = gc.probe_keyframes(FFPROBE, film)
            origin, end = index.at_or_before(Fraction(4))[-1], index.at_or_before(Fraction(8))[-1]
            clip = Path(folder) / "media" / "clip.mp4"
            gc.cut_clip(FFMPEG, film, index, origin, end, clip)
            result = gc.verify_clip(FFMPEG, film, clip, index, origin, end)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["expected_frames"], 100)
            probe = gc.probe_clip(FFPROBE, clip)
            self.assertEqual((float(probe["format_start_time"]), float(probe["video_start_time"])), (0.0, 0.0))
            with self.assertRaises(FileExistsError):  # a clip is never overwritten
                gc.cut_clip(FFMPEG, film, index, origin, end, clip)


class ShiftTest(unittest.TestCase):
    QUEUE = {"source": {"sha256": "f" * 64, "path": "x"}, "detection_scope": {"selected": ["adult"]},
             "items": [{"id": "a", "start_seconds": 90.0, "end_seconds": 95.0},
                       {"id": "b", "start_seconds": 120.0, "end_seconds": 130.0,
                        "detected_intervals": [{"start_seconds": 121.0, "end_seconds": 122.0}]},
                       {"id": "c", "start_seconds": 400.0, "end_seconds": 410.0}],
             "advisory_items": [{"id": "d", "start_seconds": 99.8, "end_seconds": 99.8}]}

    def test_queue_items_move_into_clip_time_and_the_source_is_the_clip(self):
        moved = gc.shift_queue(self.QUEUE, Fraction(100), 200.0, "c" * 64)
        self.assertEqual([i["id"] for i in moved["items"]], ["b"])
        self.assertEqual((moved["items"][0]["start_seconds"], moved["items"][0]["detected_intervals"][0]["end_seconds"]),
                         (20.0, 22.0))
        self.assertEqual([i["id"] for i in moved["advisory_items"]], ["d"])  # its 0.5 s extent reaches the clip
        self.assertEqual(moved["source"], {"sha256": "c" * 64, "path": "x"})
        self.assertEqual(self.QUEUE["items"][1]["start_seconds"], 120.0)  # the film queue is untouched

    def test_labels_are_clamped_to_the_clip_and_a_label_outside_it_is_refused(self):
        moved = gc.shift_event({"id": "e", "start_seconds": 0.0, "end_seconds": 11762.72}, Fraction(816), 330.0)
        self.assertEqual((moved["start_seconds"], moved["end_seconds"]), (0.0, 330.0))
        with self.assertRaises(ValueError):
            gc.shift_event({"id": "e", "start_seconds": 10.0, "end_seconds": 20.0}, Fraction(816), 330.0)


class MigrationTest(unittest.TestCase):
    """v1 + v1.1 on one fake film, migrated to two clips; the same queue scores the same."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        film = {"job_id": 39, "path": "input/film.mp4", "sha256": "a" * 64, "size_bytes": 1,
                "duration_seconds": 1000.0, "width": 1920, "height": 1080, "fps": 25.0, "content_style": "live_action"}
        v1 = {"schema_version": 1, "golden_set": "v1", "sources": {"film": film},
              "segments": [{"id": "S1", "source": "film", "start_seconds": 0.0, "end_seconds": 100.0, "split": "dev"}]}
        v11 = {"schema_version": 1, "golden_set": "v1.1", "extends": "v1", "extends_manifest_sha256": canonical_sha256(v1),
               "sources": {"film": film},
               "segments": [{"id": "S2", "source": "film", "start_seconds": 500.0, "end_seconds": 600.0,
                             "split": "holdout"}]}
        box = {"x": 10, "y": 10, "width": 100, "height": 40}
        events = {"v1": [{"id": "gs-S1-0001", "segment_id": "S1", "category": "visual_logo", "start_seconds": 0.0,
                          "end_seconds": 1000.0, "region_source_pixels": box, "expected_action": "BLUR",
                          "severity": "must_catch", "ambiguous": False, "notes": "", "from_suggestion": None}],
                  "v1.1": [{"id": "gs-S2-0001", "segment_id": "S2", "category": "adult", "start_seconds": 520.0,
                            "end_seconds": 530.0, "region_source_pixels": None, "expected_action": "BLUR",
                            "severity": "must_catch", "ambiguous": False, "notes": "", "from_suggestion": "sug-1"},
                           {"id": "gs-S2-0002", "segment_id": "S2", "category": "gore", "start_seconds": 570.0,
                            "end_seconds": 575.0, "region_source_pixels": None, "expected_action": "KEEP",
                            "severity": None, "ambiguous": False, "notes": "", "from_suggestion": None}]}
        golden = self.root / "annotations/golden"
        for name, manifest in (("v1", v1), ("v1.1", v11)):
            write_json_atomic(golden / name / "segments.json", manifest)
            write_json_atomic(golden / name / "events.json", {
                "schema_version": 1, "golden_set": name, "manifest_sha256": canonical_sha256(manifest), "revision": 3,
                "segments": {s["id"]: {"status": "complete", "completed_at": "t"} for s in manifest["segments"]},
                "events": events[name], "suggestion_resolutions": {
                    "sug-1": {"resolution": "accepted", "event_id": "gs-S2-0001", "segment_id": "S2",
                              "suggestion": {"id": "sug-1", "segment_id": "S2", "start_seconds": 520.0,
                                             "end_seconds": 530.0}}} if name == "v1.1" else {}})
        self.manifest, self.labels, self.parts = load_golden_sets(golden, ["v1", "v1.1"])
        index = gc.VideoIndex(Fraction(1, 25000), [s * 25000 for s in range(1000)])
        self.clips = gc.plan_clips([{"source": "film", "job_id": 39, "start": s["start_seconds"], "end": s["end_seconds"],
                                     "segment_id": s["id"], "scored": True} for s in self.manifest["segments"]],
                                   {"film": index}, {"film": 1000.0}, PERIODS)
        self.infos = {c["key"]: {"path": f"media/{c['key']}.mp4", "sha256": canonical_sha256(c["key"]), "size_bytes": 5,
                                 "duration_seconds": float((c["end_seconds"] or 1000) - c["origin_seconds"]),
                                 "width": 1920, "height": 1080, "fps": 25.0} for c in self.clips}

    def tearDown(self):
        self.temp.cleanup()

    def queue(self, sha):
        return {"source": {"sha256": sha}, "detection_scope": {"selected": ["advertising", "adult", "gore", "violence"]},
                "items": [{"id": "logo", "category": "visual_logo", "start_seconds": 0.0, "end_seconds": 1000.0,
                           "suggested_region_source_pixels": {"x": 12, "y": 12, "width": 96, "height": 36},
                           "suggested_decision": "BLUR"},
                          {"id": "nude", "category": "adult", "start_seconds": 519.0, "end_seconds": 528.0,
                           "suggested_decision": "BLUR"},
                          {"id": "blood", "category": "gore", "start_seconds": 571.0, "end_seconds": 572.0,
                           "suggested_decision": "CUT"}]}

    def migrate(self):
        return gc.build_v2(self.parts, self.clips, self.infos, PERIODS, {"sug-9"}, created_at="t")

    def test_v2_keeps_ids_and_moves_every_time_into_its_clip(self):
        manifest, labels, history = self.migrate()
        validate_manifest(manifest)
        self.assertEqual([c["key"] for c in self.clips], ["j39-00000", "j39-00462"])
        self.assertEqual([(s["id"], s["source"], s["start_seconds"]) for s in manifest["segments"]],
                         [("S1", "j39-00000", 0.0), ("S2", "j39-00462", 38.0)])
        moved = {e["id"]: e for e in labels["events"]}
        self.assertEqual((moved["gs-S1-0001"]["end_seconds"], moved["gs-S2-0001"]["start_seconds"]),
                         (self.infos["j39-00000"]["duration_seconds"], 58.0))
        self.assertEqual(labels["suggestion_resolutions"]["sug-1"]["suggestion"]["start_seconds"], 58.0)
        self.assertEqual(manifest["sources"]["j39-00462"]["origin"]["origin_seconds"], 462.0)
        self.assertNotIn("job_id", manifest["sources"]["j39-00462"])
        self.assertEqual(set(manifest["derived_from"]), {"v1", "v1.1"})
        self.assertEqual(manifest["approximate_suggestions"], ["sug-9"])
        self.assertEqual((labels["manifest_sha256"], history["after"]["events"]), (canonical_sha256(manifest), 3))

    def score_both(self, tamper=None):
        manifest, labels, _ = self.migrate()
        folder = self.root / "staging/v2"
        write_json_atomic(folder / "segments.json", manifest)
        write_json_atomic(folder / "events.json", labels)
        clip_manifest, clip_labels, clip_parts = load_golden_sets(self.root / "staging", ["v2"])
        film_card, _ = score_sets(self.manifest, self.labels, self.parts, {"film": self.queue("a" * 64)})
        queues, offsets = {}, {}
        for key, source in clip_manifest["sources"].items():
            offset = source["origin"]["origin_pts"] * Fraction(source["origin"]["time_base"])
            queues[key] = gc.shift_queue(self.queue("a" * 64), offset, source["duration_seconds"], source["sha256"])
            offsets.update({s["id"]: offset for s in clip_manifest["segments"] if s["source"] == key})
        if tamper:
            tamper(queues)
        clip_card, _ = score_sets(clip_manifest, clip_labels, clip_parts, queues)
        return gc.compare_shifted_cards(film_card, clip_card, offsets)

    def test_the_same_queues_score_identically_on_films_and_clips(self):
        self.assertEqual(self.score_both(), [])

    def test_a_moved_item_is_reported(self):
        def tamper(queues):
            queues["j39-00462"]["items"][1]["start_seconds"] += 30.0
        problems = self.score_both(tamper)
        self.assertTrue(any("gs-S2-0001" in p for p in problems), problems)


class ClipSourceTest(unittest.TestCase):
    def test_a_clip_path_resolves_in_its_set_folder_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            set_dir = root / "annotations/golden-v2-staging/v2"
            (set_dir / "media").mkdir(parents=True)
            (set_dir / "media/c.mp4").write_bytes(b"12345")
            manifest = {"sources": {"c": {"kind": "clip", "path": "media/c.mp4", "size_bytes": 5,
                                          "sha256": "0" * 64}}}
            self.assertEqual(source_file(root, manifest, "c", set_dir=set_dir), (set_dir / "media/c.mp4").resolve())
            with self.assertRaises(ValueError):
                source_file(root, manifest, "c")  # a clip needs its set folder


if __name__ == "__main__":
    sys.exit(unittest.main())
