from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from biliflow.ad_candidate_pipeline import (
    binary_metrics,
    box_iou_xywh,
    build_regression_manifest,
    select_grounding_fallback,
)


class AdCandidatePipelineTests(unittest.TestCase):
    def test_binary_metrics_and_iou(self) -> None:
        metrics = binary_metrics([True, True, False, False], [True, False, True, False])
        self.assertEqual(metrics["true_positive"], 1)
        self.assertEqual(metrics["true_negative"], 1)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertEqual(metrics["specificity"], 0.5)
        self.assertEqual(
            box_iou_xywh(
                {"x": 0, "y": 0, "width": 10, "height": 10},
                {"x": 5, "y": 5, "width": 10, "height": 10},
            ),
            25 / 175,
        )

    def test_grounding_fallback_expands_a_confirmed_logo_region(self) -> None:
        selected = select_grounding_fallback(
            [{
                "x": 109, "y": 25, "width": 215, "height": 201,
                "score": 0.91, "label": "brand logo",
            }],
            [{"x": 143, "y": 117, "width": 133, "height": 42}],
            image_size=(1920, 1080),
            focus_region={"x": 0, "y": 0, "width": 600, "height": 500},
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected["width"], 215)

    def test_grounding_fallback_rejects_unrelated_box(self) -> None:
        selected = select_grounding_fallback(
            [{
                "x": 1500, "y": 700, "width": 200, "height": 100,
                "score": 0.95, "label": "brand logo",
            }],
            [{"x": 100, "y": 20, "width": 200, "height": 100}],
            image_size=(1920, 1080),
            focus_region={"x": 0, "y": 0, "width": 500, "height": 300},
        )
        self.assertIsNone(selected)

    def test_manifest_is_balanced_and_uses_saved_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "reports"
            previews = reports / "sample" / "thumbnails"
            previews.mkdir(parents=True)
            rows = []
            items = []
            for index, decision in enumerate(("BLUR", "CUT", "KEEP", "KEEP")):
                image_path = previews / f"{index}.jpg"
                Image.new("RGB", (64, 36), "black").save(image_path)
                item_id = f"item-{index}"
                items.append({
                    "id": item_id,
                    "preview_images": [str(image_path.relative_to(root))],
                    "labels": ["candidate"],
                    "suggested_region_source_pixels": {
                        "x": 1, "y": 2, "width": 10, "height": 8,
                    },
                })
                rows.append({
                    "review_item_id": item_id,
                    "category": "visual_logo",
                    "expected_decision": decision,
                    "queue": "reports/sample/queue.json",
                })
            (reports / "sample" / "queue.json").write_text(
                json.dumps({"items": items}), encoding="utf-8"
            )
            regression = root / "annotations.json"
            regression.write_text(json.dumps({"examples": rows}), encoding="utf-8")

            manifest = build_regression_manifest(
                root, regression, positive_limit=1, negative_limit=1
            )

            self.assertEqual(len(manifest), 2)
            self.assertEqual(sum(item.expected_positive for item in manifest), 1)
            self.assertTrue(any(item.expected_region is not None for item in manifest))


if __name__ == "__main__":
    unittest.main()

