"""The session manager of the source accounts (download_accounts): TTL, generations, sign-in attempts,
restart, independent sources, failure handling, and no secret in the database, status, files or logs.

Made-up sessions only, in temporary roots under the install's temp/. Most tests use a fake protector and a
fake ACL (fast and deterministic); the canary test uses the real DPAPI and the real private ACL.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import recycle_bin
from biliflow.download_account_config import AccountConfig, AdapterSpec, SourceAccount
from biliflow.download_account_vault import SessionVault, VaultIOError, VaultRefused
from biliflow.download_account_winsec import (
    ADMINISTRATORS_SID,
    SYSTEM_SID,
    AclReport,
    ProtectorRejected,
    current_user_sid,
)
from biliflow.download_accounts import (
    FALLBACK_TTL_SECONDS,
    LOGIN_ATTEMPT_MAX_SECONDS,
    AccountBusy,
    AccountManager,
    AccountUnknown,
    LoginRequired,
    SessionSaveFailed,
    SessionUnavailable,
    StaleLogin,
)

TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
T0 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)
CANARY = "BF-CANARY-cookie-4c81e2"
PUBLIC_KEYS = {"id", "label", "state", "session_check", "authenticated_at", "recheck_at", "checked_at",
               "error_code", "message"}


def source(source_id: str, check: str = "ttl") -> SourceAccount:
    host = f"{source_id}.example"
    return SourceAccount(source_id, AdapterSpec("ticket-files" if check == "ttl" else "live-check", check),
                         f"Nguồn {source_id}", f"https://{host}/login", {"portal": (host,), "tickets": (),
                                                                        "files": (f"files.{host}",)})


CONFIG = AccountConfig({item.id: item for item in (source("alpha"), source("beta"), source("gamma", "live"))})


def cookie(domain: str, value: str = "v1", name: str = "sid") -> dict:
    return {"name": name, "value": value, "domain": domain, "path": "/", "expires": -1, "httpOnly": True,
            "secure": True, "sameSite": "Lax"}


def state(value: str = "v1", source_id: str = "alpha") -> dict:
    """A made-up storage state that belongs to the source (its portal host), so nothing is filtered out."""
    return {"cookies": [cookie(f"{source_id}.example", value)],
            "origins": [{"origin": f"https://{source_id}.example", "localStorage": []}]}


class Clock:
    def __init__(self, now: datetime = T0):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def at(self, seconds: float) -> None:
        self.now = T0 + timedelta(seconds=seconds)


class FakeProtector:
    """Reversible stand-in for DPAPI with switches for failures; ``gate`` holds a write mid-way."""

    def __init__(self):
        self.reject = False
        self.fail_protect = False
        self.gate: tuple[threading.Event, threading.Event] | None = None

    def protect(self, data, entropy):
        if self.gate is not None:
            entered, release = self.gate
            entered.set()
            release.wait(10)
        if self.fail_protect:
            raise OSError(5, "denied")
        return entropy[-8:] + bytes(byte ^ 0x5A for byte in data)

    def unprotect(self, blob, entropy):
        if self.reject or blob[:8] != entropy[-8:]:
            raise ProtectorRejected("fake")
        return bytes(byte ^ 0x5A for byte in blob[8:])


class GoodAcl:
    """A private ACL as the vault wants it, for the Windows account ``user_sid``."""

    def __init__(self, user_sid: str = "S-1-5-21-1-2-3-1001"):
        self.user_sid = user_sid

    def create_directory(self, path):
        os.mkdir(path)

    def secure(self, path):
        pass

    def inspect(self, path):
        return AclReport(self.user_sid, True, ((self.user_sid, 0x1F01FF), (SYSTEM_SID, 0x1F01FF),
                                               (ADMINISTRATORS_SID, 0x1F01FF)), 0)


class FailingCommit:
    """The store's connection, except that the commit of the change saving a session (an UPDATE carrying
    "ACTIVE") fails once without committing, as a disk error would."""

    def __init__(self, connection):
        self.connection = connection
        self.armed = False
        self.failed = 0

    def execute(self, sql, values=()):
        if sql.startswith("UPDATE") and "ACTIVE" in values:
            self.armed = True
        return self.connection.execute(sql, values)

    def commit(self):
        if self.armed:
            self.armed = False
            self.failed += 1
            raise sqlite3.OperationalError("disk I/O error")
        self.connection.commit()

    def rollback(self):
        self.armed = False
        self.connection.rollback()

    def close(self):
        self.connection.close()


class ManagerCase(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.root = Path(self._temp.name)
        self.clock = Clock()
        self.protector = FakeProtector()
        self.managers: list[AccountManager] = []
        self.manager = self.new_manager()

    def tearDown(self):
        for manager in self.managers:
            manager.close()
        self._temp.cleanup()

    def new_manager(self, user_sid: str = "S-1-5-21-1-2-3-1001") -> AccountManager:
        vault = SessionVault(self.root, protector=self.protector, acl=GoodAcl(user_sid))
        manager = AccountManager(self.root, CONFIG, vault=vault, clock=self.clock)
        self.managers.append(manager)
        return manager

    def connect(self, source_id="alpha", value="v1", manager=None):
        manager = manager or self.manager
        attempt = manager.begin_login(source_id)
        return manager.complete_login(attempt, state(value, source_id))

    def folder(self, source_id="alpha", manager=None) -> Path:
        return (manager or self.manager).vault.source_folder(source_id)

    def files(self, source_id="alpha", manager=None) -> list[str]:
        folder = self.folder(source_id, manager)
        return sorted(path.name for path in folder.iterdir()) if folder.exists() else []


class TtlTest(ManagerCase):
    def test_a_session_is_usable_until_just_before_3600_seconds(self):
        self.assertEqual(FALLBACK_TTL_SECONDS, 3600)  # the value is pinned here; other tests use the name
        self.connect()
        status = self.manager.status("alpha")
        self.assertEqual(status["state"], "CONNECTED")
        self.assertEqual(status["recheck_at"], (T0 + timedelta(seconds=3600)).isoformat())
        for seconds in (3599, 3600 - 0.001):  # the dispatcher's gate and a hidden run's session agree
            with self.subTest(seconds=seconds):
                self.clock.at(seconds)
                self.assertEqual(self.manager.session_gate("alpha"), (True, None, 1))
                self.assertEqual(self.manager.session_for("alpha").state, state())
                self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")
        for seconds in (3600, 3601):
            with self.subTest(seconds=seconds):
                self.clock.at(seconds)
                self.assertEqual(self.manager.session_gate("alpha"), (False, "SESSION_EXPIRED", 1))
                with self.assertRaises(LoginRequired) as caught:
                    self.manager.session_for("alpha")
                self.assertEqual((caught.exception.code, caught.exception.generation), ("SESSION_EXPIRED", 1))
                status = self.manager.status("alpha")
                self.assertEqual((status["state"], status["error_code"]), ("NEEDS_LOGIN", "SESSION_EXPIRED"))
        self.assertEqual(self.files(), ["session-1.bin"])  # an expired session is not deleted

    def test_reading_checking_and_rotating_never_extend_the_session(self):
        self.connect()
        for seconds in range(0, 3000, 100):
            self.clock.at(seconds)
            self.manager.statuses()
        self.clock.at(1000)
        self.assertTrue(self.manager.record_check(self.manager.session_for("alpha"), "valid"))
        self.clock.at(2000)
        lease = self.manager.session_for("alpha")
        self.assertTrue(self.manager.save_rotated(lease, state("rotated")))
        self.assertEqual(self.manager.session_for("alpha").state, state("rotated"))
        status = self.manager.status("alpha")
        self.assertEqual(status["authenticated_at"], T0.isoformat())
        self.assertEqual(status["recheck_at"], (T0 + timedelta(seconds=FALLBACK_TTL_SECONDS)).isoformat())
        self.clock.at(FALLBACK_TTL_SECONDS)
        with self.assertRaises(LoginRequired) as caught:
            self.manager.session_for("alpha")
        self.assertEqual(caught.exception.code, "SESSION_EXPIRED")  # its age counts from the sign-in only

    def test_a_clock_set_back_before_the_sign_in_asks_for_a_new_one(self):
        self.connect()
        self.clock.at(-60)
        with self.assertRaises(LoginRequired) as caught:
            self.manager.session_for("alpha")
        self.assertEqual(caught.exception.code, "CLOCK_CHANGED")

    def test_a_live_adapter_has_no_ttl_and_an_unreachable_check_does_not_invalidate(self):
        self.connect("gamma")
        self.clock.at(10 * FALLBACK_TTL_SECONDS)
        lease = self.manager.session_for("gamma")
        self.assertEqual(lease.generation, 1)
        self.assertIsNone(self.manager.status("gamma")["recheck_at"])
        self.assertTrue(self.manager.record_check(lease, "unreachable"))
        self.assertEqual(self.manager.status("gamma")["state"], "CHECK_FAILED")
        self.assertEqual(self.manager.session_for("gamma").generation, 1)
        self.assertTrue(self.manager.record_check(lease, "valid"))
        self.assertEqual(self.manager.status("gamma")["state"], "CONNECTED")
        self.assertTrue(self.manager.record_check(lease, "invalid"))
        self.assertEqual(self.manager.status("gamma")["state"], "NEEDS_LOGIN")
        with self.assertRaises(LoginRequired):
            self.manager.session_for("gamma")

    def test_check_failed_is_only_for_a_live_adapter(self):
        self.connect()
        self.assertFalse(self.manager.record_check(self.manager.session_for("alpha"), "unreachable"))
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("CONNECTED", None))


class SignInTest(ManagerCase):
    def run_held(self, action, *, entered_by="alpha"):
        """Run ``action`` while a sign-in of ``entered_by`` is held inside its save; it must not wait."""
        entered, release = threading.Event(), threading.Event()
        attempt = self.manager.begin_login(entered_by)
        self.protector.gate = (entered, release)
        saving = threading.Thread(target=self.manager.complete_login, args=(attempt, state("held", entered_by)))
        saving.start()
        try:
            self.assertTrue(entered.wait(10))
            self.protector.gate = None
            results = []
            other = threading.Thread(target=lambda: results.append(action()))
            other.start()
            other.join(5)
            self.assertFalse(other.is_alive(), "blocked by another source's save")
            return results[0]
        finally:
            release.set()
            saving.join(10)

    def test_a_save_of_one_source_never_holds_up_another_source_or_the_status(self):
        beta = self.run_held(lambda: (self.manager.begin_login("beta"), self.manager.statuses()))
        self.assertEqual([item["state"] for item in beta[1]], ["LOGGING_IN", "LOGGING_IN", "NOT_CONNECTED"])
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")

    def test_two_sign_ins_started_at_once_give_one_attempt(self):
        barrier, results = threading.Barrier(2), []

        def start():
            barrier.wait(5)
            try:
                results.append(self.manager.begin_login("alpha"))
            except AccountBusy as error:
                results.append(error.code)

        threads = [threading.Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sorted(type(item).__name__ for item in results), ["LoginAttempt", "str"])
        self.assertIn("LOGIN_BUSY", results)

    def test_an_attempt_ends_once(self):
        attempt = self.manager.begin_login("alpha")
        self.manager.complete_login(attempt, state())
        self.assertFalse(self.manager.end_login(attempt, "LOGIN_WINDOW_CLOSED"))
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")

    def test_an_invalid_session_keeps_its_reason_through_a_new_sign_in_that_ends(self):
        self.connect()
        self.manager.mark_invalid(self.manager.session_for("alpha"))
        for end in (lambda attempt: self.manager.cancel_login("alpha"),
                    lambda attempt: self.manager.end_login(attempt, "LOGIN_WINDOW_CLOSED")):
            attempt = self.manager.begin_login("alpha")
            self.assertEqual(self.manager.status("alpha")["error_code"], "SESSION_REJECTED")
            self.assertTrue(end(attempt))
            status = self.manager.status("alpha")
            self.assertEqual((status["state"], status["error_code"]), ("NEEDS_LOGIN", "SESSION_REJECTED"))
            with self.assertRaises(LoginRequired) as caught:
                self.manager.session_for("alpha")
            self.assertEqual(caught.exception.code, "SESSION_REJECTED")

    def test_a_cancelled_or_closed_window_is_never_a_success(self):
        for code in ("LOGIN_CANCELLED", "LOGIN_WINDOW_CLOSED", "LOGIN_TIMEOUT", "LOGIN_FAILED"):
            with self.subTest(code=code):
                attempt = self.manager.begin_login("alpha")
                self.assertEqual(self.manager.status("alpha")["state"], "LOGGING_IN")
                self.assertTrue(self.manager.end_login(attempt, code))
                status = self.manager.status("alpha")
                self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", code))
                with self.assertRaises(StaleLogin):
                    self.manager.complete_login(attempt, state())
                self.assertEqual(self.files(), [])
        with self.assertRaises(ValueError):
            self.manager.end_login(self.manager.begin_login("alpha"), "LOGIN_OK")

    def test_the_users_cancel_refuses_the_late_callback(self):
        attempt = self.manager.begin_login("alpha")
        self.assertTrue(self.manager.cancel_login("alpha"))
        self.assertFalse(self.manager.attempt_is_current(attempt))
        with self.assertRaises(StaleLogin):
            self.manager.complete_login(attempt, state())
        self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")
        self.assertFalse(self.manager.cancel_login("alpha"))

    def test_a_disconnect_ends_an_open_sign_in_and_its_late_callback_does_not_reconnect(self):
        self.connect()
        attempt = self.manager.begin_login("alpha")
        self.manager.disconnect("alpha")
        with self.assertRaises(StaleLogin):
            self.manager.complete_login(attempt, state("late"))
        status = self.manager.status("alpha")
        self.assertEqual(status["state"], "NOT_CONNECTED")
        self.assertIsNone(status["authenticated_at"])
        self.assertEqual(self.files(), [])
        with self.assertRaises(LoginRequired):
            self.manager.session_for("alpha")

    def test_an_old_callback_never_overwrites_a_newer_session(self):
        first = self.manager.begin_login("alpha")
        self.manager.cancel_login("alpha")
        second = self.manager.begin_login("alpha")
        self.manager.complete_login(second, state("second"))
        with self.assertRaises(StaleLogin):
            self.manager.complete_login(first, state("first"))
        with self.assertRaises(StaleLogin):
            self.manager.complete_login(second, state("again"))  # one success per attempt
        self.assertEqual(self.manager.session_for("alpha").state, state("second"))
        self.assertEqual(self.files(), ["session-1.bin"])

    def test_a_disconnect_during_a_save_waits_for_it_then_wins(self):
        entered, release = threading.Event(), threading.Event()
        attempt = self.manager.begin_login("alpha")
        self.protector.gate = (entered, release)
        results = {}
        saving = threading.Thread(target=lambda: results.setdefault(
            "save", self.manager.complete_login(attempt, state("racing"))["state"]))
        saving.start()
        self.assertTrue(entered.wait(10))
        self.protector.gate = None
        disconnecting = threading.Thread(target=lambda: results.setdefault(
            "disconnect", self.manager.disconnect("alpha")["state"]))
        disconnecting.start()
        disconnecting.join(0.3)
        self.assertTrue(disconnecting.is_alive())  # the per-source lock holds it until the save ends
        release.set()
        saving.join(10)
        disconnecting.join(10)
        self.assertEqual(results, {"save": "CONNECTED", "disconnect": "NOT_CONNECTED"})
        self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")
        self.assertEqual(self.manager.store.get("alpha")["generation"], 2)
        self.assertEqual(self.files(), [])
        with self.assertRaises(StaleLogin):
            self.manager.complete_login(attempt, state("late"))

    def test_one_sign_in_at_a_time_until_its_time_limit(self):
        first = self.manager.begin_login("alpha")
        with self.assertRaises(AccountBusy) as busy:
            self.manager.begin_login("alpha")
        self.assertEqual(busy.exception.code, "LOGIN_BUSY")  # a window is open, not a database that is busy
        self.clock.at(LOGIN_ATTEMPT_MAX_SECONDS - 1)
        self.assertEqual(self.manager.status("alpha")["state"], "LOGGING_IN")
        self.clock.at(LOGIN_ATTEMPT_MAX_SECONDS)
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", "LOGIN_TIMEOUT"))
        with self.assertRaises(StaleLogin) as late:
            self.manager.complete_login(first, state())
        self.assertEqual(late.exception.code, "LOGIN_TIMEOUT")
        second = self.manager.begin_login("alpha")
        self.manager.complete_login(second, state())
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")

    def test_an_old_session_stays_usable_while_a_new_sign_in_is_open(self):
        self.connect(value="old")
        self.manager.begin_login("alpha")
        self.assertEqual(self.manager.status("alpha")["state"], "LOGGING_IN")
        self.assertEqual(self.manager.session_for("alpha").state, state("old"))

    def test_a_storage_state_of_the_wrong_shape_ends_the_sign_in(self):
        for bad in ("text", {"cookies": "x"}, {"cookies": [{"name": "a"}]}, {"cookies": [], "origins": [5]}):
            with self.subTest(bad=bad):
                attempt = self.manager.begin_login("alpha")
                with self.assertRaises(SessionSaveFailed) as caught:
                    self.manager.complete_login(attempt, bad)
                self.assertEqual(caught.exception.code, "LOGIN_FAILED")
                self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")
        self.assertEqual(self.files(), [])

    def test_unknown_sources_are_refused(self):
        for action in (self.manager.begin_login, self.manager.disconnect, self.manager.session_for,
                       self.manager.status, self.manager.cancel_login):
            with self.subTest(action=action.__name__):
                with self.assertRaises(AccountUnknown):
                    action("delta")


class FailureTest(ManagerCase):
    def test_a_failed_save_is_never_connected(self):
        self.protector.fail_protect = True
        attempt = self.manager.begin_login("alpha")
        with self.assertRaises(SessionSaveFailed) as caught:
            self.manager.complete_login(attempt, state())
        self.assertEqual(caught.exception.code, "SESSION_SAVE_FAILED")
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", "SESSION_SAVE_FAILED"))
        self.assertEqual(self.files(), [])
        with self.assertRaises(LoginRequired) as caught:
            self.manager.session_for("alpha")
        self.assertEqual(caught.exception.code, "NOT_CONNECTED")

    def test_a_failed_save_keeps_the_previous_session(self):
        self.connect(value="old")
        self.protector.fail_protect = True
        attempt = self.manager.begin_login("alpha")
        with self.assertRaises(SessionSaveFailed):
            self.manager.complete_login(attempt, state("new"))
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("CONNECTED", "SESSION_SAVE_FAILED"))
        self.protector.fail_protect = False
        lease = self.manager.session_for("alpha")
        self.assertEqual((lease.generation, lease.state), (1, state("old")))

    def test_a_database_failure_after_the_save_discards_the_new_file(self):
        attempt = self.manager.begin_login("alpha")
        original = self.manager.store.update_if

        def failing(source_id, expected, **changes):
            if changes.get("session_state") == "ACTIVE":
                raise sqlite3.OperationalError("database is locked")
            return original(source_id, expected, **changes)

        with patch.object(self.manager.store, "update_if", side_effect=failing):
            with self.assertRaises(SessionSaveFailed) as caught:
                self.manager.complete_login(attempt, state())
        self.assertEqual(caught.exception.code, "SESSION_SAVE_FAILED")
        self.assertEqual(self.files(), [])
        self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")

    def test_a_corrupt_file_needs_a_sign_in_and_is_kept(self):
        self.connect()
        self.protector.reject = True
        with self.assertRaises(LoginRequired) as caught:
            self.manager.session_for("alpha")
        self.assertEqual(caught.exception.code, "SESSION_CORRUPT")
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("NEEDS_LOGIN", "SESSION_CORRUPT"))
        self.assertEqual(self.files(), ["session-1.bin"])

    def test_a_permission_or_disk_error_is_not_a_sign_in_problem_and_keeps_the_session(self):
        self.connect()
        with patch.object(self.manager.vault, "read", side_effect=VaultIOError("denied")):
            with self.assertRaises(SessionUnavailable) as caught:
                self.manager.session_for("alpha")
        self.assertEqual(caught.exception.code, "SESSION_IO_ERROR")
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("CONNECTED", "SESSION_IO_ERROR"))
        self.assertEqual(self.files(), ["session-1.bin"])
        self.assertEqual(self.manager.session_for("alpha").state, state())  # it passes: the error clears
        self.assertIsNone(self.manager.status("alpha")["error_code"])

    def test_a_failed_rotation_keeps_the_saved_file(self):
        self.connect(value="before")
        lease = self.manager.session_for("alpha")
        self.protector.fail_protect = True
        self.assertFalse(self.manager.save_rotated(lease, state("after")))
        self.protector.fail_protect = False
        self.assertEqual(self.manager.session_for("alpha").state, state("before"))

    def test_another_generation_is_ignored(self):
        self.connect(value="first")
        old = self.manager.session_for("alpha")
        self.manager.disconnect("alpha")
        self.connect(value="second")
        self.assertEqual(self.manager.store.get("alpha")["generation"], 3)
        self.assertFalse(self.manager.mark_invalid(old))
        self.assertFalse(self.manager.record_check(old, "invalid"))
        self.assertFalse(self.manager.save_rotated(old, state("stale")))
        lease = self.manager.session_for("alpha")
        self.assertEqual((lease.generation, lease.state), (3, state("second")))
        self.assertTrue(self.manager.mark_invalid(lease))
        self.assertEqual(self.manager.status("alpha")["error_code"], "SESSION_REJECTED")

    def test_a_commit_that_fails_is_rolled_back_and_never_committed_later(self):
        connection = FailingCommit(self.manager.store._connection)
        self.manager.store._connection = connection
        attempt = self.manager.begin_login("alpha")
        with self.assertRaises(SessionSaveFailed) as caught:
            self.manager.complete_login(attempt, state())
        self.assertEqual(caught.exception.code, "SESSION_SAVE_FAILED")
        self.assertEqual(connection.failed, 1)
        self.assertEqual(self.files(), [])
        row = self.manager.store.get("alpha")
        self.assertEqual((row["session_state"], row["generation"], row["error_code"]),
                         ("NONE", 0, "SESSION_SAVE_FAILED"))
        self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")

    def test_a_file_gone_while_connected_needs_a_sign_in(self):
        self.connect()
        (self.folder() / "session-1.bin").unlink()
        with self.assertRaises(LoginRequired) as caught:
            self.manager.session_for("alpha")
        self.assertEqual(caught.exception.code, "SESSION_MISSING")
        self.assertEqual(self.manager.status("alpha")["state"], "NEEDS_LOGIN")

    def test_a_refused_folder_keeps_the_session(self):
        self.connect()
        with patch.object(self.manager.vault, "read", side_effect=VaultRefused("wrong ACL")):
            with self.assertRaises(SessionUnavailable) as caught:
                self.manager.session_for("alpha")
        self.assertEqual(caught.exception.code, "SESSION_REFUSED")
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("CONNECTED", "SESSION_REFUSED"))
        self.assertEqual(self.manager.session_for("alpha").state, state())
        self.assertIsNone(self.manager.status("alpha")["error_code"])

    def test_a_file_of_another_sign_in_time_is_corrupt(self):
        self.connect()
        self.manager.vault.write("alpha", 1, (T0 - timedelta(hours=1)).isoformat(), state())
        with self.assertRaises(LoginRequired) as caught:
            self.manager.session_for("alpha")
        self.assertEqual(caught.exception.code, "SESSION_CORRUPT")

    def test_a_failed_removal_at_disconnect_is_retried_at_start_then_cleared(self):
        self.connect()
        with patch.object(self.manager.vault, "remove", side_effect=VaultIOError("in use")):
            status = self.manager.disconnect("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("NOT_CONNECTED", "SESSION_REMOVE_FAILED"))
        self.assertEqual(self.files(), ["session-1.bin"])
        with self.assertRaises(LoginRequired) as caught:
            self.manager.session_for("alpha")
        self.assertEqual(caught.exception.code, "NOT_CONNECTED")
        restarted = self.new_manager()
        self.assertEqual(restarted.recover(), ())
        self.assertEqual(self.files(), [])
        self.assertIsNone(restarted.status("alpha")["error_code"])

    def test_disconnect_never_deletes_files_when_its_row_change_is_refused(self):
        self.connect()
        with patch.object(self.manager.store, "update_if", return_value=False):
            with self.assertRaises(AccountBusy) as caught:
                self.manager.disconnect("alpha")
        self.assertEqual(caught.exception.code, "ACCOUNT_BUSY")
        self.assertEqual(self.files(), ["session-1.bin"])
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")


class OwnHostsTest(ManagerCase):
    """Only the cookies and origin storage of the source's own hosts are saved (AGENTS: cookies go only to
    their own source's hosts)."""

    MIXED = {"cookies": [cookie("alpha.example", name="host-only"), cookie(".alpha.example", name="domain"),
                         cookie("files.alpha.example", name="files-host"), cookie("ALPHA.EXAMPLE.", name="case"),
                         cookie("other.example", name="other"), cookie(".other.example", name="other-domain"),
                         cookie("evil-alpha.example", name="look-alike"), cookie("www.alpha.example", name="sub"),
                         cookie(".", name="dot")],
             "origins": [{"origin": "https://alpha.example", "localStorage": [{"name": "k", "value": "1"}]},
                         {"origin": "https://other.example", "localStorage": []},
                         {"origin": "http://alpha.example", "localStorage": []},
                         {"origin": "https://alpha.example:8443", "localStorage": []},
                         {"origin": "https://files.alpha.example", "localStorage": []}]}
    KEPT_COOKIES = ["host-only", "domain", "files-host", "case"]
    KEPT_ORIGINS = ["https://alpha.example", "https://files.alpha.example"]

    def kept(self, saved):
        return [item["name"] for item in saved["cookies"]], [item["origin"] for item in saved["origins"]]

    def test_a_sign_in_keeps_only_the_sources_own_cookies_and_origins(self):
        attempt = self.manager.begin_login("alpha")
        self.manager.complete_login(attempt, self.MIXED)
        self.assertEqual(self.kept(self.manager.session_for("alpha").state), (self.KEPT_COOKIES, self.KEPT_ORIGINS))

    def test_a_rotation_keeps_only_the_sources_own_cookies_and_origins(self):
        self.connect()
        self.assertTrue(self.manager.save_rotated(self.manager.session_for("alpha"), self.MIXED))
        self.assertEqual(self.kept(self.manager.session_for("alpha").state), (self.KEPT_COOKIES, self.KEPT_ORIGINS))
        with self.assertRaises(ValueError):
            self.manager.save_rotated(self.manager.session_for("alpha"), {"cookies": "x"})


class RestartTest(ManagerCase):
    def test_a_restart_keeps_sessions_and_ends_open_sign_ins(self):
        self.connect("alpha")
        open_attempt = self.manager.begin_login("beta")
        self.connect("gamma")
        self.manager.begin_login("gamma")  # a new sign-in over a session that stays
        restarted = self.new_manager()
        restarted.recover()
        alpha, beta, gamma = restarted.statuses()
        self.assertEqual(alpha["state"], "CONNECTED")
        self.assertEqual((beta["state"], beta["error_code"]), ("NOT_CONNECTED", "LOGIN_INTERRUPTED"))
        self.assertEqual((gamma["state"], gamma["error_code"]), ("CONNECTED", "LOGIN_INTERRUPTED"))
        with self.assertRaises(StaleLogin):
            restarted.complete_login(open_attempt, state())
        self.assertEqual(restarted.session_for("alpha").state, state())

    def test_a_restart_finds_a_missing_file(self):
        self.connect()
        (self.folder() / "session-1.bin").unlink()
        restarted = self.new_manager()
        restarted.recover()
        status = restarted.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("NEEDS_LOGIN", "SESSION_MISSING"))

    def test_a_restart_removes_other_generations_and_temp_files_only(self):
        self.connect()
        folder = self.folder()
        for name in ("session-7.bin", "session-2.bin.0123456789abcdef.tmp", "notes.txt"):
            (folder / name).write_bytes(b"x")
        restarted = self.new_manager()
        restarted.recover()
        self.assertEqual(self.files(), ["notes.txt", "session-1.bin"])
        self.assertEqual(restarted.session_for("alpha").state, state())

    def test_a_read_error_at_start_keeps_the_session(self):
        self.connect()
        restarted = self.new_manager()
        with patch.object(restarted.vault, "exists", side_effect=VaultIOError("denied")):
            self.assertEqual(restarted.recover(), ("alpha",))
        status = restarted.status("alpha")
        self.assertEqual((status["state"], status["error_code"]), ("CONNECTED", "SESSION_IO_ERROR"))
        self.assertEqual(self.files(), ["session-1.bin"])

    def test_a_restart_never_resets_the_session_age(self):
        self.connect()
        self.clock.at(3000)
        restarted = self.new_manager()
        self.assertEqual(restarted.recover(), ())
        self.assertEqual(restarted.session_for("alpha").authenticated_at, T0)
        self.assertEqual(restarted.status("alpha")["authenticated_at"], T0.isoformat())
        self.clock.at(FALLBACK_TTL_SECONDS)
        with self.assertRaises(LoginRequired) as caught:
            restarted.session_for("alpha")
        self.assertEqual(caught.exception.code, "SESSION_EXPIRED")

    def test_a_database_error_for_one_source_does_not_stop_the_others(self):
        self.connect("alpha")
        self.connect("beta")
        (self.folder("beta") / "session-1.bin.0123456789abcdef.tmp").write_bytes(b"x")
        restarted = self.new_manager()
        original = restarted.store.get

        def failing(source_id):
            if source_id == "alpha":
                raise sqlite3.OperationalError("disk I/O error")
            return original(source_id)

        with patch.object(restarted.store, "get", side_effect=failing):
            self.assertEqual(restarted.recover(), ("alpha",))
        self.assertEqual(self.files("beta"), ["session-1.bin"])


class IndependenceTest(ManagerCase):
    def test_two_sources_are_independent(self):
        self.connect("alpha", "a")
        self.assertEqual(self.manager.status("beta")["state"], "NOT_CONNECTED")
        self.manager.disconnect("beta")
        self.connect("beta", "b")
        self.assertTrue(self.manager.mark_invalid(self.manager.session_for("alpha")))
        self.assertEqual(self.manager.status("alpha")["state"], "NEEDS_LOGIN")
        self.assertEqual(self.manager.session_for("beta").state, state("b", "beta"))
        self.manager.disconnect("beta")
        self.assertEqual(self.files("alpha"), ["session-1.bin"])
        self.assertEqual(self.files("beta"), [])
        self.clock.at(FALLBACK_TTL_SECONDS)
        self.assertEqual([item["state"] for item in self.manager.statuses()],
                         ["NEEDS_LOGIN", "NOT_CONNECTED", "NOT_CONNECTED"])

    def test_the_status_has_only_public_fields(self):
        self.connect()
        for status in self.manager.statuses():
            self.assertEqual(set(status), PUBLIC_KEYS)
        text = json.dumps(self.manager.statuses())
        self.assertNotIn("alpha.example", text)  # no host or sign-in link
        self.assertNotIn("v1", text)

    def test_two_managers_of_one_root_share_their_source_locks(self):
        other = self.new_manager()
        self.assertIs(other._lock("alpha"), self.manager._lock("alpha"))
        self.assertIsNot(other._lock("alpha"), other._lock("beta"))

    def test_a_root_outside_the_install_is_refused_before_anything_is_made(self):
        for root in (recycle_bin.INSTALL_ROOT / "elsewhere-not-made", recycle_bin.INSTALL_ROOT.parent):
            with self.subTest(root=root.name), self.assertRaises(ValueError):
                AccountManager(root, CONFIG, clock=self.clock)
        self.assertFalse((recycle_bin.INSTALL_ROOT / "elsewhere-not-made").exists())
        self.assertFalse((recycle_bin.INSTALL_ROOT.parent / "state" / "downloads.sqlite3").exists())


@unittest.skipUnless(os.name == "nt", "DPAPI and Windows ACLs")
class CanaryTest(unittest.TestCase):
    """A made-up cookie value never reaches the database, the status, an error, a log or a plain file."""

    def test_the_canary_cookie_stays_inside_the_encrypted_file(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=TEMP_PARENT) as name:
            root = Path(name)
            clock = Clock()
            manager = AccountManager(root, CONFIG, clock=clock)  # real DPAPI and private ACL
            try:
                with self.assertNoLogs(level="DEBUG"):
                    attempt = manager.begin_login("alpha")
                    status = manager.complete_login(attempt, state(CANARY))
                    lease = manager.session_for("alpha")
                    self.assertEqual(lease.state, state(CANARY))
                    manager.save_rotated(lease, state(CANARY + "-rotated"))
                    clock.at(FALLBACK_TTL_SECONDS)
                    with self.assertRaises(LoginRequired) as caught:
                        manager.session_for("alpha")
                texts = [json.dumps(status), json.dumps(manager.statuses()), repr(lease), repr(attempt),
                         str(caught.exception), repr(manager.store.get("alpha"))]
                for text in texts:
                    self.assertNotIn(CANARY, text)
                files = [path for path in root.rglob("*") if path.is_file()]
                self.assertTrue(any(path.name == "downloads.sqlite3" for path in files))
                self.assertTrue(any(path.name == "session-1.bin" for path in files))
                for path in files:
                    with self.subTest(file=path.name):
                        self.assertNotIn(CANARY.encode(), path.read_bytes())
                self.assertEqual(manager.account_sid, current_user_sid())  # from the process token
                self.assertNotIn(manager.account_sid, json.dumps(manager.statuses()))
                manager.disconnect("alpha")
                self.assertFalse((manager.vault.source_folder("alpha") / "session-1.bin").exists())
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
