"""The session vault (download_account_vault + download_account_winsec) with the real Windows DPAPI and ACL,
on made-up sessions in temporary roots under the install's temp/. No user's session is ever opened."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import download_account_vault as vault_module
from biliflow import job_purge, recycle_bin
from biliflow.download_account_vault import (
    MAGIC,
    MAX_STATE_BYTES,
    SessionVault,
    VaultCorrupt,
    VaultIOError,
    VaultMissing,
    VaultRefused,
)
from biliflow.download_account_winsec import (
    ADMINISTRATORS_SID,
    CRYPTPROTECT_LOCAL_MACHINE,
    PROTECT_FLAGS,
    SYSTEM_SID,
    AclReport,
    DpapiProtector,
    PrivateFolderAcl,
    ProtectorRejected,
)

TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
WINDOWS = os.name == "nt"
CANARY = "BF-CANARY-cookie-7f3a9c"
SIGNED = "2026-10-07T10:00:00+00:00"


def state(value: str = CANARY) -> dict:
    return {"cookies": [{"name": "sid", "value": value, "domain": "portal.example", "path": "/",
                         "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"}],
            "origins": [{"origin": "https://portal.example", "localStorage": [{"name": "k", "value": value}]}]}


class TempRoot:
    sid = "S-1-5-21-1-2-3-1001"  # FakeAcl's account; RealVaultTest uses the account running the tests

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def user_folder(self, root=None) -> Path:
        return (root or self.root) / "state" / "source-accounts" / self.sid

    def folder(self, source_id="alpha", root=None) -> Path:
        return self.user_folder(root) / source_id


@unittest.skipUnless(WINDOWS, "DPAPI and Windows ACLs")
class RealVaultTest(TempRoot, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.vault = SessionVault(self.root)
        self.acl = PrivateFolderAcl()
        self.sid = self.acl.user_sid

    def test_dpapi_is_for_the_current_user_only(self):
        self.assertEqual(PROTECT_FLAGS & CRYPTPROTECT_LOCAL_MACHINE, 0)

    def test_a_session_round_trips_encrypted_in_a_private_folder(self):
        self.vault.write("alpha", 1, SIGNED, state())
        stored = self.vault.read("alpha", 1)
        self.assertEqual((stored.source_id, stored.generation, stored.authenticated_at), ("alpha", 1, SIGNED))
        self.assertEqual(stored.state, state())
        self.assertNotIn(CANARY, repr(stored))
        self.assertEqual(sorted(path.name for path in self.folder().iterdir()), ["session-1.bin"])  # no .tmp
        self.assertEqual(self.vault.source_folder("alpha"), self.folder())
        raw = (self.folder() / "session-1.bin").read_bytes()
        self.assertTrue(raw.startswith(MAGIC))
        self.assertNotIn(CANARY.encode(), raw)
        private = {self.acl.user_sid, SYSTEM_SID, ADMINISTRATORS_SID}
        base = self.acl.inspect(self.user_folder())
        self.assertEqual(base.problems(self.acl.user_sid, protected=True), [])
        self.assertTrue(base.protected)
        self.assertEqual({sid for sid, _mask in base.allowed}, private)
        for path in (self.folder(), self.folder() / "session-1.bin"):
            report = self.acl.inspect(path)
            self.assertEqual(report.problems(self.acl.user_sid, protected=False), [])
            self.assertEqual({sid for sid, _mask in report.allowed}, private)

    def test_wrong_entropy_and_damaged_blobs_are_rejected(self):
        protector = DpapiProtector()
        blob = protector.protect(b"made-up data", b"entropy-a")
        self.assertEqual(protector.unprotect(blob, b"entropy-a"), b"made-up data")
        for data, entropy in ((blob, b"entropy-b"), (blob[: len(blob) // 2], b"entropy-a"), (b"junk", b"entropy-a")):
            with self.subTest(size=len(data), entropy=entropy):
                with self.assertRaises(ProtectorRejected):
                    protector.unprotect(data, entropy)

    def test_a_file_moved_to_another_source_or_generation_does_not_open(self):
        self.vault.write("alpha", 1, SIGNED, state())
        self.vault.write("beta", 1, SIGNED, state("other"))
        raw = (self.folder() / "session-1.bin").read_bytes()
        (self.folder("beta") / "session-1.bin").write_bytes(raw)
        with self.assertRaises(VaultCorrupt):
            self.vault.read("beta", 1)
        (self.folder() / "session-2.bin").write_bytes(raw)
        with self.assertRaises(VaultCorrupt):
            self.vault.read("alpha", 2)

    def test_a_damaged_file_is_corrupt_and_kept(self):
        self.vault.write("alpha", 1, SIGNED, state())
        path = self.folder() / "session-1.bin"
        for raw in (b"not a session", MAGIC + b"\x01\x02\x03", path.read_bytes()[:-20]):
            with self.subTest(size=len(raw)):
                path.write_bytes(raw)
                with self.assertRaises(VaultCorrupt):
                    self.vault.read("alpha", 1)
                self.assertTrue(path.exists())

    def test_a_missing_file_or_folder_is_missing(self):
        with self.assertRaises(VaultMissing):
            self.vault.read("alpha", 1)
        self.vault.write("alpha", 1, SIGNED, state())
        with self.assertRaises(VaultMissing):
            self.vault.read("alpha", 2)
        self.assertFalse(self.vault.exists("alpha", 2))
        self.assertTrue(self.vault.exists("alpha", 1))

    def test_a_read_error_is_an_io_error_and_keeps_the_file(self):
        self.vault.write("alpha", 1, SIGNED, state())
        with patch("builtins.open", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(VaultIOError):
                self.vault.read("alpha", 1)
        self.assertEqual(self.vault.read("alpha", 1).state, state())

    def test_a_failed_write_keeps_the_old_file_and_leaves_no_temp_file(self):
        self.vault.write("alpha", 1, SIGNED, state("first"))
        with patch.object(vault_module.os, "replace", side_effect=OSError(28, "disk full")):
            with self.assertRaises(VaultIOError):
                self.vault.write("alpha", 1, SIGNED, state("second"))
        self.assertEqual(self.vault.read("alpha", 1).state, state("first"))
        self.assertEqual(sorted(path.name for path in self.folder().iterdir()), ["session-1.bin"])

    def test_a_file_held_for_a_moment_is_saved_after_a_retry(self):
        self.vault.write("alpha", 1, SIGNED, state("first"))
        real_replace, calls = os.replace, []

        def held_once(source, target):
            calls.append(target)
            if len(calls) == 1:
                raise PermissionError(32, "being used by another process")
            return real_replace(source, target)

        with patch.object(vault_module.os, "replace", side_effect=held_once), \
                patch.object(vault_module.time, "sleep") as sleep:
            self.vault.write("alpha", 1, SIGNED, state("second"))
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(vault_module.REPLACE_PAUSE_SECONDS)
        self.assertEqual(self.vault.read("alpha", 1).state, state("second"))
        with patch.object(vault_module.os, "replace", side_effect=PermissionError(32, "in use")) as replace, \
                patch.object(vault_module.time, "sleep"):
            with self.assertRaises(VaultIOError):
                self.vault.write("alpha", 1, SIGNED, state("third"))
        self.assertEqual(replace.call_count, vault_module.REPLACE_ATTEMPTS)
        self.assertEqual(self.vault.read("alpha", 1).state, state("second"))
        self.assertEqual(sorted(path.name for path in self.folder().iterdir()), ["session-1.bin"])

    def test_a_root_outside_the_install_temp_is_refused_and_nothing_is_made(self):
        for root in (recycle_bin.INSTALL_ROOT / "temp", recycle_bin.INSTALL_ROOT.parent,
                     recycle_bin.INSTALL_ROOT / "elsewhere-not-made"):
            with self.subTest(root=root.name):
                with self.assertRaises(VaultRefused):
                    SessionVault(root).write("alpha", 1, SIGNED, state())
        self.assertFalse((recycle_bin.INSTALL_ROOT / "temp" / "state").exists())
        self.assertFalse((recycle_bin.INSTALL_ROOT / "elsewhere-not-made").exists())
        with patch.object(job_purge, "INSTALL_ROOT", self.root / "not-the-install"):
            with self.assertRaises(VaultRefused):
                self.vault.write("alpha", 1, SIGNED, state())
        self.assertFalse((self.root / "state").exists())

    def test_junctions_are_refused_at_every_level(self):
        import _winapi

        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        for level in ("state", "state/source-accounts", f"state/source-accounts/{self.sid}",
                      f"state/source-accounts/{self.sid}/alpha"):
            with self.subTest(level=level), TemporaryDirectory(dir=TEMP_PARENT) as name:
                root = Path(name)
                if level.endswith("alpha"):
                    SessionVault(root).write("beta", 1, SIGNED, state())  # real folders above the junction
                elif level != "state":
                    (root / level).parent.mkdir(parents=True)
                link = root / level
                _winapi.CreateJunction(str(elsewhere), str(link))
                with self.assertRaises(VaultRefused):
                    SessionVault(root).write("alpha", 1, SIGNED, state())
                with self.assertRaises(VaultRefused):
                    SessionVault(root).read("alpha", 1)
                self.assertEqual(list(elsewhere.iterdir()), [])
                os.rmdir(link)  # removes the junction only

    def test_a_linked_session_file_is_refused(self):
        self.vault.write("alpha", 1, SIGNED, state())
        target = self.root / "outside.bin"
        target.write_bytes((self.folder() / "session-1.bin").read_bytes())
        try:
            os.symlink(target, self.folder() / "session-2.bin")
        except OSError:
            self.skipTest("this account may not create symbolic links")
        with self.assertRaises(VaultRefused):
            self.vault.read("alpha", 2)

    def test_a_folder_with_an_inherited_acl_is_refused_on_read_and_secured_on_write(self):
        self.folder().mkdir(parents=True)  # plain folders: inherited ACL
        with self.assertRaises(VaultRefused):
            self.vault.read("alpha", 1)
        self.vault.write("alpha", 1, SIGNED, state())
        base = self.acl.inspect(self.user_folder())
        self.assertEqual(base.problems(self.acl.user_sid, protected=True), [])
        self.assertEqual(self.vault.read("alpha", 1).state, state())

    def test_remove_deletes_only_the_vaults_own_names(self):
        self.vault.write("alpha", 2, SIGNED, state())
        folder = self.folder()
        for name in ("session-1.bin", "session-1.bin.0123456789abcdef.tmp", "notes.txt", "session-x.bin",
                     "session-3.bin.bak"):
            (folder / name).write_bytes(b"x")
        self.assertEqual(self.vault.remove("alpha", keep=2), 2)
        self.assertEqual(sorted(path.name for path in folder.iterdir()),
                         ["notes.txt", "session-2.bin", "session-3.bin.bak", "session-x.bin"])
        self.assertEqual(self.vault.remove("alpha"), 1)
        self.assertFalse((folder / "session-2.bin").exists())
        self.assertEqual(self.vault.remove("gamma"), 0)

    def test_bad_ids_generations_and_sizes_are_refused(self):
        for source_id, generation in (("../x", 1), ("Alpha", 1), ("con", 1), ("nul", 1), ("com1", 1), ("lpt9", 1),
                                      ("alpha", 0), ("alpha", -1), ("alpha", True), ("alpha", 10 ** 13),
                                      ("alpha", "1")):
            with self.subTest(source_id=source_id, generation=generation):
                with self.assertRaises(VaultRefused):
                    self.vault.write(source_id, generation, SIGNED, state())
        with self.assertRaises(VaultRefused):
            self.vault.write("alpha", 1, SIGNED, state("x" * MAX_STATE_BYTES))
        self.assertFalse(self.folder().exists())


class FakeAcl:
    """An ACL report a test chooses, to check the refusals without changing a real folder's rights."""

    def __init__(self, owner=None, strangers=(), user_sid="S-1-5-21-1-2-3-1001"):
        self.user_sid = user_sid
        self.owner = owner or self.user_sid
        self.strangers = tuple(strangers)
        self.secured = []

    def create_directory(self, path):
        os.mkdir(path)

    def secure(self, path):
        self.secured.append(path)

    def inspect(self, path):
        allowed = ((self.user_sid, 0x1F01FF), (SYSTEM_SID, 0x1F01FF), (ADMINISTRATORS_SID, 0x1F01FF),
                   *((sid, 0x1301BF) for sid in self.strangers))
        return AclReport(self.owner, True, allowed, 0)


class XorProtector:
    def protect(self, data, entropy):
        return bytes(byte ^ 0x5A for byte in data)

    def unprotect(self, blob, entropy):
        return bytes(byte ^ 0x5A for byte in blob)


class AclRefusalTest(TempRoot, unittest.TestCase):
    def test_a_folder_that_lets_another_account_in_is_never_used(self):
        acl = FakeAcl(strangers=("S-1-5-11",))
        with self.assertRaises(VaultRefused):
            SessionVault(self.root, protector=XorProtector(), acl=acl).write("alpha", 1, SIGNED, state())
        self.assertEqual(len(acl.secured), 1)  # one attempt to set the private ACL, then the check failed
        self.assertFalse(self.folder().exists())

    def test_a_folder_owned_by_another_account_is_not_repaired(self):
        acl = FakeAcl(owner="S-1-5-21-9-9-9-1002", strangers=("S-1-5-11",))
        with self.assertRaises(VaultRefused):
            SessionVault(self.root, protector=XorProtector(), acl=acl).write("alpha", 1, SIGNED, state())
        self.assertEqual(acl.secured, [])


class OtherAccountTest(TempRoot, unittest.TestCase):
    def test_each_windows_account_has_its_own_folder(self):
        first = SessionVault(self.root, protector=XorProtector(), acl=FakeAcl())
        second = SessionVault(self.root, protector=XorProtector(), acl=FakeAcl(user_sid="S-1-5-21-9-9-9-1002"))
        first.write("alpha", 1, SIGNED, state("first"))
        with self.assertRaises(VaultMissing):
            second.read("alpha", 1)
        self.assertFalse(second.exists("alpha", 1))
        second.write("alpha", 2, SIGNED, state("second"))
        self.assertEqual(second.remove("alpha", keep=2), 0)
        self.assertEqual(second.remove("alpha"), 1)
        self.assertEqual(first.read("alpha", 1).state, state("first"))  # never touched by the second account
        self.assertNotEqual(first.source_folder("alpha"), second.source_folder("alpha"))

    def test_an_account_id_that_is_not_a_sid_is_refused(self):
        for sid in ("..", "S-1-5-21-1\\..\\x", "", None, "S-1"):
            with self.subTest(sid=sid):
                vault = SessionVault(self.root, protector=XorProtector(), acl=FakeAcl(user_sid=sid))
                with self.assertRaises(VaultRefused):
                    vault.write("alpha", 1, SIGNED, state())
                with self.assertRaises(VaultRefused):
                    vault.browser_folder()
        self.assertFalse((self.root / "state" / "source-accounts").exists())
        self.assertFalse((self.root / "temp" / "source-account-browser").exists())


class BrowserFolderTest(TempRoot, unittest.TestCase):
    """The private folder of the session browser's temporary profiles (M2a)."""

    @unittest.skipUnless(WINDOWS, "Windows ACLs")
    def test_the_folder_is_private_and_a_profile_inside_inherits_it(self):
        vault, acl = SessionVault(self.root), PrivateFolderAcl()
        folder = vault.browser_folder()
        self.assertEqual(folder, self.root.resolve() / "temp" / "source-account-browser" / acl.user_sid)
        self.assertEqual(acl.inspect(folder).problems(acl.user_sid, protected=True), [])
        profile = folder / "run-0123456789abcdef"
        os.mkdir(profile)  # as SessionBrowser makes it
        self.assertEqual(acl.inspect(profile).problems(acl.user_sid, protected=False), [])
        self.assertEqual(vault.browser_folder(), folder)  # the second call keeps it as it is

    @unittest.skipUnless(WINDOWS, "junctions")
    def test_junctions_are_refused_at_every_level(self):
        import _winapi

        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        sid = PrivateFolderAcl().user_sid
        for level in ("temp", "temp/source-account-browser", f"temp/source-account-browser/{sid}"):
            with self.subTest(level=level), TemporaryDirectory(dir=TEMP_PARENT) as name:
                root = Path(name)
                if level != "temp":
                    (root / level).parent.mkdir(parents=True)
                link = root / level
                _winapi.CreateJunction(str(elsewhere), str(link))
                with self.assertRaises(VaultRefused):
                    SessionVault(root).browser_folder()
                self.assertEqual(list(elsewhere.iterdir()), [])
                os.rmdir(link)  # removes the junction only

    def test_a_folder_another_account_owns_is_refused_and_each_account_has_its_own(self):
        with self.assertRaises(VaultRefused):
            SessionVault(self.root, acl=FakeAcl(owner="S-1-5-21-9-9-9-1002", strangers=("S-1-5-11",))).browser_folder()
        first = SessionVault(self.root, acl=FakeAcl()).browser_folder()
        second = SessionVault(self.root, acl=FakeAcl(user_sid="S-1-5-21-9-9-9-1002")).browser_folder()
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, second.parent)

    def test_a_root_outside_the_install_temp_is_refused(self):
        with self.assertRaises(VaultRefused):
            SessionVault(Path(os.environ.get("SystemDrive", "C:") + "\\"), acl=FakeAcl()).browser_folder()

    def test_profiles_are_fresh_and_only_run_folders_are_removed(self):
        vault = SessionVault(self.root, acl=FakeAcl())
        folder = vault.browser_folder()
        first, second = vault.new_browser_profile(), vault.new_browser_profile()
        self.assertNotEqual(first, second)
        for profile in (first, second):
            self.assertEqual(profile.parent, folder)
            self.assertRegex(profile.name, r"^run-[0-9a-f]{16}$")
            self.assertEqual(list(profile.iterdir()), [])
        (first / "Default").mkdir()
        (first / "Default" / "Cookies").write_bytes(b"fake")
        keep = folder / "keep"
        keep.mkdir()
        (folder / "notes.txt").write_text("x")
        outside = self.root / "run-0123456789abcdef"
        outside.mkdir()
        for refused in (keep, outside, folder, folder / "run-short"):
            with self.assertRaises(VaultRefused):
                vault.remove_browser_profile(refused)
        self.assertTrue(vault.remove_browser_profile(first))
        self.assertFalse(first.exists())
        self.assertTrue(vault.remove_browser_profile(first))  # already gone counts as removed
        left = folder / "run-fedcba9876543210"  # what a killed run leaves
        left.mkdir()
        self.assertEqual(vault.remove_browser_profiles(), 0)
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ["keep", "notes.txt"])
        self.assertTrue(outside.is_dir())

    def test_a_profile_that_cannot_be_deleted_is_counted_and_kept_for_the_next_start(self):
        vault = SessionVault(self.root, acl=FakeAcl())
        profile = vault.new_browser_profile()

        def held(path, *args, **kwargs):  # Edge helpers or an antivirus still hold a file
            raise PermissionError("in use")
        with patch.object(vault_module.shutil, "rmtree", held), patch.object(vault_module, "REPLACE_PAUSE_SECONDS", 0):
            self.assertFalse(vault.remove_browser_profile(profile))
            self.assertEqual(vault.remove_browser_profiles(), 1)
        self.assertTrue(profile.is_dir())
        self.assertEqual(vault.remove_browser_profiles(), 0)
        self.assertFalse(profile.exists())

    @unittest.skipUnless(WINDOWS, "Windows sharing modes")
    def test_a_pinned_profile_and_every_folder_above_it_cannot_be_moved(self):
        vault = SessionVault(self.root, acl=FakeAcl())
        profile = vault.new_browser_profile()
        pin = vault.pin_browser_profile(profile)
        try:
            for folder in (profile, profile.parent, profile.parent.parent, self.root / "temp"):
                with self.subTest(folder=folder.name), self.assertRaises(OSError):
                    os.rename(folder, folder.with_name(folder.name + "-moved"))  # what a swap needs first
        finally:
            pin.close()
        self.assertTrue(vault.remove_browser_profile(profile))
        self.assertEqual(list(vault.browser_folder().iterdir()), [])

    def test_pinning_refuses_any_folder_but_a_fresh_profile_and_leaves_nothing_there(self):
        vault = SessionVault(self.root, acl=FakeAcl())
        folder = vault.browser_folder()
        keep = folder / "keep"
        outside = self.root / "run-0123456789abcdef"
        for refused in (keep, outside):
            refused.mkdir()
            with self.assertRaises(VaultRefused):
                vault.pin_browser_profile(refused)
            self.assertEqual(list(refused.iterdir()), [])

    @unittest.skipUnless(WINDOWS, "junctions")
    def test_pinning_refuses_a_profile_whose_account_folder_was_swapped(self):
        import _winapi

        vault = SessionVault(self.root, acl=FakeAcl())
        profile = vault.new_browser_profile()
        account = profile.parent
        elsewhere = self.root / "elsewhere"
        (elsewhere / profile.name).mkdir(parents=True)
        os.rename(account, self.root / "real-account")  # another account swaps the folder before the pin
        _winapi.CreateJunction(str(elsewhere), str(account))
        try:
            with self.assertRaises(VaultRefused):
                vault.pin_browser_profile(profile)
            self.assertEqual(list((elsewhere / profile.name).iterdir()), [])
        finally:
            os.rmdir(account)  # the junction only

    @unittest.skipUnless(WINDOWS, "junctions")
    def test_a_junction_in_place_of_a_profile_is_removed_without_following_it(self):
        import _winapi

        vault = SessionVault(self.root, acl=FakeAcl())
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.txt").write_text("x")
        link = vault.browser_folder() / "run-00112233445566ff"
        _winapi.CreateJunction(str(elsewhere), str(link))
        self.assertEqual(vault.remove_browser_profiles(), 0)
        self.assertFalse(os.path.lexists(link))
        self.assertEqual((elsewhere / "keep.txt").read_text(), "x")
        profile = vault.new_browser_profile()  # a junction inside a profile: the link goes, its target stays
        _winapi.CreateJunction(str(elsewhere), str(profile / "inner"))
        self.assertTrue(vault.remove_browser_profile(profile))
        self.assertFalse(os.path.lexists(profile))
        self.assertEqual((elsewhere / "keep.txt").read_text(), "x")


if __name__ == "__main__":
    unittest.main()
