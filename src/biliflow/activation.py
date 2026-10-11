"""Activation keys bound to a machine code (docs/PERF_INSTALL_PLAN.md section 7, C6).

C6 core only: nothing here is wired into the Control Center or the CLI yet.

- **Machine code.** Three salted hashes (8 bytes each) of the MachineGuid, the
  board UUID read from the SMBIOS firmware table (``GetSystemFirmwareTable``,
  never ``wmic``; placeholder UUIDs count as unknown) and the serial of the
  system volume. A key matches when at least 2 of the 3 known parts are equal.
  An unknown part (all zero bytes) never matches. Reinstalling Windows changes
  the MachineGuid and the volume serial, so only the board is left (1 of 3).
- **Key.** An Ed25519 signature (pycryptodomex) over the machine code, set id,
  kind (``perpetual`` or ``month:N``), issue day, expiry day and a random key
  id; never a customer name. Base32 (RFC 4648, no padding), 172 characters,
  shown in groups of 4. Stored in ``<root>/state/activation/key.json``.
- **Public key.** Only ``RELEASE_PUBLIC_KEY_HEX`` is trusted, and the test key
  is refused even there. Tests select the test key by patching
  ``_trusted_public_key``; no production path can. Signing lives in the
  issuing tool (``keygen/issue_key.py``), outside the package.
- **Clock.** The latest time seen is kept in ``clock.json`` with a local HMAC
  derived from the machine's own identifiers. A clock more than 48 h behind it
  is refused (``CLOCK_BACK``); within 48 h the expiry check uses the later of
  the two. A month key whose clock file is missing or altered needs its key
  entered again (``NEEDS_RECHECK``); its issue day is the lower bound then.

This is a deterrent, not protection against someone with expertise.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
import struct
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


KEY_VERSION = 1
MACHINE_CODE_VERSION = 1
CLOCK_VERSION = 1
KEY_FILE_VERSION = 1
HASH_BYTES = 8
MACHINE_CHECKSUM_BYTES = 5
SIGNATURE_BYTES = 64
MAX_MONTHS = 120
DAY_EPOCH = date(2020, 1, 1)
CLOCK_TOLERANCE_SECONDS = 48 * 3600
CLOCK_WRITE_INTERVAL_SECONDS = 600
MAX_STATE_FILE_BYTES = 4096
MATCHES_NEEDED = 2
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
KIND_PERPETUAL = "perpetual"
_KIND_CODES = {KIND_PERPETUAL: 0, "month": 1}
_PAYLOAD = struct.Struct(">BBBIHH8s")
PAYLOAD_BYTES = _PAYLOAD.size + 3 * HASH_BYTES
KEY_CHARS = -(-(PAYLOAD_BYTES + SIGNATURE_BYTES) * 8 // 5)
MACHINE_CODE_BYTES = 1 + 3 * HASH_BYTES + MACHINE_CHECKSUM_BYTES
MACHINE_CODE_CHARS = MACHINE_CODE_BYTES * 8 // 5
_UNKNOWN = bytes(HASH_BYTES)
_PARTS = ("machine_guid", "board_uuid", "volume_serial")
_SALT = b"BiliFlow activation v1 machine code"
_CLOCK_SALT = b"BiliFlow activation v1 local clock"
_BASE32 = re.compile(r"[A-Z2-7]+")
_LOOKALIKES = str.maketrans({"0": "O", "1": "I", "8": "B"})
_PLACEHOLDER_UUIDS = frozenset({
    "03000200-0400-0500-0006-000700080009",
    "12345678-1234-5678-90ab-cddeefaabbcc",
})

# The release public key (32 bytes, hex) becomes a constant when the user makes the
# real key pair on their PC. Empty: no key is trusted and every check is NOT_CONFIGURED.
RELEASE_PUBLIC_KEY_HEX = ""
# SHA-256 of the test public key (tests/fixtures/activation/): never trusted here.
TEST_PUBLIC_KEY_SHA256 = "408879356d81bc800b757a0aa18db3572d76f5a51fbcdc86d7bceb9b907ccd26"

ACTIVE = "ACTIVE"
NOT_CONFIGURED = "NOT_CONFIGURED"
NO_KEY = "NO_KEY"
INVALID = "INVALID"
WRONG_MACHINE = "WRONG_MACHINE"
EXPIRED = "EXPIRED"
CLOCK_BACK = "CLOCK_BACK"
NEEDS_RECHECK = "NEEDS_RECHECK"
UNAVAILABLE = "UNAVAILABLE"

MESSAGES = {
    ACTIVE: "Đã kích hoạt.",
    NOT_CONFIGURED: "Bản này chưa có khóa công khai để kiểm key; không kích hoạt được.",
    NO_KEY: "Chưa có key kích hoạt.",
    INVALID: "Key không hợp lệ.",
    WRONG_MACHINE: "Key này cấp cho máy khác.",
    EXPIRED: "Key đã hết hạn.",
    CLOCK_BACK: "Đồng hồ máy đang chậm hơn thời điểm BiliFlow từng thấy; hãy chỉnh lại giờ.",
    NEEDS_RECHECK: "Cần nhập lại key để kiểm lại thời hạn.",
    UNAVAILABLE: "Không đọc được mã máy.",
}


class ActivationError(ValueError):
    def __init__(self, state: str, detail: str = ""):
        super().__init__(MESSAGES[state] + (f" ({detail})" if detail else ""))
        self.state = state
        self.detail = detail


# ---------------------------------------------------------------- machine code

@dataclass(frozen=True)
class MachineIdentity:
    """The raw identifiers; never stored or shown, only hashed."""
    machine_guid: str | None
    board_uuid: str | None
    volume_serial: str | None


@dataclass(frozen=True)
class MachineCode:
    hashes: tuple[bytes, bytes, bytes]

    def text(self) -> str:
        body = bytes([MACHINE_CODE_VERSION]) + b"".join(self.hashes)
        raw = body + hashlib.sha256(body).digest()[:MACHINE_CHECKSUM_BYTES]
        return _group(_b32(raw), 6)

    @classmethod
    def parse(cls, text: str) -> "MachineCode":
        raw = _unb32(_normalize(text), MACHINE_CODE_CHARS)
        if raw is None or len(raw) != MACHINE_CODE_BYTES or raw[0] != MACHINE_CODE_VERSION:
            raise ActivationError(INVALID, "mã máy sai định dạng")
        body, checksum = raw[:-MACHINE_CHECKSUM_BYTES], raw[-MACHINE_CHECKSUM_BYTES:]
        if not hmac.compare_digest(hashlib.sha256(body).digest()[:MACHINE_CHECKSUM_BYTES], checksum):
            raise ActivationError(INVALID, "mã máy gõ sai")
        return cls._from_hashes(body[1:])

    @classmethod
    def _from_hashes(cls, data: bytes) -> "MachineCode":
        return cls(tuple(data[i * HASH_BYTES:(i + 1) * HASH_BYTES] for i in range(3)))  # type: ignore[arg-type]

    def known(self) -> int:
        return sum(1 for part in self.hashes if part != _UNKNOWN)

    def matches(self, other: "MachineCode") -> int:
        return sum(
            1 for mine, theirs in zip(self.hashes, other.hashes)
            if mine != _UNKNOWN and hmac.compare_digest(mine, theirs)
        )


def _normalized_part(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if name == "board_uuid":
        return usable_board_uuid(text)
    if name == "volume_serial":
        text = text.upper()
        return text if re.fullmatch(r"[0-9A-F]{8}", text) and text != "00000000" else None
    text = text.lower()
    return text or None


def machine_code(identity: MachineIdentity) -> MachineCode:
    hashes = []
    for name in _PARTS:
        value = _normalized_part(name, getattr(identity, name))
        if value is None:
            hashes.append(_UNKNOWN)
            continue
        digest = hmac.new(_SALT, name.encode() + b"\0" + value.encode("utf-8"), hashlib.sha256).digest()
        part = digest[:HASH_BYTES]
        hashes.append(part if part != _UNKNOWN else b"\x01" + part[1:])
    return MachineCode(tuple(hashes))  # type: ignore[arg-type]


def usable_board_uuid(text: str) -> str | None:
    """The canonical UUID, or None for a placeholder (all one byte value, known fakes)."""
    try:
        value = uuid.UUID(text.strip())
    except (ValueError, AttributeError):
        return None
    if len(set(value.bytes)) == 1 or str(value) in _PLACEHOLDER_UUIDS:
        return None
    return str(value)


def parse_smbios_uuid(raw: bytes) -> str | None:
    """Board UUID from a ``RawSMBIOSData`` buffer (``GetSystemFirmwareTable('RSMB')``)."""
    if len(raw) < 8:
        return None
    major, minor = raw[1], raw[2]
    (length,) = struct.unpack_from("<I", raw, 4)
    table = raw[8:8 + length]
    offset = 0
    while offset + 4 <= len(table):
        kind, size = table[offset], table[offset + 1]
        if size < 4 or offset + size > len(table):
            return None
        if kind == 1 and size >= 0x18:
            data = bytes(table[offset + 8:offset + 24])
            # SMBIOS 2.6 and later store the first three fields little-endian.
            value = uuid.UUID(bytes_le=data) if (major, minor) >= (2, 6) else uuid.UUID(bytes=data)
            return usable_board_uuid(str(value))
        if kind == 127:
            return None
        end = table.find(b"\0\0", offset + size)
        if end < 0:
            return None
        offset = end + 2
    return None


def read_machine_identity() -> MachineIdentity:
    """Windows only; each part that cannot be read is None (unknown)."""
    if sys.platform != "win32":
        raise ActivationError(UNAVAILABLE, "chỉ chạy trên Windows")
    return MachineIdentity(_read_machine_guid(), _read_board_uuid(), _read_volume_serial())


def _read_machine_guid() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, kind = winreg.QueryValueEx(key, "MachineGuid")
    except OSError:
        return None
    return value if kind == winreg.REG_SZ and isinstance(value, str) else None


def _read_board_uuid() -> str | None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    provider = int.from_bytes(b"RSMB", "big")
    get_table = kernel32.GetSystemFirmwareTable
    get_table.argtypes = (ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)
    get_table.restype = ctypes.c_uint32
    size = get_table(provider, 0, None, 0)
    if not size or size > 1 << 20:
        return None
    buffer = ctypes.create_string_buffer(size)
    if get_table(provider, 0, buffer, size) != size:
        return None
    return parse_smbios_uuid(buffer.raw)


def _read_volume_serial() -> str | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    folder = ctypes.create_unicode_buffer(260)
    if not kernel32.GetSystemWindowsDirectoryW(folder, 260):
        return None
    drive = os.path.splitdrive(folder.value)[0]
    if not re.fullmatch(r"[A-Za-z]:", drive):
        return None
    serial = wintypes.DWORD()
    if not kernel32.GetVolumeInformationW(drive + "\\", None, 0, ctypes.byref(serial), None, None, None, 0):
        return None
    return f"{serial.value:08X}"


# ------------------------------------------------------------------------ keys

@dataclass(frozen=True)
class KeyFields:
    machine: MachineCode
    set_id: int
    kind: str  # "perpetual" or "month:N"
    issued: date
    expires: date | None  # first day the key no longer works; None when perpetual
    key_id: bytes

    @property
    def months(self) -> int:
        return 0 if self.kind == KIND_PERPETUAL else int(self.kind.split(":", 1)[1])


def add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    following = date(year + (month == 12), month % 12 + 1, 1)
    return date(year, month, min(day.day, (following - timedelta(days=1)).day))


def parse_kind(text: str) -> str:
    if text == KIND_PERPETUAL:
        return text
    match = re.fullmatch(r"month:([1-9][0-9]*)", text)
    if not match or int(match.group(1)) > MAX_MONTHS:
        raise ActivationError(INVALID, f"loại key phải là perpetual hoặc month:1..{MAX_MONTHS}")
    return text


def _day_number(day: date) -> int:
    number = (day - DAY_EPOCH).days
    if not 0 < number < 1 << 16:
        raise ActivationError(INVALID, "ngày ngoài khoảng cho phép")
    return number


def encode_payload(fields: KeyFields) -> bytes:
    kind = parse_kind(fields.kind)
    months = fields.months
    if len(fields.key_id) != 8 or not 0 <= fields.set_id < 1 << 32:
        raise ActivationError(INVALID, "mã key hoặc mã bộ sai")
    if fields.machine.known() < MATCHES_NEEDED:
        raise ActivationError(INVALID, "mã máy có ít hơn 2 phần đọc được")
    if kind == KIND_PERPETUAL:
        if fields.expires is not None:
            raise ActivationError(INVALID, "key vĩnh viễn không có ngày hết hạn")
        expires = 0
    else:
        if fields.expires is None or fields.expires <= fields.issued:
            raise ActivationError(INVALID, "key tháng phải hết hạn sau ngày cấp")
        expires = _day_number(fields.expires)
    head = _PAYLOAD.pack(KEY_VERSION, _KIND_CODES["month" if months else KIND_PERPETUAL], months,
                         fields.set_id, _day_number(fields.issued), expires, fields.key_id)
    return head + b"".join(fields.machine.hashes)


def decode_payload(payload: bytes) -> KeyFields:
    if len(payload) != PAYLOAD_BYTES:
        raise ActivationError(INVALID, "độ dài")
    version, kind, months, set_id, issued, expires, key_id = _PAYLOAD.unpack_from(payload)
    if version != KEY_VERSION or issued == 0:
        raise ActivationError(INVALID, "phiên bản")
    issued_day = DAY_EPOCH + timedelta(days=issued)
    if kind == _KIND_CODES[KIND_PERPETUAL] and months == 0 and expires == 0:
        name, expires_day = KIND_PERPETUAL, None
    elif kind == _KIND_CODES["month"] and 1 <= months <= MAX_MONTHS and expires > issued:
        name, expires_day = f"month:{months}", DAY_EPOCH + timedelta(days=expires)
    else:
        raise ActivationError(INVALID, "loại key")
    machine = MachineCode._from_hashes(payload[_PAYLOAD.size:])
    if machine.known() < MATCHES_NEEDED:
        raise ActivationError(INVALID, "mã máy")
    return KeyFields(machine, set_id, name, issued_day, expires_day, key_id)


def format_key(payload: bytes, signature: bytes) -> str:
    if len(payload) != PAYLOAD_BYTES or len(signature) != SIGNATURE_BYTES:
        raise ActivationError(INVALID, "độ dài")
    return _group(_b32(payload + signature), 4)


def split_key(text: str) -> tuple[bytes, bytes]:
    raw = _unb32(_normalize(text), KEY_CHARS)
    if raw is None or len(raw) != PAYLOAD_BYTES + SIGNATURE_BYTES:
        raise ActivationError(INVALID, "sai định dạng")
    return raw[:PAYLOAD_BYTES], raw[PAYLOAD_BYTES:]


def verify_key_text(text: str, public_key: bytes) -> KeyFields:
    """The fields of a key whose signature ``public_key`` made; raises ActivationError."""
    from Cryptodome.Signature import eddsa

    payload, signature = split_key(text)
    try:
        verifier = eddsa.new(eddsa.import_public_key(public_key), "rfc8032")
        verifier.verify(payload, signature)
    except (ValueError, TypeError):
        raise ActivationError(INVALID, "chữ ký") from None
    return decode_payload(payload)


def _trusted_public_key() -> bytes | None:
    try:
        key = bytes.fromhex(RELEASE_PUBLIC_KEY_HEX)
    except ValueError:
        return None
    if len(key) != 32 or hashlib.sha256(key).hexdigest() == TEST_PUBLIC_KEY_SHA256:
        return None
    return key


# ----------------------------------------------------------------- local state

@dataclass(frozen=True)
class ActivationStatus:
    state: str
    message: str
    kind: str | None = None
    set_id: int | None = None
    key_id: str | None = None
    issued: str | None = None
    expires: str | None = None  # first day the key no longer works
    matched_parts: int | None = None

    @property
    def active(self) -> bool:
        return self.state == ACTIVE


def activation_dir(root: Path) -> Path:
    return Path(root) / "state" / "activation"


def _status(state: str, fields: KeyFields | None = None, matched: int | None = None) -> ActivationStatus:
    if fields is None:
        return ActivationStatus(state, MESSAGES[state], matched_parts=matched)
    return ActivationStatus(
        state, MESSAGES[state], kind=fields.kind, set_id=fields.set_id, key_id=fields.key_id.hex(),
        issued=fields.issued.isoformat(), expires=fields.expires.isoformat() if fields.expires else None,
        matched_parts=matched,
    )


def _clock_key(identity: MachineIdentity) -> bytes:
    material = "\0".join(str(getattr(identity, name) or "") for name in _PARTS)
    return hmac.new(_CLOCK_SALT, material.encode("utf-8"), hashlib.sha256).digest()


def _read_small_json(path: Path) -> dict | None:
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_STATE_FILE_BYTES:
            return None
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict) -> None:
    folder = path.parent
    folder.mkdir(parents=True, exist_ok=True)
    info = os.lstat(folder)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        raise ActivationError(INVALID, "thư mục kích hoạt là liên kết")
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    os.replace(temporary, path)


def _clock_mac(identity: MachineIdentity, last_seen: int) -> str:
    message = f"{CLOCK_VERSION}:{last_seen}".encode()
    return hmac.new(_clock_key(identity), message, hashlib.sha256).hexdigest()


def read_last_seen(root: Path, identity: MachineIdentity) -> int | None:
    data = _read_small_json(activation_dir(root) / "clock.json")
    if not data or data.get("version") != CLOCK_VERSION:
        return None
    last_seen, mac = data.get("last_seen"), data.get("mac")
    if type(last_seen) is not int or last_seen <= 0 or not isinstance(mac, str):
        return None
    return last_seen if hmac.compare_digest(_clock_mac(identity, last_seen), mac) else None


def _write_last_seen(root: Path, identity: MachineIdentity, last_seen: int) -> None:
    _write_json(activation_dir(root) / "clock.json",
                {"version": CLOCK_VERSION, "last_seen": last_seen, "mac": _clock_mac(identity, last_seen)})


def _day_start(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def _utc_day(seconds: float) -> date:
    return datetime.fromtimestamp(seconds, timezone.utc).date()


def _read_key_text(root: Path) -> str | None:
    """None when there is no key file, "" when it cannot be read as one."""
    path = activation_dir(root) / "key.json"
    if not os.path.lexists(path):
        return None
    data = _read_small_json(path)
    if not data or data.get("version") != KEY_FILE_VERSION or not isinstance(data.get("key"), str):
        return ""
    return data["key"]


def _evaluate(text: str, identity: MachineIdentity, now: int, last_seen: int | None,
              *, entering: bool) -> tuple[ActivationStatus, KeyFields | None, int | None]:
    """(status, fields, the clock value to keep) for one key; never writes."""
    public_key = _trusted_public_key()
    if public_key is None:
        return _status(NOT_CONFIGURED), None, None
    try:
        fields = verify_key_text(text, public_key)
    except ActivationError:
        return _status(INVALID), None, None
    matched = fields.machine.matches(machine_code(identity))
    if matched < MATCHES_NEEDED:
        return _status(WRONG_MACHINE, fields, matched), None, None
    floor = _day_start(fields.issued)
    if now < floor - CLOCK_TOLERANCE_SECONDS:
        return _status(CLOCK_BACK, fields, matched), None, None
    if last_seen is not None and now < last_seen - CLOCK_TOLERANCE_SECONDS:
        return _status(CLOCK_BACK, fields, matched), None, None
    if last_seen is None and fields.kind != KIND_PERPETUAL and not entering:
        return _status(NEEDS_RECHECK, fields, matched), fields, None
    keep = max(now, floor, last_seen or 0)
    if fields.expires is not None and _utc_day(keep) >= fields.expires:
        return _status(EXPIRED, fields, matched), fields, keep
    return _status(ACTIVE, fields, matched), fields, keep


def check(root: Path, *, now: float | None = None,
          identity: MachineIdentity | None = None) -> ActivationStatus:
    """The activation state of ``root``. May move the clock file forward, nothing else."""
    try:
        identity = identity or read_machine_identity()
    except (ActivationError, OSError):
        return _status(UNAVAILABLE)
    text = _read_key_text(root)
    if text is None:
        return _status(NO_KEY)
    current = int(time.time() if now is None else now)
    last_seen = read_last_seen(root, identity)
    status, _, keep = _evaluate(text, identity, current, last_seen, entering=False)
    if keep is not None and (last_seen is None or keep >= last_seen + CLOCK_WRITE_INTERVAL_SECONDS):
        try:
            _write_last_seen(root, identity, keep)
        except (OSError, ActivationError):
            pass  # The answer stands; the next check tries again.
    return status


def activate(root: Path, key_text: str, *, now: float | None = None,
             identity: MachineIdentity | None = None) -> ActivationStatus:
    """Check ``key_text`` for this machine and, if it is valid now, store it."""
    try:
        identity = identity or read_machine_identity()
    except (ActivationError, OSError):
        return _status(UNAVAILABLE)
    current = int(time.time() if now is None else now)
    last_seen = read_last_seen(root, identity)
    status, fields, keep = _evaluate(key_text, identity, current, last_seen, entering=True)
    if status.state != ACTIVE or fields is None or keep is None:
        return status
    payload, signature = split_key(key_text)
    _write_json(activation_dir(root) / "key.json",
                {"version": KEY_FILE_VERSION, "key": format_key(payload, signature)})
    if last_seen is None or keep > last_seen:
        _write_last_seen(root, identity, keep)
    return status


# --------------------------------------------------------------------- helpers

def _normalize(text: str) -> str:
    return re.sub(r"[\s\-]", "", str(text)).upper().translate(_LOOKALIKES)


def _b32(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _unb32(text: str, chars: int) -> bytes | None:
    if len(text) != chars or not _BASE32.fullmatch(text):
        return None
    try:
        raw = base64.b32decode(text + "=" * (-len(text) % 8))
    except ValueError:
        return None
    # One text per key: unused trailing bits must be zero.
    return raw if _b32(raw) == text else None


def _group(text: str, size: int) -> str:
    return "-".join(text[i:i + size] for i in range(0, len(text), size))
