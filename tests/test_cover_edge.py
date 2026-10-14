"""The cover method at the right and bottom frame edge (job 141, 2026-10-14).

FFmpeg's crop rounds its size and corner down to whole chroma samples of the
picture it crops (2 x 2 pixels for 4:2:0, 4 x 4 for 4:1:0, none for 4:4:4).
Until 2026-10-14 the cover chain placed delogo's rectangle in the size it asked
for; a crop of odd size with no picture (or one pixel) past the region's right
or bottom edge lost that column or row, the rectangle touched the cropped
picture's edge and delogo refused the whole graph ("Logo area is outside of the
frame"), on every try. Job 141: a region x=583 y=35 w=697 h=49 on a 1280 x 534
yuv420p film.

Measured on FFmpeg 9.0.1 (docs/evidence/cover-edge/README.md): the first
regional branch of a kept segment crops the source's pixel format (or 4:2:0
after a full-frame blur gblur cannot take it through), every later branch an
overlay's (4:2:0); overlay lays a patch at x and y rounded down to even; crop
exact=1 at an odd x garbles the chroma of a 10-bit picture.
"""
import json
import os
import re
import subprocess
import unittest
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import blur_layout
from biliflow.blur_filter import (
    CHROMA_420,
    COVER_MARGIN_PIXELS,
    COVER_METHOD,
    GBLUR,
    NO_ROUNDING,
    SourceChroma,
    cover_sigma,
    regional_blur_filters,
)
from biliflow.final_renderer import _final_filter_graph

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = Path(os.environ.get("BILIFLOW_FFMPEG") or ROOT / "tools/ffmpeg/bin/ffmpeg.exe")
FRAME = (160, 90)  # 90 rows: not a whole number of 4:1:0 chroma rows, as job 141's 534
ENABLE = "enable='between(t,0.000000,0.080000)'"
STEPS = {
    "yuv420p": (2, 2), "yuv422p": (2, 1), "yuv444p": (1, 1), "yuv410p": (4, 4),
    "yuv411p": (4, 1), "yuv440p": (1, 2),
}
# Share of the source's fine detail a line of pixels keeps: about 1 left alone, 0 blurred.
BLURRED_BELOW = 0.05
JOB_141_FRAME = (1280, 534)
JOB_141_REGION = {"x": 583, "y": 35, "width": 697, "height": 49}
CROP_STEPS = ROOT / "docs" / "evidence" / "cover-edge" / "crop-steps.jsonl"
# Source formats outside STEPS: (step alone, step after a full-frame blur), as measured.
OTHER_STEPS = {
    "rgb24": ((1, 1), (2, 2)), "yuv440p10le": ((1, 2), (2, 2)), "yuv422p10le": ((2, 1), (2, 1)),
    "nv16": ((2, 1), (2, 2)),
}


def old_cover_chain(region: dict, frame_size, enable: str) -> str:
    """_cover_chain as on 11a059c, character for character: what every export that ran used."""
    x, y = int(region["x"]), int(region["y"])
    width, height = int(region["width"]), int(region["height"])
    left, top = min(COVER_MARGIN_PIXELS, x), min(COVER_MARGIN_PIXELS, y)
    if frame_size is None:
        right = bottom = 0
    else:
        right = max(0, min(COVER_MARGIN_PIXELS, int(frame_size[0]) - x - width))
        bottom = max(0, min(COVER_MARGIN_PIXELS, int(frame_size[1]) - y - height))
    crop_width, crop_height = width + left + right, height + top + bottom
    chain = f"crop={crop_width}:{crop_height}:{x - left}:{y - top},"
    logo_x, logo_y = max(1, left), max(1, top)
    logo_width = min(crop_width - 1, left + width) - logo_x
    logo_height = min(crop_height - 1, top + height) - logo_y
    if logo_width >= 1 and logo_height >= 1:
        chain += f"delogo=x={logo_x}:y={logo_y}:w={logo_width}:h={logo_height}:{enable},"
    return chain + f"crop={width}:{height}:{left}:{top}"


CROP_DELOGO = re.compile(
    r"crop=(\d+):(\d+):(\d+):(\d+)(:exact=1)?,delogo=x=(\d+):y=(\d+):w=(\d+):h=(\d+):"
)
CROP_BACK = re.compile(r"delogo=x=\d+:y=\d+:w=\d+:h=\d+:enable='[^']*',crop=(\d+):(\d+):(\d+):(\d+)(:exact=1)?")
LAY = re.compile(r"overlay=(\d+):(\d+):enable")


def old_chain_fits(region: dict, frame_size, step) -> bool:
    """Whether FFmpeg ran 11a059c's chain: delogo's rectangle inside the crop rounded down to ``step``."""
    found = CROP_DELOGO.search(old_cover_chain(region, frame_size, ENABLE))
    if found is None:
        return True  # no delogo at all
    width, height = int(found[1]), int(found[2])
    logo_x, logo_y, logo_w, logo_h = (int(found[k]) for k in (6, 7, 8, 9))
    return logo_x + logo_w < width - width % step[0] and logo_y + logo_h < height - height % step[1]


def branch(region, *, step=CHROMA_420, feather=0, frame_size=FRAME, mode="all_edges") -> list[str]:
    return regional_blur_filters(
        input_label="in", output_label="out", prefix="p", region=region, sigma=cover_sigma(region),
        feather=feather, enable=ENABLE, feather_mode=mode, method=COVER_METHOD, frame_size=frame_size,
        chroma_step=step,
    )


def edge_regions(frame=FRAME, margins=range(5)):
    """Regions at the right and bottom edge: odd and even size, some pixels from the edge, odd and even corner."""
    width, height = frame
    for size, margin, shift in product((60, 61, 62, 63), margins, (0, 1)):
        yield "right", {"x": width - margin - size, "y": 30 + shift, "width": size, "height": 20 + shift}
        yield "bottom", {"x": 50 + shift, "y": height - margin - (size - 40), "width": 40 + shift,
                         "height": size - 40}


class SourceChromaTests(unittest.TestCase):
    def test_every_measured_pixel_format_gets_the_steps_ffmpeg_cropped_it_with(self):
        measured = 0
        for line in CROP_STEPS.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            alone, after = row.get("alone", {}), row.get("after_full_frame_blur", {})
            if "step" not in alone or "step" not in after:
                continue  # FFmpeg cannot make a picture of that format (hardware, bayer...)
            with self.subTest(pix_fmt=row["pix_fmt"]):
                self.assertEqual(SourceChroma.of(row["pix_fmt"]),
                                 SourceChroma(tuple(alone["step"]), tuple(after["step"])))
                measured += 1
        self.assertGreater(measured, 200)
        for pix_fmt, (alone, after) in {**{name: (step, step) for name, step in STEPS.items()},
                                        **OTHER_STEPS}.items():
            with self.subTest(pix_fmt=pix_fmt):
                self.assertEqual(SourceChroma.of(pix_fmt), SourceChroma(alone, after))

    def test_a_source_of_unknown_format_keeps_every_chain_of_before(self):
        for pix_fmt in (None, "", "unknown"):
            with self.subTest(pix_fmt=pix_fmt):
                self.assertEqual(SourceChroma.of(pix_fmt), SourceChroma(NO_ROUNDING, NO_ROUNDING))
        # pal8 (measured from a palette clip, paletteuse): its own step, 4:2:0 after gblur.
        self.assertEqual(SourceChroma.of("pal8"), SourceChroma(NO_ROUNDING, CHROMA_420))


class CoverChainTextTests(unittest.TestCase):
    FRAMES = (FRAME, (161, 91), JOB_141_FRAME, None)

    def test_every_chain_ffmpeg_ran_keeps_its_text(self):
        checked = 0
        for frame, step in product(self.FRAMES, set(STEPS.values())):
            for _, region in edge_regions(frame or FRAME):
                if not old_chain_fits(region, frame, step):
                    continue
                checked += 1
                with self.subTest(frame=frame, step=step, region=region):
                    chains = branch(region, step=step, frame_size=frame)
                    self.assertIn(old_cover_chain(region, frame, ENABLE), chains[1])
                    self.assertNotIn("exact", chains[1])
                    self.assertTrue(chains[-1].startswith(f"[pbase][pblurred]overlay={region['x']}:{region['y']}:"))
        self.assertGreater(checked, 400)

    def test_chains_ffmpeg_refused_crop_exactly_from_an_even_corner_and_keep_delogo_inside(self):
        checked = 0
        for frame, step, feather in product(self.FRAMES, set(STEPS.values()), (0, 2)):
            for _, region in edge_regions(frame or FRAME):
                if old_chain_fits(region, frame, step):
                    continue
                checked += 1
                with self.subTest(frame=frame, step=step, feather=feather, region=region):
                    text = ";".join(branch(region, step=step, feather=feather, frame_size=frame))
                    first = CROP_DELOGO.search(text)
                    back = CROP_BACK.search(text)
                    lay = LAY.search(text)
                    self.assertTrue(first[5] and back[5], "both crops exact")
                    crop_w, crop_h, crop_x, crop_y = (int(first[k]) for k in (1, 2, 3, 4))
                    logo_x, logo_y, logo_w, logo_h = (int(first[k]) for k in (6, 7, 8, 9))
                    # No rounding left for delogo to trip on, and no odd x for a 10-bit picture.
                    self.assertGreaterEqual(min(logo_x, logo_y), 1)
                    self.assertLess(logo_x + logo_w, crop_w)
                    self.assertLess(logo_y + logo_h, crop_h)
                    self.assertEqual((crop_x % 2, crop_y % 2), (0, 0))
                    # The patch is cut and laid at the same even corner, over the whole region.
                    patch_w, patch_h, back_x, back_y = (int(back[k]) for k in (1, 2, 3, 4))
                    lay_x, lay_y = int(lay[1]), int(lay[2])
                    self.assertEqual((crop_x + back_x, crop_y + back_y), (lay_x, lay_y))
                    self.assertEqual((lay_x % 2, lay_y % 2), (0, 0))
                    self.assertIn(region["x"] - lay_x, (0, 1))
                    self.assertIn(region["y"] - lay_y, (0, 1))
                    self.assertEqual(lay_x + patch_w, region["x"] + region["width"])
                    self.assertEqual(lay_y + patch_h, region["y"] + region["height"])
                    if frame is not None:
                        self.assertLessEqual(crop_x + crop_w, frame[0])
                        self.assertLessEqual(crop_y + crop_h, frame[1])
        self.assertGreater(checked, 100)

    def test_job_141_region(self):
        self.assertFalse(old_chain_fits(JOB_141_REGION, JOB_141_FRAME, (2, 2)))
        chains = branch(JOB_141_REGION, feather=2, frame_size=JOB_141_FRAME)
        self.assertIn(
            "crop=706:66:574:26:exact=1,delogo=x=8:y=8:w=697:h=50:"
            f"{ENABLE},crop=698:50:8:8:exact=1,", chains[1],
        )
        self.assertIn("overlay=582:34:", chains[-1])

    def test_a_feathered_side_on_the_frame_edge_is_not_faded(self):
        def mask(region, mode="all_edges"):
            return next(chain for chain in branch(region, feather=2, mode=mode) if "geq=" in chain)

        # Ran before: its text, faded on all four sides, as on 11a059c.
        self.assertIn("geq=lum='255*min(1\\,min(min(X\\,W-1-X)\\,min(Y\\,H-1-Y))/2)'",
                      mask({"x": 100, "y": 30, "width": 60, "height": 20}))
        # Refused before (odd width at the right edge): no fade on the right side.
        self.assertIn("geq=lum='255*min(1\\,min(X\\,min(Y\\,H-1-Y))/2)'",
                      mask({"x": 99, "y": 30, "width": 61, "height": 20}))
        # At the bottom edge, and in the right-bottom corner.
        self.assertIn("geq=lum='255*min(1\\,min(min(X\\,W-1-X)\\,Y)/2)'",
                      mask({"x": 50, "y": 69, "width": 40, "height": 21}))
        self.assertIn("geq=lum='255*min(1\\,min(X\\,Y)/2)'", mask({"x": 99, "y": 69, "width": 61, "height": 21}))
        # vertical_only fades the top and bottom only.
        self.assertIn("geq=lum='255*min(1\\,Y/2)'",
                      mask({"x": 50, "y": 69, "width": 40, "height": 21}, mode="vertical_only"))
        # Refused 1 pixel from the edge (even width): exact crops, faded on every side.
        one_off = {"x": 99, "y": 30, "width": 60, "height": 20}
        self.assertIn("exact=1", ";".join(branch(one_off, feather=2)))
        self.assertIn("geq=lum='255*min(1\\,min(min(X\\,W-1-X)\\,min(Y\\,H-1-Y))/2)'", mask(one_off))

    def test_gblur_regions_keep_their_text(self):
        region = {"x": 99, "y": 69, "width": 61, "height": 21}
        chains = regional_blur_filters(
            input_label="in", output_label="out", prefix="p", region=region, sigma=28, feather=2,
            enable=ENABLE, method=GBLUR, frame_size=FRAME, chroma_step=(4, 4),
        )
        self.assertIn("[pcrop]crop=61:21:99:69,gblur", chains[1])
        self.assertTrue(chains[-1].startswith("[pbase][psoft]overlay=99:69:"))


def cover(start, end, region, *, feather=2):
    return {
        "id": f"cover-{region['x']}-{region['y']}", "type": "blur", "start_seconds": start, "end_seconds": end,
        "region_source_pixels": region,
        "blur": {"sigma": 28, "edge_feather_pixels": feather, "edge_feather_mode": "all_edges",
                 "method": COVER_METHOD},
    }


# Far from every edge region below: the branch after it reads a picture after its overlay.
EARLIER = {"x": 4, "y": 4, "width": 30, "height": 20}


# The second frame of run_graph's source (25 fps): the regions of export_graph(after_full_frame=True) are on.
AFTER_FULL_FRAME_START, AFTER_FULL_FRAME_FRAME = 0.04, 1


def full_frame(start, end):
    return {"id": f"full-{start:g}", "type": "blur", "start_seconds": start, "end_seconds": end,
            "region_source_pixels": "FULL_FRAME",
            "blur": {"sigma": 10, "edge_feather_pixels": 0, "edge_feather_mode": "all_edges", "method": GBLUR}}


def export_graph(region, *, pix_fmt, feather, later, layout, frame_size=FRAME, duration=0.08,
                 after_full_frame=False, source_chroma=None):
    """The export's graph of ``region`` (after EARLIER when ``later``) on a source of ``pix_fmt``.

    ``after_full_frame`` puts a full-frame blur first in the graph (the renderer orders
    blurs by their start): on for the first frame only, the regions from the second
    (AFTER_FULL_FRAME_FRAME) on.
    """
    start = AFTER_FULL_FRAME_START if after_full_frame else 0.0
    operations = (([full_frame(0.0, 0.02)] if after_full_frame else [])
                  + ([cover(start, duration, EARLIER, feather=feather)] if later else [])
                  + [cover(start, duration, region, feather=feather)])
    limit = 10**18 if layout == blur_layout.NESTED_LAYOUT else 0
    with patch.object(blur_layout, "NESTED_PATH_LIMIT", limit):
        graph, _, wiring = _final_filter_graph(
            operations=operations, duration=duration, has_audio=False, frame_size=frame_size,
            chroma_rows_divisor=4 if pix_fmt == "yuv410p" else 2,
            source_chroma=source_chroma or SourceChroma.of(pix_fmt),
        )
    assert wiring["layout"] == layout, wiring
    assert not after_full_frame or graph.index("gblur=sigma=10:") < graph.index("crop="), graph
    return graph


def source_filter(pix_fmt, width, height, *, noise=True):
    """Two frames of testsrc2, with static noise (fine detail everywhere), in ``pix_fmt``.

    testsrc2 is made in yuv420p first: asked for yuv410p itself, it rounds 90 rows down to 88.
    """
    return (f"testsrc2=s={width}x{height}:r=25:d=0.08,format=yuv420p,"
            + ("noise=alls=40:allf=u," if noise else "") + f"format={pix_fmt}")


def run_graph(graph, pix_fmt, folder, frame_size=FRAME, frame=0):
    """Frame ``frame`` of static noise over testsrc2 through ``graph``: (returncode, stderr, source luma, output luma).

    The graph's output is yuv420p, as the export's (-pix_fmt yuv420p): the format
    FFmpeg converts a source to before a full-frame gblur follows from it.
    """
    width, height = frame_size
    script = folder / "graph.txt"
    picked = f";[vout]trim=start_frame={frame}[vpick]" if frame else ""
    script.write_text(graph + picked, encoding="utf-8")
    source, output = folder / "source.yuv", folder / "output.yuv"
    done = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         source_filter(pix_fmt, width, height),
         "-/filter_complex", str(script),
         "-map", "[vpick]" if frame else "[vout]", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "yuv420p",
         str(output),
         "-map", "0:v", "-vf", f"trim=start_frame={frame}", "-frames:v", "1", "-f", "rawvideo",
         "-pix_fmt", "yuv420p", str(source)],
        capture_output=True, text=True, timeout=60,
    )
    if done.returncode:
        return done.returncode, done.stderr, None, None
    luma = width * height
    return 0, done.stderr, source.read_bytes()[:luma], output.read_bytes()[:luma]


def line_detail(source, output, width, pixels):
    """Share of the source's detail along a line of pixels (x, y) that the output keeps (1: all of it)."""
    def high_pass(frame):
        values = [frame[y * width + x] for x, y in pixels]
        return [values[k] - (values[k - 1] + values[k] + values[k + 1]) / 3 for k in range(1, len(values) - 1)]

    kept, total = high_pass(source), high_pass(output)
    return sum(a * b for a, b in zip(kept, total)) / sum(a * a for a in kept)


def area_detail(source, output, width, x0, y0, x1, y1):
    """The same for the pixels of a box, each less the mean of the 3 x 3 pixels around it."""
    def high_pass(frame):
        return [
            frame[y * width + x] - sum(frame[(y + dy) * width + x + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)) / 9
            for y in range(y0, y1) for x in range(x0, x1)
        ]

    kept, total = high_pass(source), high_pass(output)
    return sum(a * b for a, b in zip(kept, total)) / sum(a * a for a in kept)


def faded_inside(region, feather, frame_size):
    """The region's rows and columns outside the bands where the refused-then-widened patch fades (feather)."""
    x, y = region["x"] - region["x"] % 2, region["y"] - region["y"] % 2
    x1, y1 = region["x"] + region["width"], region["y"] + region["height"]
    band = min(feather, max(1, min(x1 - x, y1 - y) // 3)) if feather > 0 else 0
    rows = range(max(region["y"], y + band), y1 - (0 if y1 == frame_size[1] else band))
    columns = range(max(region["x"], x + band), x1 - (0 if x1 == frame_size[0] else band))
    return rows, columns


def changed_outside(source, output, frame_size, boxes):
    """Luma pixels outside every box (x0, y0, x1, y1) that differ from the source."""
    width, height = frame_size
    return sum(
        1 for y in range(height) for x in range(width)
        if not any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in boxes)
        and source[y * width + x] != output[y * width + x]
    )


class BranchStepTests(unittest.TestCase):
    """Which pixel format each branch crops: the source's for a segment's first regional branch, 4:2:0 after."""

    EDGE = {"x": 99, "y": 30, "width": 61, "height": 20}  # 69 wide with its margin: refused at 4:2:0

    def graph(self, operations, layout, pix_fmt="yuv444p"):
        limit = 10**18 if layout == blur_layout.NESTED_LAYOUT else 0
        with patch.object(blur_layout, "NESTED_PATH_LIMIT", limit):
            graph, _, wiring = _final_filter_graph(
                operations=operations, duration=4.0, has_audio=False, frame_size=FRAME,
                chroma_rows_divisor=2, source_chroma=SourceChroma.of(pix_fmt),
            )
        self.assertEqual(wiring["layout"], layout)
        return graph

    def test_a_yuv444p_source_rounds_only_its_later_branches(self):
        for layout in (blur_layout.NESTED_LAYOUT, blur_layout.SHARED_LAYOUT):
            with self.subTest(layout=layout):
                alone = self.graph([cover(0.0, 4.0, self.EDGE)], layout)
                self.assertIn("crop=69:36:91:22,delogo", alone)
                self.assertNotIn("exact", alone)
                # After a full-frame blur (gblur keeps yuv444p) it is still the segment's first regional branch.
                self.assertNotIn("exact", self.graph([full_frame(0.0, 4.0), cover(0.0, 4.0, self.EDGE)], layout))
                later = self.graph([cover(0.0, 4.0, EARLIER), cover(0.0, 4.0, self.EDGE)], layout)
                self.assertIn("crop=70:36:90:22:exact=1,delogo", later)
                self.assertEqual(later.count("exact=1"), 2)  # its two crops; the earlier region ran
                # Each kept segment after a CUT starts again with the source's format.
                cut = {"id": "cut", "type": "cut", "start_seconds": 1.0, "end_seconds": 2.0}
                segments = self.graph([cut, cover(0.0, 4.0, EARLIER), cover(0.0, 4.0, self.EDGE)], layout)
                self.assertEqual(segments.count("crop=70:36:90:22:exact=1,delogo"), 2)
                first_only = self.graph([cut, cover(0.0, 4.0, self.EDGE)], layout)
                self.assertEqual(first_only.count("crop=69:36:91:22,delogo"), 2)
                self.assertNotIn("exact", first_only)

    def test_a_420_source_rounds_every_branch(self):
        for layout in (blur_layout.NESTED_LAYOUT, blur_layout.SHARED_LAYOUT):
            with self.subTest(layout=layout):
                self.assertIn("crop=70:36:90:22:exact=1,delogo",
                              self.graph([cover(0.0, 4.0, self.EDGE)], layout, pix_fmt="yuv420p"))

    def test_an_rgb_source_rounds_its_first_branch_only_after_a_full_frame_blur(self):
        cut = {"id": "cut", "type": "cut", "start_seconds": 1.0, "end_seconds": 2.0}
        for layout in (blur_layout.NESTED_LAYOUT, blur_layout.SHARED_LAYOUT):
            with self.subTest(layout=layout):
                alone = self.graph([cover(0.0, 4.0, self.EDGE)], layout, pix_fmt="rgb24")
                self.assertIn("crop=69:36:91:22,delogo", alone)
                self.assertNotIn("exact", alone)
                after = self.graph([full_frame(0.0, 4.0), cover(0.0, 4.0, self.EDGE)], layout, pix_fmt="rgb24")
                self.assertIn("crop=70:36:90:22:exact=1,delogo", after)
                # The full-frame blur of the first kept segment changes nothing in the second.
                # (The renderer orders blurs by start, the longer first: the region is on twice, briefly.)
                segments = self.graph([cut, full_frame(0.0, 0.5), cover(0.0, 0.4, self.EDGE),
                                       cover(2.5, 2.9, self.EDGE)], layout, pix_fmt="rgb24")
                self.assertEqual(segments.count("crop=70:36:90:22:exact=1,delogo"), 1)
                self.assertEqual(segments.count("crop=69:36:91:22,delogo"), 1)
                self.assertLess(segments.index("exact=1"), segments.index("crop=69:36:91:22,delogo"))

    def test_an_unknown_format_keeps_the_chain_of_before_in_every_branch(self):
        for layout in (blur_layout.NESTED_LAYOUT, blur_layout.SHARED_LAYOUT):
            with self.subTest(layout=layout):
                graph = self.graph([cover(0.0, 4.0, self.EDGE)], layout, pix_fmt=None)
                self.assertIn("crop=69:36:91:22,delogo", graph)
                self.assertNotIn("exact", graph)


class PreviewStepTests(unittest.TestCase):
    """The review previews (CLI render-previews) build the same branch from their own probe."""

    def preview_chain(self, pix_fmt):
        from biliflow.review_workflow import render_edit_previews

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "previews").mkdir()
            source, ffmpeg, ffprobe = root / "source.mp4", root / "ffmpeg.exe", root / "ffprobe.exe"
            for path in (source, ffmpeg, ffprobe):
                path.write_bytes(b"x")
            plan = root / "plan.json"
            plan.write_text(json.dumps({
                "status": "READY_FOR_PREVIEW", "source": {"path": str(source), "duration_seconds": 10.0},
                "approved_operations": [cover(2.0, 4.0, BranchStepTests.EDGE)],
            }), encoding="utf-8")
            probe = {"format": {"duration": "10.0"}, "streams": [
                {"codec_type": "video", "width": FRAME[0], "height": FRAME[1], "pix_fmt": pix_fmt}]}
            commands = []

            def fake_run(command):
                commands.append(command)
                Path(command[-1]).write_bytes(b"mp4")

            with patch("biliflow.review_workflow.probe_video", return_value=probe), \
                    patch("biliflow.review_workflow._run", side_effect=fake_run):
                render_edit_previews(project_root=root, plan_path=plan, output_dir=root / "previews" / "p",
                                     ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
        (command,) = commands
        return command[command.index("-filter_complex") + 1]

    def test_the_preview_rounds_by_the_probed_pixel_format(self):
        self.assertIn("crop=69:36:91:22,delogo", self.preview_chain("yuv444p"))
        self.assertIn("crop=70:36:90:22:exact=1,delogo", self.preview_chain("yuv420p"))


@unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
class CropRoundingModelTests(unittest.TestCase):
    """The rule the fix relies on, checked on FFmpeg itself: 11a059c's chain runs exactly when old_chain_fits says so."""

    def test_the_old_chain_runs_exactly_when_delogo_fits_the_rounded_crop(self):
        regions = [region for _, region in edge_regions(margins=range(4))
                   if region["width"] in (40, 41, 60, 61) and region["height"] in (20, 21)]
        refused = 0
        with TemporaryDirectory() as directory:
            for pix_fmt, step in STEPS.items():
                for region in regions:
                    with self.subTest(pix_fmt=pix_fmt, region=region):
                        done = subprocess.run(
                            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                             source_filter(pix_fmt, *FRAME, noise=False),
                             "-vf", old_cover_chain(region, FRAME, "enable=1"), "-frames:v", "1", "-f", "null", "-"],
                            capture_output=True, text=True, timeout=60, cwd=directory,
                        )
                        fits = old_chain_fits(region, FRAME, step)
                        self.assertEqual(done.returncode == 0, fits, done.stderr[-300:])
                        if not fits:
                            refused += 1
                            self.assertIn("Logo area is outside of the frame", done.stderr)
        self.assertGreater(refused, 20)


@unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
class CoverEdgeExportTests(unittest.TestCase):
    """Real FFmpeg, the export's own graph: refused edge regions now run and are blurred up to the frame edge."""

    def check(self, region, *, pix_fmt, feather, later, layout, folder) -> bool:
        """True when 11a059c refused the region (now checked for its blur), False when it ran (same text)."""
        width, height = FRAME
        step = CHROMA_420 if later else STEPS[pix_fmt]
        graph = export_graph(region, pix_fmt=pix_fmt, feather=feather, later=later, layout=layout)
        code, stderr, source, output = run_graph(graph, pix_fmt, folder)
        self.assertEqual(code, 0, stderr[-1500:])
        if old_chain_fits(region, FRAME, step):
            self.assertNotIn("exact", graph)
            return False
        self.assertIn("exact=1", graph)
        x0, y0 = region["x"], region["y"]
        x1, y1 = x0 + region["width"], y0 + region["height"]
        self.assertLess(area_detail(source, output, width, x0 + 4, y0 + 4, x1 - 4, y1 - 4), BLURRED_BELOW)
        # The frame's last column (row), on every row (column) of the region but those
        # where a faded side of the patch fades into the picture delogo could not clean.
        rows, columns = faded_inside(region, feather, FRAME)
        if x1 == width:
            self.assertLess(line_detail(source, output, width, [(x1 - 1, y) for y in rows]), BLURRED_BELOW)
        if y1 == height:
            self.assertLess(line_detail(source, output, width, [(x, y1 - 1) for x in columns]), BLURRED_BELOW)
        # Nothing changes past the region but the pixel left of and above an odd corner.
        boxes = [(x0 - x0 % 2, y0 - y0 % 2, x1, y1)]
        if later:
            boxes.append((EARLIER["x"], EARLIER["y"], EARLIER["x"] + EARLIER["width"],
                          EARLIER["y"] + EARLIER["height"]))
        self.assertEqual(changed_outside(source, output, FRAME, boxes), 0)
        return True

    def matrix(self, formats, *, later, layouts, feathers=(0, 2)) -> int:
        regions = [region for _, region in edge_regions(margins=range(3))
                   if region["width"] in (40, 41, 60, 61) and region["height"] in (20, 21)]
        fixed = 0
        with TemporaryDirectory() as directory:
            for pix_fmt, feather, layout, region in product(formats, feathers, layouts, regions):
                with self.subTest(pix_fmt=pix_fmt, feather=feather, layout=layout, later=later, region=region):
                    fixed += self.check(region, pix_fmt=pix_fmt, feather=feather, later=later, layout=layout,
                                        folder=Path(directory))
        return fixed

    def test_the_first_branch_of_a_segment_crops_the_source_format(self):
        fixed = self.matrix(("yuv420p", "yuv422p", "yuv444p", "yuv410p"), later=False,
                            layouts=(blur_layout.NESTED_LAYOUT,))
        self.assertGreater(fixed, 20)

    def test_later_branches_crop_420_whatever_the_source(self):
        fixed = self.matrix(("yuv444p", "yuv410p"), later=True, layouts=(blur_layout.NESTED_LAYOUT,))
        self.assertGreater(fixed, 10)

    def test_the_shared_layout_wires_the_same_branches(self):
        fixed = self.matrix(("yuv420p", "yuv422p", "yuv444p", "yuv410p"), later=False,
                            layouts=(blur_layout.SHARED_LAYOUT,), feathers=(2,))
        fixed += self.matrix(("yuv444p",), later=True, layouts=(blur_layout.SHARED_LAYOUT,), feathers=(2,))
        self.assertGreater(fixed, 10)

    def test_job_141_region_on_a_frame_of_its_size(self):
        width, height = JOB_141_FRAME
        x0, y0 = JOB_141_REGION["x"], JOB_141_REGION["y"]
        y1 = y0 + JOB_141_REGION["height"]
        with TemporaryDirectory() as directory:
            for feather in (0, 2):
                with self.subTest(feather=feather):
                    graph = export_graph(JOB_141_REGION, pix_fmt="yuv420p", feather=feather, later=False,
                                         layout=blur_layout.NESTED_LAYOUT, frame_size=JOB_141_FRAME)
                    code, stderr, source, output = run_graph(graph, "yuv420p", Path(directory), JOB_141_FRAME)
                    self.assertEqual(code, 0, stderr[-1500:])
                    self.assertLess(area_detail(source, output, width, x0 + 4, y0 + 4, width - 4, y1 - 4),
                                    BLURRED_BELOW)
                    rows, _ = faded_inside(JOB_141_REGION, feather, JOB_141_FRAME)
                    for column in (width - 1, width - 2):  # 11a059c's gblur never reached these two
                        self.assertLess(line_detail(source, output, width, [(column, y) for y in rows]),
                                        BLURRED_BELOW, column)
                    for row in (y1 - 1, y1 - 2):
                        self.assertLess(line_detail(source, output, width, [(x, row) for x in range(x0, width)]),
                                        BLURRED_BELOW, row)
                    self.assertEqual(changed_outside(source, output, JOB_141_FRAME, [(x0 - 1, y0 - 1, width, y1)]), 0)

    def test_a_10_bit_source_gets_the_colours_of_the_8_bit_one(self):
        # crop exact=1 at an odd x reads a 10-bit chroma plane from the wrong bytes;
        # the fix crops from an even corner.
        region = {"x": 99, "y": 31, "width": 61, "height": 21}
        planes = {}
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            for pix_fmt in ("yuv420p", "yuv420p10le"):
                graph = export_graph(region, pix_fmt=pix_fmt, feather=2, later=False,
                                     layout=blur_layout.NESTED_LAYOUT)
                self.assertIn("exact=1", graph)
                (folder / "graph.txt").write_text(graph, encoding="utf-8")
                done = subprocess.run(
                    [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                     source_filter(pix_fmt, *FRAME),
                     "-/filter_complex", str(folder / "graph.txt"), "-map", "[vout]", "-frames:v", "1",
                     "-f", "rawvideo", "-pix_fmt", "yuv444p", "-"],
                    capture_output=True, timeout=60,
                )
                self.assertEqual(done.returncode, 0, done.stderr[-400:])
                planes[pix_fmt] = done.stdout
        size = FRAME[0] * FRAME[1]
        for plane in (1, 2):
            eight, ten = (planes[name][plane * size:(plane + 1) * size] for name in ("yuv420p", "yuv420p10le"))
            worst = max(abs(eight[y * FRAME[0] + x] - ten[y * FRAME[0] + x])
                        for y in range(region["y"], region["y"] + region["height"])
                        for x in range(region["x"], FRAME[0]))
            self.assertLessEqual(worst, 6, plane)


@unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
class SourceFormatExportTests(unittest.TestCase):
    """Real FFmpeg on sources gblur may not take (OTHER_STEPS), alone and after a full-frame blur."""

    def test_the_chain_of_before_runs_exactly_where_the_measured_step_says_and_every_region_exports(self):
        regions = [region for _, region in edge_regions(margins=range(2))
                   if region["width"] in (40, 41, 60, 61) and region["height"] in (20, 21)]
        width, height = FRAME
        fixed = 0
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            for pix_fmt, after_full_frame, region in product(OTHER_STEPS, (False, True), regions):
                with self.subTest(pix_fmt=pix_fmt, after_full_frame=after_full_frame, region=region):
                    step = OTHER_STEPS[pix_fmt][after_full_frame]
                    graphs = {
                        name: export_graph(region, pix_fmt=pix_fmt, feather=2, later=False,
                                           layout=blur_layout.NESTED_LAYOUT, after_full_frame=after_full_frame,
                                           source_chroma=chroma)
                        for name, chroma in (("before", SourceChroma(NO_ROUNDING, NO_ROUNDING)), ("now", None))
                    }
                    frame = AFTER_FULL_FRAME_FRAME if after_full_frame else 0
                    code, stderr, _, _ = run_graph(graphs["before"], pix_fmt, folder, frame=frame)
                    fits = old_chain_fits(region, FRAME, step)
                    self.assertEqual(code == 0, fits, stderr[-300:])
                    if fits:
                        self.assertEqual(graphs["now"], graphs["before"])
                        continue
                    fixed += 1
                    self.assertIn("Logo area is outside of the frame", stderr)
                    self.assertIn("exact=1", graphs["now"])
                    code, stderr, source, output = run_graph(graphs["now"], pix_fmt, folder, frame=frame)
                    self.assertEqual(code, 0, stderr[-1500:])
                    x0, y0 = region["x"], region["y"]
                    x1, y1 = x0 + region["width"], y0 + region["height"]
                    self.assertLess(area_detail(source, output, width, x0 + 4, y0 + 4, x1 - 4, y1 - 4),
                                    BLURRED_BELOW)
                    rows, columns = faded_inside(region, 2, FRAME)
                    if x1 == width:
                        self.assertLess(line_detail(source, output, width, [(x1 - 1, y) for y in rows]),
                                        BLURRED_BELOW)
                    if y1 == height:
                        self.assertLess(line_detail(source, output, width, [(x, y1 - 1) for x in columns]),
                                        BLURRED_BELOW)
        self.assertGreater(fixed, 20)

    def test_a_full_frame_blur_in_one_kept_segment_leaves_the_next_one_its_source_format(self):
        region = BranchStepTests.EDGE  # 69 wide with its margin: refused only where crop rounds to 4:2:0
        operations = [{"id": "cut", "type": "cut", "start_seconds": 0.1, "end_seconds": 0.2},
                      full_frame(0.0, 0.08), cover(0.0, 0.06, region), cover(0.25, 0.31, region)]
        graph, _, _ = _final_filter_graph(operations=operations, duration=0.4, has_audio=False, frame_size=FRAME,
                                          chroma_rows_divisor=2, source_chroma=SourceChroma.of("rgb24"))
        self.assertEqual(graph.count("exact=1"), 2)  # the first segment's two crops
        with TemporaryDirectory() as directory:
            script = Path(directory) / "graph.txt"
            script.write_text(graph, encoding="utf-8")
            done = subprocess.run(
                [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 f"testsrc2=s={FRAME[0]}x{FRAME[1]}:r=25:d=0.4,format=rgb24",
                 "-/filter_complex", str(script), "-map", "[vout]", "-pix_fmt", "yuv420p", "-f", "null", "-"],
                capture_output=True, text=True, timeout=60,
            )
        self.assertEqual(done.returncode, 0, done.stderr[-1500:])


if __name__ == "__main__":
    unittest.main()
