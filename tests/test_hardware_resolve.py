"""resolve() and choose() of the hardware profile (docs/HARDWARE_PROFILE.md sections 5.5, 5.6, 7 and 9)."""
from __future__ import annotations

import inspect
import itertools
import unittest
from dataclasses import replace
from unittest import mock

from biliflow import hardware_profile as hp
from biliflow import hardware_resolve, hardware_table, job_purge
from biliflow.hardware_schema import (
    FileStat, GpuInfo, Observation, UntestedFast, classify_attempt, previous_had_cuda, read_profile,
)
from tests.hardware_fixtures import (
    ADA, AMPERE, BASIC_DISPLAY, BLACKWELL, CPU_OBSERVATION, FFMPEG_MTIME, FFMPEG_SIZE, INTEL_ADAPTER, SMALL_AMPERE,
    TORCH, TURING, WHEN, TempRootCase, cpu_with_had_cuda, failed_on_card, observation, profile_document, rehash,
)

MAIN_FILES = {
    "missing": None, "broken": b"{broken", "ok_cuda": lambda: profile_document("cuda"),
    "ok_cuda_untested": lambda: profile_document("cuda", gpus=(AMPERE,)),
    "ok_cpu": lambda: profile_document("cpu"), "ok_cpu_had_cuda": cpu_with_had_cuda,
    "failed": lambda: profile_document("failed"), "failed_no_kernel": lambda: failed_on_card(BLACKWELL),
}
LAST_GOOD_FILES = {
    "missing": None, "broken": b'{"schema": 1, "schema": 2}', "cuda": lambda: profile_document("cuda"),
    "cpu": lambda: profile_document("cpu"),
}
# The switch file: missing (both off), broken, or "Chỉ dùng CPU" off/on x the untested-fast switch
# off, on for the card of the profile ("this") or on for another card.
SWITCHES = (("missing", None), ("broken", None)) + tuple(itertools.product(("off", "on"), ("off", "this", "other")))
SWITCH_LABELS = {"missing": ("missing", None), "broken": ("broken", None), "off": ("off", "off"), "on": ("on", "off")}
OTHER_CARD_SWITCH = UntestedFast(ADA.name, ADA.compute_capability, WHEN)
READABLE_MAIN = {"ok_cuda": "cuda", "ok_cuda_untested": "cuda", "ok_cpu": "cpu", "failed": "failed",
                 "failed_no_kernel": "failed_no_kernel"}


def card_of(main: str) -> GpuInfo:
    """The card the observations are made around: the main file's, or the last-good's (Turing)."""
    return AMPERE if main == "ok_cuda_untested" else TURING


def observations(card: GpuInfo) -> dict[str, Observation | None]:
    """The observations of section 9 around ``card``."""
    return {
        "none": None,
        "match": observation((card,)),
        "gpu_lost": observation(()),
        "gpu_changed": observation((AMPERE if card == TURING else TURING,)),
        "driver_in_range": observation((card,), driver="620.10"),
        "driver_out_of_range": observation((card,), driver="565.90"),
        "nvml_no_library": CPU_OBSERVATION,
        "nvml_error": observation((), nvml="error", driver=None, pci_seen=True),
        "pci_without_nvml": observation((), nvml="no_library", driver=None, pci_seen=True),
        "torch_changed": observation((card,), torch="2.15.0+cu128"),
        "ffmpeg_changed": observation((card,), ffmpeg=FileStat(FFMPEG_SIZE + 1, FFMPEG_MTIME)),
        "two_gpus": observation((card, card)),
    }


OBSERVATIONS = observations(TURING)


def never_had_cuda(main: str, last_good: str) -> bool:
    """Section 3.5 on the fixture labels: every readable file without CUDA, none broken."""
    return main in ("missing", "ok_cpu") and last_good in ("missing", "cpu")


def cpu_status(torch: str | None, cpu_runs: bool) -> str:
    if torch != TORCH:
        return "no_gated_row"
    return "ready_ungated" if cpu_runs else "cpu_row_not_ready"  # every grid root is a test root


def cpu_profile_status(main: str, last_good: str, observed, cpu_runs: bool) -> str:
    if never_had_cuda(main, last_good):
        if observed is None:
            return cpu_status(TORCH, cpu_runs)
        if observed.gpus or observed.nvidia_pci_seen is not False:
            return "stale_gpu"
        return cpu_status(observed.torch_build, cpu_runs)
    if observed is not None:
        if len(observed.gpus) > 1:
            return "multi_gpu_unsupported"
        if observed.gpus:
            return "stale_gpu"
        if observed.torch_build != TORCH:
            return "stale_torch"
    return "no_gated_row"


def expected_status(main: str, last_good: str, switch: tuple[str, str | None], name: str, cpu_runs: bool) -> str:
    """Section 7.1 written out on the labels of section 9, independently of hardware_resolve.

    The untested-fast switch never changes the status, only the row (checked in the invariants).
    """
    card = card_of(main)
    observed = observations(card)[name]
    if switch[0] == "broken":
        return "override_invalid"
    if switch[0] == "on":
        torch = observed.torch_build if observed is not None else (TORCH if main in READABLE_MAIN else None)
        return cpu_status(torch, cpu_runs)
    if main in READABLE_MAIN:
        kind, which = READABLE_MAIN[main], "main"
    elif last_good in ("cuda", "cpu") and observed is not None:
        kind, which = last_good, "last_good"
    elif main == "missing" and last_good == "missing":
        return "needs_measurement"
    else:
        return "invalid"
    if kind == "failed":
        return "cuda_unavailable"
    if kind == "failed_no_kernel":  # step 4 names the reason (section 5.6), whatever is observed
        return "arch_unsupported"
    if kind == "cpu":
        return cpu_profile_status(main, last_good, observed, cpu_runs)
    gated = card == TURING  # the Ampere profile has no gated row: option D (section 5.6)
    if observed is None:
        return "ready" if gated else "ready_untested"
    if len(observed.gpus) > 1:
        return "multi_gpu_unsupported"
    if observed.nvml != "ok":
        return "cuda_unavailable"
    if observed.gpus != (card,):
        return "stale_gpu"
    if observed.torch_build != TORCH:
        return "stale_torch"
    if name == "driver_out_of_range" or not gated:
        return "ready_untested"
    return "ready" if which == "main" else "ready_last_good"


def cpu_row_runnable():
    """The table as after E3: the CPU row runs (still ungated)."""
    rows = tuple(replace(row, runnable_after=None) if row.match.vendor == "none" else row
                 for row in hardware_table.ROWS)
    return mock.patch.object(hardware_table, "ROWS", rows)


def codes(reasons) -> list[str]:
    return [reason.code for reason in reasons]


def untested_problems(result) -> list[str]:
    """Section 9: every untested choice is cuda with TF32 off and carries untested_arch and tf32_off."""
    problems = []
    if result.row_status != "untested" or not {"untested_arch", "tf32_off"} <= set(codes(result.warnings)):
        problems.append("untested result without its row status or labels")
    for option in result.families.values():
        if option.knobs is None or (option.knobs.device, option.tf32, option.knobs.tf32) != ("cuda", "off", "off"):
            problems.append(f"untested family {option.requested} is not cuda with TF32 off")
    return problems


class SwitchRoots(TempRootCase):
    def fill_root(self, root, main: str, last_good: str, switch: tuple[str, str | None]) -> None:
        for which, files, label in (("main", MAIN_FILES, main), ("last_good", LAST_GOOD_FILES, last_good)):
            source = files[label]
            if source is not None:
                self.write(which, source if isinstance(source, bytes) else source(), root)
        cpu, fast = switch
        if cpu == "broken":
            self.broken_switch(root)
        elif cpu != "missing":
            card = card_of(main)
            bound = {"off": None, "this": UntestedFast(card.name, card.compute_capability, WHEN),
                     "other": OTHER_CARD_SWITCH}[fast]
            self.switch(cpu_only=cpu == "on", untested_fast=bound, root=root)


class GridTests(SwitchRoots):
    """Section 9: main file x last-good x both switches x observation, one expected status each."""

    def build_roots(self) -> dict[tuple, object]:
        roots = {}
        for index, (main, last_good, switch) in enumerate(itertools.product(MAIN_FILES, LAST_GOOD_FILES, SWITCHES)):
            root = self.new_root(f"c{index}")
            self.fill_root(root, main, last_good, switch)
            roots[(main, last_good, switch)] = root
        return roots

    def check_grid(self, cpu_runs: bool) -> None:
        problems, seen = [], set()
        for (main, last_good, switch), root in self.build_roots().items():
            for name, observed in observations(card_of(main)).items():
                result = hp.resolve(root, observed=observed)
                label = f"{main}/{last_good}/{'+'.join(map(str, switch))}/{name}"
                expected = expected_status(main, last_good, switch, name, cpu_runs)
                seen.add(result.status)
                if result.status != expected:
                    problems.append(f"{label}: {result.status} != {expected}")
                problems.extend(f"{label}: {problem}" for problem in self.invariants(
                    result, main, last_good, switch, observed))
        self.assertEqual(problems[:20], [], f"{len(problems)} problems")
        self.assertTrue({"ready", "ready_last_good", "ready_untested", "arch_unsupported"} <= seen)

    def invariants(self, result, main, last_good, switch, observed) -> list[str]:
        problems = []
        granted = [option for option in result.families.values() if option.knobs is not None]
        if result.ready != bool(granted):
            problems.append("ready status and granted families disagree")
        for option in granted:  # I4: never silently on CPU
            if option.knobs.device == "cpu" and not (switch[0] == "on" or never_had_cuda(main, last_good)):
                problems.append("cpu device without the switch or a never-CUDA machine")
            if (option.knobs.device == "cpu") != (result.source in ("cpu_only", "no_cuda_machine")):
                problems.append("device and source disagree")
        if observed is None and result.status == "ready_last_good":
            problems.append("ready_last_good without an observation")
        if result.status not in hp.STATUSES:
            problems.append(f"unknown status {result.status}")
        if result.status == "ready_untested":
            problems.extend(untested_problems(result) + switch_problems(result, switch[1]))
        return problems

    def test_every_combination_with_the_table_of_today(self):
        self.check_grid(cpu_runs=False)

    def test_every_combination_with_the_cpu_row_runnable_after_e3(self):
        with cpu_row_runnable():
            self.check_grid(cpu_runs=True)


def switch_problems(result, fast: str | None) -> list[str]:
    """The untested-fast switch holds only for the card it was turned on for (sections 3.6, 5.6)."""
    applies = fast == "this"
    problems = []
    if result.row_id != ("nvidia-untested-fast" if applies else "nvidia-untested"):
        problems.append(f"row {result.row_id} with the fast switch {fast}")
    if result.untested_fast_applies != applies or ("untested_fast_on" in codes(result.warnings)) != applies:
        problems.append(f"the fast switch {fast} applies wrongly")
    if ("untested_fast_other_card" in codes(result.warnings)) != (fast == "other"):
        problems.append(f"untested_fast_other_card with the fast switch {fast}")
    return problems


class ExplicitStatusTests(SwitchRoots):
    """Hand-written rows of the decision table (no oracle)."""
    ROWS = (
        ("missing", "missing", "missing", "match", "needs_measurement"),
        ("missing", "missing", "missing", "none", "needs_measurement"),
        ("broken", "missing", "missing", "match", "invalid"),
        ("broken", "cuda", "missing", "match", "ready_last_good"),
        ("broken", "cuda", "missing", "none", "invalid"),
        ("missing", "cuda", "missing", "none", "invalid"),
        ("missing", "cuda", "off", "match", "ready_last_good"),
        ("ok_cpu_had_cuda", "cuda", "missing", "match", "ready_last_good"),
        ("failed", "cuda", "missing", "match", "cuda_unavailable"),
        ("ok_cuda", "missing", "missing", "match", "ready"),
        ("ok_cuda", "missing", "off", "none", "ready"),
        ("ok_cuda", "missing", "missing", "gpu_lost", "stale_gpu"),
        ("ok_cuda", "missing", "missing", "gpu_changed", "stale_gpu"),
        ("ok_cuda", "missing", "missing", "driver_in_range", "ready"),
        ("ok_cuda", "missing", "missing", "driver_out_of_range", "ready_untested"),
        ("ok_cuda", "missing", "missing", "nvml_no_library", "cuda_unavailable"),
        ("ok_cuda", "missing", "missing", "nvml_error", "cuda_unavailable"),
        ("ok_cuda", "missing", "missing", "pci_without_nvml", "cuda_unavailable"),
        ("ok_cuda", "missing", "missing", "torch_changed", "stale_torch"),
        ("ok_cuda", "missing", "missing", "ffmpeg_changed", "ready"),
        ("ok_cuda", "missing", "missing", "two_gpus", "multi_gpu_unsupported"),
        ("ok_cuda", "cuda", "broken", "match", "override_invalid"),
        ("ok_cuda", "cuda", "on", "match", "cpu_row_not_ready"),
        ("ok_cpu", "missing", "missing", "nvml_no_library", "cpu_row_not_ready"),
        ("ok_cpu", "missing", "missing", "none", "cpu_row_not_ready"),
        ("ok_cpu", "missing", "missing", "pci_without_nvml", "stale_gpu"),
        ("ok_cpu", "missing", "missing", "match", "stale_gpu"),
        ("ok_cpu", "broken", "missing", "nvml_no_library", "no_gated_row"),
        ("ok_cpu", "cuda", "missing", "nvml_no_library", "no_gated_row"),
        ("missing", "cpu", "missing", "nvml_no_library", "cpu_row_not_ready"),
        ("missing", "missing", "on", "none", "no_gated_row"),
        ("missing", "missing", "on", "nvml_no_library", "cpu_row_not_ready"),
        ("ok_cuda", "missing", "on", "torch_changed", "no_gated_row"),
        ("ok_cuda_untested", "missing", "missing", "match", "ready_untested"),
        ("ok_cuda_untested", "missing", "missing", "none", "ready_untested"),
        ("ok_cuda_untested", "missing", ("off", "this"), "driver_in_range", "ready_untested"),
        ("ok_cuda_untested", "missing", ("off", "other"), "driver_out_of_range", "ready_untested"),
        ("ok_cuda_untested", "cuda", ("on", "this"), "match", "cpu_row_not_ready"),
        ("ok_cuda_untested", "missing", "missing", "gpu_changed", "stale_gpu"),
        ("ok_cuda_untested", "missing", "missing", "torch_changed", "stale_torch"),
        ("ok_cuda", "missing", ("off", "this"), "match", "ready"),
        ("failed_no_kernel", "cuda", "missing", "match", "arch_unsupported"),
        ("failed_no_kernel", "missing", "on", "none", "cpu_row_not_ready"),
    )

    def test_each_row_gives_its_status(self):
        for index, (main, last_good, label, name, expected) in enumerate(self.ROWS):
            switch = SWITCH_LABELS.get(label, label)
            with self.subTest(main=main, last_good=last_good, switch=switch, observation=name):
                root = self.new_root(f"r{index}")
                self.fill_root(root, main, last_good, switch)
                observed = observations(card_of(main))[name]
                self.assertEqual(hp.resolve(root, observed=observed).status, expected)
                self.assertEqual(expected_status(main, last_good, switch, name, False), expected)


class ReadyTests(TempRootCase):
    def test_gated_turing_gives_the_three_families_of_today(self):
        document = profile_document("cuda")
        self.write("main", document)
        result = hp.resolve(self.root, observed=observation())
        self.assertEqual((result.status, result.row_id, result.row_status, result.source),
                         ("ready", "nvidia-sm75-torch2.14.0+cu126", "gated", "profile"))
        self.assertEqual({family: option.knobs for family, option in result.families.items()}, dict(hp.LEGACY_KNOBS))
        self.assertEqual(codes(result.reasons), ["row_match"])
        self.assertIn("617.42 ≥ 576.80", result.reasons[0].text)
        self.assertEqual(result.profile.content_sha256, document["content_sha256"])
        self.assertEqual(codes(result.caveats), ["no_golden_gate"])
        self.assertIn("c1_off_fp32", codes(result.families["standard"].warnings))
        fast = codes(result.families["fast"].warnings)
        self.assertEqual(fast, ["fp16_anime_careful_only", "fp16_violence_l_only"])

    def test_driver_change_within_the_range_warns(self):
        self.write("main", profile_document("cuda"))
        result = hp.resolve(self.root, observed=observation(driver="620.10"))
        self.assertEqual(result.status, "ready")
        self.assertIn("driver_changed", codes(result.warnings))

    def test_ffmpeg_and_cpu_changes_only_warn(self):
        self.write("main", profile_document("cuda"))
        changed = observation(ffmpeg=None, cpu="Another CPU")
        result = hp.resolve(self.root, observed=changed)
        self.assertEqual(result.status, "ready")
        self.assertEqual(codes(result.warnings), ["ffmpeg_changed", "machine_changed"])

    def test_last_good_needs_an_observation_and_warns(self):
        self.write("main", b"{broken")
        self.write("last_good", profile_document("cuda"))
        self.assertEqual(hp.resolve(self.root, observed=None).status, "invalid")
        result = hp.resolve(self.root, observed=observation())
        self.assertEqual((result.status, result.source), ("ready_last_good", "last_good"))
        self.assertIn("last_good_used", codes(result.warnings))

    def test_last_good_never_replaces_a_recorded_failure(self):
        self.write("main", profile_document("failed"))
        self.write("last_good", profile_document("cuda"))
        result = hp.resolve(self.root, observed=observation())
        self.assertEqual(result.status, "cuda_unavailable")
        self.assertIn("CUDA", result.message)

    def test_last_good_never_replaces_a_stale_fingerprint(self):
        self.write("main", b"{broken")
        self.write("last_good", profile_document("cuda"))
        self.assertEqual(hp.resolve(self.root, observed=observation((AMPERE,))).status, "stale_gpu")


class MandatoryCaseTests(TempRootCase):
    """The mandatory cases of section 9."""

    def test_remeasure_after_a_driver_uninstall_is_refused_and_never_cpu(self):
        self.write("main", profile_document("cuda"))
        self.write("last_good", profile_document("cuda"))
        last_good_bytes = (self.root / "state" / "hardware-profile.last-good.json").read_bytes()
        failed = profile_document("failed")
        previous = previous_had_cuda(read_profile(self.root, "main"), read_profile(self.root, "last_good"))
        outcome = classify_attempt(failed["detection"], failed["fingerprint"]["gate_key"], previous=previous)
        self.assertEqual((outcome.result, outcome.failure_code, outcome.had_cuda), ("failed", "cuda_unavailable", True))
        self.write("main", failed)
        result = hp.resolve(self.root, observed=OBSERVATIONS["pci_without_nvml"])
        self.assertEqual(result.status, "cuda_unavailable")
        self.assertTrue(all(option.knobs is None for option in result.families.values()))
        self.assertEqual((self.root / "state" / "hardware-profile.last-good.json").read_bytes(), last_good_bytes)
        with self.assertRaises(hp.HardwareRefusal) as raised:
            hp.choose("scan", root=self.root, request=hp.ScanRequest(True, 1), observed=OBSERVATIONS["match"])
        self.assertEqual(raised.exception.code, "cuda_unavailable")

    def test_an_nvidia_card_without_driver_or_an_unreadable_card_list_is_never_cpu(self):
        basic = {"ok": True, "error": None, "items": [BASIC_DISPLAY, INTEL_ADAPTER]}
        timeout = {"ok": False, "error": "quá giờ khi đọc danh sách card", "items": []}
        for name, adapters in (("basic display", basic), ("timeout", timeout)):
            with self.subTest(case=name):
                self.root = self.new_root(name.replace(" ", "-"))
                self.write("main", profile_document("cpu", adapters=adapters))
                result = hp.resolve(self.root, observed=CPU_OBSERVATION)
                self.assertEqual(result.status, "no_gated_row")
                self.assertTrue(all(option.knobs is None for option in result.families.values()))
        failed = profile_document("failed")
        failed["attempt"]["failure"]["code"] = "adapters_unreadable"
        self.write("main", rehash(failed))
        result = hp.resolve(self.root, observed=CPU_OBSERVATION)
        self.assertEqual(result.status, "cuda_unavailable")
        self.assertIn("Không xác định được máy có card NVIDIA hay không", result.message)

    def test_observed_none_never_gives_ready_last_good(self):
        self.write("last_good", profile_document("cuda"))
        for main in (None, b"{broken", cpu_with_had_cuda()):
            with self.subTest(main=type(main).__name__):
                if main is not None:
                    self.write("main", main)
                self.assertNotEqual(hp.resolve(self.root, observed=None).status, "ready_last_good")

    def test_the_test_root_is_computed_inside_resolve_only(self):
        self.assertEqual(list(inspect.signature(hp.resolve).parameters), ["root", "observed"])
        self.assertEqual(list(inspect.signature(hp.choose).parameters), ["task", "root", "request", "observed"])
        self.switch(cpu_only=True)
        with cpu_row_runnable():
            on_test_root = hp.resolve(self.root, observed=CPU_OBSERVATION)
            with mock.patch.object(job_purge, "INSTALL_ROOT", self.root):
                on_install = hp.resolve(self.root, observed=CPU_OBSERVATION)
            with mock.patch.object(job_purge, "allowed_project_root", side_effect=OSError("boom")):
                on_error = hp.resolve(self.root, observed=CPU_OBSERVATION)
        self.assertEqual((on_test_root.status, on_test_root.test_root), ("ready_ungated", True))
        self.assertEqual((on_install.status, on_install.test_root), ("no_gated_row", False))
        self.assertEqual((on_error.status, on_error.test_root), ("no_gated_row", False))

    def test_cpu_only_switch_runs_the_cpu_form_only_when_the_row_runs(self):
        self.write("main", profile_document("cuda"))
        self.switch(cpu_only=True)
        before = hp.resolve(self.root, observed=observation())
        self.assertEqual((before.status, before.source), ("cpu_row_not_ready", "cpu_only"))
        self.assertIn("cpu_only_recorded", codes(before.warnings))
        with cpu_row_runnable():
            after = hp.resolve(self.root, observed=observation())
        self.assertEqual(after.status, "ready_ungated")
        self.assertEqual({option.knobs.form for option in after.families.values()}, {"cpu"})
        self.assertTrue(all(option.capped for name, option in after.families.items() if name != "standard"))


class UntestedTests(TempRootCase):
    """Option D (section 5.6)."""

    def resolve_card(self, gpu, *, driver="617.42", torch=TORCH, arch_list=None, switch=None, **profile):
        options = {} if arch_list is None else {"arch_list": arch_list}
        self.write("main", profile_document("cuda", gpus=(gpu,), driver=driver, torch=torch, **options, **profile))
        if switch is not None:
            self.switch(untested_fast=switch)
        return hp.resolve(self.root, observed=observation((gpu,), driver=driver, torch=torch))

    def assert_standard_only(self, result):
        self.assertEqual((result.status, result.row_id, result.row_status),
                         ("ready_untested", "nvidia-untested", "untested"))
        self.assertEqual(untested_problems(result), [])
        for requested, option in result.families.items():
            self.assertEqual((option.family, option.capped, option.tf32), ("standard", requested != "standard", "off"))
            self.assertEqual(option.knobs, hp.plain_knobs(1, tf32="off"))
            self.assertEqual("family_capped" in codes(option.warnings), requested != "standard")
        self.assertNotIn("untested_fast_on", codes(result.warnings))

    def test_ampere_runs_standard_with_tf32_off_and_the_untested_label(self):
        result = self.resolve_card(AMPERE)
        self.assert_standard_only(result)
        self.assertIn(hp.Reason("untested_arch", "Chưa qua cổng trên đời card này (sm_86)"), result.warnings)
        self.assertIn("tf32_off", codes(result.warnings))
        self.assertNotIn("untested_torch", codes(result.warnings))
        self.assertFalse(result.untested_fast_applies)

    def test_ampere_is_not_refused_on_the_install_root_either(self):
        with mock.patch.object(job_purge, "INSTALL_ROOT", self.root):
            result = self.resolve_card(AMPERE)
        self.assertFalse(result.test_root)
        self.assert_standard_only(result)

    def test_ada_runs_through_sm_86(self):
        result = self.resolve_card(ADA)
        self.assert_standard_only(result)
        self.assertIn("Chưa qua cổng trên đời card này (sm_89)", [reason.text for reason in result.warnings])

    def test_blackwell_without_code_in_torch_is_refused_with_the_list(self):
        result = self.resolve_card(BLACKWELL)
        self.assertEqual(result.status, "arch_unsupported")
        self.assertIn("sm_120", result.message)
        self.assertIn("sm_50 sm_60 sm_61 sm_70 sm_75 sm_80 sm_86 sm_90", result.message)
        self.assertEqual(codes(result.reasons), ["arch_unsupported"])
        with self.assertRaises(hp.HardwareRefusal) as raised:
            hp.choose("scan", root=self.root, request=hp.ScanRequest(False, 1), observed=observation((BLACKWELL,)))
        self.assertEqual((raised.exception.status, raised.exception.code), ("arch_unsupported", "arch_unsupported"))

    def test_an_empty_arch_list_is_refused(self):
        result = self.resolve_card(AMPERE, arch_list=())
        self.assertEqual(result.status, "arch_unsupported")
        self.assertIn("(có: không có)", result.message)

    def test_ptx_of_an_older_generation_is_accepted(self):
        self.assertTrue(hp.arch_supported(["compute_90"], (12, 0)))
        self.assertFalse(hp.arch_supported(["sm_90", "sm_90a"], (12, 0)))
        self.assertFalse(hp.arch_supported(["sm_86"], (8, 0)))
        self.assertTrue(hp.arch_supported(["sm_80"], (8, 6)))

    def test_an_old_driver_is_refused_with_the_minimum(self):
        for gpu in (AMPERE, TURING):
            with self.subTest(gpu=gpu.name):
                self.root = self.new_root(f"driver-{gpu.compute_capability}")
                result = self.resolve_card(gpu, driver="552.22")
                self.assertEqual(result.status, "driver_too_old")
                self.assertIn("Driver 552.22 thấp hơn 561.17, mức tối thiểu cho CUDA 12.6 của bản torch "
                              "2.14.0+cu126", result.message)

    def test_an_unknown_cuda_tag_is_refused(self):
        arch = ("sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120")
        result = self.resolve_card(AMPERE, torch="2.15.0+cu128", arch_list=arch)
        self.assertEqual(result.status, "driver_too_old")
        self.assertIn("Chưa biết driver tối thiểu cho bản torch 2.15.0+cu128", result.message)

    def test_a_small_card_is_refused(self):
        result = self.resolve_card(SMALL_AMPERE)
        self.assertEqual(result.status, "vram_too_small")
        self.assertIn("4096 MiB", result.message)

    def test_turing_with_another_torch_build_is_untested_torch(self):
        result = self.resolve_card(TURING, torch="2.13.0+cu126")
        self.assert_standard_only(result)
        self.assertEqual(codes(result.warnings), ["untested_arch", "untested_torch", "tf32_off"])
        self.assertIn("Chưa qua cổng trên đời card này (sm_75)", [reason.text for reason in result.warnings])
        self.assertIn("2.13.0+cu126", result.warnings[1].text)

    def test_turing_with_an_ungated_driver_is_untested_driver(self):
        result = self.resolve_card(TURING, driver="565.90")
        self.assert_standard_only(result)
        self.assertEqual(codes(result.warnings), ["untested_arch", "untested_driver", "tf32_off"])
        self.assertIn("565.90", result.warnings[1].text)

    def test_a_new_generation_with_an_ungated_driver_also_says_so(self):
        result = self.resolve_card(AMPERE, driver="565.90")
        self.assert_standard_only(result)
        self.assertEqual(codes(result.warnings), ["untested_arch", "untested_driver", "tf32_off"])

    def test_the_minimum_driver_and_vram_themselves_run(self):
        for gpu, driver in ((AMPERE, "561.17"), (replace(AMPERE, vram_mib=6144), "617.42")):
            with self.subTest(vram=gpu.vram_mib, driver=driver):
                self.root = self.new_root(f"edge-{gpu.vram_mib}-{driver}")
                self.assert_standard_only(self.resolve_card(gpu, driver=driver))

    def test_the_refusals_come_in_the_order_of_section_5_6(self):
        cases = ((BLACKWELL, "552.22", "arch_unsupported"), (SMALL_AMPERE, "552.22", "driver_too_old"),
                 (replace(BLACKWELL, vram_mib=4096), "617.42", "arch_unsupported"))
        for gpu, driver, status in cases:
            with self.subTest(gpu=gpu.name, vram=gpu.vram_mib, driver=driver):
                self.root = self.new_root(f"order-{gpu.compute_capability}-{gpu.vram_mib}-{driver}")
                self.assertEqual(self.resolve_card(gpu, driver=driver).status, status)

    def test_the_switch_for_this_card_gives_the_turing_families_with_tf32_off(self):
        switch = UntestedFast(AMPERE.name, AMPERE.compute_capability, WHEN)
        result = self.resolve_card(AMPERE, switch=switch)
        self.assertEqual((result.status, result.row_id, result.untested_fast_applies),
                         ("ready_untested", "nvidia-untested-fast", True))
        self.assertEqual(untested_problems(result), [])
        self.assertEqual({name: option.family for name, option in result.families.items()},
                         {family: family for family in hp.FAMILIES})
        self.assertFalse(any(option.capped for option in result.families.values()))
        self.assertEqual({option.tf32 for option in result.families.values()}, {"off"})
        self.assertEqual(result.families["fast"].knobs, hp.prewarm_knobs("nvdec", tf32="off"))
        self.assertEqual(result.families["standard_ocr8"].knobs, hp.plain_knobs(8, tf32="off"))
        self.assertIn("untested_fast_on", codes(result.warnings))
        self.assertIn("untested_fast_on", codes(result.caveats))
        self.assertIn("RTX 2060", next(c.text for c in result.caveats if c.code == "untested_fast_on"))
        choice = hp.choose("scan", root=self.root, request=hp.ScanRequest(True, 1), observed=observation((AMPERE,)))
        self.assertEqual((choice.family, choice.row_id, choice.knobs.tf32), ("fast", "nvidia-untested-fast", "off"))
        self.assertEqual(codes(choice.warnings).count("untested_fast_on"), 1)

    def test_the_switch_without_a_self_test_keeps_cpu_decode(self):
        switch = UntestedFast(AMPERE.name, AMPERE.compute_capability, WHEN)
        result = self.resolve_card(AMPERE, switch=switch, nvdec_trial="not_run", framemd5=None)
        self.assertEqual(result.families["fast"].logo_decode, "cpu")
        self.assertIn("nvdec_unverified", codes(result.families["fast"].warnings))

    def test_the_switch_for_another_card_has_no_effect(self):
        others = (UntestedFast(TURING.name, TURING.compute_capability, WHEN),
                  UntestedFast(AMPERE.name, "8.9", WHEN), UntestedFast("NVIDIA GeForce RTX 3070", "8.6", WHEN))
        for index, switch in enumerate(others):
            with self.subTest(gpu=switch.gpu_name, capability=switch.compute_capability):
                self.root = self.new_root(f"other-{index}")
                result = self.resolve_card(AMPERE, switch=switch)
                self.assert_standard_only(result)
                self.assertIn("untested_fast_other_card", codes(result.warnings))
                self.assertFalse(result.untested_fast_applies)
                self.assertEqual(result.untested_fast, switch)

    def test_the_switch_does_nothing_on_a_gated_card(self):
        result = self.resolve_card(TURING, switch=UntestedFast(TURING.name, TURING.compute_capability, WHEN))
        self.assertEqual((result.status, result.row_id, result.untested_fast_applies),
                         ("ready", "nvidia-sm75-torch2.14.0+cu126", False))
        self.assertEqual({option.knobs for option in result.families.values()}, set(hp.LEGACY_KNOBS.values()))

    def test_untested_through_last_good_keeps_its_status(self):
        self.write("main", b"{broken")
        self.write("last_good", profile_document("cuda", gpus=(AMPERE,)))
        result = hp.resolve(self.root, observed=observation((AMPERE,)))
        self.assertEqual((result.status, result.source), ("ready_untested", "last_good"))
        self.assertIn("last_good_used", codes(result.warnings))

    def test_choose_caps_a_fast_request_and_speed_arguments_refuse_tf32_off(self):
        self.write("main", profile_document("cuda", gpus=(AMPERE,)))
        choice = hp.choose("scan", root=self.root, request=hp.ScanRequest(True, 8), observed=observation((AMPERE,)))
        self.assertEqual((choice.family, choice.requested_family, choice.row_status), ("standard", "fast", "untested"))
        self.assertEqual(choice.stage("animation_safety").tf32, "off")
        self.assertTrue({"family_capped", "untested_arch", "tf32_off"} <= set(codes(choice.warnings)))
        with self.assertRaises(ValueError) as raised:
            hp.speed_arguments(choice.knobs, config={}, source_sha256=None)
        self.assertIn("Đợt 2", str(raised.exception))


class FailedProfileTests(TempRootCase):
    """Step 4: a recorded failure names the card's missing code or an old driver, never runs on the CPU."""

    def resolve_failed(self, document, observed=None):
        self.write("main", document)
        return hp.resolve(self.root, observed=observed)

    def assert_refused(self, result, status):
        self.assertEqual(result.status, status)
        self.assertEqual(codes(result.reasons), [status])
        self.assertTrue(all(option.knobs is None and option.refusal == status for option in result.families.values()))

    def test_a_card_without_code_in_torch_is_arch_unsupported(self):
        for observed in (None, observation((BLACKWELL,))):
            with self.subTest(observed=observed is not None):
                self.root = self.new_root(f"no-kernel-{observed is not None}")
                result = self.resolve_failed(failed_on_card(BLACKWELL), observed)
                self.assert_refused(result, "arch_unsupported")
                self.assertIn("không có mã cho sm_120 của NVIDIA GeForce RTX 5070", result.message)
                self.assertIn("sm_50 sm_60 sm_61 sm_70 sm_75 sm_80 sm_86 sm_90", result.message)
        with self.assertRaises(hp.HardwareRefusal) as raised:
            hp.choose("scan", root=self.root, request=hp.ScanRequest(False, 1), observed=observation((BLACKWELL,)))
        self.assertEqual(raised.exception.code, "arch_unsupported")

    def test_steps_4_and_8_share_one_arch_rule_and_message(self):
        failed_root = self.root
        failed = self.resolve_failed(failed_on_card(BLACKWELL))
        self.root = self.new_root("measured")
        measured = self.resolve_failed(profile_document("cuda", gpus=(BLACKWELL,)), observation((BLACKWELL,)))
        self.assertEqual((failed.status, failed.message), (measured.status, measured.message))
        with mock.patch.object(hardware_resolve, "_arch_refusal", return_value=None) as rule:
            self.assertEqual(hp.resolve(failed_root, observed=None).status, "cuda_unavailable")
            self.assertEqual(hp.resolve(self.root, observed=observation((BLACKWELL,))).status, "ready_untested")
        self.assertEqual(rule.call_count, 2)

    def test_an_old_driver_is_driver_too_old(self):
        for seen_by_torch in (False, True):
            with self.subTest(seen_by_torch=seen_by_torch):
                self.root = self.new_root(f"old-driver-{seen_by_torch}")
                result = self.resolve_failed(failed_on_card(AMPERE, driver="552.22", seen_by_torch=seen_by_torch))
                self.assert_refused(result, "driver_too_old")
                self.assertIn("Driver 552.22 thấp hơn 561.17", result.message)

    def test_a_missing_kernel_comes_before_an_old_driver(self):
        result = self.resolve_failed(failed_on_card(BLACKWELL, driver="552.22"))
        self.assertEqual(result.status, "arch_unsupported")

    def test_other_failures_stay_cuda_unavailable(self):
        cases = {
            "kernel present, driver new enough": failed_on_card(AMPERE),
            "torch did not see the card": failed_on_card(AMPERE, seen_by_torch=False),
            "minimum unknown for the build": failed_on_card(AMPERE, driver="552.22", torch="2.15.0+cu128",
                                                            seen_by_torch=False),
            "driver gone": profile_document("failed"),
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                self.root = self.new_root(name.replace(" ", "-").replace(",", ""))
                result = self.resolve_failed(document, observation((AMPERE,)))
                self.assertEqual(result.status, "cuda_unavailable")
                self.assertTrue(all(option.knobs is None for option in result.families.values()))


class NvdecRuleTests(TempRootCase):
    """Section 5.5 for the fast family of the gated row."""

    def fast_option(self, observed=None, **profile):
        self.write("main", profile_document("cuda", **profile))
        result = hp.resolve(self.root, observed=observed or observation(driver=profile.get("driver", "617.42")))
        return result, result.families["fast"]

    def test_passed_self_test_on_the_same_driver_uses_nvdec(self):
        result, fast = self.fast_option()
        self.assertEqual((fast.logo_decode, fast.knobs), ("nvdec", hp.LEGACY_KNOBS["fast"]))
        self.assertEqual(result.families["standard"].logo_decode, None)

    def test_failed_self_test_falls_back_to_cpu(self):
        for options in ({"nvdec_trial": "failed", "framemd5": None}, {"framemd5": False}):
            with self.subTest(**options):
                self.root = self.new_root(f"failed-{len(options)}")
                _, fast = self.fast_option(**options)
                self.assertEqual(fast.logo_decode, "cpu")
                self.assertIn("nvdec_selftest_failed", codes(fast.warnings))

    def test_a_new_driver_without_a_self_test_is_unverified(self):
        result, fast = self.fast_option(observation(driver="620.10"))
        self.assertEqual(fast.logo_decode, "cpu")
        self.assertIn("nvdec_unverified", codes(fast.warnings))
        self.assertEqual(fast.knobs, hp.prewarm_knobs("cpu"))

    def test_a_verified_pair_needs_no_self_test(self):
        _, fast = self.fast_option(driver="576.80", nvdec_trial="not_run", framemd5=None)
        self.assertEqual(fast.logo_decode, "nvdec")

    def test_a_failed_self_test_overrides_a_verified_pair(self):
        _, fast = self.fast_option(driver="576.80", nvdec_trial="failed", framemd5=None)
        self.assertEqual(fast.logo_decode, "cpu")

    def test_changed_or_missing_ffmpeg_decodes_on_cpu_with_one_warning(self):
        changed = observation(ffmpeg=FileStat(FFMPEG_SIZE, FFMPEG_MTIME + 1))
        result, fast = self.fast_option(changed)
        self.assertEqual(fast.logo_decode, "cpu")
        self.assertIn("ffmpeg_changed", codes(result.warnings))
        self.assertNotIn("ffmpeg_changed", codes(fast.warnings))
        self.root = self.new_root("no-ffmpeg")
        result, fast = self.fast_option(ffmpeg_ok=False)
        self.assertEqual(fast.logo_decode, "cpu")
        self.assertIn("ffmpeg_unavailable", codes(result.warnings))


class ChooseTests(TempRootCase):
    REQUEST = hp.ScanRequest(True, 1)

    def test_scan_choice_carries_the_row_and_the_profile(self):
        document = profile_document("cuda")
        self.write("main", document)
        choice = hp.choose("scan", root=self.root, request=self.REQUEST, observed=observation())
        self.assertEqual((choice.task, choice.family, choice.device, choice.source, choice.row_status),
                         ("scan", "fast", "cuda", "profile", "gated"))
        self.assertEqual(choice.knobs, hp.LEGACY_KNOBS["fast"])
        self.assertEqual(choice.profile_sha256, document["content_sha256"])
        self.assertEqual(choice.fingerprint_sha256, document["fingerprint"]["sha256"])
        self.assertEqual(choice.observed_driver, "617.42")
        self.assertEqual(choice.stage("visual_logo").routing_workers, 3)
        self.assertIn("no_golden_gate", codes(choice.warnings))

    def test_scan_refuses_every_status_that_is_not_ready(self):
        cases = {
            "needs_measurement": lambda: None,
            "invalid": lambda: self.write("main", b"{broken"),
            "override_invalid": lambda: self.broken_switch(),
            "cuda_unavailable": lambda: self.write("main", profile_document("failed")),
            "stale_torch": lambda: self.write("main", profile_document("cuda", torch="2.13.0+cu126")),
            "multi_gpu_unsupported": lambda: self.write("main", profile_document("cuda", gpus=(TURING, TURING))),
            "cpu_row_not_ready": lambda: self.switch(cpu_only=True),
            "no_gated_row": lambda: self.switch(cpu_only=True),
            "stale_gpu": lambda: self.write("main", profile_document("cuda", gpus=(AMPERE,))),
            "arch_unsupported": lambda: self.write("main", profile_document("cuda", gpus=(BLACKWELL,))),
            "driver_too_old": lambda: self.write("main", profile_document("cuda", gpus=(AMPERE,), driver="552.22")),
            "vram_too_small": lambda: self.write("main", profile_document("cuda", gpus=(SMALL_AMPERE,))),
        }
        observed = {"no_gated_row": observation(torch="2.15.0+cu128"), "arch_unsupported": observation((BLACKWELL,)),
                    "driver_too_old": observation((AMPERE,), driver="552.22"),
                    "vram_too_small": observation((SMALL_AMPERE,))}
        for status, arrange in cases.items():
            with self.subTest(status=status):
                self.root = self.new_root(status)
                arrange()
                with self.assertRaises(hp.HardwareRefusal) as raised:
                    hp.choose("scan", root=self.root, request=self.REQUEST,
                              observed=observed.get(status, observation()))
                self.assertEqual((raised.exception.status, raised.exception.code), (status, status))
                self.assertTrue(raised.exception.message)
                self.assertIsInstance(raised.exception, ValueError)

    def test_scan_without_an_observation_is_refused_even_when_ready(self):
        self.write("main", profile_document("cuda"))
        with self.assertRaises(hp.HardwareRefusal) as raised:
            hp.choose("scan", root=self.root, request=self.REQUEST, observed=None)
        self.assertEqual((raised.exception.status, raised.exception.code), ("ready", "not_observed"))

    def test_export_never_raises_and_is_always_x264(self):
        arrangements = (lambda: None, lambda: self.broken_switch(), lambda: self.write("main", b"{broken"),
                        lambda: self.write("main", profile_document("cuda")))
        for index, arrange in enumerate(arrangements):
            with self.subTest(case=index):
                self.root = self.new_root(f"export-{index}")
                arrange()
                for observed in (None, observation()):
                    choice = hp.choose("export", root=self.root, observed=observed)
                    self.assertEqual((choice.task, choice.row_id, choice.row_status), ("export", "export-x264", "gated"))
                    self.assertEqual(choice.knobs, hardware_table.EXPORT_KNOBS)
                    self.assertEqual("not_observed" in codes(choice.warnings), observed is None)

    def test_wrong_arguments_are_programming_errors(self):
        with self.assertRaises(ValueError):
            hp.choose("render", root=self.root)
        with self.assertRaises(ValueError):
            hp.choose("scan", root=self.root, request=None, observed=observation())

    def test_resolve_turns_an_internal_error_into_invalid(self):
        with mock.patch.object(hardware_resolve, "resolve_root", side_effect=RuntimeError("boom")):
            result = hp.resolve(self.root, observed=observation())
        self.assertEqual(result.status, "invalid")
        self.assertIn("RuntimeError", result.message)
        self.assertTrue(all(option.refusal == "invalid" for option in result.families.values()))
        self.assertEqual(hp.resolve(self.root, observed="not an observation").status, "invalid")


if __name__ == "__main__":
    unittest.main()
