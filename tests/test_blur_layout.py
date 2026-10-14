"""How a kept segment's blurs are wired (blur_layout), without FFmpeg."""
import random
import re
import unittest
from collections import Counter

from biliflow.blur_filter import COVER_MARGIN_PIXELS, COVER_METHOD, GBLUR, regional_blur_filters
from biliflow.blur_layout import (
    EDGE_PIXELS,
    EXACT_REGION_LIMIT,
    FULL_FRAME,
    NESTED_LAYOUT,
    NESTED_PATH_LIMIT,
    SHARED_LAYOUT,
    SHARED_PATH_BUDGET,
    SegmentBlur,
    nested_paths,
    segment_blur_filters,
    shared_sources,
)
from biliflow.final_renderer import _final_filter_graph, build_final_filter_graph

LABELS = re.compile(r"\[([^\]]+)\]")
SPAN = (0.0, 100.0)


def region(x, y, width=40, height=20, *, ranges=(SPAN,), method=COVER_METHOD, feather=2):
    return SegmentBlur(region={"x": x, "y": y, "width": width, "height": height}, sigma=28,
                       feather=feather, feather_mode="all_edges", method=method, ranges=tuple(ranges))


def full_frame(*ranges):
    return SegmentBlur(region=FULL_FRAME, sigma=28, feather=0, feather_mode="all_edges",
                       method=GBLUR, ranges=tuple(ranges))


def regions_missing_a_change(blurs, sources):
    """The blurs whose branch reads a picture without an earlier blur's change that can reach it while both are on.

    Written from the rule itself, apart from blur_layout: picture s holds the
    changes of blurs 0..s-1, an overlay changes its region and EDGE_PIXELS
    around it, a branch reads its region, EDGE_PIXELS around it and, with the
    cover method, COVER_MARGIN_PIXELS more; a full-frame blur changes everything.
    """
    def box(blur, margin):
        r = blur.region
        return r["x"] - margin, r["y"] - margin, r["x"] + r["width"] + margin, r["y"] + r["height"] + margin

    missing = set()
    for k, source in enumerate(sources):
        if source is None:
            continue
        cover = blurs[k].method == COVER_METHOD
        x0, y0, x1, y1 = box(blurs[k], EDGE_PIXELS + (COVER_MARGIN_PIXELS if cover else 0))
        for earlier in blurs[source:k]:
            on_together = any(a <= d + 0.001 and c <= b + 0.001
                              for a, b in earlier.ranges for c, d in blurs[k].ranges)
            if not on_together:
                continue
            if earlier.full_frame:
                missing.add(k)
                continue
            e0, f0, e1, f1 = box(earlier, EDGE_PIXELS)
            if e0 < x1 and x0 < e1 and f0 < y1 and y0 < f1:
                missing.add(k)
    return missing


def layout(blurs, frame_size=(1280, 720)):
    return segment_blur_filters(index=0, input_label="v0trim", blurs=blurs, frame_size=frame_size,
                                chroma_rows_divisor=2)


def chains(filters):
    """(input labels, output labels) of each chain of a graph."""
    result = []
    for chain in filters:
        head = re.match(r"^((?:\[[^\]]+\])*)", chain).group(1)
        tail = re.search(r"((?:\[[^\]]+\])*)$", chain).group(1)
        result.append((LABELS.findall(head), LABELS.findall(tail)))
    return result


def paths_in_graph(filters, source, sink):
    """Paths from ``source`` to ``sink`` through the graph's chains, counted on the graph itself."""
    paths = Counter({source: 1})
    pending = chains(filters)
    while pending:
        ready = [item for item in pending if all(label in paths for label in item[0])]
        if not ready:
            raise AssertionError("the graph has a chain whose input is never made")
        for inputs, outputs in ready:
            pending.remove((inputs, outputs))
            arriving = sum(paths[label] for label in inputs)
            for label in outputs:
                paths[label] = arriving
    return paths[sink]


class LayoutAssertions:
    def assert_well_formed(self, result, input_label="v0trim"):
        """Each picture is made once and read once; the segment's picture goes in once and its last comes out."""
        made, read = Counter(), Counter()
        for inputs, outputs in chains(result.filters):
            read.update(inputs)
            made.update(outputs)
        self.assertEqual(read[input_label], 1)
        self.assertNotIn(input_label, made)
        self.assertEqual(made[result.output_label], 1)
        self.assertNotIn(result.output_label, read)
        for label, count in made.items():
            self.assertEqual(count, 1, label)
            if label != result.output_label:
                self.assertEqual(read[label], 1, label)
        self.assertEqual(set(read) - {input_label}, set(made) - {result.output_label})
        self.assertEqual(paths_in_graph(result.filters, input_label, result.output_label), result.paths)


class NestedLayoutTests(LayoutAssertions, unittest.TestCase):
    def test_a_segment_within_the_limit_keeps_the_graph_of_before_character_for_character(self):
        blurs = [full_frame((1.0, 2.0))] + [region(10 + 100 * k, 600) for k in range(5)]
        self.assertEqual(nested_paths(blurs), NESTED_PATH_LIMIT)  # 4**5: the largest export before job 134
        result = layout(blurs)
        expected = ["[v0trim]gblur=sigma=28:enable='between(t,1.000000,2.000000)'[v0blur0]"]
        for k, blur in enumerate(blurs[1:], start=1):
            expected += regional_blur_filters(
                input_label=f"v0blur{k - 1}", output_label=f"v0blur{k}", prefix=f"v0region{k}",
                region=blur.region, sigma=28, feather=2, feather_mode="all_edges",
                enable="enable='between(t,0.000000,100.000000)'", method=COVER_METHOD,
                frame_size=(1280, 720), chroma_rows_divisor=2,
            )
        self.assertEqual(list(result.filters), expected)
        self.assertEqual((result.layout, result.output_label, result.older_reads), (NESTED_LAYOUT, "v0blur5", 0))
        self.assert_well_formed(result)

    def test_the_paths_multiply_by_each_region_nested(self):
        self.assertEqual(nested_paths([region(0, 0, feather=0)]), 2)
        self.assertEqual(nested_paths([region(0, 0, method=GBLUR)]), 3)
        self.assertEqual(nested_paths([region(0, 0)]), 4)
        self.assertEqual(nested_paths([full_frame(SPAN)] * 3), 1)
        self.assertEqual(nested_paths([region(0, 0)] * 25), 4 ** 25)  # job 134: never a frame


class SharedLayoutTests(LayoutAssertions, unittest.TestCase):
    def test_past_the_limit_every_later_branch_reads_after_the_first_overlay_and_the_paths_add_up(self):
        blurs = [region(10 + 100 * k, 600) for k in range(6)]
        self.assertGreater(nested_paths(blurs), NESTED_PATH_LIMIT)
        result = layout(blurs)
        self.assertEqual((result.layout, result.older_reads), (SHARED_LAYOUT, 0))
        self.assertEqual(result.paths, 4 + 5 * 3 * 4)
        self.assertEqual(result.filters[0], "[v0trim]split=2[v0keep0][v0region0crop]")
        self.assertIn("[v0blur0]split=6[v0keep1][v0region1crop][v0region2crop][v0region3crop]"
                      "[v0region4crop][v0region5crop]", result.filters)
        self.assertIn("[v0keep1][v0region1patch]overlay=110:600:enable='between(t,0.000000,100.000000)'[v0blur1]",
                      result.filters)
        self.assert_well_formed(result)

    def test_a_branch_reads_after_the_last_earlier_blur_that_can_change_what_it_reads_while_both_are_on(self):
        blurs = [
            region(0, 0),                                   # 0: the first overlay
            region(400, 400, 100, 50),                      # 1
            region(450, 420, ranges=((10.0, 20.0),)),       # 2: inside 1, while 1 is on
            region(450, 420, ranges=((200.0, 300.0),)),     # 3: inside 1, while 1 is off
            region(460, 430, ranges=((15.0, 16.0),)),       # 4: inside 2 and 1, while both are on
            region(900, 100),                               # 5: touches nothing
        ]
        sources, _, older = shared_sources(blurs)
        self.assertEqual(sources, [0, 1, 2, 1, 3, 1])
        self.assertEqual(older, 0)

    def test_the_cover_methods_margin_and_the_chroma_edge_count_as_reading(self):
        earlier = region(100, 100, 40, 20, method=GBLUR)  # changes x 96..144 with its edge
        for gap, method, reads_it in ((2 * EDGE_PIXELS - 1, GBLUR, True), (2 * EDGE_PIXELS, GBLUR, False),
                                      (2 * EDGE_PIXELS + 7, COVER_METHOD, True),
                                      (2 * EDGE_PIXELS + 8, COVER_METHOD, False)):
            with self.subTest(gap=gap, method=method):
                later = region(140 + gap, 100, 40, 20, method=method)
                sources, _, _ = shared_sources([region(800, 600), earlier, later])
                self.assertEqual(sources[2], 2 if reads_it else 1)

    def test_times_that_touch_count_as_meeting(self):
        for start, meets in ((10.0005, True), (10.0, True), (10.01, False)):
            with self.subTest(start=start):
                sources, _, _ = shared_sources([
                    region(800, 600), region(0, 0, ranges=((0.0, 10.0),)),
                    region(5, 5, ranges=((start, 12.0),)),
                ])
                self.assertEqual(sources[2], 2 if meets else 1)

    def test_full_frame_blurs_change_every_picture_while_they_are_on(self):
        # Before the first region: it reads what it read nested, the picture after them.
        self.assertEqual(shared_sources([full_frame(SPAN), region(0, 0), region(500, 500)])[0], [None, 1, 2])
        blurs = [region(0, 0), full_frame((2.0, 3.0)), region(500, 500, ranges=((0.0, 1.0),)),
                 region(500, 500, ranges=((2.5, 4.0),))]
        self.assertEqual(shared_sources(blurs)[0], [0, None, 1, 2])

    def test_ten_regions_always_read_exactly_even_in_one_long_chain(self):
        blurs = [region(10 + k, 10 + k) for k in range(EXACT_REGION_LIMIT)]  # each over the one before
        sources, paths, older = shared_sources(blurs)
        self.assertEqual(sources, [0] + list(range(1, EXACT_REGION_LIMIT)))
        self.assertEqual((paths, older), (4 ** EXACT_REGION_LIMIT, 0))  # as slow as nested, never different

    def test_a_longer_chain_reads_older_pictures_within_the_budget_and_says_so(self):
        blurs = [region(10 + k, 10 + k) for k in range(30)]
        result = layout(blurs)
        self.assertLessEqual(result.paths, SHARED_PATH_BUDGET)
        self.assertGreater(result.older_reads, 0)
        self.assert_well_formed(result)

    def test_sixty_regions_shaped_like_job_134_read_exactly_with_few_paths(self):
        # Two strips over the whole segment (one over the other), then subtitle-band cards:
        # some in the same place at other times, some side by side at the same time.
        blurs = [region(829, 36, 335, 63), region(0, 13, 1280, 48)]
        for k in range(58):
            start = 30.0 * (k // 2)
            blurs.append(region(300 + 400 * (k % 2), 430, 300, 40, ranges=((start, start + 6.5),)))
        result = layout(blurs)
        self.assertEqual((result.layout, result.older_reads), (SHARED_LAYOUT, 0))
        self.assertLess(result.paths, 1024)
        self.assert_well_formed(result)
        # The second strip reads after the first, which it overlaps; the cards touch neither strip.
        self.assertEqual(shared_sources(blurs)[0][:4], [0, 1, 1, 1])

    def test_random_segments_are_well_formed_and_exact_up_to_ten_regions(self):
        rng = random.Random(134)
        for case in range(300):
            blurs = []
            for _ in range(rng.choice([1, 3, 6, 10, 11, 25, 60])):
                start = rng.uniform(0, 90)
                ranges = ((start, start + rng.uniform(0, 20)),)
                if rng.random() < 0.08:
                    blurs.append(full_frame(*ranges))
                else:
                    blurs.append(region(rng.randrange(0, 1200), rng.randrange(0, 680), rng.randrange(8, 300),
                                        rng.randrange(8, 120), ranges=ranges,
                                        method=rng.choice([GBLUR, COVER_METHOD]), feather=rng.choice([0, 2])))
            with self.subTest(case=case, blurs=len(blurs)):
                result = layout(blurs)
                self.assert_well_formed(result)
                regions = sum(not blur.full_frame for blur in blurs)
                expected = NESTED_LAYOUT if nested_paths(blurs) <= NESTED_PATH_LIMIT else SHARED_LAYOUT
                self.assertEqual(result.layout, expected)
                sources, _, older_reads = shared_sources(blurs)
                missing = regions_missing_a_change(blurs, sources)
                # Exactly the regions the export counts read an older picture than exactness needs.
                self.assertEqual(len(missing), older_reads)
                if regions <= EXACT_REGION_LIMIT:
                    self.assertEqual((missing, result.older_reads), (set(), 0))
                else:
                    self.assertLessEqual(result.paths, max(SHARED_PATH_BUDGET, 4 * (1 + 3 * regions)))


class ExportGraphTests(unittest.TestCase):
    @staticmethod
    def card(index, start, end):
        return {"id": f"card-{index}", "type": "blur", "start_seconds": start, "end_seconds": end,
                "region_source_pixels": {"x": 40 + 20 * (index % 30), "y": 420 + 40 * (index // 30),
                                         "width": 120, "height": 30},
                "blur": {"sigma": 28, "edge_feather_pixels": 2, "edge_feather_mode": "all_edges",
                         "method": COVER_METHOD}}

    def test_each_kept_segment_is_wired_on_its_own_and_the_export_says_how(self):
        operations = [{"id": "cut", "type": "cut", "start_seconds": 0.0, "end_seconds": 10.0},
                      {"id": "mid", "type": "cut", "start_seconds": 50.0, "end_seconds": 55.0}]
        operations += [self.card(k, 10.0 + k, 90.0) for k in range(12)]  # each overlaps its neighbours
        operations += [self.card(100 + k, 60.0 + k, 61.0 + k) for k in range(3)]
        graph, expected, wiring = _final_filter_graph(operations=operations, duration=100.0, has_audio=True,
                                                      frame_size=(1280, 720), chroma_rows_divisor=2)
        self.assertEqual(expected, 85.0)
        # Of the 12 cards the renderer keeps 6 (the others lie under their two neighbours while
        # on); the second segment adds the 3 short ones. At most 10 each: both read exactly.
        self.assertEqual(len(set(re.findall(r"\[v0region\d+crop\]", graph))), 6)
        self.assertEqual(len(set(re.findall(r"\[v1region\d+crop\]", graph))), 9)
        self.assertGreater(wiring["paths"], NESTED_PATH_LIMIT)
        self.assertEqual(wiring["layout"], SHARED_LAYOUT)
        self.assertEqual(wiring["regions_reading_older_picture"], 0)
        self.assertIn("concat=n=2:v=1:a=1[vout][aout]", graph)
        self.assertEqual(build_final_filter_graph(operations=operations, duration=100.0, has_audio=True,
                                                  frame_size=(1280, 720)), (graph, expected))

    def test_a_plan_without_blurs_has_one_path(self):
        _, _, wiring = _final_filter_graph(operations=[], duration=10.0, has_audio=False, frame_size=None,
                                           chroma_rows_divisor=2)
        self.assertEqual(wiring, {"layout": NESTED_LAYOUT, "paths": 1, "regions_reading_older_picture": 0})


if __name__ == "__main__":
    unittest.main()
