import unittest

from biliflow.vlm_confirmation import _answer_state, _timestamp


class VlmConfirmationTests(unittest.TestCase):
    def test_timestamp_uses_strongest_frame_evidence(self) -> None:
        interval = {
            "start_seconds": 100.0,
            "end_seconds": 110.0,
            "strongest_frame": "thumbnails/clip-003151-3150.875s.jpg",
        }
        self.assertEqual(_timestamp(interval), 3150.875)

    def test_timestamp_falls_back_to_interval_midpoint(self) -> None:
        interval = {"start_seconds": 10.0, "end_seconds": 14.0}
        self.assertEqual(_timestamp(interval), 12.0)

    def test_answer_state_keeps_uncertain_outputs_for_human_review(self) -> None:
        self.assertEqual(_answer_state("YES"), "CONFIRMED")
        self.assertEqual(_answer_state("no"), "REJECTED")
        self.assertEqual(_answer_state("I cannot tell"), "UNCERTAIN")


if __name__ == "__main__":
    unittest.main()
