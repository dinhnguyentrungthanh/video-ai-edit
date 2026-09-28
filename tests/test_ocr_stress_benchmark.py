import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


scripts = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("ocr_stress_benchmark", scripts / "benchmark_ocr_stress.py")
benchmark = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(scripts), *sys.path]):
    spec.loader.exec_module(benchmark)


class StressBenchmarkTests(unittest.TestCase):
    def row(self, text, y, score=.11):
        return ([[10, y], [200, y], [200, y + 20], [10, y + 20]], text, score)

    def test_long_top_banner_uses_lower_effective_cutoff(self):
        result = benchmark.acceptance_margin(self.row("LONG DEMO EXAMPLE", 10))
        self.assertEqual(result["cutoff"], .1)
        self.assertAlmostEqual(result["margin"], .01)

    def test_same_score_is_not_near_cutoff_for_bottom_or_short_text(self):
        for row in (self.row("LONG DEMO EXAMPLE", 400), self.row("DEMO", 10)):
            result = benchmark.acceptance_margin(row)
            self.assertEqual(result["cutoff"], .35)
            self.assertAlmostEqual(result["margin"], .24)

    def test_invalid_characters_and_small_crops_do_not_count(self):
        self.assertIsNone(benchmark.acceptance_margin(self.row("!", 10)))
        self.assertIsNone(benchmark.acceptance_margin(([[0, 0], [4, 0], [4, 4], [0, 4]], "DEMO", .35)))


if __name__ == "__main__":
    unittest.main()
