"""Request limits of the local servers (Control Center and standalone review UI).

Standard library only. Both servers:

- bind to an IPv4 loopback address only (``localhost`` binds 127.0.0.1, with
  no name lookup): on a LAN address any machine could send
  ``Host: 127.0.0.1`` past the DNS-rebinding check and read the session token;
- read a request body only for a plain non-negative Content-Length (a negative
  one made ``rfile.read(-1)`` wait until the client closed the connection);
- close a connection whose request stops arriving (``REQUEST_TIMEOUT_SECONDS``
  per socket read or write; streaming a video lifts it). It is a limit per
  read, not a deadline: a local program that trickles a byte at a time can
  still hold a thread, which only processes of this machine can reach.
"""

from __future__ import annotations

import argparse
import ipaddress
from typing import Any

# Seconds one read or write of a request or its response may wait.
REQUEST_TIMEOUT_SECONDS = 20.0
CONTENT_LENGTH_MESSAGE = "Content-Length không hợp lệ"
REQUEST_TIMEOUT_MESSAGE = "Hết thời gian chờ dữ liệu gửi lên"
LOOPBACK_ONLY_MESSAGE = (
    "BiliFlow chỉ mở trên chính máy này: --host phải là 127.0.0.1 (hoặc localhost), "
    "không dùng “{host}”."
)


def content_length(headers: Any) -> int:
    """The request's Content-Length (0 when absent); ValueError unless a plain non-negative number."""
    raw = headers.get("Content-Length")
    if raw is None:
        return 0
    value = str(raw).strip()
    if not value or not value.isascii() or not value.isdigit() or len(value) > 18:
        raise ValueError(CONTENT_LENGTH_MESSAGE)
    return int(value)


def is_loopback_host(host: Any) -> bool:
    """``localhost`` or an IPv4 loopback address (127.0.0.0/8), written exactly.

    The servers listen on IPv4 only. Any other spelling (spaces, brackets,
    IPv6, look-alike letters) is refused, never normalised.
    """
    if not isinstance(host, str) or not host.isascii():
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.IPv4Address(host).is_loopback
    except ValueError:
        return False


def require_loopback_host(host: Any) -> None:
    """ValueError unless ``host`` is a loopback address (checked before binding)."""
    if not is_loopback_host(host):
        raise ValueError(LOOPBACK_ONLY_MESSAGE.format(host=host))


def loopback_bind_address(host: Any) -> str:
    """The address to bind for ``host``: 127.0.0.1 for localhost (no name lookup); ValueError otherwise."""
    require_loopback_host(host)
    return "127.0.0.1" if host.lower() == "localhost" else host


def loopback_host_argument(value: str) -> str:
    """argparse ``type`` of --host: a loopback address, else a usage error (exit code 2)."""
    if not is_loopback_host(value):
        raise argparse.ArgumentTypeError(LOOPBACK_ONLY_MESSAGE.format(host=value))
    return value
