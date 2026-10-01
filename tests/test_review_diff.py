import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_full_advertising import diff_review_queues, render_review_diff, review_difference_rows  # noqa: E402


def item(category, start, end, decision=None, region=None, **extra):
    value = {"category": category, "start_seconds": start, "end_seconds": end, "suggested_decision": decision,
             "suggested_region_source_pixels": region, "labels": ["x"], "priority": "high"}
    value.update(extra)
    return value


class ReviewDiffTests(unittest.TestCase):
    def test_identical_queues_have_no_rows(self):
        queue = {"items": [item("gore", 1, 2)], "advisory_items": [item("text", 5, 6, "KEEP")]}
        rows, summary, problem = diff_review_queues(queue, queue)
        self.assertEqual((rows, problem), ([], False))
        self.assertEqual(summary["items"], {"baseline": 1, "candidate": 1, "identical": 1, "changed": 0,
                                            "only_fp32": 0, "only_fp16": 0})

    def test_changed_lost_and_new_items_are_reported(self):
        box = {"x": 10, "y": 10, "width": 50, "height": 20}
        moved = {"x": 12, "y": 10, "width": 50, "height": 20}
        baseline = {"items": [item("visual_logo", 0, 10, "BLUR", box), item("gore", 20, 25)], "advisory_items": []}
        candidate = {"items": [item("visual_logo", 0, 10, "BLUR", moved), item("violence", 40, 41)],
                     "advisory_items": [item("text", 1, 2)]}
        rows, summary, problem = diff_review_queues(baseline, candidate)
        kinds = [(r["group"], r["kind"]) for r in rows]
        self.assertEqual(kinds, [("items", "khác chi tiết"), ("items", "chỉ có ở FP32 (mất khi dùng FP16)"),
                                 ("items", "chỉ có ở FP16 (mục mới)"), ("advisory_items", "chỉ có ở FP16 (mục mới)")])
        self.assertEqual(rows[0]["region_delta_px"], 2)
        self.assertFalse(rows[0]["decision_changed"])
        self.assertTrue(problem)  # a primary item disappeared
        self.assertEqual(summary["advisory_items"]["only_fp16"], 1)
        flat = review_difference_rows(rows)
        self.assertNotIn("preview_images", flat[0]["baseline"])
        self.assertIn("khác chi tiết", render_review_diff(rows, summary, "<p>intro</p>"))

    def test_only_a_suggestion_change_on_a_primary_item_is_a_problem(self):
        baseline = {"items": [item("visual_logo", 5, 10, "CUT")]}
        candidate = {"items": [item("visual_logo", 5, 10, "KEEP")]}
        rows, _, problem = diff_review_queues(baseline, candidate)
        self.assertTrue(rows[0]["decision_changed"])
        self.assertTrue(problem)
        advisory_only = diff_review_queues({"advisory_items": [item("text", 1, 2, "KEEP")]},
                                           {"advisory_items": [item("text", 1, 2, "BLUR")]})
        self.assertFalse(advisory_only[2])


if __name__ == "__main__":
    unittest.main()
