import copy
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "ocr_contiguous_benchmark",
    Path(__file__).resolve().parents[1] / "scripts/benchmark_ocr_contiguous.py",
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class OcrContiguousBenchmarkTests(unittest.TestCase):
    def test_report_comparison_ignores_only_documented_numeric_noise(self):
        baseline = {"tracks": [{"text": "brand", "confidence": .81, "region": [1, 2, 3, 4]}],
                    "frames_scanned": 30, "metrics": {"seconds": 10}}
        candidate = copy.deepcopy(baseline)
        candidate["tracks"][0]["confidence"] += .000001
        candidate["metrics"]["seconds"] = 5
        self.assertEqual(benchmark.normalize(baseline), benchmark.normalize(candidate))
        for field, value in (("text", "other"), ("region", [1, 2, 3, 5])):
            changed = copy.deepcopy(candidate)
            changed["tracks"][0][field] = value
            self.assertNotEqual(benchmark.normalize(baseline), benchmark.normalize(changed))
        candidate["frames_scanned"] = 29
        self.assertNotEqual(benchmark.normalize(baseline), benchmark.normalize(candidate))

    def test_queue_paths_can_differ_but_mapping_cannot(self):
        baseline = {"items": [{"source_candidate_refs": ["baseline/text.json#track:1"]}]}
        candidate = {"items": [{"source_candidate_refs": ["batch/text.json#track:1"]}]}
        self.assertEqual(benchmark.queue_projection(baseline), benchmark.queue_projection(candidate))
        candidate["items"][0]["source_candidate_refs"] = ["batch/text.json#track:2"]
        self.assertNotEqual(benchmark.queue_projection(baseline), benchmark.queue_projection(candidate))

    def test_queue_comparison_preserves_actions_intervals_and_geometry(self):
        baseline = {"items": [{"suggested_decision": "BLUR", "decision": None,
                    "source_frame_size": [960, 540], "suggested_blur_edge_mode": "tight",
                    "detected_intervals": [{"start_seconds": 0, "end_seconds": 3}],
                    "suggested_region_source_pixels": {"x": 1, "y": 2, "width": 3, "height": 4}}]}
        for field, value in (("suggested_decision", "KEEP"), ("decision", "BLUR"),
                             ("source_frame_size", [1920, 1080]),
                             ("suggested_blur_edge_mode", "full_width"),
                             ("detected_intervals", []), ("suggested_region_source_pixels", None)):
            changed = copy.deepcopy(baseline)
            changed["items"][0][field] = value
            self.assertNotEqual(benchmark.queue_projection(baseline), benchmark.queue_projection(changed), field)

    def test_advisory_candidates_cannot_disappear(self):
        baseline = {"items": [], "advisory_items": [{"category": "text"}]}
        self.assertNotEqual(benchmark.queue_projection(baseline), benchmark.queue_projection({"items": []}))


if __name__ == "__main__":
    unittest.main()
