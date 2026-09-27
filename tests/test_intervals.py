import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.intervals import compact_interval_thumbnails, group_hits, merge_intervals


class GroupHitsTests(unittest.TestCase):
    def test_groups_nearby_hits_and_pads_bounds(self):
        hits = [
            {"timestamp_seconds": 0.5, "score": 0.8, "thumbnail": "a.jpg"},
            {"timestamp_seconds": 1.5, "score": 0.9, "thumbnail": "b.jpg"},
            {"timestamp_seconds": 8.0, "score": 0.7, "thumbnail": "c.jpg"},
        ]
        result = group_hits(hits, 2.0, 1.0, 8.5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["start_seconds"], 0.0)
        self.assertEqual(result[0]["max_score"], 0.9)
        self.assertEqual(result[1]["end_seconds"], 8.5)

    def test_keeps_only_one_thumbnail_per_interval(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "thumbnails").mkdir()
            hits = []
            for index, score in enumerate((0.7, 0.9, 0.8)):
                relative = f"thumbnails/{index}.jpg"
                (root / relative).write_bytes(b"frame")
                hits.append(
                    {
                        "timestamp_seconds": float(index),
                        "score": score,
                        "thumbnail": relative,
                    }
                )
            intervals = group_hits(hits, 2.0, 0.0, 3.0)
            retained = compact_interval_thumbnails(root, hits, intervals)
            self.assertEqual(retained, 1)
            self.assertEqual(
                [path.name for path in (root / "thumbnails").glob("*.jpg")],
                ["1.jpg"],
            )

    def test_merges_nearby_review_intervals_and_keeps_strongest(self):
        intervals = [
            {
                "start_seconds": 10.0, "end_seconds": 12.0,
                "max_score": 0.6, "strongest_frame": "a.jpg", "sample_count": 3,
            },
            {
                "start_seconds": 15.0, "end_seconds": 18.0,
                "max_score": 0.9, "strongest_frame": "b.jpg", "sample_count": 4,
            },
        ]
        result = merge_intervals(intervals, maximum_gap_seconds=3.0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["strongest_frame"], "b.jpg")
        self.assertEqual(result[0]["sample_count"], 7)


if __name__ == "__main__":
    unittest.main()
