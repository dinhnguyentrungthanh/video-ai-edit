"""Scene cards (R1/R2), opening studio idents (R3a) and studio-logo memory (R3b), 2026-10-01."""
import importlib.util
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
    STUDIO_LOGO_FRAMES_PATH,
    STUDIO_LOGO_MAX_FRAMES,
    STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE,
    STUDIO_LOGO_MEMORY_PATH,
    STUDIO_LOGO_MIN_FRAME_RANGE,
    STUDIO_LOGO_MINIMUM_SIMILARITY,
    compare_studio_logo,
    forget_studio_logo,
    grid_difference,
    hash_similarity,
    load_studio_logo_memory,
    match_studio_logo,
    perceptual_hash,
    prepare_studio_logo_frames,
    refresh_studio_logo_masks,
    remember_studio_logo,
    studio_logo_frame_informative,
    studio_logo_frame_signature,
    studio_logo_grid,
    studio_logo_ignored_regions,
    studio_logo_mask_area,
    studio_logo_signatures,
    studio_logo_window_frames,
    upgrade_studio_logo_memory,
)
from biliflow.cleanup import cleanup_candidates, prune_file_caches
from biliflow.review_evidence import item_evidence
from biliflow.review_workflow import (
    FORCED_BOUNDARY_STUDIO_MOVE,
    SCENE_CARD_MAXIMUM_GAP_SECONDS,
    SCENE_CARD_MAXIMUM_SPAN_SECONDS,
    STUDIO_IDENT_REASON,
    _interactive_html,
    ad_text_tokens,
    application_intervals,
    build_edit_plan,
    build_review_queue,
    bulk_accept_suggested_decisions,
    clear_review_decision,
    full_frame_logo_ad_evidence,
    group_safety_review_events,
    ocr_text_known,
    record_review_decision,
    route_confirmed_studio_logos,
    studio_logo_memory_allowed,
)

ROOT = Path(__file__).resolve().parents[1]
# Real-data tests read previews, queues and videos (read-only) from the project; a git worktree
# without reports/ or input/ can point them at the main tree with BILIFLOW_TEST_DATA_ROOT.
DATA_ROOT = Path(os.environ.get("BILIFLOW_TEST_DATA_ROOT") or ROOT)


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


def _watermarked(image, text="Motchillv.ph", origin=(10, 24)):
    """The ident with a top-left site watermark; returns (relative box of the watermark, image)."""
    (width, height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
    out = image.copy()
    cv2.putText(out, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2, cv2.LINE_AA)
    x, y = origin[0] - 2, origin[1] - height - 2
    frame_height, frame_width = image.shape[:2]
    box = [x / frame_width, y / frame_height, (width + 4) / frame_width, (height + baseline + 4) / frame_height]
    return box, out


def _legacy_match(root, item, records):
    """match_studio_logo exactly as it was before schema 2 (2026-10-01), for the v1 equality check."""
    records = [record for record in records if isinstance(record, dict) and record.get("decision") == "KEEP"
               and record.get("memory_class") == "studio_logo"]
    if not records or item.get("category") != "visual_logo" or item.get("candidate_type") == "persistent_overlay" \
            or isinstance(item.get("suggested_region_source_pixels"), dict):
        return None
    signatures = studio_logo_signatures(root, item, strict=True)
    if not signatures:
        return None
    weakest, worst_cells, matched = None, 0, {}
    for signature in signatures:
        best = None
        for record in records:
            for stored in record.get("signatures") or []:
                similarity = hash_similarity(signature["phash"], str(stored.get("phash", "")))
                if similarity < STUDIO_LOGO_MINIMUM_SIMILARITY:
                    continue
                cells = grid_difference(signature["grid"], stored.get("grid"))
                if cells is None or cells > STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE:
                    continue
                if best is None or (similarity, -cells) > best[0]:
                    best = ((similarity, -cells), record, similarity, cells)
        if best is None:
            return None
        _, record, similarity, cells = best
        matched.setdefault(str(record.get("key")), record)
        weakest = similarity if weakest is None else min(weakest, similarity)
        worst_cells = max(worst_cells, cells)
    primary = next(iter(matched.values()))
    return {
        "similarity": round(weakest, 6), "minimum_similarity": STUDIO_LOGO_MINIMUM_SIMILARITY,
        "cell_difference": worst_cells, "maximum_cell_difference": STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE,
        "memory_key": primary.get("key"), "memory_keys": list(matched), "labels": list(primary.get("labels") or []),
        "source_sha256": primary.get("source_sha256"), "review_item_id": primary.get("review_item_id"),
        "matched_frames": len(signatures),
        "known_texts": list(dict.fromkeys(str(text) for record in matched.values()
                                          for text in ((record.get("window_text") or {}).get("texts") or []))),
        "automatic_edit": False,
    }


def _legacy_compare(root, item, records):
    """compare_studio_logo exactly as it was before schema 2."""
    records = [record for record in records if isinstance(record, dict) and record.get("decision") == "KEEP"
               and record.get("memory_class") == "studio_logo"]
    if not records:
        return None
    signatures = studio_logo_signatures(root, item, strict=True)
    if not signatures:
        return None
    weakest = None
    for signature in signatures:
        best = None
        for record in records:
            for stored in record.get("signatures") or []:
                similarity = hash_similarity(signature["phash"], str(stored.get("phash", "")))
                cells = grid_difference(signature["grid"], stored.get("grid"))
                rank = (similarity, -(256 if cells is None else cells))
                if best is None or rank > best[0]:
                    best = (rank, similarity, cells)
        if weakest is None or best[0] < weakest[0]:
            weakest = best
    return {"records": len(records), "frames": len(signatures), "best_similarity": round(weakest[1], 4),
            "best_cell_difference": weakest[2], "minimum_similarity": STUDIO_LOGO_MINIMUM_SIMILARITY,
            "maximum_cell_difference": STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE}


def _ffmpeg_tools():
    for base in (DATA_ROOT / "tools" / "ffmpeg" / "bin", ROOT / "tools" / "ffmpeg" / "bin"):
        if (base / "ffmpeg.exe").is_file() and (base / "ffprobe.exe").is_file():
            return base / "ffmpeg.exe", base / "ffprobe.exe"
    found = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if all(found) and Path(found[0]).parent == Path(found[1]).parent:
        return Path(found[0]), Path(found[1])
    return None


def _testsrc_clip(ffmpeg, path):
    """A 3 s 640x360 25 fps test clip (generated, never a project video)."""
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    for codec in ("libx264", "mpeg4"):
        result = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", "testsrc=size=640x360:rate=25", "-t", "3", "-pix_fmt", "yuv420p", "-c:v", codec, str(path)],
            capture_output=True, timeout=120)
        if result.returncode == 0 and path.is_file():
            return path
    raise unittest.SkipTest("ffmpeg could not encode a test clip")


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
                      duration=400, extra_interval=None, thumbnail="ident.jpg"):
        directory = self.root / "reports" / name
        (directory / "thumbs").mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
        self.assertTrue(ok)
        (directory / "thumbs" / thumbnail).write_bytes(encoded.tobytes())
        interval = {
            "start_seconds": start, "end_seconds": end, "max_score": 1.0, "strongest_frame": f"thumbs/{thumbnail}",
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
        # A scene-level KEEP changes no brand record, so the brand memory file
        # is not even written (its bytes feed the logo stage cache key).
        brand_path = self.root / MEMORY_PATH
        brand = json.loads(brand_path.read_text(encoding="utf-8")) if brand_path.exists() else {"records": []}
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

    # ------------------------------------------------- platform logos "làm mờ & nhớ" (batch 4a, 2026-10-03)

    @staticmethod
    def _platform_logo_image():
        """A dark frame with a small green logo, like the iQIYI ident."""
        image = np.zeros((180, 320, 3), dtype=np.uint8)
        cv2.rectangle(image, (130, 70), (190, 100), (90, 230, 120), -1)
        return image

    def _platform_queue(self, name, image=None):
        reports = self._ident_report(name, self._platform_logo_image() if image is None else image,
                                     text_tracks=[self._track(6, 9, ["iQIYI"])], thumbnail="ident-6.000s.jpg")
        queue = self._build(reports, name=name)
        [ident] = self._ident(queue)
        return self.root / "reports" / name / "queue.json", ident

    def test_remember_platform_logo_requires_blur_and_writes_record(self):
        path, ident = self._platform_queue("plat")
        decide = lambda **kwargs: record_review_decision(  # noqa: E731
            project_root=self.root, queue_path=path, item_id=ident["id"], **kwargs)
        with self.assertRaisesRegex(ValueError, "Làm mờ"):
            decide(decision="KEEP", remember_platform_logo=True)
        with self.assertRaisesRegex(ValueError, "Chỉ chọn một"):
            decide(decision="BLUR", remember_platform_logo=True, remember_studio_logo=True)
        bright_path, bright = self._platform_queue("bright", _ident_image())
        before = bright_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Không thấy hình logo trên nền tối"):
            record_review_decision(project_root=self.root, queue_path=bright_path, item_id=bright["id"],
                                   decision="BLUR", remember_platform_logo=True)
        self.assertEqual(bright_path.read_bytes(), before, "a refused remember writes nothing")
        self.assertFalse((self.root / STUDIO_LOGO_MEMORY_PATH).exists())

        updated = decide(decision="BLUR", remember_platform_logo=True)
        item = next(value for value in updated["items"] if value["id"] == ident["id"])
        self.assertEqual(item["decision"], "BLUR")
        region = item["decision_region_source_pixels"]
        # The logo box found in the preview (130-190 x 70-100 of 320x180), padded 3 %, in 1920x1080 pixels.
        self.assertLessEqual((region["x"], region["y"]), (780, 420))
        self.assertGreaterEqual((region["x"] + region["width"], region["y"] + region["height"]), (1146, 606))
        self.assertLess(region["width"] * region["height"], 0.10 * 1920 * 1080)
        self.assertEqual((item["start_seconds"], item["end_seconds"]), (5.0, 10.0),
                         "only a decoded window tightens the interval")
        memory = item["platform_logo_memory"]
        self.assertTrue(memory["remembered"])
        self.assertEqual(memory["platform"], {"key": "iqiyi", "name": "iQIYI"}, "named by the window's OCR")
        self.assertEqual((memory["logo_frames"], memory["frames_source"]), (1, "preview_only"))
        self.assertNotIn("studio_logo_memory", item)
        self.assertTrue(updated["audit_log"][-1]["remember_platform_logo"])
        [record] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual((record["memory_class"], record["decision"]), ("platform_logo", "BLUR"))
        self.assertEqual(record["key"], f"plat:{ident['id']}")
        self.assertEqual(record["logo_frame_times"], [6.0])
        self.assertEqual(record["blur_region"]["method"], "logo_pixels")
        self.assertEqual(record["platform"], {"key": "iqiyi", "name": "iQIYI"})
        self.assertTrue((self.root / record["stored_frames"][0]["image"]).is_file())
        self.assertIsNone(match_studio_logo(self.root, dict(ident, decision=None), [record]),
                          "studio matching never reads a platform record")

    def test_other_decision_forgets_platform_record(self):
        path, ident = self._platform_queue("plat")
        record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                               decision="BLUR", remember_platform_logo=True)
        [record] = load_studio_logo_memory(self.root)["records"]
        folder = self.root / record["frames_folder"]
        self.assertTrue(folder.is_dir())
        files = sorted(value.name for value in folder.iterdir())
        snapshot = (self.root / STUDIO_LOGO_MEMORY_PATH).read_bytes()
        updated = record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                                         decision="BLUR", full_frame=True)
        self.assertNotIn("platform_logo_memory", next(v for v in updated["items"] if v["id"] == ident["id"]))
        self.assertEqual(load_studio_logo_memory(self.root)["records"], [])
        self.assertFalse(folder.exists())
        # Security review 2026-10-03: forgetting backs the memory up and moves the frames, never deletes.
        backups = self.root / "state" / "backups"
        [moved] = backups.glob(f"studio-logo-frames-*/{folder.name}")
        self.assertEqual(sorted(value.name for value in moved.iterdir()), files)
        self.assertIn(snapshot, [value.read_bytes() for value in backups.glob("studio-logo-memory-*.json")])
        record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                               decision="BLUR", remember_platform_logo=True)
        cleared = clear_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"])
        self.assertNotIn("platform_logo_memory", next(v for v in cleared["items"] if v["id"] == ident["id"]))
        self.assertEqual(load_studio_logo_memory(self.root)["records"], [])
        self.assertEqual(len(list(backups.glob(f"studio-logo-frames-*/{folder.name}*"))), 2)

    def test_platform_remember_writes_nothing_while_the_memory_keeps_changing(self):
        path, ident = self._platform_queue("plat")
        record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                               decision="BLUR", remember_platform_logo=True)
        [record] = load_studio_logo_memory(self.root)["records"]
        memory = self.root / STUDIO_LOGO_MEMORY_PATH
        before = path.read_bytes()
        from biliflow import platform_memory
        original = platform_memory.backup_memory

        def backup_then_touch(root, snapshot):
            saved = original(root, snapshot)
            memory.write_bytes(memory.read_bytes() + b" ")  # another writer, every time
            return saved

        with mock.patch("biliflow.platform_memory.backup_memory", backup_then_touch):
            with self.assertRaisesRegex(ValueError, "Bộ nhớ logo vừa thay đổi"):
                record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                                       decision="BLUR", remember_platform_logo=True)
        self.assertEqual(path.read_bytes(), before, "the decision is not written either")
        [kept] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual(kept["frames_folder"], record["frames_folder"])
        self.assertTrue((self.root / record["frames_folder"]).is_dir())

    def test_studio_re_remember_keeps_the_old_frames_in_backups(self):
        path, ident = self._confirm_studio_logo("film-a", [self._track(6, 9, ["TOHO"])])
        [record] = load_studio_logo_memory(self.root)["records"]
        folder = self.root / record["frames_folder"]
        files = sorted(value.name for value in folder.iterdir())
        snapshot = (self.root / STUDIO_LOGO_MEMORY_PATH).read_bytes()
        record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                               decision="KEEP", remember_studio_logo=True)
        [again] = load_studio_logo_memory(self.root)["records"]
        self.assertTrue(all((self.root / entry["image"]).is_file() for entry in again["stored_frames"]))
        backups = self.root / "state" / "backups"
        [moved] = backups.glob(f"studio-logo-frames-*/{folder.name}")
        self.assertEqual(sorted(value.name for value in moved.iterdir()), files)
        self.assertIn(snapshot, [value.read_bytes() for value in backups.glob("studio-logo-memory-*.json")])

    # ------------------------------------------------- schema 2 in the decision flow (user decision 2026-10-02)

    def _watermark_queue(self, name):
        """An ident card whose preview carries the film's watermark, plus that watermark's own card."""
        box, image = _watermarked(_ident_image())
        queue = self._build(self._ident_report(name, image, text_tracks=[self._track(6, 9, ["TOHO"])]), name=name)
        path = self.root / "reports" / name / "queue.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        region = {"x": round(box[0] * 1920), "y": round(box[1] * 1080),
                  "width": round(box[2] * 1920), "height": round(box[3] * 1080)}
        payload["items"].append(_card("text", 0.0, 400.0, id="wm-1", candidate_type="persistent_overlay",
                                      priority="high", suggested_decision="BLUR", source_frame_size=[1920, 1080],
                                      suggested_region_source_pixels=region))
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        [ident] = self._ident(queue)
        return path, ident

    def test_later_blur_and_undo_update_masks_and_signatures(self):
        path, ident = self._watermark_queue("film-a")
        record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                               decision="KEEP", remember_studio_logo=True)
        [record] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual(record["ignored_regions"], [])
        legacy = {"key": "legacy:old", "source_sha256": "film-a", "review_item_id": "old", "decision": "KEEP",
                  "memory_class": "studio_logo", "signatures": record["signatures"]}
        memory_path = self.root / STUDIO_LOGO_MEMORY_PATH
        memory = json.loads(memory_path.read_text(encoding="utf-8"))
        memory["records"].append(legacy)
        memory_path.write_text(json.dumps(memory), encoding="utf-8")
        clean = self._ident_report("film-b", _ident_image(), text_tracks=[self._track(6, 9, ["TOHO"])])
        self.assertEqual(len(self._ident(self._build(clean, name="b0", use_studio_logo_memory=True))), 1,
                         "before the BLUR the remembered watermark breaks the match")

        blurred = record_review_decision(project_root=self.root, queue_path=path, item_id="wm-1", decision="BLUR")
        self.assertEqual(blurred["audit_log"][-1]["studio_logo_masks_refreshed"], 1)
        card = next(i for i in blurred["items"] if i["id"] == ident["id"])["studio_logo_memory"]
        self.assertEqual(card["ignored_regions"], [{"item_id": "wm-1", "category": "text"}])
        self.assertTrue(card["mask_updated_at"])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["items"][-1]["decision"], "BLUR")
        masked, kept_legacy = load_studio_logo_memory(self.root)["records"]
        self.assertEqual([region["item_id"] for region in masked["ignored_regions"]], ["wm-1"])
        self.assertNotEqual(masked["frames"][0]["grid"], record["frames"][0]["grid"])
        self.assertEqual(kept_legacy, legacy, "a schema-1 record is never refreshed")
        queue_b = self._build(clean, name="b1", use_studio_logo_memory=True)
        self.assertEqual(self._ident(queue_b), [])
        [moved] = self._ident(queue_b, "advisory_items")
        self.assertEqual(moved["studio_logo_match"]["masked_regions"], 1)
        self.assertIn("không có lớp phủ lạ ngoài vùng watermark đã làm mờ", moved["reasons"][-1])

        cleared = clear_review_decision(project_root=self.root, queue_path=path, item_id="wm-1")
        self.assertEqual(cleared["audit_log"][-1]["studio_logo_masks_refreshed"], 1)
        restored = load_studio_logo_memory(self.root)["records"][0]
        self.assertEqual(restored["ignored_regions"], [])
        self.assertEqual(restored["frames"], record["frames"], "undo restores the unmasked signatures")
        record_review_decision(project_root=self.root, queue_path=path, item_id="wm-1", decision="BLUR")
        kept = record_review_decision(project_root=self.root, queue_path=path, item_id="wm-1", decision="KEEP")
        self.assertEqual(kept["audit_log"][-1]["studio_logo_masks_refreshed"], 1)
        self.assertEqual(load_studio_logo_memory(self.root)["records"][0]["ignored_regions"], [])
        again = record_review_decision(project_root=self.root, queue_path=path, item_id="wm-1", decision="CUT")
        self.assertNotIn("studio_logo_masks_refreshed", again["audit_log"][-1], "nothing changed")

        memory_path.write_text("{", encoding="utf-8")
        unreadable = record_review_decision(project_root=self.root, queue_path=path, item_id="wm-1",
                                            decision="BLUR")
        self.assertEqual(next(i for i in unreadable["items"] if i["id"] == "wm-1")["decision"], "BLUR")
        self.assertNotIn("studio_logo_masks_refreshed", unreadable["audit_log"][-1])
        self.assertEqual(memory_path.read_text(encoding="utf-8"), "{")
        clear_review_decision(project_root=self.root, queue_path=path, item_id="wm-1")

    def test_accepting_the_watermark_suggestion_in_bulk_also_updates_the_mask(self):
        path, ident = self._watermark_queue("film-a")
        record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                               decision="KEEP", remember_studio_logo=True)
        accepted = bulk_accept_suggested_decisions(project_root=self.root, queue_path=path, review_filter="text")
        self.assertEqual(accepted["audit_log"][-1]["studio_logo_masks_refreshed"], 1)
        [record] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual([region["item_id"] for region in record["ignored_regions"]], ["wm-1"])

    def test_a_watermark_decision_in_a_rerun_queue_keeps_the_regions_decided_elsewhere(self):
        # Review finding 2026-10-02: a refresh from another queue of the same source replaced the
        # record's regions wholesale, dropping the BLUR decided in the queue of the remembered card.
        path, ident = self._watermark_queue("film-a")
        record_review_decision(project_root=self.root, queue_path=path, item_id="wm-1", decision="BLUR")
        record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                               decision="KEEP", remember_studio_logo=True)
        rerun = self.root / "reports" / "film-a-rerun" / "queue.json"
        rerun.parent.mkdir(parents=True)
        payload = json.loads(path.read_text(encoding="utf-8"))
        for card in payload["items"]:
            if card.get("candidate_type") == "persistent_overlay":
                card["id"] += "-rerun"
                for key in ("decision", "decided_at", "decision_region_source_pixels"):
                    card.pop(key, None)
            card.pop("studio_logo_memory", None)
            if card["id"] == ident["id"]:
                card["decision"] = None
        rerun.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        def regions():
            [record] = load_studio_logo_memory(self.root)["records"]
            return sorted(region["item_id"] for region in record["ignored_regions"])

        self.assertEqual(regions(), ["wm-1"])
        kept = record_review_decision(project_root=self.root, queue_path=rerun, item_id="wm-1-rerun",
                                      decision="KEEP")
        self.assertNotIn("studio_logo_masks_refreshed", kept["audit_log"][-1], "nothing changed")
        self.assertEqual(regions(), ["wm-1"], "the BLUR of the original queue is kept")
        clean = self._ident_report("film-b", _ident_image(), text_tracks=[self._track(6, 9, ["TOHO"])])
        self.assertEqual(len(self._ident(self._build(clean, name="b1", use_studio_logo_memory=True),
                                         "advisory_items")), 1, "the masked record still matches")
        blurred = record_review_decision(project_root=self.root, queue_path=rerun, item_id="wm-1-rerun",
                                         decision="BLUR")
        self.assertEqual(blurred["audit_log"][-1]["studio_logo_masks_refreshed"], 1)
        self.assertEqual(regions(), ["wm-1", "wm-1-rerun"])
        record_review_decision(project_root=self.root, queue_path=path, item_id="wm-1", decision="KEEP")
        self.assertEqual(regions(), ["wm-1-rerun"], "each queue only changes its own watermark cards")
        clear_review_decision(project_root=self.root, queue_path=rerun, item_id="wm-1-rerun")
        self.assertEqual(regions(), [])

    def test_a_refresh_gives_an_upgraded_card_the_whole_schema_2_summary(self):
        # Review finding 2026-10-02: after studio-logo-upgrade the card had frames but no frames_source,
        # so the page called the decoded frames "ảnh xem trước" and warned about the watermark.
        path, ident = self._watermark_queue("film-a")
        record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                               decision="KEEP", remember_studio_logo=True)
        payload = json.loads(path.read_text(encoding="utf-8"))
        card = next(i for i in payload["items"] if i["id"] == ident["id"])
        card["studio_logo_memory"] = {"remembered": True, "at": card["decided_at"], "frames": 8,
                                      "text_scan_covered": True, "window_texts": ["TOHO"]}  # a v1 card
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        blurred = record_review_decision(project_root=self.root, queue_path=path, item_id="wm-1", decision="BLUR")
        memory = next(i for i in blurred["items"] if i["id"] == ident["id"])["studio_logo_memory"]
        [record] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual(memory["frames"], len(record["frames"]))
        self.assertEqual(memory["frames_source"], record["frames_source"])
        self.assertEqual(memory["frames_reason"], record["frames_reason"])
        self.assertEqual(memory["window"], [5.0, 10.0])
        self.assertEqual(memory["ignored_regions"], [{"item_id": "wm-1", "category": "text"}])
        self.assertEqual(memory["mask_updated_at"], record["mask_updated_at"])
        self.assertEqual(memory["window_texts"], ["TOHO"], "the v1 fields of the card are kept")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), blurred)

    def test_remember_stores_frames_and_regions_on_item(self):
        path, ident = self._watermark_queue("film-a")
        record_review_decision(project_root=self.root, queue_path=path, item_id="wm-1", decision="BLUR")
        updated = record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"],
                                         decision="KEEP", remember_studio_logo=True)
        memory = next(i for i in updated["items"] if i["id"] == ident["id"])["studio_logo_memory"]
        self.assertTrue(memory["remembered"])
        self.assertEqual(memory["frames"], 1)
        self.assertEqual(memory["frames_source"], "preview_only")
        self.assertEqual(memory["frames_reason"], "ffmpeg_missing")
        self.assertEqual(memory["window"], [5.0, 10.0])
        self.assertEqual(memory["ignored_regions"], [{"item_id": "wm-1", "category": "text"}])
        self.assertFalse(memory["mask_refused"])
        [record] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual(record["record_version"], 2)
        self.assertEqual(record["window_text"], {"covered": True, "texts": ["TOHO"]})
        folder = self.root / record["frames_folder"]
        self.assertTrue((self.root / record["frames"][0]["image"]).is_file())
        self.assertTrue(record["signatures"], "the preview signatures of schema 1 are kept")
        record_review_decision(project_root=self.root, queue_path=path, item_id=ident["id"], decision="CUT")
        self.assertEqual(load_studio_logo_memory(self.root)["records"], [])
        self.assertFalse(folder.exists(), "a different decision on the card removes its frames with the record")
        flat = self._build(self._ident_report("flat", np.zeros((180, 320, 3), np.uint8),
                                              text_tracks=[self._track(6, 9, ["TOHO"])]), name="flat")
        flat_path = self.root / "reports" / "flat" / "queue.json"
        before = flat_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "gần như một màu"):
            record_review_decision(project_root=self.root, queue_path=flat_path,
                                   item_id=self._ident(flat)[0]["id"], decision="KEEP", remember_studio_logo=True)
        self.assertEqual(flat_path.read_bytes(), before)

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

    def test_forced_cards_matching_studio_logo_still_move_to_advisory(self):
        # Lead decision A1 (2026-10-03): the licence card "giữ & nhớ" keeps moving the forced
        # first-window and last-6-s cards; only the corroboration gates spare them.
        self.assertIs(FORCED_BOUNDARY_STUDIO_MOVE, True)
        record = remember_studio_logo(self.root, self.queue, self.item("a.jpg"))
        quiet = {"tracks": [], "scan_start_seconds": 0.0, "scan_duration_seconds": 100.0}
        for kind, start, end in (("opening_boundary", 0.0, 5.0), ("ending_boundary", 94.0, 100.0)):
            with self.subTest(kind=kind):
                card = self.item("a.jpg", id=f"review-{kind}", decision=None, candidate_type=kind,
                                 start_seconds=start, end_seconds=end, source_candidate_refs=[])
                kept, moved = route_confirmed_studio_logos(
                    self.root, [card], {"reports/x/text-scan.json": quiet}, {"records": [record]})
                self.assertEqual(kept, [])
                [routed] = moved
                self.assertEqual(routed["suggested_decision"], "KEEP")
                self.assertIs(routed["advisory"], True)
                self.assertEqual(routed["studio_logo_match"]["memory_key"], record["key"])

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

    # ------------------------------------------------- schema 2: window frames + blurred watermarks

    def queue_with(self, *cards, path=None):
        return {"source": {"sha256": "film", "path": str(path or self.root / "missing.mp4")},
                "items": list(cards)}

    def v2_record(self, preview, regions=(), item_id="logo-1", window_frames=None):
        item = self.item(preview, id=item_id, start_seconds=5.0, end_seconds=10.0)
        prepared = prepare_studio_logo_frames(self.root, self.queue_with(), item, window_frames,
                                              ignored_regions=list(regions))
        return remember_studio_logo(self.root, self.queue_with(), item, frames=prepared)

    def test_v1_record_without_frames_matches_exactly_as_before(self):
        record = remember_studio_logo(self.root, self.queue, self.item("a.jpg"))
        self.assertNotIn("frames", record)
        _write_rgb(self.root / "reports" / "a-q75.jpg", _ident_image(), quality=75)
        overlay = _ident_image()
        cv2.putText(overlay, "PHIMMOI", (176, 171), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (255, 255, 255), 1, cv2.LINE_AA)
        _write_rgb(self.root / "reports" / "overlay.jpg", overlay)
        other = dict(record, key="film:logo-2", signatures=studio_logo_signatures(self.root, self.item("b.jpg")))
        for records in ([record], [record, other], [other, record]):
            for previews in (("a.jpg",), ("a-q75.jpg",), ("b.jpg",), ("overlay.jpg",), ("a.jpg", "b.jpg"),
                             ("a-q75.jpg", "a.jpg"), ("missing.jpg",)):
                with self.subTest(records=len(records), previews=previews):
                    item = self.item(*previews, decision=None)
                    self.assertEqual(match_studio_logo(self.root, item, records),
                                     _legacy_match(self.root, item, records))
                    self.assertEqual(compare_studio_logo(self.root, item, records),
                                     _legacy_compare(self.root, item, records))

    def test_v1_memory_file_still_loads_and_is_written_as_v2(self):
        record = remember_studio_logo(self.root, self.queue, self.item("a.jpg"))
        path = self.root / STUDIO_LOGO_MEMORY_PATH
        legacy = {"schema_version": 1, "updated_at": "2026-10-02T14:09:10+07:00", "records": [record],
                  "safety": {"automatic_edit": False}}
        path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        loaded = load_studio_logo_memory(self.root)
        self.assertEqual(loaded["schema_version"], 1)
        self.assertEqual(loaded["records"], [record])
        self.assertFalse(forget_studio_logo(self.root, self.queue, "unknown"))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), legacy, "nothing to forget writes nothing")
        self.assertEqual(refresh_studio_logo_masks(self.root, {"source": {"sha256": "film"}, "items": []}), 0)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), legacy, "a v1 record is never refreshed")
        remember_studio_logo(self.root, {"source": {"sha256": "film-2"}}, self.item("b.jpg", id="logo-2"))
        written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(written["schema_version"], 2)
        self.assertEqual(written["records"][0], record, "the schema-1 record is kept as it was")
        path.write_text(json.dumps(dict(legacy, schema_version=3)), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "schema"):
            load_studio_logo_memory(self.root)

    def test_mask_only_from_blurred_persistent_overlay_cards(self):
        def overlay(card_id, decision="BLUR", category="text", start=0.0, end=400.0, **extra):
            card = {"id": card_id, "category": category, "candidate_type": "persistent_overlay",
                    "start_seconds": start, "end_seconds": end, "decision": decision,
                    "suggested_region_source_pixels": {"x": 46, "y": 40, "width": 191, "height": 49},
                    "source_frame_size": [1280, 534], "decided_at": "2026-10-02T14:00:00+07:00"}
            card.update(extra)
            return card

        ident = self.item("a.jpg", start_seconds=5.0, end_seconds=10.0)
        queue = {"source": {"sha256": "film", "frame_size": [1920, 1080]}, "items": [
            overlay("text-blur"),
            overlay("logo-blur", category="visual_logo",
                    decision_region_source_pixels={"x": 389, "y": 489, "width": 510, "height": 32}),
            overlay("queue-size", source_frame_size=None,
                    suggested_region_source_pixels={"x": 960, "y": 540, "width": 192, "height": 108}),
            overlay("keep", decision="KEEP"), overlay("cut", decision="CUT"), overlay("undecided", decision=None),
            overlay("full-frame", decision_region_source_pixels="FULL_FRAME"),
            overlay("before", start=0.0, end=5.0), overlay("after", start=10.0, end=20.0),
            overlay("violence", category="violence"),
            overlay("no-region", suggested_region_source_pixels=None),
            dict(overlay("regional-logo"), candidate_type="opening_promotion"),
            ident,
        ], "advisory_items": [overlay("advisory-blur", start=6.0, end=7.0)]}
        regions = studio_logo_ignored_regions(queue, ident)
        self.assertEqual([region["item_id"] for region in regions],
                         ["text-blur", "logo-blur", "queue-size", "advisory-blur"])
        self.assertEqual(regions[0]["box"], [round(46 / 1280, 6), round(40 / 534, 6),
                                             round(191 / 1280, 6), round(49 / 534, 6)])
        self.assertEqual(regions[1]["box"], [round(389 / 1280, 6), round(489 / 534, 6),
                                             round(510 / 1280, 6), round(32 / 534, 6)],
                         "the user's own region wins over the suggested one")
        self.assertEqual(regions[2]["box"], [0.5, 0.5, 0.1, 0.1], "the queue frame size is the fallback")
        self.assertEqual(regions[0]["category"], "text")
        self.assertEqual(regions[1]["category"], "visual_logo")

    def test_mask_area_cap_refuses_masking_above_20_percent(self):
        tap10 = [{"box": [46 / 1280, 40 / 534, 191 / 1280, 49 / 534], "item_id": "wm"},
                 {"box": [389 / 1280, 489 / 534, 510 / 1280, 32 / 534], "item_id": "line"}]
        self.assertAlmostEqual(studio_logo_mask_area(tap10, (134, 320)), 0.068, places=2)
        item = self.item("a.jpg", start_seconds=5.0, end_seconds=10.0)
        small = prepare_studio_logo_frames(self.root, self.queue_with(), item, ignored_regions=tap10)
        self.assertEqual([region["item_id"] for region in small["ignored_regions"]], ["wm", "line"])
        self.assertIsNone(small["mask_refused"])
        large = [{"box": [0.0, 0.0, 0.5, 0.5], "item_id": "big"}]
        refused = prepare_studio_logo_frames(self.root, self.queue_with(), item, ignored_regions=large)
        self.assertEqual(refused["ignored_regions"], [])
        self.assertEqual(refused["mask_refused"]["reason"], "area_cap")
        self.assertGreater(refused["mask_refused"]["area"], 0.20)
        unmasked = prepare_studio_logo_frames(self.root, self.queue_with(), item, ignored_regions=[])
        self.assertEqual(refused["frames"], unmasked["frames"], "a refused mask means the full picture")

    def test_masked_record_matches_clean_and_same_watermark_but_not_overlays_outside(self):
        wm_box, watermarked = _watermarked(_ident_image())
        _write_rgb(self.root / "reports" / "wm.jpg", watermarked)
        regions = [{"box": wm_box, "item_id": "wm", "category": "text"}]
        record = self.v2_record("wm.jpg", regions)
        self.assertEqual(record["record_version"], 2)
        self.assertEqual(record["frames_source"], "preview_only")
        unmasked = self.v2_record("wm.jpg", (), item_id="logo-unmasked")

        def candidate(name, image):
            _write_rgb(self.root / "reports" / f"{name}.jpg", image)
            return self.item(f"{name}.jpg", decision=None)

        clean = candidate("clean", _ident_image())
        self.assertIsNone(match_studio_logo(self.root, clean, [unmasked]), "unmasked, the watermark breaks it")
        matching = {"clean": _ident_image(), "same watermark": watermarked}
        other_text = _ident_image()
        x0, y0 = int(wm_box[0] * 320) + 4, int((wm_box[1] + wm_box[3]) * 180) - 4
        cv2.putText(other_text, "XYZ.vn", (x0, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 2, cv2.LINE_AA)
        matching["other text inside the box"] = other_text
        for name, image in matching.items():
            with self.subTest(match=name):
                match = match_studio_logo(self.root, candidate(name.replace(" ", "-"), image), [record])
                self.assertIsNotNone(match)
                self.assertEqual(match["masked_regions"], 1)
                self.assertEqual(match["frames_source"], "preview_only")
                self.assertLessEqual(match["cell_difference"], STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE)
        outside = {name: _ident_image() for name in ("corner url", "bottom banner", "box outside", "straddling")}
        cv2.putText(outside["corner url"], "www.bet88.vip", (200, 171), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (255, 255, 255), 1, cv2.LINE_AA)
        outside["bottom banner"][162:, :] = (40, 40, 160)
        outside["box outside"][60:74, 260:300] = (230, 30, 30)
        x_edge = int((wm_box[0] + wm_box[2]) * 320) - 12
        cv2.putText(outside["straddling"], "motchill.xyz", (x_edge, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (255, 255, 255), 2, cv2.LINE_AA)
        for name, image in outside.items():
            with self.subTest(no_match=name):
                item = candidate(name.replace(" ", "-"), image)
                self.assertIsNone(match_studio_logo(self.root, item, [record]))
                # Forged: the stored frame carries the overlaid frame's own masked pHash.
                masked = studio_logo_frame_signature(image, regions)
                forged = dict(record, frames=[dict(frame, phash=masked["phash"]) for frame in record["frames"]])
                self.assertIsNone(match_studio_logo(self.root, item, [forged]))
                compared = compare_studio_logo(self.root, item, [record])
                self.assertEqual(compared["masked_regions"], 1)

    def test_low_information_frames_are_never_stored_and_black_candidates_never_match(self):
        black = np.zeros((180, 320, 3), np.uint8)
        flat = np.full((180, 320, 3), (150, 20, 20), np.uint8)
        fade = (_ident_image().astype(np.float32) * 0.12).astype(np.uint8)
        for name, image in (("black", black), ("flat", flat), ("fade", fade)):
            with self.subTest(frame=name):
                self.assertFalse(studio_logo_frame_informative(image))
                self.assertLessEqual(studio_logo_frame_signature(image)["range"], STUDIO_LOGO_MIN_FRAME_RANGE)
                _write_rgb(self.root / "reports" / f"{name}.jpg", image)
        self.assertTrue(studio_logo_frame_informative(_ident_image()))
        frames = {"frames": [(5.0 + index * 0.04, cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))[1]
                              .tobytes()) for index, image in enumerate((black, _ident_image(), fade, flat))],
                  "frames_source": "source_video", "reason": None, "pipeline": None}
        record = self.v2_record("a.jpg", window_frames=frames)
        self.assertEqual(len(record["frames"]), 1, "only the ident frame is stored")
        self.assertEqual(len(record["stored_frames"]), 4, "every decoded JPEG is kept for a later re-sign")
        for name in ("black", "flat", "fade"):
            with self.subTest(candidate=name):
                self.assertIsNone(match_studio_logo(self.root, self.item(f"{name}.jpg", decision=None), [record]))
        black_only = self.v2_record("black.jpg", item_id="logo-black")
        self.assertEqual(black_only["frames"], [])
        self.assertIsNone(match_studio_logo(self.root, self.item("black.jpg", decision=None), [black_only]))

    def test_multi_frame_record_every_preview_must_still_match(self):
        record = self.v2_record("a.jpg")
        nine = match_studio_logo(self.root, self.item(*["a.jpg"] * 9, decision=None), [record])
        self.assertEqual(nine["matched_frames"], 9)
        self.assertEqual(len(nine["matched_stored_t"]), 9)
        self.assertIsNone(match_studio_logo(self.root, self.item(*(["a.jpg"] * 8 + ["b.jpg"]), decision=None),
                                            [record]))
        self.assertIsNone(match_studio_logo(self.root, self.item("a.jpg", "missing.jpg", decision=None), [record]))

    def test_frame_cap_and_dedup(self):
        def frame(index):
            image = np.zeros((180, 320, 3), np.uint8)
            cv2.rectangle(image, (index % 290, 20 + index // 10), (index % 290 + 30, 120 + index // 10),
                          (255, (index * 37) % 256, 200), -1)
            return cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))[1].tobytes()

        many = {"frames": [(index * 0.04, frame(index)) for index in range(300)], "frames_source": "source_video"}
        record = self.v2_record("a.jpg", window_frames=many)
        self.assertLessEqual(len(record["frames"]), STUDIO_LOGO_MAX_FRAMES)
        self.assertEqual(len(record["frames"]), STUDIO_LOGO_MAX_FRAMES)
        ident = cv2.imencode(".jpg", cv2.cvtColor(_ident_image(), cv2.COLOR_RGB2BGR))[1].tobytes()
        speck = _ident_image()
        speck[2, 2] = (90, 90, 90)  # one pixel: other bytes, same pHash, grid within 2
        near = cv2.imencode(".jpg", cv2.cvtColor(speck, cv2.COLOR_RGB2BGR))[1].tobytes()
        self.assertNotEqual(ident, near)
        self.assertEqual(studio_logo_frame_signature(speck)["phash"],
                         studio_logo_frame_signature(_ident_image())["phash"])
        duplicates = {"frames": [(0.0, ident), (0.04, ident), (0.08, near)], "frames_source": "source_video"}
        record = self.v2_record("a.jpg", window_frames=duplicates, item_id="logo-dup")
        self.assertEqual(len(record["frames"]), 1, "same bytes and same-signature frames are stored once")
        self.assertEqual([entry["t"] for entry in record["stored_frames"]][:2], [0.0, 0.08])

    def test_studio_logo_frames_are_never_cleanup_candidates(self):
        record = self.v2_record("a.jpg")
        folder = self.root / record["frames_folder"]
        self.assertTrue(folder.is_dir())
        self.assertEqual(folder.parent, (self.root / STUDIO_LOGO_FRAMES_PATH).resolve())
        old = 1_000_000_000
        for path in folder.iterdir():
            os.utime(path, (old, old))
        self.assertFalse(any("studio-logo-frames" in entry["path"] for entry in cleanup_candidates(self.root)))
        prune_file_caches(self.root)
        self.assertTrue(all(path.is_file() for path in folder.iterdir()))
        self.assertTrue(any(folder.iterdir()))

    def test_forget_removes_the_frames_only_together_with_the_record(self):
        record = self.v2_record("a.jpg")
        folder = self.root / record["frames_folder"]
        other = self.v2_record("b.jpg", item_id="logo-2")
        self.assertFalse(forget_studio_logo(self.root, self.queue, "logo-3"))
        self.assertTrue(folder.is_dir())
        self.assertTrue(forget_studio_logo(self.root, self.queue, "logo-1"))
        self.assertFalse(folder.exists())
        self.assertTrue((self.root / other["frames_folder"]).is_dir())
        self.assertEqual([r["key"] for r in load_studio_logo_memory(self.root)["records"]], ["film:logo-2"])

    def test_a_withdrawn_mask_without_its_frame_jpegs_stops_the_record_matching(self):
        # Review finding 2026-10-02: with state/studio-logo-frames missing, an un-BLUR left the old mask.
        wm_box, watermarked = _watermarked(_ident_image())
        _write_rgb(self.root / "reports" / "wm.jpg", watermarked)
        watermark = {"id": "wm", "category": "text", "candidate_type": "persistent_overlay", "decision": "BLUR",
                     "start_seconds": 0.0, "end_seconds": 400.0, "source_frame_size": [320, 180],
                     "suggested_region_source_pixels": {"x": wm_box[0] * 320, "y": wm_box[1] * 180,
                                                        "width": wm_box[2] * 320, "height": wm_box[3] * 180}}
        record = self.v2_record("wm.jpg", [{"box": wm_box, "item_id": "wm", "category": "text"}])
        clean = self.item("a-clean.jpg", decision=None)
        _write_rgb(self.root / "reports" / "a-clean.jpg", _ident_image())
        self.assertIsNotNone(match_studio_logo(self.root, clean, [record]))
        queue = self.queue_with(watermark, self.item("wm.jpg", start_seconds=5.0, end_seconds=10.0))
        self.assertEqual(refresh_studio_logo_masks(self.root, queue), 0, "still BLUR: nothing changes")
        shutil.rmtree(self.root / record["frames_folder"])
        self.assertEqual(refresh_studio_logo_masks(self.root, queue), 0, "missing frames alone change nothing")
        watermark["decision"] = "KEEP"
        self.assertEqual(refresh_studio_logo_masks(self.root, queue), 1)
        [refreshed] = load_studio_logo_memory(self.root)["records"]
        self.assertEqual(refreshed["ignored_regions"], [])
        self.assertEqual(refreshed["frames"], [], "the withdrawn mask is not kept")
        self.assertTrue(refreshed["frames_missing"])
        self.assertTrue(refreshed["signatures"], "nothing else of the record is removed")
        self.assertIsNone(match_studio_logo(self.root, clean, [refreshed]))
        self.assertIsNone(match_studio_logo(self.root, self.item("wm.jpg", decision=None), [refreshed]))
        self.assertEqual(refresh_studio_logo_masks(self.root, queue), 0)

    def test_upgrade_writes_nothing_when_the_memory_changes_while_it_decodes(self):
        # Review finding 2026-10-02: a forget (or remember) that landed during the decode was overwritten
        # with the stale snapshot, bringing a removed record back.
        self.v2_record("b.jpg", item_id="other")
        remember_studio_logo(self.root, self.queue_with(), self.item("a.jpg", id="kept"))
        remember_studio_logo(self.root, self.queue_with(), self.item("a.jpg", id="gone"))
        path = self.root / STUDIO_LOGO_MEMORY_PATH
        frames_root = self.root / STUDIO_LOGO_FRAMES_PATH

        def forgetting(target):
            def plan(root, record, ffmpeg_path, ffprobe_path):
                item = self.item("a.jpg", id=record["review_item_id"], start_seconds=5.0, end_seconds=10.0)
                prepared = prepare_studio_logo_frames(root, self.queue_with(), item, ignored_regions=[])
                if record["review_item_id"] == target:
                    forget_studio_logo(root, self.queue_with(), target)  # the Control Center, meanwhile
                return prepared, None, {}
            return plan

        folders_before = sorted(path.name for path in frames_root.iterdir())
        with mock.patch("biliflow.brand_memory._plan_studio_logo_upgrade", forgetting("gone")):
            report = upgrade_studio_logo_memory(self.root, apply=True, ffmpeg_path=self.root / "ffmpeg.exe")
        self.assertEqual(report["aborted"], "memory_changed_during_upgrade")
        self.assertIn("re-run", report["message"])
        self.assertEqual((report["backup"], report["upgraded"]), (None, 0))
        self.assertEqual([r["review_item_id"] for r in load_studio_logo_memory(self.root)["records"]],
                         ["other", "kept"], "the forgotten record stays forgotten")
        self.assertNotIn("frames", load_studio_logo_memory(self.root)["records"][1])
        self.assertEqual(sorted(path.name for path in frames_root.iterdir()), folders_before)
        self.assertFalse((self.root / "state" / "backups").exists())

        # A change while the frame JPEGs are written: the JPEGs nobody uses are removed again.
        memory_before = path.read_bytes()
        from biliflow import brand_memory

        real_write = brand_memory._write_studio_logo_frames

        def write_then_remember(root, prepared):
            real_write(root, prepared)
            remember_studio_logo(self.root, self.queue_with(), self.item("b.jpg", id="new"))

        def plan_only(root, record, ffmpeg_path, ffprobe_path):
            item = self.item("a.jpg", id=record["review_item_id"], start_seconds=5.0, end_seconds=10.0)
            return prepare_studio_logo_frames(root, self.queue_with(), item, ignored_regions=[]), None, {}

        with mock.patch("biliflow.brand_memory._plan_studio_logo_upgrade", plan_only), \
                mock.patch("biliflow.brand_memory._write_studio_logo_frames", write_then_remember):
            report = upgrade_studio_logo_memory(self.root, apply=True, ffmpeg_path=self.root / "ffmpeg.exe")
        self.assertEqual(report["aborted"], "memory_changed_during_upgrade")
        self.assertNotEqual(path.read_bytes(), memory_before)
        self.assertEqual([r["review_item_id"] for r in load_studio_logo_memory(self.root)["records"]],
                         ["other", "kept", "new"])
        self.assertEqual(sorted(path.name for path in frames_root.iterdir()), folders_before)
        argv = ["studio_logo_upgrade.py", "--project-root", str(self.root), "--apply",
                "--ffmpeg", str(self.root / "ffmpeg.exe")]
        with mock.patch.object(sys, "argv", argv), mock.patch("builtins.print") as printed, \
                mock.patch("biliflow.brand_memory._plan_studio_logo_upgrade", forgetting("new")):
            self.assertEqual(_upgrade_script().main(), 1, "an aborted --apply is not a success")
        self.assertEqual(json.loads(printed.call_args[0][0])["aborted"], "memory_changed_during_upgrade")
        self.assertEqual([r["review_item_id"] for r in load_studio_logo_memory(self.root)["records"]],
                         ["other", "kept"])

        # Unchanged meanwhile: applied, and the backup holds exactly the bytes replaced.
        snapshot = path.read_bytes()
        with mock.patch("biliflow.brand_memory._plan_studio_logo_upgrade", plan_only):
            report = upgrade_studio_logo_memory(self.root, apply=True, ffmpeg_path=self.root / "ffmpeg.exe")
        self.assertNotIn("aborted", report)
        self.assertEqual(report["upgraded"], 1)
        self.assertEqual((self.root / report["backup"]).read_bytes(), snapshot)
        self.assertTrue(all("frames" in r for r in load_studio_logo_memory(self.root)["records"]))

    @unittest.skipUnless(_ffmpeg_tools(), "ffmpeg/ffprobe not available")
    def test_window_frames_from_video_reproduce_scanner_preview_bytes(self):
        from biliflow.visual_logo_scanner import _iter_frames, _jpeg

        ffmpeg, _ = _ffmpeg_tools()
        clip = _testsrc_clip(ffmpeg, self.root / "input" / "clip.mp4")
        boundary = {t: _jpeg(frame) for t, frame in _iter_frames(
            ffmpeg_path=ffmpeg, input_path=clip, start=0.0, duration=3.0, sample_every=0.25,
            width=320, height=180, decode_backend="cpu")}
        preview = self.root / "reports" / "x" / "logo-0001-0.250s.jpg"
        preview.parent.mkdir(parents=True)
        preview.write_bytes(boundary[0.25])
        item = self.item("x/logo-0001-0.250s.jpg", start_seconds=0.0, end_seconds=2.0)
        window = studio_logo_window_frames(self.root, self.queue_with(path=clip), item, ffmpeg)
        self.assertEqual((window["frames_source"], window["reason"]), ("source_video", None))
        self.assertEqual(len(window["frames"]), 50, "every frame of 0-2 s at the native 25 fps")
        self.assertEqual(window["pipeline"]["analysis_size"], [320, 180])
        self.assertEqual(window["pipeline"]["fps"], 25.0)
        decoded = {round(t, 3): data for t, data in window["frames"]}
        self.assertIn(boundary[0.25], set(decoded.values()), "the scanner's 0.25 s JPEG, byte for byte")
        self.assertTrue(all(boundary[t] in set(decoded.values()) for t in (0.0, 0.5, 1.0, 1.75)))
        prepared = prepare_studio_logo_frames(self.root, self.queue_with(path=clip), item, window)
        record = remember_studio_logo(self.root, self.queue_with(path=clip), item, frames=prepared)
        self.assertEqual(record["frames_source"], "source_video")
        self.assertEqual(len(record["stored_frames"]), 50, "the preview equals a decoded frame, stored once")
        self.assertTrue(all((self.root / entry["image"]).is_file() for entry in record["stored_frames"]))

    def test_missing_or_mismatched_source_falls_back_to_previews(self):
        item = self.item("a.jpg", start_seconds=0.0, end_seconds=2.0)
        missing = studio_logo_window_frames(self.root, self.queue_with(), item, self.root / "ffmpeg.exe")
        self.assertEqual((missing["frames_source"], missing["reason"], missing["frames"]),
                         ("preview_only", "source_missing", []))
        source = self.root / "input" / "source.mp4"
        source.parent.mkdir()
        source.write_bytes(b"not a video")
        no_tool = studio_logo_window_frames(self.root, self.queue_with(path=source), item, self.root / "ffmpeg.exe")
        self.assertEqual(no_tool["reason"], "ffmpeg_missing")
        fake = self.root / "tools" / "ffmpeg.exe"
        fake.parent.mkdir()
        fake.write_text("not a program", encoding="utf-8")
        timed = self.item("x/logo-0001-0.250s.jpg", start_seconds=0.0, end_seconds=2.0)
        (self.root / "reports" / "x").mkdir()
        shutil.copyfile(self.root / "reports" / "a.jpg", self.root / "reports" / "x" / "logo-0001-0.250s.jpg")
        self.assertEqual(studio_logo_window_frames(self.root, self.queue_with(path=source), item, fake)["reason"],
                         "self_check_unavailable", "a preview without a time cannot prove the source")
        self.assertEqual(studio_logo_window_frames(self.root, self.queue_with(path=source), timed, fake)["reason"],
                         "probe_failed")
        prepared = prepare_studio_logo_frames(self.root, self.queue_with(path=source), item,
                                              studio_logo_window_frames(self.root, self.queue_with(path=source),
                                                                        item, fake))
        self.assertEqual(prepared["frames_source"], "preview_only")
        self.assertEqual(len(prepared["frames"]), 1, "the card's own preview is still remembered")
        tools = _ffmpeg_tools()
        if tools:
            clip = _testsrc_clip(tools[0], self.root / "input" / "clip.mp4")
            window = studio_logo_window_frames(self.root, self.queue_with(path=clip), timed, tools[0])
            self.assertEqual((window["frames_source"], window["reason"]), ("preview_only", "self_check_failed"))

    @unittest.skipUnless(_ffmpeg_tools(), "ffmpeg/ffprobe not available")
    def test_upgrade_is_a_dry_run_by_default_and_apply_only_adds_fields(self):
        from biliflow.visual_logo_scanner import _iter_frames, _jpeg

        ffmpeg, _ = _ffmpeg_tools()
        clip = _testsrc_clip(ffmpeg, self.root / "input" / "clip.mp4")
        job = self.root / "reports" / "jobs" / "j1"
        thumbs = job / "visual-logo" / "thumbnails"
        thumbs.mkdir(parents=True)
        frames = dict(_iter_frames(ffmpeg_path=ffmpeg, input_path=clip, start=0.0, duration=3.0,
                                   sample_every=0.25, width=320, height=180, decode_backend="cpu"))
        (thumbs / "logo-0001-0.250s.jpg").write_bytes(_jpeg(frames[0.25]))
        ident = self.item("jobs/j1/visual-logo/thumbnails/logo-0001-0.250s.jpg", id="ident", start_seconds=0.0, end_seconds=2.0,
                          studio_logo_memory={"remembered": True})
        watermark = {"id": "wm", "category": "text", "candidate_type": "persistent_overlay", "decision": "BLUR",
                     "start_seconds": 0.0, "end_seconds": 3.0, "source_frame_size": [640, 360],
                     "suggested_region_source_pixels": {"x": 10, "y": 10, "width": 100, "height": 30}}
        queue = {"source": {"sha256": "film", "path": str(clip)}, "items": [ident, watermark]}
        (job / "review-queue.json").write_text(json.dumps(queue), encoding="utf-8")
        record = remember_studio_logo(self.root, queue, ident)
        self.assertIsNotNone(record)
        orphan = remember_studio_logo(self.root, {"source": {"sha256": "gone"}}, self.item("a.jpg", id="old"))
        path = self.root / STUDIO_LOGO_MEMORY_PATH
        before = path.read_bytes()
        dry = upgrade_studio_logo_memory(self.root, ffmpeg_path=ffmpeg)
        self.assertTrue(dry["dry_run"])
        self.assertEqual([entry["status"] for entry in dry["records"]], ["upgradable", "kept_v1"])
        self.assertEqual(dry["records"][0]["ignored_regions"][0]["item_id"], "wm")
        self.assertIn("Hãy bấm lại", dry["records"][1]["message"])
        self.assertEqual(path.read_bytes(), before, "a dry run writes nothing")
        self.assertFalse((self.root / STUDIO_LOGO_FRAMES_PATH).exists())
        applied = upgrade_studio_logo_memory(self.root, apply=True, ffmpeg_path=ffmpeg)
        self.assertEqual(applied["upgraded"], 1)
        self.assertEqual((self.root / applied["backup"]).read_bytes(), before)
        upgraded, untouched = load_studio_logo_memory(self.root)["records"]
        self.assertEqual({key: upgraded[key] for key in record}, record, "every old field is kept unchanged")
        self.assertEqual(untouched, orphan)
        self.assertEqual(upgraded["frames_source"], "source_video")
        self.assertEqual([region["item_id"] for region in upgraded["ignored_regions"]], ["wm"])
        self.assertTrue(upgraded["frames"])
        again = upgrade_studio_logo_memory(self.root, apply=True, ffmpeg_path=ffmpeg)
        self.assertEqual([entry["status"] for entry in again["records"]], ["already_v2", "kept_v1"])
        self.assertEqual(again["upgraded"], 0)
        argv = ["studio_logo_upgrade.py", "--project-root", str(self.root), "--ffmpeg", str(ffmpeg)]
        with mock.patch.object(sys, "argv", argv), mock.patch("builtins.print") as printed:
            self.assertEqual(_upgrade_script().main(), 0)
        self.assertTrue(json.loads(printed.call_args[0][0])["dry_run"])

def _upgrade_script():
    """scripts/studio_logo_upgrade.py (not a ``biliflow`` subcommand: cli.py keys every scan cache)."""
    spec = importlib.util.spec_from_file_location(
        "studio_logo_upgrade", Path(__file__).resolve().parents[1] / "scripts" / "studio_logo_upgrade.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REAL_IDENTS = {
    "toho": DATA_ROOT / "reports/jobs/conan20-allgroups-full-fast-20260930-073627/visual-logo/thumbnails/logo-0002-7.750s.jpg",
    "wb": DATA_ROOT / "reports/jobs/troy-allgroups-full-fast-20260930-162534/visual-logo/thumbnails/logo-0002-9.250s.jpg",
}
_TOHO_CONAN21 = DATA_ROOT / "reports/jobs/conan21-allgroups-full-golden-20260930-222720/visual-logo/thumbnails/logo-0002-7.750s.jpg"


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

    # Tập 10's two blurred watermark boxes (review-1aa9bcb3cc7c, review-638c84b31946) on a 1280x534 source.
    TAP10_TOP_LEFT = [46 / 1280, 40 / 534, 191 / 1280, 49 / 534]
    TAP10_BOTTOM_LINE = [389 / 1280, 489 / 534, 510 / 1280, 32 / 534]

    def v2_records(self, boxes):
        regions = [{"box": box, "item_id": f"wm-{index}", "category": "text"} for index, box in enumerate(boxes)]
        records = []
        for key in ("toho", "wb"):
            item = self.item(key, decision="KEEP")
            queue = {"source": {"sha256": f"{key}-v2"}}
            prepared = prepare_studio_logo_frames(self.root, queue, item, ignored_regions=regions)
            records.append(remember_studio_logo(self.root, queue, item, window_text={"covered": True, "texts": []},
                                                frames=prepared))
        return records

    def names(self):
        return [name for name in ("corner", "url", "banner", "toho", "wb", "toho21")
                if (self.folder / f"{name}.jpg").is_file()]

    def test_no_mask_is_identical_to_v1(self):
        records = self.v2_records([])
        for name in self.names():
            with self.subTest(card=name):
                old = match_studio_logo(self.root, self.item(name), self.records)
                new = match_studio_logo(self.root, self.item(name), records)
                self.assertEqual(old is None, new is None)
                if old is not None:
                    self.assertEqual((new["similarity"], new["cell_difference"]),
                                     (old["similarity"], old["cell_difference"]))
                    self.assertEqual(new["masked_regions"], 0)
                old_compare = compare_studio_logo(self.root, self.item(name), self.records)
                new_compare = compare_studio_logo(self.root, self.item(name), records)
                self.assertEqual((new_compare["best_similarity"], new_compare["best_cell_difference"]),
                                 (old_compare["best_similarity"], old_compare["best_cell_difference"]))

    def test_unrelated_mask_keeps_overlays_outside_it_unmatched(self):
        records = self.v2_records([self.TAP10_TOP_LEFT])
        for name in ("corner", "url", "banner"):
            with self.subTest(overlay=name):
                self.assertIsNone(match_studio_logo(self.root, self.item(name), records))
        for name in self.names()[3:]:
            with self.subTest(repeat=name):
                match = match_studio_logo(self.root, self.item(name), records)
                self.assertIsNotNone(match)
                self.assertEqual(match["masked_regions"], 1)

    def test_an_overlay_entirely_inside_a_blurred_region_matches_by_design(self):
        # Documented (QUALITY_PLAN §23): the user blurred that region in the remembered episode, so the picture
        # check ignores it; text placed there is left to the mandatory OCR window-text check.
        records = self.v2_records([self.TAP10_TOP_LEFT, self.TAP10_BOTTOM_LINE])
        self.assertIsNotNone(match_studio_logo(self.root, self.item("corner"), records),
                             "the 'PHIMMOI' corner text lies inside the bottom-line mask")
        for name in ("url", "banner"):
            with self.subTest(overlay=name):
                self.assertIsNone(match_studio_logo(self.root, self.item(name), records), "wider than the mask")
        logo = {"intervals": [{"start_seconds": 5.0, "end_seconds": 10.0}]}
        text = {"tracks": [{"start_seconds": 6.0, "end_seconds": 9.0, "persistent": False,
                            "routing": "LIKELY_SCENE_TEXT", "semantic_top_label": "scene_text",
                            "sample_text": ["PHIMMOI"], "policy_hits": []}],
                "scan_start_seconds": 0.0, "scan_duration_seconds": 100.0}
        kept, moved = route_confirmed_studio_logos(
            self.root, [self.item("corner")], {"reports/x/logo.json": logo, "reports/x/text-scan.json": text},
            {"records": records})
        self.assertEqual(moved, [])
        self.assertEqual(kept[0]["studio_logo_match_blocked"]["unconfirmed_texts"], ["PHIMMOI"])


_TAP10_QUEUE = DATA_ROOT / "reports/jobs/nhất-âu-xuân-tập-10-f79bae15-run-20261002-135619/review-queue.json"
_TAP_PREVIEWS = {
    episode: next(iter(sorted((DATA_ROOT / f"reports/jobs/{folder}/visual-logo/thumbnails").glob(
        "*-2.250s.jpg"))), None) if (DATA_ROOT / f"reports/jobs/{folder}").is_dir() else None
    for episode, folder in ((12, "nhất-âu-xuân-tập-12-c6822dbc"), (16, "nhất-âu-xuân-tập-16-3b4dc53d"),
                            (17, "nhất-âu-xuân-tập-17-bae453af"))
}


def _nhat_au_xuan_available():
    if not _TAP10_QUEUE.is_file() or not all(_TAP_PREVIEWS.values()) or not _ffmpeg_tools():
        return False
    if not all(path.is_file() for path in _REAL_IDENTS.values()):
        return False
    source = json.loads(_TAP10_QUEUE.read_text(encoding="utf-8"))["source"]["path"]
    return Path(source).is_file()


@unittest.skipUnless(_nhat_au_xuan_available(), "Nhất Âu Xuân Tập 10/12/16/17 data, Golden idents or ffmpeg missing")
class RealNhatAuXuanMaskTests(unittest.TestCase):
    """The measured design case (temp/studio-mask-design/design.json), read-only on the project data."""

    def test_masked_25fps_record_matches_clean_episodes_and_nothing_else(self):
        self.assertEqual((STUDIO_LOGO_MINIMUM_SIMILARITY, STUDIO_LOGO_MAXIMUM_CELL_DIFFERENCE), (0.95, 20))
        ffmpeg, _ = _ffmpeg_tools()
        queue = json.loads(_TAP10_QUEUE.read_text(encoding="utf-8"))
        item = next(value for value in queue["items"] if value["id"] == "review-9445dc481911")
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            preview = item["preview_images"][0]
            (root / preview).parent.mkdir(parents=True)
            shutil.copyfile(DATA_ROOT / preview, root / preview)
            window = studio_logo_window_frames(root, queue, item, ffmpeg)
            self.assertEqual((window["frames_source"], window["reason"]), ("source_video", None))
            self.assertEqual(window["pipeline"]["analysis_size"], [320, 134])
            prepared = prepare_studio_logo_frames(root, queue, item, window)
            self.assertEqual(sorted(region["item_id"] for region in prepared["ignored_regions"]),
                             ["review-1aa9bcb3cc7c", "review-638c84b31946"])
            self.assertGreater(len(prepared["frames"]), 100)
            record = dict(item, key="tap10:a", decision="KEEP", memory_class="studio_logo", **{
                name: prepared[name] for name in ("frames", "ignored_regions", "frames_source")})
            for episode, path in _TAP_PREVIEWS.items():
                with self.subTest(episode=episode):
                    candidate = {"id": f"tap{episode}", "category": "visual_logo",
                                 "candidate_type": "opening_promotion",
                                 "preview_images": [path.relative_to(DATA_ROOT).as_posix()]}
                    match = match_studio_logo(DATA_ROOT, candidate, [record])
                    self.assertIsNotNone(match)
                    self.assertLessEqual(match["cell_difference"], 10)
                    self.assertEqual(match["masked_regions"], 2)
            _write_rgb(root / "reports" / "black-13.000s.jpg", np.zeros((134, 320, 3), np.uint8))
            negatives = [(DATA_ROOT, path.relative_to(DATA_ROOT).as_posix()) for path in _REAL_IDENTS.values()]
            for base, relative in (*negatives, (root, "reports/black-13.000s.jpg")):
                with self.subTest(negative=relative):
                    candidate = {"id": "negative", "category": "visual_logo", "candidate_type": "opening_promotion",
                                 "preview_images": [relative]}
                    self.assertIsNone(match_studio_logo(base, candidate, [record]))

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
    def test_platform_action_posts_flag_and_undo_restores_it(self):
        """"Đây là logo nền tảng — làm mờ & nhớ" (batch 4a): BLUR + flag, no region needed, undo keeps it."""
        source = "\n".join(_js_function(self.page, name) for name in (
            "decide", "undo", "pushUndo", "applyLocalDecision", "applyLocalClear", "platformEligible",
            "studioEligible", "platformRemembered", "platformHtml", "platformNote", "decisionLabel"))
        script = (
            "let busy=false,autoNext=false,focusId='e',previousFocusId=null,filter='all',listIds=['e','p','w'];"
            "const undoStack=[],writes=[],alerts=[],sticky=new Set();"
            "globalThis.alert=m=>alerts.push(m);globalThis.confirm=()=>true;"
            "const esc=s=>String(s??'').replace(/</g,'&lt;');const actionName=(x,d)=>d;"
            "const isScene=()=>false,isLogoItem=()=>true,momentsOf=()=>[],isAdvisoryItem=()=>false;"
            "const refuseWhileExporting=()=>false,syncLocalCounts=()=>{},afterLocalChange=()=>{},updateNavState=()=>{},"
            "updateListStatuses=()=>{},updateHeader=()=>{},renderFocus=()=>{},renderSide=()=>{},"
            "advisoryUndoMessage=()=>'';const enqueueWrite=(kind,body)=>writes.push([kind,JSON.parse(JSON.stringify(body))]);"
            "const box={x:470,y:179,width:317,height:168};"
            "const ending={id:'e',category:'visual_logo',candidate_type:'ending_boundary',suggested_region_source_pixels:null,"
            "decision:null,start_seconds:2697.68,end_seconds:2703.68};"
            "const card={id:'p',category:'visual_logo',candidate_type:'platform_logo',suggested_region_source_pixels:box,decision:null};"
            "const track={id:'w',category:'visual_logo',candidate_type:'persistent_overlay',suggested_region_source_pixels:box,decision:null};"
            "let queue={items:[ending,card,track]};const itemMap=new Map(queue.items.map(x=>[x.id,x]));"
            + source + ";(async()=>{const out={};"
            "out.offer=platformHtml(ending);out.track=platformHtml(track);"
            "await decide('e','BLUR');out.plain=[writes.length,alerts.splice(0)];"
            "await decide('e','BLUR',false,null,false,true);out.first=writes[writes.length-1];"
            "out.local=[ending.decision,ending.platform_logo_memory,platformNote(ending,platformRemembered(ending))];"
            # The server's answer: the derived logo region and the summary.
            "Object.assign(ending,{decision_region_source_pixels:{x:374,y:203,width:541,height:108},"
            "platform_logo_memory:{remembered:true,logo_frames:79,platform:{key:'iqiyi',name:'iQIYI'}}});"
            "out.label=decisionLabel(ending);out.shown=platformHtml(ending);"
            "await decide('e','KEEP');out.keep=[writes[writes.length-1],'platform_logo_memory' in ending];"
            "undo();out.undo=[writes[writes.length-1],ending.decision,ending.decision_region_source_pixels,"
            "ending.platform_logo_memory];undo();out.cleared=[writes[writes.length-1],'platform_logo_memory' in ending];"
            "await decide('p','BLUR',false,null,false,true);out.card=[writes[writes.length-1],card.decision_region_source_pixels];"
            "await decide('w','BLUR',false,null,false,true);out.trackWrite=writes[writes.length-1];"
            "out.alerts=alerts;console.log(JSON.stringify(out));})();"
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
                                timeout=60, check=True)
        out = json.loads(result.stdout)
        self.assertIn('data-act="platform"', out["offer"])
        self.assertIn("Đây là logo nền tảng — làm mờ &amp; nhớ", out["offer"])
        self.assertIn("không làm mờ cả khung", out["offer"])
        self.assertEqual(out["track"], "", "a watermark track is not a platform ident")
        # Without the flag a region-less card still needs "Làm mờ cả cảnh".
        self.assertEqual(out["plain"], [0, ["Mục này chưa có vùng được định vị; hãy chọn Làm mờ cả cảnh."]])
        flagged = {"id": "e", "decision": "BLUR", "full_frame": False, "note": None, "remember_platform_logo": True}
        self.assertEqual(out["first"], ["decision", flagged])
        self.assertEqual(out["local"], ["BLUR", {"remembered": True}, ""], "nothing is claimed before the server answers")
        self.assertEqual(out["label"], "Làm mờ logo · đã nhớ là logo nền tảng iQIYI")
        self.assertIn("✓ Đã nhớ là logo nền tảng (làm mờ)", out["shown"])
        self.assertIn('aria-pressed="true"', out["shown"])
        self.assertIn("Đã nhớ logo nền tảng iQIYI: 79 khung có logo trên nền tối.", out["shown"])
        self.assertEqual(out["keep"], [["decision", {"id": "e", "decision": "KEEP", "full_frame": False, "note": None}],
                                       False])
        # Undo restores BLUR and asks the server to remember again (it re-derives the region).
        self.assertEqual(out["undo"][0], ["decision", flagged])
        self.assertEqual(out["undo"][1:], ["BLUR", {"x": 374, "y": 203, "width": 541, "height": 108},
                                           {"remembered": True}])
        self.assertEqual(out["cleared"], [["clear", {"id": "e"}], False])
        self.assertEqual(out["card"], [["decision", {"id": "p", "decision": "BLUR", "full_frame": False, "note": None,
                                                     "remember_platform_logo": True}],
                                       {"x": 470, "y": 179, "width": 317, "height": 168}])
        self.assertNotIn("remember_platform_logo", out["trackWrite"][1])
        self.assertEqual(out["alerts"], [])
        self.assertIn("else if(act==='platform'){if(!platformRemembered(x))decide(x.id,'BLUR',false,null,false,true);}",
                      self.page)
        self.assertIn('body.export-locked [data-act="platform"]', self.page)
        self.assertIn("remember_platform_logo=body.get(\"remember_platform_logo\") is True",
                      Path(sys.modules["biliflow.review_workflow"].__file__).read_text(encoding="utf-8"))

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

    def test_studio_mask_and_frame_strings(self):
        for fragment in (
            "Sẽ bỏ qua ${blurred.length} vùng watermark bạn đã chọn làm mờ",
            "Sẽ nhớ mọi khung hình trong đoạn", "Đã nhớ ${Number(m.frames)} khung trong đoạn",
            "chưa được chọn Làm mờ", "với một khung bất kỳ của logo này",
            "nằm ngoài vùng watermark đã làm mờ; khi đó thẻ vẫn ở danh sách chính",
            "Đang bỏ qua ${m.ignored_regions.length} vùng watermark đã làm mờ.",
            "Đã cập nhật logo hãng phim đã nhớ theo vùng watermark bạn vừa chọn.",
            "Vùng watermark đã làm mờ quá lớn (trên 20% khung hình) nên không bỏ qua — logo được nhớ nguyên ảnh.",
            " (đã bỏ qua vùng watermark đã làm mờ)", "bỏ qua watermark đã làm mờ",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.page)
        self.assertIn("${studioMaskNote(x)}", _js_function(self.page, "studioHtml"))
        self.assertNotIn("studioOverlayWarning", self.page)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_studio_mask_note_counts_only_blurred_overlay_cards(self):
        source = "\n".join(_js_function(self.page, name) for name in (
            "studioRemembered", "studioMaskNote", "studioFramesNote", "studioCompareLine", "decisionLabel",
            "readingLabel", "thumbTime"))
        script = (
            "const esc=s=>String(s??'').replace(/</g,'&lt;');const actionName=(x,d)=>d;"
            "const isScene=()=>false,isLogoItem=()=>true,momentsOf=()=>[];"
            "const mmss=s=>{const v=Math.max(0,Math.floor(Number(s)||0));return `${Math.floor(v/60)}:${String(v%60).padStart(2,'0')}`;};"
            "const mmssTenth=s=>String(s);const span=x=>`${mmss(x.start_seconds)}–${mmss(x.end_seconds)}`;"
            "const wm=(id,decision,extra)=>Object.assign({id,category:'text',candidate_type:'persistent_overlay',"
            "start_seconds:0,end_seconds:2607,decision,labels:['Motchillv.ph']},extra||{});"
            "let queue={items:[wm('a','BLUR'),wm('b','BLUR',{labels:['PHIM DUOC CAP NHAT']}),wm('c','KEEP',{start_seconds:3000}),"
            "wm('d','BLUR',{start_seconds:3000})]};"
            "const card={id:'x',category:'visual_logo',start_seconds:0,end_seconds:5,preview_images:['t/logo-0001-0.250s.jpg']};"
            + source + ";const out=[studioMaskNote(card)];"
            "queue={items:[wm('a','BLUR'),wm('c',null)]};out.push(studioMaskNote(card));"
            "queue={items:[wm('c','CUT'),wm('e','BLUR',{start_seconds:3000})]};out.push(studioMaskNote(card));"
            "const kept=m=>Object.assign({},card,{decision:'KEEP',studio_logo_memory:Object.assign({remembered:true},m)});"
            "out.push(studioMaskNote(kept({frames:111,frames_source:'source_video',ignored_regions:[{item_id:'a'},{item_id:'b'}]})));"
            "out.push(studioMaskNote(kept({frames:111,frames_source:'source_video',ignored_regions:[],mask_refused:true})));"
            "out.push(studioMaskNote(kept({frames:111,frames_source:'source_video',ignored_regions:[{item_id:'a'}],mask_updated_at:'t'})));"
            "out.push(studioFramesNote(kept({frames:111,frames_source:'source_video'})));"
            "out.push(studioFramesNote(kept({frames:1,frames_source:'preview_only'})));"
            "out.push(studioFramesNote(kept({remembered:true})));"
            "out.push(decisionLabel(kept({frames:111,frames_source:'source_video',ignored_regions:[{item_id:'a'}]})));"
            "out.push(decisionLabel(kept({frames:1})));"
            "out.push(studioCompareLine(Object.assign({},card,{studio_logo_compared:{records:2,best_similarity:0.9,"
            "best_cell_difference:52,masked_regions:2}})));"
            "out.push(studioFramesNote(kept({frames:0,frames_source:'source_video',frames_missing:true})));"
            "console.log(JSON.stringify(out));"
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
                                timeout=60, check=True)
        (both, mixed, cut, ignoring, refused, refreshed, frames, preview_only, pending, label, legacy_label,
         compared, missing) = json.loads(result.stdout)
        self.assertIn("Sẽ bỏ qua 2 vùng watermark bạn đã chọn làm mờ (Motchillv.ph, PHIM DUOC CAP NHAT)", both)
        self.assertNotIn("chưa được chọn Làm mờ", both, "cards outside the window do not count")
        self.assertIn("Sẽ bỏ qua 1 vùng watermark", mixed)
        self.assertIn("chưa được chọn Làm mờ", mixed)
        self.assertNotIn("Sẽ bỏ qua", cut, "a CUT or KEEP watermark is not ignored")
        self.assertIn("chưa được chọn Làm mờ", cut)
        self.assertIn("Đang bỏ qua 2 vùng watermark đã làm mờ.", ignoring)
        self.assertIn("quá lớn (trên 20% khung hình)", refused)
        self.assertIn("Đã cập nhật logo hãng phim đã nhớ theo vùng watermark bạn vừa chọn.", refreshed)
        self.assertEqual(frames, " Đã nhớ 111 khung trong đoạn 0:00–0:05.")
        self.assertIn("Chỉ nhớ 1 ảnh xem trước (không đọc được video gốc), không phải cả đoạn 0:00–0:05", preview_only)
        self.assertEqual(pending, "", "nothing is claimed before the server answers")
        self.assertEqual(label, "Giữ nguyên · đã nhớ là logo hãng phim (111 khung, bỏ qua watermark đã làm mờ)")
        self.assertEqual(legacy_label, "Giữ nguyên · đã nhớ là logo hãng phim (1 khung)")
        self.assertTrue(compared.endswith("— chưa khớp (đã bỏ qua vùng watermark đã làm mờ)</small>"), compared)
        self.assertIn("Không còn ảnh khung hình đã nhớ", missing)
        self.assertIn("tạm thời không khớp thẻ nào", missing)

if __name__ == "__main__":
    unittest.main()
