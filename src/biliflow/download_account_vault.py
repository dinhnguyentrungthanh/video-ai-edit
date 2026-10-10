"""Each source's saved sign-in, encrypted, in a private folder (docs/SOURCE_ACCOUNTS_PLAN.md, M1).

``<root>/state/source-accounts/<user SID>/<source_id>/session-<generation>.bin``:

- ``<root>`` is the install root or a folder in its temp/ (``job_purge.allowed_project_root``). ``state``,
  ``source-accounts``, the user folder and the source folder must be real folders, never a link or a
  junction.
- One user folder per Windows account (its SID): BiliFlow run by another account has its own folder and
  simply finds no session (a new sign-in), and never touches the first account's folder.
- The user folder is created with the private ACL (``PrivateFolderAcl``: that account, SYSTEM,
  Administrators; nothing inherited) and read back on every write and read. A wrong ACL is set again only
  when a write needs the folder and its owner is one of those accounts; a read refuses it. Everything
  inside inherits the ACL, and each file is checked before it is read. ``source-accounts`` itself is a
  plain folder (it holds only the user folders).
- A file is ``MAGIC`` + a DPAPI blob (current user) of JSON ``{"v", "source", "generation",
  "authenticated_at", "state"}``, with the source id in the DPAPI entropy: a file copied to another
  source or renamed to another generation does not open there.
- A write goes to a ``.tmp`` beside the file, is flushed and checked, then renamed over it
  (``os.replace``, tried again briefly while an antivirus or indexer holds the file): a crash leaves the
  old file or the new one, never the plaintext.
- Errors are told apart. VaultMissing: no file. VaultCorrupt: a file that cannot be this session.
  VaultIOError: permission or disk (the file may be fine and is never deleted for it). VaultRefused:
  a root, a path, a link or an ACL the vault does not accept (nothing is read or written).
- ``remove`` and ``discard`` delete only this vault's own names (``session-<n>.bin``, its ``.tmp``) in
  one source folder, after the same path checks; they do not need the ACL to be right.
- The session browser's temporary profiles (M2a) live in ``<root>/temp/source-account-browser/<user SID>``,
  private like the user folder: ``new_browser_profile`` makes a ``run-<16 hex>`` folder there and
  ``remove_browser_profile(s)`` delete only such folders of that account (a link in their place is removed
  itself, never followed).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Protocol

from biliflow.download_account_config import WINDOWS_DEVICE_NAMES
from biliflow.download_account_winsec import (
    ADMINISTRATORS_SID,
    SID_PATTERN,
    SYSTEM_SID,
    AclReport,
    DpapiProtector,
    PrivateFolderAcl,
    ProtectorRejected,
)
from biliflow.download_provider_config import PROVIDER_ID
from biliflow.job_purge import allowed_project_root

VAULT_DIR = Path("state") / "source-accounts"
# The temporary profiles of the session browser (M2a): live cookies while it runs, so private like the vault.
BROWSER_DIR = Path("temp") / "source-account-browser"
MAGIC = b"BFSESSION1\n"
FORMAT_VERSION = 1
MAX_STATE_BYTES = 4 * 1024 * 1024  # the plaintext of one session
MAX_FILE_BYTES = MAX_STATE_BYTES + 1024 * 1024  # DPAPI adds far less
MAX_GENERATION = 10 ** 12
REPLACE_ATTEMPTS = 3
REPLACE_PAUSE_SECONDS = 0.1  # times the attempt number: 0.1 s, then 0.2 s
_SESSION_FILE = re.compile(r"session-([1-9][0-9]{0,12})\.bin")
_TEMP_FILE = re.compile(r"session-[1-9][0-9]{0,12}\.bin\.[0-9a-f]{16}\.tmp")
_PROFILE_DIR = re.compile(r"run-[0-9a-f]{16}")
PROFILE_PIN = ".biliflow-run"  # held open during a browser run (``pin_browser_profile``)
_ENTROPY = b"BiliFlow source-account session v1\0"


class VaultError(Exception):
    code = "SESSION_IO_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class VaultRefused(VaultError):
    code = "SESSION_REFUSED"


class VaultMissing(VaultError):
    code = "SESSION_MISSING"


class VaultCorrupt(VaultError):
    code = "SESSION_CORRUPT"


class VaultIOError(VaultError):
    code = "SESSION_IO_ERROR"


class Protector(Protocol):
    def protect(self, data: bytes, entropy: bytes) -> bytes: ...

    def unprotect(self, blob: bytes, entropy: bytes) -> bytes: ...


class FolderAcl(Protocol):
    user_sid: str

    def create_directory(self, path: Path) -> None: ...

    def secure(self, path: Path) -> None: ...

    def inspect(self, path: Path) -> AclReport: ...


@dataclass(frozen=True)
class StoredSession:
    source_id: str
    generation: int
    authenticated_at: str
    state: Mapping[str, Any] = field(repr=False)


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
                                              & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _name(generation: int) -> str:
    return f"session-{generation}.bin"


def _entropy(source_id: str) -> bytes:
    return _ENTROPY + source_id.encode("ascii")


def _check_id(source_id: object, generation: object = None) -> None:
    if not isinstance(source_id, str) or not PROVIDER_ID.fullmatch(source_id) or source_id in WINDOWS_DEVICE_NAMES:
        raise VaultRefused("Id nguồn không hợp lệ.")
    if generation is not None and (not isinstance(generation, int) or isinstance(generation, bool)
                                   or not 1 <= generation <= MAX_GENERATION):
        raise VaultRefused("Số phiên bản của phiên không hợp lệ.")


def _discard_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:  # removed at the next start or the next disconnect (``remove``)
        pass


def _replace(temp: Path, final: Path) -> None:
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            os.replace(temp, final)
            return
        except PermissionError:  # an antivirus or indexer may hold a fresh file for a moment
            if attempt == REPLACE_ATTEMPTS:
                raise
            time.sleep(REPLACE_PAUSE_SECONDS * attempt)


class SessionVault:
    """See the module docstring. ``protector`` and ``acl`` are replaceable in tests."""

    def __init__(self, root: Path, *, protector: Protector | None = None, acl: FolderAcl | None = None):
        self.root = Path(root)
        self.protector: Protector = protector or DpapiProtector()
        self._acl = acl

    @property
    def acl(self) -> FolderAcl:
        if self._acl is None:
            self._acl = PrivateFolderAcl()
        return self._acl

    # Paths -----------------------------------------------------------------------------------------

    def _root(self) -> Path:
        if not allowed_project_root(self.root):
            raise VaultRefused("Thư mục phiên chỉ được nằm trong thư mục cài BiliFlow hoặc một thư mục thử "
                               "trong temp của nó.")
        return Path(self.root).resolve()

    def _real_folder(self, path: Path, create: str | None) -> bool:
        """True when ``path`` is a real folder; False when it does not exist and ``create`` is None
        ("plain": a normal folder, "private": with the private ACL from its first moment)."""
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            if create is None:
                return False
            try:
                if create == "private":
                    self.acl.create_directory(path)
                else:
                    os.mkdir(path)
            except FileExistsError:
                pass  # made at the same moment: checked below like any folder
            except OSError:
                raise VaultIOError("Không tạo được thư mục phiên (quyền hoặc ổ đĩa).") from None
            try:
                info = os.lstat(path)
            except OSError:
                raise VaultIOError("Không đọc được thư mục phiên (quyền hoặc ổ đĩa).") from None
        except OSError:
            raise VaultIOError("Không đọc được thư mục phiên (quyền hoặc ổ đĩa).") from None
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise VaultRefused("Thư mục phiên là liên kết, junction hoặc không phải thư mục; không dùng.")
        return True

    def _inspect(self, path: Path) -> AclReport:
        try:
            return self.acl.inspect(path)
        except OSError:
            raise VaultIOError("Không đọc được quyền của thư mục phiên.") from None

    def _check_acl(self, path: Path, *, protected: bool) -> None:
        problems = self._inspect(path).problems(self.acl.user_sid, protected=protected)
        if problems:
            raise VaultRefused("Quyền truy cập của thư mục phiên không đúng: " + "; ".join(problems) + ".")

    def _check_base(self, base: Path, *, repair: bool) -> None:
        report = self._inspect(base)
        if not report.problems(self.acl.user_sid, protected=True):
            return
        owners = {self.acl.user_sid, SYSTEM_SID, ADMINISTRATORS_SID}
        if repair and report.owner in owners and not report.other_types:
            try:
                self.acl.secure(base)
            except OSError:
                raise VaultIOError("Không đặt được quyền riêng cho thư mục phiên.") from None
        self._check_acl(base, protected=True)

    @property
    def account_sid(self) -> str:
        """The Windows account whose folder this vault uses: the SID of this process's token
        (``PrivateFolderAcl``; a test's fake ACL may name another). Never read from a config or a request;
        the session manager scopes its database rows with it too."""
        sid = self.acl.user_sid
        if not isinstance(sid, str) or not SID_PATTERN.fullmatch(sid):
            raise VaultRefused("Không xác định được tài khoản Windows đang chạy BiliFlow.")
        return sid

    def source_folder(self, source_id: str) -> Path:
        """Where the files of ``source_id`` go for the Windows account running BiliFlow (no check of the
        folders on the disk: every read and write checks them)."""
        _check_id(source_id)
        return self._root() / VAULT_DIR / self.account_sid / source_id

    def browser_folder(self) -> Path:
        """``<root>/temp/source-account-browser/<user SID>``: where the session browser makes its temporary
        profile of one run (a fresh folder inside, which inherits the ACL). ``temp`` and
        ``source-account-browser`` are plain folders; the user folder gets the private ACL, checked like
        the vault's (created when missing, its ACL set again when its owner is one of the three accounts)."""
        base = self._root() / BROWSER_DIR / self.account_sid
        for plain in (base.parent.parent, base.parent):
            self._real_folder(plain, "plain")
        self._real_folder(base, "private")
        self._check_base(base, repair=True)
        if os.path.normcase(str(base.resolve())) != os.path.normcase(str(base)):
            raise VaultRefused("Đường dẫn thư mục trình duyệt đi qua một liên kết; không dùng.")
        return base

    def new_browser_profile(self) -> Path:
        """A fresh, empty profile folder ``run-<16 hex>`` for one browser run, made right after the account
        folder was checked again. A plain mkdir, so it inherits the private ACL (``tempfile.mkdtemp``'s 0o700
        would give it its own protected ACL on Windows instead); checked to be a real folder afterwards."""
        base = self.browser_folder()
        profile = base / f"run-{secrets.token_hex(8)}"
        try:
            os.mkdir(profile)
        except OSError:
            raise VaultIOError("Không tạo được thư mục tạm của trình duyệt (quyền hoặc ổ đĩa).") from None
        self._real_folder(profile, None)
        if os.path.normcase(str(profile.resolve())) != os.path.normcase(str(profile)):
            raise VaultRefused("Thư mục tạm của trình duyệt đi qua một liên kết; không dùng.")
        return profile

    def pin_browser_profile(self, profile: Path) -> BinaryIO:
        """Hold a fresh ``profile`` in place for one browser run. A file opened inside it (Python's ``open`` on
        Windows leaves out FILE_SHARE_DELETE) keeps the profile and every folder above it from being renamed
        or swapped for a junction while the handle is open; the whole path is checked again once it is held.
        The caller keeps the handle until the browser has closed, then closes it before
        ``remove_browser_profile``."""
        profile = Path(profile)
        base = self.browser_folder()
        if not _PROFILE_DIR.fullmatch(profile.name) or os.path.normcase(str(profile.parent)) != os.path.normcase(
                str(base)):
            raise VaultRefused("Chỉ giữ thư mục tạm của trình duyệt do BiliFlow tạo.")
        try:
            handle = open(profile / PROFILE_PIN, "xb")
        except OSError:
            raise VaultIOError("Không giữ được thư mục tạm của trình duyệt (quyền hoặc ổ đĩa).") from None
        try:
            held = self.browser_folder()  # links, owner and ACL, checked again while nothing above can move
            self._real_folder(profile, None)
            if (os.path.normcase(str(held)) != os.path.normcase(str(base))
                    or os.path.normcase(str(profile.resolve())) != os.path.normcase(str(profile))):
                raise VaultRefused("Thư mục tạm của trình duyệt đã bị thay; không dùng.")
        except BaseException:
            handle.close()
            raise
        return handle

    def remove_browser_profile(self, profile: Path) -> bool:
        """Delete one profile folder of this account (only a ``run-<16 hex>`` folder directly in the account's
        browser folder). A link in its place is removed itself, never followed. Tried a few times while Edge
        helpers or an antivirus still hold files. True when it is gone."""
        profile = Path(profile)
        if not _PROFILE_DIR.fullmatch(profile.name) or os.path.normcase(str(profile.parent)) != os.path.normcase(
                str(self.browser_folder())):
            raise VaultRefused("Chỉ xóa thư mục tạm của trình duyệt do BiliFlow tạo.")
        for attempt in range(1, REPLACE_ATTEMPTS + 1):
            try:
                info = os.lstat(profile)
            except FileNotFoundError:
                return True
            except OSError:
                info = None
            try:
                if info is not None and _is_link(info):  # the link goes, never what it points to
                    try:
                        os.rmdir(profile)
                    except NotADirectoryError:
                        os.unlink(profile)
                else:
                    shutil.rmtree(profile)
            except FileNotFoundError:
                return True
            except OSError:
                if attempt < REPLACE_ATTEMPTS:
                    time.sleep(REPLACE_PAUSE_SECONDS * attempt)
        return not os.path.lexists(profile)

    def remove_browser_profiles(self) -> int:
        """At start, before any browser run: delete the profile folders a killed run left behind. The number
        that could not be deleted (tried again at the next start); other names are left as they are."""
        base = self.browser_folder()
        try:
            names = [entry.name for entry in os.scandir(base) if _PROFILE_DIR.fullmatch(entry.name)]
        except OSError:
            raise VaultIOError("Không đọc được thư mục tạm của trình duyệt.") from None
        return sum(1 for name in names if not self.remove_browser_profile(base / name))

    def _folder(self, source_id: str, *, create: bool, check_acl: bool = True) -> Path | None:
        """The real folder of one source (created with ``create``); None when it does not exist."""
        folder = self.source_folder(source_id)
        base = folder.parent
        for plain in (base.parent.parent, base.parent):  # state, source-accounts
            if not self._real_folder(plain, "plain" if create else None):
                return None
        if not self._real_folder(base, "private" if create else None):
            return None
        if check_acl:
            self._check_base(base, repair=create)
        if not self._real_folder(folder, "plain" if create else None):
            return None
        if check_acl:
            self._check_acl(folder, protected=False)
        if os.path.normcase(str(folder.resolve())) != os.path.normcase(str(folder)):
            raise VaultRefused("Đường dẫn thư mục phiên đi qua một liên kết; không dùng.")
        return folder

    # Files -----------------------------------------------------------------------------------------

    def write(self, source_id: str, generation: int, authenticated_at: str, state: Mapping[str, Any]) -> None:
        """Save one generation of a source's session (atomic; see the module docstring)."""
        _check_id(source_id, generation)
        try:
            plaintext = json.dumps({"v": FORMAT_VERSION, "source": source_id, "generation": generation,
                                    "authenticated_at": authenticated_at, "state": state},
                                   ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            raise VaultRefused("Phiên không ghi được dưới dạng JSON.") from None
        if len(plaintext) > MAX_STATE_BYTES:
            raise VaultRefused(f"Phiên lớn quá {MAX_STATE_BYTES // (1024 * 1024)} MiB; không lưu.")
        folder = self._folder(source_id, create=True)
        assert folder is not None
        try:
            blob = MAGIC + self.protector.protect(plaintext, _entropy(source_id))
        except OSError:
            raise VaultIOError("Windows không mã hóa được phiên.") from None
        final = folder / _name(generation)
        temp = folder / f"{_name(generation)}.{secrets.token_hex(8)}.tmp"
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
            handle = os.open(temp, flags, 0o600)
            try:
                view = memoryview(blob)
                while view:
                    view = view[os.write(handle, view):]
                os.fsync(handle)
            finally:
                os.close(handle)
            self._check_acl(temp, protected=False)
            _replace(temp, final)
        except VaultError:
            _discard_file(temp)
            raise
        except OSError:
            _discard_file(temp)
            raise VaultIOError("Không ghi được file phiên (quyền hoặc ổ đĩa).") from None

    def read(self, source_id: str, generation: int) -> StoredSession:
        _check_id(source_id, generation)
        folder = self._folder(source_id, create=False)
        if folder is None:
            raise VaultMissing("Chưa có phiên đã lưu.")
        path = folder / _name(generation)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            raise VaultMissing("Không thấy file phiên đã lưu.") from None
        except OSError:
            raise VaultIOError("Không đọc được file phiên (quyền hoặc ổ đĩa); phiên chưa bị xóa.") from None
        if _is_link(info) or not stat.S_ISREG(info.st_mode):
            raise VaultRefused("File phiên là liên kết hoặc không phải file; không dùng.")
        if info.st_size > MAX_FILE_BYTES:
            raise VaultCorrupt("File phiên lớn bất thường.")
        self._check_acl(path, protected=False)
        try:
            with open(path, "rb") as handle:
                raw = handle.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            raise VaultMissing("Không thấy file phiên đã lưu.") from None
        except OSError:
            raise VaultIOError("Không đọc được file phiên (quyền hoặc ổ đĩa); phiên chưa bị xóa.") from None
        return self._decode(raw, source_id, generation)

    def _decode(self, raw: bytes, source_id: str, generation: int) -> StoredSession:
        if not raw.startswith(MAGIC) or len(raw) > MAX_FILE_BYTES:
            raise VaultCorrupt("File phiên không đúng dạng.")
        try:
            plaintext = self.protector.unprotect(raw[len(MAGIC):], _entropy(source_id))
        except ProtectorRejected:
            raise VaultCorrupt("Không giải mã được phiên: file hỏng, của nguồn khác hoặc của tài khoản Windows "
                               "khác.") from None
        except OSError:
            raise VaultIOError("Windows tạm thời không giải mã được phiên; phiên chưa bị xóa.") from None
        try:
            envelope = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise VaultCorrupt("Nội dung phiên không đúng dạng.") from None
        if (not isinstance(envelope, dict) or envelope.get("v") != FORMAT_VERSION
                or envelope.get("source") != source_id or envelope.get("generation") != generation
                or not isinstance(envelope.get("authenticated_at"), str)
                or not isinstance(envelope.get("state"), dict)):
            raise VaultCorrupt("Phiên đã lưu không khớp nguồn hay phiên bản.")
        return StoredSession(source_id, generation, envelope["authenticated_at"], envelope["state"])

    def exists(self, source_id: str, generation: int) -> bool:
        """The file of that generation is there (no decryption)."""
        _check_id(source_id, generation)
        folder = self._folder(source_id, create=False)
        if folder is None:
            return False
        try:
            info = os.lstat(folder / _name(generation))
        except FileNotFoundError:
            return False
        except OSError:
            raise VaultIOError("Không đọc được file phiên (quyền hoặc ổ đĩa).") from None
        return stat.S_ISREG(info.st_mode) and not _is_link(info)

    def remove(self, source_id: str, *, keep: int | None = None) -> int:
        """Delete this vault's own files of one source (every ``session-<n>.bin`` but ``keep``, and every
        unfinished ``.tmp``); other names, links and folders are left. The number deleted; VaultIOError when
        one of them could not be deleted (the next start or disconnect tries again)."""
        _check_id(source_id)
        folder = self._folder(source_id, create=False, check_acl=False)
        if folder is None:
            return 0
        try:
            entries = list(os.scandir(folder))
        except FileNotFoundError:
            return 0
        except OSError:
            raise VaultIOError("Không đọc được thư mục phiên (quyền hoặc ổ đĩa).") from None
        removed = failures = 0
        for entry in entries:
            session = _SESSION_FILE.fullmatch(entry.name)
            if not (session or _TEMP_FILE.fullmatch(entry.name)):
                continue
            if session and keep is not None and int(session.group(1)) == keep:
                continue
            try:
                info = entry.stat(follow_symlinks=False)
                if _is_link(info) or not stat.S_ISREG(info.st_mode):
                    failures += 1
                    continue
                os.unlink(entry.path)
                removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                failures += 1
        if failures:
            raise VaultIOError(f"Không xóa được {failures} file phiên cũ; BiliFlow sẽ thử lại.")
        return removed

    def discard(self, source_id: str, generation: int) -> None:
        """Delete the file of one generation that was written but never committed (best effort)."""
        _check_id(source_id, generation)
        folder = self._folder(source_id, create=False, check_acl=False)
        if folder is not None:
            path = folder / _name(generation)
            try:
                info = os.lstat(path)
            except OSError:
                return
            if stat.S_ISREG(info.st_mode) and not _is_link(info):
                _discard_file(path)
