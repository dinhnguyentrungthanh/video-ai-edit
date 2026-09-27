import unittest

from biliflow.violence_scanner import aggregate_top_k_mean


class ViolenceScannerTests(unittest.TestCase):
    def test_top_k_mean_uses_only_strongest_scores(self):
        self.assertAlmostEqual(
            aggregate_top_k_mean([0.1, 0.9, 0.3, 0.8, 0.7], 3),
            0.8,
        )

    def test_top_k_mean_handles_short_windows(self):
        self.assertAlmostEqual(aggregate_top_k_mean([0.2, 0.6], 5), 0.4)

    def test_top_k_mean_rejects_invalid_input(self):
        with self.assertRaises(ValueError):
            aggregate_top_k_mean([], 5)


if __name__ == "__main__":
    unittest.main()
