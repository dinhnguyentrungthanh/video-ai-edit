"""Locks of the hardware measurement (docs/HARDWARE_PROFILE.md section 6.3 steps 2 and 4).

Global order: the measurement's own mutex, then ``Local\\BiliFlowGpuInference``, then
``Local\\BiliFlowFinalRender``, then each guard root's existing ``state\\final-render.lock``. Every
lock is tried for a short time only; a lock that cannot be taken releases what was taken, in
reverse order. A Win32 mutex belongs to the thread that took it, so taking and releasing happen on
the main thread only; nothing here releases from a timer, a signal handler or ``atexit``.

The names are module constants. Only a function parameter replaces them (tests pass
``Local\\BiliFlowTest-<uuid>``); no command-line option or environment variable does (I10).
Imported only by hardware_measure, never by a scan stage. Standard library only; ctypes and
msvcrt are loaded inside the functions that use them.
"""
from __future__ import annotations

import contextlib
import functools
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

OWN_MUTEX = "Local\\BiliFlowHardwareProfile"
GPU_MUTEX = "Local\\BiliFlowGpuInference"
RENDER_MUTEX = "Local\\BiliFlowFinalRender"
RENDER_LOCK_PARTS = ("state", "final-render.lock")  # resource_lock.project_resource_lock(root, "final-render")
LOCK_TRY_SECONDS = 2.0
FILE_LOCK_POLL_SECONDS = 0.05
MAX_MUTEX_NAME = 200

WAIT_OBJECT_0 = 0x0
WAIT_ABANDONED = 0x80
WAIT_TIMEOUT = 0x102
WAIT_FAILED = 0xFFFFFFFF


class LockBusy(Exception):
    """A lock could not be taken in time (or at all): the measurement exits 75."""

    def __init__(self, name: str, detail: str):
        super().__init__(f"{name}: {detail}")
        self.name = name


@dataclass(frozen=True)
class LockNames:
    """The three mutex names; the defaults are the real ones (I10: only tests pass others)."""
    own: str = OWN_MUTEX
    gpu: str = GPU_MUTEX
    render: str = RENDER_MUTEX

    def __post_init__(self) -> None:
        names = (self.own, self.gpu, self.render)
        for name in names:
            if type(name) is not str or not name.startswith("Local\\") or not 6 < len(name) <= MAX_MUTEX_NAME:
                raise ValueError("tên mutex phải có dạng Local\\<tên>")
        if len(set(names)) != len(names):
            raise ValueError("ba mutex phải khác tên nhau")


@dataclass(frozen=True)
class HeldLocks:
    """What step 4 took: lock names in order, the abandoned mutexes and the lock files skipped (missing)."""
    taken: tuple[str, ...]
    abandoned: tuple[str, ...]
    skipped: tuple[str, ...]


@functools.lru_cache(maxsize=1)
def _kernel32() -> Any:
    """A private kernel32 with explicit types: never the shared ctypes.windll (others set its argtypes)."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, restype, argtypes in (
        ("CreateMutexW", wintypes.HANDLE, [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]),
        ("WaitForSingleObject", wintypes.DWORD, [wintypes.HANDLE, wintypes.DWORD]),
        ("ReleaseMutex", wintypes.BOOL, [wintypes.HANDLE]),
        ("CloseHandle", wintypes.BOOL, [wintypes.HANDLE]),
    ):
        function = getattr(kernel32, name)
        function.restype = restype
        function.argtypes = argtypes
    return kernel32


def _last_error() -> int:
    import ctypes
    return ctypes.get_last_error()


def _require_main_thread() -> None:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Khóa của phép đo chỉ được lấy trên luồng chính.")


class NamedMutex:
    """One named Win32 mutex: taken on the main thread, released by the same thread only."""

    def __init__(self, name: str):
        self.name = name
        self.abandoned = False
        self._handle: int | None = None
        self._owner: int | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def try_acquire(self, timeout_seconds: float) -> bool:
        """True when taken (WAIT_ABANDONED counts and sets ``abandoned``), False after the timeout."""
        _require_main_thread()
        if self._handle is not None:
            raise RuntimeError(f"{self.name}: đã giữ khóa này")
        kernel32 = _kernel32()
        handle = kernel32.CreateMutexW(None, False, self.name)  # not inheritable, not owned yet
        if not handle:
            raise LockBusy(self.name, f"không mở được mutex (lỗi Windows {_last_error()})")
        result = kernel32.WaitForSingleObject(handle, max(0, int(round(timeout_seconds * 1000))))
        if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            self._handle, self._owner = handle, threading.get_ident()
            self.abandoned = result == WAIT_ABANDONED
            return True
        error = _last_error()
        kernel32.CloseHandle(handle)
        if result == WAIT_TIMEOUT:
            return False
        raise LockBusy(self.name, f"chờ mutex lỗi (mã {result}, lỗi Windows {error})")

    def release(self) -> None:
        """ReleaseMutex, then CloseHandle; refused from any thread but the owner's."""
        if self._handle is None:
            return
        if threading.get_ident() != self._owner:
            raise RuntimeError(f"{self.name}: chỉ luồng đã lấy khóa mới được nhả")
        kernel32, handle = _kernel32(), self._handle
        self._handle = self._owner = None
        try:
            if not kernel32.ReleaseMutex(handle):
                raise OSError(_last_error(), f"ReleaseMutex lỗi cho {self.name}")
        finally:
            kernel32.CloseHandle(handle)


# --- the 1-byte export lock on <guard root>\state\final-render.lock -------------------------------

def lock_existing_file(path: Path, *, timeout_seconds: float = LOCK_TRY_SECONDS,
                       poll_seconds: float = FILE_LOCK_POLL_SECONDS,
                       clock: Callable[[], float] = time.monotonic,
                       sleep: Callable[[float], None] = time.sleep) -> int | None:
    """The descriptor holding byte 0 of an existing file; None when the file is missing.

    Opened O_RDWR without O_CREAT and never written, so a missing file stays missing
    (resource_lock.project_resource_lock would create it and write b"0" into a real state folder).
    Any other open error, or the byte still held after the timeout, raises LockBusy.
    """
    import msvcrt
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LockBusy(str(path), f"không mở được file khóa ({type(error).__name__})") from None
    deadline = clock() + timeout_seconds
    try:
        while True:
            os.lseek(descriptor, 0, os.SEEK_SET)  # msvcrt.locking locks from the current position
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                return descriptor
            except OSError:
                if clock() >= deadline:
                    raise LockBusy(str(path), "file khóa xuất đang bị giữ") from None
            sleep(poll_seconds)
    except BaseException:
        os.close(descriptor)
        raise


def unlock_file(descriptor: int) -> None:
    import msvcrt
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(descriptor)


# --- step 4 --------------------------------------------------------------------------------------

def unique_roots(roots: Sequence[Path]) -> tuple[Path, ...]:
    """Roots without duplicates (normcase of the resolved path), first occurrence first.

    Two handles on the same lock file in one process conflict, so a duplicate root would make
    every measurement busy.
    """
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        resolved = Path(root).resolve()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return tuple(unique)


def acquire_export_locks(stack: contextlib.ExitStack, names: LockNames, guard_roots: Sequence[Path], *,
                         timeout_seconds: float = LOCK_TRY_SECONDS) -> HeldLocks:
    """GPU mutex, FinalRender mutex, then each guard root's lock file, every release pushed on ``stack``.

    The caller already holds its own mutex. On LockBusy the caller's ``stack`` unwinds what was
    taken, in reverse order.
    """
    _require_main_thread()
    taken: list[str] = []
    abandoned: list[str] = []
    skipped: list[str] = []
    for name in (names.gpu, names.render):
        mutex = NamedMutex(name)
        if not mutex.try_acquire(timeout_seconds):
            raise LockBusy(name, "mutex đang bị giữ")
        stack.callback(mutex.release)
        taken.append(name)
        if mutex.abandoned:
            abandoned.append(name)
    for root in unique_roots(guard_roots):
        path = root.joinpath(*RENDER_LOCK_PARTS)
        descriptor = lock_existing_file(path, timeout_seconds=timeout_seconds)
        if descriptor is None:
            skipped.append(str(path))
            continue
        stack.callback(unlock_file, descriptor)
        taken.append(str(path))
    return HeldLocks(tuple(taken), tuple(abandoned), tuple(skipped))
