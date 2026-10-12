"""Issue BiliFlow activation keys (docs/PERF_INSTALL_PLAN.md section 7, C6).

Runs on the user's PC only; the installer never ships this folder. Agents never run it with a
real key. The private key and the customer ledger must live outside Git and outside every
BiliFlow folder; the tool refuses paths inside a Git checkout or a BiliFlow install.

    python keygen\\issue_key.py init --private-key D:\\keys\\biliflow-issuer.pem
    python keygen\\issue_key.py issue --private-key ... --ledger D:\\keys\\ledger.csv \\
        --machine <mã máy> --set 7 --kind month:1 --customer "<tên khách>"
    python keygen\\issue_key.py reissue --private-key ... --ledger ... --key-id <hex> --machine <mã máy mới>
    python keygen\\issue_key.py verify --public-key-hex <hex> --machine <mã máy> <key>

Reissue policy: a reissued key keeps the set, the kind and the original expiry (it never extends a
month key) and gets a new key id; at most MAX_REISSUES reissues per original key unless --force.
"""
from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import os
import secrets
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from biliflow import activation  # noqa: E402
from biliflow.activation import ActivationError, KeyFields, MachineCode  # noqa: E402


MAX_REISSUES = 3
MIN_PASSPHRASE = 12
PROTECTION = "PBKDF2WithHMAC-SHA512AndAES256-CBC"
LEDGER_FIELDS = ("recorded_at", "key_id", "set_id", "kind", "issued", "expires", "machine_code",
                 "customer", "reissue_of", "note")
PROJECT_MARKERS = (".git", "biliflow-install.json")


class ToolError(RuntimeError):
    pass


def refuse_inside_project(path: Path) -> str | None:
    """A reason when ``path`` lies inside a Git checkout or a BiliFlow install folder."""
    resolved = Path(path).resolve()
    for folder in (resolved, *resolved.parents):
        for marker in PROJECT_MARKERS:
            if os.path.lexists(folder / marker):
                return (f"{resolved} nằm trong {folder} (có {marker}); "
                        "khóa bí mật và sổ khách hàng phải ở ngoài.")
    return None


def new_private_key():
    from Cryptodome.PublicKey import ECC

    return ECC.generate(curve="Ed25519")


def public_key_bytes(private_key) -> bytes:
    return private_key.public_key().export_key(format="raw")


def is_test_key(private_key) -> bool:
    return hashlib.sha256(public_key_bytes(private_key)).hexdigest() == activation.TEST_PUBLIC_KEY_SHA256


def export_private_key(private_key, passphrase: str) -> str:
    if len(passphrase) < MIN_PASSPHRASE:
        raise ToolError(f"Mật khẩu của khóa bí mật cần ít nhất {MIN_PASSPHRASE} ký tự.")
    # UTF-8 bytes: pycryptodomex would encode a str as Latin-1 and fail on Vietnamese text.
    return private_key.export_key(format="PEM", passphrase=passphrase.encode("utf-8"), protection=PROTECTION)


def load_private_key(text: str, passphrase: str | None):
    from Cryptodome.PublicKey import ECC

    try:
        key = ECC.import_key(text, passphrase=None if passphrase is None else passphrase.encode("utf-8"))
    except (ValueError, TypeError, IndexError):
        raise ToolError("Không đọc được khóa bí mật (sai mật khẩu hoặc sai file).") from None
    if key.curve != "Ed25519" or not key.has_private():
        raise ToolError("File không phải khóa bí mật Ed25519.")
    return key


def make_fields(machine_text: str, set_id: int, kind: str, issued: date, *,
                expires: date | None = None, key_id: bytes | None = None) -> KeyFields:
    kind = activation.parse_kind(kind)
    machine = MachineCode.parse(machine_text)
    if expires is None and kind != activation.KIND_PERPETUAL:
        expires = activation.add_months(issued, int(kind.split(":", 1)[1]))
    return KeyFields(machine, set_id, kind, issued, expires, key_id or secrets.token_bytes(8))


def sign(private_key, fields: KeyFields) -> str:
    from Cryptodome.Signature import eddsa

    payload = activation.encode_payload(fields)
    return activation.format_key(payload, eddsa.new(private_key, "rfc8032").sign(payload))


def read_ledger(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def append_ledger(path: Path, row: dict[str, str]) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig" if new else "utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_FIELDS)
        if new:
            writer.writeheader()
        writer.writerow({name: row.get(name, "") for name in LEDGER_FIELDS})


def _ledger_row(fields: KeyFields, machine_text: str, customer: str, *, reissue_of: str = "",
                note: str = "") -> dict[str, str]:
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "key_id": fields.key_id.hex(), "set_id": str(fields.set_id), "kind": fields.kind,
        "issued": fields.issued.isoformat(), "expires": fields.expires.isoformat() if fields.expires else "",
        "machine_code": MachineCode.parse(machine_text).text(), "customer": customer,
        "reissue_of": reissue_of, "note": note,
    }


def issue(private_key, ledger: Path, machines: list[str], *, set_id: int, kind: str, customer: str,
          issued: date | None = None) -> list[tuple[str, str]]:
    """One key per machine code, all in set ``set_id``; each is recorded in the ledger."""
    if not customer.strip():
        raise ToolError("Cần tên khách (chỉ ghi vào sổ, không nằm trong key).")
    issued = issued or datetime.now(timezone.utc).date()
    fields = [make_fields(machine, set_id, kind, issued) for machine in machines]
    keys = []
    for machine, item in zip(machines, fields):
        key = sign(private_key, item)
        append_ledger(ledger, _ledger_row(item, machine, customer.strip()))
        keys.append((item.key_id.hex(), key))
    return keys


def _original(rows: list[dict[str, str]], key_id: str) -> dict[str, str]:
    by_id = {row["key_id"]: row for row in rows}
    row = by_id.get(key_id)
    if row is None:
        raise ToolError(f"Không có key {key_id} trong sổ.")
    seen = set()
    while row.get("reissue_of"):
        if row["key_id"] in seen or row["reissue_of"] not in by_id:
            raise ToolError("Sổ khách hàng có chuỗi cấp lại hỏng.")
        seen.add(row["key_id"])
        row = by_id[row["reissue_of"]]
    return row


def reissue(private_key, ledger: Path, key_id: str, machine: str, *, force: bool = False,
            today: date | None = None) -> tuple[str, str]:
    rows = read_ledger(ledger)
    original = _original(rows, key_id)
    count = sum(1 for row in rows if row.get("reissue_of") and _original(rows, row["key_id"]) is original)
    if count >= MAX_REISSUES and not force:
        raise ToolError(f"Key {original['key_id']} đã cấp lại {count} lần (tối đa {MAX_REISSUES}); "
                        "dùng --force nếu chắc chắn.")
    today = today or datetime.now(timezone.utc).date()
    expires = date.fromisoformat(original["expires"]) if original["expires"] else None
    if expires is not None and expires <= today:
        raise ToolError("Key gốc đã hết hạn; cấp key mới thay vì cấp lại.")
    fields = make_fields(machine, int(original["set_id"]), original["kind"], today, expires=expires)
    key = sign(private_key, fields)
    append_ledger(ledger, _ledger_row(fields, machine, original["customer"], reissue_of=key_id,
                                      note=f"cấp lại lần {count + 1} của {original['key_id']}"))
    return fields.key_id.hex(), key


def _require_outside(path: Path) -> None:
    reason = refuse_inside_project(path)
    if reason:
        raise ToolError(reason)


def _read_key_file(path: Path):
    _require_outside(path)
    text = path.read_text(encoding="ascii")
    passphrase = None if "ENCRYPTED" not in text else getpass.getpass("Mật khẩu khóa bí mật: ")
    key = load_private_key(text, passphrase)
    if is_test_key(key):
        print("CẢNH BÁO: đây là khóa TEST; key ký bằng nó không dùng được ngoài test.", file=sys.stderr)
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="issue_key.py", description="Cấp key kích hoạt BiliFlow")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Tạo cặp khóa phát hành (một lần, rồi sao lưu)")
    init.add_argument("--private-key", type=Path, required=True)
    for name in ("issue", "reissue"):
        command = sub.add_parser(name)
        command.add_argument("--private-key", type=Path, required=True)
        command.add_argument("--ledger", type=Path, required=True)
    issue_cmd, reissue_cmd = sub.choices["issue"], sub.choices["reissue"]
    issue_cmd.add_argument("--machine", action="append", required=True, help="Mã máy (lặp lại để cấp cả bộ)")
    issue_cmd.add_argument("--set", type=int, required=True, dest="set_id", help="Mã bộ")
    issue_cmd.add_argument("--kind", required=True, help="perpetual hoặc month:N")
    issue_cmd.add_argument("--customer", required=True)
    reissue_cmd.add_argument("--key-id", required=True)
    reissue_cmd.add_argument("--machine", required=True)
    reissue_cmd.add_argument("--force", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("--public-key-hex", required=True)
    verify.add_argument("--machine", required=True)
    verify.add_argument("key")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            _require_outside(args.private_key)
            if os.path.lexists(args.private_key):
                raise ToolError(f"{args.private_key} đã có; không ghi đè khóa bí mật.")
            passphrase = getpass.getpass("Mật khẩu mới cho khóa bí mật: ")
            if getpass.getpass("Nhập lại: ") != passphrase:
                raise ToolError("Hai lần nhập không khớp.")
            key = new_private_key()
            text = export_private_key(key, passphrase)
            with open(args.private_key, "x", encoding="ascii") as handle:
                handle.write(text)
            print("Khóa công khai (đặt vào RELEASE_PUBLIC_KEY_HEX):", public_key_bytes(key).hex())
            print("Hãy sao lưu file khóa bí mật và mật khẩu; mất là không cấp key được nữa.")
        elif args.command == "issue":
            _require_outside(args.ledger)
            key = _read_key_file(args.private_key)
            for key_id, text in issue(key, args.ledger, args.machine, set_id=args.set_id,
                                      kind=args.kind, customer=args.customer):
                print(key_id, text)
        elif args.command == "reissue":
            _require_outside(args.ledger)
            key = _read_key_file(args.private_key)
            key_id, text = reissue(key, args.ledger, args.key_id, args.machine, force=args.force)
            print(key_id, text)
        else:
            fields = activation.verify_key_text(args.key, bytes.fromhex(args.public_key_hex))
            matched = fields.machine.matches(MachineCode.parse(args.machine))
            print(f"Chữ ký đúng · bộ {fields.set_id} · {fields.kind} · cấp {fields.issued} · "
                  f"hết hạn {fields.expires or 'không'} · khớp {matched}/3 phần mã máy")
    except (ToolError, ActivationError, ValueError, OSError) as error:
        print(f"Lỗi: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
