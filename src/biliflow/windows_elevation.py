"""Run one program as administrator after the Windows UAC prompt, and wait for its exit code.

Used only by tailscale_manager (docs/TAILSCALE_PLAN.md) to start scripts/tailscale-setup.ps1 when
the user presses install, start or firewall on the PC. ShellExecuteExW with the "runas" verb shows
the UAC prompt; declining it raises ElevationDeclined. Standard library only (ctypes).
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SW_HIDE = 0
ERROR_CANCELLED = 1223
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
COINIT_APARTMENTTHREADED = 0x2
COINIT_DISABLE_OLE1DDE = 0x4


class ElevationDeclined(Exception):
    """The user answered "No" to the UAC prompt (or closed it)."""


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _api() -> tuple[ctypes.WinDLL, ctypes.WinDLL, ctypes.WinDLL, ctypes.WinDLL]:
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32")
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    return shell32, kernel32, user32, ole32


def run_elevated(exe: str, params: str, *, timeout: float = 900.0, verb: str = "runas") -> int:
    """Start `exe params` hidden with `verb` ("runas": the UAC prompt) and return its exit code.

    The prompt is parented to the foreground window (the browser where the user clicked), so it
    comes to the front instead of only blinking in the taskbar. `verb="open"` exists for tests.
    """
    if sys.platform != "win32":
        raise OSError("Chỉ chạy được trên Windows")
    shell32, kernel32, user32, ole32 = _api()
    com = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)
    try:
        info = SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
        info.hwnd = user32.GetForegroundWindow()
        info.lpVerb, info.lpFile, info.lpParameters, info.nShow = verb, exe, params, SW_HIDE
        if not shell32.ShellExecuteExW(ctypes.byref(info)):
            error = ctypes.get_last_error()
            if error == ERROR_CANCELLED:
                raise ElevationDeclined()
            raise ctypes.WinError(error)
        if not info.hProcess:
            raise OSError("Windows không trả về tiến trình vừa chạy")
        try:
            waited = kernel32.WaitForSingleObject(info.hProcess, max(0, int(timeout * 1000)))
            if waited == WAIT_TIMEOUT:
                raise TimeoutError(f"Quá {int(timeout)} giây mà bước chạy quyền Admin chưa xong")
            if waited != WAIT_OBJECT_0:
                raise ctypes.WinError(ctypes.get_last_error())
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return int(code.value)
        finally:
            kernel32.CloseHandle(info.hProcess)
    finally:
        if com in (0, 1):  # S_OK, S_FALSE: this call initialised COM on the thread
            ole32.CoUninitialize()
