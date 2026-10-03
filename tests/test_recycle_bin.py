"""recycle_bin: checks before the shell call, the guarded shell call and $I/$R verification.

Every unit test runs against a temporary install root with ``_shell_delete``
replaced by a fake that moves the file into a fake ``$Recycle.Bin``. The module
setup also replaces ``_shell_delete`` with a function that fails the test, so
nothing here can reach the real Recycle Bin. The only real call is
``RealRecycleBinTest``: it is skipped unless the lead sets
``BILIFLOW_TEST_RECYCLE_BIN=1`` for its single run, and it only recycles a 1 KB
file it creates under ``TEST_RECYCLE_ROOT``.
"""

import ctypes
import json
import os
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from biliflow import recycle_bin
from biliflow.recycle_bin import (
    ABORTED_MESSAGE,
    ALLOWED_ROOT_MESSAGE,
    CAPACITY_MARGIN_BYTES,
    IN_PROGRESS_MESSAGE,
    LINK_MESSAGE,
    NO_BIN_SETTINGS_MESSAGE,
    NOT_MOVED_MESSAGE,
    NOT_WINDOWS_MESSAGE,
    NUKE_MESSAGE,
    OUTSIDE_MESSAGE,
    PATH_MESSAGE,
    POLICY_MESSAGE,
    SHARING_MESSAGE,
    SIZE_MESSAGE,
    TIMEOUT_MESSAGE,
    BinInfo,
    RecycleFailed,
    RecycleRefused,
    RecycleResult,
    RecycleTimeout,
)


REAL_BIN_ENABLED = os.environ.get("BILIFLOW_TEST_RECYCLE_BIN") == "1"
# Captured before any test patches them.
ORIGINAL_INSTALL_ROOT = recycle_bin.INSTALL_ROOT
ORIGINAL_TEST_RECYCLE_ROOT = recycle_bin.TEST_RECYCLE_ROOT
ORIGINAL_TEST_BIN_ENABLED = recycle_bin._test_bin_enabled
GUID = "{2fd9f59c-d156-40e6-b5c9-93b787892ee9}"
MAX_MIB = 49741
MAX_BYTES = 52_157_218_816
USED_BYTES = 11_823_971_925
ITEMS = 7
GIB = 1073741824

_SHELL_PATCH = None


def _refuse_real_shell(*args, **kwargs):
    raise AssertionError("real Recycle Bin call in a test")


def setUpModule():
    global _SHELL_PATCH
    if not REAL_BIN_ENABLED:
        _SHELL_PATCH = patch.object(recycle_bin, "_shell_delete", new=_refuse_real_shell)
        _SHELL_PATCH.start()


def tearDownModule():
    global _SHELL_PATCH
    if _SHELL_PATCH is not None:
        _SHELL_PATCH.stop()
        _SHELL_PATCH = None


def info_record_v2(path, size, filetime=133_000_000_000_000_000):
    text = str(path) + "\0"
    return struct.pack("<qqqi", 2, size, filetime, len(text)) + text.encode("utf-16-le")


def info_record_v1(path, size, filetime=133_000_000_000_000_000):
    encoded = str(path).encode("utf-16-le").ljust(520, b"\0")
    return struct.pack("<qqq", 1, size, filetime) + encoded


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FakeShell:
    """Stands in for SHFileOperationW: optionally moves the file into a fake bin."""

    def __init__(self, sid_dir, *, rc=0, aborted=False, move=True, record=True, twin=True,
                 block=None, error=None):
        self.sid_dir = Path(sid_dir)
        self.rc, self.aborted, self.move = rc, aborted, move
        self.record, self.twin, self.block, self.error = record, twin, block, error
        self.calls = []
        self.records = []

    def __call__(self, path):
        current = threading.current_thread()
        self.calls.append({"path": path, "thread": current.name, "daemon": current.daemon})
        if self.block is not None:
            self.block.wait(10)
        if self.error is not None:
            raise self.error
        if self.move:
            size = os.path.getsize(path)
            token = uuid.uuid4().hex[:6].upper()
            extension = os.path.splitext(path)[1]
            moved = self.sid_dir / f"$R{token}{extension}"
            os.replace(path, moved)
            if self.record:
                info = self.sid_dir / f"$I{token}{extension}"
                info.write_bytes(info_record_v2(path, size))
                self.records.append(str(info))
            if not self.twin:
                moved.unlink()
        return self.rc, self.aborted


class RecycleFixture(unittest.TestCase):
    """A temporary install root and fake volume; every Windows helper is patched."""

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.install = self.base / "install"
        self.input = self.install / "input"
        self.input.mkdir(parents=True)
        self.volume = self.base / "volume"
        self.sid_dir = self.volume / "$Recycle.Bin" / "S-1-5-21-1000"
        self.sid_dir.mkdir(parents=True)
        self.shell = FakeShell(self.sid_dir)
        self.co_initialize = Mock(return_value=True)
        self.co_uninitialize = Mock()
        # The verification retry pauses are recorded, never slept.
        self.sleeps = []
        self.patch_values({
            "_sleep": self.sleeps.append,
            "INSTALL_ROOT": self.install,
            "TEST_RECYCLE_ROOT": self.install / "temp" / "recycle-bin-test",
            "_test_bin_enabled": lambda environ=None: False,
            "_volume_root": lambda path: str(self.volume) + "\\",
            "_drive_type": lambda root: recycle_bin.DRIVE_FIXED,
            "_volume_guid": lambda root: GUID,
            "_bitbucket_settings": lambda guid: (MAX_MIB, 0, None),
            "_policy_values": lambda: {"NoRecycleFiles": None, "RecycleBinSize": None, "unreadable": False},
            "_query_bin": lambda root: (USED_BYTES, ITEMS),
            "_co_initialize": self.co_initialize,
            "_co_uninitialize": self.co_uninitialize,
            "_shell_delete": lambda path: self.shell(path),
        })
        self.addCleanup(self.assert_no_operation_left)

    def patch_values(self, values):
        for name, value in values.items():
            patcher = patch.object(recycle_bin, name, new=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_no_operation_left(self):
        if self.shell.block is not None:
            self.shell.block.set()
        self.assertTrue(
            wait_until(lambda: not recycle_bin.operations_in_progress()),
            "a recycle thread is still registered after the test",
        )

    def video(self, name="Tập 12.mp4", size=1024, folder=None):
        path = (folder or self.input) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(os.urandom(size))
        return path

    def send(self, path, **kwargs):
        kwargs.setdefault("allowed_root", self.input)
        kwargs.setdefault("expected_size", path.stat().st_size)
        return recycle_bin.send_to_recycle_bin(path, **kwargs)


class ConstantsAndStructuresTests(unittest.TestCase):
    def test_flags_and_constants(self):
        self.assertEqual(recycle_bin.RECYCLE_FLAGS, 0x4454)
        self.assertEqual(recycle_bin.FO_DELETE, 3)
        self.assertEqual(recycle_bin.MAX_PATH_CHARS, 259)
        self.assertEqual(recycle_bin.CAPACITY_MARGIN_BYTES, 64 * 1048576)
        self.assertEqual(recycle_bin.DEFAULT_TIMEOUT_SECONDS, 60.0)
        self.assertEqual(ORIGINAL_INSTALL_ROOT, Path(recycle_bin.__file__).resolve().parents[2])
        self.assertEqual(ORIGINAL_TEST_RECYCLE_ROOT, ORIGINAL_INSTALL_ROOT / "temp" / "recycle-bin-test")

    @unittest.skipUnless(ctypes.sizeof(ctypes.c_void_p) == 8, "64-bit layout")
    def test_structure_sizes_on_x64(self):
        shfileop, shqueryrbinfo = recycle_bin._structures()
        self.assertEqual(ctypes.sizeof(shfileop), 56)
        self.assertEqual(ctypes.sizeof(shqueryrbinfo), 24)

    def test_path_buffer_ends_with_two_nuls_and_the_operation_points_to_it(self):
        path = "E:\\DungChung\\BiliFlow\\input\\Tập 12.mp4"
        buffer = recycle_bin._path_buffer(path)
        self.assertEqual(len(buffer), len(path) + 2)
        self.assertEqual(buffer[len(path):], "\0\0")
        operation, kept = recycle_bin._file_operation(path)
        shfileop, _ = recycle_bin._structures()
        address = ctypes.c_void_p.from_buffer(operation, shfileop.pFrom.offset).value
        self.assertEqual(address, ctypes.addressof(kept))
        self.assertEqual(ctypes.wstring_at(address, len(path) + 2), path + "\0\0")
        self.assertEqual(operation.wFunc, 3)
        self.assertEqual(operation.fFlags, 0x4454)
        self.assertIsNone(operation.pTo)
        self.assertIsNone(operation.hwnd)
        self.assertIsNone(operation.lpszProgressTitle)
        self.assertEqual(operation.fAnyOperationsAborted, 0)

    def test_query_bin_never_takes_an_empty_root(self):
        # NULL or '' would add up every drive; refused before any shell call.
        for value in ("", None):
            with self.assertRaises(ValueError):
                recycle_bin._query_bin(value)

    def test_import_runs_nothing(self):
        code = (
            "import sys, threading\n"
            "import biliflow.recycle_bin as rb\n"
            "print('ctypes' in sys.modules)\n"
            "print(any(t.name == 'biliflow-recycle-bin' for t in threading.enumerate()))\n"
            "print(sorted(rb.operations_in_progress()))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True,
        )
        self.assertEqual(result.stdout.split(), ["False", "False", "[]"])

    @unittest.skipIf(REAL_BIN_ENABLED, "the module patch is off for the real-bin run")
    def test_module_setup_blocks_the_real_shell_call(self):
        with self.assertRaisesRegex(AssertionError, "real Recycle Bin call in a test"):
            recycle_bin._shell_delete("E:\\nothing-to-recycle.mp4")


class AllowedRootTests(RecycleFixture):
    def test_test_bin_switch_reads_only_the_exact_value(self):
        # The unpatched helper, checked against plain dicts: os.environ is never changed.
        self.assertTrue(ORIGINAL_TEST_BIN_ENABLED({"BILIFLOW_TEST_RECYCLE_BIN": "1"}))
        self.assertFalse(ORIGINAL_TEST_BIN_ENABLED({"BILIFLOW_TEST_RECYCLE_BIN": "0"}))
        self.assertFalse(ORIGINAL_TEST_BIN_ENABLED({"BILIFLOW_TEST_RECYCLE_BIN": "true"}))
        self.assertFalse(ORIGINAL_TEST_BIN_ENABLED({}))

    def test_allowed_roots_are_input_and_the_test_root_only_when_enabled(self):
        self.assertEqual(recycle_bin._allowed_roots(), [self.input])
        with patch.object(recycle_bin, "_test_bin_enabled", return_value=True):
            self.assertEqual(
                recycle_bin._allowed_roots(),
                [self.input, self.install / "temp" / "recycle-bin-test"],
            )

    def test_any_other_allowed_root_is_refused_before_the_file_is_touched(self):
        other = self.base / "elsewhere"
        target = self.video(folder=other)
        for enabled in (False, True):
            with self.subTest(test_bin_enabled=enabled), patch.object(
                recycle_bin, "_test_bin_enabled", return_value=enabled,
            ):
                with self.assertRaises(RecycleRefused) as caught:
                    self.send(target, allowed_root=other)
                self.assertEqual(str(caught.exception), ALLOWED_ROOT_MESSAGE)
        # The parent of input and the install root are not input either.
        for root in (self.install, self.base):
            with self.assertRaises(RecycleRefused) as caught:
                self.send(target, allowed_root=root)
            self.assertEqual(str(caught.exception), ALLOWED_ROOT_MESSAGE)
        self.assertTrue(target.is_file())
        self.assertEqual(self.shell.calls, [])

    def test_the_test_root_is_allowed_only_while_the_switch_is_on(self):
        test_root = self.install / "temp" / "recycle-bin-test"
        target = self.video("probe.txt", folder=test_root)
        with self.assertRaises(RecycleRefused) as caught:
            self.send(target, allowed_root=test_root)
        self.assertEqual(str(caught.exception), ALLOWED_ROOT_MESSAGE)
        self.assertEqual(self.shell.calls, [])
        with patch.object(recycle_bin, "_test_bin_enabled", return_value=True):
            result = self.send(target, allowed_root=test_root)
        self.assertFalse(target.exists())
        self.assertTrue(result.verified)


class PathRefusalTests(RecycleFixture):
    def test_a_plain_file_inside_input_is_accepted(self):
        target = self.video()
        self.assertIsNone(recycle_bin.path_refusal(target, allowed_root=self.input))
        nested = self.video("Mùa 2/Tập 1.mp4")
        self.assertIsNone(recycle_bin.path_refusal(nested, allowed_root=self.input))
        info = recycle_bin.validate_target(target, allowed_root=self.input, expected_size=1024)
        self.assertEqual(info.st_size, 1024)

    def test_relative_outside_and_the_root_itself_are_outside(self):
        outside = self.video(folder=self.base / "reports")
        cases = {
            "relative": "input\\Tập 12.mp4",
            "empty": "",
            "outside": outside,
            "the root itself": self.input,
            "sibling with a common prefix": self.base / "install" / "input-old" / "a.mp4",
        }
        for label, value in cases.items():
            with self.subTest(label):
                self.assertEqual(
                    recycle_bin.path_refusal(value, allowed_root=self.input), OUTSIDE_MESSAGE,
                )

    def test_wildcards_long_paths_and_device_prefixes_are_refused(self):
        target = self.video()
        prefix = str(self.input) + "\\"
        too_long = prefix + "a" * (260 - len(prefix) - 4) + ".mp4"
        self.assertEqual(len(too_long), 260)
        cases = {
            "star": prefix + "Tập*.mp4",
            "question mark": prefix + "Tập ?.mp4",
            "device prefix": "\\\\?\\" + str(target),
            "nul": prefix + "a\0b.mp4",
            "260 characters": too_long,
            # Security review (L3): a colon after the drive names an alternate data stream.
            "alternate data stream": str(target) + ":hidden.mp4",
            "colon in a folder": prefix + "a:b\\Tập 12.mp4",
        }
        for label, value in cases.items():
            with self.subTest(label):
                self.assertEqual(recycle_bin.path_refusal(value, allowed_root=self.input), PATH_MESSAGE)
        # 259 characters passes the length check (the file just does not exist).
        exact = prefix + "a" * (259 - len(prefix) - 4) + ".mp4"
        self.assertEqual(len(exact), 259)
        self.assertEqual(recycle_bin.path_refusal(exact, allowed_root=self.input), SIZE_MESSAGE)

    def test_a_directory_or_missing_file_is_not_a_recyclable_video(self):
        folder = self.input / "Tập 12.mp4"
        folder.mkdir()
        self.assertEqual(recycle_bin.path_refusal(folder, allowed_root=self.input), SIZE_MESSAGE)
        self.assertEqual(
            recycle_bin.path_refusal(self.input / "missing.mp4", allowed_root=self.input), SIZE_MESSAGE,
        )

    def test_a_link_or_junction_is_refused(self):
        target = self.video()
        real_realpath = os.path.realpath

        def realpath(path, *args, **kwargs):
            value = real_realpath(path, *args, **kwargs)
            if os.path.normcase(str(path)) == os.path.normcase(str(target)):
                return str(self.base / "elsewhere" / target.name)
            return value

        with patch("os.path.realpath", side_effect=realpath):
            self.assertEqual(recycle_bin.path_refusal(target, allowed_root=self.input), LINK_MESSAGE)

    def test_a_reparse_point_attribute_is_refused(self):
        target = self.video()
        real_lstat = os.lstat

        def lstat(path, *args, **kwargs):
            value = real_lstat(path, *args, **kwargs)
            if os.path.normcase(str(path)) == os.path.normcase(str(target)):
                return SimpleNamespace(
                    st_mode=value.st_mode, st_size=value.st_size,
                    st_file_attributes=getattr(value, "st_file_attributes", 0) | 0x400,
                )
            return value

        with patch("os.lstat", side_effect=lstat):
            self.assertEqual(recycle_bin.path_refusal(target, allowed_root=self.input), LINK_MESSAGE)

    def test_validate_target_checks_the_platform_the_path_and_the_size(self):
        target = self.video()
        with self.assertRaises(RecycleRefused) as caught:
            recycle_bin.validate_target(target, allowed_root=self.input, expected_size=1023)
        self.assertEqual(str(caught.exception), SIZE_MESSAGE)
        with self.assertRaises(RecycleRefused) as caught:
            recycle_bin.validate_target(self.base / "x.mp4", allowed_root=self.input, expected_size=1)
        self.assertEqual(str(caught.exception), OUTSIDE_MESSAGE)
        with patch.object(recycle_bin, "_is_windows", return_value=False):
            with self.assertRaises(RecycleRefused) as caught:
                recycle_bin.validate_target(target, allowed_root=self.input, expected_size=1024)
            self.assertEqual(str(caught.exception), NOT_WINDOWS_MESSAGE)
            with self.assertRaises(RecycleRefused) as caught:
                recycle_bin.volume_bin_info(target)
            self.assertEqual(str(caught.exception), NOT_WINDOWS_MESSAGE)


class VolumeBinInfoTests(RecycleFixture):
    def setUp(self):
        super().setUp()
        self.patch_values({"_volume_root": lambda path: "E:\\"})

    def refusal(self, **values):
        with patch.multiple(recycle_bin, **values):
            with self.assertRaises(RecycleRefused) as caught:
                recycle_bin.volume_bin_info(self.input / "Tập 12.mp4")
        return str(caught.exception)

    def test_reads_the_volume_settings_and_usage(self):
        info = recycle_bin.volume_bin_info(self.input / "Tập 12.mp4")
        self.assertEqual(info, BinInfo(
            volume="E:", root="E:\\", guid=GUID, max_bytes=MAX_BYTES, used_bytes=USED_BYTES, items=ITEMS,
        ))
        self.assertEqual(info.max_bytes, 49741 * 1048576)
        self.assertEqual(info.available_bytes, MAX_BYTES - USED_BYTES)
        self.assertEqual(
            BinInfo("E:", "E:\\", GUID, 10, 20, 1).available_bytes, 0,
        )

    def test_missing_nuke_values_count_as_zero(self):
        with patch.object(recycle_bin, "_bitbucket_settings", return_value=(MAX_MIB, None, None)):
            self.assertEqual(recycle_bin.volume_bin_info(self.input).max_bytes, MAX_BYTES)
        with patch.object(recycle_bin, "_policy_values", return_value={"NoRecycleFiles": 0}):
            self.assertEqual(recycle_bin.volume_bin_info(self.input).items, ITEMS)

    def test_each_unusable_bin_is_refused_with_its_reason(self):
        no_settings = NO_BIN_SETTINGS_MESSAGE.format(volume="E:")
        nuke = NUKE_MESSAGE.format(volume="E:")
        cases = [
            ("not a fixed disk", {"_drive_type": lambda root: 2},
             "Ổ E: không phải ổ cứng cố định; không dùng Thùng rác được."),
            ("no volume key", {"_bitbucket_settings": lambda guid: (None, None, None)}, no_settings),
            ("no guid", {"_volume_guid": Mock(side_effect=OSError("no guid"))}, no_settings),
            ("MaxCapacity 0", {"_bitbucket_settings": lambda guid: (0, 0, None)}, no_settings),
            ("volume NukeOnDelete", {"_bitbucket_settings": lambda guid: (MAX_MIB, 1, None)}, nuke),
            ("root NukeOnDelete", {"_bitbucket_settings": lambda guid: (MAX_MIB, 0, 1)}, nuke),
            ("NoRecycleFiles", {"_policy_values": lambda: {"NoRecycleFiles": 1}}, POLICY_MESSAGE),
            ("RecycleBinSize", {"_policy_values": lambda: {"RecycleBinSize": 10}}, POLICY_MESSAGE),
            ("unreadable policy", {"_policy_values": lambda: {"unreadable": True}}, POLICY_MESSAGE),
            ("query failed", {"_query_bin": Mock(side_effect=OSError("0x80004005"))},
             "Không đọc được dung lượng Thùng rác của ổ E:; không dọn được."),
        ]
        self.assertEqual(
            no_settings,
            "Chưa thấy cấu hình Thùng rác của ổ E: trong Windows; mở Thuộc tính Thùng rác một lần rồi thử lại.",
        )
        self.assertEqual(
            nuke,
            "Thùng rác của ổ E: đang đặt “Xóa file ngay, không chuyển vào Thùng rác”; không dọn được.",
        )
        for label, values, expected in cases:
            with self.subTest(label):
                self.assertEqual(self.refusal(**values), expected)


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.info = BinInfo("E:", "E:\\", GUID, MAX_BYTES, USED_BYTES, ITEMS)

    def test_forty_gib_more_overflows_and_the_message_has_every_number(self):
        message = recycle_bin.capacity_refusal(self.info, 40 * GIB)
        self.assertEqual(
            message,
            "Không thể dọn: Thùng rác của ổ E: đang chứa 11,0 GB, giới hạn 48,6 GB; chuyển thêm "
            "40,0 GB sẽ vượt giới hạn và Windows có thể xóa vĩnh viễn các mục cũ nhất. Hãy dọn sạch "
            "Thùng rác hoặc chọn ít video hơn.",
        )
        with self.assertRaises(RecycleRefused) as caught:
            recycle_bin.check_capacity(self.info, 40 * GIB)
        self.assertEqual(str(caught.exception), message)

    def test_jobs_40_to_59_fit_but_not_into_a_bin_holding_45_gib(self):
        jobs_40_59 = 5_327_042_728
        self.assertIsNone(recycle_bin.capacity_refusal(self.info, jobs_40_59))
        recycle_bin.check_capacity(self.info, jobs_40_59)
        full = BinInfo("E:", "E:\\", GUID, MAX_BYTES, 45 * GIB, 300)
        self.assertIn("đang chứa 45,0 GB", recycle_bin.capacity_refusal(full, jobs_40_59))

    def test_the_64_mib_margin_is_the_boundary(self):
        room = MAX_BYTES - CAPACITY_MARGIN_BYTES - USED_BYTES
        self.assertIsNone(recycle_bin.capacity_refusal(self.info, room))
        self.assertIsNotNone(recycle_bin.capacity_refusal(self.info, room + 1))


class SendToRecycleBinTests(RecycleFixture):
    def test_moves_the_file_and_verifies_the_record_on_a_com_thread(self):
        target = self.video()
        result = self.send(target)
        self.assertFalse(target.exists())
        self.assertIsInstance(result, RecycleResult)
        self.assertEqual(result.path, str(target))
        self.assertEqual(result.size_bytes, 1024)
        self.assertTrue(result.verified)
        self.assertEqual(result.record_path, self.shell.records[0])
        self.assertGreaterEqual(result.elapsed_seconds, 0.0)
        self.assertEqual(
            self.shell.calls, [{"path": str(target), "thread": "biliflow-recycle-bin", "daemon": True}],
        )
        self.co_initialize.assert_called_once_with()
        self.co_uninitialize.assert_called_once_with()
        self.assertEqual(recycle_bin.operations_in_progress(), frozenset())

    def test_no_record_or_an_orphan_record_leaves_it_unverified(self):
        for label, shell in (
            ("no $I", FakeShell(self.sid_dir, record=False)),
            ("$I without $R", FakeShell(self.sid_dir, twin=False)),
        ):
            with self.subTest(label):
                self.shell = shell
                target = self.video(f"{label.replace('$', '')}.mp4")
                result = self.send(target)
                self.assertFalse(target.exists())
                self.assertFalse(result.verified)
                self.assertIsNone(result.record_path)

    def test_shell_failures_keep_the_file_and_explain_why(self):
        cases = [
            ({"rc": 32}, SHARING_MESSAGE),
            ({"rc": 5}, SHARING_MESSAGE),
            ({"rc": 0x78}, SHARING_MESSAGE),
            ({"aborted": True}, ABORTED_MESSAGE),
            ({"rc": 1223}, ABORTED_MESSAGE),
            ({"rc": 0x75}, ABORTED_MESSAGE),
            ({"rc": 2}, "Windows báo lỗi 0x2 khi chuyển vào Thùng rác; video gốc vẫn còn trong input."),
            ({"rc": 0x402}, "Windows báo lỗi 0x402 khi chuyển vào Thùng rác; video gốc vẫn còn trong input."),
            ({}, NOT_MOVED_MESSAGE),
        ]
        for index, (values, expected) in enumerate(cases):
            with self.subTest(values=values):
                self.shell = FakeShell(self.sid_dir, move=False, **values)
                target = self.video(f"case-{index}.mp4")
                with self.assertRaises(RecycleFailed) as caught:
                    self.send(target)
                self.assertEqual(str(caught.exception), expected)
                self.assertTrue(target.is_file())
                self.assertEqual(recycle_bin.operations_in_progress(), frozenset())

    def test_a_file_that_left_despite_an_error_code_is_reported_as_moved(self):
        # Never "still in input" for a file that is gone; verification decides.
        self.shell = FakeShell(self.sid_dir, rc=32)
        target = self.video()
        result = self.send(target)
        self.assertFalse(target.exists())
        self.assertTrue(result.verified)

    def test_checks_run_before_the_shell_for_every_file(self):
        target = self.video()
        with self.assertRaises(RecycleRefused) as caught:
            self.send(target, expected_size=1000)
        self.assertEqual(str(caught.exception), SIZE_MESSAGE)
        with patch.object(recycle_bin, "_query_bin", return_value=(MAX_BYTES - CAPACITY_MARGIN_BYTES, 9)):
            with self.assertRaises(RecycleRefused) as caught:
                self.send(target)
        self.assertTrue(str(caught.exception).startswith("Không thể dọn: Thùng rác của ổ "))
        with patch.object(recycle_bin, "_bitbucket_settings", return_value=(MAX_MIB, 1, None)):
            with self.assertRaises(RecycleRefused):
                self.send(target)
        self.assertTrue(target.is_file())
        self.assertEqual(self.shell.calls, [])
        self.co_initialize.assert_not_called()

    def test_com_is_uninitialized_only_after_a_successful_initialize(self):
        self.co_initialize.return_value = False
        target = self.video()
        self.send(target)
        self.co_uninitialize.assert_not_called()

    def test_an_exception_in_the_shell_thread_reaches_the_caller(self):
        self.shell = FakeShell(self.sid_dir, error=RuntimeError("boom"))
        target = self.video()
        with self.assertRaisesRegex(RuntimeError, "boom"):
            self.send(target)
        self.co_uninitialize.assert_called_once_with()
        self.assertTrue(target.is_file())
        self.assertEqual(recycle_bin.operations_in_progress(), frozenset())

    def test_a_timeout_blocks_further_recycles_until_windows_answers(self):
        release = threading.Event()
        self.shell = FakeShell(self.sid_dir, block=release)
        late, answered = [], threading.Event()

        def on_late_result(result, error):
            late.append((result, error))
            # The busy guard clears only after the late result is recorded.
            late.append(sorted(recycle_bin.operations_in_progress()))
            answered.set()

        first = self.video("Tập 1.mp4")
        second = self.video("Tập 2.mp4")
        started = time.monotonic()
        with self.assertRaises(RecycleTimeout) as caught:
            self.send(first, timeout=0.2, on_late_result=on_late_result)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(str(caught.exception), TIMEOUT_MESSAGE)
        self.assertEqual(recycle_bin.operations_in_progress(), frozenset({str(first)}))
        with self.assertRaises(RecycleRefused) as caught:
            self.send(second)
        self.assertEqual(str(caught.exception), IN_PROGRESS_MESSAGE)
        self.assertTrue(second.is_file())
        self.assertEqual(len(self.shell.calls), 1)
        release.set()
        self.assertTrue(answered.wait(5))
        result, error = late[0]
        self.assertIsNone(error)
        self.assertEqual((result.path, result.size_bytes, result.verified), (str(first), 1024, True))
        self.assertEqual(late[1], [str(first)])
        self.assertTrue(wait_until(lambda: not recycle_bin.operations_in_progress()))
        self.assertFalse(first.exists())
        # The next recycle runs normally.
        self.assertTrue(self.send(second).verified)

    def test_a_late_failure_reaches_the_callback(self):
        release = threading.Event()
        self.shell = FakeShell(self.sid_dir, block=release, rc=32, move=False)
        late, answered = [], threading.Event()

        def on_late_result(result, error):
            late.append((result, error))
            answered.set()
            raise RuntimeError("a callback error must not break the recycle thread")

        target = self.video()
        with self.assertRaises(RecycleTimeout):
            self.send(target, timeout=0.2, on_late_result=on_late_result)
        release.set()
        self.assertTrue(answered.wait(5))
        result, error = late[0]
        self.assertIsNone(result)
        self.assertIsInstance(error, RecycleFailed)
        self.assertEqual(str(error), SHARING_MESSAGE)
        self.assertTrue(wait_until(lambda: not recycle_bin.operations_in_progress()))
        self.assertTrue(target.is_file())

    def test_a_timeout_without_a_callback_still_clears_when_windows_answers(self):
        release = threading.Event()
        self.shell = FakeShell(self.sid_dir, block=release)
        target = self.video()
        with self.assertRaises(RecycleTimeout):
            self.send(target, timeout=0.1)
        self.assertTrue(recycle_bin.operations_in_progress())
        release.set()
        self.assertTrue(wait_until(lambda: not recycle_bin.operations_in_progress()))


class VerificationRetryTests(RecycleFixture):
    """Windows may write the $I record after SHFileOperationW returns (jobs 43, 47 on 2026-10-03)."""

    def test_verification_retries_until_the_record_appears(self):
        self.shell = FakeShell(self.sid_dir, record=False)
        target = self.video()
        size = target.stat().st_size
        written = []

        def record_arrives_late(seconds):
            self.sleeps.append(seconds)
            if len(self.sleeps) == 2:
                (moved,) = self.sid_dir.glob("$R*")
                info = self.sid_dir / ("$I" + moved.name[2:])
                info.write_bytes(info_record_v2(target, size))
                written.append(str(info))

        self.patch_values({"_sleep": record_arrives_late})
        result = self.send(target)
        self.assertFalse(target.exists())
        self.assertTrue(result.verified)
        self.assertEqual(result.record_path, written[0])
        self.assertEqual(self.sleeps, [0.05, 0.1])

    def test_verification_gives_up_after_the_retry_schedule(self):
        self.shell = FakeShell(self.sid_dir, record=False)
        target = self.video()
        lookups = []
        real_find = recycle_bin.find_recycle_record

        def counting_find(*args, **kwargs):
            lookups.append(kwargs["since"])
            return real_find(*args, **kwargs)

        self.patch_values({"find_recycle_record": counting_find})
        result = self.send(target)
        self.assertFalse(target.exists())
        self.assertFalse(result.verified)
        self.assertIsNone(result.record_path)
        self.assertEqual(recycle_bin.VERIFY_RETRY_DELAYS, (0.05, 0.1, 0.2, 0.4, 0.8, 1.6))
        self.assertEqual(self.sleeps, list(recycle_bin.VERIFY_RETRY_DELAYS))
        self.assertLessEqual(sum(self.sleeps), 3.2)
        self.assertEqual(len(lookups), 7)
        self.assertEqual(len(set(lookups)), 1)

    def test_a_record_found_at_once_needs_no_pause(self):
        result = self.send(self.video())
        self.assertTrue(result.verified)
        self.assertEqual(self.sleeps, [])

    def test_a_lookup_error_counts_as_not_found_yet(self):
        record = "E:\\$Recycle.Bin\\S-1-5-21-1000\\$IABC123.mp4"
        answers = [OSError("bin folder busy"), ValueError("half-written $I"), record]

        def finder(*args, **kwargs):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with patch.object(recycle_bin, "find_recycle_record", side_effect=finder):
            found = recycle_bin.find_recycle_record_patiently("E:\\", "E:\\a.mp4", 1, since=0.0)
        self.assertEqual(found, record)
        self.assertEqual(self.sleeps, [0.05, 0.1])
        with patch.object(recycle_bin, "find_recycle_record", return_value=None) as never:
            self.assertIsNone(
                recycle_bin.find_recycle_record_patiently("E:\\", "E:\\a.mp4", 1, since=0.0, delays=(0.5,)),
            )
        self.assertEqual(never.call_count, 2)
        self.assertEqual(self.sleeps, [0.05, 0.1, 0.5])


class ExportRecyclerTests(RecycleFixture):
    """Batch 4 "Lưu trữ": only the reviewed export of an archived job (and its manifest) may go."""

    EXPORT = "Tập 12-1a2b3c4d-5e6f7a8b-reviewed.mp4"

    def export(self, name=EXPORT, size=2048, folder=None):
        return self.video(name, size=size, folder=folder or self.install / "output")

    def send_export(self, path, **kwargs):
        kwargs.setdefault("allowed_root", self.install / "output")
        kwargs.setdefault("expected_size", path.stat().st_size)
        return recycle_bin.send_export_to_recycle_bin(path, **kwargs)

    def refused(self, path, expected, **kwargs):
        with self.assertRaises(RecycleRefused) as caught:
            self.send_export(path, **kwargs)
        self.assertEqual(str(caught.exception), expected)
        self.assertTrue(path.is_file())

    def test_export_recycler_accepts_only_reviewed_exports_inside_output(self):
        output = self.install / "output"
        mp4 = self.export()
        manifest = self.export(self.EXPORT + ".manifest.json", size=300)
        result = self.send_export(mp4)
        self.assertFalse(mp4.exists())
        self.assertEqual((result.path, result.size_bytes, result.verified), (str(mp4), 2048, True))
        self.assertEqual(result.record_path, self.shell.records[0])
        self.assertTrue(self.send_export(manifest).verified)
        self.assertFalse(manifest.exists())
        self.assertEqual(len(self.shell.calls), 2)
        # Nothing but <install>/output, and only "…-reviewed.mp4" or its ".manifest.json".
        name_message = recycle_bin.EXPORT_NAME_MESSAGE
        root_message = "Chỉ chuyển được bản xuất trong thư mục output của BiliFlow vào Thùng rác."
        self.assertEqual(recycle_bin.EXPORT_ALLOWED_ROOT_MESSAGE, root_message)
        for name in ("Tập 12.mp4", "x-reviewed.mp4.json", "x-reviewed.mkv", "x-reviewed.mp4.manifest.json.bak"):
            with self.subTest(name=name):
                self.refused(self.export(name), name_message)
        self.refused(self.video("y-reviewed.mp4"), root_message, allowed_root=self.input)
        for root in (self.install, self.base, output / "sub"):
            with self.subTest(root=root):
                self.refused(self.export("z-reviewed.mp4"), root_message, allowed_root=root)
        # The path checks speak about the export, not about a source video.
        outside = self.video("w-reviewed.mp4", folder=self.base / "elsewhere")
        self.refused(outside, "Bản xuất nằm ngoài thư mục output; không chuyển vào Thùng rác.")
        self.refused(self.export("v-reviewed.mp4"), "Bản xuất không còn đúng dung lượng đã kiểm tra.",
                     expected_size=1)
        real = os.path.realpath
        linked = self.export("link-reviewed.mp4")
        with patch("os.path.realpath", side_effect=lambda p, *a, **k: str(self.base / "x") if p == str(linked) else real(p, *a, **k)):
            self.refused(linked, "Bản xuất là liên kết (symlink/junction); không chuyển vào Thùng rác.")
        with patch.object(recycle_bin, "_query_bin", return_value=(MAX_BYTES - CAPACITY_MARGIN_BYTES, 9)):
            with self.assertRaises(RecycleRefused) as caught:
                self.send_export(self.export("full-reviewed.mp4"))
        self.assertTrue(str(caught.exception).startswith("Không thể lưu trữ: Thùng rác của ổ "), caught.exception)
        self.assertEqual(len(self.shell.calls), 2)
        # Shell failures keep the export in output and say so.
        for index, (values, expected) in enumerate((
            ({"aborted": True}, "Windows đã hủy thao tác; bản xuất vẫn còn trong output."),
            ({"rc": 2}, "Windows báo lỗi 0x2 khi chuyển vào Thùng rác; bản xuất vẫn còn trong output."),
            ({}, "Windows không chuyển file; bản xuất vẫn còn trong output."),
            ({"rc": 32}, SHARING_MESSAGE),
        )):
            with self.subTest(values=values):
                self.shell = FakeShell(self.sid_dir, move=False, **values)
                target = self.export(f"case-{index}-reviewed.mp4")
                with self.assertRaises(RecycleFailed) as caught:
                    self.send_export(target)
                self.assertEqual(str(caught.exception), expected)
                self.assertTrue(target.is_file())

    def test_export_recycler_refuses_streams_and_subfolders(self):
        # Security review (L3): "x.mp4:y-reviewed.mp4" is a stream of another file (lstat even
        # reports the stream's own size), and an export is always directly in output/.
        output = self.install / "output"
        host = self.export("host.mp4", size=4096)
        data = host.read_bytes()
        stream = Path(str(host) + ":evil-reviewed.mp4")
        with open(stream, "wb") as handle:
            handle.write(b"s" * 64)
        self.assertEqual(os.lstat(stream).st_size, 64)
        with self.assertRaises(RecycleRefused) as caught:
            self.send_export(stream, expected_size=64)
        self.assertEqual(str(caught.exception), recycle_bin.EXPORT_WORDING.path)
        self.assertEqual(recycle_bin.export_path_refusal(stream, output_root=output), recycle_bin.EXPORT_WORDING.path)
        folder_message = "Bản xuất không nằm ngay trong thư mục output; không chuyển vào Thùng rác."
        self.assertEqual(recycle_bin.EXPORT_FOLDER_MESSAGE, folder_message)
        for relative in ("sub/x-reviewed.mp4", "sub/x-reviewed.mp4.manifest.json", "a/b/y-reviewed.mp4"):
            with self.subTest(relative=relative):
                self.refused(self.export(Path(relative).name, folder=output / Path(relative).parent),
                             folder_message)
        # The same rule when the archive checks a card (no size read, nothing touched).
        self.assertEqual(recycle_bin.export_path_refusal(output / "c" / "z-reviewed.mp4", output_root=output),
                         folder_message)
        self.assertIsNone(recycle_bin.export_path_refusal(self.export(), output_root=output))
        self.assertEqual(self.shell.calls, [])
        self.assertEqual(host.read_bytes(), data)
        with open(stream, "rb") as handle:
            self.assertEqual(handle.read(), b"s" * 64)

    def test_send_to_recycle_bin_still_refuses_output(self):
        output = self.install / "output"
        mp4 = self.export()
        with self.assertRaises(RecycleRefused) as caught:
            self.send(mp4, allowed_root=output)
        self.assertEqual(str(caught.exception), ALLOWED_ROOT_MESSAGE)
        # An export name inside input is still a source: input wording, no export rule.
        source = self.video("Tập 13-reviewed.mp4")
        with self.assertRaises(RecycleRefused) as caught:
            self.send(source, expected_size=1)
        self.assertEqual(str(caught.exception), SIZE_MESSAGE)
        self.assertTrue(mp4.is_file())
        self.assertTrue(source.is_file())
        self.assertEqual(self.shell.calls, [])
        # The source wording is unchanged for every source failure.
        self.shell = FakeShell(self.sid_dir, move=False, aborted=True)
        with self.assertRaises(RecycleFailed) as caught:
            self.send(source)
        self.assertEqual(str(caught.exception), ABORTED_MESSAGE)


class InfoRecordTests(unittest.TestCase):
    def test_parses_version_2_and_version_1(self):
        path = "E:\\DungChung\\BiliFlow\\input\\Tập 12.mp4"
        self.assertEqual(recycle_bin.parse_info_record(info_record_v2(path, 252168775, 99)),
                         (2, 252168775, 99, path))
        self.assertEqual(recycle_bin.parse_info_record(info_record_v1(path, 1024, 7)), (1, 1024, 7, path))

    def test_malformed_records_raise_value_error(self):
        good = info_record_v2("E:\\a.mp4", 1)
        cases = {
            "empty": b"",
            "short header": good[:20],
            "no length": good[:24],
            "zero length": struct.pack("<qqqi", 2, 1, 0, 0),
            "length past the end": struct.pack("<qqqi", 2, 1, 0, 50) + "E:\\a".encode("utf-16-le"),
            "negative length": struct.pack("<qqqi", 2, 1, 0, -1),
            "unknown version": struct.pack("<qqqi", 3, 1, 0, 2) + "a\0".encode("utf-16-le"),
            "short version 1": info_record_v1("E:\\a.mp4", 1)[:100],
            "empty version 1": struct.pack("<qqq", 1, 1, 0) + b"\0" * 520,
            "odd utf-16": struct.pack("<qqqi", 2, 1, 0, 2) + b"\x00\xd8\x00\xd8",
        }
        for label, data in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                recycle_bin.parse_info_record(data)


class FindRecycleRecordTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.volume = Path(temp.name).resolve() / "volume"
        self.bin = self.volume / "$Recycle.Bin"
        self.user = self.bin / "S-1-5-21-1000"
        self.system = self.bin / "S-1-5-18"
        self.user.mkdir(parents=True)
        self.system.mkdir(parents=True)
        self.original = "E:\\DungChung\\BiliFlow\\input\\Tập 12.mp4"
        self.since = time.time() - 60

    def record(self, token, *, path=None, size=1024, twin=True, folder=None, extension=".mp4", age=None):
        folder = folder or self.user
        info = folder / f"$I{token}{extension}"
        info.write_bytes(info_record_v2(path or self.original, size))
        if twin:
            (folder / f"$R{token}{extension}").write_bytes(b"x" * 8)
        if age is not None:
            moment = time.time() - age
            os.utime(info, (moment, moment))
        return str(info)

    def find(self, size=1024, since=None):
        return recycle_bin.find_recycle_record(
            str(self.volume) + "\\", self.original, size, since=self.since if since is None else since,
        )

    def test_finds_the_record_with_its_twin(self):
        expected = self.record("ABC123")
        self.assertEqual(self.find(), expected)

    def test_matches_case_and_unicode_normalization(self):
        decomposed = unicodedata.normalize("NFD", self.original.upper())
        expected = self.record("NFD001", path=decomposed)
        self.assertEqual(self.find(), expected)

    def test_rejects_orphans_wrong_size_wrong_path_old_records_and_other_extensions(self):
        self.record("ORPHAN", twin=False)
        self.record("SIZE01", size=1025)
        self.record("PATH01", path="E:\\DungChung\\BiliFlow\\input\\Tập 13.mp4")
        self.record("OLD001", age=3600)
        self.record("MKV001", extension=".mkv")
        self.assertIsNone(self.find())

    def test_skips_folders_it_cannot_read(self):
        expected = self.record("USER01")
        self.record("SYSTEM", folder=self.system)
        real_scandir = os.scandir
        refused = []

        def scandir(path="."):
            if os.path.normcase(str(path)) == os.path.normcase(str(self.system)):
                refused.append(path)
                raise PermissionError(5, "Access is denied", str(path))
            return real_scandir(path)

        with patch("os.scandir", side_effect=scandir):
            self.assertEqual(self.find(), expected)
        self.assertEqual(len(refused), 1)

    def test_records_written_after_until_are_ignored(self):
        # Security review (L5): a later deletion of the same path and size must not answer for an
        # earlier move; within [since, until] the newest record still wins.
        volume = str(self.volume) + "\\"
        older = self.record("OLDER2", age=600)
        self.record("NEWER2", age=10)
        since = time.time() - 3600
        self.assertEqual(recycle_bin.find_recycle_record(volume, self.original, 1024, since=since,
                                                         until=time.time() - 300), older)
        self.assertIsNone(recycle_bin.find_recycle_record(volume, self.original, 1024, since=since,
                                                          until=time.time() - 900))
        self.assertNotEqual(recycle_bin.find_recycle_record(volume, self.original, 1024, since=since,
                                                            until=None), older)

    def test_newest_match_wins_and_a_missing_bin_finds_nothing(self):
        self.record("OLDER1", age=30)
        newest = self.record("NEWER1")
        self.assertEqual(self.find(), newest)
        self.assertIsNone(recycle_bin.find_recycle_record(
            str(self.volume / "nowhere"), self.original, 1024, since=0,
        ))


@unittest.skipUnless(
    os.name == "nt" and REAL_BIN_ENABLED,
    "the single real Recycle Bin run is opt-in (BILIFLOW_TEST_RECYCLE_BIN=1, set only by the lead)",
)
class RealRecycleBinTest(unittest.TestCase):
    """G4: exactly one real recycle of a 1 KB file this test creates. No mixin, no patches."""

    def test_one_kilobyte_file_goes_to_the_recycle_bin(self):
        root = recycle_bin.TEST_RECYCLE_ROOT
        self.assertEqual(root, recycle_bin.INSTALL_ROOT / "temp" / "recycle-bin-test")
        self.assertNotIn("input", [part.casefold() for part in root.relative_to(recycle_bin.INSTALL_ROOT).parts])
        marker = root / "ran-once.json"
        if marker.exists():
            self.skipTest("Đã chạy một lần; không chạy lại khi chưa có người dùng đồng ý.")
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{uuid.uuid4()}.txt"
        target.write_bytes(os.urandom(1024))
        result = None
        try:
            result = recycle_bin.send_to_recycle_bin(target, allowed_root=root, expected_size=1024)
        finally:
            marker.write_text(json.dumps({
                "path": str(target),
                "record_path": None if result is None else result.record_path,
                "verified": None if result is None else result.verified,
                "at": datetime.now(timezone.utc).astimezone().isoformat(),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertFalse(target.exists())
        self.assertTrue(result.verified)
        self.assertTrue(Path(result.record_path).is_file())


if __name__ == "__main__":
    unittest.main()
