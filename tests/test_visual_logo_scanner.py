import unittest
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from biliflow.brand_memory import _relative_crop, perceptual_hash

from biliflow.visual_logo_scanner import (
    _read_routing_cache,
    _route_with_brand_memory,
    _write_routing_cache,
    adaptive_candidate_budget,
    candidate_selection_coverage,
    consolidate_end_card_intervals,
    consolidate_opening_promotion_intervals,
    consolidate_persistent_overlay_intervals,
    effective_sample_every,
    is_instruction_echo,
    logo_candidate_features,
    parse_logo_answer,
    parse_boundary_scene_answer,
    regional_logo_candidate_features,
    retain_coverage_candidate,
    select_window_evidence,
    select_candidate_windows,
    validated_source_sha256,
    write_visual_logo_audit_html,
)


class VisualLogoScannerTests(unittest.TestCase):
    def test_legacy_lossy_routing_cache_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "routing.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "source_sha256": "a" * 64,
                    "cache_key": "test", "windows": []}, handle)
            self.assertIsNone(_read_routing_cache(path, source_sha256="a" * 64, cache_key="test"))

    def test_cache_keeps_middle_regional_evidence_used_by_window_routing(self):
        key = (10.0, 15.0)
        frames = [
            {"timestamp_seconds": 10.0, "jpeg": b"strong", "focus_jpeg": None,
             "features": {"score": .99, "focus_region": "full", "full_frame_score": .99, "regional_score": .99}},
            {"timestamp_seconds": 12.0, "jpeg": b"middle", "focus_jpeg": b"logo",
             "features": {"score": .8, "focus_region": "top_right", "full_frame_score": .2, "regional_score": .8}},
            {"timestamp_seconds": 14.0, "jpeg": b"latest", "focus_jpeg": None,
             "features": {"score": .5, "focus_region": "full", "full_frame_score": .5, "regional_score": .5}},
        ]
        windows = {key: frames}
        before = candidate_selection_coverage(windows, [key], scan_start=0, coverage_bucket_seconds=300)
        self.assertEqual(before["regional_candidate_windows"], 1)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "routing.json.gz"
            _write_routing_cache(path, source_sha256="a" * 64, cache_key="test",
                windows=windows, window_sample_counts={key: 3}, boundary_keys=set(),
                boundary_transitions=[], frames_scanned=3, heuristic_hits=3,
                coverage_fallback_count=0, scene_routed_count=0)
            restored = _read_routing_cache(path, source_sha256="a" * 64, cache_key="test")
        self.assertEqual(restored["decoded_windows"], windows)
        self.assertEqual(candidate_selection_coverage(restored["decoded_windows"], [key],
            scan_start=0, coverage_bucket_seconds=300), before)
        self.assertEqual(select_window_evidence(restored["decoded_windows"][key]), select_window_evidence(frames))

    def test_brand_memory_replaces_unrelated_focus_with_learned_region(self):
        frame = np.full((180, 320, 3), 20, dtype=np.uint8)
        frame[9:45, 256:307] = 230
        relative_box = [0.80, 0.05, 0.16, 0.20]
        crop = _relative_crop(frame, relative_box)
        record = {
            "key": "known-logo", "review_item_id": "review-logo",
            "source_sha256": "a" * 64, "decision": "BLUR",
            "memory_class": "brand", "relative_box": relative_box,
            "phash": perceptual_hash(crop), "labels": ["Known watermark"],
        }
        features = {
            "score": 0.2, "focus_box_analysis": [20, 20, 100, 100],
            "focus_region": "center",
        }
        routed = _route_with_brand_memory(frame, features, [record])
        self.assertEqual(routed["focus_box_analysis"], [256, 9, 307, 45])
        self.assertEqual(routed["focus_region"], "approved_brand_memory")
        self.assertEqual(routed["brand_memory"]["relative_box"], relative_box)

    def test_boundary_scene_parser_is_strict_and_auditable(self):
        self.assertEqual(
            parse_boundary_scene_answer("PROMO_FULL_FRAME"), "PROMO_FULL_FRAME"
        )
        self.assertEqual(
            parse_boundary_scene_answer("MOVIE_CONTENT | current episode"), "MOVIE_CONTENT"
        )
        self.assertEqual(parse_boundary_scene_answer("maybe promo"), "UNCERTAIN")

    def test_two_opening_promotions_become_one_cut_suggestion(self):
        intervals = [
            {
                "start_seconds": 0, "end_seconds": 5, "max_score": 0.9,
                "candidate_type": "opening_promotion", "strongest_frame": "a.jpg",
            },
            {
                "start_seconds": 5, "end_seconds": 10, "max_score": 0.8,
                "candidate_type": "opening_promotion", "strongest_frame": "b.jpg",
            },
            {
                "start_seconds": 10, "end_seconds": 15, "max_score": 1.0,
                "candidate_type": None, "strongest_frame": "movie.jpg",
            },
        ]
        result = consolidate_opening_promotion_intervals(
            intervals, scan_start=0, boundary_seconds=30, window_seconds=5,
        )
        opening = next(item for item in result if item.get("candidate_type") == "opening_promotion")
        self.assertEqual((opening["start_seconds"], opening["end_seconds"]), (0, 10.0))
        self.assertEqual(opening["suggested_decision"], "CUT")
        self.assertEqual(len(result), 2)

    def test_cache_checksum_is_verified_from_file_content(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.bin"
            path.write_bytes(b"video-a")
            digest = validated_source_sha256(path)
            self.assertEqual(validated_source_sha256(path, digest), digest)
            path.write_bytes(b"video-b")
            with self.assertRaisesRegex(ValueError, "checksum changed"):
                validated_source_sha256(path, digest)

    def test_routing_cache_round_trip_preserves_evidence_and_counts(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "routing.json.gz"
            key = (10.0, 15.0)
            windows = {key: [{
                "timestamp_seconds": 12.0,
                "jpeg": b"jpeg",
                "focus_jpeg": b"focus",
                "features": {"score": 0.8},
            }]}
            _write_routing_cache(
                path, source_sha256="a" * 64, cache_key="cache",
                windows=windows, window_sample_counts={key: 7},
                boundary_keys={key}, boundary_transitions=[(12.0, 0.3)],
                frames_scanned=99, heuristic_hits=4,
                coverage_fallback_count=2, scene_routed_count=1,
            )
            restored = _read_routing_cache(
                path, source_sha256="a" * 64, cache_key="cache",
            )
        self.assertIsNotNone(restored)
        self.assertEqual(restored["decoded_counts"][key], 7)
        self.assertEqual(restored["decoded_windows"][key][0]["jpeg"], b"jpeg")
        self.assertEqual(restored["coverage_fallback_count"], 2)

    def test_coverage_fallback_keeps_only_strongest_frames(self):
        buckets = {}
        for timestamp, score in [(2, 0.2), (4, 0.8), (6, 0.5)]:
            retain_coverage_candidate(
                buckets, 0,
                {"timestamp_seconds": timestamp, "features": {"score": score}},
                maximum_per_bucket=2,
            )
        self.assertEqual(
            [item["timestamp_seconds"] for item in buckets[0]], [4, 6]
        )

    def test_adaptive_budget_reduces_short_video_vlm_work(self):
        self.assertEqual(adaptive_candidate_budget(
            scan_duration=445, requested_maximum=80, boundary_window_count=12,
        ), 48)
        self.assertEqual(adaptive_candidate_budget(
            scan_duration=4084, requested_maximum=80, boundary_window_count=12,
        ), 80)

    def test_regional_router_surfaces_small_upper_logo(self):
        frame = np.full((180, 320, 3), 205, dtype=np.uint8)
        previous = frame.copy()
        # Small stable stylized mark in the upper-left over changing content.
        frame[12:36, 30:64] = (240, 30, 30)
        previous[12:36, 30:64] = (240, 30, 30)
        frame[70:170, 80:300] = (20, 130, 210)
        previous[70:170, 80:300] = (210, 130, 20)
        features = regional_logo_candidate_features(frame, previous)
        self.assertNotEqual(features["focus_region"], "full")
        self.assertGreaterEqual(features["score"], features["full_frame_score"])
        self.assertEqual(len(features["focus_box_analysis"]), 4)

    def test_end_card_bridges_one_rejected_window_and_suggests_cut(self):
        intervals = [
            {"start_seconds": 430, "end_seconds": 435, "max_score": 0.8,
             "predicted_label": "Visual brand/logo candidate", "strongest_frame": "a.jpg",
             "candidate_type": "closing_promotion"},
            {"start_seconds": 440, "end_seconds": 445, "max_score": 0.9,
             "predicted_label": "NewGates Anime", "strongest_frame": "c.jpg"},
        ]
        rejected = [{"start_seconds": 435, "end_seconds": 440}]
        retained, still_rejected = consolidate_end_card_intervals(
            intervals, rejected, duration=445.3, boundary_seconds=30,
            window_seconds=5, transition_samples=[(429.1, 0.31), (430.0, 0.02)],
        )
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]["start_seconds"], 429.1)
        self.assertEqual(retained[0]["end_seconds"], 445.3)
        self.assertEqual(retained[0]["candidate_type"], "branded_end_card")
        self.assertEqual(retained[0]["suggested_decision"], "CUT")
        self.assertEqual(still_rejected, rejected)

    def test_end_card_primary_preview_is_inside_refined_interval(self):
        intervals = [
            {
                "start_seconds": 6730, "end_seconds": 6735,
                "max_score": 1.0,
                "strongest_frame": "logo-6730.069s.jpg",
                "strongest_timestamp_seconds": 6730.069,
                "candidate_type": "closing_promotion",
            },
            {
                "start_seconds": 6735, "end_seconds": 6738.1,
                "max_score": 0.8,
                "strongest_frame": "logo-6735.069s.jpg",
                "strongest_timestamp_seconds": 6735.069,
            },
        ]
        retained, _ = consolidate_end_card_intervals(
            intervals, [], duration=6738.1, boundary_seconds=30,
            window_seconds=5,
            transition_samples=[(6730.319, 0.3), (6730.069, 0.02)],
        )
        self.assertEqual(retained[0]["start_seconds"], 6730.319)
        self.assertEqual(retained[0]["strongest_frame"], "logo-6735.069s.jpg")
        self.assertEqual(retained[0]["strongest_timestamp_seconds"], 6735.069)

    def test_corner_watermark_alone_does_not_become_full_scene_end_card(self):
        intervals = [{
            "start_seconds": start, "end_seconds": end, "max_score": 1.0,
            "strongest_frame": f"logo-{timestamp:.3f}s.jpg",
            "strongest_timestamp_seconds": timestamp,
            "visual_logo_confirmation": {
                "confirmation_source": "approved_brand_memory",
                "boundary_scene_context": {"state": "UNCERTAIN"},
                "features": {"focus_region": "approved_brand_memory"},
            },
        } for start, end, timestamp in (
            (6730, 6735, 6730.069), (6735, 6738.1, 6735.069),
        )]
        retained, rejected = consolidate_end_card_intervals(
            intervals, [], duration=6738.1, boundary_seconds=30,
            window_seconds=5, transition_samples=[(6730.319, 0.3)],
        )
        self.assertEqual(retained, intervals)
        self.assertEqual(rejected, [])
        self.assertFalse(any(
            item.get("candidate_type") == "branded_end_card" for item in retained
        ))

    def test_window_evidence_keeps_strongest_and_latest(self):
        frames = [
            {"timestamp_seconds": 5.0, "features": {"score": 0.9}},
            {"timestamp_seconds": 7.5, "features": {"score": 0.3}},
            {"timestamp_seconds": 9.5, "features": {"score": 0.4}},
        ]
        selected = select_window_evidence(frames, maximum=2)
        self.assertEqual([item["timestamp_seconds"] for item in selected], [5.0, 9.5])

    def test_repeated_top_region_is_one_persistent_overlay(self):
        def item(start, focus="top_left"):
            return {
                "start_seconds": start, "end_seconds": start + 5,
                "max_score": 0.9, "predicted_label": "Known watermark",
                "strongest_frame": f"{start}.jpg",
                "visual_logo_confirmation": {"features": {"focus_region": focus}},
            }
        intervals = [item(10), item(55), item(315), item(350), item(100, "full"), {
            **item(430), "start_seconds": 429.1, "end_seconds": 445.3,
            "candidate_type": "branded_end_card",
        }]
        result = consolidate_persistent_overlay_intervals(
            intervals, duration=445.3, window_seconds=5,
            transition_samples=[(6.2, 0.35), (9.8, 0.02)],
        )
        overlays = [item for item in result if item.get("candidate_type") == "persistent_overlay"]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0]["start_seconds"], 6.2)
        self.assertEqual(overlays[0]["end_seconds"], 429.1)
        self.assertEqual(len(overlays[0]["supporting_frames"]), 3)

    def test_two_persistent_regions_remain_two_review_tracks(self):
        def item(start, focus):
            return {
                "start_seconds": start, "end_seconds": start + 5,
                "max_score": 0.9, "predicted_label": f"Known {focus} watermark",
                "strongest_frame": f"{focus}-{start}.jpg",
                "visual_logo_confirmation": {"features": {"focus_region": focus}},
            }
        intervals = [
            *[item(value, "top_left") for value in (10, 50, 100, 160)],
            *[item(value, "top_right") for value in (20, 70, 120, 170)],
        ]
        result = consolidate_persistent_overlay_intervals(
            intervals, duration=200, window_seconds=5,
        )
        overlays = [value for value in result if value.get("candidate_type") == "persistent_overlay"]
        self.assertEqual(len(overlays), 2)
        self.assertEqual(
            {value["temporal_confirmation"]["focus_region"] for value in overlays},
            {"top_left", "top_right"},
        )

    def test_same_label_in_one_tile_does_not_share_a_timeline(self):
        # Conan Movie 20: a grounding box on a character's head and the real
        # phimmoi watermark both centred inside the wide top_right tile and
        # carried the same label, so the head box inherited the watermark's
        # timeline and reached review as a whole-film blur demand.
        def item(start, box):
            return {
                "start_seconds": start, "end_seconds": start + 5,
                "max_score": 0.9, "predicted_label": "Known watermark",
                "strongest_frame": f"{start}.jpg",
                "visual_logo_confirmation": {"features": {
                    "focus_region": "top_right", "focus_box_analysis": list(box),
                }},
            }
        head = (1216, 206, 1451, 418)
        marks = [(1598, 58, 1848, 99), (1566, 58, 1816, 99), (1561, 51, 1811, 92)]
        result = consolidate_persistent_overlay_intervals(
            [item(510, head), *[
                item(start, box) for start, box in zip((520, 3000, 6000), marks)
            ]],
            duration=6690, window_seconds=5,
        )
        # Three real observations are below the support threshold on their own,
        # and the head box must not be the fourth that promotes them.
        self.assertFalse(any(
            value.get("candidate_type") == "persistent_overlay" for value in result
        ))

    def test_corroborated_watermark_still_consolidates_beside_a_stray_box(self):
        def item(start, box, name):
            return {
                "start_seconds": start, "end_seconds": start + 5,
                "max_score": 0.9, "predicted_label": "Known watermark",
                "strongest_frame": f"{name}-{start}.jpg",
                "visual_logo_confirmation": {"features": {
                    "focus_region": "top_right", "focus_box_analysis": list(box),
                }},
            }
        mark = (1598, 58, 1848, 99)
        result = consolidate_persistent_overlay_intervals(
            [
                item(510, (1216, 206, 1451, 418), "head"),
                *[item(start, mark, "mark") for start in (520, 2000, 4000, 6000)],
            ],
            duration=6690, window_seconds=5,
        )
        overlays = [
            value for value in result
            if value.get("candidate_type") == "persistent_overlay"
        ]
        self.assertEqual(len(overlays), 1)
        self.assertTrue(all(
            "head" not in str(frame)
            for frame in overlays[0].get("supporting_frames") or []
        ))

    def test_generic_confirmations_do_not_become_full_timeline_overlay(self):
        intervals = [{
            "start_seconds": start, "end_seconds": start + 5,
            "max_score": 0.9, "predicted_label": "Visual brand/logo candidate",
            "strongest_frame": f"{start}.jpg",
            "visual_logo_confirmation": {"features": {"focus_region": "top_left"}},
        } for start in (10, 100, 200, 300)]
        result = consolidate_persistent_overlay_intervals(
            intervals, duration=400, window_seconds=5,
        )
        self.assertFalse(any(
            value.get("candidate_type") == "persistent_overlay" for value in result
        ))
        self.assertEqual(len(result), 4)

    def test_opening_promotions_never_seed_a_persistent_overlay(self):
        intervals = [{
            "start_seconds": start, "end_seconds": start + 5,
            "max_score": 1.0, "predicted_label": "Known watermark",
            "strongest_frame": f"{start}.jpg",
            "candidate_type": "opening_promotion",
            "visual_logo_confirmation": {"features": {
                "focus_region": "top_right",
                "focus_box_analysis": [280, 0, 320, 30],
            }},
        } for start in (0, 5, 10, 15)]
        result = consolidate_persistent_overlay_intervals(
            intervals, duration=600, window_seconds=5,
        )
        self.assertEqual(result, intervals)
        self.assertFalse(any(
            item.get("candidate_type") == "persistent_overlay" for item in result
        ))

    def test_nearby_approved_memory_boxes_form_one_track(self):
        def item(start, box, relative_box):
            return {
                "start_seconds": start, "end_seconds": start + 5,
                "max_score": 0.95, "predicted_label": "Known watermark",
                "strongest_frame": f"{start}.jpg",
                "visual_logo_confirmation": {
                    "confirmation_source": "approved_brand_memory",
                    "features": {
                        "focus_region": "top_right",
                        "focus_box_analysis": list(box),
                        "brand_memory": {
                            "memory_class": "brand",
                            "relative_box": list(relative_box),
                        },
                    },
                },
            }
        result = consolidate_persistent_overlay_intervals(
            [
                item(10, (280, 5, 319, 25), (0.875, 0.02, 0.12, 0.09)),
                item(100, (278, 5, 318, 25), (0.87, 0.02, 0.125, 0.09)),
                item(200, (281, 4, 320, 24), (0.878, 0.018, 0.12, 0.09)),
                item(300, (279, 6, 319, 26), (0.872, 0.022, 0.123, 0.09)),
            ],
            duration=400, window_seconds=5,
        )
        overlays = [
            item for item in result
            if item.get("candidate_type") == "persistent_overlay"
        ]
        self.assertEqual(len(overlays), 1)
        self.assertEqual(overlays[0]["end_seconds"], 400)

    def test_weak_memory_match_cannot_create_a_persistent_track(self):
        intervals = [{
            "start_seconds": start, "end_seconds": start + 5,
            "max_score": 1.0,
            "predicted_label": "Visual brand/logo candidate",
            "strongest_frame": f"{start}.jpg",
            "visual_logo_confirmation": {
                "confirmation_source": "qwen_local",
                "features": {
                    "focus_region": "middle_right",
                    "focus_box_analysis": [173, 36, 320, 148],
                    "brand_memory": {
                        "memory_class": "brand", "similarity": 0.84,
                        "relative_box": [0.81, 0.04, 0.15, 0.05],
                    },
                },
            },
        } for start in (5, 500, 1000, 2000, 3000, 4000, 6000)]
        result = consolidate_persistent_overlay_intervals(
            intervals, duration=6690, window_seconds=5,
        )
        self.assertFalse(any(
            item.get("candidate_type") == "persistent_overlay" for item in result
        ))

    def test_exhaustive_mode_caps_sampling_interval_for_short_logos(self):
        self.assertEqual(effective_sample_every(2.0, True), 0.5)
        self.assertEqual(effective_sample_every(0.25, True), 0.25)
        self.assertEqual(effective_sample_every(2.0, False), 2.0)

    def test_audit_html_keeps_rejected_window_and_escapes_answer(self):
        with TemporaryDirectory() as directory:
            output = write_visual_logo_audit_html(Path(directory), [{
                "start_seconds": 0,
                "end_seconds": 5,
                "audit_frame": "audit-thumbnails/window.jpg",
                "visual_logo_confirmation": {
                    "state": "REJECTED",
                    "answer": "NO <unsafe>",
                },
            }])
            document = output.read_text(encoding="utf-8")
        self.assertIn('data-state="REJECTED"', document)
        self.assertIn("NO &lt;unsafe&gt;", document)

    def test_centered_mark_on_uniform_background_is_a_strong_candidate(self):
        frame = np.zeros((180, 320, 3), dtype=np.uint8)
        frame[45:140, 125:145] = (230, 0, 0)
        frame[45:140, 175:195] = (180, 0, 0)
        for offset in range(26):
            frame[50 + offset:140, 145 + offset:146 + offset] = (255, 20, 20)
        features = logo_candidate_features(frame)
        self.assertGreaterEqual(features["score"], 0.52)
        self.assertGreater(features["center_fraction"], 0.9)

    def test_full_frame_scene_does_not_receive_ident_layout_score(self):
        rng = np.random.default_rng(7)
        frame = rng.integers(0, 256, size=(180, 320, 3), dtype=np.uint8)
        features = logo_candidate_features(frame)
        self.assertLess(features["ident_score"], 0.35)

    def test_parse_confirmed_brand_name(self):
        self.assertEqual(parse_logo_answer("YES | Netflix"), ("CONFIRMED", "Netflix"))

    def test_parse_clean_movie_policy_answers(self):
        self.assertEqual(
            parse_logo_answer("REMOVE | Netflix"), ("CONFIRMED", "Netflix")
        )
        self.assertEqual(parse_logo_answer("KEEP | scene billboard"), ("REJECTED", None))
        self.assertEqual(parse_logo_answer("UNCERTAIN | tiny mark"), ("UNCERTAIN", None))

    def test_instruction_echo_is_not_a_confirmation(self):
        self.assertEqual(
            parse_logo_answer("YES | brand name when known"), ("UNCERTAIN", None)
        )
        self.assertTrue(
            is_instruction_echo("YES | We Are Finding Graphics Added Before, After, or On Top Of A Movie")
        )
        self.assertEqual(
            parse_logo_answer("YES | We are finding graphics added before a movie"),
            ("UNCERTAIN", None),
        )

    def test_parse_uncertain_answer_is_retained(self):
        self.assertEqual(parse_logo_answer("Maybe a logo"), ("UNCERTAIN", None))

    def test_candidate_budget_is_distributed_across_timeline(self):
        windows = {}
        for timestamp in range(0, 1200, 10):
            windows[(float(timestamp), float(timestamp + 5))] = [
                {"features": {"score": 1.0 - timestamp / 2000}}
            ]
        selected = select_candidate_windows(
            windows, {(0.0, 5.0)}, 13,
            scan_start=0.0, scan_end=1200.0, coverage_bucket_seconds=300.0,
        )
        starts = [key[0] for key in selected]
        self.assertTrue(any(300 <= value < 600 for value in starts))
        self.assertTrue(any(600 <= value < 900 for value in starts))
        self.assertTrue(any(900 <= value < 1200 for value in starts))

    def test_novel_windows_are_selected_before_repeated_approved_brand(self):
        known_features = {
            "score": 1.0,
            "brand_memory": {
                "memory_class": "brand", "similarity": 0.97,
                "memory_key": "approved-watermark",
            },
        }
        windows = {
            (float(timestamp), float(timestamp + 5)): [{"features": known_features}]
            for timestamp in range(0, 600, 5)
        }
        novel_keys = {(125.0, 130.0), (425.0, 430.0)}
        for key in novel_keys:
            windows[key] = [{"features": {"score": 0.55}}]
        selected = select_candidate_windows(
            windows, {(0.0, 5.0)}, 6,
            scan_start=0.0, scan_end=600.0, coverage_bucket_seconds=300.0,
        )
        self.assertTrue(novel_keys.issubset(set(selected)))
        coverage = candidate_selection_coverage(
            windows, selected, scan_start=0.0, coverage_bucket_seconds=300.0,
        )
        self.assertEqual(coverage["novel_candidate_windows_omitted"], 0)
        self.assertEqual(coverage["approved_brand_time_groups"], 2)
        self.assertEqual(coverage["approved_brand_time_groups_missing"], 0)
        self.assertTrue(coverage["complete"])

    def test_approved_memory_records_at_same_geometry_share_one_track_group(self):
        windows = {}
        for index, timestamp in enumerate((10.0, 60.0, 120.0, 180.0)):
            windows[(timestamp, timestamp + 5.0)] = [{"features": {
                "score": 0.9 + index * 0.01,
                "brand_memory": {
                    "memory_class": "brand", "similarity": 0.97,
                    "memory_key": f"approved-example-{index}",
                    "relative_box": [0.05 + index * 0.001, 0.14, 0.10, 0.06],
                },
            }}]
        selected = select_candidate_windows(
            windows, set(), 1,
            scan_start=0.0, scan_end=300.0, coverage_bucket_seconds=300.0,
        )
        coverage = candidate_selection_coverage(
            windows, selected, scan_start=0.0, coverage_bucket_seconds=300.0,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(coverage["approved_brand_time_groups"], 1)
        self.assertEqual(coverage["approved_brand_time_groups_missing"], 0)
        self.assertTrue(coverage["complete"])

    def test_distinct_approved_memory_geometries_remain_separate_tracks(self):
        def known(memory_key, relative_box):
            return [{"features": {
                "score": 1.0,
                "brand_memory": {
                    "memory_class": "brand", "similarity": 0.97,
                    "memory_key": memory_key, "relative_box": relative_box,
                },
            }}]
        windows = {
            (10.0, 15.0): known("top-left", [0.05, 0.05, 0.10, 0.05]),
            (20.0, 25.0): known("bottom-right", [0.80, 0.85, 0.15, 0.08]),
        }
        selected = select_candidate_windows(
            windows, set(), 1,
            scan_start=0.0, scan_end=300.0, coverage_bucket_seconds=300.0,
        )
        coverage = candidate_selection_coverage(
            windows, selected, scan_start=0.0, coverage_bucket_seconds=300.0,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(coverage["approved_brand_time_groups"], 2)
        self.assertEqual(coverage["approved_brand_time_groups_missing"], 1)
        self.assertFalse(coverage["complete"])

    def test_weak_brand_memory_match_stays_in_novel_regional_coverage(self):
        key = (125.0, 130.0)
        windows = {key: [{"features": {
            "score": 0.8,
            "focus_region": "top_right",
            "full_frame_score": 0.5,
            "regional_score": 0.8,
            "brand_memory": {
                "memory_class": "brand", "similarity": 0.90,
                "memory_key": "weak-example",
                "relative_box": [0.82, 0.04, 0.15, 0.06],
            },
        }}]}
        selected = select_candidate_windows(
            windows, set(), 1,
            scan_start=0.0, scan_end=300.0, coverage_bucket_seconds=300.0,
        )
        coverage = candidate_selection_coverage(
            windows, selected, scan_start=0.0, coverage_bucket_seconds=300.0,
        )
        self.assertEqual(selected, [key])
        self.assertEqual(coverage["approved_brand_candidate_windows"], 0)
        self.assertEqual(coverage["regional_candidate_windows"], 1)
        self.assertTrue(coverage["complete"])

    def test_selection_coverage_exposes_unconfirmed_novel_windows(self):
        windows = {
            (0.0, 5.0): [{"features": {"score": 0.9}}],
            (5.0, 10.0): [{"features": {"score": 0.8}}],
        }
        coverage = candidate_selection_coverage(
            windows, [(0.0, 5.0)],
            scan_start=0.0, coverage_bucket_seconds=300.0,
        )
        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["novel_candidate_windows_omitted"], 1)

    def test_full_frame_noise_is_sampled_but_regional_leads_are_all_checked(self):
        windows = {}
        for timestamp in range(0, 600, 10):
            windows[(float(timestamp), float(timestamp + 5))] = [{
                "features": {
                    "score": 1.0,
                    "focus_region": "full",
                    "full_frame_score": 1.0,
                    "regional_score": 1.0,
                    "ident_score": timestamp / 600,
                    "overlay_score": 1.0 - timestamp / 600,
                }
            }]
        regional_keys = {(125.0, 130.0), (425.0, 430.0)}
        for key in regional_keys:
            windows[key] = [{
                "features": {
                    "score": 0.8,
                    "focus_region": "top_right",
                    "full_frame_score": 0.5,
                    "regional_score": 0.8,
                }
            }]
        selected = select_candidate_windows(
            windows, {(0.0, 5.0)}, 7,
            scan_start=0.0, scan_end=600.0, coverage_bucket_seconds=300.0,
        )
        coverage = candidate_selection_coverage(
            windows, selected, scan_start=0.0, coverage_bucket_seconds=300.0,
        )
        self.assertTrue(regional_keys.issubset(set(selected)))
        self.assertEqual(coverage["regional_candidate_windows_omitted"], 0)
        self.assertEqual(coverage["full_frame_representatives_required"], 4)
        self.assertEqual(coverage["full_frame_representatives_missing"], 0)
        self.assertGreater(coverage["full_frame_windows_collapsed"], 0)
        self.assertTrue(coverage["complete"])

    def test_weak_regional_advantage_remains_full_frame_coverage(self):
        windows = {
            (0.0, 5.0): [{"features": {
                "score": 0.54, "focus_region": "top_right",
                "full_frame_score": 0.52, "regional_score": 0.54,
            }}],
            (5.0, 10.0): [{"features": {
                "score": 0.8, "focus_region": "full",
                "full_frame_score": 0.8, "regional_score": 0.8,
            }}],
        }
        coverage = candidate_selection_coverage(
            windows, [(0.0, 5.0), (5.0, 10.0)],
            scan_start=0.0, coverage_bucket_seconds=300.0,
        )
        self.assertEqual(coverage["regional_candidate_windows"], 0)
        self.assertEqual(coverage["full_frame_candidate_windows"], 2)
        self.assertTrue(coverage["complete"])


if __name__ == "__main__":
    unittest.main()

