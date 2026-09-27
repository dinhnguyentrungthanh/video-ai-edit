import tempfile
import unittest
from pathlib import Path

from biliflow.storage import storage_status


class StorageTests(unittest.TestCase):
    def test_storage_status_has_valid_percent(self):
        with tempfile.TemporaryDirectory() as directory:
            status = storage_status(Path(directory))
        self.assertGreater(status.total_gb, 0)
        self.assertGreaterEqual(status.free_percent, 0)
        self.assertLessEqual(status.free_percent, 100)


if __name__ == "__main__":
    unittest.main()

