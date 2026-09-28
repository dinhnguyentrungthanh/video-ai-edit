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
