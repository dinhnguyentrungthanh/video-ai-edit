"""The checked HTTP client of the session browser (download_account_http), without a browser.

A local HTTPS fixture server with a throwaway CA (tests/tls_fixtures.py) stands for the source's hosts. The
fixture's resolver and connector reach it only through ``SessionNetwork``, the client's dependency
injection: production has no other way to a local address. Every test checks what really happened on the
wire: the requests and headers the server received, the addresses the connector was given, the names the
resolver was asked.
"""
from __future__ import annotations

import gzip
import socket
import ssl
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from biliflow import recycle_bin
from biliflow.download_account_http import (
    MAX_REQUEST_BODY,
    MAX_REQUEST_SECONDS,
    MAX_RESPONSE_BYTES,
    MAX_RESPONSE_CAP,
    REQUEST_SECONDS,
    SessionHttp,
    SessionNetwork,
)
from biliflow.download_http import Cancelled, HttpError, SafeHttp, default_connector
from biliflow.download_links import default_resolver, is_public_address
from biliflow.download_runner import ProcessControl
from tests.source_fixtures import PUBLIC_ADDRESS, FixtureServer, Reply
from tests.tls_fixtures import HAVE_TLS, NEED_TLS, make_tls_files

TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
HOSTS = ("portal.alpha.example", "tickets.alpha.example", "files.alpha.example")
CERT_NAMES = ("portal.alpha.example", "tickets.alpha.example")  # files.alpha.example: a name mismatch
SECRET = "BF-CANARY-token-7d21"


class Resolver:
    """Records every name asked; answers PUBLIC_ADDRESS unless ``answers`` says otherwise (a list, or a
    callable for answers that change between lookups)."""

    def __init__(self, answers: dict | None = None):
        self.answers = answers or {}
        self.calls: list[str] = []

    def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        answer = self.answers.get(host)
        if callable(answer):
            return answer()
        return list(answer) if answer is not None else [PUBLIC_ADDRESS]


def rebinding(first: list[str], then: list[str]):
    answers = iter([first])

    def answer() -> list[str]:
        return next(answers, then)
    return answer


class RawTlsServer:
    """Answers every TLS connection with the same raw bytes (anything http.server would not send: an
    interim 103, raw UTF-8 headers, a body ended by closing), waits ``linger`` seconds, then closes it with
    close_notify, or without it when ``ragged``."""

    def __init__(self, context: ssl.SSLContext, answer: bytes, *, ragged: bool = False, linger: float = 0.0):
        self.context, self.answer, self.ragged, self.linger = context, answer, ragged, linger
        self.requests: list[bytes] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                raw, _ = self.sock.accept()
            except OSError:
                return
            connection = raw
            try:
                raw.settimeout(5)
                connection = self.context.wrap_socket(raw, server_side=True)
                self.requests.append(connection.recv(65536))
                connection.sendall(self.answer)
                time.sleep(self.linger)
                if not self.ragged:
                    connection.unwrap()  # sends close_notify
            except OSError:  # ssl.SSLError included: the client may close first
                pass
            finally:
                connection.close()

    def connector(self, address, port, timeout):
        return socket.create_connection(self.sock.getsockname(), timeout=timeout)

    def close(self):
        self.sock.close()


@unittest.skipUnless(HAVE_TLS, NEED_TLS)
class SessionHttpCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        cls._tls_dir = TemporaryDirectory(dir=TEMP_PARENT, prefix="session-http-tls-")
        cls.tls = make_tls_files(Path(cls._tls_dir.name), CERT_NAMES)

    @classmethod
    def tearDownClass(cls):
        cls._tls_dir.cleanup()

    def setUp(self):
        self.server = FixtureServer(tls=self.tls.server_context())
        self.addCleanup(self.server.close)
        self.resolver = Resolver()
        self.control = ProcessControl()

    def client(self, *, resolver=None, ssl_context=None, connector=None, **options) -> SessionHttp:
        network = SessionNetwork(resolver=resolver or self.resolver, connector=connector or self.server.connector,
                                 ssl_context=ssl_context or self.tls.client_context())
        return SessionHttp(HOSTS, network, **options)

    def assert_refused(self, code: str, action) -> HttpError:
        with self.assertRaises(HttpError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def raw_server(self, answer: bytes, **options) -> RawTlsServer:
        server = RawTlsServer(self.tls.server_context(), answer, **options)
        self.addCleanup(server.close)
        return server


class ScopeTest(SessionHttpCase):
    def test_a_host_outside_the_source_is_refused_before_any_lookup(self):
        http = self.client()
        for url in ("https://portal.beta.example/", "https://evil.example/x", "https://alpha.example/",
                    "https://sub.portal.alpha.example/"):
            self.assert_refused("HOST_NOT_ALLOWED", lambda: http.send(url, self.control))
        self.assertEqual(self.resolver.calls, [])
        self.assertEqual((self.server.requests, self.server.connections), ([], []))

    def test_only_https_on_the_default_port_with_a_host_name(self):
        http = self.client()
        cases = {"http://portal.alpha.example/": "NOT_HTTPS", "https://portal.alpha.example:8443/": "BAD_PORT",
                 "https://127.0.0.1/": "IP_LITERAL", "https://user:pw@portal.alpha.example/": "USERINFO",
                 "ftp://portal.alpha.example/": "BAD_SCHEME", "https://localhost/": "NO_HOST"}
        for url, code in cases.items():
            self.assert_refused(code, lambda: http.send(url, self.control))
        self.assertEqual(self.resolver.calls, [])
        self.assertEqual(self.server.connections, [])

    def test_an_internal_address_is_refused_and_never_connected(self):
        for answer in (["10.0.0.5"], ["127.0.0.1"], [PUBLIC_ADDRESS, "192.168.1.9"], ["::1"], ["169.254.169.254"]):
            resolver = Resolver({"portal.alpha.example": answer})
            self.assert_refused("PRIVATE_ADDRESS", lambda: self.client(resolver=resolver).send(
                "https://portal.alpha.example/", self.control))
        self.assertEqual((self.server.requests, self.server.connections), ([], []))

    def test_dns_rebinding_cannot_reach_an_internal_address(self):
        self.server.route("/a", Reply(b"ok", content_type="text/plain"))
        resolver = Resolver({"portal.alpha.example": rebinding([PUBLIC_ADDRESS], ["127.0.0.1"])})
        http = self.client(resolver=resolver)
        self.assertEqual(http.send("https://portal.alpha.example/a", self.control).body, b"ok")
        self.assert_refused("PRIVATE_ADDRESS", lambda: http.send("https://portal.alpha.example/a", self.control))
        # One lookup per request, and the socket went only to the address that lookup checked.
        self.assertEqual(resolver.calls, ["portal.alpha.example", "portal.alpha.example"])
        self.assertEqual(self.server.connections, [(PUBLIC_ADDRESS, 443)])
        self.assertEqual(self.server.count("/a"), 1)

    def test_the_connection_uses_the_checked_addresses_in_order_without_a_new_lookup(self):
        self.server.route("/a", Reply(b"ok", content_type="text/plain"))
        second = "93.184.215.15"
        tried = []

        def connector(address, port, timeout):
            tried.append(address)
            if address == PUBLIC_ADDRESS:
                raise ConnectionRefusedError("first address down")
            return self.server.connector(address, port, timeout)
        resolver = Resolver({"portal.alpha.example": [PUBLIC_ADDRESS, second]})
        reply = self.client(resolver=resolver, connector=connector).send("https://portal.alpha.example/a",
                                                                         self.control)
        self.assertEqual(reply.body, b"ok")
        self.assertEqual(tried, [PUBLIC_ADDRESS, second])
        self.assertEqual(resolver.calls, ["portal.alpha.example"])
        self.assertEqual(self.server.seen("/a")[0].host, "portal.alpha.example")  # the Host header keeps the name


class TlsTest(SessionHttpCase):
    def test_a_certificate_of_an_untrusted_ca_is_refused(self):
        http = self.client(ssl_context=ssl.create_default_context())  # the system CAs: not the fixture CA
        self.assert_refused("TLS_ERROR", lambda: http.send("https://portal.alpha.example/", self.control))
        self.assertEqual(self.server.requests, [])

    def test_a_certificate_for_another_name_is_refused(self):
        http = self.client()
        self.assert_refused("TLS_ERROR", lambda: http.send("https://files.alpha.example/", self.control))
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.server.connections, [(PUBLIC_ADDRESS, 443)])  # it got as far as the handshake


class ExchangeTest(SessionHttpCase):
    def test_one_exchange_a_redirect_comes_back_unfollowed(self):
        self.server.route("/r", Reply(b"", 302, "text/html",
                                      headers={"Location": "https://tickets.alpha.example/next"}))
        reply = self.client().send("https://portal.alpha.example/r", self.control)
        self.assertEqual((reply.status, reply.header("Location")), (302, "https://tickets.alpha.example/next"))
        self.assertEqual(self.server.count("/next"), 0)
        self.assertEqual(self.resolver.calls, ["portal.alpha.example"])

    def test_the_browsers_headers_go_out_without_hop_by_hop_conditional_or_range_ones(self):
        self.server.route("/h", Reply(b"ok", content_type="text/plain", headers={"Alt-Svc": 'h3=":443"'}))
        reply = self.client().send("https://portal.alpha.example/h", self.control, headers={
            "Cookie": "sid=s1; pref=p1", "X-Csrf-Token": "c1", "Host": "evil.example", "Connection": "keep-alive",
            "If-None-Match": '"e"', "If-Modified-Since": "x", "Range": "bytes=0-1", "Accept-Encoding": "br",
            "Proxy-Authorization": "Basic x", ":authority": "evil.example", "Upgrade": "h2c"})
        seen = self.server.seen("/h")[0].headers
        self.assertEqual((seen["cookie"], seen["x-csrf-token"], seen["host"]),
                         ("sid=s1; pref=p1", "c1", "portal.alpha.example"))
        self.assertEqual(seen["accept-encoding"], "identity")
        self.assertEqual(seen["connection"], "close")
        for dropped in ("if-none-match", "if-modified-since", "range", "proxy-authorization", "upgrade"):
            self.assertNotIn(dropped, seen)
        self.assertEqual(reply.header("Alt-Svc"), 'h3=":443"')  # the browser layer drops it (its own test)

    def test_no_cookie_is_added_when_the_browser_sent_none(self):
        self.server.route("/n", Reply(b"ok", content_type="text/plain"))
        self.client().send("https://portal.alpha.example/n", self.control, headers={"Accept": "*/*"})
        self.assertNotIn("cookie", self.server.seen("/n")[0].headers)

    def test_a_header_with_a_line_break_is_refused(self):
        self.assert_refused("BAD_REQUEST", lambda: self.client().send(
            "https://portal.alpha.example/", self.control, headers={"X-A": "1\r\nX-B: 2"}))
        self.assertEqual(self.server.requests, [])

    def test_methods_and_request_bodies(self):
        self.server.route("/p", Reply(b"ok", content_type="text/plain"))
        http = self.client()
        http.send("https://portal.alpha.example/p", self.control, method="POST", body=b"user=a&pw=" + SECRET.encode())
        self.assertEqual(self.server.seen("/p")[0].body, b"user=a&pw=" + SECRET.encode())
        self.assert_refused("METHOD_NOT_ALLOWED",
                            lambda: http.send("https://portal.alpha.example/p", self.control, method="PUT"))
        self.assert_refused("BAD_REQUEST",
                            lambda: http.send("https://portal.alpha.example/p", self.control, body=b"x"))
        self.assert_refused("REQUEST_TOO_LARGE", lambda: http.send(
            "https://portal.alpha.example/p", self.control, method="POST", body=b"x" * (MAX_REQUEST_BODY + 1)))
        self.assertEqual(self.server.count("/p"), 1)

    def test_head_returns_no_body_without_waiting_for_one(self):
        server = self.raw_server(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\n",
                                 linger=3.0)  # keeps the connection open: reading a body would wait
        started = time.monotonic()
        reply = self.client(connector=server.connector).send("https://portal.alpha.example/h", self.control,
                                                             method="HEAD")
        self.assertEqual((reply.status, reply.body), (200, b""))
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertTrue(server.requests[0].startswith(b"HEAD /h HTTP/1.1"))

    def test_an_interim_103_is_refused_not_taken_as_the_answer(self):
        server = self.raw_server(b"HTTP/1.1 103 Early Hints\r\nLink: </a.css>; rel=preload\r\n\r\n"
                                 b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        self.assert_refused("BAD_RESPONSE", lambda: self.client(connector=server.connector).send(
            "https://portal.alpha.example/", self.control))

    def test_a_raw_utf8_location_reaches_the_browser_as_the_server_meant_it(self):
        server = self.raw_server("HTTP/1.1 302 Found\r\nLocation: /tập-1?q=phim\r\nContent-Length: 0\r\n\r\n"
                                 .encode("utf-8"))
        reply = self.client(connector=server.connector).send("https://portal.alpha.example/", self.control)
        self.assertEqual((reply.status, reply.header("Location")), (302, "/tập-1?q=phim"))

    def test_a_chunked_body_is_read_whole_and_limited(self):
        chunked = (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n"
                   b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n")
        server = self.raw_server(chunked, linger=3.0)  # the last chunk ends the body, not the closing
        started = time.monotonic()
        reply = self.client(connector=server.connector).send("https://portal.alpha.example/", self.control)
        self.assertEqual(reply.body, b"hello world")
        self.assertEqual(reply.header("Transfer-Encoding"), "")  # framing used up here
        self.assertLess(time.monotonic() - started, 1.5)
        big = self.raw_server(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                              + b"".join(b"4000\r\n" + b"x" * 0x4000 + b"\r\n" for _ in range(8)) + b"0\r\n\r\n")
        self.assert_refused("TOO_LARGE_RESPONSE", lambda: self.client(connector=big.connector, max_body=100_000).send(
            "https://portal.alpha.example/", self.control))

    def test_a_body_without_a_length_needs_close_notify(self):
        answer = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nhello"
        clean = self.raw_server(answer)
        self.assertEqual(self.client(connector=clean.connector).send("https://portal.alpha.example/",
                                                                     self.control).body, b"hello")
        cut = self.raw_server(answer, ragged=True)  # cut by a third party: never taken as a whole answer
        self.assert_refused("NETWORK", lambda: self.client(connector=cut.connector).send(
            "https://portal.alpha.example/", self.control))

    def test_an_error_names_the_host_but_never_the_path_or_query(self):
        self.server.route(f"/slow/{SECRET}", lambda seen, number: (time.sleep(2), Reply(b"late"))[1])
        url = f"https://portal.alpha.example/slow/{SECRET}?ticket={SECRET}"
        errors = (self.assert_refused("TIMEOUT", lambda: self.client(request_seconds=0.5).send(url, self.control)),
                  self.assert_refused("HOST_NOT_ALLOWED", lambda: self.client().send(
                      f"https://evil.example/{SECRET}?t={SECRET}", self.control)))
        for error in errors:
            self.assertNotIn(SECRET, str(error))
            self.assertNotIn(SECRET, error.message)


class LimitTest(SessionHttpCase):
    def test_a_body_over_the_limit_is_refused(self):
        self.server.route("/big", Reply(b"x" * 200_000, content_type="text/plain"))
        self.assert_refused("TOO_LARGE_RESPONSE", lambda: self.client(max_body=100_000).send(
            "https://portal.alpha.example/big", self.control))

    def test_a_body_without_a_length_is_cut_at_the_limit(self):
        self.server.route("/big", Reply(b"x" * 200_000, content_type="text/plain"))
        original = FixtureServer.__dict__["_send"]  # the staticmethod itself, put back as it was

        def without_length(handler, reply, seen):  # the same body, ended by closing instead of a length
            handler.send_response(200)
            handler.send_header("Content-Type", "text/plain")
            handler.send_header("Connection", "close")
            handler.end_headers()
            try:
                handler.wfile.write(reply.body)
            except OSError:
                pass
        FixtureServer._send = staticmethod(without_length)
        self.addCleanup(setattr, FixtureServer, "_send", original)
        self.assert_refused("TOO_LARGE_RESPONSE", lambda: self.client(max_body=100_000).send(
            "https://portal.alpha.example/big", self.control))

    def test_a_compressed_body_is_limited_after_decompression(self):
        bomb = gzip.compress(b"\0" * 2_000_000)
        self.assertLess(len(bomb), 100_000)
        self.server.route("/gz", Reply(bomb, content_type="text/plain", headers={"Content-Encoding": "gzip"}))
        self.assert_refused("TOO_LARGE_RESPONSE", lambda: self.client(max_body=100_000).send(
            "https://portal.alpha.example/gz", self.control))
        self.server.route("/small", Reply(gzip.compress(b"hello"), content_type="text/plain",
                                          headers={"Content-Encoding": "gzip"}))
        reply = self.client().send("https://portal.alpha.example/small", self.control)
        self.assertEqual(reply.body, b"hello")
        self.assertEqual(reply.header("Content-Encoding"), "")  # decoded here: the browser gets plain bytes

    def test_a_body_shorter_than_its_length_is_refused(self):
        self.server.route("/cut", Reply(b"x" * 1000, content_type="text/plain", cut_after=100))
        self.assert_refused("NETWORK", lambda: self.client().send("https://portal.alpha.example/cut", self.control))

    def test_broken_truncated_or_unknown_compression_is_refused(self):
        whole = gzip.compress(b"hello world " * 200)
        bodies = {"/bad": (b"not gzip at all", "gzip"), "/short": (whole[:-12], "gzip"),
                  "/br": (b"\x0b\x02\x80hello\x03", "br")}
        for path, (body, encoding) in bodies.items():
            self.server.route(path, Reply(body, content_type="text/plain", headers={"Content-Encoding": encoding}))
            self.assert_refused("BAD_RESPONSE", lambda: self.client().send(f"https://portal.alpha.example{path}",
                                                                           self.control))

    def test_a_dns_lookup_that_never_answers_ends_in_time(self):
        def hanging(host, port):
            time.sleep(3)
            return [PUBLIC_ADDRESS]
        started = time.monotonic()
        self.assert_refused("DNS_FAILED", lambda: self.client(resolver=hanging, request_seconds=0.5).send(
            "https://portal.alpha.example/", self.control))
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.server.connections, [])

    def test_a_slow_drip_ends_at_the_deadline_of_the_whole_request(self):
        # One byte every 50 ms: each read is quick, the whole answer would take 10 s.
        self.server.route("/drip", Reply(b"x" * 200, content_type="text/plain", chunk=1, delay=0.05))
        http = self.client(request_seconds=1.0, read_timeout=5.0)
        started = time.monotonic()
        self.assert_refused("TIMEOUT", lambda: http.send("https://portal.alpha.example/drip", self.control))
        self.assertLess(time.monotonic() - started, 2.5)

    def test_slow_headers_end_at_the_deadline(self):
        self.server.route("/late", lambda seen, number: (time.sleep(3), Reply(b"late"))[1])
        http = self.client(request_seconds=1.0, read_timeout=10.0)
        started = time.monotonic()
        self.assert_refused("TIMEOUT", lambda: http.send("https://portal.alpha.example/late", self.control))
        self.assertLess(time.monotonic() - started, 2.5)

    def test_a_silent_tls_handshake_ends_at_the_deadline(self):
        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen(4)
        self.addCleanup(silent.close)

        def connector(address, port, timeout):  # accepted by the kernel, then never a byte of TLS back
            return socket.create_connection(silent.getsockname(), timeout=timeout)
        http = self.client(connector=connector, request_seconds=1.0, connect_timeout=10.0)
        started = time.monotonic()
        self.assert_refused("TIMEOUT", lambda: http.send("https://portal.alpha.example/", self.control))
        self.assertLess(time.monotonic() - started, 2.5)

    def test_a_cancel_closes_the_socket_at_once(self):
        self.server.route("/drip", Reply(b"x" * 200, content_type="text/plain", chunk=1, delay=0.05))
        http = self.client(request_seconds=30.0)
        threading.Timer(0.4, self.control.request, args=("cancel",)).start()
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            http.send("https://portal.alpha.example/drip", self.control)
        self.assertLess(time.monotonic() - started, 2.0)
        with self.assertRaises(Cancelled):  # nothing more goes out once cancelled
            http.send("https://portal.alpha.example/drip", self.control)
        self.assertEqual(self.server.count("/drip"), 1)

    def test_a_cancel_during_a_hanging_dns_lookup_ends_it_at_once(self):
        def hanging(host, port):
            time.sleep(5)
            return [PUBLIC_ADDRESS]
        http = self.client(resolver=hanging, request_seconds=30.0)
        threading.Timer(0.2, self.control.request, args=("cancel",)).start()
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            http.send("https://portal.alpha.example/", self.control)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(self.server.connections, [])

    def test_a_cancel_while_connecting_stops_before_any_byte_is_sent(self):
        def slow_connector(address, port, timeout):  # the cancel comes while the TCP connect still runs
            time.sleep(0.6)
            return self.server.connector(address, port, timeout)
        http = self.client(connector=slow_connector, request_seconds=30.0)
        threading.Timer(0.2, self.control.request, args=("cancel",)).start()
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            http.send("https://portal.alpha.example/a", self.control)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.server.requests, [])

    def test_a_cancel_during_the_tls_handshake_closes_the_socket_at_once(self):
        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen(4)
        self.addCleanup(silent.close)

        def connector(address, port, timeout):
            return socket.create_connection(silent.getsockname(), timeout=timeout)
        http = self.client(connector=connector, request_seconds=30.0, connect_timeout=30.0)
        threading.Timer(0.3, self.control.request, args=("cancel",)).start()
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            http.send("https://portal.alpha.example/", self.control)
        self.assertLess(time.monotonic() - started, 1.5)


class DefaultsTest(unittest.TestCase):
    """What production gets when nothing is injected: no test can widen it by accident."""

    def test_production_checks_tls_and_every_address_with_the_system_parts(self):
        network = SessionNetwork()
        self.assertEqual((network.resolver, network.is_public, network.connector, network.ssl_context),
                         (default_resolver, is_public_address, default_connector, None))
        http = SessionHttp(HOSTS)
        self.assertEqual(http.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(http.context.check_hostname)
        self.assertEqual((http.request_seconds, http.max_body, http.connector),
                         (REQUEST_SECONDS, MAX_RESPONSE_BYTES, default_connector))

    def test_limits_have_ceilings_and_a_source_needs_hosts(self):
        for options in ({"request_seconds": 0}, {"request_seconds": MAX_REQUEST_SECONDS + 1},
                        {"connect_timeout": -1}, {"read_timeout": MAX_REQUEST_SECONDS * 10},
                        {"max_body": 0}, {"max_body": MAX_RESPONSE_CAP + 1}):
            with self.assertRaises(ValueError):
                SessionHttp(HOSTS, **options)
        with self.assertRaises(ValueError):
            SessionHttp(())


class AnonymousClientTest(unittest.TestCase):
    def test_safehttp_still_refuses_any_cookie(self):
        """The anonymous downloader keeps its own client: no cookie or credential header can be handed to it."""
        for name in ("Cookie", "cookie", "Authorization"):
            with self.assertRaises(ValueError):
                SafeHttp().open("https://media.example/x", ProcessControl(), headers={name: "sid=1"})


if __name__ == "__main__":
    unittest.main()
