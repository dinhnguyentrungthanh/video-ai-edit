import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.text_semantics import classify_text_report, classify_text_track


class TextSemanticTests(unittest.TestCase):
    def _track(self, **updates):
        track = {
            "track_id": 1,
            "start_seconds": 10.0,
            "end_seconds": 16.0,
            "recommended_blur_end_seconds": 19.5,
            "zone": "subtitle",
            "persistent": True,
            "union_box": [200, 600, 1080, 690],
            "sample_text": ["Ngày mai chúng ta sẽ gặp lại."],
            "max_confidence": 0.9,
        }
        track.update(updates)
        return track

    def test_strong_subtitle_is_not_routed_to_review(self):
        result = classify_text_track(
            self._track(),
            semantic_scores={"advertisement": 0.08, "subtitle": 0.78},
            analysis_size=[1280, 720],
            video_duration=3600,
            policy={"always_review_terms": ["Netflix"]},
        )
        self.assertFalse(result["review_candidate"])
        self.assertEqual(result["routing"], "LIKELY_SUBTITLE")

    def test_ad_semantics_force_review_without_a_keyword(self):
        result = classify_text_track(
            self._track(
                zone="top-center",
                sample_text=["Nhận ưu đãi độc quyền, truy cập ngay hôm nay"],
                union_box=[30, 20, 1250, 100],
            ),
            semantic_scores={"advertisement": 0.72, "subtitle": 0.12},
            analysis_size=[1280, 720],
            video_duration=3600,
            policy={"always_review_terms": []},
        )
        self.assertTrue(result["review_candidate"])
        self.assertEqual(result["routing"], "REVIEW_AD_LIKELY")

    def test_user_policy_override_only_routes_to_confirmation(self):
        result = classify_text_track(
            self._track(sample_text=["NETFLIX | DUBBING"]),
            semantic_scores={"advertisement": 0.10, "credits": 0.75},
            analysis_size=[1280, 720],
            video_duration=3600,
            policy={"always_review_terms": ["Netflix"]},
        )
        self.assertTrue(result["review_candidate"])
        self.assertEqual(result["routing"], "REVIEW_POLICY_OVERRIDE")

    def test_end_credit_roll_is_suppressed_unless_user_policy_matches(self):
        result = classify_text_track(
            self._track(
                start_seconds=3500.0,
                end_seconds=3506.0,
                zone="middle-center",
                sample_text=["Executive Producer | Production Manager"],
            ),
            semantic_scores={"advertisement": 0.58, "credits": 0.38},
            analysis_size=[1280, 720],
            video_duration=3600,
            policy={"always_review_terms": ["Netflix"]},
        )
        self.assertFalse(result["review_candidate"])
        self.assertEqual(result["routing"], "LIKELY_CREDITS")

    def test_small_one_off_scene_sign_is_not_an_ad_candidate(self):
        result = classify_text_track(
            self._track(
                start_seconds=800.0,
                end_seconds=803.0,
                zone="top-right",
                persistent=False,
                union_box=[1040, 80, 1160, 130],
                sample_text=["TOKYO"],
            ),
            semantic_scores={"advertisement": 0.01, "credits": 0.81, "scene_text": 0.15},
            analysis_size=[1280, 720],
            video_duration=3600,
            policy={"always_review_terms": ["Netflix"]},
        )
        self.assertFalse(result["review_candidate"])
        self.assertEqual(result["routing"], "LIKELY_SCENE_TEXT")

    def test_low_ad_ambiguous_text_is_optional_instead_of_required(self):
        result = classify_text_track(
            self._track(
                start_seconds=1200.0,
                end_seconds=1203.0,
                persistent=False,
                zone="middle-center",
                sample_text=["870"],
            ),
            semantic_scores={
                "advertisement": 0.04, "subtitle": 0.10,
                "credits": 0.10, "scene_text": 0.12,
            },
            analysis_size=[1280, 720],
            video_duration=3600,
            policy={"always_review_terms": []},
        )
        self.assertFalse(result["review_candidate"])
        self.assertEqual(result["routing"], "LOW_AD_UNCERTAIN")

    def test_low_ad_persistent_overlay_still_requires_review(self):
        result = classify_text_track(
            self._track(
                zone="top-right",
                persistent=True,
                union_box=[1000, 20, 1250, 80],
                sample_text=["UNKNOWN"],
            ),
            semantic_scores={
                "advertisement": 0.04, "subtitle": 0.10,
                "credits": 0.10, "scene_text": 0.12,
            },
            analysis_size=[1280, 720],
            video_duration=3600,
            policy={"always_review_terms": []},
        )
        self.assertTrue(result["review_candidate"])
        self.assertEqual(result["routing"], "REVIEW_UNCERTAIN_OVERLAY")

    def test_report_keeps_all_tracks_but_review_queue_can_filter(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "reports" / "raw").mkdir(parents=True)
            (root / "reports" / "smart").mkdir(parents=True)
            (root / "models" / "semantic").mkdir(parents=True)
            (root / "config").mkdir()
            source = root / "reports" / "raw" / "text-scan.json"
            source.write_text(json.dumps({
                "tracks": [self._track()],
                "analysis_size": [1280, 720],
                "duration_seconds": 3600,
            }), encoding="utf-8")
            policy = root / "config" / "policy.json"
            policy.write_text('{"always_review_terms": []}', encoding="utf-8")
            output = root / "reports" / "smart" / "scan.json"

            classify_text_report(
                project_root=root,
                input_report=source,
                output_report=output,
                model_dir=root / "models" / "semantic",
                policy_path=policy,
                seed_path=None,
                classifier=lambda _: {"advertisement": 0.05, "subtitle": 0.85},
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["tracks"]), 1)
            self.assertEqual(payload["review_candidate_count"], 0)
            self.assertFalse(payload["tracks"][0]["review_candidate"])


if __name__ == "__main__":
    unittest.main()
