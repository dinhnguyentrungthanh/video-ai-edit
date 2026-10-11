"""The table of hardware configurations and today's whole command lines.

docs/HARDWARE_PROFILE.md sections 5.1-5.6, 8 (item 4) and 9 ("bảng", "dòng lệnh"). The command
lines are built by job_pipeline.pipeline_stages on a temporary root under <install>/temp; nothing
here runs a stage, imports torch or reads the real state folder.
"""
from __future__ import annotations

import itertools
import shutil
import unittest
from dataclasses import fields, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

from biliflow import hardware_profile as hp
from biliflow import hardware_table as ht
from biliflow.cache_dependencies import stage_source_paths
from biliflow.job_pipeline import load_profiles, pipeline_stages
from biliflow.stage_cache import CACHEABLE_STAGES
from tests.hardware_fixtures import FFMPEG_SHA, TEMP_PARENT

ROOT = Path(__file__).resolve().parents[1]
GATED_ROW = "nvidia-sm75-torch2.14.0+cu126"
CPU_ROW = "cpu-fp32-torch2.14.0+cu126"
SPEED_FIELDS = ("text", "visual_logo", "animation_safety", "live_safety")
# Content style and detector groups; together they emit all 10 GPU stages (live_safety, gore and
# violence exclude each other, so one selection cannot).
SELECTIONS = (
    ("live_action", None), ("mixed", ("gore",)), ("live_action", ("violence",)), ("animation", None), ("mixed", None),
)


def row(row_id: str) -> ht.Row:
    return next(entry for entry in ht.ROWS if entry.row_id == row_id)


def family_fits(family: str, knobs: hp.ScanKnobs) -> bool:
    """Section 2.3: fast is the prewarm form, standard_ocr8 plain batch 8, standard plain batch 1 or cpu."""
    batch = knobs.text.recognition_batch_size
    return {"fast": knobs.form == "prewarm", "standard_ocr8": knobs.form == "plain" and batch == 8,
            "standard": (knobs.form == "plain" and batch == 1) or knobs.form == "cpu"}[family]


def with_speed(argv: tuple[str, ...], extra: tuple[object, ...]) -> tuple[str, ...]:
    """``argv`` with ``extra`` right after ``"--device", "cuda"``, each value as run_command writes it."""
    if "--device" not in argv:
        if extra:
            raise AssertionError(f"speed arguments for a command without --device: {argv[6]}")
        return argv
    index = argv.index("--device")
    if argv.count("--device") != 1 or argv[index + 1] != "cuda":
        raise AssertionError(f"unexpected device options in {argv[6]}")
    return argv[:index + 2] + tuple(str(value) for value in extra) + argv[index + 2:]


def expected_stages(standard: list, speed: hp.SpeedArguments) -> list:
    """The standard OCR-1 stages with ``speed`` inserted into the four stages that take it."""
    result = []
    for stage in standard:
        extra = getattr(speed, stage.name) if stage.name in SPEED_FIELDS else ()
        commands = tuple(replace(command, argv=with_speed(command.argv, extra)) for command in stage.commands)
        result.append(replace(stage, commands=commands))
    return result


def with_cpu_decode(stages: list) -> list:
    """``stages`` with the one value after ``--logo-decode`` (``nvdec``) set to ``cpu``, nothing else."""
    result = []
    for stage in stages:
        commands = []
        for command in stage.commands:
            argv = command.argv
            if "--logo-decode" in argv:
                index = argv.index("--logo-decode") + 1
                if stage.name != "text" or argv.count("--logo-decode") != 1 or argv[index] != "nvdec":
                    raise AssertionError(f"unexpected decode options in {stage.name}")
                argv = argv[:index] + ("cpu",) + argv[index + 1:]
            commands.append(replace(command, argv=argv))
        result.append(replace(stage, commands=tuple(commands)))
    return result


class TableTests(unittest.TestCase):
    def test_version_constants_and_ids(self):
        ids = [entry.row_id for entry in ht.ROWS]
        self.assertEqual(ht.TABLE_VERSION, 1)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [GATED_ROW, CPU_ROW, "nvidia-untested", "nvidia-untested-fast"])
        self.assertNotIn(ht.EXPORT_ROW_ID, ids)
        self.assertEqual(dict(ht.CUDA_DRIVER_MIN), {"cu126": "561.17"})
        self.assertIn("12.6 Update 3", ht.CUDA_DRIVER_MIN_EVIDENCE["cu126"])
        self.assertEqual(ht.UNTESTED_MIN_VRAM_MIB, 6144)

    def test_every_family_passes_its_checks_and_has_exactly_one_form(self):
        for entry in ht.ROWS:
            self.assertIn(entry.default_family, entry.families)
            for family, knobs in entry.families.items():
                with self.subTest(row=entry.row_id, family=family):
                    rebuilt = replace(knobs)  # runs __post_init__ again
                    self.assertEqual(rebuilt, knobs)
                    self.assertIn(knobs.form, hp.FORMS)
                    self.assertTrue(family_fits(family, knobs))

    def test_the_gated_row_is_todays_command_line_with_tf32_default(self):
        gated = row(GATED_ROW)
        self.assertEqual(gated.status, "gated")
        self.assertEqual(gated.match, ht.Match("nvidia", "7.5", 6144, 1, frozenset({"2.14.0+cu126"}), "576.80", None))
        self.assertEqual(dict(gated.families), dict(hp.LEGACY_KNOBS))
        self.assertEqual({knobs.tf32 for knobs in gated.families.values()}, {"default"})
        self.assertEqual((gated.default_family, dict(gated.free_fields), gated.runnable_after), ("fast", {}, None))
        self.assertEqual(gated.nvdec_verified, frozenset({("576.80", FFMPEG_SHA)}))
        self.assertEqual([caveat.code for caveat in gated.caveats],
                         ["c1_off_fp32", "fp16_anime_careful_only", "fp16_violence_l_only", "no_golden_gate"])
        self.assertTrue(gated.evidence)

    def test_the_cpu_row_is_fp32_batch_1_and_waits_for_e3(self):
        cpu = row(CPU_ROW)
        self.assertEqual((cpu.status, cpu.match.vendor, cpu.runnable_after, cpu.default_family),
                         ("ungated", "none", "E3", "standard"))
        self.assertEqual(list(cpu.families), ["standard"])
        knobs = cpu.families["standard"]
        self.assertEqual((knobs.form, knobs.device, knobs.tf32, knobs.text.recognition_batch_size), ("cpu", "cpu",
                                                                                                    "default", 1))
        self.assertNotIn("fp16", {knobs.text.detect_precision, knobs.animation_precision, knobs.violence_precision})
        with self.assertRaises(ValueError):
            replace(knobs, tf32="off")

    def test_the_untested_rows_turn_tf32_off_everywhere(self):
        standard, fast = row("nvidia-untested"), row("nvidia-untested-fast")
        gated = row(GATED_ROW)
        for entry in (standard, fast):
            with self.subTest(row=entry.row_id):
                self.assertEqual(entry.status, "untested")
                self.assertEqual(entry.match, ht.Match("nvidia", None, None, None, frozenset(), None, None))
                self.assertEqual({knobs.tf32 for knobs in entry.families.values()}, {"off"})
                self.assertEqual((entry.nvdec_verified, dict(entry.free_fields), entry.runnable_after),
                                 (frozenset(), {}, None))
                self.assertTrue(any("5.6" in item for item in entry.evidence))
        self.assertEqual(dict(standard.families), {"standard": hp.plain_knobs(1, tf32="off")})
        self.assertEqual(standard.default_family, "standard")
        self.assertEqual(dict(fast.families),
                         {family: replace(knobs, tf32="off") for family, knobs in gated.families.items()})
        self.assertEqual(fast.default_family, "fast")
        self.assertEqual([caveat.code for caveat in fast.caveats][0], "untested_fast_on")
        for risk in ht.UNTESTED_FAST_RISKS:
            self.assertIn(risk, fast.caveats[0].text)

    def test_untested_families_cannot_be_written_before_dot_2(self):
        for entry in (row("nvidia-untested"), row("nvidia-untested-fast")):
            for family, knobs in entry.families.items():
                with self.subTest(row=entry.row_id, family=family):
                    with self.assertRaises(ValueError):
                        hp.speed_arguments(knobs, config=load_profiles(ROOT)["careful"], source_sha256=None)

    def test_rows_stay_immutable_and_checked(self):
        gated = row(GATED_ROW)
        self.assertIsInstance(gated.families, MappingProxyType)
        with self.assertRaises(TypeError):
            gated.families["fast"] = hp.plain_knobs(1)  # type: ignore[index]
        with self.assertRaises(ValueError):
            replace(gated, families=dict(gated.families))
        with self.assertRaises(ValueError):
            replace(gated, status="maybe")
        with self.assertRaises(ValueError):
            replace(gated.match, driver_min="576.80a")

    def test_drivers_compare_as_numbers(self):
        self.assertGreater(hp.driver_tuple("1000.10"), hp.driver_tuple("999.99"))
        self.assertGreater(hp.driver_tuple("617.42"), hp.driver_tuple("576.80"))
        self.assertLess(hp.driver_tuple("561.9"), hp.driver_tuple("561.17"))
        self.assertIsNone(hp.driver_tuple("32.0.16.1742.5"))
        self.assertIsNone(hp.driver_tuple(None))

    def test_the_export_row_is_version_1(self):
        self.assertEqual(ht.EXPORT_ROW_ID, "export-x264")
        self.assertEqual([getattr(ht.EXPORT_KNOBS, item.name) for item in fields(ht.EXPORT_KNOBS)],
                         ["libx264", "veryfast", 20, "vbv", 384000, "cpu_full_decode"])
        with self.assertRaises(ValueError):
            replace(ht.EXPORT_KNOBS, encoder="h264_nvenc")


class WholeCommandLineTests(unittest.TestCase):
    """Section 8 item 4: every stage's argv is the standard OCR-1 argv plus speed_arguments, whole."""

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "config").mkdir()
        shutil.copyfile(ROOT / "config" / "processing_profiles.json", self.root / "config" / "processing_profiles.json")
        self.source = self.root / "movie.mp4"
        self.source.write_bytes(b"movie")

    def stages(self, style, groups, profile, sha, verify, *, fast=False, ocr=1) -> list:
        return pipeline_stages(root=self.root, job_key="hw-argv", source=self.source, content_style=style,
                               profile=profile, source_sha256=sha, detector_groups=groups,
                               ocr_recognition_batch_size=ocr, fast_scan=fast, adult_verification=verify)

    def test_each_family_is_the_standard_command_line_plus_its_speed_arguments(self):
        seen: set[str] = set()
        combinations = itertools.product(SELECTIONS, ("careful", "fast"), (None, "f" * 64), (None, False))
        for (style, groups), profile, sha, verify in combinations:
            standard = self.stages(style, groups, profile, sha, verify)
            config = load_profiles(self.root)[profile]
            for fast, ocr in itertools.product((True, False), (1, 8)):
                family = hp.ScanRequest(fast, ocr).family
                with self.subTest(style=style, groups=groups, profile=profile, sha=bool(sha), verify=verify,
                                  fast=fast, ocr=ocr):
                    actual = self.stages(style, groups, profile, sha, verify, fast=fast, ocr=ocr)
                    speed = hp.speed_arguments(hp.LEGACY_KNOBS[family], config=config, source_sha256=sha)
                    self.assertEqual(actual, expected_stages(standard, speed))
                    seen.update(stage.name for stage in actual if stage.uses_gpu)
        self.assertEqual(seen, set(hp.GPU_STAGES))
        self.assertEqual(seen, set(CACHEABLE_STAGES))

    def test_the_gated_families_are_the_legacy_knobs(self):
        gated = row(GATED_ROW)
        config = load_profiles(self.root)["careful"]
        for family in hp.FAMILIES:
            with self.subTest(family=family):
                self.assertEqual(hp.speed_arguments(gated.families[family], config=config, source_sha256="f" * 64),
                                 hp.speed_arguments(hp.LEGACY_KNOBS[family], config=config, source_sha256="f" * 64))

    def test_cpu_decode_changes_exactly_one_element_of_the_text_command(self):
        for (style, groups), sha in itertools.product(SELECTIONS, (None, "f" * 64)):
            with self.subTest(style=style, groups=groups, sha=bool(sha)):
                standard = self.stages(style, groups, "careful", sha, None)
                config = load_profiles(self.root)["careful"]
                nvdec = expected_stages(standard, hp.speed_arguments(hp.prewarm_knobs("nvdec"), config=config,
                                                                     source_sha256=sha))
                cpu = expected_stages(standard, hp.speed_arguments(hp.prewarm_knobs("cpu"), config=config,
                                                                   source_sha256=sha))
                self.assertEqual(nvdec, self.stages(style, groups, "careful", sha, None, fast=True))
                self.assertEqual(cpu, with_cpu_decode(nvdec))  # whole lists: lengths and every element
                has_text = any(stage.name == "text" for stage in standard)
                self.assertEqual(cpu != nvdec, has_text)


class CacheScopeTests(unittest.TestCase):
    def test_no_scan_stage_key_contains_a_hardware_module(self):
        everything = set((ROOT / "src" / "biliflow").rglob("*.py"))
        for stage in sorted(CACHEABLE_STAGES):
            with self.subTest(stage=stage):
                paths = set(stage_source_paths(ROOT, stage))
                self.assertFalse(everything <= paths)  # no fallback to hashing the whole tree
                self.assertEqual({path.name for path in paths if path.name.startswith("hardware_")}, set())


if __name__ == "__main__":
    unittest.main()
