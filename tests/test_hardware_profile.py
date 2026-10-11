"""Files, knobs, command-line pieces and the frozen value of the hardware profile.

docs/HARDWARE_PROFILE.md sections 2.2, 3.1-3.6, 4.3 and 9 ("E1, thuần"). resolve() and
choose() are in test_hardware_resolve.py; the table and whole command lines in
test_hardware_table.py.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from biliflow import hardware_profile as hp
from biliflow import hardware_schema as hs
from biliflow import hardware_table
from tests.hardware_fixtures import (
    AMPERE, BASIC_DISPLAY, INTEL_ADAPTER, TURING, WHEN, TempRootCase, cpu_with_had_cuda, observation,
    profile_document, rehash,
)

SRC = Path(__file__).resolve().parents[1] / "src"
NOW = datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)
CONFIG = {
    "logo_sample_every": 2.0, "logo_boundary_sample_every": 0.25, "logo_boundary_seconds": 30.0,
    "logo_scene_change_threshold": 0.22, "logo_coverage_bucket_seconds": 300.0,
    "logo_coverage_fallbacks_per_bucket": 2, "logo_max_candidate_windows": 420,
}


def mutated(change) -> dict:
    """A valid profile with one change, both hashes recomputed so only that change is wrong."""
    document = copy.deepcopy(profile_document("cuda"))
    change(document)
    return rehash(document)


def held_once(target: Path):
    """Patch Path.open so the first open of ``target`` raises PermissionError (an antivirus holding it)."""
    real_open = Path.open
    opened: list[Path] = []

    def fake_open(self, *args, **kwargs):
        if self == target and not opened:
            opened.append(self)
            raise PermissionError(13, "held")
        return real_open(self, *args, **kwargs)
    return mock.patch.object(Path, "open", fake_open)


def run_child(code: str) -> list[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (str(SRC), os.environ.get("PYTHONPATH"))))}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True,
                            env=env)
    return result.stdout.split()


class ProfileReadTests(TempRootCase):
    def assert_invalid(self, data, where: str) -> None:
        self.write("main", data)
        read = hp.read_profile(self.root, "main")
        self.assertEqual(read.state, "invalid")
        self.assertIn(where, read.error)

    def test_a_valid_profile_reads_ok_and_a_missing_one_is_missing(self):
        self.assertEqual(hp.read_profile(self.root, "main").state, "missing")
        self.write("main", profile_document("cuda"))
        self.assertEqual(hp.read_profile(self.root, "main").state, "ok")

    def test_each_schema_fault_alone_makes_the_file_invalid(self):
        def setter(path, value):
            def change(document):
                *parents, key = path
                for part in parents:
                    document = document[part]
                document[key] = value
            return change
        cases = {
            ("profile_version", "1"): "profile_version",
            ("profile_version", True): "profile_version",
            ("profile_version", 2): "profile_version",
            ("detection", "ram_bytes", 1.5): "detection.ram_bytes",
            ("detection", "torch_cuda", "ok", 1): "detection.torch_cuda.ok",
            ("writer", "mode", "guess"): "writer.mode",
            ("detection", "nvml", "status", "maybe"): "detection.nvml.status",
            ("written_at", "2026-10-11T10:00:00"): "written_at",
            ("detection", "cpu", "model", "x" * 121): "detection.cpu.model",
            ("schema", "biliflow.other"): "schema",
        }
        for (*path, value), where in cases.items():
            with self.subTest(path=path, value=value):
                self.assert_invalid(mutated(setter(path, value)), where)

    def test_nvidia_smi_is_checked_and_may_hold_nulls(self):
        smi = {"ok": True, "driver_version": "617.42", "error": None,
               "gpus": [{"name": TURING.name, "compute_capability": "7.5", "vram_mib": 6144},
                        {"name": "NVIDIA Example", "compute_capability": None, "vram_mib": None}]}
        self.write("main", mutated(lambda d: d["detection"].update(nvidia_smi=smi)))
        self.assertEqual(hp.read_profile(self.root, "main").state, "ok")
        bad = (
            ("compute_capability", lambda s: s["gpus"][0].update(compute_capability="7")),
            ("vram_mib", lambda s: s["gpus"][0].update(vram_mib="6144")),
            ("khóa lạ", lambda s: s["gpus"][0].update(uuid="x")),
            ("nvidia_smi.ok", lambda s: s.update(ok="yes")),
            ("thiếu khóa", lambda s: s.pop("driver_version")),
        )
        for where, change in bad:
            with self.subTest(where=where):
                broken = copy.deepcopy(smi)
                change(broken)
                self.assert_invalid(mutated(lambda d, value=broken: d["detection"].update(nvidia_smi=value)), where)

    def test_nvdec_codecs_need_the_three_codecs_and_a_closed_trial(self):
        cases = (
            ("nvdec_codecs", lambda c: c.pop("av1")),
            ("khóa lạ", lambda c: c.update(vp9={"trial": "ok", "error": None})),
            ("nvdec_codecs.hevc.trial", lambda c: c["hevc"].update(trial="maybe")),
            ("nvdec_codecs.h264", lambda c: c["h264"].pop("error")),
        )
        for where, change in cases:
            with self.subTest(where=where):
                self.assert_invalid(mutated(lambda d: change(d["detection"]["ffmpeg"]["nvdec_codecs"])), where)

    def test_unknown_and_missing_keys_are_invalid(self):
        self.assert_invalid(mutated(lambda d: d.update(extra=1)), "khóa lạ")
        self.assert_invalid(mutated(lambda d: d["detection"]["cpu"].update(extra=1)), "detection.cpu")
        self.assert_invalid(mutated(lambda d: d.pop("history")), "thiếu khóa")
        self.assert_invalid(mutated(lambda d: d["detection"]["adapters"]["items"][0].update(vendor="nvidiaa")),
                            "vendor")

    def test_duplicate_keys_nan_and_infinity_are_invalid(self):
        text = json.dumps(profile_document("cuda"))
        self.assert_invalid(('{"schema": "dup", ' + text[1:]).encode(), "duplicate")
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                self.assert_invalid(text.replace('"seconds": 2.5', f'"seconds": {constant}').encode(), constant)

    def test_a_bom_is_accepted(self):
        self.write("main", b"\xef\xbb\xbf" + json.dumps(profile_document("cuda")).encode("utf-8"))
        self.assertEqual(hp.read_profile(self.root, "main").state, "ok")

    def test_a_file_above_256_kib_is_invalid(self):
        data = json.dumps(profile_document("cuda")).encode("utf-8")
        self.assert_invalid(data + b" " * (hs.MAX_FILE_BYTES + 1 - len(data)), "256 KiB")

    def test_hashes_must_match(self):
        document = profile_document("cuda")
        stale = dict(document, written_at="2026-10-12T10:00:00+07:00")
        self.assert_invalid(stale, "content_sha256")
        fingerprint = copy.deepcopy(document)
        fingerprint["fingerprint"]["sha256"] = "0" * 64
        body = {key: value for key, value in fingerprint.items() if key != "content_sha256"}
        fingerprint["content_sha256"] = hs.canonical_sha256(body)
        self.assert_invalid(fingerprint, "fingerprint.sha256")

    def test_each_cross_check(self):
        failure = {"code": "cuda_unavailable", "step": "torch_probe", "message": "x"}
        cases = (
            (lambda d: d["attempt"].update(failure=failure), "attempt.failure"),
            (lambda d: d["attempt"].update(result="failed"), "attempt.failure"),
            (lambda d: d["detection"]["torch_cuda"].update(device_count=0), "detection.torch_cuda"),
            (lambda d: d.update(history={"had_cuda": False, "first_cuda_at": None}), "detection.torch_cuda"),
            (lambda d: d["detection"]["nvml"].update(status="error"), "nvidia_gpus"),
            (lambda d: d["detection"]["torch_cuda"].update(device_count=2), "nvidia_gpus"),
        )
        for change, where in cases:
            with self.subTest(where=where):
                self.assert_invalid(mutated(change), where)
        self.assert_invalid(cpu_with_had_cuda(), "history.had_cuda")
        early = copy.deepcopy(profile_document("cpu"))
        early["history"]["first_cuda_at"] = WHEN
        self.assert_invalid(rehash(early), "history.first_cuda_at")

    def test_a_failed_mismatch_attempt_stays_readable(self):
        def change(document):
            document["attempt"].update(result="failed", failure={"code": "nvml_torch_mismatch", "step": "compare",
                                                                 "message": "khác nhau"})
            document["detection"]["torch_cuda"]["device_count"] = 2
        self.write("main", mutated(change))
        self.assertEqual(hp.read_profile(self.root, "main").state, "ok")

    def test_reads_are_cached_on_mtime_and_size(self):
        self.write("main", profile_document("cuda"))
        path = hs.profile_path(self.root, "main")
        first = hp.read_profile(self.root, "main")
        self.assertIs(hp.read_profile(self.root, "main"), first)
        stat = path.stat()
        data = path.read_bytes()
        path.write_bytes(data.replace(b'"content_sha256": "', b'"content_sha256": "x', 1)[:len(data)])
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertIs(hp.read_profile(self.root, "main"), first)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10 ** 9))
        self.assertEqual(hp.read_profile(self.root, "main").state, "invalid")

    def test_an_io_error_is_reported_but_never_cached(self):
        self.write("main", profile_document("cuda"))
        with held_once(hs.profile_path(self.root, "main")):
            held = hp.read_profile(self.root, "main")
            again = hp.read_profile(self.root, "main")
        self.assertEqual((held.state, held.error), ("invalid", "không đọc được file (PermissionError)"))
        self.assertEqual(again.state, "ok")
        self.assertIs(hp.read_profile(self.root, "main"), again)  # a read of the content is still cached


class AtomicWriteTests(TempRootCase):
    def test_a_leftover_tmp_file_is_ignored_by_readers(self):
        leftover = self.root / "state" / "hardware-profile.json.0123456789abcdef.tmp"
        leftover.write_bytes(b"{half written")
        self.assertEqual(hp.read_profile(self.root, "main").state, "missing")
        hs.write_profile(self.root, profile_document("cuda"), last_good=True)
        self.assertEqual(hp.read_profile(self.root, "main").state, "ok")
        self.assertEqual(hp.read_profile(self.root, "last_good").state, "ok")
        self.assertEqual(leftover.read_bytes(), b"{half written")

    def test_a_permission_error_on_replace_is_retried(self):
        real_replace = os.replace
        target = self.root / "state" / "example.json"
        calls = []

        def busy_once(source, destination):
            calls.append(source)
            if len(calls) == 1:
                raise PermissionError("busy")
            return real_replace(source, destination)
        with mock.patch("biliflow.hardware_schema.time.sleep") as sleep, \
                mock.patch("biliflow.hardware_schema.os.replace", side_effect=busy_once):
            hs.write_json_atomic(target, {"value": 1})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"value": 1})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])  # the same temp file, retried
        sleep.assert_called_once()
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_a_lasting_permission_error_raises_and_leaves_no_temp_file(self):
        target = self.root / "state" / "example.json"
        target.write_text('{"value": 0}', encoding="utf-8")
        with mock.patch("biliflow.hardware_schema.time.sleep"), \
                mock.patch("biliflow.hardware_schema.os.replace", side_effect=PermissionError("busy")):
            with self.assertRaises(PermissionError):
                hs.write_json_atomic(target, {"value": 1})
        self.assertEqual(target.read_text(encoding="utf-8"), '{"value": 0}')
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_only_a_successful_attempt_becomes_last_good(self):
        with self.assertRaises(hs.SchemaError):
            hs.write_profile(self.root, profile_document("failed"), last_good=True)
        self.assertEqual(hp.read_profile(self.root, "last_good").state, "missing")


class OverrideTests(TempRootCase):
    def test_missing_means_both_switches_off(self):
        read = hp.read_override(self.root)
        self.assertEqual((read.state, read.cpu_only, read.untested_fast), ("missing", False, None))

    def test_both_switches_round_trip(self):
        switch = hp.UntestedFast(AMPERE.name, AMPERE.compute_capability, WHEN)
        hp.write_override(self.root, cpu_only=True, untested_fast=switch, now=NOW)
        read = hp.read_override(self.root)
        self.assertEqual((read.state, read.cpu_only, read.untested_fast), ("ok", True, switch))
        self.assertEqual(read.changed_at, "2026-10-11T03:00:00+00:00")
        stored = json.loads(hs.override_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(stored["untested_fast"], {"gpu_name": AMPERE.name, "compute_capability": "8.6",
                                                   "enabled_at": WHEN})
        hp.write_override(self.root, cpu_only=False, untested_fast=None, now=NOW)
        read = hp.read_override(self.root)
        self.assertEqual((read.state, read.cpu_only, read.untested_fast), ("ok", False, None))

    def test_an_io_error_on_the_switch_file_is_not_cached(self):
        hp.write_override(self.root, cpu_only=True, untested_fast=None, now=NOW)
        with held_once(hs.override_path(self.root)):
            held = hp.read_override(self.root)
            again = hp.read_override(self.root)
        self.assertEqual((held.state, held.cpu_only, held.error),
                         ("invalid", False, "không đọc được file (PermissionError)"))
        self.assertEqual((again.state, again.cpu_only), ("ok", True))

    def test_a_broken_switch_file_is_invalid(self):
        good = {"schema": hs.OVERRIDE_SCHEMA, "version": 1, "cpu_only": False, "untested_fast": None,
                "changed_at": WHEN, "source": "dashboard-pc"}
        bad_switch = {"gpu_name": "x", "compute_capability": "8", "enabled_at": WHEN}
        switch = {"gpu_name": AMPERE.name, "compute_capability": "8.6", "enabled_at": WHEN}
        cases = {"json": b"{nope", "extra": dict(good, extra=1), "version": dict(good, version=2),
                 "cpu_only": dict(good, cpu_only="yes"), "untested_fast": dict(good, untested_fast=bad_switch),
                 "missing": {key: value for key, value in good.items() if key != "untested_fast"},
                 "switch type": dict(good, untested_fast="8.6"), "switch on": dict(good, untested_fast=True),
                 "switch extra": dict(good, untested_fast=dict(switch, uuid="x")),
                 "switch missing": dict(good, untested_fast={"gpu_name": AMPERE.name, "compute_capability": "8.6"}),
                 "switch naive time": dict(good, untested_fast=dict(switch, enabled_at="2026-10-11T10:00:00")),
                 "source": dict(good, source="phone")}
        for name, value in cases.items():
            with self.subTest(case=name):
                root = self.new_root(f"o-{name}")
                path = hs.override_path(root)
                path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
                self.assertEqual(hp.read_override(root).state, "invalid")

    def test_bad_arguments_are_refused_before_writing(self):
        bad = (dict(cpu_only="yes", untested_fast=None, now=NOW),
               dict(cpu_only=True, untested_fast=None, now=datetime(2026, 10, 11)),
               dict(cpu_only=True, untested_fast={"gpu_name": "x"}, now=NOW),
               dict(cpu_only=True, untested_fast=hp.UntestedFast("x", "8", WHEN), now=NOW))
        for arguments in bad:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    hp.write_override(self.root, **arguments)
        self.assertFalse(hs.override_path(self.root).exists())


class HistoryTests(unittest.TestCase):
    def test_previous_had_cuda(self):
        missing, broken = hs.ProfileRead("missing"), hs.ProfileRead("invalid", error="x")
        cpu, cuda = hs.ProfileRead("ok", profile_document("cpu")), hs.ProfileRead("ok", profile_document("cuda"))
        failed = hs.ProfileRead("ok", profile_document("failed"))
        cases = (((missing, missing), False), ((cpu, missing), False), ((missing, cpu), False),
                 ((cuda, missing), True), ((broken, missing), True), ((cpu, broken), True), ((failed, cpu), True))
        for reads, expected in cases:
            with self.subTest(states=[read.state for read in reads]):
                self.assertEqual(hs.previous_had_cuda(*reads), expected)

    def test_classify_attempt_follows_the_table_of_section_3_5(self):
        cuda, cpu = profile_document("cuda"), profile_document("cpu")

        def case(base, *, torch=None, nvml=None, adapters=None, gpus=None):
            detection = copy.deepcopy(base["detection"])
            gate = copy.deepcopy(base["fingerprint"]["gate_key"])
            detection["torch_cuda"].update(torch or {})
            detection["nvml"].update(nvml or {})
            if adapters is not None:
                detection["adapters"] = adapters
            if gpus is not None:
                gate["nvidia_gpus"] = gpus
            return detection, gate
        unreadable = {"ok": False, "error": "timeout", "items": []}
        basic = {"ok": True, "error": None, "items": [BASIC_DISPLAY, INTEL_ADAPTER]}
        rows = (
            (case(cuda), False, ("ok", None, True)),
            (case(cuda, nvml={"status": "error"}), False, ("failed", "nvml_failed", True)),
            (case(cuda, torch={"device_count": 2}), False, ("failed", "nvml_torch_mismatch", True)),
            (case(cuda, torch={"capability": "8.6"}), False, ("failed", "nvml_torch_mismatch", True)),
            (case(cpu), True, ("failed", "cuda_unavailable", True)),
            (case(cpu, torch={"error": "torch_probe_timeout: 120 s"}), True, ("failed", "torch_probe_timeout", True)),
            (case(cpu, torch={"error": "torch_probe_crashed"}), False, ("failed", "torch_probe_crashed", False)),
            (case(cpu, adapters=unreadable), False, ("failed", "adapters_unreadable", False)),
            (case(cpu, adapters=basic), False, ("failed", "cuda_unavailable", False)),
            (case(cpu, gpus=[{"name": TURING.name, "compute_capability": "7.5", "vram_mib": 6144}]), False,
             ("failed", "cuda_unavailable", False)),
            (case(cpu, nvml={"status": "error"}), False, ("failed", "nvml_failed", False)),
            (case(cpu), False, ("ok", None, False)),
        )
        for index, ((detection, gate), previous, expected) in enumerate(rows):
            with self.subTest(row=index):
                outcome = hs.classify_attempt(detection, gate, previous=previous)
                self.assertEqual((outcome.result, outcome.failure_code, outcome.had_cuda), expected)


class ScanKnobsTests(unittest.TestCase):
    @staticmethod
    def knobs(device="cuda", detect="fp32", batch=1, window=1, prewarm=False, workers=1, decode="cpu",
              logo_workers=None, animation="fp32", violence="fp32", tf32="default"):
        text = hp.TextKnobs(detect, batch, window, prewarm, workers, decode)
        return hp.ScanKnobs(device, text, workers if logo_workers is None else logo_workers, animation, violence, tf32)

    def test_every_rule_of_section_2_2_refuses(self):
        prewarm = dict(detect="fp16", batch=8, window=4, prewarm=True, workers=3, animation="fp16", violence="fp16")
        cases = {
            "fp16 detect on cpu": dict(device="cpu", detect="fp16"),
            "fp16 anime on cpu": dict(device="cpu", animation="fp16"),
            "fp16 violence on cpu": dict(device="cpu", violence="fp16"),
            "batch 8 on cpu": dict(device="cpu", batch=8),
            "window without batch": dict(window=4),
            "routing prewarm on cpu": dict(device="cpu", prewarm=True),
            "nvdec on cpu": dict(device="cpu", decode="nvdec"),
            "nvdec without prewarm": dict(decode="nvdec"),
            "workers differ": dict(prewarm, logo_workers=1),
            "tf32 off on cpu": dict(device="cpu", tf32="off"),
            "fast before G1 (detect fp32)": dict(prewarm, detect="fp32"),
            "plain with fp16 anime": dict(animation="fp16"),
            "prewarm without window": dict(prewarm, window=1),
            "prewarm with batch 1": dict(prewarm, batch=1, window=1),
        }
        for name, arguments in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    self.knobs(**arguments)

    def test_types_and_values_are_closed(self):
        for arguments in (dict(batch=True), dict(batch=2), dict(device="cuda:0"), dict(tf32="on"),
                          dict(workers=2), dict(decode="dxva2"), dict(detect="bf16")):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    self.knobs(**arguments)
        with self.assertRaises(ValueError):
            hp.ScanKnobs("cuda", {"detect_precision": "fp32"}, 1, "fp32", "fp32")
        with self.assertRaises(ValueError):
            hp.ScanRequest(1, 1)
        with self.assertRaises(ValueError):
            hp.ScanRequest(False, 4)

    def test_the_three_forms_and_tf32(self):
        cases = ((hp.prewarm_knobs("nvdec"), "prewarm"), (hp.prewarm_knobs("cpu", tf32="off"), "prewarm"),
                 (hp.plain_knobs(1), "plain"), (hp.plain_knobs(8, tf32="off"), "plain"), (hp.cpu_knobs(), "cpu"))
        for knobs, form in cases:
            with self.subTest(form=form, tf32=knobs.tf32):
                self.assertEqual(knobs.form, form)

    def test_request_family(self):
        self.assertEqual([hp.ScanRequest(*pair).family for pair in ((True, 1), (True, 8), (False, 8), (False, 1))],
                         ["fast", "fast", "standard_ocr8", "standard"])

    def test_stage_gives_each_gpu_stage_its_knobs_and_tf32(self):
        knobs = hp.prewarm_knobs("nvdec", tf32="off")
        for name in hp.GPU_STAGES:
            with self.subTest(stage=name):
                stage = knobs.stage(name)
                self.assertEqual((stage.stage, stage.device, stage.tf32), (name, "cuda", "off"))
                self.assertEqual(stage.text, knobs.text if name == "text" else None)
                self.assertEqual(stage.routing_workers, 3 if name == "visual_logo" else None)
                self.assertEqual(stage.precision, "fp16" if name == "animation_safety" else None)
                self.assertEqual(stage.violence_precision, "fp16" if name == "live_safety" else None)
        self.assertEqual(len(hp.GPU_STAGES), 10)
        with self.assertRaises(ValueError):
            knobs.stage("build_review")

    def test_legacy_knobs_are_todays_command_lines_and_immutable(self):
        self.assertEqual(dict(hp.LEGACY_KNOBS), {"fast": hp.prewarm_knobs("nvdec"), "standard_ocr8": hp.plain_knobs(8),
                                                 "standard": hp.plain_knobs(1)})
        self.assertEqual({knobs.tf32 for knobs in hp.LEGACY_KNOBS.values()}, {"default"})
        self.assertEqual(hp.LEGACY_KNOBS["fast"].text.logo_decode, "nvdec")
        with self.assertRaises(TypeError):
            hp.LEGACY_KNOBS["fast"] = hp.plain_knobs(1)  # type: ignore[index]


class SpeedArgumentsTests(unittest.TestCase):
    def test_prewarm_reproduces_the_fast_branch_of_job_pipeline(self):
        arguments = hp.speed_arguments(hp.LEGACY_KNOBS["fast"], config=CONFIG, source_sha256="f" * 64)
        self.assertEqual(arguments.text, (
            "--recognition-batch-size", 8, "--recognition-frame-window", 4, "--prewarm-logo-routing",
            "--logo-sample-every", 2.0, "--logo-boundary-sample-every", 0.25, "--logo-boundary-seconds", 30.0,
            "--logo-scene-change-threshold", 0.22, "--logo-coverage-bucket-seconds", 300.0,
            "--logo-coverage-fallbacks-per-bucket", 2, "--logo-routing-workers", 3, "--logo-decode", "nvdec",
            "--logo-source-sha256", "f" * 64, "--detect-precision", "fp16"))
        self.assertEqual((arguments.visual_logo, arguments.animation_safety, arguments.live_safety),
                         (("--routing-workers", 3), ("--precision", "fp16"), ("--violence-precision", "fp16")))

    def test_the_source_hash_is_written_only_when_present(self):
        for sha in (None, ""):
            with self.subTest(sha=sha):
                text = hp.speed_arguments(hp.LEGACY_KNOBS["fast"], config=CONFIG, source_sha256=sha).text
                self.assertNotIn("--logo-source-sha256", text)

    def test_cpu_decode_is_written_explicitly(self):
        text = hp.speed_arguments(hp.prewarm_knobs("cpu"), config=CONFIG, source_sha256=None).text
        self.assertEqual(text[text.index("--logo-decode") + 1], "cpu")

    def test_plain_and_cpu_forms(self):
        cases = ((hp.plain_knobs(8), ("--recognition-batch-size", 8)), (hp.plain_knobs(1), ()), (hp.cpu_knobs(), ()))
        for knobs, text in cases:
            with self.subTest(form=knobs.form, batch=knobs.text.recognition_batch_size):
                self.assertEqual(hp.speed_arguments(knobs, config={}, source_sha256="f" * 64),
                                 hp.SpeedArguments(text, (), (), ()))

    def test_tf32_off_is_refused_until_the_flag_exists(self):
        for knobs in (hp.plain_knobs(1, tf32="off"), hp.prewarm_knobs("nvdec", tf32="off")):
            with self.subTest(form=knobs.form):
                with self.assertRaises(ValueError) as raised:
                    hp.speed_arguments(knobs, config=CONFIG, source_sha256=None)
                self.assertIn("cli.py", str(raised.exception))
        with self.assertRaises(ValueError):
            hp.speed_arguments("fast", config=CONFIG, source_sha256=None)  # type: ignore[arg-type]


class FrozenValueTests(TempRootCase):
    def gated_choice(self, request):
        self.write("main", profile_document("cuda"))
        return hp.choose("scan", root=self.root, request=request, observed=observation())

    def test_freeze_and_parse_round_trip_with_tf32(self):
        request = hp.ScanRequest(True, 1)
        choice = self.gated_choice(request)
        value = hp.freeze_value(choice, request, NOW)
        self.assertEqual(value["knobs"]["tf32"], "default")
        self.assertEqual((value["schema"], value["version"], value["row_status"]), ("biliflow.hw-choice", 1, "gated"))
        for raw in (value, json.dumps(value, ensure_ascii=False)):
            parsed = hp.parse_frozen(raw, None)
            self.assertIsInstance(parsed, hp.FrozenScan)
            self.assertEqual((parsed.knobs, parsed.family, parsed.legacy), (choice.knobs, "fast", False))
            self.assertEqual(parsed.warnings, choice.warnings)

    def test_an_untested_choice_freezes_tf32_off(self):
        self.write("main", profile_document("cuda", gpus=(AMPERE,)))
        request = hp.ScanRequest(False, 1)
        choice = hp.choose("scan", root=self.root, request=request, observed=observation((AMPERE,)))
        value = hp.freeze_value(choice, request, NOW)
        self.assertEqual((value["row_status"], value["knobs"]["tf32"]), ("untested", "off"))
        self.assertEqual(hp.parse_frozen(value, request).knobs, hp.plain_knobs(1, tf32="off"))

    def test_none_is_a_legacy_job_whatever_the_table_says(self):
        cases = ((hp.ScanRequest(True, 1), "fast"), (hp.ScanRequest(False, 8), "standard_ocr8"),
                 (hp.ScanRequest(False, 1), "standard"))
        with mock.patch.object(hardware_table, "ROWS", ()):
            for request, family in cases:
                for raw in (None, "null"):
                    with self.subTest(family=family, raw=raw):
                        parsed = hp.parse_frozen(raw, request)
                        self.assertEqual((parsed.legacy, parsed.family, parsed.knobs),
                                         (True, family, hp.LEGACY_KNOBS[family]))
        self.assertEqual(hp.parse_frozen(None, hp.ScanRequest(True, 8)).knobs.text.logo_decode, "nvdec")
        self.assertIsInstance(hp.parse_frozen(None, None), hp.FrozenInvalid)

    def test_garbage_gives_frozen_invalid_and_never_raises(self):
        request = hp.ScanRequest(True, 1)
        value = hp.freeze_value(self.gated_choice(request), request, NOW)

        def changed(**changes):
            copied = copy.deepcopy(value)
            copied.update(changes)
            return copied
        fp32_fast = copy.deepcopy(value["knobs"])
        fp32_fast["text"]["detect_precision"] = "fp32"
        extra_knob = dict(copy.deepcopy(value["knobs"]), lanes=2)
        no_tf32 = {key: item for key, item in value["knobs"].items() if key != "tf32"}
        cases = {
            "bad json": "{nope", "number": 42, "list": [], "bytes": b"{}", "extra key": changed(extra=1),
            "missing key": {key: item for key, item in value.items() if key != "warnings"},
            "version": changed(version=2), "schema": changed(schema="other"), "knobs rule": changed(knobs=fp32_fast),
            "unknown knob": changed(knobs=extra_knob), "missing tf32": changed(knobs=no_tf32),
            "family mismatch": changed(family="standard"),
            "above request": changed(requested={"fast_scan": False, "ocr_batch_size": 1}),
            "bad warning": changed(warnings=[{"code": "made_up", "text": "x"}]),
            "naive time": changed(frozen_at="2026-10-11T10:00:00"), "row status": changed(row_status="retired"),
            "device": changed(device="cpu"), "driver": changed(observed_driver="six"),
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                parsed = hp.parse_frozen(raw, request)
                self.assertIsInstance(parsed, hp.FrozenInvalid)
                self.assertEqual(parsed.message, "Cấu hình phần cứng của job hỏng – Chạy lại")

    def test_freeze_refuses_bad_arguments(self):
        request = hp.ScanRequest(True, 1)
        choice = self.gated_choice(request)
        with self.assertRaises(ValueError):
            hp.freeze_value(choice, request, datetime(2026, 10, 11))
        with self.assertRaises(ValueError):
            hp.freeze_value(choice, hp.ScanRequest(False, 1), NOW)


class ModuleContractTests(unittest.TestCase):
    def test_importing_the_hardware_modules_loads_no_torch_nvml_psutil_and_starts_no_thread(self):
        code = (
            "import sys, threading\n"
            "before = threading.active_count()\n"
            "import biliflow.hardware_profile\n"
            "print(*(name in sys.modules for name in ('biliflow.hardware_table', 'biliflow.hardware_resolve',"
            " 'biliflow.job_purge')))\n"
            "import biliflow.hardware_schema, biliflow.hardware_table, biliflow.hardware_resolve\n"
            "print(threading.active_count() - before)\n"
            "print(*(name in sys.modules for name in ('torch', 'pynvml', 'psutil')))\n"
        )
        self.assertEqual(run_child(code), ["False", "False", "False", "0", "False", "False", "False"])

    def test_importing_the_control_center_loads_no_hardware_module_and_no_torch(self):
        code = (
            "import sys\n"
            "import biliflow.control_center\n"
            "print(sorted(name for name in sys.modules if name.startswith('biliflow.hardware_')))\n"
            "print('torch' in sys.modules)\n"
        )
        self.assertEqual(run_child(code), ["[]", "False"])

    def test_the_re_exports_are_the_file_layer_functions(self):
        for name in ("read_profile", "read_override", "write_override", "UntestedFast", "Observation", "GpuInfo",
                     "FileStat"):
            with self.subTest(name=name):
                self.assertIs(getattr(hp, name), getattr(hs, name))

    def test_no_hardware_module_writes_a_job_setting(self):
        # The per-job value is written from Đợt 2 only, with JOB_SETTING_NAMES (tests/test_job_purge.py:640-649).
        for path in sorted((SRC / "biliflow").glob("hardware_*.py")):
            with self.subTest(module=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("set_setting(", text)
                self.assertIsNone(re.search(r"""f?["']hw:""", text))


if __name__ == "__main__":
    unittest.main()
