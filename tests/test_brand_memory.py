import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np

from biliflow.brand_memory import (
    approved_brand_memory_region,
    forget_review_item,
    hash_similarity,
    load_brand_memory,
    match_brand_memory,
    match_region_memory,
    memory_revision,
    perceptual_hash,
    remember_review_item,
    tighten_brand_foreground_region,
)


class BrandMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "state").mkdir()
        (self.root / "reports" / "review" / "thumbs").mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def _frame(self) -> np.ndarray:
        frame = np.full((180, 320, 3), 35, dtype=np.uint8)
        cv2.rectangle(frame, (18, 12), (94, 64), (230, 230, 230), 3)
        cv2.putText(frame, "NGA", (25, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (245, 30, 30), 2)
        return frame

    def test_perceptual_hash_tolerates_small_resize(self):
        frame = self._frame()
        resized = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_CUBIC)
        self.assertGreaterEqual(
            hash_similarity(perceptual_hash(frame), perceptual_hash(resized)), 0.95
        )

    def test_human_decision_creates_candidate_memory_without_edit_authority(self):
        frame_rgb = self._frame()
        preview = self.root / "reports" / "review" / "thumbs" / "logo.jpg"
        cv2.imwrite(str(preview), cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR))
        queue = {"source": {"sha256": "a" * 64}}
        item = {
            "id": "review-logo",
            "category": "visual_logo",
            "decision": "BLUR",
            "labels": ["NewGates Anime"],
            "candidate_type": "persistent_overlay",
            "preview_images": [preview.relative_to(self.root).as_posix()],
            "source_frame_size": [320, 180],
            "decision_region_source_pixels": {
                "x": 10, "y": 5, "width": 95, "height": 70,
            },
        }
        record = remember_review_item(self.root, queue, item)
        self.assertIsNotNone(record)
        payload = load_brand_memory(self.root)
        self.assertEqual(len(payload["records"]), 1)
        match = match_brand_memory(frame_rgb, payload["records"], minimum_similarity=0.9)
        self.assertIsNotNone(match)
        self.assertFalse(match["automatic_edit"])
        self.assertNotIn("decision", match)
        self.assertEqual(match["labels"], ["NewGates Anime"])
        self.assertEqual(match["memory_class"], "brand")
        self.assertEqual(match["relative_box"], record["relative_box"])
        self.assertEqual(
            approved_brand_memory_region(match, (320, 180)),
            {"x": 10, "y": 5, "width": 95, "height": 70},
        )

        revision = memory_revision(payload)
        self.assertTrue(forget_review_item(self.root, queue, "review-logo"))
        self.assertNotEqual(revision, memory_revision(load_brand_memory(self.root)))

    def test_keep_decision_creates_negative_signature_without_edit_authority(self):
        frame_rgb = self._frame()
        preview = self.root / "reports" / "review" / "thumbs" / "title.jpg"
        cv2.imwrite(str(preview), cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR))
        queue = {"source": {"sha256": "b" * 64}}
        item = {
            "id": "keep", "category": "visual_logo", "decision": "KEEP",
            "labels": ["Movie title"],
            "candidate_type": "title_overlay",
            "preview_images": [preview.relative_to(self.root).as_posix()],
            "source_frame_size": [320, 180],
            "suggested_region_source_pixels": {
                "x": 10, "y": 5, "width": 95, "height": 70,
            },
        }
        record = remember_review_item(self.root, queue, item)
        self.assertEqual(record["memory_class"], "non_brand")
        match = match_brand_memory(
            frame_rgb, load_brand_memory(self.root)["records"], minimum_similarity=0.9
        )
        self.assertEqual(match["memory_class"], "non_brand")
        self.assertFalse(match["automatic_edit"])
        regional = match_region_memory(
            frame_rgb, [10 / 320, 5 / 180, 95 / 320, 70 / 180],
            load_brand_memory(self.root)["records"], minimum_similarity=0.9,
        )
        self.assertEqual(regional["memory_class"], "non_brand")

    def test_frame_match_returns_geometry_from_winning_record(self):
        frame = self._frame()
        winning_box = [10 / 320, 5 / 180, 95 / 320, 70 / 180]
        wrong_last_box = [0.70, 0.70, 0.20, 0.20]
        winning_crop = frame[0:81, 2:113]
        records = [{
            "key": "winner", "decision": "BLUR", "memory_class": "brand",
            "relative_box": winning_box,
            "phash": perceptual_hash(winning_crop), "labels": ["winner"],
        }, {
            "key": "later-record", "decision": "BLUR", "memory_class": "brand",
            "relative_box": wrong_last_box,
            "phash": "0000000000000000", "labels": ["later"],
        }]
        # Use the production crop so the first record is unambiguously best.
        from biliflow.brand_memory import _relative_crop
        records[0]["phash"] = perceptual_hash(_relative_crop(frame, winning_box))
        match = match_brand_memory(frame, records, minimum_similarity=0.9)
        self.assertEqual(match["memory_key"], "winner")
        self.assertEqual(match["relative_box"], winning_box)

    def test_region_memory_rejects_same_hash_at_unrelated_location(self):
        frame = self._frame()
        learned_box = [10 / 320, 5 / 180, 95 / 320, 70 / 180]
        crop = frame[1:81, 2:113]
        record = {
            "decision": "BLUR", "memory_class": "brand",
            "relative_box": learned_box,
            "phash": perceptual_hash(crop),
        }
        self.assertIsNone(match_region_memory(
            frame, [0.60, 0.60, learned_box[2], learned_box[3]], [record],
            minimum_similarity=0.0,
        ))

    def test_foreground_refinement_trims_padding_without_clipping_logo(self):
        frame = np.full((180, 320, 3), (42, 68, 120), dtype=np.uint8)
        cv2.putText(
            frame, "BRAND", (48, 72), cv2.FONT_HERSHEY_SIMPLEX,
            1.0, (238, 238, 238), 3, cv2.LINE_AA,
        )
        outer = {"x": 30, "y": 20, "width": 230, "height": 90}
        refined = tighten_brand_foreground_region(frame, outer)
        self.assertGreater(refined["y"], outer["y"])
        self.assertLess(refined["height"], outer["height"])
        self.assertLessEqual(refined["x"], 48)
        self.assertGreaterEqual(refined["x"] + refined["width"], 150)
        self.assertLessEqual(refined["y"], 48)
        self.assertGreaterEqual(refined["y"] + refined["height"], 76)

    def test_foreground_refinement_is_noop_on_ambiguous_noise(self):
        rng = np.random.default_rng(19)
        frame = rng.integers(0, 256, size=(180, 320, 3), dtype=np.uint8)
        outer = {"x": 30, "y": 20, "width": 230, "height": 90}
        self.assertEqual(tighten_brand_foreground_region(frame, outer), outer)

    def test_full_frame_cut_is_not_saved_as_logo_memory(self):
        frame_rgb = self._frame()
        preview = self.root / "reports" / "review" / "thumbs" / "scene.jpg"
        cv2.imwrite(str(preview), cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR))
        queue = {"source": {"sha256": "c" * 64}}
        item = {
            "id": "scene-cut", "category": "visual_logo", "decision": "CUT",
            "preview_images": [preview.relative_to(self.root).as_posix()],
            "source_frame_size": [320, 180],
        }
        self.assertIsNone(remember_review_item(self.root, queue, item))
        self.assertEqual(load_brand_memory(self.root)["records"], [])


if __name__ == "__main__":
    unittest.main()
