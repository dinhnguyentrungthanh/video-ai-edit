"""Bounded host-wall profiling; never synchronizes CUDA or changes model inputs."""
from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter


class ScanPerformance:
    """Single-thread collector with exclusive timings for nested operations.

    Pipe reads measure consumer wait/copy, not total FFmpeg decode time. Model
    spans should include existing CPU materialization of results; asynchronous
    GPU work is not separately synchronized or reported as GPU kernel time.
    """

    def __init__(self, clock=perf_counter):
        self._clock = clock
        self._started = clock()
        self._phases = {}
        self._stack = []

    @contextmanager
    def measure(self, name):
        entry = [self._clock(), 0.0]
        self._stack.append(entry)
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            elapsed = max(0.0, self._clock() - entry[0])
            self._stack.pop()
            if self._stack:
                self._stack[-1][1] += elapsed
            phase = self._phases.setdefault(name, {
                "wall_seconds": 0.0, "calls": 0, "failed_calls": 0,
            })
            phase["wall_seconds"] += max(0.0, elapsed - entry[1])
            phase["calls"] += 1
            phase["failed_calls"] += int(failed)

    def call(self, name, function, *args, **kwargs):
        with self.measure(name):
            return function(*args, **kwargs)

    def iterate(self, name, values):
        iterator = iter(values)
        while True:
            # Normal exhaustion is not a failed call. Time next(), excluding
            # all work performed by the consumer between yields.
            with self.measure(name):
                try:
                    value = next(iterator)
                except StopIteration:
                    return
            yield value

    def snapshot(self):
        if self._stack:
            raise RuntimeError("Cannot snapshot an active performance span")
        total = max(0.0, self._clock() - self._started)
        measured = sum(p["wall_seconds"] for p in self._phases.values())
        return {
            "schema_version": 1,
            "clock": "host_wall_exclusive",
            "cuda_synchronized": False,
            "scope": "collector creation to snapshot; later work and serialization excluded",
            "total_wall_seconds": round(total, 6),
            "unattributed_wall_seconds": round(max(0.0, total - measured), 6),
            "phases": {
                name: {**phase, "wall_seconds": round(phase["wall_seconds"], 6)}
                for name, phase in sorted(self._phases.items())
            },
        }
