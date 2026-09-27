import unittest

from biliflow.image_benchmark import binary_metrics
from biliflow.intervals import merge_intervals
from biliflow.scanner import complete_nsfw_sequence_context, temporal_confirm_hits


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


class NsfwSequenceCompletionTests(unittest.TestCase):
    def test_merges_short_cutaways_and_extends_only_from_strong_seed(self) -> None:
        intervals = [
            {"start_seconds": 421.5, "end_seconds": 438.0, "max_score": 0.999,
             "strongest_frame": "a.jpg", "sample_count": 12},
            {"start_seconds": 441.0, "end_seconds": 446.0, "max_score": 0.999,
             "strongest_frame": "b.jpg", "sample_count": 7},
            {"start_seconds": 449.0, "end_seconds": 460.5, "max_score": 0.998,
             "strongest_frame": "c.jpg", "sample_count": 10},
            {"start_seconds": 475.0, "end_seconds": 478.0, "max_score": 0.977,
             "strongest_frame": "d.jpg", "sample_count": 2},
        ]
        merged = merge_intervals(intervals, maximum_gap_seconds=3.0)
        samples = [
            {"timestamp_seconds": 415.5, "score": 0.766},
            {"timestamp_seconds": 414.5, "score": 0.38},
            {"timestamp_seconds": 464.5, "score": 0.897},
            {"timestamp_seconds": 466.0, "score": 0.01},
            {"timestamp_seconds": 476.0, "score": 0.964},
        ]
        completed = complete_nsfw_sequence_context(
            merged,
            samples,
            context_threshold=0.70,
            context_seconds=8.0,
            padding_seconds=1.0,
            duration_seconds=1200.0,
        )
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[0]["start_seconds"], 414.5)
        self.assertEqual(completed[0]["end_seconds"], 465.5)
        self.assertTrue(completed[0]["sequence_context"]["applied"])
        self.assertEqual(completed[1]["start_seconds"], 475.0)

    def test_moderate_evidence_does_not_create_interval_without_seed(self) -> None:
        completed = complete_nsfw_sequence_context(
            [],
            [{"timestamp_seconds": 10.0, "score": 0.90}],
            context_threshold=0.70,
            context_seconds=8.0,
            padding_seconds=1.0,
            duration_seconds=100.0,
        )
        self.assertEqual(completed, [])


if __name__ == "__main__":
    unittest.main()
