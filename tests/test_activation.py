"""Activation keys (activation.py) and the issuing tool (keygen/issue_key.py), with the TEST ONLY key
pair in tests/fixtures/activation. Machine identities are made up; roots are temporary folders under
the install's temp/. Nothing here reads a real issuing key or a real ledger."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import struct
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from biliflow import activation
from biliflow.activation import (
    ActivationError,
    KeyFields,
    MachineCode,
    MachineIdentity,
    machine_code,
    parse_smbios_uuid,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_PARENT = REPO_ROOT / "temp"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "activation"
PUBLIC = bytes.fromhex((FIXTURES / "TEST-ONLY-public-key.hex").read_text(encoding="ascii").strip())
_spec = importlib.util.spec_from_file_location("issue_key", REPO_ROOT / "keygen" / "issue_key.py")
keygen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(keygen)
TEST_KEY = keygen.load_private_key((FIXTURES / "TEST-ONLY-private-key.pem").read_text(encoding="ascii"), None)

PC = MachineIdentity("0f8fad5b-d9cb-469f-a165-70867728950e", "7c9e6679-7425-40de-944b-e07fc1f90ae7", "1A2B3C4D")
REINSTALLED = MachineIdentity("16fd2706-8baf-433b-82eb-8c7fada847da", PC.board_uuid, "5E6F7A8B")
NEW_BOARD = MachineIdentity(PC.machine_guid, "a8098c1a-f86e-11da-bd1a-00112444be1e", PC.volume_serial)
ISSUED = date(2026, 10, 11)


def at(day: date, hours: float = 12) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() + hours * 3600


def make_key(kind: str = "perpetual", identity: MachineIdentity = PC, issued: date = ISSUED, set_id: int = 7) -> str:
    return keygen.sign(TEST_KEY, keygen.make_fields(machine_code(identity).text(), set_id, kind, issued))


def smbios(version: tuple[int, int], uuid_bytes: bytes) -> bytes:
    bios = bytes([0, 0x18]) + b"\0\0" + bytes(0x14) + b"Vendor\0\0"
    system = bytes([1, 0x1b]) + b"\1\0" + bytes(4) + uuid_bytes + bytes(0x1b - 24) + b"\0\0"
    end = bytes([127, 4]) + b"\2\0" + b"\0\0"
    table = bios + system + end
    return bytes([0, version[0], version[1], 0]) + struct.pack("<I", len(table)) + table


class MachineCodeTest(unittest.TestCase):
    def test_text_round_trips_and_tolerates_typing(self):
        code = machine_code(PC)
        text = code.text()
        self.assertEqual(len(text.replace("-", "")), activation.MACHINE_CODE_CHARS)
        self.assertEqual(MachineCode.parse(text), code)
        self.assertEqual(MachineCode.parse(" " + text.lower().replace("-", " ") + "\n"), code)
        self.assertEqual(MachineCode.parse(text.replace("O", "0").replace("I", "1")), code)

    def test_a_typo_or_wrong_length_is_refused(self):
        text = machine_code(PC).text().replace("-", "")
        typo = text[:10] + ("A" if text[10] != "A" else "B") + text[11:]
        for bad in (typo, text[:-1], text + "A", "", "not a code"):
            with self.subTest(bad), self.assertRaises(ActivationError) as caught:
                MachineCode.parse(bad)
            self.assertEqual(caught.exception.state, activation.INVALID)

    def test_parts_are_salted_hashes_of_normalized_values(self):
        code = machine_code(PC)
        for raw in ("0f8fad5b", "1A2B3C4D", "7c9e6679"):
            self.assertNotIn(raw.upper(), code.text().replace("-", ""))
        upper = MachineIdentity(PC.machine_guid.upper(), PC.board_uuid.upper(), PC.volume_serial.lower())
        self.assertEqual(machine_code(upper), code)
        self.assertEqual(len({part for part in code.hashes}), 3)

    def test_two_of_three_parts_match(self):
        code = machine_code(PC)
        self.assertEqual(machine_code(PC).matches(code), 3)
        self.assertEqual(machine_code(NEW_BOARD).matches(code), 2)
        self.assertEqual(machine_code(REINSTALLED).matches(code), 1)

    def test_an_unknown_part_never_matches(self):
        fake_board = MachineIdentity(PC.machine_guid, "03000200-0400-0500-0006-000700080009", PC.volume_serial)
        code = machine_code(fake_board)
        self.assertEqual(code.known(), 2)
        self.assertEqual(code.hashes[1], bytes(8))
        self.assertEqual(machine_code(MachineIdentity(PC.machine_guid, None, PC.volume_serial)).matches(code), 2)
        self.assertEqual(machine_code(MachineIdentity(None, None, None)).known(), 0)
        self.assertEqual(machine_code(MachineIdentity("x", None, "00000000")).known(), 1)

    def test_placeholder_board_uuids_are_unknown(self):
        for text in ("00000000-0000-0000-0000-000000000000", "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
                     "03000200-0400-0500-0006-000700080009", "not-a-uuid", ""):
            self.assertIsNone(activation.usable_board_uuid(text), text)
        self.assertEqual(activation.usable_board_uuid(PC.board_uuid.upper()), PC.board_uuid)

    def test_smbios_system_uuid_is_read_by_version(self):
        value = uuid.UUID(PC.board_uuid)
        self.assertEqual(parse_smbios_uuid(smbios((3, 4), value.bytes_le)), PC.board_uuid)
        self.assertEqual(parse_smbios_uuid(smbios((2, 4), value.bytes)), PC.board_uuid)
        self.assertIsNone(parse_smbios_uuid(smbios((3, 4), bytes(16))))
        self.assertIsNone(parse_smbios_uuid(smbios((3, 4), value.bytes_le)[:30]))
        self.assertIsNone(parse_smbios_uuid(b"\0\3"))

    @unittest.skipIf(sys.platform == "win32", "non-Windows refusal")
    def test_reading_the_identity_needs_windows(self):
        with self.assertRaises(ActivationError) as caught:
            activation.read_machine_identity()
        self.assertEqual(caught.exception.state, activation.UNAVAILABLE)

    @unittest.skipUnless(sys.platform == "win32", "reads this PC's identifiers")
    def test_this_pc_has_at_least_two_known_parts(self):
        identity = activation.read_machine_identity()
        self.assertGreaterEqual(machine_code(identity).known(), 2)
        self.assertEqual(activation.read_machine_identity(), identity)


class KeyFormatTest(unittest.TestCase):
    def test_a_key_round_trips(self):
        text = make_key("month:3")
        self.assertEqual(len(text.replace("-", "")), activation.KEY_CHARS)
        self.assertEqual(activation.KEY_CHARS, 172)
        fields = activation.verify_key_text(text, PUBLIC)
        self.assertEqual((fields.set_id, fields.kind, fields.issued, fields.expires),
                         (7, "month:3", ISSUED, date(2027, 1, 11)))
        self.assertEqual(fields.machine, machine_code(PC))
        perpetual = activation.verify_key_text(make_key(), PUBLIC)
        self.assertEqual((perpetual.kind, perpetual.expires, perpetual.months), ("perpetual", None, 0))

    def test_month_ends_are_clamped(self):
        self.assertEqual(activation.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(activation.add_months(date(2027, 12, 15), 1), date(2028, 1, 15))
        self.assertEqual(activation.add_months(date(2026, 10, 11), 12), date(2027, 10, 11))

    def test_any_change_breaks_the_key(self):
        compact = make_key().replace("-", "")
        middle = compact[:80] + ("A" if compact[80] != "A" else "B") + compact[81:]
        raw = base64.b32decode(compact + "====")
        trailing = base64.b32encode(raw).decode().rstrip("=")
        last = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".index(trailing[-1])
        loose = trailing[:-1] + "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"[last | 1]
        self.assertNotEqual(loose, compact)
        for bad in (middle, loose, compact[:-4], compact + "AAAA", ""):
            with self.subTest(bad[:12]), self.assertRaises(ActivationError):
                activation.verify_key_text(bad, PUBLIC)
        other = keygen.public_key_bytes(keygen.new_private_key())
        with self.assertRaises(ActivationError):
            activation.verify_key_text(compact, other)

    def test_invalid_fields_are_never_encoded(self):
        machine = machine_code(PC)
        cases = [
            KeyFields(machine, 1, "perpetual", ISSUED, ISSUED + timedelta(days=30), bytes(8)),
            KeyFields(machine, 1, "month:1", ISSUED, None, bytes(8)),
            KeyFields(machine, 1, "month:1", ISSUED, ISSUED, bytes(8)),
            KeyFields(machine, 1, "month:121", ISSUED, ISSUED + timedelta(days=9999), bytes(8)),
            KeyFields(machine, 1, "month:0", ISSUED, ISSUED + timedelta(days=1), bytes(8)),
            KeyFields(machine, 1, "perpetual", ISSUED, None, bytes(7)),
            KeyFields(machine, -1, "perpetual", ISSUED, None, bytes(8)),
            KeyFields(machine_code(MachineIdentity("x", None, None)), 1, "perpetual", ISSUED, None, bytes(8)),
        ]
        for fields in cases:
            with self.subTest(fields.kind), self.assertRaises(ActivationError):
                activation.encode_payload(fields)

    def test_only_the_release_key_is_trusted_and_never_the_test_key(self):
        self.assertIsNone(activation._trusted_public_key())
        other = keygen.public_key_bytes(keygen.new_private_key())
        for value, expected in ((PUBLIC.hex(), None), ("zz", None), ("ab" * 16, None), (other.hex(), other)):
            with patch.object(activation, "RELEASE_PUBLIC_KEY_HEX", value):
                self.assertEqual(activation._trusted_public_key(), expected)
        self.assertTrue(keygen.is_test_key(TEST_KEY))


class ActivationStateTest(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.root = Path(self._temp.name)
        trusted = patch.object(activation, "_trusted_public_key", return_value=PUBLIC)
        trusted.start()
        self.addCleanup(trusted.stop)

    def tearDown(self):
        self._temp.cleanup()

    def check(self, now: float, identity: MachineIdentity = PC):
        return activation.check(self.root, now=now, identity=identity)

    def activate(self, key: str, now: float, identity: MachineIdentity = PC):
        return activation.activate(self.root, key, now=now, identity=identity)

    def folder(self) -> Path:
        return self.root / "state" / "activation"

    def test_no_key_then_a_perpetual_key(self):
        self.assertEqual(self.check(at(ISSUED)).state, activation.NO_KEY)
        status = self.activate(make_key(), at(ISSUED))
        self.assertTrue(status.active)
        self.assertEqual((status.kind, status.set_id, status.matched_parts), ("perpetual", 7, 3))
        self.assertEqual(sorted(path.name for path in self.root.rglob("*") if path.is_file()),
                         ["clock.json", "key.json"])
        self.assertEqual(self.check(at(ISSUED + timedelta(days=4000))).state, activation.ACTIVE)

    def test_without_a_release_key_nothing_is_active(self):
        self.activate(make_key(), at(ISSUED))
        with patch.object(activation, "_trusted_public_key", return_value=None):
            self.assertEqual(self.check(at(ISSUED)).state, activation.NOT_CONFIGURED)
            self.assertEqual(self.activate(make_key(), at(ISSUED)).state, activation.NOT_CONFIGURED)

    def test_an_unreadable_key_file_is_invalid(self):
        self.folder().mkdir(parents=True)
        (self.folder() / "key.json").write_text("{nope", encoding="utf-8")
        self.assertEqual(self.check(at(ISSUED)).state, activation.INVALID)
        (self.folder() / "key.json").write_text(json.dumps({"version": 1, "key": "AAAA"}), encoding="utf-8")
        self.assertEqual(self.check(at(ISSUED)).state, activation.INVALID)

    def test_a_month_key_ends_on_its_expiry_day(self):
        self.assertTrue(self.activate(make_key("month:1"), at(ISSUED)).active)
        self.assertTrue(self.check(at(date(2026, 11, 10), 23.9)).active)
        expired = self.check(at(date(2026, 11, 11), 0.1))
        self.assertEqual((expired.state, expired.expires), (activation.EXPIRED, "2026-11-11"))

    def test_another_machine_is_refused_and_nothing_is_stored(self):
        status = self.activate(make_key(identity=REINSTALLED), at(ISSUED))
        self.assertEqual((status.state, status.matched_parts), (activation.WRONG_MACHINE, 1))
        self.assertFalse(self.folder().exists())
        self.assertTrue(self.activate(make_key(identity=NEW_BOARD), at(ISSUED)).active)

    def test_an_expired_key_is_not_stored(self):
        self.assertEqual(self.activate(make_key("month:1"), at(date(2026, 12, 1))).state, activation.EXPIRED)
        self.assertFalse((self.folder() / "key.json").exists())

    def test_a_clock_before_the_issue_day_is_refused(self):
        early = self.activate(make_key("month:1"), at(ISSUED) - 3 * 86400)
        self.assertEqual(early.state, activation.CLOCK_BACK)
        self.assertFalse(self.folder().exists())
        self.assertTrue(self.activate(make_key("month:1"), at(ISSUED, 0) - 47 * 3600).active)

    def test_a_clock_turned_back_more_than_48_hours_is_refused(self):
        self.activate(make_key("month:1"), at(ISSUED))
        later = at(date(2026, 11, 5))
        self.assertTrue(self.check(later).active)
        self.assertEqual(self.check(later - 49 * 3600).state, activation.CLOCK_BACK)
        self.assertTrue(self.check(later - 47 * 3600).active)

    def test_within_the_tolerance_expiry_uses_the_latest_time_seen(self):
        self.activate(make_key("month:1"), at(ISSUED))
        self.assertEqual(self.check(at(date(2026, 11, 11), 6)).state, activation.EXPIRED)
        self.assertEqual(self.check(at(date(2026, 11, 10), 6)).state, activation.EXPIRED)

    def test_the_clock_moves_forward_only_and_not_on_every_check(self):
        self.activate(make_key("month:6"), at(ISSUED))
        first = activation.read_last_seen(self.root, PC)
        self.check(at(ISSUED) + 60)
        self.assertEqual(activation.read_last_seen(self.root, PC), first)
        self.check(at(ISSUED) + 700)
        self.assertEqual(activation.read_last_seen(self.root, PC), int(at(ISSUED)) + 700)
        self.check(at(ISSUED) - 3600)
        self.assertEqual(activation.read_last_seen(self.root, PC), int(at(ISSUED)) + 700)

    def test_a_lost_or_altered_clock_needs_the_month_key_again(self):
        key = make_key("month:6")
        self.activate(key, at(ISSUED))
        clock = self.folder() / "clock.json"
        data = json.loads(clock.read_text(encoding="utf-8"))
        data["last_seen"] -= 30 * 86400
        clock.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.check(at(ISSUED) + 86400).state, activation.NEEDS_RECHECK)
        clock.unlink()
        self.assertEqual(self.check(at(ISSUED) + 86400).state, activation.NEEDS_RECHECK)
        self.assertFalse(clock.exists())
        self.assertTrue(self.activate(key, at(ISSUED) + 86400).active)
        self.assertTrue(self.check(at(ISSUED) + 86400).active)

    def test_a_clock_written_for_another_machine_does_not_count(self):
        self.activate(make_key("month:6", identity=NEW_BOARD), at(ISSUED), identity=NEW_BOARD)
        self.assertIsNone(activation.read_last_seen(self.root, PC))
        self.assertIsNotNone(activation.read_last_seen(self.root, NEW_BOARD))

    def test_a_perpetual_key_rebuilds_a_lost_clock(self):
        self.activate(make_key(), at(ISSUED))
        (self.folder() / "clock.json").unlink()
        self.assertTrue(self.check(at(ISSUED) + 86400).active)
        self.assertEqual(activation.read_last_seen(self.root, PC), int(at(ISSUED)) + 86400)

    def test_the_stored_key_and_state_carry_no_identifier(self):
        self.activate(make_key("month:1"), at(ISSUED))
        stored = "".join(path.read_text(encoding="utf-8") for path in self.folder().iterdir())
        for raw in (PC.machine_guid, PC.board_uuid, PC.volume_serial):
            self.assertNotIn(raw.lower(), stored.lower())

    def test_an_unreadable_identity_is_unavailable(self):
        with patch.object(activation, "read_machine_identity",
                          side_effect=ActivationError(activation.UNAVAILABLE)):
            self.assertEqual(activation.check(self.root, now=at(ISSUED)).state, activation.UNAVAILABLE)
            self.assertEqual(activation.activate(self.root, make_key(), now=at(ISSUED)).state,
                             activation.UNAVAILABLE)

    @unittest.skipIf(sys.platform == "win32", "symlinks need extra rights on Windows")
    def test_a_linked_activation_folder_is_refused(self):
        target = self.root / "elsewhere"
        target.mkdir()
        (self.root / "state").mkdir()
        os.symlink(target, self.folder())
        with self.assertRaises(ActivationError):
            self.activate(make_key(), at(ISSUED))
        self.assertEqual(list(target.iterdir()), [])


class IssuingToolTest(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT)
        self.ledger = Path(self._temp.name) / "ledger.csv"

    def tearDown(self):
        self._temp.cleanup()

    def test_keys_and_ledger_stay_outside_any_checkout(self):
        for path in (REPO_ROOT / "issuer.pem", Path(self._temp.name) / "ledger.csv", REPO_ROOT / "src" / "x"):
            self.assertIsNotNone(keygen.refuse_inside_project(path))
        target = Path(self._temp.name) / "issuer.pem"
        with patch.object(keygen.getpass, "getpass", side_effect=AssertionError("asked")), patch("sys.stderr"):
            self.assertEqual(keygen.main(["init", "--private-key", str(target)]), 1)
            self.assertEqual(keygen.main(["issue", "--private-key", str(target), "--ledger", str(self.ledger),
                                          "--machine", machine_code(PC).text(), "--set", "1",
                                          "--kind", "perpetual", "--customer", "Khách thử"]), 1)
        self.assertFalse(target.exists())
        self.assertFalse(self.ledger.exists())

    def test_the_private_key_is_exported_with_a_passphrase(self):
        key = keygen.new_private_key()
        with self.assertRaises(keygen.ToolError):
            keygen.export_private_key(key, "short")
        text = keygen.export_private_key(key, "mật khẩu đủ dài 123")
        self.assertIn("ENCRYPTED", text)
        self.assertEqual(keygen.public_key_bytes(keygen.load_private_key(text, "mật khẩu đủ dài 123")),
                         keygen.public_key_bytes(key))
        with self.assertRaises(keygen.ToolError):
            keygen.load_private_key(text, "sai mật khẩu rồi nhé")
        with self.assertRaises(keygen.ToolError):
            keygen.load_private_key(PUBLIC.hex(), None)

    def test_a_set_of_keys_is_issued_and_recorded(self):
        machines = [machine_code(PC).text(), machine_code(REINSTALLED).text()]
        keys = keygen.issue(TEST_KEY, self.ledger, machines, set_id=12, kind="month:2", customer="Khách thử",
                            issued=ISSUED)
        rows = keygen.read_ledger(self.ledger)
        self.assertEqual([row["key_id"] for row in rows], [key_id for key_id, _ in keys])
        for (key_id, text), row in zip(keys, rows):
            fields = activation.verify_key_text(text, PUBLIC)
            self.assertEqual((fields.key_id.hex(), fields.set_id, fields.expires), (key_id, 12, date(2026, 12, 11)))
            self.assertEqual((row["customer"], row["set_id"], row["expires"]), ("Khách thử", "12", "2026-12-11"))
        self.assertEqual(len({key_id for key_id, _ in keys}), 2)

    def test_nothing_is_recorded_when_one_machine_code_is_wrong(self):
        with self.assertRaises(ActivationError):
            keygen.issue(TEST_KEY, self.ledger, [machine_code(PC).text(), "AAAA"], set_id=1,
                         kind="perpetual", customer="Khách thử")
        with self.assertRaises(keygen.ToolError):
            keygen.issue(TEST_KEY, self.ledger, [machine_code(PC).text()], set_id=1, kind="perpetual", customer=" ")
        self.assertFalse(self.ledger.exists())

    def test_reissue_keeps_the_terms_and_has_a_limit(self):
        [(original, _)] = keygen.issue(TEST_KEY, self.ledger, [machine_code(PC).text()], set_id=3,
                                       kind="month:6", customer="Khách thử", issued=ISSUED)
        today = date(2026, 12, 1)
        previous = original
        for _ in range(keygen.MAX_REISSUES):
            previous, text = keygen.reissue(TEST_KEY, self.ledger, previous, machine_code(REINSTALLED).text(),
                                            today=today)
            fields = activation.verify_key_text(text, PUBLIC)
            self.assertEqual((fields.set_id, fields.kind, fields.issued, fields.expires),
                             (3, "month:6", today, date(2027, 4, 11)))
        with self.assertRaises(keygen.ToolError):
            keygen.reissue(TEST_KEY, self.ledger, original, machine_code(PC).text(), today=today)
        keygen.reissue(TEST_KEY, self.ledger, previous, machine_code(PC).text(), today=today, force=True)
        rows = keygen.read_ledger(self.ledger)
        self.assertEqual(len(rows), keygen.MAX_REISSUES + 2)
        self.assertTrue(all(row["customer"] == "Khách thử" for row in rows))
        with self.assertRaises(keygen.ToolError):
            keygen.reissue(TEST_KEY, self.ledger, original, machine_code(PC).text(), today=date(2027, 4, 11),
                           force=True)
        with self.assertRaises(keygen.ToolError):
            keygen.reissue(TEST_KEY, self.ledger, "00" * 8, machine_code(PC).text(), today=today)

    def test_verify_command_reads_a_key(self):
        text = make_key("month:1")
        with patch("sys.stdout") as out:
            code = keygen.main(["verify", "--public-key-hex", PUBLIC.hex(), "--machine",
                                machine_code(NEW_BOARD).text(), text])
        self.assertEqual(code, 0)
        self.assertIn("2/3", "".join(call.args[0] for call in out.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
