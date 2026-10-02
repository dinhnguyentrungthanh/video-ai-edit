"""Visual-logo VLM loop: processor inputs prepared on a producer thread, serial results."""
import contextlib
import hashlib
import io
import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import torch

from biliflow import visual_logo_scanner as scanner
from biliflow.visual_logo_scanner import (
    BOUNDARY_SCENE_PROMPT,
    VISUAL_LOGO_PROMPT,
    VISUAL_LOGO_RETRY_PROMPT,
    _approved_memory_match,
    _prepared_logo_windows,
    is_instruction_echo,
    select_window_evidence,
)

PREFETCH_THREAD = "biliflow-iterator-prefetch"
PROMPT_CODES = {VISUAL_LOGO_PROMPT: 1, VISUAL_LOGO_RETRY_PROMPT: 2, BOUNDARY_SCENE_PROMPT: 3}


class _Batch(dict):
    def to(self, device):
        return self


def _answer(code: int, value: int) -> str:
    if code == 1:  # logo prompt: sometimes an instruction echo that forces the retry prompt
        return ["Reply exactly NO or YES | actual brand name", "NO", f"YES | Brand{value}", "UNCERTAIN"][value % 4]
    if code == 2:
        return f"YES | Retry{value}"
    return "PROMO_FULL_FRAME" if value % 2 else "MOVIE_CONTENT"


class _Processor:
    """Encodes (prompt, JPEG bytes); flags concurrent tokenizer use."""

    def __init__(self):
        self.encodes = []
        self.decodes = []
        self.active = 0
        self.overlaps = 0
        self._guard = threading.Lock()

    def _enter(self):
        with self._guard:
            self.active += 1
            self.overlaps += self.active > 1

    def _leave(self):
        with self._guard:
            self.active -= 1

    def apply_chat_template(self, messages, **kwargs):
        self._enter()
        try:
            time.sleep(0.002)
            assert kwargs == {"add_generation_prompt": True, "tokenize": True,
                              "return_dict": True, "return_tensors": "pt"}
            content = messages[0]["content"]
            paths = [Path(item["path"]) for item in content if item["type"] == "image"]
            (prompt,) = [item["text"] for item in content if item["type"] == "text"]
            digest = hashlib.sha256(b"".join(path.read_bytes() for path in paths)).digest()
            code = PROMPT_CODES[prompt]
            self.encodes.append((code, threading.current_thread().name))
            return _Batch(input_ids=torch.tensor([[code, digest[0], digest[1], len(paths)]]))
        finally:
            self._leave()

    def decode(self, tokens, skip_special_tokens):
        self._enter()
        try:
            time.sleep(0.002)
            self.decodes.append(threading.current_thread().name)
            return _answer(int(tokens[0]) // 1000, int(tokens[0]) % 1000)
        finally:
            self._leave()


class _Model:
    device = "cpu"

    def __init__(self):
        self.calls = []

    def to(self, device):
        return self

    def eval(self):
        return self

    def generate(self, input_ids, do_sample, max_new_tokens):
        assert do_sample is False and max_new_tokens == 20
        self.calls.append((tuple(input_ids[0].tolist()), threading.current_thread().name))
        code, first = int(input_ids[0, 0]), int(input_ids[0, 1])
        return torch.cat([input_ids, torch.tensor([[code * 1000 + first]])], dim=1)


class _InlinePrefetch:
    """IteratorPrefetch stand-in that advances the producer on the consumer thread."""

    def __init__(self, process, items, *, depth):
        self.items = items

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.items)


def _windows():
    def frame(key, index, score, focus=False, memory=None):
        features = {"score": score}
        if memory:
            features["brand_memory"] = memory
        return {
            "timestamp_seconds": key[0] + 1.0 + index,
            "jpeg": f"jpeg-{key[0]}-{index}".encode() * 3,
            "focus_jpeg": f"crop-{key[0]}-{index}".encode() if focus else None,
            "features": features,
        }

    approved = {"similarity": 0.97, "memory_class": "brand", "labels": ["Acme"]}
    weak = {"similarity": 0.80, "memory_class": "brand", "labels": ["Weak"]}
    keys = [(0.0, 5.0), (5.0, 10.0), (20.0, 25.0), (100.0, 105.0), (150.0, 155.0),
            (200.0, 205.0), (250.0, 255.0), (300.0, 305.0), (350.0, 355.0), (400.0, 405.0),
            (450.0, 455.0), (575.0, 580.0), (590.0, 595.0)]
    windows = {}
    for position, key in enumerate(keys):
        memory = approved if key[0] in (150.0, 590.0) else weak if key[0] == 250.0 else None
        frames = [frame(key, 0, 0.6 + position / 100, focus=position % 2 == 0, memory=memory)]
        if position % 3:
            frames.append(frame(key, 1, 0.55, focus=position % 4 == 1))
        if position % 5 == 0:
            frames.append(frame(key, 2, 0.5))
        windows[key] = frames
    boundary = {(0.0, 5.0), (5.0, 10.0), (20.0, 25.0), (575.0, 580.0), (590.0, 595.0)}
    return windows, boundary


def _serial_blobs(frames):
    """JPEG bytes the serial loop wrote for one window, in order."""
    blobs = []
    for candidate in select_window_evidence(frames, maximum=2):
        blobs.append(candidate["jpeg"])
        if candidate.get("focus_jpeg") and len(blobs) < 5:
            blobs.append(candidate["focus_jpeg"])
        if len(blobs) >= 3:
            break
    return blobs


def _expected_calls(windows, boundary):
    """The serial loop's generate calls: (prompt code, digest bytes, frame count) per ask."""
    calls = []
    for key in sorted(windows):
        blobs = _serial_blobs(windows[key])
        digest = hashlib.sha256(b"".join(blobs)).digest()

        def ask(prompt):
            calls.append((PROMPT_CODES[prompt], digest[0], digest[1], len(blobs)))
            return _answer(PROMPT_CODES[prompt], digest[0])

        if not _approved_memory_match(select_window_evidence(windows[key], maximum=2)[0]):
            if is_instruction_echo(ask(VISUAL_LOGO_PROMPT)):
                ask(VISUAL_LOGO_RETRY_PROMPT)
        if key in boundary:
            ask(BOUNDARY_SCENE_PROMPT)
    return calls


class PreparedLogoWindowsTests(unittest.TestCase):
    def test_writes_the_serial_jpegs_and_prepares_answer_independent_prompts(self):
        windows, boundary = _windows()
        seen = []

        def prepare(paths, prompt):
            seen.append(([path.read_bytes() for path in paths], prompt))
            return ("inputs", prompt)

        with TemporaryDirectory() as directory:
            items = list(_prepared_logo_windows(sorted(windows), windows, boundary, Path(directory),
                                                prepare, scanner.ScanPerformance()))
        self.assertEqual([item[:2] for item in items],
                         [(index, key) for index, key in enumerate(sorted(windows), start=1)])
        expected_seen = []
        for index, key, candidates, strongest, paths, prepared in items:
            self.assertEqual(candidates, select_window_evidence(windows[key], maximum=2))
            self.assertIs(strongest, candidates[0])
            self.assertEqual([path.name for path in paths][0], f"window-{index:04d}-frame-1.jpg")
            wanted = []
            if not _approved_memory_match(strongest):
                wanted.append(VISUAL_LOGO_PROMPT)
            if key in boundary:
                wanted.append(BOUNDARY_SCENE_PROMPT)
            self.assertEqual(list(prepared), wanted)
            self.assertEqual(prepared, {prompt: ("inputs", prompt) for prompt in wanted})
            expected_seen += [(_serial_blobs(windows[key]), prompt) for prompt in wanted]
        self.assertEqual(seen, expected_seen)


class VisualLogoVlmPrefetchTests(unittest.TestCase):
    DURATION = 600.0

    def run_scan(self, *, inline=False):
        windows, boundary = _windows()
        processor, model = _Processor(), _Model()
        cached = {"decoded_windows": windows,
                  "decoded_counts": {key: len(frames) for key, frames in windows.items()},
                  "decoded_boundary_keys": boundary, "boundary_transitions": [],
                  "frames_scanned": 40, "heuristic_hits": 13}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            model_dir = root / "models" / "qwen"
            model_dir.mkdir(parents=True)
            (model_dir / "manifest.json").write_text(json.dumps({"model": "qwen-test"}), encoding="utf-8")
            video = root / "film.mp4"
            video.write_bytes(b"film bytes")
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                (root / name).write_bytes(b"x")
            patches = [
                mock.patch.object(scanner, "require_capacity"),
                mock.patch.object(scanner, "probe_video", return_value={
                    "streams": [{"codec_type": "video", "width": 1280, "height": 720}]}),
                mock.patch.object(scanner, "duration_seconds", return_value=self.DURATION),
                mock.patch.object(scanner, "_read_routing_cache", return_value=cached),
                mock.patch.object(scanner, "select_candidate_windows",
                                  side_effect=lambda windows, *args, **kwargs: sorted(windows)),
                mock.patch("transformers.AutoProcessor.from_pretrained", return_value=processor),
                mock.patch("transformers.AutoModelForImageTextToText.from_pretrained", return_value=model),
            ]
            if inline:
                patches.append(mock.patch.object(scanner, "IteratorPrefetch", _InlinePrefetch))
            stdout = io.StringIO()
            with contextlib.ExitStack() as stack, contextlib.redirect_stdout(stdout):
                for patch in patches:
                    stack.enter_context(patch)
                payload = scanner.scan_visual_logos(
                    project_root=root, input_path=video, report_dir=root / "reports" / "job" / "visual-logo",
                    model_path=model_dir, ffmpeg_path=root / "ffmpeg.exe", ffprobe_path=root / "ffprobe.exe",
                    device_name="cpu", source_sha256=hashlib.sha256(b"film bytes").hexdigest(),
                )
            files = {path.relative_to(root / "reports").as_posix(): path.read_bytes()
                     for path in sorted((root / "reports").rglob("*.jpg"))}
            text = json.dumps(payload)
            for form in (json.dumps(str(root))[1:-1], root.as_posix()):
                text = text.replace(form, "<ROOT>")
            payload = json.loads(text)
        lines = [line for line in stdout.getvalue().splitlines() if line.startswith("Visual-logo VLM")]
        return payload, files, lines, processor, model, windows, boundary

    def test_prefetched_vlm_loop_matches_the_serial_loop(self):
        payload, files, lines, processor, model, windows, boundary = self.run_scan()
        inline_payload, inline_files, inline_lines, *_ = self.run_scan(inline=True)

        expected = _expected_calls(windows, boundary)
        self.assertEqual([call for call, _ in model.calls], expected)
        codes = [call[0] for call in expected]
        self.assertIn(2, codes)  # an instruction echo forced a retry
        self.assertEqual(codes.count(3), len(boundary))
        self.assertEqual({thread for _, thread in model.calls}, {"MainThread"})
        self.assertEqual({thread for code, thread in processor.encodes if code != 2}, {PREFETCH_THREAD})
        self.assertEqual({thread for code, thread in processor.encodes if code == 2}, {"MainThread"})
        self.assertEqual(len(processor.encodes), len(expected))  # every prepared prompt is asked once
        self.assertEqual(set(processor.decodes), {"MainThread"})
        self.assertEqual(processor.overlaps, 0)

        def stable(value):
            value = json.loads(json.dumps(value))
            for key in ("metrics", "created_at"):
                value.pop(key, None)
            return value

        self.assertEqual(stable(payload), stable(inline_payload))
        self.assertEqual(files, inline_files)
        self.assertTrue(files)
        self.assertEqual(lines, inline_lines)
        self.assertEqual(len(lines), len(windows))
        sources = [item["visual_logo_confirmation"].get("confirmation_source")
                   for item in payload["intervals"] + payload["rejected_windows"]]
        self.assertIn("approved_brand_memory", sources)
        prefetch = payload["metrics"]["vlm_prefetch_performance"]
        self.assertEqual(prefetch["phases"]["frame_write"]["calls"], len(windows))
        self.assertEqual(prefetch["phases"]["processor"]["calls"], codes.count(1) + codes.count(3))
        self.assertFalse(any(thread.name == PREFETCH_THREAD for thread in threading.enumerate()))


if __name__ == "__main__":
    unittest.main()
