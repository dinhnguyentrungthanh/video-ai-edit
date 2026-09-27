import unittest

from biliflow.florence_regions import (
    RegionProposal,
    box_iou,
    consolidate_region_proposals,
    looks_like_site_mark,
    quad_to_box,
    prioritize_brand_region_proposals,
    select_brand_region_proposals,
    select_compact_overlay_proposals,
    select_focus_region_proposals,
)


class FlorenceRegionTests(unittest.TestCase):
    def test_unknown_brand_prefers_visual_grounding_over_large_ocr_title(self):
        title = RegionProposal((300, 60, 1080, 300), ("ocr",), ("movie title",))
        logo = RegionProposal((100, 20, 340, 230), ("grounding",), ("company logo",))
        selected = prioritize_brand_region_proposals([title, logo], None)
        self.assertEqual(selected[0], logo)

    def test_confirmed_brand_keeps_text_match_order(self):
        matched = RegionProposal((130, 100, 290, 170), ("ocr",), ("ANIME",))
        visual = RegionProposal((90, 10, 350, 240), ("grounding",), ("logo",))
        self.assertEqual(
            prioritize_brand_region_proposals([matched, visual], "NewGates Anime")[0],
            matched,
        )

    def test_focus_tile_removes_unrelated_title_region(self):
        proposals = [
            RegionProposal((100, 40, 260, 140), ("grounding",), ("logo",)),
            RegionProposal((900, 40, 1500, 150), ("ocr",), ("movie title",)),
        ]
        selected = select_focus_region_proposals(proposals, (0, 0, 500, 300))
        self.assertEqual(selected, [proposals[0]])

    def test_box_iou(self):
        self.assertAlmostEqual(box_iou([0, 0, 10, 10], [5, 5, 15, 15]), 25 / 175)

    def test_quad_to_box(self):
        self.assertEqual(quad_to_box([2, 4, 8, 3, 9, 10, 1, 11]), (1, 3, 9, 11))

    def test_filters_full_frame_hallucination(self):
        proposals = consolidate_region_proposals(
            grounding_boxes=[[0, 10, 1278, 719]],
            grounding_labels=["promotional banner"],
            ocr_quads=[], ocr_labels=[], image_size=(1280, 720),
        )
        self.assertEqual(proposals, [])

    def test_merges_grounding_and_ocr_and_prefers_tight_ocr_box(self):
        proposals = consolidate_region_proposals(
            grounding_boxes=[[560, 225, 720, 500]],
            grounding_labels=["platform logo"],
            ocr_quads=[[565, 230, 715, 230, 715, 495, 565, 495]],
            ocr_labels=["N"], image_size=(1280, 720),
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].box, (565, 230, 715, 495))
        self.assertEqual(set(proposals[0].sources), {"grounding", "ocr"})

    def test_keeps_scene_sign_only_for_upstream_semantic_gate_to_reject(self):
        proposals = consolidate_region_proposals(
            grounding_boxes=[[546, 196, 604, 270]],
            grounding_labels=["platform logo"],
            ocr_quads=[[564, 206, 589, 206, 589, 237, 564, 237]],
            ocr_labels=["P"], image_size=(1280, 720),
        )
        self.assertTrue(proposals)

    def test_selects_confirmed_brand_text_without_hardcoded_brand_list(self):
        proposals = consolidate_region_proposals(
            grounding_boxes=[[400, 100, 880, 615]],
            grounding_labels=["platform logo"],
            ocr_quads=[
                [410, 115, 866, 115, 866, 148, 410, 148],
                [664, 569, 844, 569, 844, 592, 664, 592],
            ],
            ocr_labels=["Version en español", "NETFLIX | DUBBING"],
            image_size=(1280, 720),
        )
        selected = select_brand_region_proposals(proposals, "Netflix")
        self.assertEqual(len(selected), 1)
        self.assertIn("NETFLIX", selected[0].labels[0])

    def test_accepts_confirmed_brand_initial_for_visual_ident(self):
        proposals = consolidate_region_proposals(
            grounding_boxes=[[560, 225, 720, 500]],
            grounding_labels=["platform logo"],
            ocr_quads=[[565, 230, 715, 230, 715, 495, 565, 495]],
            ocr_labels=["N"], image_size=(1280, 720),
        )
        self.assertEqual(
            len(select_brand_region_proposals(proposals, "Netflix")), 1
        )

    def test_focus_filter_fails_closed_instead_of_keeping_every_box(self):
        # A proposal that contradicts the coarse router must not survive by
        # default: sending the reviewer to an unflagged region is worse than
        # offering no region at all.
        proposals = [RegionProposal((900, 400, 1100, 600), ("grounding",), ("logo",))]
        self.assertEqual(select_focus_region_proposals(proposals, (0, 0, 400, 300)), [])
        self.assertEqual(
            select_focus_region_proposals(
                proposals, (0, 0, 400, 300), allow_fallback=True
            ),
            proposals,
        )


class PersistentOverlayShapeTests(unittest.TestCase):
    """Regression cover for the Conan Movie 20 misdetection.

    Florence grounded a character's head at 510s while the real phimmoi
    watermark sat in the top-right corner. Both boxes centred inside the wide
    top_right router tile, so the head box reached review as a blur proposal.
    """

    FRAME = (1920, 1080)
    HEAD = RegionProposal((1216, 206, 1451, 418), ("grounding",), ("a company logo",))
    HALF_FRAME = RegionProposal(
        (1074, 28, 1625, 433), ("grounding",), ("promotional banner",)
    )
    WATERMARK = RegionProposal((1598, 58, 1848, 99), ("ocr",), ("phimmoi.net",))

    def test_narrow_corner_strip_rejects_head_box_and_keeps_watermark(self):
        from biliflow.visual_logo_scanner import localization_focus_box

        strip = localization_focus_box("top_right", self.FRAME)
        selected = select_focus_region_proposals(
            [self.HEAD, self.HALF_FRAME, self.WATERMARK], strip
        )
        self.assertEqual(selected, [self.WATERMARK])

    def test_compact_fallback_rejects_central_head_box(self):
        # The head box is small enough to pass an area test on its own, so
        # placement is what excludes it: an overlay sits in the margin.
        selected = select_compact_overlay_proposals(
            [self.HEAD, self.HALF_FRAME, self.WATERMARK], self.FRAME
        )
        self.assertEqual(selected, [self.WATERMARK])

    def test_read_site_mark_outranks_bare_grounding_guess(self):
        ordered = prioritize_brand_region_proposals(
            [self.HEAD, self.WATERMARK], None
        )
        self.assertEqual(ordered[0], self.WATERMARK)

    def test_site_mark_shape_is_recognised_without_a_brand_list(self):
        for label in ("phimmoi.net", "PHIM.MEDIA", "bilibili", "</s>vuighe.tv"):
            self.assertTrue(looks_like_site_mark(label), label)
        for label in (
            "a company logo", "Detective Conan The Movie", "Welcome",
            "COURTESY", "N", "",
        ):
            self.assertFalse(looks_like_site_mark(label), label)


if __name__ == "__main__":
    unittest.main()
