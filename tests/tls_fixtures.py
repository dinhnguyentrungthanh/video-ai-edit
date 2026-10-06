"""HTTPS for the source-provider tests: a throwaway test CA and a server certificate, made at run time.

No key or certificate is stored in the repository. pycryptodomex (in the audited lock, used by
yt-dlp) makes the P-256 keys and the signatures; the DER of the two X.509 v3 certificates is written
here. The CA key stays in memory and is dropped once the server certificate is signed, so nothing can
sign another certificate for that CA. A client trusts only this CA (``client_context``), or the system
CAs plus this one for the manual demo (``with_system=True``), in its own process: nothing is installed
in Windows. Host names are checked as usual (the server certificate names only ``names``).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from Cryptodome.Hash import SHA256
    from Cryptodome.PublicKey import ECC
    from Cryptodome.Signature import DSS
except ImportError:  # pragma: no cover - the project's venv has pycryptodomex (yt-dlp)
    ECC = None

HAVE_TLS = ECC is not None
NEED_TLS = "pycryptodomex is required for the HTTPS fixtures"
ECDSA_SHA256 = "1.2.840.10045.4.3.2"
CA_NAME = "BiliFlow test CA (temporary)"


# ------------------------------------------------------------------------------------------- DER
def _tlv(tag: int, payload: bytes) -> bytes:
    size = len(payload)
    if size < 0x80:
        head = bytes([size])
    else:
        raw = size.to_bytes((size.bit_length() + 7) // 8, "big")
        head = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + head + payload


def _seq(*items: bytes) -> bytes:
    return _tlv(0x30, b"".join(items))


def _int(value: int) -> bytes:
    return _tlv(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big"))  # room for the sign bit


def _oid(dotted: str) -> bytes:
    first, second, *rest = (int(part) for part in dotted.split("."))
    body = bytearray([40 * first + second])
    for arc in rest:
        groups = [arc & 0x7F]
        arc >>= 7
        while arc:
            groups.append(0x80 | (arc & 0x7F))
            arc >>= 7
        body += bytes(reversed(groups))
    return _tlv(0x06, bytes(body))


def _name(common_name: str) -> bytes:
    return _seq(_tlv(0x31, _seq(_oid("2.5.4.3"), _tlv(0x0C, common_name.encode("utf-8")))))


def _time(moment: dt.datetime) -> bytes:
    return _tlv(0x17, moment.strftime("%y%m%d%H%M%SZ").encode("ascii"))  # UTCTime (years before 2050)


def _extension(oid: str, value: bytes, *, critical: bool = False) -> bytes:
    return _seq(_oid(oid), _tlv(0x01, b"\xff") if critical else b"", _tlv(0x04, value))


def _certificate(*, subject: str, public_key: bytes, signer: Any, extensions: list[bytes], days: int) -> bytes:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    serial = int.from_bytes(os.urandom(16), "big") >> 1 or 1
    algorithm = _seq(_oid(ECDSA_SHA256))
    tbs = _seq(_tlv(0xA0, _int(2)), _int(serial), algorithm, _name(CA_NAME),
               _seq(_time(now - dt.timedelta(hours=1)), _time(now + dt.timedelta(days=days))),
               _name(subject), public_key, _tlv(0xA3, _seq(*extensions)))
    signature = DSS.new(signer, "fips-186-3", encoding="der").sign(SHA256.new(tbs))
    return _seq(tbs, algorithm, _tlv(0x03, b"\x00" + signature))


def _pem(label: str, der: bytes) -> str:
    text = base64.b64encode(der).decode("ascii")
    lines = [text[index:index + 64] for index in range(0, len(text), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


# ----------------------------------------------------------------------------------------- files
@dataclass(frozen=True)
class TlsFiles:
    ca: Path    # the test CA certificate: what a client trusts
    cert: Path  # the server certificate (subjectAltName: the names given)
    key: Path   # the server's private key

    def server_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert), str(self.key))
        return context

    def client_context(self, *, with_system: bool = False) -> ssl.SSLContext:
        """This CA only, or (manual demo) the system CAs plus this CA; certificates and host names checked."""
        if not with_system:
            return ssl.create_default_context(cafile=str(self.ca))
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=str(self.ca))
        return context


def make_tls_files(directory: Path, names: tuple[str, ...] = ("media.example",), *, days: int = 2) -> TlsFiles:
    """A test CA (its key dropped after signing) and a server certificate for ``names``, as PEM files."""
    directory.mkdir(parents=True, exist_ok=True)
    ca_key, server_key = ECC.generate(curve="P-256"), ECC.generate(curve="P-256")
    ca_public = ca_key.public_key().export_key(format="DER")
    ca_id = hashlib.sha256(ca_public).digest()[:20]
    ca = _certificate(subject=CA_NAME, public_key=ca_public, signer=ca_key, days=days, extensions=[
        _extension("2.5.29.19", _seq(_tlv(0x01, b"\xff")), critical=True),  # basicConstraints: CA
        _extension("2.5.29.15", _tlv(0x03, b"\x01\x06"), critical=True),   # keyCertSign, cRLSign
        _extension("2.5.29.14", _tlv(0x04, ca_id)),                         # subjectKeyIdentifier
    ])
    server = _certificate(subject=names[0], public_key=server_key.public_key().export_key(format="DER"),
                          signer=ca_key, days=days, extensions=[
        _extension("2.5.29.19", _seq(), critical=True),                     # not a CA
        _extension("2.5.29.15", _tlv(0x03, b"\x07\x80"), critical=True),   # digitalSignature
        _extension("2.5.29.37", _seq(_oid("1.3.6.1.5.5.7.3.1"))),           # serverAuth
        _extension("2.5.29.17", _seq(*(_tlv(0x82, name.encode("ascii")) for name in names))),  # DNS names
        _extension("2.5.29.35", _seq(_tlv(0x80, ca_id))),                   # authorityKeyIdentifier
    ])
    del ca_key  # nothing signs another certificate for this CA
    files = TlsFiles(directory / "test-ca.pem", directory / "server.pem", directory / "server-key.pem")
    files.ca.write_text(_pem("CERTIFICATE", ca), encoding="ascii")
    files.cert.write_text(_pem("CERTIFICATE", server), encoding="ascii")
    files.key.write_text(server_key.export_key(format="PEM"), encoding="ascii")
    return files
