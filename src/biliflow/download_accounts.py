"""Session manager of the source accounts (docs/SOURCE_ACCOUNTS_PLAN.md, M1): one session per source.

Nothing here opens a browser or calls a site. What M2 (browser) and M4 (queue, API) use:

- ``begin_login`` → LoginAttempt: the user pressed Đăng nhập. The source shows LOGGING_IN while the
  window may be open, at most LOGIN_ATTEMPT_MAX_SECONDS; a session saved before stays usable meanwhile.
- ``complete_login(attempt, storage_state)``: the browser saw evidence of a sign-in. Only what belongs to
  the source's own hosts is kept (``own_state``), encrypted into the vault under the next generation
  first, then the row is committed (CONNECTED). A failed save leaves the source as it was: never
  CONNECTED on a session that was not saved.
- ``end_login(attempt, code)``: cancelled, window closed, timed out or failed; never a success.
- ``cancel_login`` and ``disconnect``: the user's buttons. Both end an open sign-in, so its late
  callback is refused (StaleLogin); ``disconnect`` also bumps the generation and deletes the files.
- ``session_for`` → SessionLease (generation + state, in memory only), or LoginRequired (no session,
  expired, rejected, missing or unreadable file), or SessionUnavailable (permission, disk or ACL: not a
  sign-in problem, and the session is kept).
- ``save_rotated(lease, state)``: cookies rotated during a hidden run; same generation and same
  ``authenticated_at``.
- ``mark_invalid(lease)`` and ``record_check(lease, outcome)``: evidence about a session; ignored for another
  generation.
- A LoginAttempt and a SessionLease carry their owner: the manager's project root and Windows account
  (M2a). A manager refuses, before reading or writing anything, an attempt or a lease that another manager
  gave out, even for the same source id and generation, so the browser result of one root or account is
  never saved into, or held against, another one.
- ``recover`` at start: an open sign-in cannot survive a restart (LOGIN_INTERRUPTED); an ACTIVE session
  whose file is gone needs a sign-in; files of other generations and unfinished .tmp files go. It returns
  the sources it could not settle (the caller logs them; nothing is guessed).

Scope: a manager acts for one Windows account, the one this process runs as (``account_sid``, read by the
vault from the process token, never from a config or a request). Its database rows (``AccountStore``) and
its vault folder both belong to that account, so every action above (status, sign-in, cancel, disconnect,
``session_for``, rotation, checks, compare-and-set, ``recover``) sees and changes only that account's
state and files. Another account using the same install, before or after it, starts from NOT_CONNECTED
with its own generations and never lowers, replaces or cleans up the first account's session.

One AccountManager per project root and process (the Control Center's); two managers of one root and
account in one process share the per-source locks. Rows and files of a source removed from the config are left as they
are (encrypted, never used) until the source is configured again.

Session age: for an adapter without a real check ("ttl") a session is usable while
0 <= now − authenticated_at < FALLBACK_TTL_SECONDS. Nothing extends it: reading the status, checks and
cookie rotation never write ``authenticated_at``. A clock set back before the sign-in asks for a new one.

Secrets stay out of the database, the status and every message: the status says only the state, times
and an error code with its fixed Vietnamese text.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from biliflow.download_account_config import AccountConfig, SourceAccount
from biliflow.download_account_store import AccountStore, utc_now
from biliflow.download_account_vault import SessionVault, VaultCorrupt, VaultError, VaultMissing
from biliflow.job_purge import allowed_project_root

FALLBACK_TTL_SECONDS = 3600
LOGIN_ATTEMPT_MAX_SECONDS = 10 * 60  # the sign-in window's own limit (plan 9.3); unrelated to the TTL
DISCONNECT_ATTEMPTS = 3  # compare-and-set tries; only another writer of the same row makes one fail
STATES = ("NOT_CONNECTED", "LOGGING_IN", "CONNECTED", "NEEDS_LOGIN", "CHECK_FAILED")
LOGIN_END_CODES = frozenset({"LOGIN_CANCELLED", "LOGIN_WINDOW_CLOSED", "LOGIN_TIMEOUT", "LOGIN_FAILED"})
CHECK_OUTCOMES = frozenset({"valid", "invalid", "unreachable"})
MESSAGES = {
    "ACCOUNT_UNKNOWN": "Nguồn này chưa được cấu hình.",
    "NOT_CONNECTED": "Chưa kết nối nguồn này.",
    "LOGIN_BUSY": "Cửa sổ đăng nhập của nguồn này đang mở.",
    "LOGIN_UNSUPPORTED": "BiliFlow chưa biết cách xác nhận đăng nhập của nguồn này; chưa mở cửa sổ.",
    "ACCOUNT_BUSY": "Nguồn này đang được thay đổi ở nơi khác; hãy thử lại.",
    "LOGIN_STALE": "Lượt đăng nhập này đã bị hủy hoặc thay bằng lượt khác.",
    "LOGIN_CANCELLED": "Đã hủy đăng nhập; phiên cũ (nếu có) giữ nguyên.",
    "LOGIN_WINDOW_CLOSED": "Cửa sổ đăng nhập đã đóng trước khi đăng nhập xong.",
    "LOGIN_TIMEOUT": "Hết thời gian chờ đăng nhập.",
    "LOGIN_FAILED": "Không ghi nhận được đăng nhập thành công.",
    "LOGIN_INTERRUPTED": "BiliFlow khởi động lại trong lúc đăng nhập; hãy đăng nhập lại.",
    "SESSION_SAVE_FAILED": "Không lưu được phiên đăng nhập; chưa kết nối bằng phiên mới.",
    "SESSION_EXPIRED": "Đã quá 1 giờ từ lần đăng nhập; cần đăng nhập lại.",
    "CLOCK_CHANGED": "Đồng hồ máy đang trước lần đăng nhập; cần đăng nhập lại.",
    "SESSION_REJECTED": "Nguồn báo phiên không còn hợp lệ; cần đăng nhập lại.",
    "SESSION_CORRUPT": "Không mở được phiên đã lưu (hỏng hoặc của tài khoản Windows khác); cần đăng nhập lại.",
    "SESSION_MISSING": "Không thấy file phiên đã lưu; cần đăng nhập lại.",
    "SESSION_IO_ERROR": "Không đọc được phiên do quyền hoặc ổ đĩa; phiên chưa bị xóa.",
    "SESSION_REFUSED": "Thư mục phiên không an toàn (liên kết hoặc quyền sai); chưa dùng phiên.",
    "SESSION_REMOVE_FAILED": "Đã ngắt kết nối nhưng chưa xóa được file phiên cũ; BiliFlow sẽ thử lại.",
    "CHECK_UNREACHABLE": "Không kiểm tra được kết nối (lỗi mạng); phiên chưa bị coi là hết hạn.",
}


class AccountError(Exception):
    """A refused account action; ``message`` is the fixed text of ``code``."""

    def __init__(self, code: str, source_id: str | None = None):
        self.code = code
        self.message = MESSAGES.get(code, code)
        self.source_id = source_id
        super().__init__(self.message)


class AccountUnknown(AccountError):
    pass


class AccountBusy(AccountError):
    pass


class StaleLogin(AccountError):
    """A sign-in that was cancelled, replaced, timed out or ended by a disconnect: nothing is saved."""


class SessionSaveFailed(AccountError):
    pass


class SessionUnavailable(AccountError):
    """The saved session cannot be read now (permission, disk, ACL); it is kept and not invalidated."""


class LoginRequired(AccountError):
    """The source needs the user to sign in again (M4: its tasks wait without a slot)."""

    def __init__(self, code: str, source_id: str, generation: int | None):
        super().__init__(code, source_id)
        self.generation = generation


@dataclass(frozen=True)
class LoginAttempt:
    """One sign-in; ``owner`` is (project root, Windows account) of the manager that began it."""
    source_id: str
    attempt_id: str = field(repr=False)
    started_at: datetime
    owner: tuple[str, str] = field(repr=False)


@dataclass(frozen=True)
class SessionLease:
    """A session for one hidden run: kept in memory only, tied to its generation and to the manager that
    gave it out (``owner``: project root, Windows account)."""
    source_id: str
    generation: int
    authenticated_at: datetime
    state: Mapping[str, Any] = field(repr=False)
    owner: tuple[str, str] = field(repr=False)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def check_storage_state(state: object) -> dict[str, Any]:
    """The context state M2 hands over, shaped like Playwright's ``storage_state()``:
    ``{"cookies": [{"name", "value", "domain", "path", ...}], "origins": [{"origin", ...}]}``.
    ValueError (without any value in its text) for anything else."""
    if not isinstance(state, Mapping):
        raise ValueError("storage state must be an object")
    cookies, origins = state.get("cookies"), state.get("origins", [])
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise ValueError("storage state needs a cookie list and an origin list")
    for cookie in cookies:
        if not isinstance(cookie, Mapping) or not all(isinstance(cookie.get(key), str)
                                                      for key in ("name", "value", "domain", "path")):
            raise ValueError("a cookie lacks its name, value, domain or path")
    for origin in origins:
        if not isinstance(origin, Mapping) or not isinstance(origin.get("origin"), str):
            raise ValueError("an origin entry lacks its origin")
    return dict(state)


def _cookie_reaches(domain: str, hosts: frozenset[str]) -> bool:
    """A cookie of ``domain`` would be sent to one of ``hosts``. Playwright writes a domain cookie as
    ".site" (sent to site and its subdomains) and a host-only cookie as "site" (sent to site only)."""
    name = domain.strip().lower().rstrip(".")
    if name.startswith("."):
        name = name[1:]
        return bool(name) and any(host == name or host.endswith("." + name) for host in hosts)
    return name in hosts


def _origin_host(origin: str) -> str | None:
    """The host of an https origin on its default port, else None."""
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or port is not None or parts.username is not None or parts.password is not None:
        return None
    return (parts.hostname or "").rstrip(".") or None


def own_state(source: SourceAccount, state: Mapping[str, Any]) -> dict[str, Any]:
    """Only what belongs to the source's own hosts (a ``check_storage_state`` result): the cookies a browser
    would send to one of them and the storage of their https origins. Whatever else the browser context
    holds (an identity provider, an advert, another site) is never saved. If M2 shows that a source needs
    the cookies of a separate sign-in host, that host goes into the source's config, not past this filter."""
    hosts = source.all_hosts
    return {"cookies": [cookie for cookie in state["cookies"] if _cookie_reaches(cookie["domain"], hosts)],
            "origins": [origin for origin in state.get("origins", []) if _origin_host(origin["origin"]) in hosts]}


# Per-source locks of every manager in this process, by project root and Windows account: two managers of
# one root and account (a test's "restart", a second caller by mistake) never run two changes of one
# source at once. Another account has its own rows and folder, so it needs no lock of the first one.
_LOCKS_GUARD = threading.Lock()
_SOURCE_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}


class AccountManager:
    """See the module docstring. One lock per source: actions of two sources never wait for each other."""

    def __init__(self, root: Path, config: AccountConfig, *, store: AccountStore | None = None,
                 vault: SessionVault | None = None, clock: Callable[[], datetime] = utc_now):
        if not allowed_project_root(root):  # checked before the database or any folder is made
            raise ValueError("Source accounts live only in the install root or a test folder in its temp/")
        self.root = Path(root)
        self._root_key = os.path.normcase(str(self.root.resolve()))
        self.config = config
        self.clock = clock
        self.vault = vault or SessionVault(self.root)
        # The Windows account this process runs as (the vault reads it from the process token): its rows
        # and its vault folder are the only ones this manager reads or changes.
        self.account_sid = self.vault.account_sid
        self.store = store or AccountStore(self.root / "state" / "downloads.sqlite3", self.account_sid, clock=clock)
        if self.store.account_sid != self.account_sid:
            raise ValueError("The account store and the vault belong to different Windows accounts")
        self._owner = (self._root_key, self.account_sid)

    def close(self) -> None:
        self.store.close()

    @property
    def owner(self) -> tuple[str, str]:
        """(project root, Windows account): what this manager's attempts and leases carry."""
        return self._owner

    def owns(self, item: LoginAttempt | SessionLease) -> bool:
        """``item`` was given out by a manager of this project root and Windows account."""
        return item.owner == self._owner

    def _source(self, source_id: str) -> SourceAccount:
        source = self.config.sources.get(source_id)
        if source is None:
            raise AccountUnknown("ACCOUNT_UNKNOWN", source_id)
        return source

    def _lock(self, source_id: str) -> threading.Lock:
        with _LOCKS_GUARD:
            return _SOURCE_LOCKS.setdefault((self._root_key, self.account_sid, source_id), threading.Lock())

    # Reading ---------------------------------------------------------------------------------------

    @staticmethod
    def _attempt_open(row: Mapping[str, Any], now: datetime) -> bool:
        started = _parse(row.get("login_started_at"))
        return (bool(row.get("login_attempt")) and started is not None
                and 0 <= (now - started).total_seconds() < LOGIN_ATTEMPT_MAX_SECONDS)

    @staticmethod
    def _too_old(source: SourceAccount, row: Mapping[str, Any], now: datetime) -> str | None:
        """Why an ACTIVE session of a "ttl" adapter needs a new sign-in (None while it is usable)."""
        if source.adapter.session_check != "ttl":
            return None
        signed = _parse(row.get("authenticated_at"))
        if signed is None:
            return "SESSION_CORRUPT"
        age = (now - signed).total_seconds()
        if age < 0:
            return "CLOCK_CHANGED"
        return "SESSION_EXPIRED" if age >= FALLBACK_TTL_SECONDS else None

    def _public(self, source: SourceAccount, row: Mapping[str, Any] | None, now: datetime) -> dict[str, Any]:
        status: dict[str, Any] = {"id": source.id, "label": source.label, "state": "NOT_CONNECTED",
                                  "session_check": source.adapter.session_check, "authenticated_at": None,
                                  "recheck_at": None, "checked_at": None, "error_code": None, "message": None}
        if row is None:
            return status
        code = row.get("error_code")
        session = row["session_state"]
        if self._attempt_open(row, now):
            status["state"] = "LOGGING_IN"
        elif session == "INVALID":
            status["state"] = "NEEDS_LOGIN"
        elif session == "ACTIVE":
            too_old = self._too_old(source, row, now)
            if too_old:
                status["state"], code = "NEEDS_LOGIN", too_old
            else:
                status["state"] = "CHECK_FAILED" if row.get("check_state") == "UNREACHABLE" else "CONNECTED"
        if row.get("login_attempt") and status["state"] != "LOGGING_IN":
            code = code or "LOGIN_TIMEOUT"  # a window left open past its limit
        if session != "NONE":
            status["authenticated_at"] = row.get("authenticated_at")
            status["checked_at"] = row.get("checked_at")
            signed = _parse(row.get("authenticated_at"))
            if session == "ACTIVE" and signed and source.adapter.session_check == "ttl":
                status["recheck_at"] = _iso(signed + timedelta(seconds=FALLBACK_TTL_SECONDS))
        status["error_code"] = code
        status["message"] = MESSAGES.get(code) if code else None
        return status

    def status(self, source_id: str) -> dict[str, Any]:
        return self._public(self._source(source_id), self.store.get(source_id), self.clock())

    def statuses(self) -> list[dict[str, Any]]:
        """Every configured source, in config order (reading never changes anything)."""
        now = self.clock()
        return [self._public(source, self.store.get(source.id), now) for source in self.config.sources.values()]

    def session_gate(self, source_id: str) -> tuple[bool, str | None, int | None]:
        """For the downloader's dispatcher (M4), from the account's row only (no vault, no browser): whether the
        source's saved session may be used now (ACTIVE and allowed by the TTL; its file is read by
        ``session_for`` only), else why a sign-in is needed (NOT_CONNECTED, SESSION_EXPIRED, SESSION_REJECTED…),
        and the session's generation (None without one). A session whose file cannot be read now still counts
        as usable here: ``session_for`` then keeps its own code (permission, disk), never a sign-in."""
        source = self._source(source_id)
        row = self.store.get(source_id)
        if row is None or row["session_state"] == "NONE":
            return False, "NOT_CONNECTED", None
        generation = int(row["generation"])
        if row["session_state"] == "INVALID":
            return False, row["error_code"] or "SESSION_REJECTED", generation
        too_old = self._too_old(source, row, self.clock())
        if too_old:
            return False, too_old, generation
        return True, None, generation

    def attempt_is_current(self, attempt: LoginAttempt) -> bool:
        """False once the attempt was cancelled, replaced, timed out or ended by a disconnect, and for an
        attempt of another manager."""
        if not self.owns(attempt):
            return False
        row = self.store.get(attempt.source_id)
        return bool(row) and row["login_attempt"] == attempt.attempt_id and self._attempt_open(row, self.clock())

    # Sign-in ---------------------------------------------------------------------------------------

    def begin_login(self, source_id: str) -> LoginAttempt:
        self._source(source_id)
        with self._lock(source_id):
            row = self.store.ensure(source_id)
            now = self.clock()
            if self._attempt_open(row, now):
                raise AccountBusy("LOGIN_BUSY", source_id)
            attempt = LoginAttempt(source_id, secrets.token_hex(16), now, self._owner)
            changes: dict[str, Any] = {"login_attempt": attempt.attempt_id, "login_started_at": _iso(now)}
            if row["session_state"] != "INVALID":  # an invalid session keeps the reason it needs a sign-in
                changes["error_code"] = None
            if not self.store.update_if(source_id, {"login_attempt": row["login_attempt"],
                                                    "session_state": row["session_state"]}, **changes):
                raise AccountBusy("LOGIN_BUSY", source_id)
            return attempt

    def _end(self, source_id: str, attempt_id: str, code: str) -> bool:
        """End one sign-in with ``code``; an INVALID session keeps its own reason instead. False when the
        attempt is no longer the open one, or when the database cannot be written (LOGGING_IN still ends,
        at its time limit or at the next start)."""
        try:
            row = self.store.get(source_id)
            if row is None or row["login_attempt"] != attempt_id:
                return False
            changes: dict[str, Any] = {"login_attempt": None, "login_started_at": None}
            if row["session_state"] != "INVALID":
                changes["error_code"] = code
            return self.store.update_if(source_id, {"login_attempt": attempt_id,
                                                    "session_state": row["session_state"]}, **changes)
        except sqlite3.Error:
            return False

    def end_login(self, attempt: LoginAttempt, code: str) -> bool:
        """The window was cancelled, closed, timed out or failed: never a success. False when stale or when
        the database cannot be written (see ``_end``)."""
        if code not in LOGIN_END_CODES:
            raise ValueError(f"Unknown sign-in end code {code!r}")
        if not self.owns(attempt):
            return False
        with self._lock(attempt.source_id):
            return self._end(attempt.source_id, attempt.attempt_id, code)

    def cancel_login(self, source_id: str) -> bool:
        """The user's cancel: ends whatever sign-in is open for the source (its late callback is refused)."""
        self._source(source_id)
        with self._lock(source_id):
            row = self.store.get(source_id)
            if row is None or not row["login_attempt"]:
                return False
            return self._end(source_id, row["login_attempt"], "LOGIN_CANCELLED")

    def complete_login(self, attempt: LoginAttempt, storage_state: Mapping[str, Any]) -> dict[str, Any]:
        """Save the signed-in state as the next generation, then commit it (see the module docstring). An
        attempt of another manager is refused before anything is read or written."""
        source = self._source(attempt.source_id)
        if not self.owns(attempt):
            raise StaleLogin("LOGIN_STALE", source.id)
        with self._lock(source.id):
            row = self.store.get(source.id)
            now = self.clock()
            if row is None or row["login_attempt"] != attempt.attempt_id:
                raise StaleLogin("LOGIN_STALE", source.id)
            if not self._attempt_open(row, now):
                self._end(source.id, attempt.attempt_id, "LOGIN_TIMEOUT")
                raise StaleLogin("LOGIN_TIMEOUT", source.id)
            try:
                state = own_state(source, check_storage_state(storage_state))
            except ValueError:
                self._end(source.id, attempt.attempt_id, "LOGIN_FAILED")
                raise SessionSaveFailed("LOGIN_FAILED", source.id) from None
            generation, signed = row["generation"] + 1, _iso(now)
            try:
                self.vault.write(source.id, generation, signed, state)
            except VaultError:
                self._end(source.id, attempt.attempt_id, "SESSION_SAVE_FAILED")
                raise SessionSaveFailed("SESSION_SAVE_FAILED", source.id) from None
            try:
                committed: bool | None = self.store.update_if(
                    source.id, {"login_attempt": attempt.attempt_id, "generation": row["generation"]},
                    session_state="ACTIVE", generation=generation, authenticated_at=signed, login_attempt=None,
                    login_started_at=None, check_state=None, checked_at=None, error_code=None)
            except sqlite3.Error:
                committed = None
            if not committed:
                self._discard(source.id, generation)
                if committed is None:
                    self._end(source.id, attempt.attempt_id, "SESSION_SAVE_FAILED")
                    raise SessionSaveFailed("SESSION_SAVE_FAILED", source.id)
                raise StaleLogin("LOGIN_STALE", source.id)
            self._remove_files(source.id, keep=generation)
            return self._public(source, self.store.get(source.id), self.clock())

    def disconnect(self, source_id: str) -> dict[str, Any]:
        """Forget the source's session: next generation, no session, saved files deleted. The files go only
        after the row moved on, so a file of a session the row still names is never deleted."""
        source = self._source(source_id)
        with self._lock(source_id):
            for _ in range(DISCONNECT_ATTEMPTS):
                row = self.store.ensure(source_id)
                generation = row["generation"] + 1
                if self.store.update_if(source_id, {"generation": row["generation"]}, session_state="NONE",
                                        generation=generation, authenticated_at=None, login_attempt=None,
                                        login_started_at=None, check_state=None, checked_at=None,
                                        error_code=None):
                    break
            else:
                raise AccountBusy("ACCOUNT_BUSY", source_id)
            try:
                self.vault.remove(source_id)
            except VaultError:  # the files left cannot be opened under the new generation
                self.store.update_if(source_id, {"generation": generation}, error_code="SESSION_REMOVE_FAILED")
            return self._public(source, self.store.get(source_id), self.clock())

    # Using a session -------------------------------------------------------------------------------

    def _invalidate(self, source_id: str, generation: int, code: str) -> bool:
        return self.store.update_if(source_id, {"generation": generation, "session_state": "ACTIVE"},
                                    session_state="INVALID", error_code=code)

    def session_for(self, source_id: str) -> SessionLease:
        source = self._source(source_id)
        with self._lock(source_id):
            row = self.store.get(source_id)
            if row is None or row["session_state"] == "NONE":
                raise LoginRequired("NOT_CONNECTED", source_id, None)
            generation = row["generation"]
            if row["session_state"] == "INVALID":
                raise LoginRequired(row["error_code"] or "SESSION_REJECTED", source_id, generation)
            too_old = self._too_old(source, row, self.clock())
            if too_old:
                raise LoginRequired(too_old, source_id, generation)
            try:
                stored = self.vault.read(source_id, generation)
            except (VaultMissing, VaultCorrupt) as error:
                self._invalidate(source_id, generation, error.code)
                raise LoginRequired(error.code, source_id, generation) from None
            except VaultError as error:  # permission, disk or ACL: the session is kept
                self.store.update_if(source_id, {"generation": generation}, error_code=error.code)
                raise SessionUnavailable(error.code, source_id) from None
            if stored.authenticated_at != row["authenticated_at"]:
                self._invalidate(source_id, generation, "SESSION_CORRUPT")
                raise LoginRequired("SESSION_CORRUPT", source_id, generation)
            if row["error_code"] in ("SESSION_IO_ERROR", "SESSION_REFUSED"):
                self.store.update_if(source_id, {"generation": generation, "error_code": row["error_code"]},
                                     error_code=None)
            signed = _parse(row["authenticated_at"])
            if signed is None:  # a "live" adapter has no TTL check that would have refused it already
                self._invalidate(source_id, generation, "SESSION_CORRUPT")
                raise LoginRequired("SESSION_CORRUPT", source_id, generation)
            return SessionLease(source_id, generation, signed, stored.state, self._owner)

    def lease_is_current(self, lease: SessionLease) -> bool:
        """The lease's session is still the source's saved one: same generation, still ACTIVE (a disconnect,
        a new sign-in or an invalidation since make it stale). The TTL is not read: a lease given out while it
        was usable stays the one its run used. False for a lease of another manager."""
        if not self.owns(lease):
            return False
        self._source(lease.source_id)
        row = self.store.get(lease.source_id)
        return bool(row) and row["generation"] == lease.generation and row["session_state"] == "ACTIVE"

    def save_rotated(self, lease: SessionLease, storage_state: Mapping[str, Any]) -> bool:
        """Save cookies a hidden run rotated, under the same generation and ``authenticated_at`` (only the
        source's own, like ``complete_login``). False when the lease is stale, belongs to another manager or
        the save failed (the file saved before stays as it was); ValueError for a state of the wrong shape
        (a bug of the caller)."""
        source = self._source(lease.source_id)
        if not self.owns(lease):
            return False
        state = own_state(source, check_storage_state(storage_state))
        with self._lock(source.id):
            row = self.store.get(source.id)
            if row is None or row["generation"] != lease.generation or row["session_state"] != "ACTIVE":
                return False
            try:
                self.vault.write(source.id, lease.generation, row["authenticated_at"], state)
            except VaultError as error:
                self.store.update_if(source.id, {"generation": lease.generation}, error_code=error.code)
                return False
            return True

    def mark_invalid(self, lease: SessionLease) -> bool:
        """The source showed that the lease's session is no longer valid (a sign-in page, an explicit
        message). False for another generation or a lease of another manager."""
        self._source(lease.source_id)
        if not self.owns(lease):
            return False
        with self._lock(lease.source_id):
            return self._invalidate(lease.source_id, lease.generation, "SESSION_REJECTED")

    def record_check(self, lease: SessionLease, outcome: str) -> bool:
        """The result of a real session check ("valid", "invalid", "unreachable") of the lease's session; it
        never changes ``authenticated_at``, so a check never extends a "ttl" session. CHECK_FAILED is only
        for a "live" adapter: for a "ttl" one an unreachable check is ignored (False), its TTL decides.
        False for another generation or a lease of another manager."""
        if outcome not in CHECK_OUTCOMES:
            raise ValueError(f"Unknown check outcome {outcome!r}")
        source = self._source(lease.source_id)
        if not self.owns(lease):
            return False
        if outcome == "unreachable" and source.adapter.session_check != "live":
            return False
        source_id, generation = lease.source_id, lease.generation
        with self._lock(source_id):
            expected = {"generation": generation, "session_state": "ACTIVE"}
            if outcome == "valid":
                return self.store.update_if(source_id, expected, check_state=None,
                                            checked_at=_iso(self.clock()), error_code=None)
            if outcome == "invalid":
                return self._invalidate(source_id, generation, "SESSION_REJECTED")
            return self.store.update_if(source_id, expected, check_state="UNREACHABLE",
                                        error_code="CHECK_UNREACHABLE")

    # Start -----------------------------------------------------------------------------------------

    def _discard(self, source_id: str, generation: int) -> None:
        try:
            self.vault.discard(source_id, generation)
        except VaultError:  # not committed, so never opened; removed at the next start
            pass

    def _remove_files(self, source_id: str, keep: int | None) -> bool:
        """False when some could not be removed: files of other generations are never opened, and the
        next start or disconnect tries again."""
        try:
            self.vault.remove(source_id, keep=keep)
        except VaultError:
            return False
        return True

    def recover(self) -> tuple[str, ...]:
        """At start: end sign-ins a restart interrupted, check that ACTIVE sessions still have their file,
        and remove files of other generations (see the module docstring). The ids of the sources that could
        not be settled now (database, permission or disk); each keeps its session and its error code."""
        unsettled = []
        for source in self.config.sources.values():
            with self._lock(source.id):
                try:
                    settled = self._recover_source(source.id)
                except sqlite3.Error:
                    settled = False
            if not settled:
                unsettled.append(source.id)
        return tuple(unsettled)

    def clear_browser_profiles(self) -> bool:
        """At start, before any browser run (M4 calls it next to ``recover``): delete the temporary browser
        profiles a killed run left in this account's private folder. False when some are still there (the
        next start tries again); nothing else is touched."""
        try:
            return self.vault.remove_browser_profiles() == 0
        except VaultError:
            return False

    def _recover_source(self, source_id: str) -> bool:
        row = self.store.get(source_id)
        if row is None:
            return self._remove_files(source_id, keep=None)
        if row["login_attempt"]:
            self._end(source_id, row["login_attempt"], "LOGIN_INTERRUPTED")
        generation = row["generation"]
        if row["session_state"] == "ACTIVE":
            try:
                present = self.vault.exists(source_id, generation)
            except VaultError as error:
                self.store.update_if(source_id, {"generation": generation}, error_code=error.code)
                return False
            if not present:
                self._invalidate(source_id, generation, "SESSION_MISSING")
        removed = self._remove_files(source_id, keep=generation if row["session_state"] != "NONE" else None)
        if removed and row["session_state"] == "NONE":  # an earlier disconnect's files are gone now
            self.store.update_if(source_id, {"generation": generation, "error_code": "SESSION_REMOVE_FAILED"},
                                 error_code=None)
        return removed
