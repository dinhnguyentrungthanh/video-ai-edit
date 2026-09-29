from types import SimpleNamespace
import unittest

from biliflow.ocr_batch_experiment import SameWidthReader, recognize_same_width


class OcrBatchExperimentTests(unittest.TestCase):
    def setUp(self):
        self.reader = SimpleNamespace(model_lang="latin", character="abc", lang_char="ab",
                                      recognizer=None, converter=None, device="cpu")

    def crops(self, horizontal, free, grey, model_height):
        name, width = (horizontal or free)[0]
        return [(name, "pixels:" + name)], width

    def test_preserves_crop_width_order_and_batch_bound(self):
        calls = []
        def infer(*args):
            width, crops = args[2], args[5]
            calls.append((width, [c[0] for c in crops]))
            return [(name, pixels, 0.8) for name, pixels in crops]
        boxes = [("first", 128), ("second", 64), ("third", 128), ("fourth", 128)]
        result = recognize_same_width(self.reader, None, boxes, [("free", 64)], batch_size=2,
            image_list_fn=self.crops, text_fn=infer, image_height=64)
        self.assertEqual([r[0] for r in result], ["first", "second", "third", "fourth", "free"])
        self.assertEqual(calls, [(128, ["first", "third"]), (128, ["fourth"]), (64, ["second", "free"])])

    def test_missing_prediction_fails_instead_of_dropping_crop(self):
        with self.assertRaisesRegex(RuntimeError, "different number"):
            recognize_same_width(self.reader, None, [("first", 128)], [], batch_size=2,
                image_list_fn=self.crops, text_fn=lambda *args: [], image_height=64)

    def test_empty_frame_does_not_invoke_model(self):
        def unexpected(*args):
            self.fail("Empty frame must not call recognition")
        self.assertEqual(recognize_same_width(self.reader, None, [], [],
            image_list_fn=self.crops, text_fn=unexpected, image_height=64), [])

    def test_interrupt_propagates_without_returning_partial_predictions(self):
        calls = []
        def infer(*args):
            calls.append(args[2])
            if len(calls) == 2:
                raise KeyboardInterrupt("cancelled")
            return [(name, pixels, .8) for name, pixels in args[5]]
        with self.assertRaises(KeyboardInterrupt):
            recognize_same_width(self.reader, None, [("first", 64), ("second", 128)], [],
                image_list_fn=self.crops, text_fn=infer, image_height=64)
        self.assertEqual(calls, [64, 128])

    def test_unsupported_language_and_batch_are_explicit(self):
        with self.assertRaises(ValueError):
            recognize_same_width(self.reader, None, [], [], batch_size=128)
        self.reader.model_lang = "arabic"
        with self.assertRaises(ValueError):
            recognize_same_width(self.reader, None, [], [])

    def test_adapter_rejects_unvalidated_options_before_model_calls(self):
        adapter = SameWidthReader(self.reader)
        with self.assertRaisesRegex(ValueError, "Unvalidated OCR options"):
            adapter.readtext(None, detail=0)

    def test_wide_crops_reduce_batch_size_without_resizing(self):
        calls = []
        def infer(*args):
            calls.append((args[2], len(args[5]), args[9]))
            return [(name, pixels, 0.8) for name, pixels in args[5]]
        boxes = [(str(i), 8192) for i in range(3)]
        result = recognize_same_width(self.reader, None, boxes, [], batch_size=8,
            image_list_fn=self.crops, text_fn=infer, image_height=64)
        self.assertEqual(calls, [(8192, 1, 1)] * 3)
        self.assertEqual([row[0] for row in result], ["0", "1", "2"])

    def test_scan_defaults_remain_serial_and_unsupported_device_fails_early(self):
        import inspect
        from biliflow.textscan import scan_text
        from pathlib import Path
        self.assertEqual(inspect.signature(scan_text).parameters["recognition_batch_size"].default, 1)
        with self.assertRaisesRegex(ValueError, "requires CUDA"):
            scan_text(project_root=Path('.'), input_path=Path('missing.mp4'), report_dir=Path('.'),
                model_dir=Path('.'), ffmpeg_path=Path('.'), ffprobe_path=Path('.'),
                device_name="cpu", recognition_batch_size=8)

    def test_cli_batching_is_explicit_and_default_is_one(self):
        from biliflow.cli import build_parser
        parser = build_parser()
        args = ["scan-text", "--input", "movie.mp4", "--report-dir", "reports/test"]
        self.assertEqual(parser.parse_args(args).recognition_batch_size, 1)
        self.assertEqual(parser.parse_args(args + ["--recognition-batch-size", "8"]).recognition_batch_size, 8)


class CrossFrameFixture:
    """Frames are (name, [(box_name, width), ...]); detection is faked per frame."""

    def setUp(self):
        import numpy as np
        self.np = np
        self.calls = []
        self.detected = {}
        reader = SimpleNamespace(model_lang="latin", character="abc", lang_char="ab",
                                 recognizer=None, converter=None, device="cuda")
        def detect(color, reformat=False):
            boxes = self.detected[int(color[0, 0, 0])]
            return [boxes], [[]]
        reader.detect = detect
        self.reader = reader

    def frame(self, index, boxes, size=4):
        image = self.np.full((size, size, 3), index, dtype=self.np.uint8)
        self.detected[index] = boxes
        return (index, image)

    def crops(self, horizontal, free, grey, model_height):
        name, width = horizontal[0]
        return [(name, self.np.zeros((2, 2), dtype=self.np.uint8))], width

    def reader_for(self, **kwargs):
        from biliflow.ocr_batch_experiment import CrossFrameReader
        names = {}
        def infer(*args):
            self.calls.append((args[2], len(args[5])))
            return [(names.setdefault(id(crop), f"p{len(names)}"), "t", 0.9) for crop in args[5]]
        return CrossFrameReader(self.reader, image_list_fn=self.crops, text_fn=infer,
                                image_height=64, **kwargs)

    def run_frames(self, frames, **kwargs):
        from biliflow.ocr_batch_experiment import VALIDATED_READTEXT_OPTIONS
        reader = self.reader_for(**kwargs)
        out = list(reader.iter_readtext(frames, image_of=lambda item: item[1], **VALIDATED_READTEXT_OPTIONS))
        return reader, out


class CrossFrameReaderTests(CrossFrameFixture, unittest.TestCase):
    def test_groups_same_width_across_frames_and_restores_order(self):
        frames = [self.frame(1, [("a", 128), ("b", 64)]), self.frame(2, []),
                  self.frame(3, [("c", 128)]), self.frame(4, [("d", 64), ("e", 128)])]
        reader, out = self.run_frames(frames, frame_window=4)
        self.assertEqual([item[0] for item, _ in out], [1, 2, 3, 4])
        self.assertEqual([len(p) for _, p in out], [2, 0, 1, 2])
        self.assertEqual(self.calls, [(128, 3), (64, 2)])
        self.assertEqual(reader.stats.groups, 1)
        self.assertEqual(reader.stats.recognition_calls, 2)

    def test_prediction_belongs_to_its_own_frame_crop(self):
        from biliflow.ocr_batch_experiment import recognize_prepared
        def infer(*args):
            return [(crop, "t", .5) for crop in args[5]]
        frames = [[(128, "f0c0"), (64, "f0c1")], [(64, "f1c0"), (128, "f1c1")]]
        result = recognize_prepared(self.reader, frames, batch_size=8, text_fn=infer, image_height=64)
        self.assertEqual([[p[0] for p in f] for f in result], [["f0c0", "f0c1"], ["f1c0", "f1c1"]])

    def test_window_one_matches_single_frame_calls(self):
        frames = [self.frame(1, [("a", 128), ("b", 128)]), self.frame(2, [("c", 128)])]
        reader, out = self.run_frames(frames, frame_window=1)
        self.assertEqual(self.calls, [(128, 2), (128, 1)])
        self.assertEqual(reader.stats.groups, 2)

    def test_partial_final_group_and_empty_frames_are_emitted(self):
        frames = [self.frame(i, [] if i % 2 else [("x", 64)]) for i in range(1, 6)]
        reader, out = self.run_frames(frames, frame_window=4)
        self.assertEqual([item[0] for item, _ in out], [1, 2, 3, 4, 5])
        self.assertEqual(reader.stats.group_sizes, [4, 1])
        self.assertEqual(reader.stats.frames_emitted, 5)

    def test_byte_budget_flushes_early_without_loss_or_duplication(self):
        frames = [self.frame(i, [("x", 64)], size=16) for i in range(1, 6)]
        per_frame = 16 * 16 * 3 + 4
        reader, out = self.run_frames(frames, frame_window=8, byte_budget=per_frame * 2 + 1)
        self.assertEqual([item[0] for item, _ in out], [1, 2, 3, 4, 5])
        self.assertEqual(sum(reader.stats.group_sizes), 5)
        self.assertTrue(all(size <= 2 for size in reader.stats.group_sizes))
        self.assertGreater(reader.stats.budget_flushes, 0)
        self.assertLessEqual(reader.stats.max_pending_bytes, per_frame * 2 + 1)

    def test_single_frame_over_budget_is_recognized_whole(self):
        frames = [self.frame(1, [("a", 64), ("b", 64), ("c", 64)], size=16)]
        reader, out = self.run_frames(frames, frame_window=4, byte_budget=10)
        self.assertEqual(len(out[0][1]), 3)
        self.assertEqual(self.calls, [(64, 3)])

    def test_wide_crops_across_frames_stay_serial(self):
        frames = [self.frame(i, [("w", 8192)]) for i in range(1, 4)]
        self.run_frames(frames, frame_window=4)
        self.assertEqual(self.calls, [(8192, 1)] * 3)

    def test_crop_view_counts_parent_buffer(self):
        from biliflow.ocr_batch_experiment import _owned_nbytes
        parent = self.np.zeros((100, 100), dtype=self.np.uint8)
        self.assertEqual(_owned_nbytes(parent[:2, :2]), 10000)

    def test_missing_prediction_and_interrupt_propagate(self):
        from biliflow.ocr_batch_experiment import CrossFrameReader, VALIDATED_READTEXT_OPTIONS
        frames = [self.frame(1, [("a", 64)]), self.frame(2, [("b", 64)])]
        short = CrossFrameReader(self.reader, image_list_fn=self.crops, image_height=64,
                                 text_fn=lambda *args: [("p", "t", .9)])
        with self.assertRaisesRegex(RuntimeError, "different number"):
            list(short.iter_readtext(frames, image_of=lambda i: i[1], **VALIDATED_READTEXT_OPTIONS))
        def interrupt(*args):
            raise KeyboardInterrupt
        stopped = CrossFrameReader(self.reader, image_list_fn=self.crops, image_height=64, text_fn=interrupt)
        emitted = []
        with self.assertRaises(KeyboardInterrupt):
            for item in stopped.iter_readtext(frames, image_of=lambda i: i[1], **VALIDATED_READTEXT_OPTIONS):
                emitted.append(item)
        self.assertEqual(emitted, [])

    def test_rejects_unvalidated_options_and_windows(self):
        from biliflow.ocr_batch_experiment import CrossFrameReader
        with self.assertRaises(ValueError):
            CrossFrameReader(self.reader, frame_window=0)
        with self.assertRaises(ValueError):
            CrossFrameReader(self.reader, frame_window=9)
        reader = CrossFrameReader(self.reader)
        with self.assertRaisesRegex(ValueError, "Unvalidated"):
            list(reader.iter_readtext([], detail=0))
