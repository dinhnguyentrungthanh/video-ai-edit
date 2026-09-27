import unittest

from biliflow.image_benchmark import binary_metrics
from biliflow.scanner import temporal_confirm_hits


class BinaryMetricsTests(unittest.TestCase):
    def test_counts_and_rates(self) -> None:
        metrics = binary_metrics([1, 1, 0, 0], [1, 0, 1, 0])
        self.assertEqual(metrics["true_positive"], 1)
        self.assertEqual(metrics["true_negative"], 1)
        self.assertEqual(metrics["false_positive"], 1)
        self.assertEqual(metrics["false_negative"], 1)
        self.assertEqual(metrics["accuracy"], 0.5)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertEqual(metrics["specificity"], 0.5)
        self.assertEqual(metrics["f1"], 0.5)

    def test_handles_missing_positive_predictions(self) -> None:
        metrics = binary_metrics([0, 0], [0, 0])
        self.assertIsNone(metrics["precision"])
        self.assertIsNone(metrics["recall"])
        self.assertEqual(metrics["specificity"], 1.0)


class TemporalConfirmationTests(unittest.TestCase):
    def test_requires_three_hits_inside_five_frames(self) -> None:
        hits = [{"frame_index": value} for value in (1, 2, 3, 10, 12)]
        confirmed = temporal_confirm_hits(hits, window_frames=5, minimum_hits=3)
        self.assertEqual([hit["frame_index"] for hit in confirmed], [1, 2, 3])

    def test_rejects_isolated_hits(self) -> None:
        hits = [{"frame_index": value} for value in (1, 7, 14)]
        self.assertEqual(temporal_confirm_hits(hits, window_frames=5, minimum_hits=3), [])


if __name__ == "__main__":
    unittest.main()
