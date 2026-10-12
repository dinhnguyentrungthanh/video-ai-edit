import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow.cli import main


class CliTests(unittest.TestCase):
    def test_nsfw_scan_routes_only_supported_arguments(self):
        with TemporaryDirectory() as directory:
            argv = [
                "biliflow", "--project-root", directory, "scan",
                "--input", "input.mp4", "--report-dir", "reports/result",
            ]
            with patch.object(sys, "argv", argv), patch(
                "biliflow.scanner.scan_nsfw", return_value={"status": "COMPLETED"}
            ) as scanner, patch(
                "biliflow.cli.ensure_model_allowed"
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            arguments = scanner.call_args.kwargs
            self.assertEqual(arguments["threshold"], 0.95)
            self.assertEqual(arguments["review_merge_gap_seconds"], 3.0)
            self.assertEqual(arguments["sequence_context_threshold"], 0.70)
            self.assertEqual(arguments["sequence_context_seconds"], 8.0)
            self.assertEqual(arguments["content_style"], "unknown")
            self.assertNotIn("adult_threshold", arguments)
            self.assertEqual(arguments["input_path"], Path("input.mp4"))

    def test_visual_logo_exhaustive_mode_reaches_scanner(self):
        with TemporaryDirectory() as directory:
            argv = [
                "biliflow", "--project-root", directory, "scan-visual-logo",
                "--input", "input.mp4", "--report-dir", "reports/result",
                "--exhaustive",
            ]
            result = {
                "frames_scanned": 1,
                "candidate_windows": 1,
                "intervals": [],
                "answer_counts": {"CONFIRMED": 0, "REJECTED": 1, "UNCERTAIN": 0},
                "metrics": {},
            }
            with patch.object(sys, "argv", argv), patch(
                "biliflow.visual_logo_scanner.scan_visual_logos", return_value=result
            ) as scanner, patch(
                "biliflow.cli.ensure_model_allowed"
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertTrue(scanner.call_args.kwargs["exhaustive"])

    def test_render_previews_routes_sampled_operation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            argv = [
                "biliflow", "--project-root", directory, "render-previews",
                "--plan", "work/plan.json", "--output-dir", "previews/job",
                "--operation-id", "op-blur",
            ]
            with patch.object(sys, "argv", argv), patch(
                "biliflow.review_workflow.render_edit_previews", return_value={"status": "done"}
            ) as renderer, redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertEqual(renderer.call_args.kwargs["operation_ids"], ("op-blur",))

    def test_importing_the_cli_loads_no_model_library(self):
        # Each stage is its own process; the CLI must leave torch, timm, easyocr and
        # transformers to the one command that needs them.
        code = ("import sys, biliflow.cli; print(','.join(sorted(name for name in "
                "('torch', 'timm', 'easyocr', 'transformers', 'cv2') if name in sys.modules)))")
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parents[1], check=True)
        self.assertEqual(done.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
