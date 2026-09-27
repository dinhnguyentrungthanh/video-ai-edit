import io
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
                "biliflow.cli.scan_nsfw", return_value={"status": "COMPLETED"}
            ) as scanner, patch(
                "biliflow.cli.ensure_model_allowed"
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            arguments = scanner.call_args.kwargs
            self.assertEqual(arguments["threshold"], 0.95)
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
                "biliflow.cli.scan_visual_logos", return_value=result
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
                "biliflow.cli.render_edit_previews", return_value={"status": "done"}
            ) as renderer, redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            self.assertEqual(renderer.call_args.kwargs["operation_ids"], ("op-blur",))


if __name__ == "__main__":
    unittest.main()
