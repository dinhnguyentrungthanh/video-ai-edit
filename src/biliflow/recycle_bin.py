"""Move one file to the Windows Recycle Bin, with every check done first.

"Dọn video gốc" (batch 3) moves a source video that was exported or skipped
to the Recycle Bin, so it can still be restored until the bin is emptied.
Windows must never delete a file permanently on our behalf, so before the
shell is called this module refuses:

- any ``allowed_root`` other than ``<install>/input`` (plus
  ``<install>/temp/recycle-bin-test`` while ``BILIFLOW_TEST_RECYCLE_BIN=1``);
- a path that is relative, a link or junction, outside that root, longer than
  259 characters, or not the size that was scanned;
- a volume that is not a fixed disk, has no Recycle Bin settings, deletes
  immediately ("NukeOnDelete"), is overridden by policy, or whose bin would
  overflow (Windows then deletes the oldest items permanently).

The shell call is ``SHFileOperationW(FO_DELETE, FOF_ALLOWUNDO | ...)`` on a
daemon COM STA thread with a timeout. ``FOF_WANTNUKEWARNING`` is kept on
purpose: if a check above is bypassed, Windows asks instead of deleting
silently, and the timeout covers a dialog nobody answers. After the move the
``$I`` record (and its ``$R`` twin) is looked up to verify where the file went.

Standard library only. ``ctypes`` and ``winreg`` are imported inside the
functions that need them; nothing runs at import time. Tests patch the small
``_`` helpers (``_shell_delete`` above all) so they never reach the real bin.
"""

from __future__ import annotations

import functools
import os
import re
import stat
import struct
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


FO_DELETE = 3
FOF_SILENT = 0x4
FOF_NOCONFIRMATION = 0x10
FOF_ALLOWUNDO = 0x40
FOF_NOERRORUI = 0x400
FOF_WANTNUKEWARNING = 0x4000
RECYCLE_FLAGS = (
    FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING
)  # 0x4454
MAX_PATH_CHARS = 259
DRIVE_FIXED = 3
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
BITBUCKET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket"
POLICIES_EXPLORER_KEY = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
CAPACITY_MARGIN_BYTES = 64 * 1048576
DEFAULT_TIMEOUT_SECONDS = 60.0
INSTALL_ROOT = Path(__file__).resolve().parents[2]
TEST_RECYCLE_ROOT = INSTALL_ROOT / "temp" / "recycle-bin-test"
TEST_RECYCLE_ENV = "BILIFLOW_TEST_RECYCLE_BIN"
COINIT_APARTMENTTHREADED = 0x2
COINIT_DISABLE_OLE1DDE = 0x4
MEBIBYTE = 1048576
GIBIBYTE = 1073741824
# SHFileOperationW return codes: sharing/access violations and user aborts.
SHARING_RETURN_CODES = frozenset({5, 32, 0x78})
ABORTED_RETURN_CODES = frozenset({0x75, 1223})
INFO_RECORD_V1_PATH_BYTES = 520
INFO_RECORD_MAX_CHARS = 32768

# Verbatim messages ({volume} = 'E:'; GB = bytes / 1073741824, one decimal, decimal comma).
NOT_WINDOWS_MESSAGE = "Thùng rác chỉ dùng được trên Windows."
ALLOWED_ROOT_MESSAGE = "Chỉ dọn được video trong thư mục input của BiliFlow."
NOT_FIXED_MESSAGE = "Ổ {volume} không phải ổ cứng cố định; không dùng Thùng rác được."
NO_BIN_SETTINGS_MESSAGE = (
    "Chưa thấy cấu hình Thùng rác của ổ {volume} trong Windows; mở Thuộc tính Thùng rác "
    "một lần rồi thử lại."
)
NUKE_MESSAGE = (
    "Thùng rác của ổ {volume} đang đặt “Xóa file ngay, không chuyển vào Thùng rác”; "
    "không dọn được."
)
POLICY_MESSAGE = "Chính sách Windows tắt hoặc tự đặt giới hạn Thùng rác; không dọn được."
QUERY_MESSAGE = "Không đọc được dung lượng Thùng rác của ổ {volume}; không dọn được."
OUTSIDE_MESSAGE = "Video gốc nằm ngoài thư mục input; không dọn."
LINK_MESSAGE = "Video gốc là liên kết (symlink/junction); không dọn."
PATH_MESSAGE = (
    "Đường dẫn video gốc dài hơn 259 ký tự hoặc có ký tự Thùng rác không nhận; không dọn."
)
SIZE_MESSAGE = "Video gốc không còn đúng dung lượng đã quét."
CAPACITY_MESSAGE = (
    "Không thể dọn: Thùng rác của ổ {volume} đang chứa {used} GB, giới hạn {max} GB; "
    "chuyển thêm {extra} GB sẽ vượt giới hạn và Windows có thể xóa vĩnh viễn các mục cũ nhất. "
    "Hãy dọn sạch Thùng rác hoặc chọn ít video hơn."
)
SHARING_MESSAGE = (
    "File đang được mở (ví dụ đang phát trong trang duyệt). Đóng trang duyệt của video này "
    "rồi thử lại."
)
ABORTED_MESSAGE = "Windows đã hủy thao tác; video gốc vẫn còn trong input."
ERROR_RC_MESSAGE = (
    "Windows báo lỗi 0x{rc:X} khi chuyển vào Thùng rác; video gốc vẫn còn trong input."
)
NOT_MOVED_MESSAGE = "Windows không chuyển file; video gốc vẫn còn trong input."
TIMEOUT_MESSAGE = (
    "Windows chưa trả lời sau 60 giây — có thể đang hỏi xác nhận. Kiểm tra cửa sổ Windows; "
    "đừng chọn xóa vĩnh viễn."
)
IN_PROGRESS_MESSAGE = (
    "Lần chuyển vào Thùng rác trước chưa xong (Windows có thể đang hỏi xác nhận). "
    "Kiểm tra cửa sổ Windows rồi thử lại."
)

_GUID_PATTERN = re.compile(r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}")


class RecycleRefused(ValueError):
    """Refused before the file was touched."""


class RecycleFailed(RuntimeError):
    """The shell reported a failure and the file is still in place."""


class RecycleTimeout(RuntimeError):
    """The shell did not return within the timeout (it may be showing a dialog)."""


@dataclass(frozen=True)
class BinInfo:
    volume: str
    root: str
    guid: str
    max_bytes: int
    used_bytes: int
    items: int

    @property
    def available_bytes(self) -> int:
        return max(0, self.max_bytes - self.used_bytes)


@dataclass(frozen=True)
class RecycleResult:
    path: str
    size_bytes: int
    verified: bool
    record_path: str | None
    elapsed_seconds: float


LateResultCallback = Callable[[RecycleResult | None, BaseException | None], None]


# --------------------------------------------------------------------------
# Small helpers; tests patch these.


def _is_windows() -> bool:
    return os.name == "nt"


def _test_bin_enabled(environ: Any = None) -> bool:
    """The opt-in for the one real Recycle Bin test (set only by the lead)."""
    values = os.environ if environ is None else environ
    return values.get(TEST_RECYCLE_ENV) == "1"


def _allowed_roots() -> list[Path]:
    roots = [INSTALL_ROOT / "input"]
    if _test_bin_enabled():
        roots.append(TEST_RECYCLE_ROOT)
    return roots


@functools.lru_cache(maxsize=1)
def _structures() -> tuple[type, type]:
    """(SHFILEOPSTRUCTW, SHQUERYRBINFO), natural alignment (56 and 24 bytes on x64)."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("i64Size", ctypes.c_longlong),
            ("i64NumItems", ctypes.c_longlong),
        ]

    return SHFILEOPSTRUCTW, SHQUERYRBINFO


@functools.lru_cache(maxsize=1)
def _api() -> dict[str, Any]:
    """Private WinDLL handles with full argtypes/restype (never the shared ctypes.windll)."""
    import ctypes
    from ctypes import wintypes

    shfileop, shqueryrbinfo = _structures()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    volume_path = kernel32.GetVolumePathNameW
    volume_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    volume_path.restype = wintypes.BOOL
    drive_type = kernel32.GetDriveTypeW
    drive_type.argtypes = [wintypes.LPCWSTR]
    drive_type.restype = wintypes.UINT
    volume_name = kernel32.GetVolumeNameForVolumeMountPointW
    volume_name.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    volume_name.restype = wintypes.BOOL
    query_bin = shell32.SHQueryRecycleBinW
    query_bin.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(shqueryrbinfo)]
    query_bin.restype = ctypes.c_long
    file_operation = shell32.SHFileOperationW
    file_operation.argtypes = [ctypes.POINTER(shfileop)]
    file_operation.restype = ctypes.c_int
    co_initialize = ole32.CoInitializeEx
    co_initialize.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    co_initialize.restype = ctypes.c_long
    co_uninitialize = ole32.CoUninitialize
    co_uninitialize.argtypes = []
    co_uninitialize.restype = None
    return {
        "GetVolumePathNameW": volume_path,
        "GetDriveTypeW": drive_type,
        "GetVolumeNameForVolumeMountPointW": volume_name,
        "SHQueryRecycleBinW": query_bin,
        "SHFileOperationW": file_operation,
        "CoInitializeEx": co_initialize,
        "CoUninitialize": co_uninitialize,
    }


def _path_buffer(path: Any) -> Any:
    """pFrom for SHFileOperationW: the path, then two NUL characters (a one-item list)."""
    import ctypes

    return ctypes.create_unicode_buffer(str(path) + "\0")


def _file_operation(path: Any) -> tuple[Any, Any]:
    """The SHFILEOPSTRUCTW for one recycle, and the buffer it points to (keep it alive)."""
    import ctypes
    from ctypes import wintypes

    shfileop, _ = _structures()
    buffer = _path_buffer(path)
    operation = shfileop()
    operation.hwnd = None
    operation.wFunc = FO_DELETE
    operation.pFrom = ctypes.cast(buffer, wintypes.LPCWSTR)
    operation.pTo = None
    operation.fFlags = RECYCLE_FLAGS
    operation.fAnyOperationsAborted = 0
    operation.hNameMappings = None
    operation.lpszProgressTitle = None
    return operation, buffer


def _volume_root(path: Any) -> str:
    """GetVolumePathNameW: the mount point of the volume holding ``path`` (``'E:\\'``)."""
    import ctypes

    buffer = ctypes.create_unicode_buffer(1024)
    if not _api()["GetVolumePathNameW"](str(path), buffer, len(buffer)):
        raise OSError(ctypes.get_last_error(), "GetVolumePathNameW failed", str(path))
    return buffer.value


def _drive_type(root: str) -> int:
    return int(_api()["GetDriveTypeW"](str(root)))


def _volume_guid(root: str) -> str:
    """GetVolumeNameForVolumeMountPointW: the ``{guid}`` naming the BitBucket volume key."""
    import ctypes

    buffer = ctypes.create_unicode_buffer(64)
    if not _api()["GetVolumeNameForVolumeMountPointW"](str(root), buffer, len(buffer)):
        raise OSError(ctypes.get_last_error(), "GetVolumeNameForVolumeMountPointW failed", str(root))
    match = _GUID_PATTERN.search(buffer.value)
    if match is None:
        raise ValueError(f"No volume GUID in {buffer.value!r}")
    return match.group(0)


def _registry_number(key: Any, name: str) -> int | None:
    import winreg

    try:
        value, _ = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None
    return int(value)


def _bitbucket_settings(guid: str) -> tuple[int | None, int | None, int | None]:
    """(MaxCapacity MiB, NukeOnDelete, root NukeOnDelete) from HKCU; None when absent."""
    import winreg

    root_nuke = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BITBUCKET_KEY) as key:
            root_nuke = _registry_number(key, "NukeOnDelete")
    except FileNotFoundError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{BITBUCKET_KEY}\\Volume\\{guid}") as key:
            return (
                _registry_number(key, "MaxCapacity"),
                _registry_number(key, "NukeOnDelete"),
                root_nuke,
            )
    except FileNotFoundError:
        return None, None, root_nuke


def _policy_values() -> dict[str, Any]:
    """Explorer policies that disable or size the Recycle Bin (HKLM, then HKCU).

    ``NoRecycleFiles`` is 1 when either hive sets it; ``RecycleBinSize`` is the
    first value found. ``unreadable`` is True when a policy key exists but
    cannot be read, so the caller fails closed.
    """
    import winreg

    values: dict[str, Any] = {"NoRecycleFiles": None, "RecycleBinSize": None, "unreadable": False}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, POLICIES_EXPLORER_KEY) as key:
                for name in ("NoRecycleFiles", "RecycleBinSize"):
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                    except FileNotFoundError:
                        continue
                    if name == "NoRecycleFiles":
                        if values[name] is None or value == 1:
                            values[name] = value
                    elif values[name] is None:
                        values[name] = value
        except FileNotFoundError:
            continue
        except OSError:
            values["unreadable"] = True
    return values


def _query_bin(root: str) -> tuple[int, int]:
    """(bytes, items) of the Recycle Bin of one volume.

    Always the volume root: SHQueryRecycleBinW(NULL or '') adds up every drive.
    """
    import ctypes

    if not root:
        raise ValueError("SHQueryRecycleBinW needs a volume root, never NULL or ''")
    _, shqueryrbinfo = _structures()
    info = shqueryrbinfo()
    info.cbSize = ctypes.sizeof(info)
    result = int(_api()["SHQueryRecycleBinW"](str(root), ctypes.byref(info)))
    if result != 0:
        raise OSError(f"SHQueryRecycleBinW failed: 0x{result & 0xFFFFFFFF:08X}")
    return int(info.i64Size), int(info.i64NumItems)


def _co_initialize() -> bool:
    """CoInitializeEx(STA | DISABLE_OLE1DDE); True when CoUninitialize must follow."""
    result = int(_api()["CoInitializeEx"](None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE))
    return result in (0, 1)  # S_OK, S_FALSE


def _co_uninitialize() -> None:
    _api()["CoUninitialize"]()


def _shell_delete(path: Any) -> tuple[int, bool]:
    """SHFileOperationW(FO_DELETE, RECYCLE_FLAGS) on one path: (return code, aborted)."""
    import ctypes

    operation, buffer = _file_operation(path)
    result = int(_api()["SHFileOperationW"](ctypes.byref(operation)))
    aborted = bool(operation.fAnyOperationsAborted)
    del buffer
    return result, aborted


# --------------------------------------------------------------------------
# Checks done before the file is touched.


def _normcase(path: Any) -> str:
    return os.path.normcase(str(path))


def _key(path: Any) -> str:
    return _normcase(os.path.abspath(str(path)))


def _is_under(path: str, root: str) -> bool:
    """``path`` is strictly inside ``root`` (both normcased absolute strings)."""
    try:
        common = os.path.commonpath([path, root])
    except ValueError:
        return False
    return common == root and path != root


def path_refusal(path: Any, *, allowed_root: Any) -> str | None:
    """Why ``path`` must not go to the Recycle Bin, or None. Never reads the size."""
    raw = str(path)
    if not raw or not os.path.isabs(raw):
        return OUTSIDE_MESSAGE
    if (
        "\0" in raw or "*" in raw or "?" in raw
        or raw.startswith("\\\\?\\") or raw.startswith("//?/")
    ):
        return PATH_MESSAGE
    absolute = os.path.abspath(raw)
    if len(absolute) > MAX_PATH_CHARS:
        return PATH_MESSAGE
    root = _normcase(Path(allowed_root).resolve())
    if not _is_under(_normcase(absolute), root):
        return OUTSIDE_MESSAGE
    if _normcase(os.path.realpath(absolute)) != _normcase(absolute):
        return LINK_MESSAGE
    try:
        info = os.lstat(absolute)
    except OSError:
        return SIZE_MESSAGE
    if getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        return LINK_MESSAGE
    if not stat.S_ISREG(info.st_mode):
        return SIZE_MESSAGE
    return None


def validate_target(path: Any, *, allowed_root: Any, expected_size: int) -> os.stat_result:
    """The file's lstat when it may be recycled; RecycleRefused otherwise."""
    if not _is_windows():
        raise RecycleRefused(NOT_WINDOWS_MESSAGE)
    refusal = path_refusal(path, allowed_root=allowed_root)
    if refusal:
        raise RecycleRefused(refusal)
    try:
        info = os.lstat(os.path.abspath(str(path)))
    except OSError as error:
        raise RecycleRefused(SIZE_MESSAGE) from error
    if isinstance(expected_size, bool) or int(info.st_size) != expected_size:
        raise RecycleRefused(SIZE_MESSAGE)
    return info


def volume_bin_info(path: Any) -> BinInfo:
    """Recycle Bin settings and usage of the volume holding ``path``; RecycleRefused when unusable."""
    if not _is_windows():
        raise RecycleRefused(NOT_WINDOWS_MESSAGE)
    drive = os.path.splitdrive(os.path.abspath(str(path)))[0] or str(path)
    try:
        root = _volume_root(path)
    except (OSError, ValueError) as error:
        raise RecycleRefused(QUERY_MESSAGE.format(volume=drive)) from error
    volume = root.rstrip("\\/") or root
    try:
        drive_type = _drive_type(root)
    except (OSError, ValueError) as error:
        raise RecycleRefused(NOT_FIXED_MESSAGE.format(volume=volume)) from error
    if drive_type != DRIVE_FIXED:
        raise RecycleRefused(NOT_FIXED_MESSAGE.format(volume=volume))
    try:
        guid = _volume_guid(root)
        max_mib, nuke, root_nuke = _bitbucket_settings(guid)
    except (OSError, ValueError, TypeError) as error:
        raise RecycleRefused(NO_BIN_SETTINGS_MESSAGE.format(volume=volume)) from error
    if max_mib is None or int(max_mib) <= 0:
        raise RecycleRefused(NO_BIN_SETTINGS_MESSAGE.format(volume=volume))
    if int(nuke or 0) != 0 or int(root_nuke or 0) != 0:
        raise RecycleRefused(NUKE_MESSAGE.format(volume=volume))
    try:
        policies = _policy_values()
    except (OSError, ValueError) as error:
        raise RecycleRefused(POLICY_MESSAGE) from error
    if (
        policies.get("unreadable")
        or policies.get("NoRecycleFiles") == 1
        or policies.get("RecycleBinSize") is not None
    ):
        raise RecycleRefused(POLICY_MESSAGE)
    try:
        used_bytes, items = _query_bin(root)
    except (OSError, ValueError) as error:
        raise RecycleRefused(QUERY_MESSAGE.format(volume=volume)) from error
    return BinInfo(
        volume=volume, root=root, guid=guid, max_bytes=int(max_mib) * MEBIBYTE,
        used_bytes=int(used_bytes), items=int(items),
    )


def _gigabytes(value: int) -> str:
    return f"{value / GIBIBYTE:.1f}".replace(".", ",")


def capacity_refusal(info: BinInfo, extra_bytes: int) -> str | None:
    """The CAPACITY text when ``extra_bytes`` more would overflow the bin, else None."""
    if info.used_bytes + int(extra_bytes) > info.max_bytes - CAPACITY_MARGIN_BYTES:
        return CAPACITY_MESSAGE.format(
            volume=info.volume, used=_gigabytes(info.used_bytes),
            max=_gigabytes(info.max_bytes), extra=_gigabytes(int(extra_bytes)),
        )
    return None


def check_capacity(info: BinInfo, extra_bytes: int) -> None:
    refusal = capacity_refusal(info, extra_bytes)
    if refusal:
        raise RecycleRefused(refusal)


# --------------------------------------------------------------------------
# The shell call: one at a time, on its own COM STA thread, with a timeout.


class _Operation:
    """One shell call. Mutable; every field is read and written under _PENDING_LOCK."""

    def __init__(self, key: str, path: str, size: int, volume_root: str, started: float):
        self.key = key
        self.path = path
        self.size = size
        self.volume_root = volume_root
        self.started = started
        self.monotonic_started = time.monotonic()
        self.done = False
        self.rc: int | None = None
        self.aborted = False
        self.error: BaseException | None = None
        self.timed_out = False
        self.on_late_result: LateResultCallback | None = None


# Paths whose shell call has not returned yet (normcased absolute path). A path
# whose call timed out stays here until Windows answers, and blocks every
# other recycle meanwhile.
_PENDING_LOCK = threading.Lock()
_PENDING: dict[str, _Operation] = {}


def operations_in_progress() -> frozenset[str]:
    """Paths whose shell call has not returned (including every timed-out call)."""
    with _PENDING_LOCK:
        return frozenset(operation.path for operation in _PENDING.values())


def _conclude(operation: _Operation) -> RecycleResult:
    """Turn a finished shell call into a result, or raise what went wrong."""
    if operation.error is not None:
        raise operation.error
    if os.path.lexists(operation.path):
        rc = int(operation.rc or 0)
        if rc in SHARING_RETURN_CODES:
            raise RecycleFailed(SHARING_MESSAGE)
        if operation.aborted or rc in ABORTED_RETURN_CODES:
            raise RecycleFailed(ABORTED_MESSAGE)
        if rc != 0:
            raise RecycleFailed(ERROR_RC_MESSAGE.format(rc=rc))
        raise RecycleFailed(NOT_MOVED_MESSAGE)
    # The file left its folder; whatever the return code said, look for the
    # bin record rather than report a failure for a file that is gone.
    try:
        record = find_recycle_record(
            operation.volume_root, operation.path, operation.size,
            since=operation.started - 5,
        )
    except (OSError, ValueError):
        record = None
    return RecycleResult(
        path=operation.path, size_bytes=operation.size, verified=record is not None,
        record_path=record, elapsed_seconds=round(time.monotonic() - operation.monotonic_started, 3),
    )


def _run_operation(operation: _Operation) -> None:
    rc, aborted, error = None, False, None
    try:
        initialized = _co_initialize()
        try:
            rc, aborted = _shell_delete(operation.path)
        finally:
            if initialized:
                _co_uninitialize()
    except BaseException as caught:  # noqa: BLE001 - handed to the caller or the callback
        error = caught
    with _PENDING_LOCK:
        operation.rc, operation.aborted, operation.error = rc, bool(aborted), error
        operation.done = True
        late = operation.timed_out
        if not late:
            _PENDING.pop(operation.key, None)
    if not late:
        return
    try:
        result, failure = None, None
        try:
            result = _conclude(operation)
        except BaseException as caught:  # noqa: BLE001
            failure = caught
        callback = operation.on_late_result
        if callback is not None:
            try:
                callback(result, failure)
            except Exception:  # noqa: BLE001 - the callback must not kill this thread
                pass
    finally:
        # Only now may another recycle start: the late result is recorded.
        with _PENDING_LOCK:
            _PENDING.pop(operation.key, None)


def send_to_recycle_bin(
    path: Any, *, allowed_root: Any, expected_size: int,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    on_late_result: LateResultCallback | None = None,
) -> RecycleResult:
    """Move one file to the Recycle Bin of its volume after every check passes.

    Raises RecycleRefused (nothing touched), RecycleFailed (the shell failed
    and the file is still there) or RecycleTimeout (no answer within
    ``timeout``; ``on_late_result(result, error)`` is then called once from the
    recycle thread when Windows answers).
    """
    allowed = _normcase(Path(allowed_root).resolve())
    if allowed not in {_normcase(Path(root).resolve()) for root in _allowed_roots()}:
        raise RecycleRefused(ALLOWED_ROOT_MESSAGE)
    validate_target(path, allowed_root=allowed_root, expected_size=expected_size)
    target = os.path.abspath(str(path))
    info = volume_bin_info(target)
    check_capacity(info, expected_size)
    operation = _Operation(_key(target), target, int(expected_size), info.root, time.time())
    with _PENDING_LOCK:
        if _PENDING:
            raise RecycleRefused(IN_PROGRESS_MESSAGE)
        _PENDING[operation.key] = operation
    thread = threading.Thread(
        target=_run_operation, args=(operation,), name="biliflow-recycle-bin", daemon=True,
    )
    try:
        thread.start()
    except BaseException:
        with _PENDING_LOCK:
            _PENDING.pop(operation.key, None)
        raise
    thread.join(timeout)
    with _PENDING_LOCK:
        if not operation.done:
            operation.timed_out = True
            operation.on_late_result = on_late_result
            raise RecycleTimeout(TIMEOUT_MESSAGE)
    return _conclude(operation)


# --------------------------------------------------------------------------
# Verification: the $I record Windows writes next to the moved file ($R).


def parse_info_record(data: bytes) -> tuple[int, int, int, str]:
    """(version, size, deletion FILETIME, original path) of a ``$I`` file; ValueError if malformed."""
    data = bytes(data)
    if len(data) < 24:
        raise ValueError("Recycle Bin record is too short")
    version, size, filetime = struct.unpack_from("<qqq", data, 0)
    if version == 2:
        if len(data) < 28:
            raise ValueError("Recycle Bin record has no path length")
        (characters,) = struct.unpack_from("<i", data, 24)
        if not 1 <= characters <= INFO_RECORD_MAX_CHARS or len(data) < 28 + 2 * characters:
            raise ValueError("Recycle Bin record has a bad path length")
        text = data[28:28 + 2 * characters].decode("utf-16-le")
        if text.endswith("\0"):
            text = text[:-1]
        if not text or "\0" in text:
            raise ValueError("Recycle Bin record has a bad path")
        return version, size, filetime, text
    if version == 1:
        if len(data) < 24 + INFO_RECORD_V1_PATH_BYTES:
            raise ValueError("Recycle Bin record is too short")
        text = data[24:24 + INFO_RECORD_V1_PATH_BYTES].decode("utf-16-le").split("\0", 1)[0]
        if not text:
            raise ValueError("Recycle Bin record has no path")
        return version, size, filetime, text
    raise ValueError(f"Unsupported Recycle Bin record version: {version}")


def _comparable(path: str) -> str:
    return unicodedata.normalize("NFC", os.path.normcase(os.path.abspath(path)))


def find_recycle_record(volume_root: Any, original: Any, size: int, *, since: float) -> str | None:
    """The ``$I`` file recording ``original`` (same path and size, written at or after ``since``).

    ``since`` is a POSIX timestamp in seconds. The ``$R`` twin must exist too:
    an orphan ``$I`` does not prove where the file is. Folders that cannot be
    read (S-1-5-18) are skipped.
    """
    wanted = _comparable(str(original))
    extension = os.path.splitext(str(original))[1].casefold()
    bin_folder = os.path.join(str(volume_root), "$Recycle.Bin")
    best: tuple[float, str] | None = None
    try:
        folders = list(os.scandir(bin_folder))
    except OSError:
        return None
    for folder in folders:
        if not folder.name.upper().startswith("S-"):
            continue
        try:
            entries = list(os.scandir(folder.path))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if not name.upper().startswith("$I") or os.path.splitext(name)[1].casefold() != extension:
                continue
            try:
                modified = entry.stat().st_mtime
                if modified < since:
                    continue
                with open(entry.path, "rb") as handle:
                    data = handle.read(65536)
                _, record_size, _, recorded = parse_info_record(data)
            except (OSError, ValueError):
                continue
            if record_size != size or _comparable(recorded) != wanted:
                continue
            if not os.path.lexists(os.path.join(folder.path, "$R" + name[2:])):
                continue
            if best is None or modified > best[0]:
                best = (modified, entry.path)
    return None if best is None else best[1]
