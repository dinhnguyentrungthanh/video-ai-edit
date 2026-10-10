"""Windows security of the source-account vault: DPAPI for the current user and a private folder ACL.

- DPAPI: ``CryptProtectData`` / ``CryptUnprotectData`` with ``CRYPTPROTECT_UI_FORBIDDEN`` only, never
  ``CRYPTPROTECT_LOCAL_MACHINE``: only the Windows user who saved a session can open it; another account
  (a sandbox account included) cannot. A program that runs as that same user can, which DPAPI cannot stop.
- ACL: the vault folder is created with a protected DACL (nothing inherited from its parent) that allows
  only the user running BiliFlow, SYSTEM and Administrators, with full control inherited by everything
  created inside. ``inspect`` reads the DACL and the owner back, so a caller can check what is really set.

``ctypes`` only: no package is added. On another system every call raises OSError.
"""
from __future__ import annotations

import ctypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4  # never used: any account of the PC could open the data
PROTECT_FLAGS = CRYPTPROTECT_UI_FORBIDDEN
# CryptUnprotectData errors of the machine, not of the data (memory, the RPC service): they may pass, so
# the session is not given up for them. Every other failure means "these bytes cannot be opened here".
# Flipping each byte of a blob in turn gave 13, 0x8009000B, 1325 and 0xD000000D, so a damaged, cut or
# foreign blob has no single error code.
_ENVIRONMENT_ERRORS = frozenset({8, 14, 1450, 1722, 1723, 1726})

SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
# The text form of a SID ("S-1-5-21-…"): the account part of a vault folder and of an account row.
SID_PATTERN = re.compile(r"S-1-[0-9]{1,14}(?:-[0-9]{1,10}){1,15}")
FILE_ALL_ACCESS = 0x1F01FF
GENERIC_ALL = 0x10000000
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x1
_DACL_SECURITY_INFORMATION = 0x4
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SE_DACL_PROTECTED = 0x1000
_ACCESS_ALLOWED_ACE_TYPE, _ACCESS_DENIED_ACE_TYPE = 0, 1
_TOKEN_QUERY, _TOKEN_USER = 0x0008, 1
_ERROR_ALREADY_EXISTS, _ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND = 183, 2, 3


class ProtectorRejected(Exception):
    """DPAPI cannot open these bytes here: damaged, another source's entropy, or another user's key."""


class _Api:
    """The Windows functions, loaded on first use (``ctypes.WinDLL`` exists on Windows only)."""

    def __init__(self) -> None:
        from ctypes import wintypes

        if os.name != "nt":
            raise OSError("Windows security functions are only available on Windows")
        self.w = wintypes
        self.crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                        ("bInheritHandle", wintypes.BOOL)]

        class AclSizeInformation(ctypes.Structure):
            _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                        ("AclBytesFree", wintypes.DWORD)]

        class AceHeader(ctypes.Structure):
            _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", wintypes.WORD)]

        self.DataBlob, self.SecurityAttributes = DataBlob, SecurityAttributes
        self.AclSizeInformation, self.AceHeader = AclSizeInformation, AceHeader
        blob_p = ctypes.POINTER(DataBlob)
        void_pp = ctypes.POINTER(ctypes.c_void_p)
        self._declare(self.crypt32.CryptProtectData, wintypes.BOOL,
                      [blob_p, wintypes.LPCWSTR, blob_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_p])
        self._declare(self.crypt32.CryptUnprotectData, wintypes.BOOL,
                      [blob_p, ctypes.c_void_p, blob_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_p])
        self._declare(self.kernel32.LocalFree, ctypes.c_void_p, [ctypes.c_void_p])
        self._declare(self.kernel32.GetCurrentProcess, wintypes.HANDLE, [])
        self._declare(self.kernel32.CloseHandle, wintypes.BOOL, [wintypes.HANDLE])
        self._declare(self.kernel32.CreateDirectoryW, wintypes.BOOL,
                      [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)])
        self._declare(self.advapi32.OpenProcessToken, wintypes.BOOL,
                      [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)])
        self._declare(self.advapi32.GetTokenInformation, wintypes.BOOL,
                      [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                       ctypes.POINTER(wintypes.DWORD)])
        self._declare(self.advapi32.ConvertSidToStringSidW, wintypes.BOOL,
                      [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)])
        self._declare(self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW, wintypes.BOOL,
                      [wintypes.LPCWSTR, wintypes.DWORD, void_pp, ctypes.POINTER(wintypes.ULONG)])
        self._declare(self.advapi32.GetSecurityDescriptorDacl, wintypes.BOOL,
                      [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL), void_pp, ctypes.POINTER(wintypes.BOOL)])
        self._declare(self.advapi32.SetNamedSecurityInfoW, wintypes.DWORD,
                      [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p])
        self._declare(self.advapi32.GetNamedSecurityInfoW, wintypes.DWORD,
                      [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, void_pp, void_pp, void_pp, void_pp,
                       void_pp])
        self._declare(self.advapi32.GetSecurityDescriptorControl, wintypes.BOOL,
                      [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)])
        self._declare(self.advapi32.GetAclInformation, wintypes.BOOL,
                      [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int])
        self._declare(self.advapi32.GetAce, wintypes.BOOL, [ctypes.c_void_p, wintypes.DWORD, void_pp])

    @staticmethod
    def _declare(function: Any, restype: Any, argtypes: list[Any]) -> None:
        function.restype = restype
        function.argtypes = argtypes


_API: _Api | None = None


def _api() -> _Api:
    global _API
    if _API is None:
        _API = _Api()
    return _API


def _last_error() -> OSError:
    code = ctypes.get_last_error()
    return OSError(0, ctypes.FormatError(code).strip(), None, code)


def _in_blob(api: _Api, data: bytes) -> tuple[Any, Any]:
    """A DATA_BLOB over a copy of ``data`` and the buffer that keeps it alive."""
    buffer = (ctypes.c_ubyte * max(1, len(data))).from_buffer_copy(data or b"\0")
    return api.DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


class DpapiProtector:
    """Encrypts for the current Windows user; see the module docstring."""

    def protect(self, data: bytes, entropy: bytes) -> bytes:
        api = _api()
        data_in, data_buffer = _in_blob(api, data)
        salt, _salt_buffer = _in_blob(api, entropy)
        out = api.DataBlob()
        try:
            if not api.crypt32.CryptProtectData(ctypes.byref(data_in), None, ctypes.byref(salt), None, None,
                                                PROTECT_FLAGS, ctypes.byref(out)):
                raise _last_error()
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.memset(data_buffer, 0, ctypes.sizeof(data_buffer))  # no plaintext left in this copy
            if out.pbData:
                api.kernel32.LocalFree(out.pbData)

    def unprotect(self, blob: bytes, entropy: bytes) -> bytes:
        """The plaintext; OSError for an error of the machine that may pass, else ProtectorRejected."""
        api = _api()
        data_in, _data_buffer = _in_blob(api, blob)
        salt, _salt_buffer = _in_blob(api, entropy)
        out = api.DataBlob()
        try:
            if not api.crypt32.CryptUnprotectData(ctypes.byref(data_in), None, ctypes.byref(salt), None, None,
                                                  PROTECT_FLAGS, ctypes.byref(out)):
                error = _last_error()
                if (error.winerror or 0) & 0xFFFFFFFF in _ENVIRONMENT_ERRORS:
                    raise error
                raise ProtectorRejected(f"DPAPI error {(error.winerror or 0) & 0xFFFFFFFF:#x}")
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            if out.pbData:
                ctypes.memset(out.pbData, 0, out.cbData)
                api.kernel32.LocalFree(out.pbData)


def current_user_sid() -> str:
    """The SID of the Windows user this process runs as (``S-1-5-21-…``)."""
    api = _api()
    token = api.w.HANDLE()
    if not api.advapi32.OpenProcessToken(api.kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _last_error()
    try:
        size = api.w.DWORD()
        api.advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not api.advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, size, ctypes.byref(size)):
            raise _last_error()
        return _sid_string(api, ctypes.c_void_p.from_buffer(buffer).value)  # TOKEN_USER.User.Sid comes first
    finally:
        api.kernel32.CloseHandle(token)


def _sid_string(api: _Api, sid: int | None) -> str:
    text = api.w.LPWSTR()
    if not sid or not api.advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise _last_error()
    try:
        return text.value or ""
    finally:
        api.kernel32.LocalFree(text)


@dataclass(frozen=True)
class AclReport:
    """What a path's security descriptor really says."""
    owner: str
    protected: bool
    allowed: tuple[tuple[str, int], ...]  # (SID, access mask) of every allow entry
    other_types: int  # entries that are neither allow nor deny (callback, object, audit…)

    def problems(self, user_sid: str, *, protected: bool) -> list[str]:
        """Why this is not the private ACL (empty when it is)."""
        allowed_sids = {user_sid, SYSTEM_SID, ADMINISTRATORS_SID}
        found: list[str] = []
        if protected and not self.protected:
            found.append("ACL vẫn kế thừa từ thư mục cha")
        strangers = sorted({sid for sid, _mask in self.allowed if sid not in allowed_sids})
        if strangers:
            found.append(f"ACL cho thêm {len(strangers)} tài khoản khác")
        if self.other_types:
            found.append("ACL có mục lạ")
        if not any(sid == user_sid and (mask & FILE_ALL_ACCESS == FILE_ALL_ACCESS or mask & GENERIC_ALL)
                   for sid, mask in self.allowed):
            found.append("tài khoản đang chạy BiliFlow thiếu quyền đầy đủ")
        if self.owner not in allowed_sids:
            found.append("chủ sở hữu là tài khoản khác")
        return found


class PrivateFolderAcl:
    """Creates, sets and reads the private ACL (user running BiliFlow + SYSTEM + Administrators)."""

    def __init__(self, user_sid: str | None = None):
        self.user_sid = user_sid or current_user_sid()

    @property
    def sddl(self) -> str:
        # D:P = protected (no inherited entry); OICI = inherited by files and folders created inside; FA = full.
        return f"D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{self.user_sid})"

    def _descriptor(self, api: _Api) -> ctypes.c_void_p:
        descriptor = ctypes.c_void_p()
        if not api.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(self.sddl, 1,
                                                                                 ctypes.byref(descriptor), None):
            raise _last_error()
        return descriptor

    def create_directory(self, path: Path) -> None:
        """A new folder that has the private ACL from its first moment; FileExistsError when it exists."""
        api = _api()
        descriptor = self._descriptor(api)
        try:
            attributes = api.SecurityAttributes(ctypes.sizeof(api.SecurityAttributes), descriptor.value, False)
            if not api.kernel32.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                error = _last_error()
                if error.winerror == _ERROR_ALREADY_EXISTS:
                    raise FileExistsError(str(path))
                if error.winerror in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
                    raise FileNotFoundError(str(path))
                raise error
        finally:
            api.kernel32.LocalFree(descriptor)

    def secure(self, path: Path) -> None:
        """Replace the DACL of ``path`` with the private one (protected); the children inherit it."""
        api = _api()
        descriptor = self._descriptor(api)
        try:
            present, defaulted, dacl = api.w.BOOL(), api.w.BOOL(), ctypes.c_void_p()
            if not api.advapi32.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                                                          ctypes.byref(defaulted)):
                raise _last_error()
            code = api.advapi32.SetNamedSecurityInfoW(
                str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None, None, dacl, None)
            if code:
                raise OSError(0, ctypes.FormatError(code).strip(), str(path), code)
        finally:
            api.kernel32.LocalFree(descriptor)

    def inspect(self, path: Path) -> AclReport:
        api = _api()
        owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        code = api.advapi32.GetNamedSecurityInfoW(
            str(path), _SE_FILE_OBJECT, _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if code:
            raise OSError(0, ctypes.FormatError(code).strip(), str(path), code)
        try:
            control, revision = api.w.WORD(), api.w.DWORD()
            if not api.advapi32.GetSecurityDescriptorControl(descriptor, ctypes.byref(control),
                                                             ctypes.byref(revision)):
                raise _last_error()
            protected = bool(control.value & _SE_DACL_PROTECTED)
            if not dacl.value:  # a NULL DACL lets everyone in
                return AclReport(_sid_string(api, owner.value), protected, (("S-1-1-0", GENERIC_ALL),), 0)
            info = api.AclSizeInformation()
            if not api.advapi32.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
                raise _last_error()
            allowed: list[tuple[str, int]] = []
            other_types = 0
            for index in range(info.AceCount):
                ace = ctypes.c_void_p()
                if not api.advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                    raise _last_error()
                kind = api.AceHeader.from_address(ace.value).AceType
                if kind == _ACCESS_ALLOWED_ACE_TYPE:  # header (4 bytes), mask (4 bytes), then the SID
                    allowed.append((_sid_string(api, ace.value + 8), api.w.DWORD.from_address(ace.value + 4).value))
                elif kind != _ACCESS_DENIED_ACE_TYPE:  # a deny entry only takes access away
                    other_types += 1
            return AclReport(_sid_string(api, owner.value), protected, tuple(allowed), other_types)
        finally:
            api.kernel32.LocalFree(descriptor)
