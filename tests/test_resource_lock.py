import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.resource_lock import project_resource_lock


class ResourceLockTests(unittest.TestCase):
    def test_second_worker_waits_for_same_project_slot(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state").mkdir()
            order = []

            def worker():
                with project_resource_lock(root, "render", poll_seconds=0.01):
                    order.append("second")

            with project_resource_lock(root, "render", poll_seconds=0.01):
                thread = threading.Thread(target=worker)
                thread.start()
                time.sleep(0.05)
                self.assertEqual(order, [])
            thread.join(timeout=1)
            self.assertEqual(order, ["second"])


if __name__ == "__main__":
    unittest.main()
