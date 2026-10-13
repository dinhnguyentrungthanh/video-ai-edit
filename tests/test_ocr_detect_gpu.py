"""CRAFT input built on the GPU: bit-identical to EasyOCR's CPU preprocessing, else EasyOCR's own path."""
import unittest
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np
import torch
from easyocr import detection
from easyocr.imgproc import normalizeMeanVariance, resize_aspect_ratio

from biliflow import ocr_detect_gpu
from biliflow.ocr_detect_gpu import craft_input, gpu_textbox
from biliflow.ocr_precision import with_detect_precision

DEVICES = ("cpu", "cuda") if torch.cuda.is_available() else ("cpu",)
# 960x540 is the fast-scan analysis size of a 16:9 film (padded to 544); 544 needs no pad.
SHAPES = ((540, 960), (544, 960), (17, 33), (404, 960))
DETECT = {"canvas_size": 2560, "mag_ratio": 1.0, "text_threshold": 0.7, "link_threshold": 0.4,
          "low_text": 0.4, "poly": False}


def reference_input(image):
    resized, _, _ = resize_aspect_ratio(image, 2560, interpolation=cv2.INTER_LINEAR, mag_ratio=1.0)
    return np.transpose(normalizeMeanVariance(resized), (2, 0, 1))[None]


def fake_craft(x):
    """Deterministic stand-in for CRAFT: half-resolution score and link maps of the input."""
    pooled = torch.nn.functional.avg_pool2d(x, 2)
    score = (pooled.mean(dim=1) > 0.0).to(x.dtype)
    return torch.stack([score, score], dim=-1), pooled


def frame_with_text(height=540, width=960):
    rng = np.random.default_rng(7)
    image = rng.integers(0, 60, size=(height, width, 3), dtype=np.uint8)
    for top, left, bottom, right in ((40, 60, 70, 400), (300, 500, 330, 900), (500, 20, 536, 300)):
        image[top:bottom, left:right] = 250
    image.setflags(write=False)  # frames read from the FFmpeg pipe are read-only
    return image


def textbox_arguments(device, **overrides):
    return {**DETECT, "device": device, "optimal_num_chars": None, "threshold": 0.2,
            "bbox_min_score": 0.2, "bbox_min_size": 3, "max_candidates": 0, **overrides}


class CraftInputTests(unittest.TestCase):
    def test_input_is_bit_identical_to_easyocr_preprocessing(self):
        rng = np.random.default_rng(3)
        for device in DEVICES:
            for height, width in SHAPES:
                with self.subTest(device=device, shape=(height, width)):
                    image = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
                    image.setflags(write=False)
                    expected = reference_input(image)
                    actual = craft_input(image, device).cpu().numpy()
                    self.assertEqual(actual.dtype, np.float32)
                    self.assertEqual(actual.shape, expected.shape)
                    # Compare the bits, so -0.0 against 0.0 or any last-place rounding would fail.
                    self.assertTrue(np.array_equal(actual.view(np.uint32), expected.view(np.uint32)))

    def test_boxes_match_easyocr_on_the_same_device(self):
        image = frame_with_text()
        for device in DEVICES:
            with self.subTest(device=device), mock.patch.object(ocr_detect_gpu, "_is_cuda", return_value=True):
                expected = detection.get_textbox(fake_craft, image, **textbox_arguments(device))
                actual = gpu_textbox(detection.get_textbox)(fake_craft, image, **textbox_arguments(device))
                self.assertEqual(len(expected[0]), 3)
                self.assertEqual(len(actual), len(expected))
                for got, want in zip(actual[0], expected[0], strict=True):
                    self.assertEqual(got.dtype, want.dtype)
                    self.assertTrue(np.array_equal(got, want))
                # The float polygons before EasyOCR's int32 cast match too.
                _boxes, want_polys = detection.test_net(2560, 1.0, fake_craft, image, 0.7, 0.4, 0.4, False, device)
                got_polys = ocr_detect_gpu._detect(fake_craft, image, 1.0, 0.7, 0.4, 0.4, False, device)
                for got, want in zip(got_polys[0], want_polys[0], strict=True):
                    self.assertTrue(np.array_equal(np.asarray(got).view(np.uint32), np.asarray(want).view(np.uint32)))

    def test_other_inputs_use_easyocr_unchanged(self):
        image = frame_with_text()
        cases = {
            "cpu device": ((image,), {"device": "cpu"}),
            "float frame": ((image.astype(np.float32),), {}),
            "grey frame": ((image[:, :, 0],), {}),
            "four channels": ((np.dstack([image, image[:, :, :1]]),), {}),
            "empty frame": ((image[:0],), {}),
            "char estimate": ((image,), {"optimal_num_chars": 5}),
            "magnified": ((image,), {"mag_ratio": 2.0}),
            "canvas smaller than frame": ((image,), {"canvas_size": 512}),
        }
        for name, ((frame,), overrides) in cases.items():
            with self.subTest(name):
                original = mock.Mock(return_value="easyocr result")
                arguments = textbox_arguments(overrides.pop("device", "cuda"), **overrides)
                with mock.patch.object(ocr_detect_gpu, "craft_input") as built:
                    result = gpu_textbox(original)("net", frame, **arguments)
                self.assertEqual(result, "easyocr result")
                original.assert_called_once_with("net", frame, **arguments)
                built.assert_not_called()

    @unittest.skipUnless(torch.cuda.is_available(), "needs CUDA")
    def test_cuda_path_does_not_call_easyocr(self):
        original = mock.Mock()
        result = gpu_textbox(original)(fake_craft, frame_with_text(), **textbox_arguments("cuda"))
        original.assert_not_called()
        self.assertEqual(len(result[0]), 3)


class DetectPrecisionWiringTests(unittest.TestCase):
    def test_fp16_reader_gets_gpu_textbox_and_caller_keeps_easyocr(self):
        reader = SimpleNamespace(device="cuda", detector=object(), get_textbox=detection.get_textbox)
        self.assertIs(with_detect_precision(reader, "fp32").get_textbox, detection.get_textbox)
        variant = with_detect_precision(reader, "fp16")
        self.assertIs(reader.get_textbox, detection.get_textbox)
        self.assertIsNot(variant.get_textbox, detection.get_textbox)
        self.assertIs(variant.get_textbox.original, detection.get_textbox)

    def test_other_detectors_are_left_alone(self):
        def dbnet_textbox(*args, **kwargs):
            return []
        reader = SimpleNamespace(device="cuda", detector=object(), get_textbox=dbnet_textbox)
        self.assertIs(with_detect_precision(reader, "fp16").get_textbox, dbnet_textbox)
        bare = with_detect_precision(SimpleNamespace(device="cuda", detector=object()), "fp16")
        self.assertFalse(hasattr(bare, "get_textbox"))


if __name__ == "__main__":
    unittest.main()
