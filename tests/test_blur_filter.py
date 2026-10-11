"""Regional blur filters: the legacy blur and the cover method (delogo, then blur; user decision 2026-10-04)."""
import hashlib
import os
import re
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.blur_filter import (
    COVER_METHOD,
    GBLUR,
    blur_method,
    cover_sigma,
    regional_blur_filters,
)
from biliflow.final_renderer import build_final_filter_graph

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
    def test_default_method_blurs_only_the_region_and_only_while_enabled(self):
        # Perf plan A2: the per-frame filters of the branch carry the overlay's enable too.
        self.assertEqual(
            regional_blur_filters(
                input_label="in", output_label="out", prefix="p", region=LOGO,
                sigma=28, feather=0, enable="enable=1",
            ),
            [
                "[in]split[pbase][pcrop]",
                "[pcrop]crop=60:20:40:30,gblur=sigma=28:enable=1[pblurred]",
                "[pbase][pblurred]overlay=40:30:enable=1[out]",
            ],
        )

    def test_cover_reads_a_margin_of_picture_and_crops_back_to_the_region(self):
        self.assertEqual(cover(LOGO)[1], (
            "[pcrop]crop=76:36:32:22,delogo=x=8:y=8:w=60:h=20:enable=1,crop=60:20:8:8,"
            "gblur=sigma=28:enable=1[pblurred]"
        ))

    def test_cover_keeps_one_pixel_inside_where_the_region_touches_the_frame_edge(self):
        corner = {"x": 0, "y": 0, "width": 50, "height": 20}
        self.assertIn("crop=58:28:0:0,delogo=x=1:y=1:w=49:h=19:enable=1,crop=50:20:0:0", cover(corner)[1])
        far = {"x": 110, "y": 70, "width": 50, "height": 20}
        self.assertIn("crop=58:28:102:62,delogo=x=8:y=8:w=49:h=19:enable=1,crop=50:20:8:8", cover(far)[1])
        whole = {"x": 0, "y": 0, "width": 160, "height": 90}
        self.assertIn("crop=160:90:0:0,delogo=x=1:y=1:w=158:h=88:enable=1,crop=160:90:0:0", cover(whole)[1])

    def test_cover_without_frame_size_never_reads_past_the_region_right_and_bottom(self):
        self.assertIn(
            "crop=68:28:32:22,delogo=x=8:y=8:w=59:h=19:enable=1,crop=60:20:8:8", cover(LOGO, None)[1],
        )

    def test_cover_feathers_its_edge_into_the_cleaned_region_never_the_logo(self):
        self.assertEqual(cover(LOGO, feather=4), [
            "[in]split[pbase][pcrop]",
            "[pcrop]crop=76:36:32:22,delogo=x=8:y=8:w=60:h=20:enable=1,crop=60:20:8:8,"
            "split[pclean][pblursource]",
            "[pblursource]gblur=sigma=28:enable=1,split[pcoloursource][pmasksource]",
            "[pcoloursource]format=yuv420p[pcolour]",
            "[pmasksource]format=gray,"
            "geq=lum='255*min(1\\,min(min(X\\,W-1-X)\\,min(Y\\,H-1-Y))/4)':enable=1[pmask]",
            "[pcolour][pmask]alphamerge[psoft]",
            "[pclean][psoft]overlay=0:0[ppatch]",
            "[pbase][ppatch]overlay=40:30:enable=1[out]",
        ])

    def test_only_the_per_frame_filters_of_the_branch_carry_enable(self):
        # FFmpeg refuses enable on crop, split and format (no timeline support).
        gated = {"delogo", "gblur", "geq"}
        for method in (GBLUR, COVER_METHOD):
            for feather in (0, 3):
                with self.subTest(method=method, feather=feather):
                    chains = regional_blur_filters(
                        input_label="in", output_label="out", prefix="p", region=LOGO, sigma=28,
                        feather=feather, enable="enable=1", method=method, frame_size=FRAME,
                    )
                    filters = [
                        item for chain in chains[:-1]
                        for item in re.split(r"(?<!\\),", re.sub(r"\[[^\]]*\]", "", chain))
                    ]
                    for item in filters:
                        self.assertEqual(item.endswith(":enable=1"), item.split("=")[0] in gated, item)
                    self.assertTrue(chains[-1].endswith(":enable=1[out]"))

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


UNGATED_ENABLE = re.compile(r":enable='[^']*'")
# A regional branch filter that carries enable (full-frame gblur had it before A2).
GATED_FILTERS = (
    re.compile(r"delogo=x=\d+:y=\d+:w=\d+:h=\d+:enable"),
    re.compile(r"geq=lum='[^']*':enable"),
    re.compile(r"(?:,|blursource\])gblur=sigma=[\d.]+:enable"),
)


def ungated_regional_blur_filters(**kwargs) -> list[str]:
    """The branch as it was before perf plan A2: only its final overlay carries enable."""
    chains = regional_blur_filters(**kwargs)
    return [UNGATED_ENABLE.sub("", chain) for chain in chains[:-1]] + chains[-1:]


def blur(start, end, region, *, method=GBLUR, feather=0, mode="all_edges", sigma=28):
    return {
        "id": f"blur-{start}-{end}", "type": "blur", "start_seconds": start, "end_seconds": end,
        "region_source_pixels": region,
        "blur": {"sigma": sigma, "edge_feather_pixels": feather, "edge_feather_mode": mode, "method": method},
    }


def box(x, y, width, height):
    return {"x": x, "y": y, "width": width, "height": height}


@unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
class BlurGatingEquivalenceTests(unittest.TestCase):
    """Perf plan A2 gate on synthetic clips: gating the branch filters changes no output byte.

    framemd5 of [vout] and [aout] and the SHA-256 of the x264 mp4 (the export's
    encoder options) must be identical with and without enable on delogo, gblur and geq.
    """

    SIZE = (320, 180)
    SECONDS = 6.0
    # Regions are 64 rows high: FFmpeg's gblur gives wrong, run-dependent pixels when a plane
    # has fewer rows than its threads (yuv420p chroma: half the region), so shorter regions would
    # make both graphs unstable on machines with many CPUs (docs/evidence/perf-export/gblur-threads.md).
    CASES = {
        # Range ends on frame times (25 fps): between() includes both ends.
        "cover_feather": [blur(1.0, 2.0, box(100, 50, 80, 64), method=COVER_METHOD, feather=4, sigma=28)],
        "gblur_two_ranges_at_the_frame_edge": [
            blur(0.5, 1.02, box(0, 0, 60, 64)), blur(4.0, 4.4, box(0, 0, 60, 64)),
        ],
        "gblur_feather_vertical_only": [blur(3.0, 5.0, box(200, 100, 100, 64), feather=3, mode="vertical_only")],
        "cover_whole_clip": [blur(0.0, 6.0, box(40, 110, 150, 64), method=COVER_METHOD, sigma=28)],
        "cut_and_blurs_across_it": [
            {"id": "cut", "type": "cut", "start_seconds": 2.2, "end_seconds": 2.6},
            blur(0.0, 6.0, box(10, 10, 120, 64), method=COVER_METHOD, feather=2),
            blur(2.0, 3.0, box(150, 80, 60, 64)),
        ],
        "full_frame_and_cover_at_the_end": [
            {**blur(5.0, 5.5, "FULL_FRAME"), "region_source_pixels": "FULL_FRAME"},
            blur(5.2, 6.0, box(60, 60, 100, 64), method=COVER_METHOD, feather=2, sigma=30),
        ],
    }
    ENCODE = [
        "-map_metadata", "0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-maxrate", "4000000", "-bufsize", "8000000", "-b:v", "1000000", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "384000", "-movflags", "+faststart",
    ]

    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.source = Path(cls.directory.name) / "source.mp4"
        width, height = cls.SIZE
        subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:r=25:d={cls.SECONDS:g}",
             "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:d={cls.SECONDS:g}",
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.source)],
            capture_output=True, timeout=120, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def graphs(self, operations):
        arguments = {"operations": operations, "duration": self.SECONDS, "has_audio": True,
                     "frame_size": self.SIZE}
        gated, _ = build_final_filter_graph(**arguments)
        with patch("biliflow.final_renderer.regional_blur_filters", ungated_regional_blur_filters):
            ungated, _ = build_final_filter_graph(**arguments)
        return ungated, gated

    def ffmpeg(self, graph, output_arguments):
        return subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(self.source),
             "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", *output_arguments],
            capture_output=True, timeout=120, check=True,
        ).stdout

    def framemd5(self, graph):
        return self.ffmpeg(graph, ["-pix_fmt", "yuv420p", "-f", "framemd5", "-"])

    def mp4_sha256(self, graph, name):
        output = Path(self.directory.name) / f"{name}.mp4"
        self.ffmpeg(graph, [*self.ENCODE, "-y", str(output)])
        return hashlib.sha256(output.read_bytes()).hexdigest()

    def test_gating_changes_the_graph_but_no_frame_and_no_byte_of_the_mp4(self):
        for name, operations in self.CASES.items():
            with self.subTest(case=name):
                ungated, gated = self.graphs(operations)
                self.assertNotEqual(ungated, gated)
                for pattern in GATED_FILTERS:
                    self.assertIsNone(pattern.search(ungated), pattern.pattern)
                frames = self.framemd5(gated)
                self.assertGreater(frames.count(b"\n0,"), 100)
                self.assertEqual(self.framemd5(ungated), frames)
                self.assertEqual(self.mp4_sha256(ungated, f"{name}-ungated"),
                                 self.mp4_sha256(gated, f"{name}-gated"))


if __name__ == "__main__":
    unittest.main()
