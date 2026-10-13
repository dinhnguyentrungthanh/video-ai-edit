"""Request limits of the local servers: Content-Length, loopback-only binding."""

import contextlib
import errno
import io
import select
import socket
import socketserver
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import Mock, patch

from biliflow import cli, control_entry
from biliflow.control_center import ControlCenter, serve_control_center
from biliflow.http_guards import (
    CONTENT_LENGTH_MESSAGE,
    REQUEST_TIMEOUT_SECONDS,
    content_length,
    is_loopback_host,
    loopback_bind_address,
)
from biliflow.phone_access import _PhoneServer
from biliflow.review_workflow import serve_review_ui


LAN_HOSTS = ("0.0.0.0", "192.168.1.5", "10.0.0.2", "::", "my-pc", "127.0.0.1.evil.example", "")
# Spellings the IPv4 servers would not bind as written, or that only look like loopback.
ODD_HOSTS = (
    " 127.0.0.1", "127.0.0.1 ", "[127.0.0.1]", "::1", "[::1]", "::ffff:127.0.0.1", "127.1", "0x7f.0.0.1",
    "2130706433", "127.000.000.001", "localhost.", "localhoſt", "ｌｏｃａｌｈｏｓｔ", "local\0host", None,
)
LOOPBACK_HOSTS = ("127.0.0.1", "127.5.6.7", "localhost", "LOCALHOST")
# A Dashboard V2 load opens about 20 connections at once (the servers answer HTTP/1.0: one connection per
# request) next to its polls; the burst is twice that.
BURST = 40
CONNECTING = {0, errno.EINPROGRESS, errno.EWOULDBLOCK, getattr(errno, "WSAEWOULDBLOCK", errno.EWOULDBLOCK)}


def burst(address: tuple[str, int], count: int = BURST, wait: float = 10.0) -> list[Any]:
    """What each of ``count`` connections opened at once to ``address`` ended with, while the server accepts
    none of them: 0 when the kernel queued it for the server."""
    sockets = [socket.socket() for _ in range(count)]
    try:
        outcome: dict[socket.socket, Any] = {}
        for sock in sockets:
            sock.setblocking(False)
            code = sock.connect_ex(address)
            if code not in CONNECTING:
                outcome[sock] = code  # refused at once (WSAENOBUFS on Windows)
        pending = [sock for sock in sockets if sock not in outcome]
        deadline = time.monotonic() + wait
        while pending and time.monotonic() < deadline:
            _, writable, failed = select.select([], pending, pending, 0.5)
            for sock in {*writable, *failed}:
                outcome[sock] = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                pending.remove(sock)
        return [outcome.get(sock, "still connecting") for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


class _Stop(Exception):
    """Ends a server's serve() from its patched serve_forever."""


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass


class ContentLengthTests(unittest.TestCase):
    def test_a_plain_non_negative_number(self):
        self.assertEqual(content_length({}), 0)
        for value, expected in (("0", 0), ("12", 12), (" 7 ", 7), ("65536", 65536)):
            with self.subTest(value=value):
                self.assertEqual(content_length({"Content-Length": value}), expected)

    def test_anything_else_is_refused(self):
        for value in ("-1", "+5", "1e3", "abc", "", " ", "0x10", "１２", "1 2", "9" * 5000):
            with self.subTest(value=value[:12]):
                with self.assertRaises(ValueError) as caught:
                    content_length({"Content-Length": value})
                self.assertEqual(str(caught.exception), CONTENT_LENGTH_MESSAGE)

    def test_the_request_timeout_is_short_but_not_tiny(self):
        self.assertGreaterEqual(REQUEST_TIMEOUT_SECONDS, 5)
        self.assertLessEqual(REQUEST_TIMEOUT_SECONDS, 60)


class LoopbackTests(unittest.TestCase):
    def test_loopback_names(self):
        for host in LOOPBACK_HOSTS:
            with self.subTest(host=host):
                self.assertTrue(is_loopback_host(host))
        for host in LAN_HOSTS + ODD_HOSTS:
            with self.subTest(host=host):
                self.assertFalse(is_loopback_host(host))

    def test_the_servers_bind_the_loopback_address_itself(self):
        # No name lookup: "localhost" binds 127.0.0.1 whatever the resolver says.
        self.assertEqual(loopback_bind_address("localhost"), "127.0.0.1")
        self.assertEqual(loopback_bind_address("LocalHost"), "127.0.0.1")
        self.assertEqual(loopback_bind_address("127.5.6.7"), "127.5.6.7")
        for host in ("::1", " 127.0.0.1", "0.0.0.0", None):
            with self.subTest(host=host), self.assertRaises(ValueError):
                loopback_bind_address(host)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            (root / "reports" / "q.json").write_text("{}", encoding="utf-8")
            with patch("biliflow.review_workflow.LocalHTTPServer", side_effect=OSError("bind")) as server:
                with self.assertRaises(OSError):
                    serve_review_ui(project_root=root, queue_path=root / "reports" / "q.json",
                                    host="localhost", port=0)
            self.assertEqual(server.call_args.args[0], ("127.0.0.1", 0))
            stub = ControlCenter.__new__(ControlCenter)
            stub.host, stub.port, stub.root, stub.server = "localhost", 0, root, None
            stub._stopping = threading.Event()
            stub.stop_ai_audits, stub.stop_ai_login = Mock(), Mock()
            stub.watcher, stub.scheduler, stub.store, stub.lock = Mock(), Mock(), Mock(), Mock()
            with patch("biliflow.control_center.LocalHTTPServer", side_effect=OSError("bind")) as server, \
                    patch("biliflow.source_cleanup.wait_idle"):
                with self.assertRaises(OSError):
                    stub.serve()
            self.assertEqual(server.call_args.args[0], ("127.0.0.1", 0))
            stub.store.close.assert_called_once_with()

    def run_entry(self, host):
        stderr = io.StringIO()
        with TemporaryDirectory() as directory, \
                patch("biliflow.control_entry.serve_control_center") as serve, \
                contextlib.redirect_stderr(stderr):
            try:
                code = control_entry.main(["--project-root", directory, "--host", host])
            except SystemExit as error:
                code = error.code
        return code, serve, stderr.getvalue()

    def test_control_entry_refuses_a_lan_host(self):
        # ::1 too: the servers listen on IPv4 only and could not bind it.
        for host in ("0.0.0.0", "192.168.1.5", "::", "::1", " 127.0.0.1"):
            with self.subTest(host=host):
                code, serve, stderr = self.run_entry(host)
                self.assertEqual(code, 2)
                serve.assert_not_called()
                self.assertIn("127.0.0.1", stderr)
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host=host):
                code, serve, _ = self.run_entry(host)
                self.assertEqual(code, 0)
                self.assertEqual(serve.call_args.kwargs["host"], host)

    def test_the_cli_servers_refuse_a_lan_host(self):
        parser = cli.build_parser()
        for argv in (["control-center", "--host", "0.0.0.0"],
                     ["review-ui", "--queue", "q.json", "--host", "192.168.1.5"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    parser.parse_args(argv)
                self.assertEqual(caught.exception.code, 2)
        self.assertEqual(parser.parse_args(["control-center"]).host, "127.0.0.1")
        self.assertEqual(parser.parse_args(["review-ui", "--queue", "q.json", "--host", "localhost"]).host,
                         "localhost")

    def test_the_servers_refuse_a_lan_host_before_anything_else(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("biliflow.control_center.ControlCenter") as center:
                with self.assertRaises(ValueError):
                    serve_control_center(project_root=root, host="0.0.0.0")
                center.assert_not_called()
            stub = ControlCenter.__new__(ControlCenter)
            stub.host, stub.port = "192.168.1.5", 0
            with patch("biliflow.control_center.LocalHTTPServer") as server:
                with self.assertRaises(ValueError):
                    stub.serve()
                server.assert_not_called()
            with patch("biliflow.review_workflow.LocalHTTPServer") as server:
                with self.assertRaises(ValueError):
                    serve_review_ui(project_root=root / "missing", queue_path=root / "q.json", host="0.0.0.0")
                server.assert_not_called()


class ListenBacklogTests(unittest.TestCase):
    """While a server's thread is busy, the kernel queues new connections up to the listen backlog. With
    socketserver's default of 5, Windows refused the rest of a page's burst (Edge: ERR_CONNECTION_REFUSED,
    or ERR_NO_BUFFER_SPACE at once), and Dashboard V2 lost a script or a poll."""

    def burst_in_serve(self, start) -> list[Any]:
        """``start()`` runs a server; its first serve_forever, before it accepts anything, opens the burst."""
        seen: list[Any] = []

        def busy(server, *args, **kwargs):
            seen.extend(burst(server.server_address))
            raise _Stop

        with patch.object(socketserver.BaseServer, "serve_forever", autospec=True, side_effect=busy), \
                patch("builtins.print"), self.assertRaises(_Stop):
            start()
        return seen

    def test_a_burst_of_connections_waits_while_the_server_is_busy(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reports").mkdir()
            (root / "reports" / "q.json").write_text("{}", encoding="utf-8")
            stub = ControlCenter.__new__(ControlCenter)
            stub.host, stub.port, stub.root, stub.server, stub.token = "127.0.0.1", 0, root, None, "t"
            stub._stopping = threading.Event()
            stub._stopping.set()  # serve()'s own cleanup is not under test; the server is closed below
            stub.watcher, stub.scheduler = Mock(), Mock()
            with self.subTest(server="Control Center"):
                try:
                    self.assertEqual(self.burst_in_serve(stub.serve), [0] * BURST)
                finally:
                    if stub.server is not None:
                        stub.server.server_close()
            with self.subTest(server="review UI"):
                self.assertEqual(self.burst_in_serve(lambda: serve_review_ui(
                    project_root=root, queue_path=root / "reports" / "q.json", host="127.0.0.1", port=0)),
                    [0] * BURST)
        with self.subTest(server="phone"):
            phone = _PhoneServer(("127.0.0.1", 0), _Quiet)
            try:
                self.assertEqual(burst(phone.server_address), [0] * BURST)
            finally:
                phone.server_close()


if __name__ == "__main__":
    unittest.main()
