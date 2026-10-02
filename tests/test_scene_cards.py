"""Scene cards (R1/R2), opening studio idents (R3a) and studio-logo memory (R3b), 2026-10-01."""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import cv2
import numpy as np

from biliflow import cli
from biliflow.brand_memory import (
    MEMORY_PATH,
    STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE,
    STUDIO_LOGO_MEMORY_PATH,
    STUDIO_LOGO_MINIMUM_SIMILARITY,
    forget_studio_logo,
    hash_similarity,
    load_studio_logo_memory,
    match_studio_logo,
    perceptual_hash,
    remember_studio_logo,
    studio_logo_signatures,
)
from biliflow.review_evidence import item_evidence
from biliflow.review_workflow import (
    SCENE_CARD_MAXIMUM_GAP_SECONDS,
    SCENE_CARD_MAXIMUM_SPAN_SECONDS,
    STUDIO_IDENT_REASON,
    _interactive_html,
    ad_text_tokens,
    application_intervals,
    build_edit_plan,
    build_review_queue,
    clear_review_decision,
    full_frame_logo_ad_evidence,
    group_safety_review_events,
    ocr_text_known,
    record_review_decision,
    route_confirmed_studio_logos,
    studio_logo_memory_allowed,
)

ROOT = Path(__file__).resolve().parents[1]


def _card(category, start, end, **extra):
    item = {
        "id": f"{category}-{start}", "category": category, "start_seconds": start, "end_seconds": end,
        "max_score": 0.9, "priority": "context", "labels": [], "reasons": [], "evidence": [f"{category}.json"],
        "preview_images": [], "source_candidate_refs": [f"{category}.json#interval:{start}"],
        "detected_intervals": [{"start_seconds": start, "end_seconds": end}],
        "suggested_decision": None, "decision": None,
    }
    item.update(extra)
    return item


def _ident_image(variant=0):
    """A static studio ident: dark frame with a bright emblem; variant changes the drawing."""
    image = np.zeros((180, 320, 3), dtype=np.uint8)
    if variant == 0:
        cv2.circle(image, (160, 90), 55, (230, 200, 60), -1)
        cv2.rectangle(image, (120, 80), (200, 100), (20, 20, 20), -1)
    else:
        for x in range(0, 320, 40):
            cv2.rectangle(image, (x, 0), (x + 18, 180), (200, 200, 200), -1)
    return image


def _write_rgb(path, image, quality=95):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    path.write_bytes(encoded.tobytes())


def _flip_bits(phash, count):
    value = int(phash, 16)
    for bit in range(count):
        value ^= 1 << (bit * 7)
    return f"{value:016x}"


class SceneCardGroupingTests(unittest.TestCase):
    def test_violence_moments_closer_than_20s_form_one_fight_card(self):
        cards = group_safety_review_events([
            _card("violence", 100, 130), _card("violence", 145, 160), _card("violence", 179.9, 190),
        ])
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual((card["start_seconds"], card["end_seconds"]), (100, 190))
        self.assertEqual(card["scene_card"]["kind"], "fight")
        self.assertEqual(card["scene_card"]["moment_count"], 3)
        self.assertEqual(card["scene_card"]["detected_seconds"], 55.1)
        self.assertEqual(card["temporal_policy"], "discrete_detected_intervals")
        self.assertEqual([(m["start_seconds"], m["end_seconds"]) for m in card["detected_intervals"]],
                         [(100, 130), (145, 160), (179.9, 190)])
        self.assertEqual(len(card["source_candidate_refs"]), 3)

    def test_gore_forms_blood_cards_and_a_gap_of_exactly_20s_starts_a_new_card(self):
        self.assertEqual(SCENE_CARD_MAXIMUM_GAP_SECONDS, 20.0)
        cards = group_safety_review_events([_card("gore", 10, 20), _card("gore", 40, 45)])
        self.assertEqual(len(cards), 2)
        self.assertFalse(any(card.get("scene_card") for card in cards))
        merged = group_safety_review_events([_card("gore", 10, 20), _card("gore", 39.9, 45)])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["scene_card"]["kind"], "blood")

    def test_a_card_never_spans_more_than_300_seconds(self):
        self.assertEqual(SCENE_CARD_MAXIMUM_SPAN_SECONDS, 300.0)
        items = [_card("violence", start, start + 20) for start in range(0, 1000, 30)]  # gaps of 10 s
        cards = group_safety_review_events(items)
        self.assertGreater(len(cards), 3)
        for card in cards:
            self.assertLessEqual(card["end_seconds"] - card["start_seconds"], 300)
        moments = sorted((m["start_seconds"], m["end_seconds"]) for card in cards
                         for m in application_intervals(card))
        self.assertEqual(moments, [(start, start + 20) for start in range(0, 1000, 30)])

    def test_adult_never_forms_scene_cards(self):
        cards = group_safety_review_events([_card("adult", 10, 12), _card("adult", 25, 27)])
        self.assertEqual(len(cards), 2, "a 13 s gap groups violence/gore, never 18+")
        short = group_safety_review_events([_card("adult", 10, 12), _card("adult", 16, 18)])
        self.assertEqual(len(short), 1)
        self.assertNotIn("scene_card", short[0])
        self.assertEqual(short[0]["candidate_type"], "review_event_group")

    def test_categories_decisions_and_conflicting_suggestions_never_merge(self):
        mixed = group_safety_review_events([_card("violence", 10, 20), _card("gore", 21, 30)])
        self.assertEqual(sorted(card["category"] for card in mixed), ["gore", "violence"])
        decided = group_safety_review_events([_card("violence", 10, 20, decision="KEEP"), _card("violence", 25, 30)])
        self.assertEqual(len(decided), 2)
        conflicting = group_safety_review_events([
            _card("gore", 10, 20, suggested_decision="BLUR"), _card("gore", 25, 30, suggested_decision="KEEP"),
        ])
        self.assertEqual(len(conflicting), 2)

    def test_a_card_suggests_only_what_every_member_suggested(self):
        mixed = group_safety_review_events([
            _card("violence", 10, 20, suggested_decision="CUT"), _card("violence", 25, 30),
        ])
        self.assertEqual(len(mixed), 1)
        self.assertIsNone(mixed[0]["suggested_decision"])  # bulk accept never cuts the unsuggested moment
        self.assertEqual(mixed[0]["scene_card"]["member_suggestions"], ["CUT", None])
        same = group_safety_review_events([
            _card("violence", 10, 20, suggested_decision="CUT"), _card("violence", 25, 30, suggested_decision="CUT"),
        ])
        self.assertEqual(same[0]["suggested_decision"], "CUT")
        # a KEEP-suggested moment never joins a card that already holds a CUT-suggested one
        split = group_safety_review_events([
            _card("violence", 10, 20, suggested_decision="CUT"), _card("violence", 25, 30),
            _card("violence", 35, 40, suggested_decision="KEEP"),
        ])
        self.assertEqual(len(split), 2)
        self.assertEqual(split[1]["suggested_decision"], "KEEP")

    def test_a_discrete_member_keeps_its_own_moments(self):
        preserved = _card("violence", 10, 30, temporal_policy="discrete_detected_intervals", detected_intervals=[
            {"start_seconds": 10, "end_seconds": 14}, {"start_seconds": 25, "end_seconds": 30}])
        card = group_safety_review_events([preserved, _card("violence", 40, 50)])[0]
        self.assertEqual([(m["start_seconds"], m["end_seconds"]) for m in card["detected_intervals"]],
                         [(10, 14), (25, 30), (40, 50)])


class SceneCardQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        for name in ("reports", "work", "input", "state"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")

    def tearDown(self):
        self.temporary.cleanup()

    def _report(self, name, category, intervals, **payload):
        directory = self.root / "reports" / name
        (directory / "thumbs").mkdir(parents=True, exist_ok=True)
        for index, interval in enumerate(intervals):
            (directory / "thumbs" / f"{index}.jpg").write_bytes(b"image")
            interval.setdefault("strongest_frame", f"thumbs/{index}.jpg")
        path = directory / "scan.json"
        path.write_text(json.dumps({
            "status": "COMPLETED", "scan_type": category, "input": str(self.source),
            "input_sha256": "abc", "duration_seconds": 400, "intervals": intervals, **payload,
        }), encoding="utf-8")
        return path

    def test_edit_plan_of_a_scene_card_covers_exactly_its_moments(self):
        report = self._report("violence", "violence", [
            {"start_seconds": 10, "end_seconds": 20, "max_score": 0.8},
            {"start_seconds": 21, "end_seconds": 25, "max_score": 0.8},  # 1 s: one card before scenes
            {"start_seconds": 35, "end_seconds": 45, "max_score": 0.9},  # 10 s gap: same fight
            {"start_seconds": 70, "end_seconds": 80, "max_score": 0.7},  # 25 s gap: next card
        ])
        queue_path = self.root / "reports" / "q" / "queue.json"
        queue = build_review_queue(project_root=self.root, report_paths=[report], queue_path=queue_path)
        self.assertEqual(queue["scene_cards"]["maximum_gap_seconds"], 20.0)
        self.assertEqual(queue["scene_cards"]["cards"], 1)
        scene = next(item for item in queue["items"] if item.get("scene_card"))
        self.assertEqual([(m["start_seconds"], m["end_seconds"]) for m in scene["detected_intervals"]],
                         [(10, 25), (35, 45)])
        single = next(item for item in queue["items"] if not item.get("scene_card"))
        record_review_decision(project_root=self.root, queue_path=queue_path, item_id=scene["id"],
                               decision="BLUR", full_frame=True)
        record_review_decision(project_root=self.root, queue_path=queue_path, item_id=single["id"],
                               decision="CUT")
        plan = build_edit_plan(project_root=self.root, queue_path=queue_path,
                               plan_path=self.root / "work" / "plan.json")
        blur = [(o["start_seconds"], o["end_seconds"]) for o in plan["approved_operations"] if o["type"] == "blur"]
        self.assertEqual(blur, [(10, 25), (35, 45)], "the 25-35 s gap is never blurred")
        cut = [(o["start_seconds"], o["end_seconds"]) for o in plan["approved_operations"] if o["type"] == "cut"]
        self.assertEqual(cut, [(70, 80)])
        self.assertTrue(all(o["review_item_id"] == scene["id"] for o in plan["approved_operations"]
                            if o["type"] == "blur"))

    def test_strip_evidence_of_a_scene_card_stays_inside_its_moments(self):
        report = self._report("gore", "gore", [
            {"start_seconds": 100, "end_seconds": 110, "max_score": 0.9},
            {"start_seconds": 125, "end_seconds": 128, "max_score": 0.9},
        ])
        queue_path = self.root / "reports" / "g" / "queue.json"
        queue = build_review_queue(project_root=self.root, report_paths=[report], queue_path=queue_path)
        card = queue["items"][0]
        self.assertEqual(card["scene_card"]["moment_count"], 2)
        evidence = item_evidence(self.root, queue, card["id"])
        self.assertEqual(evidence["detected_intervals"], [{"start": 100, "end": 110}, {"start": 125, "end": 128}])
        self.assertTrue(evidence["frames"])
        for frame in evidence["frames"]:
            self.assertTrue(100 <= frame["t"] <= 110 or 125 <= frame["t"] <= 128, frame)

    # ------------------------------------------------------------------ R3a / R3b

    def _ident_report(self, name, image, *, text_tracks=None, proposals=None, start=5.0, end=10.0,
                      duration=400, extra_interval=None):
        directory = self.root / "reports" / name
        (directory / "thumbs").mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        self.assertTrue(ok)
        (directory / "thumbs" / "ident.jpg").write_bytes(encoded.tobytes())
        interval = {
            "start_seconds": start, "end_seconds": end, "max_score": 1.0, "strongest_frame": "thumbs/ident.jpg",
            "predicted_label": "Visual brand/logo candidate",
            "visual_logo_confirmation": {
                "state": "CONFIRMED", "confirmation_source": "qwen_local", "boundary_window": True,
                "features": {"full_frame_score": 0.9},
                "boundary_scene_context": {"state": "UNCERTAIN"},
            },
            "region_localization": {"frame_size": [1920, 1080], "proposals": proposals or []},
        }
        intervals = [interval] + ([extra_interval] if extra_interval else [])
        logo = directory / "scan-localized.json"
        logo.write_text(json.dumps({
            "status": "COMPLETED", "scan_type": "visual_logo", "input": str(self.source), "input_sha256": name,
            "duration_seconds": duration, "intervals": intervals, "source_size": [1920, 1080],
        }), encoding="utf-8")
        reports = [logo]
        if text_tracks is not None:
            text = directory / "text-scan.json"
            text.write_text(json.dumps({
                "status": "COMPLETED", "input": str(self.source), "input_sha256": name,
                "duration_seconds": duration, "scan_start_seconds": 0.0, "scan_duration_seconds": duration,
                "source_size": [1920, 1080], "analysis_size": [960, 540], "tracks": text_tracks,
            }), encoding="utf-8")
            reports.append(text)
        return reports

    @staticmethod
    def _track(start, end, texts, **extra):
        track = {"start_seconds": start, "end_seconds": end, "sample_text": texts, "routing": "LIKELY_SCENE_TEXT",
                 "review_candidate": False, "persistent": False, "candidate_type": None, "policy_hits": [],
                 "semantic_top_label": "credits", "union_box": [300, 200, 600, 260]}
        track.update(extra)
        return track

    def _build(self, reports, name="q", **kwargs):
        return build_review_queue(project_root=self.root, report_paths=reports,
                                  queue_path=self.root / "reports" / name / "queue.json", **kwargs)

    def _ident(self, queue, key="items"):
        return [item for item in queue[key] if item.get("candidate_type") == "opening_promotion"]

    def test_opening_studio_ident_without_ad_text_has_no_preselected_cut_and_stays_main(self):
        reports = self._ident_report("film-a", _ident_image(), text_tracks=[
            self._track(6, 9, ["WARNER BROS", "PICTURES"]), self._track(8, 10, ["A TimeWarner Company"])])
        queue = self._build(reports)
        [ident] = self._ident(queue)
        self.assertIsNone(ident["suggested_decision"])
        self.assertEqual(ident["suggestion_withheld"]["previous_suggested_decision"], "CUT")
        self.assertTrue(ident["opening_ident"])
        self.assertIsNone(ident.get("advisory"))
        self.assertEqual(self._ident(queue, "advisory_items"), [])
        self.assertEqual(ident["suggestion_withheld"]["window_texts"],
                         ["WARNER BROS", "PICTURES", "A TimeWarner Company"])
        self.assertIn("Chữ đọc được: WARNER BROS, PICTURES, A TimeWarner Company", " ".join(ident["reasons"]))

    def test_an_opening_ident_never_claims_no_ad_text_when_ocr_read_a_word(self):
        # Reviewer finding 2026-10-01: a bare site name ("PHIMMOI", "KUBET") is no URL/phone token and is not
        # ad-routed, so the Cut suggestion is withheld; the card then claimed no ad text had been seen.
        for index, track in enumerate((
            self._track(9, 12, ["PHIMMOI"], routing="LOW_AD_UNCERTAIN", semantic_top_label="scene_text"),
            self._track(9, 12, ["KUBET"]),
        )):
            with self.subTest(text=track["sample_text"]):
                queue = self._build(self._ident_report(f"site-{index}", _ident_image(), text_tracks=[track],
                                                       start=5.0, end=15.0), name=f"site-{index}")
                [ident] = self._ident(queue)
                self.assertIsNone(ident.get("advisory"), "the card stays in the main list")
                self.assertEqual(self._ident(queue, "advisory_items"), [])
                self.assertEqual(ident["suggestion_withheld"]["window_texts"], track["sample_text"])
                reasons = " ".join(ident["reasons"])
                self.assertNotIn("không thấy website, số điện thoại hay chữ quảng cáo", reasons)
                self.assertNotIn("không đọc thấy chữ", reasons)
                self.assertIn(f"Chữ đọc được: {track['sample_text'][0]} — nếu là tên web/thương hiệu lạ hãy Cắt",
                              reasons)
        # Only a window where OCR read nothing at all says so.
        queue = self._build(self._ident_report("silent", _ident_image(), text_tracks=[]), name="silent")
        [ident] = self._ident(queue)
        self.assertEqual(ident["suggestion_withheld"]["window_texts"], [])
        self.assertIn(STUDIO_IDENT_REASON, ident["reasons"])
        self.assertIn("không đọc thấy chữ nào", STUDIO_IDENT_REASON)

    def test_website_phone_handle_or_ad_routed_text_keeps_the_cut_suggestion(self):
        for index, track in enumerate((
            self._track(6, 9, ["xembz.net"]), self._track(6, 9, ["Hotline 0912 345 678"]),
            self._track(6, 9, ["follow @phimhay"]), self._track(6, 9, ["PhimOnline net"]),
            self._track(6, 9, ["MUA NGAY"], routing="REVIEW_AD_LIKELY"),
            self._track(6, 9, ["Netflix"], policy_hits=["Netflix"]),
        )):
            with self.subTest(track=track["sample_text"]):
                queue = self._build(self._ident_report(f"ad-{index}", _ident_image(), text_tracks=[track]),
                                    name=f"ad-{index}")
                [ident] = self._ident(queue)
                self.assertEqual(ident["suggested_decision"], "CUT")
                self.assertNotIn("suggestion_withheld", ident)

    def test_without_a_text_scan_the_cut_suggestion_is_kept(self):
        queue = self._build(self._ident_report("no-text", _ident_image(), text_tracks=None))
        self.assertEqual(self._ident(queue)[0]["suggested_decision"], "CUT")

    def test_ad_tokens(self):
        self.assertEqual(ad_text_tokens("WARNER BROS PICTURES"), [])
        self.assertEqual(ad_text_tokens("A TimeWarner Company"), [])
        self.assertTrue(ad_text_tokens("www.example.vn"))
        self.assertTrue(ad_text_tokens("XEMBZ NET"))
        self.assertTrue(ad_text_tokens("0912.345.678"))
        self.assertEqual(ad_text_tokens("52"), [])

    def test_persistent_watermark_is_not_ad_evidence_but_a_brand_region_elsewhere_is(self):
        watermark = {"x": 118, "y": 173, "width": 150, "height": 31}
        persistent = _card("visual_logo", 0, 400, candidate_type="persistent_overlay",
                           suggested_region_source_pixels=watermark)
        payloads = {
            "text.json": {"tracks": [self._track(0, 400, ["XEMBZ NET"], persistent=True,
                                                 routing="REVIEW_PERSISTENT_OVERLAY")],
                          "scan_start_seconds": 0, "scan_duration_seconds": 400},
            "logo.json": {"intervals": [{"region_localization": {"frame_size": [1920, 1080], "proposals": [
                {"blur_region_px": [107, 160, 172, 53], "region_classification": "external_brand_candidate",
                 "suggested_decision": "BLUR", "labels": ["</s>XEMBZ.NET"]}]}}]},
        }
        ident = _card("visual_logo", 5, 15, candidate_type="opening_promotion", suggested_decision="CUT",
                      source_candidate_refs=["logo.json#interval:0"], source_frame_size=[1920, 1080])
        self.assertEqual(full_frame_logo_ad_evidence(ident, [persistent, ident], payloads), [])
        payloads["logo.json"]["intervals"][0]["region_localization"]["proposals"].append(
            {"blur_region_px": [1500, 900, 300, 120], "region_classification": "external_brand", "labels": ["SHOP"]})
        self.assertEqual(full_frame_logo_ad_evidence(ident, [persistent, ident], payloads),
                         ["brand_region:external_brand"])
        self.assertIsNone(full_frame_logo_ad_evidence(ident, [persistent, ident], {"logo.json": {"intervals": []}}))

    def test_confirmed_studio_logo_is_remembered_separately_and_routes_later_cards_to_candidates(self):
        tracks = [self._track(6, 9, ["TOHO"])]
        queue_a = self._build(self._ident_report("film-a", _ident_image(), text_tracks=tracks), name="a")
        [ident_a] = self._ident(queue_a)
        path_a = self.root / "reports" / "a" / "queue.json"
        updated = record_review_decision(project_root=self.root, queue_path=path_a, item_id=ident_a["id"],
                                         decision="KEEP", remember_studio_logo=True)
        item = next(i for i in updated["items"] if i["id"] == ident_a["id"])
        self.assertEqual(item["decision"], "KEEP")
        self.assertTrue(item["studio_logo_memory"]["remembered"])
        self.assertTrue(updated["audit_log"][-1]["remember_studio_logo"])
        memory = load_studio_logo_memory(self.root)
        self.assertEqual(len(memory["records"]), 1)
        self.assertEqual(memory["records"][0]["memory_class"], "studio_logo")
        brand = json.loads((self.root / MEMORY_PATH).read_text(encoding="utf-8"))
        self.assertEqual(brand["records"], [], "studio logos never enter the brand blur memory")

        self.assertEqual(memory["records"][0]["window_text"], {"covered": True, "texts": ["TOHO"]})
        self.assertEqual(item["studio_logo_memory"]["window_texts"], ["TOHO"])

        # Another film opens with the same static ident: optional list, with the reason.
        queue_b = self._build(self._ident_report("film-b", _ident_image(), text_tracks=tracks), name="b",
                              use_studio_logo_memory=True)
        self.assertEqual(self._ident(queue_b), [])
        [moved] = self._ident(queue_b, "advisory_items")
        self.assertTrue(moved["advisory"])
        self.assertGreaterEqual(moved["studio_logo_match"]["similarity"], STUDIO_LOGO_MINIMUM_SIMILARITY)
        self.assertIn("Khớp logo hãng phim bạn đã xác nhận", " ".join(moved["reasons"]))
        self.assertEqual(queue_b["studio_logo_memory"]["moved_to_candidates"], 1)

        # A different ident stays required; so does the same ident next to a website.
        queue_c = self._build(self._ident_report("film-c", _ident_image(1), text_tracks=tracks), name="c",
                              use_studio_logo_memory=True)
        self.assertEqual(len(self._ident(queue_c)), 1)
        queue_d = self._build(self._ident_report("film-d", _ident_image(), text_tracks=[
            self._track(6, 9, ["phimhay.tv"])]), name="d", use_studio_logo_memory=True)
        self.assertEqual(len(self._ident(queue_d)), 1)
        self.assertEqual(self._ident(queue_d)[0]["suggested_decision"], "CUT")
        # Measurements ignore the memory: it is off unless a caller opts in.
        ignored = self._build(self._ident_report("film-e", _ident_image(), text_tracks=tracks), name="e")
        self.assertEqual(len(self._ident(ignored)), 1)
        self.assertFalse(ignored["studio_logo_memory"]["used"])

        # Any other decision, or clearing it, forgets the studio logo again.
        record_review_decision(project_root=self.root, queue_path=path_a, item_id=ident_a["id"], decision="CUT")
        self.assertEqual(load_studio_logo_memory(self.root)["records"], [])
        record_review_decision(project_root=self.root, queue_path=path_a, item_id=ident_a["id"],
                               decision="KEEP", remember_studio_logo=True)
        self.assertEqual(len(load_studio_logo_memory(self.root)["records"]), 1)
        cleared = clear_review_decision(project_root=self.root, queue_path=path_a, item_id=ident_a["id"])
        self.assertNotIn("studio_logo_memory", next(i for i in cleared["items"] if i["id"] == ident_a["id"]))
        self.assertEqual(load_studio_logo_memory(self.root)["records"], [])

    def test_no_confirmed_logo_means_no_effect_and_no_memory_file(self):
        tracks = [self._track(6, 9, ["TOHO"])]
        reports = self._ident_report("film-a", _ident_image(), text_tracks=tracks)
        with_memory = self._build(reports, name="m1", use_studio_logo_memory=True)
        without = self._build(reports, name="m2")
        strip = lambda queue: [{k: v for k, v in item.items()} for item in queue["items"]]
        self.assertEqual(strip(with_memory), strip(without))
        self.assertEqual(with_memory["advisory_items"], without["advisory_items"])
        self.assertEqual(with_memory["studio_logo_memory"]["confirmed_logos"], 0)
        self.assertFalse((self.root / STUDIO_LOGO_MEMORY_PATH).exists())

    def _confirm_studio_logo(self, name, tracks):
        queue = self._build(self._ident_report(name, _ident_image(), text_tracks=tracks), name=name)
        [ident] = self._ident(queue)
        path = self.root / "reports" / name / "queue.json"
        record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                               decision="KEEP", remember_studio_logo=True)
        return path, ident

    def test_text_the_confirmed_ident_did_not_show_keeps_a_matching_card_required(self):
        # Reviewer finding 2026-10-01: a site name with no URL token ("PHIMMOI", "BZNET") overlaid on a
        # remembered ident, routed LOW_AD_UNCERTAIN or as scene text, used to send the card to the optional list.
        self._confirm_studio_logo("film-a", [self._track(6, 9, ["WARNER BROS", "PICTURES"])])
        blocked = {
            "low-ad site name": ([self._track(6, 9, ["WARNER BROS"]),
                                  self._track(6, 9, ["PHIMMOI"], routing="LOW_AD_UNCERTAIN",
                                              semantic_top_label="scene_text")], ["PHIMMOI"]),
            "scene-text site name": ([self._track(7, 8, ["BZNET"])], ["BZNET"]),
            "site name on the ident line": ([self._track(6, 9, ["WARNER BROS PHIMMOI"])], ["WARNER BROS PHIMMOI"]),
        }
        for index, (label, (tracks, unknown)) in enumerate(blocked.items()):
            with self.subTest(label):
                queue = self._build(self._ident_report(f"blocked-{index}", _ident_image(), text_tracks=tracks),
                                    name=f"blocked-{index}", use_studio_logo_memory=True)
                [ident] = self._ident(queue)
                self.assertIsNone(ident.get("advisory"))
                self.assertEqual(ident["studio_logo_match_blocked"]["unconfirmed_texts"], unknown)
                self.assertEqual(self._ident(queue, "advisory_items"), [])
                self.assertEqual(queue["studio_logo_memory"]["moved_to_candidates"], 0)
                self.assertEqual(queue["studio_logo_memory"]["kept_required_after_picture_match"], 1)
        # OCR noise on a remembered line, a fragment of it, text outside the window or no text still move.
        moved = {
            "ocr noise": [self._track(6, 9, ["WARNR BROS", "PICIURES"])],
            "fragment": [self._track(6, 9, ["BROS"])],
            "outside the window": [self._track(30, 33, ["PHIMMOI"])],
            "no text": [],
        }
        for index, (label, tracks) in enumerate(moved.items()):
            with self.subTest(label):
                queue = self._build(self._ident_report(f"moved-{index}", _ident_image(), text_tracks=tracks),
                                    name=f"moved-{index}", use_studio_logo_memory=True)
                self.assertEqual(self._ident(queue), [])
                [ident] = self._ident(queue, "advisory_items")
                self.assertEqual(ident["studio_logo_match"]["known_texts"], ["WARNER BROS", "PICTURES"])
        # Without a text scan nothing is relaxed.
        queue = self._build(self._ident_report("no-scan", _ident_image(), text_tracks=None), name="no-scan",
                            use_studio_logo_memory=True)
        [ident] = self._ident(queue)
        self.assertFalse(ident["studio_logo_match_blocked"]["text_scan_covered"])

    def test_ocr_text_known_tolerates_noise_but_nothing_added(self):
        known = ["WARNER BROS", "PICTURES", "A TimeWarner Company", "52"]
        for text in ("WARNER BROS", "WARNR BROS", "PICIURES'", "BROS:", "PICTU R E $", "52", "Warner", "-", "I"):
            with self.subTest(text=text):
                self.assertTrue(ocr_text_known(text, known))
        for text in ("PHIMMOI", "BZNET", "Nenet", "N2Net", "WNET", "NCnet", "WARNER BROS PHIMMOI", "53", "KUBET"):
            with self.subTest(text=text):
                self.assertFalse(ocr_text_known(text, known))
        self.assertFalse(ocr_text_known("TOHO", []))

    def test_a_word_joined_onto_a_remembered_line_is_not_known(self):
        # Reviewer finding 2026-10-01: the symmetric 0.80 ratio let a long remembered line absorb ~20 % extra
        # characters. The real Troy memory (reports/benchmarks/review-load-fix-20261001-215846/r3b-memory-root).
        known = ["WARNER", "WARNR", "PICIURES'", "PICTURES", "BROS:", "BROS", "A TimeWarner Company"]
        for text in ("A TimeWarner Company PHIMMOI", "A TimeWarner Company KUBET", "A TimeWarner Company BZNET",
                     "WARNERBET", "BROS88", "PICTURES BET", "WARNER VN"):
            with self.subTest(text=text):
                self.assertFalse(ocr_text_known(text, known))
        # The remembered readings and one-character misreads of them still repeat the ident.
        for text in (*known, "A TimeWarner Compary", "A TimeWamer Company", "ATimeWarnerCompany", "WARNER.",
                     "PICTURE5", "Time Warner"):
            with self.subTest(text=text):
                self.assertTrue(ocr_text_known(text, known))

    def test_a_site_name_joined_onto_the_remembered_tagline_keeps_the_card_required(self):
        self._confirm_studio_logo("film-a", [self._track(6, 9, ["WARNER", "WARNR"]), self._track(6, 9, ["BROS:", "BROS"]),
                                             self._track(8, 10, ["A TimeWarner Company"])])
        joined = [self._track(6, 9, ["WARNER", "WARNR"]), self._track(6, 9, ["BROS:", "BROS"]),
                  self._track(8, 10, ["A TimeWarner Company PHIMMOI"])]
        queue = self._build(self._ident_report("joined", _ident_image(), text_tracks=joined), name="joined",
                            use_studio_logo_memory=True)
        [ident] = self._ident(queue)
        self.assertIsNone(ident.get("advisory"))
        self.assertEqual(ident["studio_logo_match_blocked"]["unconfirmed_texts"], ["A TimeWarner Company PHIMMOI"])
        self.assertEqual(queue["studio_logo_memory"]["moved_to_candidates"], 0)
        repeat = [self._track(6, 9, ["WARNER"]), self._track(6, 9, ["BROS"]),
                  self._track(8, 10, ["A TimeWarner Compary"])]
        queue = self._build(self._ident_report("repeat", _ident_image(), text_tracks=repeat), name="repeat",
                            use_studio_logo_memory=True)
        self.assertEqual(self._ident(queue), [])
        self.assertEqual(len(self._ident(queue, "advisory_items")), 1)

    def test_an_unreadable_studio_memory_never_fails_an_ordinary_decision(self):
        # Reviewer finding 2026-10-01: forget_studio_logo raised after the queue was written.
        queue = self._build(self._ident_report("film-a", _ident_image(), text_tracks=[self._track(6, 9, ["TOHO"])]))
        [ident] = self._ident(queue)
        path = self.root / "reports" / "q" / "queue.json"
        memory = self.root / STUDIO_LOGO_MEMORY_PATH
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_text("{", encoding="utf-8")
        updated = record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                                         decision="KEEP")
        self.assertEqual(next(i for i in updated["items"] if i["id"] == ident["id"])["decision"], "KEEP")
        self.assertIn("KEEP", path.with_suffix(".html").read_text(encoding="utf-8"), "the page was re-rendered")
        cleared = clear_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"])
        self.assertIsNone(next(i for i in cleared["items"] if i["id"] == ident["id"])["decision"])
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "bộ nhớ logo hãng phim"):
            record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                                   decision="KEEP", remember_studio_logo=True)
        self.assertEqual(path.read_bytes(), before, "a refused studio confirmation writes nothing")
        self.assertEqual(memory.read_text(encoding="utf-8"), "{")
        rebuilt = self._build(self._ident_report("film-b", _ident_image(), text_tracks=[self._track(6, 9, ["TOHO"])]),
                              name="b", use_studio_logo_memory=True)
        self.assertEqual(len(self._ident(rebuilt)), 1)
        self.assertTrue(rebuilt["studio_logo_memory"]["error"])

    def test_only_the_production_build_reads_the_studio_memory(self):
        # Reviewer finding 2026-10-01: benchmarks and Golden runs must not depend on state/studio-logo-memory.json.
        job_queue = self.root / "reports" / "jobs" / "job-1" / "review-queue.json"
        self.assertTrue(studio_logo_memory_allowed(job_queue, {}))
        self.assertFalse(studio_logo_memory_allowed(job_queue, {"BILIFLOW_BENCHMARK_CACHE": "E:/cache"}))
        trial = self.root / "reports" / "jobs" / "trial-1"
        trial.mkdir(parents=True)
        (trial / ".biliflow-benchmark").write_text("Isolated trial", encoding="utf-8")
        self.assertFalse(studio_logo_memory_allowed(trial / "review-queue.json", {}))
        reports = self._ident_report("film-a", _ident_image(), text_tracks=[self._track(6, 9, ["TOHO"])])
        seen = []

        def fake_build(**kwargs):
            seen.append(kwargs["use_studio_logo_memory"])
            return {}

        cases = (([], job_queue, {}), (["--no-studio-logo-memory"], job_queue, {}),
                 ([], job_queue, {"BILIFLOW_BENCHMARK_CACHE": "E:/cache"}), ([], trial / "review-queue.json", {}))
        for extra, queue_path, environment in cases:
            argv = ["biliflow", "--project-root", str(self.root), "build-review", "--report", str(reports[0]),
                    "--queue", str(queue_path), *extra]
            clean = {key: value for key, value in os.environ.items() if key != "BILIFLOW_BENCHMARK_CACHE"}
            with mock.patch.object(sys, "argv", argv), mock.patch.object(cli, "build_review_queue", fake_build), \
                    mock.patch.dict(os.environ, {**clean, **environment}, clear=True), \
                    mock.patch("builtins.print"):
                self.assertEqual(cli.main(), 0)
        self.assertEqual(seen, [True, False, False, False])

    def test_studio_logo_is_only_remembered_for_keep_on_a_full_frame_logo_card(self):
        reports = self._ident_report("film-a", _ident_image(), text_tracks=[self._track(6, 9, ["TOHO"])])
        queue = self._build(reports, name="r")
        path = self.root / "reports" / "r" / "queue.json"
        [ident] = self._ident(queue)
        with self.assertRaisesRegex(ValueError, "Giữ nguyên"):
            record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                                   decision="CUT", remember_studio_logo=True)
        violence = self._report("v", "violence", [{"start_seconds": 50, "end_seconds": 60, "max_score": 0.9}])
        queue_v = self._build([violence], name="v")
        with self.assertRaisesRegex(ValueError, "toàn khung hình"):
            record_review_decision(project_root=self.root, queue_path=self.root / "reports" / "v" / "queue.json",
                                   item_id=queue_v["items"][0]["id"], decision="KEEP", remember_studio_logo=True)
        self.assertFalse((self.root / STUDIO_LOGO_MEMORY_PATH).exists())


class StudioLogoMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "reports").mkdir()
        self.images = {}
        for name, variant in (("a.jpg", 0), ("b.jpg", 1)):
            ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(_ident_image(variant), cv2.COLOR_RGB2BGR))
            (self.root / "reports" / name).write_bytes(encoded.tobytes())
            self.images[name] = perceptual_hash(_ident_image(variant))
        self.queue = {"source": {"sha256": "film"}}

    def tearDown(self):
        self.temporary.cleanup()

    def item(self, *previews, **extra):
        value = {"id": "logo-1", "category": "visual_logo", "candidate_type": "opening_promotion",
                 "decision": "KEEP", "preview_images": [f"reports/{name}" for name in previews],
                 "suggested_region_source_pixels": None, "labels": ["logo"]}
        value.update(extra)
        return value

    def test_threshold_is_strict_at_0_95(self):
        record = remember_studio_logo(self.root, self.queue, self.item("a.jpg"))
        stored = record["signatures"][0]["phash"]
        for flipped, expected in ((0, True), (3, True), (4, False)):  # 61/64 = 0.953, 60/64 = 0.9375
            with self.subTest(flipped=flipped):
                records = [dict(record, signatures=[{"phash": _flip_bits(stored, flipped),
                                                     "grid": record["signatures"][0]["grid"]}])]
                match = match_studio_logo(self.root, self.item("a.jpg", decision=None), records)
                self.assertEqual(match is not None, expected)

    def test_every_preview_must_match_and_regions_or_watermarks_never_match(self):
        record = remember_studio_logo(self.root, self.queue, self.item("a.jpg"))
        records = [record]
        self.assertIsNotNone(match_studio_logo(self.root, self.item("a.jpg", decision=None), records))
        self.assertIsNone(match_studio_logo(self.root, self.item("a.jpg", "b.jpg", decision=None), records))
        self.assertIsNone(match_studio_logo(self.root, self.item(
            "a.jpg", decision=None, suggested_region_source_pixels={"x": 1, "y": 1, "width": 9, "height": 9}), records))
        self.assertIsNone(match_studio_logo(self.root, self.item(
            "a.jpg", decision=None, candidate_type="persistent_overlay"), records))
        self.assertIsNone(match_studio_logo(self.root, self.item(decision=None), records))

    def test_a_local_overlay_breaks_the_match_even_with_an_identical_phash(self):
        # Reviewer finding 2026-10-01: the 64-bit pHash barely notices a small overlay (a corner URL on the
        # real Toho ident scores 1.000). Worst case: the stored pHash equals the overlaid frame's own pHash.
        record = remember_studio_logo(self.root, self.queue, self.item("a.jpg"))
        clean_grid = record["signatures"][0]["grid"]
        overlays = {"corner text": _ident_image(), "bottom banner": _ident_image(), "corner box": _ident_image()}
        cv2.putText(overlays["corner text"], "PHIMMOI", (176, 171), cv2.FONT_HERSHEY_SIMPLEX, 0.36,
                    (255, 255, 255), 1, cv2.LINE_AA)
        overlays["bottom banner"][171:, :] = (40, 40, 40)
        overlays["corner box"][150:171, 250:310] = (230, 30, 30)
        for name, image in overlays.items():
            with self.subTest(overlay=name):
                path = f"{name.replace(' ', '-')}.jpg"
                _write_rgb(self.root / "reports" / path, image)
                [signature] = studio_logo_signatures(self.root, self.item(path))
                forged = dict(record, signatures=[{"phash": signature["phash"], "grid": clean_grid}])
                self.assertIsNone(match_studio_logo(self.root, self.item(path, decision=None), [forged]))
        # A plain re-encode of the remembered frame still matches.
        _write_rgb(self.root / "reports" / "a-q75.jpg", _ident_image(), quality=75)
        match = match_studio_logo(self.root, self.item("a-q75.jpg", decision=None), [record])
        self.assertIsNotNone(match)
        self.assertLessEqual(match["cell_difference"], STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE)

    def test_every_preview_is_read_and_none_is_skipped(self):
        # Reviewer finding 2026-10-01: unreadable previews and previews after the 8th were ignored.
        record = remember_studio_logo(self.root, self.queue, self.item("a.jpg"))
        self.assertIsNone(match_studio_logo(self.root, self.item("a.jpg", "missing.jpg", decision=None), [record]))
        nine = match_studio_logo(self.root, self.item(*["a.jpg"] * 9, decision=None), [record])
        self.assertEqual(nine["matched_frames"], 9)
        self.assertIsNone(match_studio_logo(self.root, self.item(*(["a.jpg"] * 8 + ["b.jpg"]), decision=None),
                                            [record]))
        self.assertIsNone(match_studio_logo(
            self.root, self.item("a.jpg", decision=None),
            [dict(record, signatures=[{"phash": record["signatures"][0]["phash"]}])]),
            "a stored frame without a colour grid never matches")

    def test_memory_file_is_separate_and_forget_removes_the_record(self):
        self.assertIsNone(remember_studio_logo(self.root, self.queue, self.item("a.jpg", decision="CUT")))
        record = remember_studio_logo(self.root, self.queue, self.item("a.jpg"))
        self.assertEqual(record["key"], "film:logo-1")
        self.assertTrue((self.root / STUDIO_LOGO_MEMORY_PATH).is_file())
        self.assertFalse((self.root / MEMORY_PATH).exists())
        payload = load_studio_logo_memory(self.root)
        self.assertFalse(payload["safety"]["automatic_edit"])
        self.assertTrue(forget_studio_logo(self.root, self.queue, "logo-1"))
        self.assertFalse(forget_studio_logo(self.root, self.queue, "logo-1"))
        self.assertEqual(load_studio_logo_memory(self.root)["records"], [])


_REAL_IDENTS = {
    "toho": ROOT / "reports/jobs/conan20-allgroups-full-fast-20260930-073627/visual-logo/thumbnails/logo-0002-7.750s.jpg",
    "wb": ROOT / "reports/jobs/troy-allgroups-full-fast-20260930-162534/visual-logo/thumbnails/logo-0002-9.250s.jpg",
}
_TOHO_CONAN21 = ROOT / "reports/jobs/conan21-allgroups-full-golden-20260930-222720/visual-logo/thumbnails/logo-0002-7.750s.jpg"


@unittest.skipUnless(all(path.is_file() for path in _REAL_IDENTS.values()), "Golden ident previews not present")
class RealIdentOverlayTests(unittest.TestCase):
    """Reviewer reproducer 2026-10-01 (r3b_adv.py) on the real Toho / Warner Bros. ident previews (read-only)."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.folder = self.root / "reports" / "x"
        self.folder.mkdir(parents=True)
        frames = {key: cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR) for key, path in _REAL_IDENTS.items()}
        h, w = frames["toho"].shape[:2]
        corner = frames["toho"].copy()
        cv2.putText(corner, "PHIMMOI", (int(w * .55), int(h * .95)), cv2.FONT_HERSHEY_SIMPLEX, w / 900,
                    (255, 255, 255), max(1, w // 320), cv2.LINE_AA)
        url = frames["toho"].copy()
        cv2.putText(url, "www.bet88.vip", (int(w * .55), int(h * .95)), cv2.FONT_HERSHEY_SIMPLEX, w / 900,
                    (255, 255, 255), max(1, w // 320), cv2.LINE_AA)
        h2, w2 = frames["wb"].shape[:2]
        banner = frames["wb"].copy()
        banner[int(h2 * .90):, :] = (0, 0, 200)  # a solid graphic banner: pHash 0.969, no text to read
        for name, image in {**frames, "corner": corner, "url": url, "banner": banner}.items():
            cv2.imwrite(str(self.folder / f"{name}.jpg"), image)
        if _TOHO_CONAN21.is_file():
            shutil.copyfile(_TOHO_CONAN21, self.folder / "toho21.jpg")
        self.records = [
            remember_studio_logo(self.root, {"source": {"sha256": key}}, self.item(key, decision="KEEP"),
                                 window_text={"covered": True, "texts": []})
            for key in ("toho", "wb")
        ]

    def tearDown(self):
        self.temporary.cleanup()

    def item(self, name, decision=None):
        return {"id": f"review-{name}", "category": "visual_logo", "candidate_type": "opening_promotion",
                "review_kind": "opening_promotion", "start_seconds": 5.0, "end_seconds": 10.0,
                "suggested_decision": "CUT", "decision": decision, "priority": "high", "labels": [], "reasons": [],
                "preview_images": [f"reports/x/{name}.jpg"], "source_candidate_refs": ["reports/x/logo.json#interval:0"]}

    def phash(self, name):
        return perceptual_hash(cv2.cvtColor(cv2.imread(str(self.folder / f"{name}.jpg")), cv2.COLOR_BGR2RGB))

    def test_overlays_pass_the_phash_but_never_the_studio_memory(self):
        for name, clean in (("corner", "toho"), ("url", "toho"), ("banner", "wb")):
            with self.subTest(overlay=name):
                self.assertGreaterEqual(hash_similarity(self.phash(name), self.phash(clean)), 0.95)
                self.assertIsNone(match_studio_logo(self.root, self.item(name), self.records))
        for name in ("toho", "wb", "toho21"):
            if (self.folder / f"{name}.jpg").is_file():
                with self.subTest(repeat=name):
                    self.assertIsNotNone(match_studio_logo(self.root, self.item(name), self.records))

    def test_route_keeps_overlaid_or_captioned_idents_in_the_main_list(self):
        logo = {"intervals": [{"start_seconds": 5.0, "end_seconds": 10.0, "region_localization": {"proposals": [
            {"region_classification": "unknown", "suggested_decision": None, "labels": ["a company logo"],
             "blur_region_px": [10, 300, 600, 60]}]}}]}
        for routing in ("LOW_AD_UNCERTAIN", "LIKELY_SCENE_TEXT"):
            text = {"tracks": [{"start_seconds": 6.0, "end_seconds": 9.0, "persistent": False, "routing": routing,
                                "semantic_top_label": "scene_text", "sample_text": ["PHIMMOI"], "policy_hits": []}],
                    "scan_start_seconds": 0.0, "scan_duration_seconds": 100.0, "duration_seconds": 100.0}
            payloads = {"reports/x/logo.json": logo, "reports/x/text-scan.json": text}
            with self.subTest(routing=routing):
                items = [self.item("corner"), self.item("banner"), self.item("toho")]
                kept, moved = route_confirmed_studio_logos(self.root, items, payloads, {"records": self.records})
                self.assertEqual([item["id"] for item in kept], ["review-corner", "review-banner", "review-toho"])
                self.assertEqual(moved, [])
                self.assertEqual(kept[2]["studio_logo_match_blocked"]["unconfirmed_texts"], ["PHIMMOI"])
        quiet = {"tracks": [], "scan_start_seconds": 0.0, "scan_duration_seconds": 100.0}
        kept, moved = route_confirmed_studio_logos(
            self.root, [self.item("toho")], {"reports/x/logo.json": logo, "reports/x/text-scan.json": quiet},
            {"records": self.records})
        self.assertEqual((kept, [item["id"] for item in moved]), ([], ["review-toho"]))


def _js_function(page, name):
    match = re.search(rf"(?:async )?function {re.escape(name)}\(", page)
    start = page.index("{", match.end())
    depth = 0
    for index in range(start, len(page)):
        depth += {"{": 1, "}": -1}.get(page[index], 0)
        if depth == 0:
            return page[match.start():index + 1]
    raise AssertionError(name)


class SceneCardPageTests(unittest.TestCase):
    def setUp(self):
        self.page = _interactive_html("token")

    def test_scene_card_strings(self):
        for fragment in (
            "Trận đánh", "Cảnh máu", "khoảnh khắc", "tổng ${mmss(momentTotal(momentsOf(x)))}",
            "(trải dài ${mmss(x.start_seconds)}–${mmss(x.end_seconds)})", "▶ Phát lần lượt ${n} khoảnh khắc",
            'data-act="seq"', 'data-act="moment"', "Quyết định chỉ áp dụng cho ${ms.length} khoảnh khắc này",
            "khoảng trống giữa chúng giữ nguyên", "Đây là logo hãng phim — giữ &amp; nhớ",
            "Không đề xuất sẵn: đoạn mở đầu giống logo hãng phim", "Logo mở đầu",
            "Khớp logo hãng phim bạn đã xác nhận giữ",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.page)
        self.assertEqual(_js_function(self.page, "sceneHeader").count("${sceneTitle(x)} · ${sceneSpan(x)}"), 1)

    def test_sequence_playback_skips_gaps_and_studio_action_posts_the_flag(self):
        guard = _js_function(self.page, "momentGuard")
        self.assertIn("playMoment(next,true)", guard)
        self.assertIn("video.pause()", guard)
        self.assertIn("if(k<0)", guard)
        self.assertIn("momentGuard()", _js_function(self.page, "watchMoments"))
        self.assertIn("seekTo(ms[i].start,true)", _js_function(self.page, "playMoment"))
        self.assertIn("momentIndex(f.t,ms)>=0", _js_function(self.page, "pickSceneStrip"))
        decide = _js_function(self.page, "decide")
        self.assertIn("remember_studio_logo:true", decide)
        self.assertIn("confirm(sceneBlurMessage(item))", decide)
        self.assertIn("body.remember_studio_logo=true", _js_function(self.page, "undo"))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_studio_suggestion_lines_in_the_browser_runtime(self):
        source = "\n".join(_js_function(self.page, name) for name in (
            "suggestionLine", "studioBlockedLine", "studioWithheldLine", "studioTextsNote"))
        script = (
            "const esc=s=>String(s??'').replace(/</g,'&lt;');const actionName=(x,d)=>d;" + source +
            ";const moved={suggestion_withheld:{reason:'x'},suggested_decision:'KEEP',advisory:true,"
            "studio_logo_match:{similarity:1}};"
            "const blocked={suggestion_withheld:{reason:'x'},studio_logo_match_blocked:{similarity:0.97,"
            "unconfirmed_texts:['PHIMMOI'],ad_evidence:[]}};"
            "const ad={studio_logo_match_blocked:{similarity:1,unconfirmed_texts:[],ad_evidence:['text_token:x.vn']}};"
            "const site={suggestion_withheld:{reason:'x',window_texts:['PHIMMOI','<b>KUBET</b>'],window_text_count:2}};"
            "const many={suggestion_withheld:{reason:'x',window_texts:['A','B','C','D','E','F','G'],window_text_count:9}};"
            "const silent={suggestion_withheld:{reason:'x',window_texts:[],window_text_count:0}};"
            "const legacy={suggestion_withheld:{reason:'x'}};"
            "console.log(JSON.stringify([suggestionLine(moved),suggestionLine(blocked),suggestionLine(ad),"
            "suggestionLine(site),suggestionLine(many),suggestionLine(silent),suggestionLine(legacy),"
            "studioTextsNote(site),studioTextsNote(Object.assign({},site,{studio_logo_memory:{remembered:true}}))]));"
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
                                timeout=60, check=True)
        moved, blocked, ad, site, many, silent, legacy, note, remembered_note = json.loads(result.stdout)
        self.assertIn("Khớp logo hãng phim bạn đã xác nhận giữ", moved)
        self.assertNotIn("Không đề xuất sẵn", moved, "a moved card does not also claim the withheld suggestion")
        self.assertIn("có chữ lạ: PHIMMOI", blocked)
        self.assertIn("vẫn để ở danh sách chính", blocked)
        self.assertIn("có dấu hiệu quảng cáo", ad)
        # Reviewer finding 2026-10-01: never claim "no ad text" when OCR read a line; name what was read.
        for line in (site, many, legacy):
            self.assertNotIn("chữ quảng cáo", line)
            self.assertNotIn("không đọc thấy chữ", line)
        self.assertIn("Chữ đọc được: PHIMMOI, &lt;b>KUBET&lt;/b> — nếu là tên web/thương hiệu lạ hãy Cắt", site)
        self.assertIn("Chữ đọc được: A, B, C, D, E, F, …", many)
        self.assertIn("không đọc thấy chữ, website hay số điện thoại nào", silent)
        self.assertIn("nếu có tên web/thương hiệu lạ hãy Cắt", legacy, "queues built before the fix stay neutral")
        self.assertIn("sẽ nhớ cả chữ: PHIMMOI, &lt;b>KUBET&lt;/b>", note)
        self.assertEqual(remembered_note, "")
        self.assertIn("${studioTextsNote(x)}", _js_function(self.page, "studioHtml"))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_scene_helpers_in_the_browser_runtime(self):
        names = ("momentsOf", "isScene", "momentTotal", "sceneTitle", "sceneSpan", "sceneHeader", "appliesLine",
                 "momentIndex", "pickStrip", "pickSceneStrip", "isSafety", "catName")
        source = "\n".join(_js_function(self.page, name) for name in names)
        script = (
            "const SAFETY={adult:['18+'],gore:['Máu me'],violence:['Bạo lực']};const KIND_NAMES={};"
            "const SCENE_WORDS={violence:'Trận đánh',gore:'Cảnh máu',adult:'Nhóm 18+'};"
            "const mmss=s=>{const v=Math.max(0,Math.floor(Number(s)||0));return `${Math.floor(v/60)}:${String(v%60).padStart(2,'0')}`;};"
            + source +
            ";const x={category:'violence',start_seconds:3235,end_seconds:3432,temporal_policy:'discrete_detected_intervals',"
            "detected_intervals:[{start_seconds:3235,end_seconds:3275},{start_seconds:3290,end_seconds:3330},{start_seconds:3410,end_seconds:3432}]};"
            "const frames=[];for(let t=3200;t<3440;t+=5)frames.push({t,kind:'context'});frames.push({t:3300,kind:'strongest',score:.9});"
            "const strip=pickSceneStrip(frames,momentsOf(x),8);"
            "console.log(JSON.stringify({header:sceneHeader(x),applies:appliesLine(x),scene:isScene(x),"
            "strip:strip.map(f=>f.t),inside:strip.every(f=>momentIndex(f.t,momentsOf(x))>=0),"
            "covered:[0,1,2].every(i=>strip.some(f=>momentIndex(f.t,momentsOf(x))===i)),"
            "strongest:strip.some(f=>f.kind==='strongest')}));"
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
                                timeout=60, check=True)
        value = json.loads(result.stdout)
        self.assertEqual(value["header"], "Trận đánh · 3 khoảnh khắc · tổng 1:42 (trải dài 53:55–57:12)")
        self.assertIn("3 khoảnh khắc này (tổng 1:42)", value["applies"])
        self.assertTrue(value["scene"])
        self.assertEqual(len(value["strip"]), 8)
        self.assertTrue(value["inside"], value["strip"])
        self.assertTrue(value["covered"])
        self.assertTrue(value["strongest"])


if __name__ == "__main__":
    unittest.main()
