import copy
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np
import torch

from biliflow.vlm_confirmation import (
    VIOLENCE_CONFIRMATION_PROMPT,
    _answer_state,
    _sample_context_frames,
    _timestamp,
    confirm_violence_report,
)

PREFETCH_THREAD = "biliflow-iterator-prefetch"
DURATION = 1000.0
NO_FRAMES_AFTER_MS = 900_000.0  # the fake decoder fails beyond 900 s


class VlmConfirmationTests(unittest.TestCase):
    def test_timestamp_uses_strongest_frame_evidence(self) -> None:
        interval = {
            "start_seconds": 100.0,
            "end_seconds": 110.0,
            "strongest_frame": "thumbnails/clip-003151-3150.875s.jpg",
        }
        self.assertEqual(_timestamp(interval), 3150.875)

    def test_timestamp_falls_back_to_interval_midpoint(self) -> None:
        interval = {"start_seconds": 10.0, "end_seconds": 14.0}
        self.assertEqual(_timestamp(interval), 12.0)

    def test_answer_state_keeps_uncertain_outputs_for_human_review(self) -> None:
        self.assertEqual(_answer_state("YES"), "CONFIRMED")
        self.assertEqual(_answer_state("no"), "REJECTED")
        self.assertEqual(_answer_state("I cannot tell"), "UNCERTAIN")


class FakeCapture:
    """Stateful seek/read decoder; frame pixels depend on the decoder position."""

    def __init__(self, log, fail_on_read=None):
        self.log = log
        self.position_ms = 0.0
        self.reads = 0
        self.fail_on_read = fail_on_read
        self.released = False

    def isOpened(self):
        return True

    def set(self, prop, value):
        self.log.append(("set", round(float(value), 3), threading.current_thread().name))
        self.position_ms = float(value)
        return True

    def read(self):
        self.reads += 1
        self.log.append(("read", round(self.position_ms, 3), threading.current_thread().name))
        if self.fail_on_read is not None and self.reads == self.fail_on_read:
            raise RuntimeError("decoder broke")
        if self.position_ms > NO_FRAMES_AFTER_MS:
            return False, None
        value = int(self.position_ms // 40) % 251
        frame = np.full((8, 12, 3), value, dtype=np.uint8)
        frame[0, 0] = (value * 7 % 256, value * 3 % 256, 11)
        # A real decoder advances after a read.
        self.position_ms += 40.0
        return True, frame

    def release(self):
        self.released = True
        self.log.append(("release", None, threading.current_thread().name))


class FakeBatch(dict):
    def to(self, device):
        return self


class FakeProcessor:
    """Encodes the JPEG bytes it is given; flags any concurrent encode/decode."""

    def __init__(self):
        self.encodes = []
        self.paths = []
        self.decodes = []
        self.active = 0
        self.overlaps = 0
        self._guard = threading.Lock()

    def _enter(self):
        with self._guard:
            self.active += 1
            if self.active > 1:
                self.overlaps += 1

    def _leave(self):
        with self._guard:
            self.active -= 1

    def apply_chat_template(self, messages, **kwargs):
        self._enter()
        try:
            time.sleep(0.003)
            content = messages[0]["content"]
            paths = [Path(item["path"]) for item in content if item["type"] == "image"]
            texts = [item["text"] for item in content if item["type"] == "text"]
            assert texts == [VIOLENCE_CONFIRMATION_PROMPT]
            assert kwargs == {"add_generation_prompt": True, "tokenize": True,
                              "return_dict": True, "return_tensors": "pt"}
            digest = hashlib.sha256(b"".join(path.read_bytes() for path in paths)).digest()
            self.paths.extend(paths)
            self.encodes.append(([f"{p.parent.name}/{p.name}" for p in paths],
                                 threading.current_thread().name))
            return FakeBatch(input_ids=torch.tensor([[digest[0], digest[1], digest[2], len(paths)]]))
        finally:
            self._leave()

    def decode(self, tokens, skip_special_tokens):
        self._enter()
        try:
            time.sleep(0.003)
            self.decodes.append(threading.current_thread().name)
            value = int(tokens[0])
            return [" yes", "No.", "maybe"][value % 3]
        finally:
            self._leave()


class FakeModel:
    device = "cpu"

    def __init__(self, fail_on_call=None):
        self.calls = []
        self.fail_on_call = fail_on_call

    def to(self, device):
        return self

    def eval(self):
        return self

    def generate(self, input_ids, do_sample, max_new_tokens):
        self.calls.append((input_ids.tolist(), threading.current_thread().name))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("CUDA out of memory")
        assert do_sample is False and max_new_tokens == 8
        answer = int(input_ids.sum()) * 31 + int(input_ids[0, 0])
        return torch.cat([input_ids, torch.tensor([[answer]])], dim=1)


def serial_reference(intervals, duration, processor, model, log):
    """The confirmation loop as it ran before prefetching (git 16d203e), on the same fakes."""
    capture = FakeCapture(log)
    retained, rejected = [], []
    answers = {"CONFIRMED": 0, "REJECTED": 0, "UNCERTAIN": 0}
    with tempfile.TemporaryDirectory(prefix="biliflow-vlm-confirm-") as temporary:
        root = Path(temporary)
        for index, raw_interval in enumerate(intervals):
            interval = copy.deepcopy(raw_interval)
            frame_dir = root / f"interval-{index:05d}"
            frame_dir.mkdir()
            center = _timestamp(interval)
            frames = _sample_context_frames(capture, center_seconds=center, duration_seconds=duration,
                                            context_seconds=2.0, frame_count=5, output_dir=frame_dir)
            if not frames:
                answer, state = "NO_FRAMES", "UNCERTAIN"
            else:
                messages = [{"role": "user", "content": (
                    [{"type": "image", "path": str(frame)} for frame in frames]
                    + [{"type": "text", "text": VIOLENCE_CONFIRMATION_PROMPT}])}]
                inputs = processor.apply_chat_template(
                    messages, add_generation_prompt=True, tokenize=True, return_dict=True,
                    return_tensors="pt").to(model.device)
                generated = model.generate(**inputs, do_sample=False, max_new_tokens=8)
                answer = processor.decode(generated[0][inputs["input_ids"].shape[-1]:],
                                          skip_special_tokens=True).strip()
                state = _answer_state(answer)
            answers[state] += 1
            interval["vlm_confirmation"] = {"state": state, "answer": answer,
                                            "center_seconds": round(center, 3),
                                            "frames_sampled": len(frames)}
            (rejected if state == "REJECTED" else retained).append(interval)
    return retained, rejected, answers


def make_intervals(count=14):
    intervals = []
    for index in range(count):
        start = 30.0 + index * 47.5
        item = {"start_seconds": start, "end_seconds": start + 4.0, "max_score": 0.3 + index / 100,
                "priority": "context"}
        if index % 3:
            item["strongest_frame"] = f"thumbnails/clip-{index:06d}-{start + 1.875:.3f}s.jpg"
        intervals.append(item)
    intervals.append({"start_seconds": 0.0, "end_seconds": 0.5, "max_score": 0.4,
                      "strongest_frame": "thumbnails/clip-000900-0.125s.jpg"})  # clamps at 0
    intervals.append({"start_seconds": 997.0, "end_seconds": 999.9, "max_score": 0.5})  # past 900 s: no frames
    intervals.append({"start_seconds": 899.0, "end_seconds": 901.0, "max_score": 0.6})  # 3 of 5 frames decode
    return intervals


class ConfirmViolencePrefetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "reports" / "job" / "violence").mkdir(parents=True)
        self.model_dir = self.root / "models" / "qwen"
        self.model_dir.mkdir(parents=True)
        (self.model_dir / "manifest.json").write_text(json.dumps({"model": "qwen-test"}), encoding="utf-8")
        self.video = self.root / "input.mp4"
        self.video.write_bytes(b"video")
        self.intervals = make_intervals()
        self.report = self.root / "reports" / "job" / "violence" / "scan.json"
        self.report.write_text(json.dumps({
            "scan_type": "violence", "status": "REVIEW_REQUIRED", "input": str(self.video),
            "duration_seconds": DURATION, "intervals": self.intervals,
            "metrics": {"elapsed_seconds": 1.0},
        }), encoding="utf-8")
        self.output = self.report.with_name("scan-confirmed.json")

    def tearDown(self):
        self.temp.cleanup()

    def run_confirmation(self, processor, model, log, fail_on_read=None):
        captures = []

        def open_capture(path):
            capture = FakeCapture(log, fail_on_read=fail_on_read)
            captures.append(capture)
            return capture

        stdout = io.StringIO()
        with mock.patch("biliflow.vlm_confirmation.cv2.VideoCapture", side_effect=open_capture), \
                mock.patch("transformers.AutoProcessor.from_pretrained", return_value=processor), \
                mock.patch("transformers.AutoModelForImageTextToText.from_pretrained", return_value=model), \
                redirect_stdout(stdout):
            try:
                payload = confirm_violence_report(project_root=self.root, report_path=self.report,
                                                  output_path=self.output, model_path=self.model_dir,
                                                  device_name="cpu")
            except BaseException as error:
                return None, captures, stdout.getvalue(), error
        return payload, captures, stdout.getvalue(), None

    def assert_no_prefetch_thread(self):
        self.assertFalse(any(thread.name == PREFETCH_THREAD for thread in threading.enumerate()))

    def test_output_and_call_order_match_the_serial_loop(self):
        serial_log, serial_processor, serial_model = [], FakeProcessor(), FakeModel()
        retained, rejected, answers = serial_reference(self.intervals, DURATION, serial_processor,
                                                       serial_model, serial_log)
        log, processor, model = [], FakeProcessor(), FakeModel()
        payload, captures, stdout, error = self.run_confirmation(processor, model, log)
        self.assertIsNone(error)

        # Same decoder calls (positions and order), all on the one producer thread.
        self.assertEqual([entry[:2] for entry in log[:-1]], [entry[:2] for entry in serial_log])
        self.assertEqual({entry[2] for entry in log[:-1]}, {PREFETCH_THREAD})
        self.assertEqual(log[-1][0], "release")  # released after the producer stopped
        self.assertTrue(captures[0].released)
        # Same JPEG sets to the processor, the same model inputs and answers, in order.
        self.assertEqual([names for names, _ in processor.encodes],
                         [names for names, _ in serial_processor.encodes])
        self.assertEqual({thread for _, thread in processor.encodes}, {PREFETCH_THREAD})
        self.assertEqual([ids for ids, _ in model.calls], [ids for ids, _ in serial_model.calls])
        self.assertEqual({thread for _, thread in model.calls}, {"MainThread"})
        self.assertEqual(set(processor.decodes), {"MainThread"})
        self.assertEqual(processor.overlaps, 0)  # the tokenizer is never used by two threads at once

        self.assertEqual(payload["intervals"], retained)
        self.assertEqual(payload["confirmation"]["rejected"], [
            {"start_seconds": item.get("start_seconds"), "end_seconds": item.get("end_seconds"),
             "max_score": item.get("max_score"), "vlm_confirmation": item.get("vlm_confirmation")}
            for item in rejected])
        self.assertEqual(payload["confirmation"]["answer_counts"], answers)
        states = {item["vlm_confirmation"]["state"] for item in retained + rejected}
        self.assertEqual(states, {"CONFIRMED", "REJECTED", "UNCERTAIN"})
        self.assertIn("NO_FRAMES", [item["vlm_confirmation"]["answer"] for item in retained])
        self.assertIn(3, [item["vlm_confirmation"]["frames_sampled"] for item in retained + rejected])
        # Progress lines in interval order.
        lines = stdout.splitlines()
        self.assertEqual([line.split(" ")[0] for line in lines],
                         [f"{i + 1}/{len(self.intervals)}" for i in range(len(self.intervals))])
        # Every JPEG lived in the temporary directory, which is gone.
        self.assertTrue(processor.paths)
        self.assertFalse(any(path.exists() for path in processor.paths))
        written = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(written["intervals"], retained)
        prefetch = written["metrics"]["confirmation_prefetch_performance"]
        self.assertEqual(prefetch["phases"]["frame_extract"]["calls"], len(self.intervals))
        self.assertEqual(prefetch["phases"]["processor"]["calls"], len(serial_processor.encodes))
        self.assertEqual(written["metrics"]["confirmation_performance"]["phases"]["model_step"]["calls"],
                         len(serial_model.calls))
        self.assert_no_prefetch_thread()

    def test_decoder_error_arrives_after_the_intervals_before_it(self):
        log, processor, model = [], FakeProcessor(), FakeModel()
        # Read 13 is the third read of the third interval.
        payload, captures, stdout, error = self.run_confirmation(processor, model, log, fail_on_read=13)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("decoder broke", str(error))
        self.assertEqual([line.split(" ")[0] for line in stdout.splitlines()],
                         [f"1/{len(self.intervals)}", f"2/{len(self.intervals)}"])
        self.assertEqual(len(model.calls), 2)
        self.assertFalse(self.output.exists())
        self.assertTrue(captures[0].released)
        self.assert_no_prefetch_thread()

    def test_model_failure_stops_the_producer_before_cleanup(self):
        log, processor, model = [], FakeProcessor(), FakeModel(fail_on_call=2)
        payload, captures, stdout, error = self.run_confirmation(processor, model, log)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("CUDA out of memory", str(error))
        self.assertFalse(self.output.exists())
        self.assert_no_prefetch_thread()
        self.assertEqual(log[-1][0], "release")
        self.assertTrue(captures[0].released)
        # Bounded read-ahead: two consumed, queue depth 2, one pending, one being prepared.
        self.assertLessEqual(len(processor.encodes), 2 + 2 + 2)
        self.assertTrue(processor.paths)
        self.assertFalse(any(path.exists() for path in processor.paths))


if __name__ == "__main__":
    unittest.main()
