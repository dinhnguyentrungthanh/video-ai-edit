import unittest

from biliflow.animation_policy import (
    adult_policy_decision,
    gore_policy_decision,
    probabilistic_union,
)


class AnimationPolicyTests(unittest.TestCase):
    def test_probabilistic_union(self):
        self.assertAlmostEqual(probabilistic_union([0.5, 0.5]), 0.75)

    def test_adult_accepts_three_specific_signals(self):
        accepted, score, count, reason = adult_policy_decision(
            {"nude": 0.06, "nipples": 0.06, "breasts_out": 0.06}
        )
        self.assertTrue(accepted)
        self.assertGreaterEqual(score, 0.15)
        self.assertEqual(count, 3)
        self.assertEqual(reason, "explicit_anatomy_or_act")

    def test_adult_rejects_one_strong_ambiguous_signal(self):
        accepted, _, count, reason = adult_policy_decision({"nude": 0.99})
        self.assertFalse(accepted)
        self.assertEqual(count, 1)
        self.assertEqual(reason, "below_policy")

    def test_adult_ignores_removed_generic_explicit_label(self):
        accepted, score, count, _ = adult_policy_decision({"explicit": 1.0})
        self.assertFalse(accepted)
        self.assertEqual(score, 0.0)
        self.assertEqual(count, 0)

    def test_gore_accepts_high_score(self):
        accepted, score, _, reason = gore_policy_decision({"blood": 0.25})
        self.assertTrue(accepted)
        self.assertGreaterEqual(score, 0.20)
        self.assertEqual(reason, "high_score")

    def test_gore_accepts_low_score_with_context(self):
        accepted, _, _, reason = gore_policy_decision(
            {"blood": 0.04, "injury": 0.05}
        )
        self.assertTrue(accepted)
        self.assertEqual(reason, "context_confirmed")

    def test_gore_rejects_single_weak_signal(self):
        accepted, _, _, reason = gore_policy_decision({"blood": 0.06})
        self.assertFalse(accepted)
        self.assertEqual(reason, "below_policy")


if __name__ == "__main__":
    unittest.main()
