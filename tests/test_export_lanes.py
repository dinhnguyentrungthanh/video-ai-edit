"""When an export may run beside a scan (perf plans A1b, A1d): the pure checks of export_lanes."""
import json
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from biliflow import export_lanes
from biliflow.export_lanes import (
    MIB,
    REASON_TEXT,
    VramRead,
    export_candidate,
    frame_size,
    largest_nvenc_need_mib,
    normalize_mode,
    nvenc_need_mib,
    read_vram,
    scan_budget,
    stage_beside_export,
    stored_mode,
    vram_decision,
)
from biliflow.final_renderer import export_deferral_path

MEASURED = {"NVENC_NEED_MIB": ((1280 * 720, 300), (1920 * 1080, 500)), "SCAN_PEAK_MIB": 6000,
            "SAFETY_MARGIN_MIB": 1000}
UNMEASURED = {"NVENC_NEED_MIB": None, "SCAN_PEAK_MIB": None, "SAFETY_MARGIN_MIB": None}
STAGES = {"light": (800, (1920, 1080)), "small_only": (700, (1280, 640)), "heavy": (5000, (1920, 1080))}
A1B_EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "a1b" / "vram-2026-10-14"
A1D_EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "a1d" / "2026-10-17" / "check.json"


def video(width, height):
    return {"streams": [{"codec_type": "audio"}, {"codec_type": "video", "width": width, "height": height}]}


class ModeTests(unittest.TestCase):
    def test_only_auto_or_off_is_accepted(self):
        self.assertEqual((normalize_mode("auto"), normalize_mode("off")), ("auto", "off"))
        for value in ("on", "", None, 1, "AUTO"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_mode(value)

    def test_an_unknown_stored_value_is_the_default(self):
        self.assertEqual(export_lanes.DEFAULT_MODE, "auto")
        self.assertEqual([stored_mode(value) for value in ("off", "auto", None, "on", 0)],
                         ["off", "auto", "auto", "auto", "auto"])


@patch.multiple(export_lanes, **UNMEASURED)
class UnmeasuredTests(unittest.TestCase):
    def test_nothing_opens_the_lane_without_measured_numbers(self):
        # Until the A1b VRAM measurement fills them in (with its evidence), the lane never opens.
        self.assertFalse(export_lanes.measured())
        for name, value in MEASURED.items():
            with self.subTest(only=name), patch.object(export_lanes, name, value):
                self.assertFalse(export_lanes.measured())
        with patch.multiple(export_lanes, **MEASURED):
            self.assertTrue(export_lanes.measured())
        self.assertIsNone(nvenc_need_mib(1920, 1080))
        decision = vram_decision(VramRead(24576, 0, 24576), baseline_mib=0, need_mib=1)
        self.assertEqual((decision.parallel, decision.reason), (False, "not_measured"))
        probe = Mock()
        candidate = export_candidate(Path("."), {"plan": "p", "output": "o"}, probe=probe)
        self.assertEqual((candidate.eligible, candidate.reason), (False, "not_measured"))
        probe.assert_not_called()


@patch.multiple(export_lanes, **MEASURED)
class VramDecisionTests(unittest.TestCase):
    def test_the_need_is_the_smallest_measured_frame_that_covers_it(self):
        for size, need in (((1280, 720), 300), ((720, 1280), 300), ((854, 480), 300), ((1920, 1080), 500),
                           ((1080, 1920), 500), ((1440, 1080), 500), ((1920, 1088), None),
                           ((2560, 1080), None), ((3840, 2160), None), ((0, 1080), None)):
            with self.subTest(size=size):
                self.assertEqual(nvenc_need_mib(*size), need)

    def test_the_lane_opens_only_with_the_margin_left_after_the_scan_peak(self):
        # total - max(used, baseline + 6000) - need >= 1000
        for total, used, baseline, need, parallel, left in (
            (12288, 1000, 1000, 500, True, 4788),
            (8500, 1000, 1000, 500, True, 1000),    # exactly the margin
            (8499, 1000, 1000, 500, False, 999),
            (12288, 9000, 1000, 500, True, 2788),   # the scan already holds more than its peak
            (12288, 11000, 1000, 500, False, 788),
            (12288, 1000, 5000, 500, False, 788),   # something else held VRAM before the scan started
        ):
            with self.subTest(total=total, used=used, baseline=baseline):
                decision = vram_decision(VramRead(total, used, total - used), baseline_mib=baseline, need_mib=need)
                self.assertEqual((decision.parallel, decision.detail["left_mib"]), (parallel, left))
                self.assertEqual(decision.reason, "opened" if parallel else "vram_short")
                self.assertEqual((decision.detail["scan_peak_mib"], decision.detail["margin_mib"]), (6000, 1000))
                self.assertEqual(decision.text, REASON_TEXT[decision.reason])

    def test_an_unreadable_gpu_never_opens_the_lane(self):
        for read, baseline in ((None, 0), (VramRead(12288, 0, 12288), None)):
            with self.subTest(read=read, baseline=baseline):
                decision = vram_decision(read, baseline_mib=baseline, need_mib=500)
                self.assertEqual((decision.parallel, decision.reason), (False, "vram_unreadable"))

    def test_the_scan_is_budgeted_at_the_peak_of_the_stage_it_runs(self):
        # A1d: total - max(used, baseline + the stage's peak) - need >= 1000
        for peak, parallel, left in ((800, True, 9988), (5000, True, 5788), (9788, True, 1000), (9789, False, 999)):
            with self.subTest(peak=peak):
                decision = vram_decision(VramRead(12288, 1000, 11288), baseline_mib=1000, need_mib=500,
                                         scan_peak_mib=peak)
                self.assertEqual((decision.parallel, decision.detail["left_mib"]), (parallel, left))
                self.assertEqual(decision.detail["scan_peak_mib"], peak)
        # What NVML shows now still counts when the stage holds more than its peak.
        decision = vram_decision(VramRead(12288, 11000, 1288), baseline_mib=1000, need_mib=500, scan_peak_mib=800)
        self.assertEqual((decision.parallel, decision.detail["left_mib"]), (False, 788))


@patch.multiple(export_lanes, **MEASURED, STAGE_PEAK_MIB=STAGES)
class ScanBudgetTests(unittest.TestCase):
    """A1d: a scan stage is budgeted at its own measured peak, only where that measurement holds."""

    def budget(self, stage="light", *, profile="careful", fast_scan=True, frame=(1280, 534)):
        return scan_budget(stage, profile=profile, fast_scan=fast_scan, frame=frame)

    def test_a_measured_stage_of_a_measured_scan_gets_its_own_peak(self):
        for stage, frame, peak in (("light", (1280, 534), 800), ("light", (1920, 1080), 800),
                                   ("light", (1080, 1920), 800), ("heavy", (640, 360), 5000),
                                   ("small_only", (1280, 640), 700), ("small_only", (640, 1280), 700)):
            with self.subTest(stage=stage, frame=frame):
                budget = self.budget(stage, frame=frame)
                self.assertEqual((budget.stage, budget.peak_mib, budget.basis), (stage, peak, "stage"))

    def test_anything_the_measurement_does_not_cover_keeps_the_largest_peak(self):
        for case, budget, basis in (
            ("a stage never measured", self.budget("gore"), "stage_not_measured"),
            ("another profile", self.budget(profile="fast"), "scan_settings"),
            ("Quét nhanh off", self.budget(fast_scan=False), "scan_settings"),
            ("an unreadable source", self.budget(frame=None), "frame_unknown"),
            ("a larger source", self.budget(frame=(1920, 1088)), "frame_not_measured"),
            ("a 4K source", self.budget("heavy", frame=(3840, 2160)), "frame_not_measured"),
            ("wider than measured", self.budget("small_only", frame=(1920, 400)), "frame_not_measured"),
            ("taller than measured", self.budget("small_only", frame=(1280, 720)), "frame_not_measured"),
        ):
            with self.subTest(case=case):
                self.assertEqual((budget.peak_mib, budget.basis), (6000, basis))
        with patch.object(export_lanes, "STAGE_PEAK_MIB", None):
            self.assertEqual((self.budget().peak_mib, self.budget().basis), (6000, "stage_not_measured"))
        with patch.object(export_lanes, "SCAN_PEAK_MIB", None):
            self.assertIsNone(self.budget())

    def test_the_largest_measured_nvenc_need(self):
        self.assertEqual(largest_nvenc_need_mib(), 500)
        with patch.object(export_lanes, "NVENC_NEED_MIB", None):
            self.assertIsNone(largest_nvenc_need_mib())


@patch.multiple(export_lanes, **MEASURED)
class StageBesideExportTests(unittest.TestCase):
    """A1d: the main lane starts a GPU stage beside a running export only within the VRAM left."""

    def test_the_stage_starts_only_with_the_margin_left(self):
        # total - (used now + the export's need + the stage's peak) >= 1000
        for used, peak, need, parallel, left in (
            (1500, 800, 500, True, 9488),
            (1500, 9288, 500, True, 1000),   # exactly the margin
            (1500, 9289, 500, False, 999),
            (6000, 5000, 500, False, 788),   # other programs grew since the export started
        ):
            with self.subTest(used=used, peak=peak):
                decision = stage_beside_export(VramRead(12288, used, 12288 - used), scan_peak_mib=peak,
                                               export_need_mib=need)
                self.assertEqual((decision.parallel, decision.detail["left_mib"]), (parallel, left))
                self.assertEqual(decision.reason, "stage_fits" if parallel else "stage_waits")
                self.assertEqual(
                    {key: decision.detail[key] for key in ("total_mib", "used_mib", "scan_peak_mib", "need_mib",
                                                           "margin_mib")},
                    {"total_mib": 12288, "used_mib": used, "scan_peak_mib": peak, "need_mib": need, "margin_mib": 1000},
                )
                self.assertEqual(decision.text, REASON_TEXT[decision.reason])

    def test_without_a_reading_or_a_measurement_the_stage_waits(self):
        decision = stage_beside_export(None, scan_peak_mib=800, export_need_mib=500)
        self.assertEqual((decision.parallel, decision.reason), (False, "stage_vram_unreadable"))
        for case in ({"scan_peak_mib": None, "export_need_mib": 500}, {"scan_peak_mib": 800, "export_need_mib": None}):
            with self.subTest(case=case):
                decision = stage_beside_export(VramRead(24576, 0, 24576), **case)
                self.assertEqual((decision.parallel, decision.reason), (False, "stage_not_measured"))
        with patch.object(export_lanes, "SAFETY_MARGIN_MIB", None):
            decision = stage_beside_export(VramRead(24576, 0, 24576), scan_peak_mib=800, export_need_mib=500)
            self.assertEqual((decision.parallel, decision.reason), (False, "stage_not_measured"))
        for reason in ("stage_waits", "stage_vram_unreadable", "stage_not_measured", "stage_fits"):
            self.assertIn(reason, REASON_TEXT)


class RecordedNumbersTests(unittest.TestCase):
    """The numbers shipped in export_lanes are measured ones, nothing guessed (A1d): the A1b
    measurement's two runs on clips and A1d's whole-film scan."""

    def measured_processes(self):
        """(stage, MiB above the baseline, frame) of every scan process measured with no export beside it."""
        settings = (export_lanes.STAGE_PEAK_PROFILE, export_lanes.STAGE_PEAK_FAST_SCAN)
        for run in ("run1", "run2"):
            summary = json.loads((A1B_EVIDENCE / run / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual((summary["settings"]["profile"], summary["settings"]["fast_scan"]), settings)
            frames = {summary["jobs"][tag]: (clip["width"], clip["height"]) for tag, clip in summary["clips"].items()}
            for item in summary["stage_processes"]:
                if not item["overlaps_render"]:
                    yield item["stage"], item["peak_above_baseline_mib"], frames[item["job_id"]]
        check = json.loads(A1D_EVIDENCE.read_text(encoding="utf-8"))
        self.assertEqual((check["settings"]["profile"], check["settings"]["fast_scan"]), settings)
        frames = {check["jobs"][tag]: (clip["width"], clip["height"]) for tag, clip in check["clips"].items()}
        for item in check["details"]["whole_film"]["processes"]:  # the film was scanned alone
            yield item["stage"], max(item["peak_above_baseline_mib"].values()), frames[item["job_id"]]

    def test_the_stage_peaks_are_the_largest_measured_without_an_export_beside(self):
        expected: dict[str, tuple[int, tuple[int, int]]] = {}
        for stage, mib, job_frame in self.measured_processes():
            if stage == "build_review":
                continue
            peak, frame = expected.get(stage, (0, (0, 0)))
            expected[stage] = (max(peak, mib), max(frame, job_frame, key=lambda size: size[0] * size[1]))
        self.assertEqual(export_lanes.STAGE_PEAK_MIB, expected)
        self.assertEqual(export_lanes.SCAN_PEAK_MIB, max(peak for peak, _frame in expected.values()))
        # The four named in the A1d request; visual_logo went 1 MiB above 4690 on the whole film.
        self.assertEqual({stage: expected[stage][0] for stage in ("visual_logo", "localize_logo", "text",
                                                                  "confirm_violence")},
                         {"visual_logo": 4691, "localize_logo": 1309, "text": 833, "confirm_violence": 4943})


@patch.multiple(export_lanes, **MEASURED)
class ExportCandidateTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ("input", "work", "output"):
            (self.root / name).mkdir()
        self.source = self.root / "input" / "movie.mp4"
        self.source.write_bytes(b"source")
        profile = patch("biliflow.export_lanes.profile_nvenc_trial")
        self.profile = profile.start()
        self.addCleanup(profile.stop)
        self.arrange()

    def arrange(self):
        """An "auto" export of a 1080p source whose NVENC the hardware profile confirms."""
        self.render = {"plan": "work/movie-edit-plan.json", "output": "output/movie-reviewed.mp4"}
        self.write_plan()
        self.profile.reset_mock()
        self.profile.return_value = "ok"
        self.probe = Mock(return_value=video(1920, 1080))

    def write_plan(self, render_settings=None, source=True):
        plan = {"status": "READY_FOR_FINAL_RENDER"}
        if source:
            plan["source"] = {"path": str(self.source)}
        if render_settings is not None:
            plan["render_settings"] = render_settings
        (self.root / "work" / "movie-edit-plan.json").write_text(json.dumps(plan), encoding="utf-8")

    def candidate(self):
        return export_candidate(self.root, self.render, probe=self.probe)

    def test_an_auto_export_of_a_measured_frame_with_nvenc_confirmed_is_eligible(self):
        candidate = self.candidate()
        self.assertEqual((candidate.eligible, candidate.reason, candidate.need_mib, candidate.width, candidate.height),
                         (True, "opened", 500, 1920, 1080))
        self.assertEqual(candidate.output, self.root / "output" / "movie-reviewed.mp4")
        self.probe.assert_called_once_with(self.source)
        # The profile is asked about the root's own FFmpeg: no trial encode beside a scan.
        self.profile.assert_called_once_with(self.root, self.root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe")

    def test_each_failed_check_names_its_reason(self):
        cases = (
            ("no_render_settings", lambda: setattr(self, "render", None)),
            ("no_render_settings", lambda: self.render.pop("output")),
            ("plan_unreadable", lambda: (self.root / "work" / "movie-edit-plan.json").write_text("{", encoding="utf-8")),
            ("plan_unreadable", lambda: self.write_plan(source=False)),
            ("plan_unreadable", lambda: self.write_plan(render_settings={"video_encoder": "av1"})),
            ("forced_x264", lambda: self.write_plan(render_settings={"video_encoder": "libx264"})),
            ("nvenc_not_confirmed", lambda: setattr(self.profile, "return_value", "failed")),
            ("nvenc_not_confirmed", lambda: setattr(self.profile, "return_value", None)),
            ("size_unknown", lambda: setattr(self.probe, "return_value", {"streams": []})),
            ("size_unknown", lambda: setattr(self.probe, "side_effect", OSError("ffprobe failed"))),
            ("size_not_measured", lambda: setattr(self.probe, "return_value", video(3840, 2160))),
        )
        for index, (reason, change) in enumerate(cases):
            with self.subTest(index=index, reason=reason):
                self.arrange()
                change()
                candidate = self.candidate()
                self.assertEqual((candidate.eligible, candidate.reason), (False, reason))
                self.assertIn(reason, REASON_TEXT)
                if reason not in {"size_unknown", "size_not_measured"}:
                    self.probe.assert_not_called()  # the source is only probed once all else passed

    def test_an_export_already_handed_back_stays_in_the_main_lane(self):
        record = export_deferral_path(self.root, self.root / self.render["output"])
        record.parent.mkdir(parents=True)
        record.write_text("{}", encoding="utf-8")
        candidate = self.candidate()
        self.assertEqual((candidate.eligible, candidate.reason), (False, "deferred"))
        self.profile.assert_not_called()
        self.probe.assert_not_called()

    def test_frame_size_reads_the_first_video_stream(self):
        self.assertEqual(frame_size(video(1280, 720)), (1280, 720))
        for probe in ({}, {"streams": [{"codec_type": "video"}]},
                      {"streams": [{"codec_type": "video", "width": 0, "height": 720}]},
                      {"streams": ["video"]}, {"streams": [{"codec_type": "video", "width": "x", "height": 1}]}):
            with self.subTest(probe=probe):
                self.assertIsNone(frame_size(probe))


class ReadVramTests(unittest.TestCase):
    """NVML in process (no nvidia-smi, no torch): any failure reads as None, never an exception."""

    @staticmethod
    def fake_nvml(*, init=None, memory=None):
        return types.SimpleNamespace(
            nvmlInit=Mock(side_effect=init),
            nvmlShutdown=Mock(),
            nvmlDeviceGetHandleByIndex=Mock(return_value="gpu0"),
            nvmlDeviceGetMemoryInfo=Mock(side_effect=memory),
        )

    def test_gpu_0_memory_in_mib(self):
        module = self.fake_nvml(memory=lambda handle: types.SimpleNamespace(
            total=12288 * MIB, used=1000 * MIB + 5, free=11288 * MIB - 5))
        with patch.dict(sys.modules, {"pynvml": module}):
            self.assertEqual(read_vram(), VramRead(12288, 1000, 11287))
        module.nvmlDeviceGetHandleByIndex.assert_called_once_with(0)
        module.nvmlShutdown.assert_called_once_with()

    def test_no_driver_or_an_nvml_error_reads_as_none(self):
        failing_init = self.fake_nvml(init=RuntimeError("NVML Shared Library Not Found"))
        failing_read = self.fake_nvml(memory=RuntimeError("GPU is lost"))
        for module in (failing_init, failing_read):
            with self.subTest(module=module), patch.dict(sys.modules, {"pynvml": module}):
                self.assertIsNone(read_vram())
        failing_init.nvmlShutdown.assert_not_called()
        failing_read.nvmlShutdown.assert_called_once_with()
        with patch.dict(sys.modules, {"pynvml": None}):  # not installed
            self.assertIsNone(read_vram())


if __name__ == "__main__":
    unittest.main()
