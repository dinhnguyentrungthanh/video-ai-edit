"""Locks of the hardware measurement (docs/HARDWARE_PROFILE.md 6.3 steps 2 and 4, section 9 "E2, đo").

Every mutex here is ``Local\\BiliFlowTest-<uuid>``, passed as a function parameter (I10); the real
``Local\\BiliFlowGpuInference`` and ``Local\\BiliFlowFinalRender`` are never touched. A Win32 mutex
is recursive for the thread that owns it, so "someone else holds it" is always a helper thread.
Lock files live in temporary roots under the worktree's temp folder.
"""
from __future__ import annotations

import contextlib
import msvcrt
import os
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import hardware_locks as hl
from biliflow import recycle_bin

TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
INFINITE = 0xFFFFFFFF
SHORT = 0.2


def unique_name() -> str:
    return f"Local\\BiliFlowTest-{uuid.uuid4()}"


def unique_names() -> hl.LockNames:
    return hl.LockNames(own=unique_name(), gpu=unique_name(), render=unique_name())


class Holder:
    """Holds a named mutex on its own thread until the ``with`` block ends.

    ``abandon=True``: the thread ends while owning it and its handle stays open, so the next waiter
    gets WAIT_ABANDONED; ``close_abandoned()`` closes that handle afterwards.
    """

    def __init__(self, name: str, *, abandon: bool = False):
        self.name = name
        self.abandon = abandon
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.handle = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        kernel32 = hl._kernel32()
        handle = kernel32.CreateMutexW(None, False, self.name)
        kernel32.WaitForSingleObject(handle, INFINITE)
        self.handle = handle
        self.ready.set()
        if self.abandon:
            return
        self.stop.wait(30)
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)

    def __enter__(self) -> "Holder":
        self.thread.start()
        if not self.ready.wait(5):
            raise AssertionError("helper thread did not take the mutex")
        if self.abandon:
            self.thread.join(5)
        return self

    def __exit__(self, *exc) -> None:
        self.stop.set()
        self.thread.join(5)
        if self.abandon:
            self.close_abandoned()

    def close_abandoned(self) -> None:
        if self.handle is not None:
            hl._kernel32().CloseHandle(self.handle)
            self.handle = None


def free_elsewhere(name: str) -> bool:
    """True when another thread can take ``name`` at once (and gives it back)."""
    result: list[int] = []

    def probe() -> None:
        kernel32 = hl._kernel32()
        handle = kernel32.CreateMutexW(None, False, name)
        waited = kernel32.WaitForSingleObject(handle, 0)
        if waited in (hl.WAIT_OBJECT_0, hl.WAIT_ABANDONED):
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)
        result.append(waited)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(5)
    return bool(result) and result[0] in (hl.WAIT_OBJECT_0, hl.WAIT_ABANDONED)


class FileHolder:
    """Holds byte 0 of a lock file through a second handle, as an export would."""

    def __init__(self, path: Path):
        self.path = path
        self.descriptor: int | None = None

    def __enter__(self) -> "FileHolder":
        self.descriptor = os.open(self.path, os.O_RDWR | os.O_BINARY)
        os.lseek(self.descriptor, 0, os.SEEK_SET)
        msvcrt.locking(self.descriptor, msvcrt.LK_NBLCK, 1)
        return self

    def __exit__(self, *exc) -> None:
        os.lseek(self.descriptor, 0, os.SEEK_SET)
        msvcrt.locking(self.descriptor, msvcrt.LK_UNLCK, 1)
        os.close(self.descriptor)


def lock_file_free(path: Path) -> bool:
    descriptor = os.open(path, os.O_RDWR | os.O_BINARY)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return True
    finally:
        os.close(descriptor)


class TempRoots(unittest.TestCase):
    def setUp(self) -> None:
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def root(self, name: str, *, lock_file: bool = True) -> Path:
        root = self.base / name
        (root / "state").mkdir(parents=True)
        if lock_file:
            (root / "state" / "final-render.lock").write_bytes(b"0")
        return root


@unittest.skipUnless(sys.platform == "win32", "Win32 mutexes")
class NamedMutexTests(unittest.TestCase):
    def test_a_free_mutex_is_taken_and_given_back(self):
        # Arrange
        mutex = hl.NamedMutex(unique_name())
        # Act
        taken = mutex.try_acquire(0)
        held_elsewhere = not free_elsewhere(mutex.name)
        mutex.release()
        # Assert
        self.assertTrue(taken)
        self.assertFalse(mutex.abandoned)
        self.assertTrue(held_elsewhere)
        self.assertTrue(free_elsewhere(mutex.name))

    def test_a_mutex_held_by_another_thread_is_busy_after_the_timeout(self):
        name = unique_name()
        with Holder(name):
            started = time.monotonic()
            taken = hl.NamedMutex(name).try_acquire(SHORT)
            elapsed = time.monotonic() - started
        self.assertFalse(taken)
        self.assertLess(elapsed, 2.0)

    def test_an_abandoned_mutex_counts_as_taken_and_is_recorded(self):
        name = unique_name()
        with Holder(name, abandon=True):
            mutex = hl.NamedMutex(name)
            taken = mutex.try_acquire(2.0)  # the OS thread ends a moment after join()
            abandoned = mutex.abandoned
            mutex.release()
        self.assertTrue(taken)
        self.assertTrue(abandoned)

    def test_release_from_another_thread_is_refused_and_the_owner_keeps_it(self):
        mutex = hl.NamedMutex(unique_name())
        mutex.try_acquire(0)
        errors: list[BaseException] = []

        def release_elsewhere() -> None:
            try:
                mutex.release()
            except RuntimeError as error:
                errors.append(error)

        thread = threading.Thread(target=release_elsewhere)
        thread.start()
        thread.join(5)
        still_held = mutex.held and not free_elsewhere(mutex.name)
        mutex.release()
        self.assertEqual(len(errors), 1)
        self.assertTrue(still_held)
        self.assertTrue(free_elsewhere(mutex.name))

    def test_taking_a_lock_off_the_main_thread_is_refused(self):
        errors: list[BaseException] = []

        def take() -> None:
            try:
                hl.NamedMutex(unique_name()).try_acquire(0)
            except RuntimeError as error:
                errors.append(error)

        thread = threading.Thread(target=take)
        thread.start()
        thread.join(5)
        self.assertEqual(len(errors), 1)

    def test_lock_names_must_be_local_and_distinct(self):
        name = unique_name()
        cases = (("Global\\x", unique_name(), unique_name()), (name, name, unique_name()),
                 ("", unique_name(), unique_name()))
        for names in cases:
            with self.subTest(names=names), self.assertRaises(ValueError):
                hl.LockNames(*names)
        real = hl.LockNames()
        self.assertEqual((real.own, real.gpu, real.render), ("Local\\BiliFlowHardwareProfile",
                                                             "Local\\BiliFlowGpuInference",
                                                             "Local\\BiliFlowFinalRender"))


@unittest.skipUnless(sys.platform == "win32", "msvcrt byte-range locks")
class FileLockTests(TempRoots):
    def test_a_missing_lock_file_is_skipped_and_never_created(self):
        root = self.root("root", lock_file=False)
        no_state = self.base / "no-state"
        no_state.mkdir()
        for path in (root / "state" / "final-render.lock", no_state / "state" / "final-render.lock"):
            with self.subTest(path=path.parent.parent.name):
                self.assertIsNone(hl.lock_existing_file(path, timeout_seconds=SHORT))
                self.assertFalse(path.exists())
        self.assertFalse((no_state / "state").exists())

    def test_a_held_lock_file_is_busy_after_the_timeout_and_is_not_written(self):
        path = self.root("root") / "state" / "final-render.lock"
        before = path.stat().st_mtime_ns
        with FileHolder(path):
            with self.assertRaises(hl.LockBusy):
                hl.lock_existing_file(path, timeout_seconds=SHORT)
        self.assertEqual(path.read_bytes(), b"0")
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_locking_and_unlocking_never_writes_the_file(self):
        path = self.root("root") / "state" / "final-render.lock"
        path.write_bytes(b"")  # an empty file stays empty: nothing is ever written
        before = path.stat().st_mtime_ns
        descriptor = hl.lock_existing_file(path, timeout_seconds=SHORT)
        held = not lock_file_free(path)
        hl.unlock_file(descriptor)
        self.assertTrue(held)
        self.assertTrue(lock_file_free(path))
        self.assertEqual(path.read_bytes(), b"")
        self.assertEqual(path.stat().st_mtime_ns, before)


@unittest.skipUnless(sys.platform == "win32", "Win32 mutexes")
class ExportLockTests(TempRoots):
    def test_every_lock_is_taken_in_the_global_order_and_missing_files_are_skipped(self):
        names = unique_names()
        first, second, third = self.root("a"), self.root("b"), self.root("c", lock_file=False)
        with contextlib.ExitStack() as stack:
            held = hl.acquire_export_locks(stack, names, [first, second, third], timeout_seconds=SHORT)
            busy_inside = (not free_elsewhere(names.gpu), not free_elsewhere(names.render),
                           not lock_file_free(first / "state" / "final-render.lock"))
        self.assertEqual(held.taken, (names.gpu, names.render, str(first / "state" / "final-render.lock"),
                                      str(second / "state" / "final-render.lock")))
        self.assertEqual(held.skipped, (str(third / "state" / "final-render.lock"),))
        self.assertEqual(busy_inside, (True, True, True))
        self.assertTrue(free_elsewhere(names.gpu) and free_elsewhere(names.render))
        self.assertTrue(lock_file_free(first / "state" / "final-render.lock"))
        self.assertFalse((third / "state" / "final-render.lock").exists())

    def test_a_partial_acquisition_is_released_in_reverse_order(self):
        names = unique_names()
        first, second = self.root("a"), self.root("b")
        order: list[str] = []
        real_release, real_unlock = hl.NamedMutex.release, hl.unlock_file

        def release(mutex):
            order.append(mutex.name)
            real_release(mutex)

        def unlock(descriptor):
            order.append("file")
            real_unlock(descriptor)

        with mock.patch.object(hl.NamedMutex, "release", release), mock.patch.object(hl, "unlock_file", unlock):
            with FileHolder(second / "state" / "final-render.lock"):
                with self.assertRaises(hl.LockBusy), contextlib.ExitStack() as stack:
                    hl.acquire_export_locks(stack, names, [first, second], timeout_seconds=SHORT)
        self.assertEqual(order, ["file", names.render, names.gpu])
        self.assertTrue(free_elsewhere(names.gpu) and free_elsewhere(names.render))
        self.assertTrue(lock_file_free(first / "state" / "final-render.lock"))

    def test_a_busy_gpu_mutex_takes_nothing_else(self):
        names = unique_names()
        root = self.root("a")
        with Holder(names.gpu):
            with self.assertRaises(hl.LockBusy), contextlib.ExitStack() as stack:
                hl.acquire_export_locks(stack, names, [root], timeout_seconds=SHORT)
            render_free = free_elsewhere(names.render)
        self.assertTrue(render_free)
        self.assertTrue(lock_file_free(root / "state" / "final-render.lock"))

    def test_an_abandoned_render_mutex_is_taken_and_reported(self):
        names = unique_names()
        with Holder(names.render, abandon=True), contextlib.ExitStack() as stack:
            held = hl.acquire_export_locks(stack, names, [], timeout_seconds=2.0)
        self.assertEqual(held.abandoned, (names.render,))
        self.assertIn(names.render, held.taken)

    def test_the_same_root_twice_locks_its_file_once(self):
        names = unique_names()
        root = self.root("a")
        other_spelling = Path(str(root).upper()) / "state" / ".."
        with contextlib.ExitStack() as stack:
            held = hl.acquire_export_locks(stack, names, [root, other_spelling], timeout_seconds=SHORT)
        self.assertEqual(len(held.taken), 3)


if __name__ == "__main__":
    unittest.main()
