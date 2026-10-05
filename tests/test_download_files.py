import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.download_files import (
    MAX_NAME_LENGTH,
    UnsafePathError,
    safe_remove_tree,
    sanitize_name,
    sha256_file,
    tree_size,
    unique_target,
    verify_video,
)


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = Path(os.environ.get("BILIFLOW_FFMPEG") or ROOT / "tools/ffmpeg/bin/ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe.exe")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def make_clip(path: Path, *, seconds: float = 12, audio: bool = True) -> Path:
    command = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", "aac", "-b:a", "64k", "-shortest"]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(path)]
    subprocess.run(command, check=True, creationflags=CREATE_NO_WINDOW)
    return path


def corrupt_tail(source: Path, target: Path) -> Path:
    data = bytearray(source.read_bytes())
    for index in range(int(len(data) * 0.75), len(data) - 2000, 7):
        data[index] = (data[index] * 31 + 7) & 0xFF
    target.write_bytes(bytes(data))
    return target


class NameTests(unittest.TestCase):
    def test_forbidden_and_control_characters_are_removed(self):
        self.assertEqual(sanitize_name('a<b>c:d"e/f\\g|h?i*j\x07k'), "a b c d e f g h i j k")

    def test_trailing_dots_spaces_and_a_typed_extension_are_dropped(self):
        self.assertEqual(sanitize_name("  Tập 1 ...  "), "Tập 1")
        self.assertEqual(sanitize_name("phim.mp4"), "phim")
        self.assertEqual(sanitize_name("phim.MP4. "), "phim")

    def test_names_are_nfc(self):
        decomposed = "Tiếng Việt"
        self.assertEqual(sanitize_name(decomposed), "Tiếng Việt")

    def test_reserved_device_names_are_changed(self):
        for name in ("CON", "con", "NUL.txt", "COM1", "lpt9", "AUX ", "PRN"):
            with self.subTest(name=name):
                cleaned = sanitize_name(name)
                self.assertNotEqual(cleaned.split(".")[0].strip().upper(),
                                    name.split(".")[0].strip().upper())
        self.assertEqual(sanitize_name("CONSOLE"), "CONSOLE")

    def test_long_names_are_cut_to_the_limit_with_the_extension(self):
        cleaned = sanitize_name("x" * 400)
        self.assertEqual(len(cleaned + ".mp4"), MAX_NAME_LENGTH)

    def test_empty_names_fall_back(self):
        self.assertEqual(sanitize_name("  ?? ", fallback="Tên gốc"), "Tên gốc")
        self.assertEqual(sanitize_name("", fallback="..."), "video")
        self.assertEqual(sanitize_name(' <>"| ', fallback=None), "")


class ExtensionListTests(unittest.TestCase):
    def test_matches_the_watcher(self):
        from biliflow.download_files import VIDEO_EXTENSIONS
        from biliflow.job_import import VIDEO_EXTENSIONS as WATCHED
        self.assertEqual(set(VIDEO_EXTENSIONS), set(WATCHED))


class UniqueTargetTests(unittest.TestCase):
    def test_existing_names_get_a_number(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            self.assertEqual(unique_target(folder, "Phim").name, "Phim.mp4")
            (folder / "Phim.mp4").write_bytes(b"1")
            self.assertEqual(unique_target(folder, "Phim").name, "Phim (2).mp4")
            (folder / "phim (2).MP4").write_bytes(b"2")
            self.assertEqual(unique_target(folder, "Phim").name, "Phim (3).mp4")

    def test_numbered_names_stay_within_the_limit(self):
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            stem = sanitize_name("y" * 400)
            (folder / f"{stem}.mp4").write_bytes(b"1")
            target = unique_target(folder, stem)
            self.assertLessEqual(len(target.name), MAX_NAME_LENGTH)
            self.assertTrue(target.name.endswith(" (2).mp4"))


class SafeRemoveTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.base = self.root / "temp" / "downloads"
        self.base.mkdir(parents=True)

    def tearDown(self):
        self.directory.cleanup()

    def test_removes_a_task_folder_and_reports_its_size(self):
        task = self.base / "7"
        (task / "frag").mkdir(parents=True)
        (task / "a.part").write_bytes(b"x" * 100)
        (task / "frag" / "b").write_bytes(b"y" * 50)
        self.assertEqual(tree_size(task), 150)
        self.assertEqual(safe_remove_tree(self.base, task), 150)
        self.assertFalse(task.exists())
        self.assertTrue(self.base.is_dir())

    def test_a_missing_folder_is_not_an_error(self):
        self.assertEqual(safe_remove_tree(self.base, self.base / "missing"), 0)

    def test_refuses_the_base_itself_and_anything_outside(self):
        outside = self.root / "input"
        outside.mkdir()
        (outside / "keep.mp4").write_bytes(b"keep")
        for target in (self.base, outside, self.base / ".." / ".." / "input", self.root):
            with self.subTest(target=str(target)), self.assertRaises(UnsafePathError):
                safe_remove_tree(self.base, target)
        self.assertTrue((outside / "keep.mp4").is_file())

    @unittest.skipUnless(os.name == "nt", "directory junctions are Windows-only")
    def test_a_junction_is_unlinked_without_touching_its_target(self):
        outside = self.root / "input"
        outside.mkdir()
        (outside / "keep.mp4").write_bytes(b"keep")
        link = self.base / "9"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                       check=True, capture_output=True, creationflags=CREATE_NO_WINDOW)
        self.assertEqual(safe_remove_tree(self.base, link), 0)
        self.assertFalse(os.path.lexists(link))
        self.assertTrue((outside / "keep.mp4").is_file())

    @unittest.skipUnless(os.name == "nt", "directory junctions are Windows-only")
    def test_a_junction_inside_a_task_folder_is_not_followed(self):
        outside = self.root / "input"
        outside.mkdir()
        (outside / "keep.mp4").write_bytes(b"keep")
        task = self.base / "10"
        task.mkdir()
        subprocess.run(["cmd", "/c", "mklink", "/J", str(task / "link"), str(outside)],
                       check=True, capture_output=True, creationflags=CREATE_NO_WINDOW)
        safe_remove_tree(self.base, task)
        self.assertFalse(task.exists())
        self.assertTrue((outside / "keep.mp4").is_file())


class Sha256Tests(unittest.TestCase):
    def test_matches_hashlib(self):
        import hashlib
        with TemporaryDirectory() as directory:
            path = Path(directory) / "f.bin"
            path.write_bytes(b"abc" * 1000)
            self.assertEqual(sha256_file(path), hashlib.sha256(b"abc" * 1000).hexdigest())


@unittest.skipUnless(FFMPEG.exists() and FFPROBE.exists(), "project FFmpeg is required (BILIFLOW_FFMPEG)")
class VerifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = TemporaryDirectory()
        folder = Path(cls.directory.name)
        cls.good = make_clip(folder / "good.mp4")
        cls.silent = make_clip(folder / "silent.mp4", audio=False)
        cls.broken = corrupt_tail(cls.good, folder / "broken.mp4")
        cls.renamed = folder / "good.txt"
        shutil.copyfile(cls.good, cls.renamed)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def verify(self, path, expected=None):
        return verify_video(FFPROBE, FFMPEG, path, expected_duration=expected)

    def test_a_good_clip_passes_and_reports_codecs(self):
        result = self.verify(self.good, expected=12.4)
        self.assertTrue(result.ok, result)
        self.assertEqual(result.video_codec, "h264")
        self.assertEqual(result.audio_codec, "aac")
        self.assertAlmostEqual(result.duration_seconds, 12.0, delta=0.2)
        self.assertEqual((result.width, result.height), (320, 240))

    def test_missing_audio_fails(self):
        result = self.verify(self.silent)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NO_AUDIO_STREAM")

    def test_duration_far_from_the_probe_fails(self):
        result = self.verify(self.good, expected=100)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "DURATION_MISMATCH")
        self.assertTrue(self.verify(self.good, expected=13.9).ok)

    def test_decode_errors_fail(self):
        result = self.verify(self.broken)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "DECODE_ERROR")

    def test_unknown_extension_and_unreadable_files_fail(self):
        self.assertEqual(self.verify(self.renamed).code, "BAD_EXTENSION")
        with TemporaryDirectory() as directory:
            junk = Path(directory) / "junk.mp4"
            junk.write_bytes(b"not a video")
            self.assertEqual(self.verify(junk).code, "PROBE_FAILED")


if __name__ == "__main__":
    unittest.main()
