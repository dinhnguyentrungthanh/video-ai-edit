"""Request limits of the local servers: Content-Length, loopback-only binding."""

import contextlib
import io
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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
from biliflow.review_workflow import serve_review_ui


LAN_HOSTS = ("0.0.0.0", "192.168.1.5", "10.0.0.2", "::", "my-pc", "127.0.0.1.evil.example", "")
# Spellings the IPv4 servers would not bind as written, or that only look like loopback.
ODD_HOSTS = (
    " 127.0.0.1", "127.0.0.1 ", "[127.0.0.1]", "::1", "[::1]", "::ffff:127.0.0.1", "127.1", "0x7f.0.0.1",
    "2130706433", "127.000.000.001", "localhost.", "localhoſt", "ｌｏｃａｌｈｏｓｔ", "local\0host", None,
)
LOOPBACK_HOSTS = ("127.0.0.1", "127.5.6.7", "localhost", "LOCALHOST")


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
            with patch("biliflow.review_workflow.ThreadingHTTPServer", side_effect=OSError("bind")) as server:
                with self.assertRaises(OSError):
                    serve_review_ui(project_root=root, queue_path=root / "reports" / "q.json",
                                    host="localhost", port=0)
            self.assertEqual(server.call_args.args[0], ("127.0.0.1", 0))
            stub = ControlCenter.__new__(ControlCenter)
            stub.host, stub.port, stub.root, stub.server = "localhost", 0, root, None
            stub._stopping = threading.Event()
            stub.stop_ai_audits, stub.stop_ai_login = Mock(), Mock()
            stub.watcher, stub.scheduler, stub.store, stub.lock = Mock(), Mock(), Mock(), Mock()
            with patch("biliflow.control_center.ThreadingHTTPServer", side_effect=OSError("bind")) as server, \
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
            with patch("biliflow.control_center.ThreadingHTTPServer") as server:
                with self.assertRaises(ValueError):
                    stub.serve()
                server.assert_not_called()
            with patch("biliflow.review_workflow.ThreadingHTTPServer") as server:
                with self.assertRaises(ValueError):
                    serve_review_ui(project_root=root / "missing", queue_path=root / "q.json", host="0.0.0.0")
                server.assert_not_called()


if __name__ == "__main__":
    unittest.main()
