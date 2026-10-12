"""The export's filter graph reaches FFmpeg through a file (perf phase 2, step 3), with real FFmpeg."""
import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.blur_filter import COVER_METHOD
from biliflow.final_renderer import build_final_filter_graph, render_final_output, render_graph_path

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = Path(os.environ.get("BILIFLOW_FFMPEG") or ROOT / "tools/ffmpeg/bin/ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe" + FFMPEG.suffix)
# Longest command line CreateProcess accepts, in characters.
WINDOWS_COMMAND_LINE_LIMIT = 32_767
SIZE = (320, 180)
SECONDS = 6.0


def blur(start, end, region, *, method="gblur", feather=4):
    return {
        "id": f"blur-{start}-{region['x']}", "type": "blur", "start_seconds": start, "end_seconds": end,
        "region_source_pixels": region,
        "blur": {"sigma": 28, "edge_feather_pixels": feather, "edge_feather_mode": "all_edges", "method": method},
    }


def many_cuts():
    """29 cuts of 2 frames, every 5 frames (25 fps), and two feathered regions over the whole clip."""
    cuts = [
        {"id": f"cut-{index}", "type": "cut",
         "start_seconds": round(0.12 + 0.2 * index, 2), "end_seconds": round(0.2 + 0.2 * index, 2)}
        for index in range(29)
    ]
    return cuts + [
        blur(0.0, SECONDS, {"x": 20, "y": 10, "width": 120, "height": 40}, method=COVER_METHOD),
        blur(0.0, SECONDS, {"x": 160, "y": 120, "width": 140, "height": 48}),
    ]


@unittest.skipUnless(FFMPEG.exists() and FFPROBE.exists(), "project FFmpeg and ffprobe are required")
class RenderGraphFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        cls.root = Path(cls.directory.name).resolve()
        for name in ("input", "work", "output", "state"):
            (cls.root / name).mkdir()
        cls.source = cls.root / "input" / "source.mp4"
        width, height = SIZE
        subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:r=25:d={SECONDS:g}",
             "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:d={SECONDS:g}",
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.source)],
            capture_output=True, timeout=120, check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def framemd5(self, graph_arguments):
        return subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(self.source), *graph_arguments,
             "-map", "[vout]", "-map", "[aout]", "-pix_fmt", "yuv420p", "-f", "framemd5", "-"],
            capture_output=True, timeout=120, check=True,
        ).stdout

    def test_the_file_gives_the_frames_of_the_same_graph_on_the_command_line(self):
        operations = [
            {"id": "cut", "type": "cut", "start_seconds": 2.2, "end_seconds": 2.6},
            blur(0.5, 5.0, {"x": 100, "y": 50, "width": 80, "height": 64}, method=COVER_METHOD),
            blur(3.0, 4.0, {"x": 10, "y": 100, "width": 60, "height": 14}, feather=0),
        ]
        graph, _ = build_final_filter_graph(operations=operations, duration=SECONDS, has_audio=True,
                                            frame_size=SIZE)
        path = self.root / "work" / "graph.txt"
        path.write_bytes(graph.encode("utf-8"))
        inline = self.framemd5(["-filter_complex", graph])
        self.assertGreater(inline.count(b"\n0,"), 100)
        self.assertEqual(self.framemd5(["-/filter_complex", str(path)]), inline)

    def test_a_graph_longer_than_a_windows_command_line_renders(self):
        operations = many_cuts()
        graph, expected = build_final_filter_graph(operations=operations, duration=SECONDS, has_audio=True,
                                                   frame_size=SIZE)
        self.assertGreater(len(graph), WINDOWS_COMMAND_LINE_LIMIT)
        plan_path = self.root / "work" / "long-plan.json"
        plan_path.write_text(json.dumps({
            "status": "READY_FOR_FINAL_RENDER", "final_export_allowed": True,
            "source": {"path": str(self.source), "duration_seconds": SECONDS,
                       "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()},
            "approved_operations": operations,
            # This test is about the graph file, not the encoder: x264, outside the GPU slot.
            "render_settings": {"video_encoder": "libx264"},
        }), encoding="utf-8")
        output = self.root / "output" / "long.mp4"
        manifest = render_final_output(project_root=self.root, plan_path=plan_path, output_path=output,
                                       ffmpeg_path=FFMPEG, ffprobe_path=FFPROBE)
        self.assertEqual(manifest["status"], "COMPLETED")
        self.assertAlmostEqual(manifest["output"]["duration_seconds"], expected, delta=0.25)
        self.assertTrue(manifest["encoding"]["full_decode_validation_passed"])
        self.assertFalse(render_graph_path(self.root, output).exists())


if __name__ == "__main__":
    unittest.main()
