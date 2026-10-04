"""Regional blur filters: the legacy blur and the cover method (delogo, then blur; user decision 2026-10-04)."""
import os
import subprocess
import unittest
from pathlib import Path

from biliflow.blur_filter import (
    COVER_METHOD,
    GBLUR,
    blur_method,
    cover_sigma,
    regional_blur_filters,
)

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = Path(os.environ.get("BILIFLOW_FFMPEG") or ROOT / "tools/ffmpeg/bin/ffmpeg.exe")
FRAME = (160, 90)
LOGO = {"x": 40, "y": 30, "width": 60, "height": 20}


def cover(region: dict, frame_size=FRAME, feather: int = 0) -> list[str]:
    return regional_blur_filters(
        input_label="in", output_label="out", prefix="p", region=region, sigma=cover_sigma(region),
        feather=feather, enable="enable=1", method=COVER_METHOD, frame_size=frame_size,
    )


class BlurFilterGraphTests(unittest.TestCase):
    def test_default_method_keeps_the_legacy_graph_byte_for_byte(self):
        self.assertEqual(
            regional_blur_filters(
                input_label="in", output_label="out", prefix="p", region=LOGO,
                sigma=28, feather=0, enable="enable=1",
            ),
            [
                "[in]split[pbase][pcrop]",
                "[pcrop]crop=60:20:40:30,gblur=sigma=28[pblurred]",
                "[pbase][pblurred]overlay=40:30:enable=1[out]",
            ],
        )

    def test_cover_reads_a_margin_of_picture_and_crops_back_to_the_region(self):
        self.assertEqual(cover(LOGO)[1], (
            "[pcrop]crop=76:36:32:22,delogo=x=8:y=8:w=60:h=20,crop=60:20:8:8,gblur=sigma=28[pblurred]"
        ))

    def test_cover_keeps_one_pixel_inside_where_the_region_touches_the_frame_edge(self):
        corner = {"x": 0, "y": 0, "width": 50, "height": 20}
        self.assertIn("crop=58:28:0:0,delogo=x=1:y=1:w=49:h=19,crop=50:20:0:0", cover(corner)[1])
        far = {"x": 110, "y": 70, "width": 50, "height": 20}
        self.assertIn("crop=58:28:102:62,delogo=x=8:y=8:w=49:h=19,crop=50:20:8:8", cover(far)[1])
        whole = {"x": 0, "y": 0, "width": 160, "height": 90}
        self.assertIn("crop=160:90:0:0,delogo=x=1:y=1:w=158:h=88,crop=160:90:0:0", cover(whole)[1])

    def test_cover_without_frame_size_never_reads_past_the_region_right_and_bottom(self):
        self.assertIn("crop=68:28:32:22,delogo=x=8:y=8:w=59:h=19,crop=60:20:8:8", cover(LOGO, None)[1])

    def test_cover_feathers_its_edge_into_the_cleaned_region_never_the_logo(self):
        self.assertEqual(cover(LOGO, feather=4), [
            "[in]split[pbase][pcrop]",
            "[pcrop]crop=76:36:32:22,delogo=x=8:y=8:w=60:h=20,crop=60:20:8:8,split[pclean][pblursource]",
            "[pblursource]gblur=sigma=28,split[pcoloursource][pmasksource]",
            "[pcoloursource]format=yuv420p[pcolour]",
            "[pmasksource]format=gray,geq=lum='255*min(1\\,min(min(X\\,W-1-X)\\,min(Y\\,H-1-Y))/4)'[pmask]",
            "[pcolour][pmask]alphamerge[psoft]",
            "[pclean][psoft]overlay=0:0[ppatch]",
            "[pbase][ppatch]overlay=40:30:enable=1[out]",
        ])

    def test_cover_sigma_grows_with_the_region_and_never_drops_below_28(self):
        self.assertEqual(cover_sigma({"x": 0, "y": 0, "width": 150, "height": 31}), 28)
        self.assertEqual(cover_sigma({"x": 0, "y": 0, "width": 538, "height": 108}), 65)
        self.assertEqual(cover_sigma({"x": 0, "y": 0, "width": 318, "height": 168}), 101)

    def test_method_defaults_to_gblur_and_rejects_unknown_values(self):
        self.assertEqual(blur_method({}), GBLUR)
        self.assertEqual(blur_method({"blur": {"method": COVER_METHOD}}), COVER_METHOD)
        with self.assertRaises(ValueError):
            blur_method({"blur": {"method": "paint"}})
        with self.assertRaises(ValueError):
            regional_blur_filters(
                input_label="in", output_label="out", prefix="p", region=LOGO, sigma=28,
                feather=0, enable="enable=1", method="paint",
            )


@unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
class BlurFilterRenderTests(unittest.TestCase):
    """One synthetic frame through real FFmpeg: a bright logo box on black, then the region's luma."""

    def render(self, background: str, region: dict, method: str) -> bytes:
        width, height = FRAME
        logo = f"drawbox=x={LOGO['x']}:y={LOGO['y']}:w={LOGO['width']}:h={LOGO['height']}:color=0x00E060:t=fill"
        graph = ";".join(regional_blur_filters(
            input_label="0:v", output_label="blurred", prefix="t", region=region,
            sigma=cover_sigma(region), feather=2, enable="enable=1", method=method, frame_size=FRAME,
        )) + ";[blurred]format=gray[out]"
        done = subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", f"{background}:s={width}x{height}:d=1,format=yuv420p,{logo}",
             "-filter_complex", graph, "-map", "[out]", "-frames:v", "1", "-f", "rawvideo", "-"],
            capture_output=True, timeout=60, check=True,
        )
        self.assertEqual(len(done.stdout), width * height)
        return done.stdout

    def region_max(self, pixels: bytes, region: dict) -> int:
        width = FRAME[0]
        return max(
            pixels[row * width + column]
            for row in range(region["y"], region["y"] + region["height"])
            for column in range(region["x"], region["x"] + region["width"])
        )

    def test_cover_hides_a_bright_logo_that_the_legacy_blur_leaves_visible(self):
        legacy = self.region_max(self.render("color=c=black", LOGO, GBLUR), LOGO)
        covered = self.region_max(self.render("color=c=black", LOGO, COVER_METHOD), LOGO)
        self.assertGreater(legacy, 100)  # the box fills its region: blurring alone keeps its colour
        self.assertLess(covered, 24)

    def test_cover_runs_on_regions_touching_every_frame_edge(self):
        for region in (
            {"x": 0, "y": 0, "width": 50, "height": 20},
            {"x": 110, "y": 70, "width": 50, "height": 20},
            {"x": 0, "y": 0, "width": 160, "height": 90},
            {"x": 33, "y": 21, "width": 61, "height": 23},  # odd position and size
        ):
            with self.subTest(region=region):
                self.render("testsrc2=r=25", region, COVER_METHOD)


if __name__ == "__main__":
    unittest.main()
