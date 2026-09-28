import unittest

from biliflow.performance import ScanPerformance


class PerformanceTests(unittest.TestCase):
    def test_nested_work_is_not_double_counted(self):
        now = [0.0]
        p = ScanPerformance(lambda: now[0])
        with p.measure("outer"):
            now[0] = 2
            with p.measure("inner"):
                now[0] = 5
            now[0] = 7
        now[0] = 9
        result = p.snapshot()
        self.assertEqual(result["phases"]["outer"]["wall_seconds"], 4)
        self.assertEqual(result["phases"]["inner"]["wall_seconds"], 3)
        self.assertEqual(result["unattributed_wall_seconds"], 2)

    def test_returns_same_object_and_propagates_failure(self):
        p = ScanPerformance()
        value = object()
        self.assertIs(p.call("model", lambda: value), value)
        with self.assertRaisesRegex(ValueError, "original failure"):
            with p.measure("model"):
                raise ValueError("original failure")
        self.assertEqual(p.snapshot()["phases"]["model"]["failed_calls"], 1)
        self.assertEqual(p.snapshot()["phases"]["model"]["calls"], 2)

    def test_iteration_excludes_consumer_and_preserves_order(self):
        now = [0.0]
        p = ScanPerformance(lambda: now[0])
        def frames():
            for i in range(3):
                now[0] += 1
                yield i
        seen = []
        for frame in p.iterate("frame_wait", frames()):
            now[0] += 10
            seen.append(frame)
        result = p.snapshot()
        self.assertEqual(seen, [0, 1, 2])
        self.assertEqual(result["phases"]["frame_wait"]["wall_seconds"], 3)
        self.assertEqual(result["phases"]["frame_wait"]["failed_calls"], 0)
        self.assertEqual(result["unattributed_wall_seconds"], 30)
