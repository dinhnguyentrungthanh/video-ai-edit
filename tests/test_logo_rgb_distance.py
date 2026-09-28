import unittest

import numpy as np

from biliflow.visual_logo_scanner import _rgb_background_distance


class LogoRgbDistanceTests(unittest.TestCase):
    def test_all_channel_values_and_half_integer_medians_are_bit_exact(self):
        values = np.arange(256, dtype=np.uint8)
        red, green = np.meshgrid(values, values)
        frames = [np.stack((red, green, (red.astype(int) + green) % 256), axis=2).astype(np.uint8)]
        rng = np.random.default_rng(741)
        frames.append(rng.integers(0, 256, (180, 320, 3), dtype=np.uint8))
        for frame in frames:
            for background in ([0., 0., 0.], [255., 255., 255.], [.5, 127.5, 254.5],
                               [32., 128., 253.], [127.5, 127.5, 127.5]):
                background = np.array(background)
                expected = np.linalg.norm(frame.astype(np.float32) - background, axis=2) / 441.673
                actual = _rgb_background_distance(frame, background)
                np.testing.assert_array_equal(actual, expected)
                np.testing.assert_array_equal(actual >= .12, expected >= .12)
                self.assertEqual(actual.dtype, expected.dtype)

    def test_noncontiguous_rgb_crop_keeps_pixels_and_background_unchanged(self):
        rng = np.random.default_rng(114)
        pixels = rng.integers(0, 256, (180, 320, 3), dtype=np.uint8)
        original = pixels.copy()
        crop = pixels[::2, 10:-10:2]
        background = np.median(crop.reshape(-1, 3), axis=0)
        original_background = background.copy()
        expected = np.linalg.norm(crop.astype(np.float32) - background, axis=2) / 441.673
        np.testing.assert_array_equal(_rgb_background_distance(crop, background), expected)
        np.testing.assert_array_equal(pixels, original)
        np.testing.assert_array_equal(background, original_background)


if __name__ == '__main__':
    unittest.main()
