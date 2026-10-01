import unittest
from pathlib import Path

from biliflow.job_pipeline import (
    load_profiles,
    normalize_detector_groups,
    pipeline_stages,
    safe_job_key,
)


ROOT = Path(__file__).resolve().parents[1]


class JobPipelineTests(unittest.TestCase):
    def test_safe_key_has_hash_and_bounded_stem(self):
        value = safe_job_key(Path("Tên video rất dài " * 10 + ".mp4"), "a" * 64)
        self.assertTrue(value.endswith("-aaaaaaaa"))
        self.assertLessEqual(len(value), 57)

    def test_profiles_include_careful_and_fast(self):
        profiles = load_profiles(ROOT)
        self.assertIn("careful", profiles)
        self.assertIn("fast", profiles)
        self.assertLess(profiles["careful"]["logo_sample_every"], profiles["fast"]["logo_sample_every"])

    def test_animation_pipeline_uses_shared_safety_pass(self):
        source = next((ROOT / "input").glob("*.mp4"))
        names = [x.name for x in pipeline_stages(
            root=ROOT, job_key="test-animation", source=source,
            content_style="animation", profile="fast")]
        self.assertEqual(names[0], "preflight")
        self.assertEqual(names[1], "animation_safety")
        self.assertNotIn("adult", names)
        self.assertEqual(names[-1], "build_review")

    def test_live_pipeline_has_confirmation(self):
        source = next((ROOT / "input").glob("*.mp4"))
        names = [x.name for x in pipeline_stages(
            root=ROOT, job_key="test-live", source=source,
            content_style="live_action", profile="careful")]
        self.assertIn("adult", names)
        self.assertIn("live_safety", names)
        self.assertNotIn("gore", names)
        self.assertNotIn("violence", names)
        self.assertIn("confirm_violence", names)
        self.assertIn("localize_logo", names)

    def test_live_adult_stage_pins_sequence_completion_policy(self):
        source = next((ROOT / "input").glob("*.mp4"))
        stages = pipeline_stages(
            root=ROOT, job_key="test-live-adult-policy", source=source,
            content_style="live_action", profile="careful", detector_groups=["adult"],
        )
        argv = next(stage for stage in stages if stage.name == "adult").commands[0].argv
        self.assertEqual(argv[argv.index("--review-merge-gap-seconds") + 1], "3.0")
        self.assertEqual(argv[argv.index("--sequence-context-threshold") + 1], "0.7")
        self.assertEqual(argv[argv.index("--sequence-context-seconds") + 1], "8.0")

    def test_localize_stage_runs_florence_then_grounding_fallback(self):
        source = next((ROOT / "input").glob("*.mp4"))
        stages = pipeline_stages(
            root=ROOT, job_key="test-grounding", source=source,
            content_style="animation", profile="fast",
        )
        stage = next(item for item in stages if item.name == "localize_logo")
        self.assertEqual(len(stage.commands), 2)
        self.assertIn("localize-visual-logo", stage.commands[0].argv)
        self.assertIn("augment-grounding-regions", stage.commands[1].argv)
        self.assertTrue(str(stage.commands[0].expected_artifacts[0]).endswith("scan-florence.json"))
        self.assertTrue(str(stage.commands[1].expected_artifacts[0]).endswith("scan-localized.json"))

    def test_visual_logo_stage_receives_verified_source_sha256(self):
        source = next((ROOT / "input").glob("*.mp4"))
        digest = "a" * 64
        stages = pipeline_stages(
            root=ROOT, job_key="test-sha", source=source,
            content_style="animation", profile="careful",
            source_sha256=digest,
        )
        logo_stage = next(stage for stage in stages if stage.name == "visual_logo")
        argv = logo_stage.commands[0].argv
        index = argv.index("--source-sha256")
        self.assertEqual(argv[index + 1], digest)

    def test_advertising_only_skips_every_safety_stage(self):
        source = next((ROOT / "input").glob("*.mp4"))
        stages = pipeline_stages(
            root=ROOT, job_key="test-advertising-only", source=source,
            content_style="animation", profile="fast",
            detector_groups=["advertising"],
        )
        self.assertEqual(
            [stage.name for stage in stages],
            ["preflight", "text", "visual_logo", "localize_logo", "build_review"],
        )
        review = stages[-1].commands[0].argv
        self.assertIn("--selected-detector", review)
        self.assertIn("advertising", review)
        self.assertNotIn("animation-safety", " ".join(review))

    def test_advertising_commands_match_between_single_and_all_scope(self):
        source = next((ROOT / "input").glob("*.mp4"))
        only = pipeline_stages(
            root=ROOT, job_key="same-ad-path", source=source,
            content_style="animation", profile="careful",
            detector_groups=["advertising"],
        )
        all_models = pipeline_stages(
            root=ROOT, job_key="same-ad-path", source=source,
            content_style="animation", profile="careful",
            detector_groups=["advertising", "adult", "gore", "violence"],
        )
        for name in ("text", "visual_logo", "localize_logo"):
            only_stage = next(stage for stage in only if stage.name == name)
            all_stage = next(stage for stage in all_models if stage.name == name)
            self.assertEqual(only_stage.commands, all_stage.commands)

    def test_animation_adult_only_uses_shared_pass_but_reviews_only_adult(self):
        source = next((ROOT / "input").glob("*.mp4"))
        stages = pipeline_stages(
            root=ROOT, job_key="test-adult-only", source=source,
            content_style="animation", profile="fast",
            detector_groups=["adult"],
        )
        self.assertEqual(
            [stage.name for stage in stages],
            ["preflight", "animation_safety", "build_review"],
        )
        expected = stages[1].commands[0].expected_artifacts
        self.assertEqual(len(expected), 1)
        self.assertTrue(str(expected[0]).endswith("adult\\scan.json"))
        review = " ".join(stages[-1].commands[0].argv)
        self.assertIn("animation-safety\\adult\\scan.json", review)
        self.assertNotIn("animation-safety\\gore\\scan.json", review)

    def test_live_gore_only_does_not_run_other_models(self):
        source = next((ROOT / "input").glob("*.mp4"))
        names = [stage.name for stage in pipeline_stages(
            root=ROOT, job_key="test-gore-only", source=source,
            content_style="live_action", profile="fast",
            detector_groups=["gore"],
        )]
        self.assertEqual(names, ["preflight", "gore", "build_review"])

    def test_live_gore_and_violence_share_one_decode_stage(self):
        source = next((ROOT / "input").glob("*.mp4"))
        stages = pipeline_stages(
            root=ROOT, job_key="test-live-shared", source=source,
            content_style="live_action", profile="careful",
            detector_groups=["gore", "violence"],
        )
        self.assertEqual(
            [stage.name for stage in stages],
            ["preflight", "live_safety", "confirm_violence", "build_review"],
        )
        shared = stages[1]
        self.assertIn("scan-live-safety", shared.commands[0].argv)
        self.assertEqual(len(shared.commands[0].expected_artifacts), 2)

    def _stages(self, style, groups=None, **extra):
        source = next((ROOT / "input").glob("*.mp4"))
        return {stage.name: stage for stage in pipeline_stages(
            root=ROOT, job_key="test-adult-triage", source=source,
            content_style=style, profile="careful", detector_groups=groups, **extra,
        )}

    def test_live_adult_runs_shot_completion_then_verification(self):
        for fast_scan in (False, True):
            with self.subTest(fast_scan=fast_scan):
                stages = self._stages("live_action", fast_scan=fast_scan)
                names = list(stages)
                self.assertEqual(names[names.index("adult") + 1], "verify_adult")
                adult = stages["adult"].commands[0].argv
                self.assertIn("--shot-completion", adult)
                self.assertEqual(adult[adult.index("--content-style") + 1], "live_action")
                verify = stages["verify_adult"]
                self.assertTrue(verify.uses_gpu)
                self.assertEqual(verify.job_state, "SCANNING_SAFETY")
                argv = verify.commands[0].argv
                self.assertIn("verify-adult", argv)
                self.assertTrue(argv[argv.index("--report") + 1].endswith("adult\\scan.json"))
                self.assertTrue(argv[argv.index("--output") + 1].endswith("adult\\scan-verified.json"))
                self.assertEqual(argv[argv.index("--device") + 1], "cuda")
                self.assertEqual(
                    [str(path) for path in verify.commands[0].expected_artifacts],
                    [argv[argv.index("--output") + 1]],
                )
                review = stages["build_review"].commands[0].argv
                reports = [review[index + 1] for index, value in enumerate(review) if value == "--report"]
                self.assertTrue(any(value.endswith("adult\\scan-verified.json") for value in reports))
                self.assertFalse(any(value.endswith("adult\\scan.json") for value in reports))
                self.assertEqual(review[review.index("--content-style") + 1], "live_action")
        self.assertEqual(
            self._stages("live_action")["verify_adult"], self._stages("live_action", fast_scan=True)["verify_adult"],
        )

    def test_mixed_job_skips_verification_and_animation_never_verifies(self):
        mixed = self._stages("mixed")
        self.assertNotIn("verify_adult", mixed)  # triage never applies to mixed queues
        self.assertIn("--shot-completion", mixed["adult"].commands[0].argv)
        review = mixed["build_review"].commands[0].argv
        self.assertEqual(review[review.index("--content-style") + 1], "mixed")
        reports = [review[index + 1] for index, value in enumerate(review) if value == "--report"]
        self.assertTrue(any(value.endswith("adult\scan.json") for value in reports))
        animation = self._stages("animation")
        self.assertNotIn("verify_adult", animation)
        self.assertNotIn("adult", animation)
        flat = " ".join(" ".join(stage.commands[0].argv) for stage in animation.values() if stage.commands)
        self.assertNotIn("--shot-completion", flat)
        self.assertNotIn("scan-verified.json", flat)
        review = animation["build_review"].commands[0].argv
        self.assertEqual(review[review.index("--content-style") + 1], "animation")

    def test_uncalibrated_profile_and_legacy_jobs_read_the_plain_adult_scan(self):
        source = next((ROOT / "input").glob("*.mp4"))
        fast_profile = {stage.name: stage for stage in pipeline_stages(
            root=ROOT, job_key="test-adult-triage", source=source,
            content_style="live_action", profile="fast")}
        legacy = self._stages("live_action", adult_verification=False)
        for stages in (fast_profile, legacy):
            self.assertNotIn("verify_adult", stages)
            review = stages["build_review"].commands[0].argv
            reports = [review[index + 1] for index, value in enumerate(review) if value == "--report"]
            self.assertTrue(any(value.endswith("adult\scan.json") for value in reports))
            self.assertFalse(any(value.endswith("scan-verified.json") for value in reports))

    def test_no_verification_without_the_adult_group(self):
        stages = self._stages("live_action", ["gore", "violence", "advertising"])
        self.assertNotIn("verify_adult", stages)
        self.assertNotIn("adult", stages)
        review = stages["build_review"].commands[0].argv
        reports = [review[index + 1] for index, value in enumerate(review) if value == "--report"]
        self.assertFalse(any("\\adult\\" in value for value in reports))

    def test_shot_completion_and_verification_have_their_own_cache_keys(self):
        from biliflow.cache_dependencies import stage_source_paths
        from biliflow.stage_cache import StageArtifactCache

        stages = self._stages("live_action", ["adult"])
        cache = StageArtifactCache(ROOT)
        self.assertTrue(cache.cacheable("verify_adult", stages["verify_adult"].commands[0].expected_artifacts))
        adult = stages["adult"].commands[0]
        without = tuple(value for value in adult.argv if value != "--shot-completion")
        source = next((ROOT / "input").glob("*.mp4"))
        keys = [cache.key(stage_name="adult", source_sha256="a" * 64, source_path=source,
                          report_root=ROOT / "reports/jobs/test-adult-triage",
                          commands=[argv], artifact_paths=adult.expected_artifacts)
                for argv in (adult.argv, without)]
        self.assertNotEqual(*keys)  # a scan without R3 is never reused for a job with R3
        dependencies = {path.name for path in stage_source_paths(ROOT, "verify_adult")}
        self.assertTrue({"adult_verification.py", "content_scanner.py", "frame_prefetch.py"} <= dependencies)

    def test_detector_selection_rejects_empty_and_unknown_values(self):
        with self.assertRaises(ValueError):
            normalize_detector_groups([])
        with self.assertRaises(ValueError):
            normalize_detector_groups(["future-detector"])


if __name__ == "__main__":
    unittest.main()
