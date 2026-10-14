"""Exports with many blur regions (job 134, 2026-10-14), with real FFmpeg.

The nested graph of 11 or more regions never finished a frame; the shared layout
(blur_layout) must export 30 and 60 regions in a few seconds, blur every region
while it is on (overlapping ones included, also where a region reads an older
picture), leave it alone when it is off, and give the nested layout's frames
wherever both run.
"""
import hashlib
import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import blur_layout
from biliflow.blur_filter import COVER_METHOD, GBLUR
from biliflow.final_renderer import _final_filter_graph, render_final_output

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = Path(os.environ.get("BILIFLOW_FFMPEG") or ROOT / "tools/ffmpeg/bin/ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe" + FFMPEG.suffix)
WIDTH, HEIGHT, SECONDS = 640, 360, 12.0
FPS = 25
CUTS = ((0.0, 1.0), (6.0, 6.5))
# Far above the few seconds these exports take; a stall fails sooner (the watch, patched
# below, still above the 98.3 s a healthy export went quiet with the CPU at 100 %).
DEADLINE_SECONDS = 180
TEST_STALL_SECONDS = 120.0
INSET_PIXELS = 4  # past the 2-pixel feather and the chroma rounding
# The share of the source's fine detail a region keeps (sharpness): 0.945 where
# it is not blurred. One region exported alone, in the nested layout as before
# 2026-10-14, keeps 0.002 with the cover method and 0.013 with plain gblur; a
# quarter of a region left sharp would keep about 0.24.
BLURRED_BELOW = 0.05
SHARP_ABOVE = 0.5
PROBE = {"x": 250, "y": 150, "width": 120, "height": 60}  # alone in the middle while on
STRIP = {"x": 0, "y": 8, "width": 640, "height": 24}


def blur(start, end, region, *, method=COVER_METHOD, feather=2, sigma=28, name=None):
    return {
        "id": name or f"blur-{start}-{region}", "type": "blur", "start_seconds": start, "end_seconds": end,
        "region_source_pixels": region,
        "blur": {"sigma": sigma, "edge_feather_pixels": feather, "edge_feather_mode": "all_edges",
                 "method": method},
    }


def many_regions(count):
    """Two strips over the whole clip (one over the other), subtitle-band cards and one probe region.

    Some cards come back later in the same place (one region with two time ranges),
    some overlap the card before them in place and time; two CUTs.
    """
    operations = [{"id": f"cut-{index}", "type": "cut", "start_seconds": start, "end_seconds": end}
                  for index, (start, end) in enumerate(CUTS)]
    operations += [blur(0.0, SECONDS, STRIP, name="strip"),
                   blur(0.0, SECONDS, {"x": 400, "y": 4, "width": 200, "height": 30}, name="logo"),
                   blur(3.0, 4.0, PROBE, sigma=40, name="probe")]
    for k in range(count - 3):
        x = 10 + 70 * (k % 9) + (30 if k % 7 == 6 else 0)
        region = {"x": x, "y": 280 + 30 * ((k // 9) % 2), "width": 60, "height": 24}
        start = round(1.0 + (k * 0.37) % 9.0, 2)
        operations.append(blur(start, min(SECONDS, start + 1.5), region, method=(GBLUR if k % 5 == 4 else COVER_METHOD),
                               name=f"card-{k}"))
        if k % 5 == 0:
            again = min(SECONDS - 0.5, start + 4.0)
            operations.append(blur(again, again + 0.5, region, name=f"card-{k}-again"))
    return operations


def output_time(source_seconds):
    removed = sum(end - start for start, end in CUTS if end <= source_seconds)
    return source_seconds - removed


def blur_checks(operations):
    """(name, source time, region) in the middle of every blur, away from the CUTs."""
    checks = []
    for item in operations:
        if item["type"] != "blur":
            continue
        middle = round((item["start_seconds"] + item["end_seconds"]) / 2, 2)
        if any(start - 0.2 <= middle <= end + 0.2 for start, end in CUTS):
            continue
        checks.append((item["id"], middle, item["region_source_pixels"]))
    return checks


@unittest.skipUnless(FFMPEG.exists() and FFPROBE.exists(), "project FFmpeg and ffprobe are required")
class ManyRegionsExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.root = Path(directory.name).resolve()
        for name in ("input", "work", "output", "state"):
            (cls.root / name).mkdir()
        cls.source = cls.root / "input" / "source.mp4"
        subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error",
             # Static noise: fine detail in every region, the same in every frame.
             "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d={SECONDS:g},noise=alls=40:allf=u",
             "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:d={SECONDS:g}",
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.source)],
            capture_output=True, timeout=120, check=True,
        )
        cls.sha = hashlib.sha256(cls.source.read_bytes()).hexdigest()
        cls.source_frames = cls.gray_frames(cls.source)

    def export(self, count):
        operations = many_regions(count)
        # The strips and the probe, the cards, and every fifth card again later.
        cards = count - 3
        self.assertEqual(sum(item["type"] == "blur" for item in operations), 3 + cards + len(range(0, cards, 5)))
        plan_path = self.root / "work" / f"plan-{count}.json"
        plan_path.write_text(json.dumps({
            "status": "READY_FOR_FINAL_RENDER", "final_export_allowed": True,
            "source": {"path": str(self.source), "duration_seconds": SECONDS, "sha256": self.sha},
            "approved_operations": operations,
            "render_settings": {"video_encoder": "libx264"},
        }), encoding="utf-8")
        output = self.root / "output" / f"regions-{count}.mp4"
        started = time.monotonic()
        with patch("biliflow.render_watchdog.STALL_SECONDS", TEST_STALL_SECONDS):
            manifest = render_final_output(project_root=self.root, plan_path=plan_path, output_path=output,
                                           ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE)
        return manifest, output, time.monotonic() - started

    @staticmethod
    def gray_frames(video):
        """Every frame of ``video`` as 8-bit luma."""
        data = subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(video),
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=120, check=True,
        ).stdout
        size = WIDTH * HEIGHT
        return [data[start:start + size] for start in range(0, len(data) - size + 1, size)]

    @staticmethod
    def fine_detail(frame, region):
        """Each pixel of a region, past its feathered edge, less the mean of the 3 x 3 pixels around it."""
        values = []
        for row in range(region["y"] + INSET_PIXELS, region["y"] + region["height"] - INSET_PIXELS):
            for column in range(region["x"] + INSET_PIXELS, region["x"] + region["width"] - INSET_PIXELS):
                around = sum(frame[(row + dy) * WIDTH + column + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
                values.append(frame[row * WIDTH + column] - around / 9)
        return values

    def sharpness(self, frames, source_seconds, region):
        """How much of the source's fine detail the exported region still holds (1: all of it).

        Not how much detail it has: the seam where two blurred regions meet is
        detail too, and the cover method fills a region with a gradient. Nor the
        correlation: a smooth region's last trace of curvature can follow a
        sharp edge of the source while holding nothing of it.
        """
        source = self.fine_detail(self.source_frames[int(source_seconds * FPS)], region)
        exported = self.fine_detail(frames[min(len(frames) - 1, int(output_time(source_seconds) * FPS))], region)
        return sum(a * b for a, b in zip(source, exported)) / sum(a * a for a in source)

    def assert_blurred(self, frames, source_seconds, region, *, blurred, name):
        sharpness = self.sharpness(frames, source_seconds, region)
        if blurred:
            self.assertLess(sharpness, BLURRED_BELOW, (name, source_seconds, region))
        else:
            self.assertGreater(sharpness, SHARP_ABOVE, (name, source_seconds, region))

    def test_30_and_60_regions_export_in_seconds_and_blur_where_and_when_they_should(self):
        for count in (30, 60):
            with self.subTest(regions=count):
                manifest, output, seconds = self.export(count)
                self.assertLess(seconds, DEADLINE_SECONDS)
                self.assertEqual(manifest["status"], "COMPLETED")
                self.assertAlmostEqual(manifest["output"]["duration_seconds"],
                                       SECONDS - sum(end - start for start, end in CUTS), delta=0.25)
                self.assertTrue(manifest["encoding"]["full_decode_validation_passed"])
                wiring = manifest["encoding"]["filter_graph"]
                self.assertEqual(wiring["layout"], "shared")
                self.assertLessEqual(wiring["paths"], blur_layout.SHARED_PATH_BUDGET)
                # Chains of overlapping cards: past the budget some read an older picture.
                self.assertGreater(wiring["regions_reading_older_picture"], 0)
                frames = self.gray_frames(output)
                checks = blur_checks(many_regions(count))
                self.assertGreater(len(checks), count)
                for name, middle, region in checks:
                    self.assert_blurred(frames, middle, region, blurred=True, name=name)
                self.assert_blurred(frames, 2.0, STRIP, blurred=True, name="strip")
                self.assert_blurred(frames, 8.0, STRIP, blurred=True, name="strip")  # after both CUTs
                self.assert_blurred(frames, 5.0, PROBE, blurred=False, name="probe")  # on 3.0-4.0 only


def lavfi_graph_framemd5(graph, pix_fmt, folder):
    path = folder / f"graph-{pix_fmt}.txt"
    path.write_text(graph, encoding="utf-8")
    out = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         f"testsrc2=size=320x180:rate=25:duration=4,format={pix_fmt}", "-/filter_complex", str(path),
         "-map", "[vout]", "-pix_fmt", "yuv420p", "-f", "framemd5", "-"],
        capture_output=True, timeout=300, check=True,
    ).stdout
    return [line for line in out.splitlines() if not line.startswith(b"#")]


@unittest.skipUnless(FFMPEG.exists(), "project FFmpeg is required")
class LayoutEquivalenceTests(unittest.TestCase):
    """The shared layout gives the nested layout's frames, overlapping regions included."""

    PLANS = {
        "overlapping_chain_cut_and_full_frame": [
            {"id": "cut", "type": "cut", "start_seconds": 1.5, "end_seconds": 1.9},
            blur(0.0, 4.0, {"x": 10, "y": 10, "width": 120, "height": 40}),
            blur(0.5, 3.0, {"x": 60, "y": 30, "width": 90, "height": 50}),
            blur(1.0, 2.5, {"x": 100, "y": 40, "width": 60, "height": 30}, method=GBLUR, feather=4),
            {**blur(2.0, 2.4, "FULL_FRAME"), "region_source_pixels": "FULL_FRAME"},
            blur(2.2, 3.6, {"x": 120, "y": 50, "width": 70, "height": 40}),
            blur(0.0, 4.0, {"x": 200, "y": 120, "width": 100, "height": 40}, feather=0),
            blur(3.0, 3.5, {"x": 10, "y": 10, "width": 120, "height": 40}),
        ],
        "same_place_at_other_times": [
            blur(0.2, 0.8, {"x": 40, "y": 120, "width": 120, "height": 30}),
            blur(2.0, 2.6, {"x": 40, "y": 120, "width": 120, "height": 30}),
            blur(0.0, 4.0, {"x": 0, "y": 4, "width": 320, "height": 20}),
            blur(0.5, 1.5, {"x": 170, "y": 120, "width": 100, "height": 30}, method=GBLUR),
            blur(1.0, 3.0, {"x": 172, "y": 124, "width": 96, "height": 26}, method=GBLUR, feather=0),
            blur(3.0, 4.0, {"x": 100, "y": 60, "width": 80, "height": 40}),
        ],
    }

    def graph(self, operations, limit):
        with patch.object(blur_layout, "NESTED_PATH_LIMIT", limit):
            graph, _, wiring = _final_filter_graph(operations=operations, duration=4.0, has_audio=False,
                                                   frame_size=(320, 180), chroma_rows_divisor=2)
        return graph, wiring

    def test_both_layouts_give_the_same_frames_on_three_pixel_formats(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, operations in self.PLANS.items():
                nested, nested_wiring = self.graph(operations, 10**18)
                shared, shared_wiring = self.graph(operations, 0)
                self.assertEqual((nested_wiring["layout"], shared_wiring["layout"]), ("nested", "shared"))
                self.assertEqual(shared_wiring["regions_reading_older_picture"], 0)
                self.assertNotEqual(nested, shared)
                for pix_fmt in ("yuv420p", "yuv420p10le", "yuv444p"):
                    with self.subTest(plan=name, pix_fmt=pix_fmt):
                        frames = lavfi_graph_framemd5(nested, pix_fmt, folder)
                        self.assertGreater(len(frames), 80)
                        self.assertEqual(lavfi_graph_framemd5(shared, pix_fmt, folder), frames)


if __name__ == "__main__":
    unittest.main()
