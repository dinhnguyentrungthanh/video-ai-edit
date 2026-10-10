"""Source-account state is scoped by Windows account (SID) + source, like the vault's folders (Codex's
review of M1, 2026-10-07): another account using the same install, before or after, never changes the
first account's state, generation, sign-in or files.

Fake SIDs with the fake protector and ACL of test_download_accounts, temporary roots and made-up SQLite
files only: no Windows account is created, no user session is opened and no real database is touched.
"""
from __future__ import annotations

import sqlite3
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow.download_account_store import TABLE, AccountStore
from biliflow.download_account_vault import SessionVault
from biliflow.download_accounts import FALLBACK_TTL_SECONDS, AccountManager, LoginRequired
from tests.test_download_accounts import CONFIG, T0, TEMP_PARENT, GoodAcl, ManagerCase, state

FIRST = "S-1-5-21-1-2-3-1001"  # ManagerCase's own account ("A")
SECOND = "S-1-5-21-9-9-9-1002"  # "B"


class UserIsolationRegression(ManagerCase):
    """Codex's three regressions (download_account_m1_review_tests.py), which failed on the shared row.
    In the first one B gets NOT_CONNECTED, not SESSION_MISSING: B never signed in within its own scope (the
    draft's shared row said ACTIVE, which is what made B's missing file look like a lost session)."""

    def test_second_user_read_does_not_invalidate_first_user(self):
        self.connect(value="first")
        other = self.new_manager(user_sid=SECOND)
        with self.assertRaises(LoginRequired) as caught:
            other.session_for("alpha")
        self.assertEqual(caught.exception.code, "NOT_CONNECTED")
        self.assertEqual(self.manager.session_for("alpha").state, state("first"))

    def test_second_user_login_and_recover_preserve_first_users_session(self):
        self.connect(value="first")
        other = self.new_manager(user_sid=SECOND)
        self.connect(value="second", manager=other)
        other.recover()
        self.assertEqual(self.manager.session_for("alpha").state, state("first"))

    def test_first_user_recovery_does_not_delete_its_session_after_second_user_login(self):
        self.connect(value="first")
        other = self.new_manager(user_sid=SECOND)
        self.connect(value="second", manager=other)
        first_files_before = self.files()
        self.manager.recover()
        self.assertEqual(self.files(), first_files_before)
        self.assertEqual(self.manager.session_for("alpha").state, state("first"))


class AccountScopeTest(ManagerCase):
    def lease(self, manager):
        lease = manager.session_for("alpha")
        return lease.generation, lease.state, lease.authenticated_at

    def test_a_then_b_then_a_then_b_with_restarts(self):
        self.connect(value="a1")  # A signs in at T0: A's generation 1
        b = self.new_manager(user_sid=SECOND)
        self.assertEqual(b.status("alpha")["state"], "NOT_CONNECTED")
        self.clock.at(100)
        self.connect(value="b1", manager=b)  # B signs in at T0+100: B's own generation 1
        self.assertEqual(b.recover(), ())

        self.clock.at(3000)
        a = self.new_manager()  # A starts again
        self.assertEqual(a.recover(), ())
        self.assertEqual(a.status("alpha")["state"], "CONNECTED")
        self.assertEqual(self.lease(a), (1, state("a1"), T0))
        self.connect(value="a2", manager=a)  # A signs in again: generation 2 of A only
        self.assertEqual(self.files(manager=a), ["session-2.bin"])

        self.clock.at(3650)
        b = self.new_manager(user_sid=SECOND)  # B starts again
        self.assertEqual(b.recover(), ())
        self.assertEqual(self.lease(b), (1, state("b1"), T0 + timedelta(seconds=100)))
        self.assertEqual(self.files(manager=b), ["session-1.bin"])

        self.clock.at(100 + FALLBACK_TTL_SECONDS - 1)  # the dispatcher's gate of each account, around B's mark
        self.assertEqual((b.session_gate("alpha"), a.session_gate("alpha")), ((True, None, 1), (True, None, 2)))
        self.clock.at(100 + FALLBACK_TTL_SECONDS)  # B's hour is over; A signed in again at 3000
        self.assertEqual((b.session_gate("alpha"), a.session_gate("alpha")),
                         ((False, "SESSION_EXPIRED", 1), (True, None, 2)))
        with self.assertRaises(LoginRequired) as caught:
            b.session_for("alpha")
        self.assertEqual(caught.exception.code, "SESSION_EXPIRED")
        a = self.new_manager()
        self.assertEqual(a.recover(), ())
        self.assertEqual(self.lease(a), (2, state("a2"), T0 + timedelta(seconds=3000)))
        self.assertEqual(a.status("alpha")["state"], "CONNECTED")
        self.assertEqual(b.status("alpha")["state"], "NEEDS_LOGIN")

    def test_the_second_accounts_actions_never_touch_the_first(self):
        self.connect(value="first")
        a_lease = self.manager.session_for("alpha")
        b = self.new_manager(user_sid=SECOND)
        attempt = b.begin_login("alpha")
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")  # not LOGGING_IN
        # A's lease means nothing in B's scope (same source id and generation 1).
        self.assertFalse(b.mark_invalid(a_lease))
        self.assertFalse(b.record_check(a_lease, "invalid"))
        self.assertFalse(b.save_rotated(a_lease, state("forged")))
        self.assertTrue(b.end_login(attempt, "LOGIN_WINDOW_CLOSED"))
        b.disconnect("alpha")
        b.disconnect("beta")
        self.assertEqual(b.recover(), ())
        self.assertEqual(self.files(), ["session-1.bin"])
        status = self.manager.status("alpha")
        self.assertEqual((status["state"], status["error_code"], status["authenticated_at"]),
                         ("CONNECTED", None, T0.isoformat()))
        self.assertEqual(self.lease(self.manager), (1, state("first"), T0))
        self.assertEqual(self.manager.store.get("alpha")["generation"], 1)
        self.assertEqual(b.store.get("alpha")["generation"], 1)  # B's own: one disconnect

    def test_the_first_accounts_disconnect_never_touches_the_second(self):
        self.connect(value="first")
        b = self.new_manager(user_sid=SECOND)
        self.connect(value="second", manager=b)
        self.manager.disconnect("alpha")
        self.assertEqual(self.files(), [])
        self.assertEqual(self.files(manager=b), ["session-1.bin"])
        self.assertEqual(self.lease(b), (1, state("second"), T0))

    def test_each_account_has_its_own_row_and_lock(self):
        self.connect(value="first")
        b = self.new_manager(user_sid=SECOND)
        self.connect(value="second", manager=b)
        connection = sqlite3.connect(self.root / "state" / "downloads.sqlite3")
        try:
            rows = connection.execute(f"SELECT account_sid, source_id, session_state, generation FROM {TABLE} "
                                      "ORDER BY account_sid").fetchall()
        finally:
            connection.close()
        self.assertEqual(rows, [(FIRST, "alpha", "ACTIVE", 1), (SECOND, "alpha", "ACTIVE", 1)])
        self.assertIsNot(b._lock("alpha"), self.manager._lock("alpha"))
        self.assertIs(self.new_manager()._lock("alpha"), self.manager._lock("alpha"))

    def test_the_account_comes_from_the_vault_and_a_store_of_another_account_is_refused(self):
        self.assertEqual(self.manager.account_sid, FIRST)
        self.assertEqual(self.manager.store.account_sid, FIRST)
        store = AccountStore(self.root / "state" / "downloads.sqlite3", SECOND)
        try:
            vault = SessionVault(self.root, protector=self.protector, acl=GoodAcl(FIRST))
            with self.assertRaises(ValueError):
                AccountManager(self.root, CONFIG, store=store, vault=vault, clock=self.clock)
        finally:
            store.close()


class StoreScopeTest(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.database = Path(self._temp.name) / "state" / "downloads.sqlite3"
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.close()
        self._temp.cleanup()

    def store(self, sid=FIRST):
        store = AccountStore(self.database, sid)
        self.stores.append(store)
        return store

    def test_unknown_columns_and_states_are_refused(self):
        store = self.store()
        store.ensure("alpha")
        with self.assertRaises(ValueError):
            store.update_if("alpha", {}, password="x")
        with self.assertRaises(ValueError):
            store.update_if("alpha", {}, account_sid=SECOND)  # a row never moves to another account
        with self.assertRaises(ValueError):
            store.update_if("alpha", {}, session_state="CONNECTED")
        self.assertTrue(store.update_if("alpha", {"generation": 0}, generation=1))
        self.assertFalse(store.update_if("alpha", {"generation": 0}, generation=2))

    def test_a_store_needs_a_real_sid(self):
        for sid in ("alpha", "", None, "S-1-5-21-1-2-3-1001' OR '1'='1", "S-1"):
            with self.subTest(sid=sid), self.assertRaises(ValueError):
                AccountStore(self.database, sid)
        self.assertFalse(self.database.exists())

    def test_two_accounts_change_only_their_own_rows(self):
        first, second = self.store(FIRST), self.store(SECOND)
        first.ensure("alpha")
        self.assertIsNone(second.get("alpha"))
        second.ensure("alpha")
        self.assertTrue(first.update_if("alpha", {"generation": 0}, generation=5, session_state="ACTIVE"))
        self.assertEqual((second.get("alpha")["generation"], second.get("alpha")["session_state"]), (0, "NONE"))
        self.assertTrue(second.update_if("alpha", {"generation": 0}, generation=1))
        self.assertEqual(first.get("alpha")["generation"], 5)

    def test_the_unscoped_table_of_the_m1_draft_is_left_alone(self):
        """Made-up database of the unreleased draft: nothing is migrated, read, changed or dropped."""
        self.database.parent.mkdir(parents=True)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("CREATE TABLE source_accounts (source_id TEXT PRIMARY KEY, session_state TEXT, "
                               "generation INTEGER, updated_at TEXT)")
            connection.execute("INSERT INTO source_accounts VALUES ('alpha', 'ACTIVE', 7, 'x')")
            connection.execute("CREATE TABLE download_settings (key TEXT PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO download_settings VALUES ('k', 'v')")
            connection.commit()
        finally:
            connection.close()
        store = self.store()
        self.assertIsNone(store.get("alpha"))
        store.ensure("alpha")
        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute("SELECT * FROM source_accounts").fetchall(),
                             [("alpha", "ACTIVE", 7, "x")])
            self.assertEqual(connection.execute("SELECT * FROM download_settings").fetchall(), [("k", "v")])
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({TABLE})")]
            keys = [row[1] for row in sorted(connection.execute(f"PRAGMA table_info({TABLE})"),
                                             key=lambda row: row[5]) if row[5]]
        finally:
            connection.close()
        self.assertEqual(columns[:2], ["account_sid", "source_id"])
        self.assertEqual(keys, ["account_sid", "source_id"])  # the primary key, in order


if __name__ == "__main__":
    unittest.main()
