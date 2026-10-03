"""Display-only evidence for whole-scene logo cards (job 42 "Nhất Âu Xuân - Tập 12", 2026-10-02).

The forced opening card (``opening_boundary``) showed a "LOGO" pill and no box
although the model answered NO. These tests cover the stored display fields
(raw model answer, evidence boxes, studio-memory comparison) and the page
helpers that explain them. None of it may change routing or suggestions.
"""
import copy
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import cv2
import numpy as np

from biliflow.brand_memory import compare_studio_logo, remember_studio_logo
from biliflow.platform_cards import ENDING_REASON, ensure_forced_ending_card
from biliflow.review_workflow import (
    _evidence_labels,
    _interactive_html,
    build_review_queue,
    link_full_scene_logo_evidence,
    route_confirmed_studio_logos,
)

DURATION = 2607.0
DISPLAY_FIELDS = ("evidence_regions", "evidence_frame_size", "studio_logo_compared")
# Troy's whole-film XEMBZ.NET card: scanner labels first, then readings and prompts.
TROY_OWNER_LABELS = [
    "Persistent external logo / watermark", "Visual brand/logo candidate", "</s>XEMBZ.NET",
    "a company logo", "channel watermark sponsor logo promotional badge q", "XEMBZ NET",
]


def _js_function(page, name):
    match = re.search(rf"(?:async )?function {re.escape(name)}\(", page)
    if match is None:
        raise AssertionError(f"function {name} is missing from the review page")
    start = page.index("{", match.end())
    depth = 0
    for index in range(start, len(page)):
        depth += {"{": 1, "}": -1}.get(page[index], 0)
        if depth == 0:
            return page[match.start():index + 1]
    raise AssertionError(name)


def _without_display_fields(value):
    value = copy.deepcopy(value)
    for item in value:
        for key in DISPLAY_FIELDS:
            item.pop(key, None)
        for key in ("vlm_answer", "promoted_from_rejected_boundary", "vlm_scene"):
            (item.get("model_evidence") or {}).pop(key, None)
    return value


def _ident_image(variant=0):
    image = np.zeros((180, 320, 3), dtype=np.uint8)
    if variant == 0:
        cv2.circle(image, (160, 90), 55, (230, 200, 60), -1)
        cv2.rectangle(image, (120, 80), (200, 100), (20, 20, 20), -1)
    else:
        for x in range(0, 320, 40):
            cv2.rectangle(image, (x, 0), (x + 18, 180), (200, 200, 200), -1)
    return image


def _write_rgb(path, image):
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    path.write_bytes(encoded.tobytes())


class OpeningCardQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("reports", "work", "input"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "source.mp4"
        self.source.write_bytes(b"source")

    def tearDown(self):
        self.temporary.cleanup()

    def _visual_report(self, intervals, rejected=(), **extra):
        directory = self.root / "reports" / "job" / "visual-logo"
        (directory / "thumbnails").mkdir(parents=True)
        for index, interval in enumerate([*intervals, *rejected]):
            name = f"thumbnails/logo-{index:04d}-{float(interval['start_seconds']) + 0.25:.3f}s.jpg"
            (directory / name).write_bytes(b"image")
            interval.setdefault("strongest_frame", name)
            interval.setdefault("audit_frame", name)
        path = directory / "scan.json"
        path.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "scan_type": "visual_logo", "input": str(self.source),
            "duration_seconds": DURATION, "source_size": [1280, 534],
            "intervals": intervals, "rejected_windows": list(rejected), **extra,
        }), encoding="utf-8")
        return path

    def _text_report(self, tracks):
        directory = self.root / "reports" / "job" / "text"
        directory.mkdir(parents=True)
        path = directory / "text-scan.json"
        path.write_text(json.dumps({
            "status": "REVIEW_REQUIRED", "input": str(self.source), "duration_seconds": DURATION,
            "scan_start_seconds": 0.0, "scan_duration_seconds": DURATION, "sample_every_seconds": 3.0,
            "source_size": [1280, 534], "analysis_size": [960, 400], "tracks": tracks,
        }, ensure_ascii=False), encoding="utf-8")
        return path

    def _build(self, *reports):
        return build_review_queue(
            project_root=self.root, report_paths=list(reports),
            queue_path=self.root / "reports" / "job" / "review-queue.json",
            use_studio_logo_memory=False,
        )

    def test_forced_opening_card_keeps_the_raw_no_answer_and_its_routed_state(self):
        report = self._visual_report(
            [{
                "start_seconds": 0.0, "end_seconds": 5.0, "max_score": 1.0, "priority": "context",
                "candidate_type": "opening_boundary", "predicted_label": "Opening boundary review",
                "reason": "The first video window is retained once so external intros are not silently missed",
                "visual_logo_confirmation": {
                    "state": "UNCERTAIN", "answer": "NO", "confirmation_source": "qwen_local",
                    "promoted_from_rejected_boundary": True,
                },
            }],
            rejected=[{
                "start_seconds": 5.0, "end_seconds": 10.0, "max_score": 0.4,
                "visual_logo_confirmation": {"state": "REJECTED", "answer": "NO " + "x" * 200},
            }],
        )
        queue = self._build(report)
        [card] = queue["items"]
        self.assertEqual(card["candidate_type"], "opening_boundary")
        self.assertIsNone(card["suggested_region_source_pixels"])
        self.assertIsNone(card["suggested_decision"])
        self.assertEqual(card["model_evidence"]["vlm_confirmation"], "UNCERTAIN", "the routed state is unchanged")
        self.assertEqual(card["model_evidence"]["vlm_answer"], "NO")
        self.assertIs(card["model_evidence"]["promoted_from_rejected_boundary"], True)
        self.assertNotIn("evidence_regions", card)
        [advisory] = queue["advisory_items"]
        self.assertEqual(advisory["model_evidence"]["vlm_confirmation"], "REJECTED")
        self.assertEqual(len(advisory["model_evidence"]["vlm_answer"]), 80)
        self.assertNotIn("promoted_from_rejected_boundary", advisory["model_evidence"])

    def test_scene_card_keeps_its_dropped_box_as_evidence_and_links_the_watermark_card(self):
        # Tập 10: the opening card's only proposal is the Motchillv.ph watermark,
        # which the fixed-text rule turns into its own whole-film card.
        visual = self._visual_report([{
            "start_seconds": 0.0, "end_seconds": 5.0, "max_score": 1.0,
            "visual_logo_confirmation": {
                "state": "CONFIRMED", "answer": "YES", "confirmation_source": "qwen_local",
                "boundary_window": True, "boundary_scene_context": {"state": "UNCERTAIN"},
                "features": {"full_frame_score": 1.0},
            },
            "region_localization": {"frame_size": [1280, 534], "proposals": [{
                "blur_region_px": [46, 40, 188, 47], "sources": ["grounding", "ocr"],
                "labels": ["a company logo", "platform logo", "studio logo", "watermark", "</s>Mothilly.ph"],
                "region_classification": "unknown",
            }]},
        }])
        text = self._text_report([{
            "track_id": 1, "start_seconds": 0.0, "end_seconds": DURATION,
            "recommended_blur_end_seconds": DURATION, "observations": 865, "zone": "top-left",
            "persistent": True, "review_priority": "low", "max_confidence": 0.5,
            "union_box": [34, 25, 210, 78], "sample_text": ["Motchillv.ph"], "ad_probability": 0.158,
            "review_candidate": False, "routing": "LIKELY_SCENE_TEXT", "reason": "x",
        }])
        queue = self._build(visual, text)
        opening = next(item for item in queue["items"] if item["candidate_type"] == "opening_promotion")
        watermark = next(item for item in queue["items"] if item["candidate_type"] == "persistent_overlay")
        self.assertEqual(watermark["category"], "text")
        self.assertIsNone(opening["suggested_region_source_pixels"])
        self.assertEqual(opening["evidence_frame_size"], [1280, 534])
        [box] = opening["evidence_regions"]
        self.assertEqual((box["x"], box["y"], box["width"], box["height"]), (46, 40, 188, 47))
        self.assertEqual(box["sources"], ["grounding", "ocr"])
        self.assertEqual(box["labels"], ["Mothilly.ph", "a company logo", "platform logo"])
        self.assertEqual(box["covered_by"], watermark["id"], "linked after the final ID assignment")
        self.assertTrue(box["covered_by_label"])
        self.assertEqual(opening["model_evidence"]["vlm_answer"], "YES")

    def test_linking_is_display_only_and_needs_overlap_in_time_and_space(self):
        owner = {"id": "wm", "category": "text", "candidate_type": "persistent_overlay",
                 "start_seconds": 10.0, "end_seconds": 100.0, "labels": ["Motchillv.ph"],
                 "suggested_region_source_pixels": {"x": 40, "y": 30, "width": 200, "height": 60},
                 "suggested_decision": "BLUR"}
        card = {"id": "open", "category": "visual_logo", "candidate_type": "opening_promotion",
                "start_seconds": 0.0, "end_seconds": 5.0, "suggested_region_source_pixels": None,
                "suggested_decision": "CUT",
                "evidence_regions": [{"x": 46, "y": 40, "width": 188, "height": 47, "covered_by": "stale"}]}
        before = copy.deepcopy([owner, card])
        link_full_scene_logo_evidence([owner, card])
        self.assertNotIn("covered_by", card["evidence_regions"][0], "a stale link is cleared; no time overlap")
        owner["start_seconds"] = 0.0
        link_full_scene_logo_evidence([owner, card])
        self.assertEqual(card["evidence_regions"][0]["covered_by"], "wm")
        self.assertEqual(card["evidence_regions"][0]["covered_by_label"], "Motchillv.ph")
        owner["suggested_region_source_pixels"] = {"x": 900, "y": 400, "width": 100, "height": 40}
        link_full_scene_logo_evidence([owner, card])
        self.assertNotIn("covered_by", card["evidence_regions"][0])
        owner["suggested_region_source_pixels"] = before[0]["suggested_region_source_pixels"]
        owner["start_seconds"] = 10.0
        self.assertEqual(_without_display_fields([owner, card]), _without_display_fields(before))

    def test_watermark_link_names_the_reading_not_the_scanner_label(self):
        owner = {"id": "wm", "category": "visual_logo", "candidate_type": "persistent_overlay",
                 "start_seconds": 0.0, "end_seconds": 100.0, "labels": list(TROY_OWNER_LABELS),
                 "suggested_region_source_pixels": {"x": 118, "y": 173, "width": 150, "height": 31}}
        card = {"id": "open", "category": "visual_logo", "candidate_type": "opening_promotion",
                "start_seconds": 0.0, "end_seconds": 5.0, "suggested_region_source_pixels": None,
                "evidence_regions": [{"x": 107, "y": 160, "width": 172, "height": 53}]}
        link_full_scene_logo_evidence([owner, card])
        self.assertEqual(card["evidence_regions"][0]["covered_by_label"], "XEMBZ.NET")
        owner["labels"] = ["Persistent external logo / watermark", "a company logo"]
        link_full_scene_logo_evidence([owner, card])
        self.assertEqual(card["evidence_regions"][0]["covered_by_label"], "watermark")
        self.assertEqual(
            _evidence_labels(["Persistent external logo / watermark", "a company logo", "</s>XEMBZ.NET"]),
            ["XEMBZ.NET", "Persistent external logo / watermark", "a company logo"],
        )

    def test_forced_ending_card_when_tail_uncovered(self):
        # Tập 17: the iQIYI outro starts 4.21 s before the end inside a rejected 5 s
        # window, so no window of the scan became a card.
        report = self._visual_report(
            [], rejected=[
                {"start_seconds": DURATION - 12.0, "end_seconds": DURATION - 7.0, "max_score": 0.2,
                 "visual_logo_confirmation": {"state": "REJECTED", "answer": "NO"}},
                {"start_seconds": DURATION - 7.0, "end_seconds": DURATION - 2.0, "max_score": 0.4,
                 "visual_logo_confirmation": {"state": "REJECTED", "answer": "NO"}},
                {"start_seconds": DURATION - 2.0, "end_seconds": DURATION, "max_score": 0.6,
                 "visual_logo_confirmation": {"state": "REJECTED", "answer": "NO " + "x" * 200},
                 "region_localization": {"frame_size": [1280, 534], "proposals": [{
                     "blur_region_px": [518, 190, 154, 132], "sources": ["grounding_dino"],
                     "labels": ["brand logo"], "region_classification": "unknown"}]}},
            ],
            scan_start_seconds=0.0, scan_duration_seconds=DURATION,
        )
        queue = self._build(report)
        [card] = queue["items"]
        self.assertEqual(card["candidate_type"], "ending_boundary")
        self.assertEqual((card["start_seconds"], card["end_seconds"]), (DURATION - 6.0, DURATION))
        self.assertEqual(card["labels"], ["Ending boundary review"])
        self.assertIn("6 giây cuối video", card["reasons"][0])
        self.assertIsNone(card["suggested_region_source_pixels"])
        self.assertIsNone(card["suggested_decision"])
        self.assertEqual(card["priority"], "context")
        self.assertEqual(card["source_candidate_refs"], [])
        self.assertEqual(
            [(window["start_seconds"], window["answer"]) for window in card["model_evidence"]["tail_windows"]],
            [(DURATION - 7.0, "NO"), (DURATION - 2.0, "NO " + "x" * 77)],
        )
        self.assertEqual(len(card["preview_images"]), 2, "no FFmpeg: the tail windows' thumbnails")
        [box] = card["evidence_regions"]
        self.assertEqual((box["x"], box["y"], box["width"], box["height"]), (518, 190, 154, 132))
        self.assertTrue(queue["platform_logos"]["ending_card"])

    def test_no_forced_ending_card_when_a_full_frame_card_covers_the_tail(self):
        end_card = {
            "start_seconds": DURATION - 10.0, "end_seconds": DURATION, "max_score": 0.95,
            "candidate_type": "branded_end_card", "suggested_decision": "CUT",
            "visual_logo_confirmation": {"state": "CONFIRMED", "answer": "YES",
                                         "confirmation_source": "qwen_local"},
        }
        report = self._visual_report([end_card], scan_start_seconds=0.0, scan_duration_seconds=DURATION)
        queue = self._build(report)
        self.assertEqual([item["candidate_type"] for item in queue["items"]], ["branded_end_card"])
        self.assertFalse(queue["platform_logos"]["ending_card"])
        # A scan that stopped before the end never claims the last seconds were shown.
        shutil.rmtree(self.root / "reports" / "job")
        partial = self._visual_report([], scan_start_seconds=0.0, scan_duration_seconds=600.0)
        self.assertEqual(self._build(partial)["items"], [])

    def test_promotion_after_a_no_answer_keeps_the_scene_answer(self):
        # visual_logo_scanner turns a REJECTED logo answer into UNCERTAIN when the
        # boundary prompt says PROMO_FULL_FRAME; the card suggests CUT.
        report = self._visual_report([{
            "start_seconds": 0.0, "end_seconds": 5.0, "max_score": 0.9, "priority": "high",
            "candidate_type": "opening_promotion", "suggested_decision": "CUT",
            "predicted_label": "Full-frame promotional material",
            "reason": "Local visual-language model classified the boundary window as full-frame promotional material",
            "visual_logo_confirmation": {
                "state": "UNCERTAIN", "answer": "NO", "confirmation_source": "qwen_local",
                "boundary_window": True, "features": {"full_frame_score": 1.0},
                "boundary_scene_context": {"state": "PROMO_FULL_FRAME", "answer": "PROMO_FULL_FRAME"},
            },
        }])
        [card] = self._build(report)["items"]
        self.assertEqual(card["suggested_decision"], "CUT")
        self.assertEqual(card["model_evidence"]["vlm_confirmation"], "UNCERTAIN")
        self.assertEqual(card["model_evidence"]["vlm_answer"], "NO")
        self.assertEqual(card["model_evidence"]["vlm_scene"], "PROMO_FULL_FRAME")


class StudioComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        _write_rgb(self.root / "reports" / "x" / "ident.jpg", _ident_image(0))
        _write_rgb(self.root / "reports" / "x" / "other.jpg", _ident_image(1))
        self.records = [remember_studio_logo(
            self.root, {"source": {"sha256": "film"}}, self.item("ident", decision="KEEP"),
            window_text={"covered": True, "texts": []},
        )]

    def tearDown(self):
        self.temporary.cleanup()

    def item(self, name, decision=None):
        return {"id": f"review-{name}", "category": "visual_logo", "candidate_type": "opening_boundary",
                "review_kind": "logo_overlay", "start_seconds": 0.0, "end_seconds": 5.0,
                "suggested_decision": None, "decision": decision, "priority": "context", "labels": [],
                "reasons": [], "suggested_region_source_pixels": None,
                "preview_images": [f"reports/x/{name}.jpg"], "source_candidate_refs": ["reports/x/logo.json#interval:0"]}

    def test_a_picture_miss_records_how_close_it_came(self):
        compared = compare_studio_logo(self.root, self.item("other"), self.records)
        self.assertEqual(compared["records"], 1)
        self.assertEqual(compared["frames"], 1)
        self.assertLess(compared["best_similarity"], 0.95)
        self.assertGreater(compared["best_cell_difference"], 20)
        self.assertEqual((compared["minimum_similarity"], compared["maximum_cell_difference"]), (0.95, 20))
        kept, moved = route_confirmed_studio_logos(
            self.root, [self.item("other")], {}, {"records": self.records})
        self.assertEqual(moved, [])
        self.assertEqual(kept[0]["studio_logo_compared"], compared)
        same = compare_studio_logo(self.root, self.item("ident"), self.records)
        self.assertEqual((same["best_similarity"], same["best_cell_difference"]), (1.0, 0))

    def test_nothing_is_recorded_without_records_or_for_decided_and_boxed_cards(self):
        stale = dict(self.item("other"), studio_logo_compared={"records": 9})
        kept, moved = route_confirmed_studio_logos(self.root, [stale], {}, {"records": []})
        self.assertNotIn("studio_logo_compared", kept[0])
        self.assertIsNone(compare_studio_logo(self.root, self.item("other"), []))
        decided = self.item("other", decision="KEEP")
        boxed = dict(self.item("other"), suggested_region_source_pixels={"x": 1, "y": 1, "width": 9, "height": 9})
        kept, _ = route_confirmed_studio_logos(self.root, [decided, boxed], {}, {"records": self.records})
        self.assertFalse(any("studio_logo_compared" in item for item in kept))

    def test_routing_is_identical_with_and_without_the_comparison(self):
        logo = {"intervals": [{"start_seconds": 0.0, "end_seconds": 5.0}]}
        quiet = {"tracks": [], "scan_start_seconds": 0.0, "scan_duration_seconds": 100.0}
        payloads = {"reports/x/logo.json": logo, "reports/x/text-scan.json": quiet}
        cards = [self.item("other"), self.item("ident"), dict(self.item("other"), id="review-decided", decision="CUT")]
        with mock.patch("biliflow.review_workflow.compare_studio_logo", return_value=None):
            plain = route_confirmed_studio_logos(
                self.root, copy.deepcopy(cards), payloads, {"records": self.records})
        shown = route_confirmed_studio_logos(self.root, copy.deepcopy(cards), payloads, {"records": self.records})
        self.assertEqual([item["id"] for item in shown[1]], ["review-ident"], "the matching ident still moves")
        self.assertEqual(
            [_without_display_fields(part) for part in shown], [_without_display_fields(part) for part in plain])
        self.assertIn("studio_logo_compared", shown[0][0])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class OpeningCardPageRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.page = _interactive_html("token")

    def run_js(self, names, body, prelude=""):
        source = "\n".join(_js_function(self.page, name) for name in names)
        script = (
            "const esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
            "const mmss=s=>{const v=Math.max(0,Math.floor(Number(s)||0));return `${Math.floor(v/60)}:${String(v%60).padStart(2,'0')}`;};"
            "const mmssTenth=s=>{const v=Math.max(0,Number(s)||0),m=Math.floor(v/60);return `${m}:${(v-m*60).toFixed(1).padStart(4,'0')}`;};"
            "const span=x=>`${mmss(x.start_seconds)}–${mmss(x.end_seconds)}`;"
            + prelude + source + ";console.log(JSON.stringify(" + body + "));"
        )
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
                                timeout=60, check=True)
        return json.loads(result.stdout)

    def test_names_of_whole_scene_logo_cards(self):
        names = self.run_js(
            ("isSafety", "catName", "sceneLogo", "hasPlayer"),
            "[catName(boundary),catName(bare),catName(boxed),catName(ident),catName(promo),catName(fight),"
            "[boundary,bare,boxed,ident,promo,fight,track].map(hasPlayer)]",
            "const SAFETY={adult:['18+'],gore:['Máu me'],violence:['Bạo lực']};"
            "const KIND_NAMES={logo_overlay:'Logo',opening_promotion:'Quảng cáo mở đầu'};"
            "const boundary={category:'visual_logo',candidate_type:'opening_boundary',review_kind:'logo_overlay'};"
            "const bare={category:'visual_logo',candidate_type:null,review_kind:'logo_overlay',suggested_region_source_pixels:null};"
            "const boxed={category:'visual_logo',candidate_type:null,review_kind:'logo_overlay',suggested_region_source_pixels:{x:1,y:1,width:5,height:5}};"
            "const ident={category:'visual_logo',candidate_type:'opening_promotion',review_kind:'opening_promotion',opening_ident:true};"
            "const promo={category:'visual_logo',candidate_type:'opening_promotion',review_kind:'opening_promotion'};"
            "const fight={category:'violence'};"
            "const track={category:'visual_logo',candidate_type:'persistent_overlay',suggested_region_source_pixels:{x:1,y:1,width:5,height:5}};",
        )
        self.assertEqual(names[:6], [
            "Kiểm tra đoạn mở đầu", "Logo toàn khung (chưa khoanh vùng)", "Logo", "Logo mở đầu",
            "Quảng cáo mở đầu", "Bạo lực",
        ])
        self.assertEqual(names[6], [True, True, False, True, True, True, False])

    def test_ai_verdict_lines(self):
        no, yes, covered, boxed, legacy_boundary, legacy, rejected, owned = self.run_js(
            ("aiVerdictHtml", "memoryMatch", "memoryBrandName", "boxesFromMemory", "readingLabel"),
            "[aiVerdictHtml(no),aiVerdictHtml(yes),aiVerdictHtml(covered),aiVerdictHtml(boxed),"
            "aiVerdictHtml(legacyBoundary),aiVerdictHtml(legacy),aiVerdictHtml(rejected),aiVerdictHtml(owned)]",
            "const base={category:'visual_logo',start_seconds:0,end_seconds:5,suggested_region_source_pixels:null};"
            "const no=Object.assign({},base,{candidate_type:'opening_boundary',model_evidence:{vlm_confirmation:'UNCERTAIN',vlm_answer:'NO',promoted_from_rejected_boundary:true}});"
            "const yes=Object.assign({},base,{candidate_type:'opening_promotion',model_evidence:{vlm_confirmation:'CONFIRMED',vlm_answer:'YES'}});"
            "const covered=Object.assign({},yes,{evidence_regions:[{x:46,y:40,width:188,height:47,covered_by:'wm',covered_by_label:'<b>Motchillv.ph</b>'}]});"
            "const boxed=Object.assign({},yes,{evidence_regions:[{x:46,y:40,width:188,height:47}]});"
            "const legacyBoundary=Object.assign({},base,{candidate_type:'opening_boundary',model_evidence:{vlm_confirmation:'UNCERTAIN'}});"
            "const legacy=Object.assign({},base,{candidate_type:'opening_promotion',model_evidence:{vlm_confirmation:'CONFIRMED'}});"
            "const rejected=Object.assign({},base,{start_seconds:5,end_seconds:10,candidate_type:'rejected_logo_candidate',model_evidence:{vlm_confirmation:'REJECTED',vlm_answer:'NO'}});"
            "const owned=Object.assign({},yes,{suggested_region_source_pixels:{x:1,y:1,width:5,height:5}});",
        )
        self.assertIn("AI trả lời KHÔNG thấy logo hay chữ quảng cáo trong 0:00–0:05.", no)
        self.assertIn("BiliFlow luôn đưa 5 giây đầu video ra một lần để bạn tự kiểm tra intro ngoài phim", no)
        self.assertIn("thẻ này không khoanh vùng logo nào — chọn Giữ nguyên nếu là nội dung phim/logo hãng, "
                      "Cắt cảnh nếu là intro ngoài.", no)
        self.assertIn("AI trả lời CÓ thấy logo/thương hiệu trong khung nhưng không định vị được vị trí — "
                      "quyết định áp dụng cho cả cảnh.", yes)
        self.assertNotIn("watermark", yes)
        self.assertIn("Vùng logo duy nhất AI khoanh ở đoạn này là watermark &lt;b&gt;Motchillv.ph&lt;/b&gt; — "
                      "đã có thẻ riêng; thẻ này chỉ hỏi về cả đoạn.", covered)
        self.assertIn("khung vàng là vùng AI khoanh, chỉ để tham khảo", boxed)
        self.assertIn("Thẻ cũ: chưa lưu câu trả lời gốc của AI.", legacy_boundary)
        self.assertIn("BiliFlow luôn đưa 5 giây đầu video", legacy_boundary)
        self.assertNotIn("KHÔNG thấy", legacy_boundary, "a legacy card never claims an answer it did not store")
        self.assertIn("Thẻ cũ: chưa lưu câu trả lời gốc của AI.", legacy)
        self.assertNotIn("5 giây đầu", legacy)
        self.assertIn("AI trả lời KHÔNG thấy logo hay chữ quảng cáo trong 0:05–0:10.", rejected)
        self.assertNotIn("5 giây đầu", rejected)
        self.assertEqual(owned, "")

    # ------------------------------------------- platform and ending cards (batch 4a, 2026-10-03)
    PLATFORM_CARDS = (
        "const box={x:470,y:179,width:317,height:168};"
        "const iqiyi={id:'p',category:'visual_logo',candidate_type:'platform_logo',review_kind:'platform_logo',"
        "start_seconds:8,end_seconds:13.52,suggested_region_source_pixels:box,platform_logo:{key:'iqiyi',name:'iQIYI',"
        "snap:{method:'dark_run'},detections:[{kind:'ocr_text',text:'iOlYI',observed_seconds:10.5,label_seconds:9}]}};"
        "const unnamed=Object.assign({},iqiyi,{platform_logo:undefined});"
        "const remembered=Object.assign({},iqiyi,{platform_logo:{key:'iqiyi',name:'iQIYI',snap:{method:'hard_cuts'},"
        "detections:[{kind:'platform_memory',similarity:0.996},{kind:'platform_memory',similarity:0.97}]}});"
        "const model=Object.assign({},iqiyi,{platform_logo:{key:'iqiyi',name:'<i>iQIYI</i>',snap:{method:'dark_run'},"
        "detections:[{kind:'vlm_label',text:'iQIYI',observed_seconds:2701}]}});"
        "const ending={id:'e',category:'visual_logo',candidate_type:'ending_boundary',review_kind:'logo_overlay',"
        "start_seconds:2697.68,end_seconds:2703.68,suggested_region_source_pixels:null,labels:['Ending boundary review'],"
        "model_evidence:{vlm_confirmation:null,vlm_source:'forced_ending',region_sources:[],tail_windows:["
        "{start_seconds:2690,end_seconds:2695,state:'rejected',answer:'No.'},"
        "{start_seconds:2695,end_seconds:2700,state:'interval',answer:''},"
        "{start_seconds:2700,end_seconds:2703.68,state:'interval',answer:'<b>Yes</b>, iQIYI'}]}};"
        "const boxedEnding=Object.assign({},ending,{model_evidence:{vlm_source:'forced_ending',tail_windows:[]},"
        "evidence_regions:[{x:374,y:203,width:541,height:108}]});"
        "const linked=Object.assign({},ending,{suggested_decision:'KEEP',platform_logo_link:{card_id:'p',platform:'iQIYI',coverage:0.67}});"
        "const duplicate={id:'d',category:'visual_logo',advisory:true,covered_by:'p',covered_by_label:'Logo nền tảng <iQIYI>'};"
    )

    def test_names_of_platform_and_ending_cards(self):
        names = self.run_js(
            ("isSafety", "catName", "sceneLogo", "hasPlayer", "readingLabel", "viText"),
            "[catName(iqiyi),catName(unnamed),catName(ending),[iqiyi,ending].map(hasPlayer),"
            "readingLabel(ending,'logo/watermark'),readingLabel(iqiyi,'x'),viText('Ending boundary review')]",
            "const SAFETY={adult:['18+']};const KIND_NAMES={logo_overlay:'Logo',platform_logo:'Logo nền tảng'};"
            + self.PLATFORM_CARDS,
        )
        self.assertEqual(names[:3], ["Logo nền tảng iQIYI", "Logo nền tảng", "Kiểm tra đoạn kết"])
        # The ending card asks about the whole scene (player); a platform card has its red box.
        self.assertEqual(names[3], [False, True])
        self.assertEqual(names[4], "logo/watermark", "the scanner label is never shown as a reading")
        self.assertEqual(names[5], "x", "a card without readings falls back")
        self.assertEqual(names[6], "Kiểm tra đoạn kết")
        self.assertIn("platform_logo:'Logo nền tảng'", self.page)

    def test_platform_and_ending_verdict_lines(self):
        ending, boxed, iqiyi, remembered, model, owned, other, linked, duplicate = self.run_js(
            ("aiVerdictHtml", "endingVerdictHtml", "platformVerdictHtml", "memoryMatch", "memoryBrandName",
             "boxesFromMemory", "readingLabel", "suggestionLine"),
            "[aiVerdictHtml(ending),aiVerdictHtml(boxedEnding),platformVerdictHtml(iqiyi),"
            "platformVerdictHtml(remembered),platformVerdictHtml(model),aiVerdictHtml(iqiyi),"
            "platformVerdictHtml(ending),suggestionLine(linked),suggestionLine(duplicate)]",
            "const actionName=(x,d)=>({KEEP:'Giữ nguyên',BLUR:'Làm mờ logo'}[d]||d);"
            "const studioWithheldLine=()=>'',studioBlockedLine=()=>'';" + self.PLATFORM_CARDS,
        )
        # The page repeats the card's reason, word for word (6.0 s = FORCED_ENDING_SECONDS).
        self.assertIn(ENDING_REASON.rstrip("."), ending)
        self.assertIn("chọn Giữ nguyên nếu là nội dung phim, Cắt cảnh nếu là đoạn kết ngoài phim", ending)
        self.assertIn("bấm “Đây là logo nền tảng — làm mờ &amp; nhớ”", ending)
        self.assertIn("AI (Qwen) ở các cửa sổ cuối: 44:50–44:55 “No.”; 45:00–45:03 “&lt;b&gt;Yes&lt;/b&gt;, iQIYI”.",
                      ending)
        for wrong in ("Thẻ cũ", "5 giây đầu", "Khung vàng"):
            self.assertNotIn(wrong, ending)
        self.assertIn("Khung vàng là vùng AI định vị ở cuối video, chỉ để tham khảo.", boxed)
        self.assertNotIn("AI (Qwen)", boxed)
        self.assertEqual(iqiyi, '<div class="ai-verdict">OCR đọc “iOlYI” lúc 0:10 — logo nền tảng iQIYI; đề xuất '
                                'Làm mờ vùng logo 0:08–0:13, không cắt cảnh — khung đỏ là vùng sẽ làm mờ.</div>')
        self.assertIn("Khớp logo nền tảng iQIYI bạn đã nhớ (giống 100%)", remembered)
        self.assertIn("Không thấy đoạn màn hình đen quanh logo", remembered)
        self.assertIn("Mô hình logo đọc “iQIYI” — logo nền tảng &lt;i&gt;iQIYI&lt;/i&gt;", model)
        self.assertEqual((owned, other), ("", ""), "a platform card has its own line; other cards get none")
        self.assertIn("Đề xuất: Giữ nguyên", linked)
        self.assertIn("Logo nền tảng iQIYI ở đoạn này đã có thẻ riêng làm mờ vùng logo — thẻ này chỉ hỏi về cả cảnh",
                      linked)
        self.assertIn("Trùng thẻ “Logo nền tảng &lt;iQIYI&gt;” ở danh sách chính", duplicate)
        self.assertIn("Ứng viên phụ — không chặn xuất", duplicate)
        side = _js_function(self.page, "adSide")
        self.assertIn("${platformVerdictHtml(x)}${suggestionLine(x)}", side)
        self.assertIn("${studioHtml(x)}${platformHtml(x)}${chosenLine(x)}", side)
        for term in ("quảng bá", "promotional", "promotion", "advertisement", "branded intro"):
            self.assertNotIn(term, ending + iqiyi + remembered)

    def test_ending_verdict_reads_the_card_python_builds(self):
        report = "reports/job/visual-logo/scan-localized.json"
        payload = {
            "scan_type": "visual_logo", "scan_start_seconds": 0.0, "scan_duration_seconds": 2703.778,
            "intervals": [{"start_seconds": 2700.0, "end_seconds": 2703.778,
                           "visual_logo_confirmation": {"state": "CONFIRMED", "answer": "Yes, iQIYI"}}],
            "rejected_windows": [{"start_seconds": 2695.0, "end_seconds": 2700.0,
                                  "visual_logo_confirmation": {"state": "REJECTED", "answer": "No."}}],
        }
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            card = ensure_forced_ending_card(root, [], {report: payload}, duration=2703.778,
                                             queue_dir=root / "reports" / "job")
        line = self.run_js(("endingVerdictHtml",), "endingVerdictHtml(card)", f"const card={json.dumps(card)};")
        self.assertIn("AI (Qwen) ở các cửa sổ cuối: 44:55–45:00 “No.”; 45:00–45:03 “Yes, iQIYI”.", line)

    def test_verdict_stays_out_of_suggestions_and_queue_text(self):
        self.assertNotIn("aiVerdictHtml", _js_function(self.page, "suggestionLine"))
        self.assertIn("aiVerdictHtml(x)", _js_function(self.page, "adSide"))
        self.assertIn("Chỉ nội dung nằm trong khung đỏ này đang được phân loại", self.page)
        self.assertIn("${studioTextsNote(x)}", _js_function(self.page, "studioHtml"))

    def test_evidence_boxes_and_caption_on_the_preview(self):
        boxed, bare, text = self.run_js(
            ("evidenceMediaHtml", "boxesFromMemory", "readingLabel"),
            "[evidenceMediaHtml(boxed,['a.jpg'],src),evidenceMediaHtml(bare,['a.jpg'],src),evidenceMediaHtml(text,['a.jpg'],src)]",
            "const src=p=>'/media/'+encodeURIComponent(p);"
            "const boxed={category:'visual_logo',evidence_frame_size:[1280,534],evidence_regions:[{x:46,y:40,width:188,height:47,covered_by:'wm'}]};"
            "const bare={category:'visual_logo'};const text={category:'text'};",
        )
        self.assertIn("Khung vàng: vùng AI định vị, chỉ để tham khảo — không phải vùng sẽ làm mờ · "
                      "watermark đã có thẻ riêng", boxed)
        self.assertIn('class="evidence-frame"', boxed)
        self.assertIn('data-sw="1280" data-sh="534"', boxed)
        self.assertIn("&quot;x&quot;:46", boxed)
        self.assertIn("Không có khung: AI không định vị vùng logo nào trong ảnh này; thẻ hỏi về cả cảnh.", bare)
        self.assertNotIn("Không có khung", text)
        draw = _js_function(self.page, "drawEvidencePreviews")
        self.assertIn("setLineDash([10,6])", draw)
        self.assertIn("for(const b of boxes.filter(b=>!b.a))", draw, "evidence boxes stay dashed amber")
        self.assertIn("for(const b of boxes.filter(b=>b.a))", draw, "only an approved region is red")

    def test_technical_details_show_labels_reasons_and_a_readable_ai_line(self):
        labels, ai, legacy, text = self.run_js(
            ("viText", "labelsReasonsHtml", "aiModelLine", "memoryMatch", "memoryBrandName", "readingLabel"),
            "[labelsReasonsHtml(x),aiModelLine(x),aiModelLine(old),aiModelLine({category:'text',model_evidence:{}})]",
            "const x={category:'visual_logo',max_score:1,labels:['Opening boundary review'],"
            "reasons:['The first video window is retained once so external intros are not silently missed'],"
            "model_evidence:{vlm_confirmation:'UNCERTAIN',vlm_answer:'NO',promoted_from_rejected_boundary:true}};"
            "const old={category:'visual_logo',max_score:0.5,model_evidence:{vlm_confirmation:'CONFIRMED'}};",
        )
        self.assertIn("Nhãn: Kiểm tra đoạn mở đầu", labels)
        self.assertIn("Lý do: Luôn giữ 5 giây đầu video một lần để không bỏ sót intro ngoài phim", labels)
        self.assertIn("AI (Qwen): trả lời “NO”; trạng thái UNCERTAIN", ai)
        self.assertIn("điểm 1.000 là điểm hình học, không phải độ tin cậy AI", ai)
        self.assertIn("thẻ cũ", legacy)
        self.assertEqual(text, "")

    def test_studio_memory_comparison_and_overlay_warning(self):
        miss, matched, decided, warning, clean, covered, frames = self.run_js(
            ("studioCompareLine", "studioRemembered", "studioMaskNote", "studioFramesNote", "thumbTime",
             "readingLabel"),
            "[studioCompareLine(card),studioCompareLine(Object.assign({},card,{studio_logo_match:{similarity:1}})),"
            "studioCompareLine(Object.assign({},card,{decision:'KEEP'})),studioMaskNote(card),"
            "studioMaskNote(Object.assign({},card,{start_seconds:3000,end_seconds:3005})),"
            "studioMaskNote(Object.assign({},card,{id:'c2',start_seconds:3000,end_seconds:3005,"
            "evidence_regions:[{covered_by:'wm'}]})),studioFramesNote(card)]",
            "const queue={items:[{id:'wm',candidate_type:'persistent_overlay',start_seconds:0,end_seconds:2607}]};"
            "const card={id:'c',category:'visual_logo',start_seconds:0,end_seconds:5,"
            "preview_images:['reports/x/thumbnails/logo-0001-0.250s.jpg'],"
            "studio_logo_compared:{records:2,best_similarity:0.656,best_cell_difference:195,minimum_similarity:0.95,maximum_cell_difference:20}};",
        )
        self.assertIn("Đã so với 2 logo hãng phim bạn đã nhớ: giống nhất 65% (cần ≥ 95%) — chưa khớp", miss)
        self.assertEqual((matched, decided), ("", ""))
        self.assertIn("Ảnh đang có watermark/lớp phủ chưa được chọn Làm mờ", warning)
        self.assertIn("BiliFlow sẽ tự bỏ qua vùng đó trong logo đã nhớ", warning)
        self.assertEqual(clean, "")
        self.assertIn("watermark/lớp phủ", covered)
        self.assertIn("Sẽ nhớ mọi khung hình trong đoạn 0:00–0:05 (giải mã lại từ video gốc đúng như lúc quét, "
                      "tối đa 250 khung); nếu không đọc được video gốc thì chỉ nhớ 1 ảnh xem trước (lúc 0:00.3).",
                      frames)

    def test_brand_memory_match_is_not_presented_as_an_ai_answer(self):
        # Conan 21 end card: vlm_source approved_brand_memory, box from brand memory.
        verdict, line, legend = self.run_js(
            ("aiVerdictHtml", "aiModelLine", "evidenceMediaHtml", "memoryMatch", "memoryBrandName",
             "boxesFromMemory", "readingLabel"),
            "[aiVerdictHtml(card),aiModelLine(card),evidenceMediaHtml(card,['a.jpg'],src)]",
            "const src=p=>'/media/'+encodeURIComponent(p);"
            "const card={category:'visual_logo',candidate_type:'branded_end_card',start_seconds:6900,end_seconds:6920,"
            "suggested_region_source_pixels:null,max_score:1,evidence_frame_size:[1280,534],"
            "model_evidence:{vlm_confirmation:'CONFIRMED',vlm_source:'approved_brand_memory',"
            "vlm_answer:'MEMORY_MATCH | Persistent external logo / watermark'},"
            "evidence_regions:[{x:10,y:10,width:200,height:40,sources:['brand_memory'],covered_by:'wm',"
            "covered_by_label:'Phim@online.net'}]};",
        )
        self.assertIn("Khớp hình một logo thương hiệu bạn đã duyệt trước đó — không phải câu trả lời của AI", verdict)
        self.assertIn("Vùng logo duy nhất bộ nhớ thương hiệu khoanh ở đoạn này là watermark Phim@online.net", verdict)
        self.assertNotIn("AI trả lời", verdict)
        self.assertNotIn("Persistent external", verdict)
        self.assertIn("Bộ nhớ thương hiệu: khớp hình logo bạn đã duyệt trước đó, AI không được hỏi về logo", line)
        self.assertNotIn("AI (Qwen): trả lời", line)
        self.assertNotIn("là điểm hình học", line)
        self.assertIn("Khung vàng: vùng được định vị (bộ nhớ thương hiệu)", legend)
        self.assertNotIn("vùng AI định vị", legend)

    def test_promotion_after_a_no_answer_reads_as_a_promotion(self):
        verdict, line, names = self.run_js(
            ("aiVerdictHtml", "aiModelLine", "viText", "memoryMatch", "memoryBrandName", "boxesFromMemory",
             "readingLabel"),
            "[aiVerdictHtml(card),aiModelLine(card),"
            "[card.labels[0],card.reasons[0],'Known approved external brand'].map(viText)]",
            "const card={category:'visual_logo',candidate_type:'opening_promotion',start_seconds:0,end_seconds:5,"
            "suggested_region_source_pixels:null,max_score:0.9,labels:['Full-frame promotional material'],"
            "reasons:['Local visual-language model classified the boundary window as full-frame promotional material'],"
            "model_evidence:{vlm_confirmation:'UNCERTAIN',vlm_source:'qwen_local',vlm_answer:'NO',"
            "vlm_scene:'PROMO_FULL_FRAME'}};",
        )
        self.assertIn("AI trả lời KHÔNG thấy logo riêng trong 0:00–0:05, nhưng nhận định cả cảnh là quảng cáo / "
                      "intro toàn khung", verdict)
        self.assertNotIn("KHÔNG thấy logo hay chữ quảng cáo", verdict)
        self.assertIn("AI (Qwen): trả lời “NO”; trạng thái UNCERTAIN; AI (Qwen) về cả cảnh: quảng cáo / intro "
                      "toàn khung (PROMO_FULL_FRAME)", line)
        self.assertEqual(names, [
            "Quảng cáo / intro toàn khung",
            "AI hình ảnh cục bộ nhận định đoạn đầu/cuối video là quảng cáo toàn khung",
            "Thương hiệu bên ngoài bạn đã duyệt",
        ])

    def test_wording_when_every_or_only_some_boxes_are_the_watermark(self):
        every, some = self.run_js(
            ("aiVerdictHtml", "memoryMatch", "memoryBrandName", "boxesFromMemory", "readingLabel"),
            "[aiVerdictHtml(every),aiVerdictHtml(some)]",
            "const base={category:'visual_logo',candidate_type:'opening_promotion',start_seconds:0,end_seconds:5,"
            "suggested_region_source_pixels:null,model_evidence:{vlm_confirmation:'CONFIRMED',vlm_answer:'YES'}};"
            "const wm={covered_by:'wm',covered_by_label:'XEMBZ.NET',sources:['ocr']};"
            "const every=Object.assign({},base,{evidence_regions:[Object.assign({x:105,y:160,width:174,height:53},wm),"
            "Object.assign({x:95,y:151,width:195,height:79},wm)]});"
            "const some=Object.assign({},base,{evidence_regions:[Object.assign({x:105,y:160,width:174,height:53},wm),"
            "{x:600,y:300,width:100,height:50,sources:['grounding']}]});",
        )
        # Only the watermark was located: do not also claim nothing was located.
        self.assertIn("không định vị được logo nào khác ngoài watermark đã có thẻ riêng", every)
        self.assertNotIn("không định vị được vị trí", every)
        self.assertIn("Mọi vùng logo AI khoanh ở đoạn này là watermark XEMBZ.NET — đã có thẻ riêng", every)
        self.assertEqual(every.count("XEMBZ.NET"), 1)
        self.assertIn("khung vàng là vùng AI khoanh, chỉ để tham khảo", some)
        self.assertIn("Một vùng logo AI khoanh ở đoạn này là watermark XEMBZ.NET", some)
        draw = _js_function(self.page, "drawEvidencePreviews")
        self.assertIn("if(b.c&&!tagged.has(b.o)){tagged.add(b.o);", draw, "one watermark tag per owner card")

    def test_scene_card_keeps_its_evidence_after_the_watermark_is_approved(self):
        # Troy: the whole-film XEMBZ.NET card approved as BLUR is borrowed by the
        # opening cards (regionOwner); they keep the AI verdict and amber boxes.
        media, plain, coverage = self.run_js(
            ("regionMediaHtml", "evidenceMediaHtml", "sceneLogo", "boxesFromMemory", "readingLabel",
             "overlapCoverage", "regionOwner", "isLogoItem", "isSafety", "trackCoversFullVideo", "regionOverlap",
             "viText"),
            "[regionMediaHtml(card,owner,owner.decision_region_source_pixels),"
            "regionMediaHtml(other,owner,owner.decision_region_source_pixels),overlapCoverage(card)]",
            "const SAFETY={adult:['18+']};const clock=s=>String(s);"
            "const owner={id:'wm',category:'visual_logo',candidate_type:'persistent_overlay',review_kind:'logo_overlay',"
            "start_seconds:0,end_seconds:2607,decision:'BLUR',labels:" + json.dumps(TROY_OWNER_LABELS) + ","
            "source_frame_size:[1280,534],suggested_region_source_pixels:{x:118,y:173,width:150,height:31},"
            "decision_region_source_pixels:{x:118,y:173,width:150,height:31}};"
            "const card={id:'open',category:'visual_logo',candidate_type:'opening_promotion',start_seconds:0,"
            "end_seconds:5,suggested_region_source_pixels:null,decision_region_source_pixels:null,"
            "preview_images:['a.jpg'],evidence_frame_size:[1280,534],evidence_regions:[{x:107,y:160,width:172,"
            "height:53,sources:['ocr'],covered_by:'wm',covered_by_label:'XEMBZ.NET'}]};"
            "const other=Object.assign({},card,{id:'o2',candidate_type:'rejected_logo_candidate'});"
            "const queue={source:{duration_seconds:2607},items:[owner,card]};",
        )
        self.assertIn('class="evidence-frame"', media)
        self.assertNotIn("region-crop", media)
        self.assertIn("&quot;a&quot;:1", media)
        self.assertIn("Khung vàng: vùng AI định vị", media)
        self.assertIn("Khung đỏ: vùng XEMBZ.NET đã được duyệt làm mờ ở thẻ riêng", media)
        self.assertIn("region-crop", plain, "other cards keep the red region view")
        self.assertIn("Track <strong>XEMBZ.NET</strong> đã được duyệt làm mờ toàn video ở thẻ riêng (khung đỏ trong "
                      "ảnh). Thẻ này không có vùng riêng — quyết định bên dưới áp dụng cho cả đoạn 0:00–0:05.", coverage)
        self.assertNotIn("vùng khác", coverage)
        self.assertNotIn("ứng viên riêng", coverage)
        self.assertIn("sceneLogo(x)||!(owner&&r&&r!=='FULL_FRAME')?aiVerdictHtml(x):''",
                      _js_function(self.page, "adSide"))

    def test_whole_scene_logo_cards_use_the_shared_player(self):
        focus = _js_function(self.page, "renderFocus")
        self.assertIn("player=hasPlayer(x)", focus)
        self.assertIn("if(player)setupSafetyMedia(x)", focus)
        self.assertIn("sceneLogo(x)?logoPlayHtml(x):''", _js_function(self.page, "adSide"))
        self.assertIn('data-act="play">▶ Phát đoạn này', _js_function(self.page, "logoPlayHtml"))
        self.assertIn("hasPlayer(x)", _js_function(self.page, "togglePlay"))
        self.assertNotIn("setupSafetyMedia(", _js_function(self.page, "refreshFocusIfChanged"))


if __name__ == "__main__":
    unittest.main()
