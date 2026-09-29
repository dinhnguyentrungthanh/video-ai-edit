import hashlib
import tempfile
import unittest
from pathlib import Path

from biliflow.source_hash import BackgroundSha256
from biliflow.textscan import _sha256_file


class BackgroundSha256Tests(unittest.TestCase):
    def test_matches_sequential_hash_across_chunk_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.bin"
            path.write_bytes(bytes(range(256)) * 9000 + b"tail")  # > 2 MiB, uneven tail
            self.assertEqual(BackgroundSha256(path).result(), hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(BackgroundSha256(path).result(), _sha256_file(path))

    def test_errors_and_cancellation_surface_on_result(self):
        with self.assertRaises(FileNotFoundError):
            BackgroundSha256(Path("does-not-exist.bin")).result()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "big.bin"
            path.write_bytes(b"\0" * (64 * 1024 * 1024))
            job = BackgroundSha256(path)
            job.cancel()
            try:
                job.result()
            except RuntimeError as error:
                self.assertIn("cancelled", str(error))


if __name__ == "__main__":
    unittest.main()
