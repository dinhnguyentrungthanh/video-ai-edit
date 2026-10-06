"""SafeHttp, the network-checked HTTP client of BiliFlow's own source transfers.

Every link is a ``.example`` name on the default port. The fixture server's connector sends each checked
address to a local server and records it, so the production policy (link rules, public DNS answers,
checked redirects) stays on and nothing leaves the PC: no real site, no real DNS lookup.
"""
from __future__ import annotations

import dataclasses
import gzip
import socket
import threading
import time
import unittest
import zlib
from functools import partial
from typing import Callable
from unittest import mock

from biliflow import download_http
from biliflow.download_http import (
    MAX_DERIVED_URL_LENGTH,
    Cancelled,
    HttpError,
    SafeHttp,
    Scope,
    allowed_headers,
    with_retries,
)
from biliflow.download_links import LinkRejected, check_link, is_public_address
from biliflow.download_runner import ProcessControl
from tests.source_fixtures import PUBLIC_ADDRESS, FixtureServer, Reply

URL = "http://media.example"
OTHER_PUBLIC = "93.184.216.34"
SECRET = "SECRET123"
# A slow body: 1 KB every 0.2 s, about 3.2 s in full; a stop must end its read well before that.
SLOW_BODY = b"x" * 16 * 1024
SLOW_CHUNK_SECONDS = 0.2
PROMPT_SECONDS = 1.5
# A reply that sends its first KB and then stays silent longer than the read timeout of the test.
STALL_SECONDS = 2.0
# How long a held answer waits before the server replies (a stop must not wait for it).
HOLD_SECONDS = 3.0


class Lookups:
    """DNS answers per host name, one list per lookup (the last one repeats); every lookup is recorded.
    A host without answers resolves to the public fixture address."""

    def __init__(self, answers: dict[str, list[list[str]]] | None = None):
        self.answers = answers or {}
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append((host, port))
        sequence = self.answers.get(host, [[PUBLIC_ADDRESS]])
        number = sum(1 for name, _ in self.calls if name == host)
        return list(sequence[min(number, len(sequence)) - 1])

    @property
    def hosts(self) -> list[str]:
        return [host for host, _ in self.calls]


class QuietControl:
    """An Interruptible that never asks to stop; it keeps the closers not removed yet and every pause."""

    requested = False

    def __init__(self) -> None:
        self.active: dict[int, Callable[[], None]] = {}
        self.waits: list[float] = []
        self._next = 0

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        return False

    def add_closer(self, closer: Callable[[], None]) -> Callable[[], None]:
        key = self._next
        self._next += 1
        self.active[key] = closer

        def remove() -> None:
            self.active.pop(key, None)
        return remove


def later(seconds: float, action: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(seconds, action)
    timer.daemon = True
    timer.start()
    return timer


def slow_body() -> Reply:
    return Reply(SLOW_BODY, chunk=1024, delay=SLOW_CHUNK_SECONDS)


def stalled_body() -> Reply:
    """The first KB at once, then nothing for STALL_SECONDS (the handler is not waited for at teardown)."""
    return Reply(b"t" * 2048, chunk=1024, delay=STALL_SECONDS)


class ServerCase(unittest.TestCase):
    """One fixture server per class (its shutdown polls every 0.5 s); each test starts from empty state."""

    server: FixtureServer

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = FixtureServer()
        # A deliberately stalled handler must not hold tearDownClass; its socket is closed by then.
        cls.server.httpd.block_on_close = False

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.close()

    def setUp(self) -> None:
        self.server.routes.clear()
        self.server.requests.clear()
        self.server.connections.clear()
        self.control = ProcessControl()
        self.lookups = Lookups()
        self.http = self.server.http(resolver=self.lookups)

    def assert_refused(self, code: str, action: Callable[[], object]) -> HttpError:
        with self.assertRaises(HttpError) as caught:
            action()
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception

    def assert_no_secret(self, error: HttpError) -> None:
        for text in (str(error), error.message):
            self.assertNotIn(SECRET, text)
            self.assertNotIn("token=", text)


# --------------------------------------------------------------------------------------- policy
class LinkPolicyTests(ServerCase):
    def test_links_that_break_the_link_rules_are_refused_before_dns_and_connect(self) -> None:
        cases = {
            "http://93.184.215.14/v": "IP_LITERAL",
            "http://[2606:2800:220:1::1]/v": "IP_LITERAL",
            "http://media.example:8080/v": "BAD_PORT",
            "https://media.example:80/v": "BAD_PORT",
            "http://viewer:secret@media.example/v": "USERINFO",
            "http://viewer@media.example/v": "USERINFO",
            "ftp://media.example/v": "BAD_SCHEME",
            "file:///C:/Windows/win.ini": "BAD_SCHEME",
            "http://nas.local/v": "LOCAL_HOST",
            "http://intranet/v": "NO_HOST",
        }
        for url, code in cases.items():
            with self.subTest(url=url):
                with self.assertRaises(LinkRejected) as rule:
                    check_link(url)
                error = self.assert_refused(code, partial(self.http.open, url, self.control))
                self.assertEqual(error.code, rule.exception.code)
                self.assertFalse(error.retryable)
                self.assertNotIn("secret", error.message)
        self.assertEqual(self.lookups.calls, [])
        self.assertEqual(self.server.connections, [])
        self.assertEqual(self.server.requests, [])

    def test_check_normalizes_the_link_and_keeps_the_query_in_the_request_path(self) -> None:
        target = self.http.check("http://Media.Example./clip/a.mp4?sig=1&e=2#t=5")
        https = self.http.check("https://media.example")

        self.assertEqual(target.url, "http://media.example/clip/a.mp4?sig=1&e=2")
        self.assertEqual((target.host, target.port, target.path, target.addresses),
                         ("media.example", 80, "/clip/a.mp4?sig=1&e=2", (PUBLIC_ADDRESS,)))
        self.assertEqual((https.scheme, https.port, https.path), ("https", 443, "/"))
        self.assertEqual(self.server.connections, [])

    def test_derived_links_may_be_longer_than_a_pasted_link_but_stay_bounded(self) -> None:
        long_path = "/seg.ts?sig=" + "a" * 4000

        self.assertEqual(self.http.check(URL + long_path).path, long_path)
        self.assert_refused("URL_TOO_LONG", partial(self.http.check, URL + "/" + "a" * MAX_DERIVED_URL_LENGTH))


class DnsPolicyTests(ServerCase):
    def test_a_host_with_any_internal_address_is_refused(self) -> None:
        answers = (["10.0.0.5"], ["127.0.0.1"], ["169.254.169.254"], ["0.0.0.0"], ["100.64.0.1"], ["::1"],
                   ["::ffff:127.0.0.1"], ["not-an-address"], [PUBLIC_ADDRESS, "192.168.1.10"])
        for answer in answers:
            with self.subTest(answer=answer):
                http = self.server.http(resolver=lambda host, port, answer=answer: list(answer))
                error = self.assert_refused("PRIVATE_ADDRESS",
                                            partial(http.open, f"{URL}/v?token={SECRET}", self.control))
                self.assertIn("media.example", error.message)
                self.assert_no_secret(error)
        self.assertEqual(self.server.connections, [])

    def test_a_lookup_that_fails_or_answers_nothing_is_dns_failed(self) -> None:
        def failing(host: str, port: int) -> list[str]:
            raise socket.gaierror(11001, "getaddrinfo failed")

        for resolver in (failing, lambda host, port: []):
            with self.subTest(resolver=resolver):
                http = self.server.http(resolver=resolver)
                error = self.assert_refused("DNS_FAILED", partial(http.open, URL + "/v", self.control))
                self.assertIn("media.example", error.message)
                self.assertTrue(error.retryable)  # the network may be down for a moment: the task can resume
        self.assertEqual(self.server.connections, [])

    def test_ipv6_forms_that_carry_an_internal_ipv4_or_stay_inside_a_network_are_internal(self) -> None:
        for address in ("::7f00:1", "::a00:1", "::ffff:0:7f00:1", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe",
                        "fec0::1", "64:ff9b:1::1", "::", "::1"):
            with self.subTest(address=address):
                self.assertFalse(is_public_address(address))
        for address in ("64:ff9b::5db8:d70e", "2606:2800:21f:cb07:6820:80da:af6b:8b2c", PUBLIC_ADDRESS):
            with self.subTest(address=address):
                self.assertTrue(is_public_address(address))

    def test_every_checked_address_is_tried_in_turn(self) -> None:
        dead, attempts = "93.184.215.99", []
        self.server.route("/v", Reply(b"second"))

        def connector(address: str, port: int, timeout: float) -> socket.socket:
            attempts.append(address)
            if address == dead:
                raise ConnectionRefusedError("dead node")
            return self.server.connector(address, port, timeout)
        http = SafeHttp(resolver=lambda host, port: [dead, PUBLIC_ADDRESS], connector=connector)

        body, _ = http.fetch(URL + "/v", self.control, limit=100)

        self.assertEqual((body, attempts), (b"second", [dead, PUBLIC_ADDRESS]))

    def test_a_non_ascii_path_is_percent_encoded_not_a_network_error(self) -> None:
        self.server.route("/phim/t%E1%BA%ADp-1.mp4", Reply(b"ok"))

        body, _ = self.server.http().fetch(URL + "/phim/tập-1.mp4", self.control, limit=100)

        self.assertEqual(body, b"ok")

    def test_a_redirect_from_https_to_http_is_refused(self) -> None:
        self.server.route("/v", Reply(b"", 302, headers={"Location": "http://media.example/plain"}))
        http = self.server.http()
        plain = http._connection
        # The fixture server has no TLS: the https link is served over plain HTTP, its scheme stays https.
        http._connection = lambda target: plain(dataclasses.replace(target, scheme="http"))

        error = self.assert_refused("DOWNGRADE", partial(http.open, "https://media.example/v", self.control))

        self.assertNotIn("plain", error.message)
        self.assertEqual(self.server.count("/plain"), 0)

    def test_one_open_resolves_once_and_connects_to_the_address_it_checked(self) -> None:
        # Arrange: a rebinding name, public at the first lookup and loopback afterwards.
        lookups = Lookups({"media.example": [[PUBLIC_ADDRESS], ["127.0.0.1"]]})
        http = self.server.http(resolver=lookups)
        self.server.route("/v", Reply(b"public"))

        # Act
        body, response = http.fetch(URL + "/v", self.control, limit=100)

        # Assert
        self.assertEqual(body, b"public")
        self.assertEqual(lookups.hosts, ["media.example"])
        self.assertEqual(self.server.connections, [(PUBLIC_ADDRESS, 80)])
        self.assertEqual(response.target.addresses, (PUBLIC_ADDRESS,))

    def test_each_new_request_checks_the_name_again(self) -> None:
        lookups = Lookups({"media.example": [[PUBLIC_ADDRESS], ["127.0.0.1"]]})
        http = self.server.http(resolver=lookups)
        self.server.route("/v", Reply(b"public"))
        http.fetch(URL + "/v", self.control, limit=100)

        self.assert_refused("PRIVATE_ADDRESS", partial(http.open, URL + "/v", self.control))

        self.assertEqual(lookups.hosts, ["media.example", "media.example"])
        self.assertEqual(self.server.connections, [(PUBLIC_ADDRESS, 80)])
        self.assertEqual(self.server.count("/v"), 1)

    def test_the_connection_never_looks_up_the_host_name_itself(self) -> None:
        real = socket.getaddrinfo
        asked: list[str] = []

        def guarded(host: str, *args: object, **kwargs: object) -> object:
            asked.append(host)
            if host != "127.0.0.1":  # only the fixture connector's own loopback; never a real lookup
                raise socket.gaierror(11001, "lookup refused by the test")
            return real(host, *args, **kwargs)

        self.server.route("/v", Reply(b"ok"))
        with mock.patch("socket.getaddrinfo", guarded):
            body, _ = self.http.fetch(URL + "/v", self.control, limit=100)

        self.assertEqual(body, b"ok")
        self.assertEqual(set(asked), {"127.0.0.1"})


# ------------------------------------------------------------------------------------ redirects
class RedirectTests(ServerCase):
    def test_a_redirect_chain_on_public_hosts_is_followed_to_the_final_link(self) -> None:
        lookups = Lookups({"cdn.example": [[OTHER_PUBLIC]]})
        http = self.server.http(resolver=lookups)
        self.server.route("/start", Reply(status=302, headers={"Location": "http://cdn.example/dir/hop"}))
        self.server.route("/dir/hop", Reply(status=301, headers={"Location": "final?part=2"}))  # relative
        self.server.route("/dir/final", Reply(b"media"))

        body, response = http.fetch(URL + "/start", self.control, limit=100)

        self.assertEqual(body, b"media")
        self.assertEqual(response.url, "http://cdn.example/dir/final?part=2")
        self.assertEqual(response.host, "cdn.example")
        self.assertEqual(lookups.hosts, ["media.example", "cdn.example", "cdn.example"])
        self.assertEqual(self.server.connections, [(PUBLIC_ADDRESS, 80), (OTHER_PUBLIC, 80), (OTHER_PUBLIC, 80)])
        self.assertEqual([seen.host for seen in self.server.requests],
                         ["media.example", "cdn.example", "cdn.example"])

    def test_every_hop_gets_the_referer_a_browser_would_send(self) -> None:
        http = self.server.http(resolver=Lookups({"cdn.example": [[OTHER_PUBLIC]]}))
        self.server.route("/start", Reply(status=302, headers={"Location": "/hop"}))  # the same host
        self.server.route("/hop", Reply(status=302, headers={"Location": "http://cdn.example/final"}))
        self.server.route("/final", Reply(b"media"))
        page = URL + "/watch?v=abc&token=SECRETVALUE123"  # a page on the host of the first request

        body, _ = http.fetch(URL + "/start", self.control, limit=100, headers={"Referer": page, "Origin": URL})

        self.assertEqual(body, b"media")
        self.assertEqual([(seen.host, seen.headers.get("referer"), seen.headers.get("origin"))
                          for seen in self.server.requests],
                         [("media.example", page, URL), ("media.example", page, URL), ("cdn.example", URL + "/", URL)])

    def test_a_page_link_with_vietnamese_letters_still_goes_out_as_a_header(self) -> None:
        self.server.route("/v", Reply(b"media"))

        body, _ = self.http.fetch(URL + "/v", self.control, limit=100, headers={"Referer": URL + "/tập-1"})

        self.assertEqual(body, b"media")  # before: UnicodeEncodeError in http.client, reported as NETWORK
        self.assertEqual(self.server.seen("/v")[0].headers.get("referer"), URL + "/t%E1%BA%ADp-1")

    def test_a_page_on_another_host_gives_only_its_origin_even_to_the_first_request(self) -> None:
        self.server.route("/v", Reply(b"media"))
        page = "http://video.example/watch?v=abc&token=SECRETVALUE123"

        self.http.fetch(URL + "/v", self.control, limit=100, headers={"Referer": page})

        self.assertEqual(self.server.seen("/v")[0].headers.get("referer"), "http://video.example/")

    def test_every_redirect_status_is_followed(self) -> None:
        self.server.route("/done", Reply(b"done"))
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.server.route(f"/r{status}", Reply(status=status, headers={"Location": "/done"}))

                body, response = self.http.fetch(f"{URL}/r{status}", self.control, limit=10)

                self.assertEqual((body, response.url), (b"done", URL + "/done"))

    def test_a_redirect_to_a_host_with_an_internal_address_is_refused_before_connecting(self) -> None:
        self.server.route("/secret", Reply(b"internal"))
        for location in ("http://intranet.example/secret", "//intranet.example/secret"):
            with self.subTest(location=location):
                self.server.connections.clear()
                lookups = Lookups({"intranet.example": [["10.0.0.5"]]})
                http = self.server.http(resolver=lookups)
                self.server.route("/start", Reply(status=302, headers={"Location": location}))

                self.assert_refused("PRIVATE_ADDRESS", partial(http.open, URL + "/start", self.control))

                self.assertEqual(lookups.hosts, ["media.example", "intranet.example"])
                self.assertEqual(self.server.connections, [(PUBLIC_ADDRESS, 80)])
        self.assertEqual(self.server.count("/secret"), 0)

    def test_a_redirect_that_breaks_the_link_rules_is_refused(self) -> None:
        cases = {
            "http://10.0.0.5/secret": "IP_LITERAL",
            "http://127.0.0.1/secret": "IP_LITERAL",
            "http://[::1]/secret": "IP_LITERAL",
            "http://2130706433/secret": "IP_LITERAL",  # 127.0.0.1 written as one number
            "http://127.1/secret": "IP_LITERAL",
            "http://0x7f.1/secret": "IP_LITERAL",
            "file:///C:/Windows/win.ini": "BAD_SCHEME",
            "ftp://media.example/secret": "BAD_SCHEME",
            "http://media.example:8080/secret": "BAD_PORT",
            "http://viewer:pw@media.example/secret": "USERINFO",
            "http://router.lan/secret": "LOCAL_HOST",
        }
        self.server.route("/secret", Reply(b"internal"))
        for location, code in cases.items():
            with self.subTest(location=location):
                self.server.connections.clear()
                self.lookups.calls.clear()
                self.server.route("/start", Reply(status=302, headers={"Location": location}))

                self.assert_refused(code, partial(self.http.open, URL + "/start", self.control))

                self.assertEqual(self.lookups.hosts, ["media.example"])
                self.assertEqual(self.server.connections, [(PUBLIC_ADDRESS, 80)])
        self.assertEqual(self.server.count("/secret"), 0)

    def test_more_redirects_than_allowed_is_too_many_redirects(self) -> None:
        self.server.route("/loop", Reply(status=302, headers={"Location": "/loop"}))
        http = self.server.http(resolver=self.lookups, max_redirects=2)

        self.assert_refused("TOO_MANY_REDIRECTS", partial(http.open, URL + "/loop", self.control))

        self.assertEqual(self.server.count("/loop"), 3)  # the first request and the two allowed redirects

    def test_exactly_the_allowed_number_of_redirects_succeeds(self) -> None:
        self.server.route("/a", Reply(status=302, headers={"Location": "/b"}))
        self.server.route("/b", Reply(status=302, headers={"Location": "/c"}))
        self.server.route("/c", Reply(b"end"))
        http = self.server.http(resolver=self.lookups, max_redirects=2)

        body, response = http.fetch(URL + "/a", self.control, limit=10)

        self.assertEqual((body, response.url), (b"end", URL + "/c"))

    def test_a_redirect_without_a_location_is_a_bad_redirect(self) -> None:
        self.server.route("/nowhere", Reply(status=302))

        error = self.assert_refused("BAD_REDIRECT",
                                    partial(self.http.open, f"{URL}/nowhere?token={SECRET}", self.control))

        self.assertIn("media.example", error.message)
        self.assert_no_secret(error)

    def test_no_closer_is_left_registered_after_redirects_errors_and_reads(self) -> None:
        control = QuietControl()
        self.server.route("/start", Reply(status=302, headers={"Location": "/done"}))
        self.server.route("/done", Reply(b"ok"))
        self.server.route("/gone", Reply(status=404))

        self.http.fetch(URL + "/start", control, limit=10)
        self.assert_refused("UNAVAILABLE", partial(self.http.open, URL + "/gone", control))
        with self.http.open(URL + "/done", control) as response:
            self.assertEqual(len(control.active), 1)
            response.read_all(10)

        self.assertEqual(control.active, {})


class RefererPolicyTest(unittest.TestCase):
    """A provider's Referer goes out as a browser sends it by default (strict-origin-when-cross-origin)."""

    @staticmethod
    def sent(referer: str, url: str = URL + "/v.mp4") -> str | None:
        scheme, _, host, path = url.split("/", 3)
        scheme = scheme.rstrip(":")
        target = download_http.Target(url, scheme, host, 443 if scheme == "https" else 80, "/" + path,
                                      (PUBLIC_ADDRESS,))
        return download_http._referer_for(target, {"Referer": referer}).get("Referer")

    def test_the_whole_page_link_only_to_its_own_origin(self) -> None:
        cases = {"http://media.example/watch?v=1#t=5": "http://media.example/watch?v=1",  # never a fragment
                 "http://user:pw@media.example/watch": "http://media.example/watch",  # never an account
                 "http://media.example:80/watch": "http://media.example/watch",
                 "http://media.example": "http://media.example/",
                 # percent-encoded as in the request line: a header carries no letter above U+00FF
                 "http://media.example/tập-1?tên=một": "http://media.example/t%E1%BA%ADp-1?t%C3%AAn=m%E1%BB%99t",
                 "http://media.example/t%E1%BA%ADp-1": "http://media.example/t%E1%BA%ADp-1"}
        for referer, expected in cases.items():
            with self.subTest(referer=referer):
                self.assertEqual(self.sent(referer), expected)
        self.assertEqual(self.sent("https://media.example/w?v=1", "https://media.example/v.mp4"),
                         "https://media.example/w?v=1")

    def test_only_the_origin_to_another_host_port_or_scheme(self) -> None:
        cases = {"http://video.example/watch?v=1&token=SECRET": "http://video.example/",
                 "http://user:pw@video.example/watch": "http://video.example/",
                 "http://www.media.example/watch": "http://www.media.example/",
                 "http://media.example:8080/watch": "http://media.example:8080/"}
        for referer, expected in cases.items():
            with self.subTest(referer=referer):
                self.assertEqual(self.sent(referer), expected)
        self.assertEqual(self.sent("http://media.example/watch", "https://media.example/v.mp4"),
                         "http://media.example/")

    def test_nothing_from_https_to_http_or_for_a_value_that_is_not_a_web_link(self) -> None:
        for referer in ("https://video.example/watch", "https://media.example/watch", "http://[::1/watch",
                        "http://video.example:99999/watch", "video.example/watch", "ftp://video.example/x", "",
                        "http:///watch", "http://bücher.example/watch"):  # the last: a host not in its IDNA form
            with self.subTest(referer=referer):
                self.assertIsNone(self.sent(referer))

    def test_other_headers_pass_unchanged(self) -> None:
        target = download_http.Target(URL + "/v", "http", "media.example", 80, "/v", (PUBLIC_ADDRESS,))
        headers = {"Origin": "http://video.example", "Range": "bytes=0-", "Accept": "video/*"}

        self.assertEqual(download_http._referer_for(target, headers), headers)


# ------------------------------------------------------------------------------------- statuses
class StatusTests(ServerCase):
    CASES = {
        401: ("LOGIN_REQUIRED", False), 407: ("LOGIN_REQUIRED", False), 403: ("FORBIDDEN", False),
        404: ("UNAVAILABLE", False), 410: ("UNAVAILABLE", False), 451: ("UNAVAILABLE", False),
        429: ("SERVER_BUSY", True), 500: ("SERVER_BUSY", True), 503: ("SERVER_BUSY", True),
        418: ("HTTP_ERROR", False),
    }

    def test_error_statuses_map_to_stable_codes_and_messages_without_the_link(self) -> None:
        for status, (code, retryable) in self.CASES.items():
            with self.subTest(status=status):
                self.server.route(f"/s{status}", Reply(b"error page", status=status, content_type="text/html"))

                error = self.assert_refused(
                    code, partial(self.http.open, f"{URL}/s{status}?token={SECRET}", self.control))

                self.assertEqual((error.status, error.retryable), (status, retryable))
                self.assertIn("media.example", error.message)
                self.assertNotIn(f"/s{status}", error.message)
                self.assert_no_secret(error)


# -------------------------------------------------------------------------------------- headers
class HeaderTests(ServerCase):
    def test_only_allow_listed_request_headers_are_accepted(self) -> None:
        accepted = allowed_headers({"Referer": URL + "/page", "Origin": URL, "Accept": "video/*",
                                    "Accept-Language": "vi", "RANGE": "bytes=0-", "If-Range": '"v1"'})

        self.assertEqual(len(accepted), 6)
        self.assertEqual(allowed_headers(None), {})
        for name in ("Cookie", "Authorization", "Proxy-Authorization", "User-Agent", "Host", "X-Forwarded-For",
                     "Accept-Encoding", "Connection"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                allowed_headers({name: "value"})

    def test_header_values_with_a_line_break_are_refused(self) -> None:
        for value in ("a\r\nCookie: session=1", "a\nX-Injected: 1", "a\rb"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                allowed_headers({"Referer": value})

    def test_a_refused_header_fails_before_any_lookup_or_connection(self) -> None:
        with self.assertRaises(ValueError):
            self.http.open(URL + "/v", self.control, headers={"Cookie": "session=1"})

        self.assertEqual(self.lookups.calls, [])
        self.assertEqual(self.server.connections, [])

    def test_the_request_carries_the_browser_user_agent_and_the_range_but_no_credentials(self) -> None:
        body = bytes(range(256))
        self.server.route("/v", Reply(body))

        with self.http.open(URL + "/v", self.control,
                            headers={"Range": "bytes=2-5", "Referer": URL + "/page"}) as response:
            data = response.read_all(100)
            status, content_range = response.status, response.header("Content-Range")

        self.assertEqual((status, data, content_range), (206, body[2:6], "bytes 2-5/256"))
        seen = self.server.seen("/v")[0]
        self.assertEqual(seen.host, "media.example")
        self.assertEqual(seen.headers["user-agent"], download_http.USER_AGENT)
        self.assertEqual(seen.headers["range"], "bytes=2-5")
        self.assertEqual(seen.headers["referer"], URL + "/page")
        self.assertEqual(seen.headers["accept-encoding"], "identity")
        for name in ("cookie", "authorization", "proxy-authorization"):
            self.assertNotIn(name, seen.headers)

    def test_a_cookie_set_by_the_server_is_never_sent_back(self) -> None:
        self.server.route("/login", Reply(status=302, headers={"Set-Cookie": "session=abc; Path=/",
                                                                "Location": "/after"}))
        self.server.route("/after", Reply(b"ok"))

        self.http.fetch(URL + "/login", self.control, limit=10)
        self.http.fetch(URL + "/after", self.control, limit=10)

        self.assertEqual(self.server.count("/after"), 2)
        for seen in self.server.requests:
            self.assertNotIn("cookie", seen.headers)


# ----------------------------------------------------------------------------------------- body
class BodyTests(ServerCase):
    def test_read_some_returns_at_most_the_limit_from_the_start_of_the_body(self) -> None:
        body = bytes(range(256)) * 400
        self.server.route("/v", Reply(body))

        with self.http.open(URL + "/v", self.control) as response:
            length = response.content_length
            head = response.read_some(10)
        with self.http.open(URL + "/v", self.control) as response:
            whole = response.read_some(10 * len(body))

        self.assertEqual((length, head, whole), (len(body), body[:10], body))

    def test_read_some_does_not_wait_for_the_rest_of_the_body(self) -> None:
        self.server.route("/slow", slow_body())
        started = time.monotonic()

        with self.http.open(URL + "/slow", self.control) as response:
            sample = response.read_some(1024)

        self.assertEqual(sample, SLOW_BODY[:1024])
        self.assertLess(time.monotonic() - started, PROMPT_SECONDS)

    def test_chunks_yield_what_has_arrived_without_waiting_for_a_full_piece(self) -> None:
        # Progress follows a slow server: the first piece is the first KB, not the 256 KB of a full read.
        self.server.route("/slow", slow_body())
        started = time.monotonic()

        with self.http.open(URL + "/slow", self.control) as response:
            first = next(response.chunks())

        self.assertLess(len(first), len(SLOW_BODY))
        self.assertTrue(SLOW_BODY.startswith(first))
        self.assertLess(time.monotonic() - started, PROMPT_SECONDS)

    def test_read_all_refuses_a_body_larger_than_its_limit(self) -> None:
        body = b"b" * 2000
        self.server.route("/v", Reply(body))

        data, _ = self.http.fetch(URL + "/v", self.control, limit=2000)
        error = self.assert_refused("TOO_LARGE_RESPONSE", partial(self.http.fetch, URL + "/v", self.control,
                                                                  limit=1999))

        self.assertEqual(data, body)
        self.assertIn("media.example", error.message)

    def test_a_compressed_body_is_decoded(self) -> None:
        payload = b"#EXTM3U\n" + b"#EXTINF:1.0,\nseg.ts\n" * 50
        for encoding, data in (("gzip", gzip.compress(payload)), ("x-gzip", gzip.compress(payload)),
                               ("deflate", zlib.compress(payload)), ("identity", payload)):
            with self.subTest(encoding=encoding):
                self.server.route("/list.m3u8", Reply(data, headers={"Content-Encoding": encoding}))

                body, _ = self.http.fetch(URL + "/list.m3u8", self.control, limit=10_000)

                self.assertEqual(body, payload)

    def test_a_compressed_body_larger_than_the_limit_once_decoded_is_refused(self) -> None:
        bomb = gzip.compress(b"\0" * 1_000_000)
        self.server.route("/bomb", Reply(bomb, headers={"Content-Encoding": "gzip"}))

        self.assert_refused("TOO_LARGE_RESPONSE", partial(self.http.fetch, URL + "/bomb", self.control,
                                                          limit=10_000))

    def test_an_unknown_or_broken_compression_is_a_bad_response(self) -> None:
        cases = (("br", b"\x1b\x00brotli-ish"), ("gzip", b"this is not gzip"))
        for encoding, data in cases:
            with self.subTest(encoding=encoding):
                self.server.route("/v", Reply(data, headers={"Content-Encoding": encoding}))

                self.assert_refused("BAD_RESPONSE", partial(self.http.fetch, URL + "/v", self.control,
                                                            limit=10_000))

    def test_a_truncated_compressed_body_is_a_bad_response(self) -> None:
        # The stream stops halfway although Content-Length matches what was sent: not the whole body.
        payload = b"#EXTM3U\n" + b"".join(b"#EXTINF:1.0,\nseg-%04d.ts\n" % number for number in range(400))
        compressed = gzip.compress(payload)
        self.server.route("/cut.m3u8", Reply(compressed[: len(compressed) // 2],
                                             headers={"Content-Encoding": "gzip"}))

        self.assert_refused("BAD_RESPONSE", partial(self.http.fetch, URL + "/cut.m3u8", self.control,
                                                    limit=100_000))

    def test_a_body_shorter_than_its_content_length_is_a_retryable_network_error(self) -> None:
        self.server.route("/cut", Reply(b"c" * 100_000, cut_after=1000))
        received = bytearray()

        with self.http.open(f"{URL}/cut?token={SECRET}", self.control) as response:
            with self.assertRaises(HttpError) as caught:
                for chunk in response.chunks():
                    received += chunk

        error = caught.exception
        self.assertEqual((error.code, error.retryable), ("NETWORK", True))
        self.assertLessEqual(len(received), 1000)
        self.assertIn("media.example", error.message)
        self.assert_no_secret(error)


# ------------------------------------------------------------------------------ timeouts/cancel
class TimeoutTests(ServerCase):
    def test_a_read_that_stalls_longer_than_the_read_timeout_is_a_retryable_network_error(self) -> None:
        self.server.route("/stall", stalled_body())
        http = self.server.http(resolver=self.lookups, read_timeout=0.3)
        started = time.monotonic()

        with http.open(f"{URL}/stall?token={SECRET}", self.control) as response:
            with self.assertRaises(HttpError) as caught:
                response.read_all(1_000_000)

        error = caught.exception
        self.assertEqual((error.code, error.retryable), ("NETWORK", True))
        self.assertLess(time.monotonic() - started, PROMPT_SECONDS)
        self.assertIn("media.example", error.message)
        self.assert_no_secret(error)
        self.assertFalse(self.control.requested)


class CancelTests(ServerCase):
    def read_until_cancelled(self, control: ProcessControl | Scope, request: Callable[[], None]) -> float:
        """Iterate a slow body with the default chunk size; ``request`` runs from another thread 0.2 s
        later. Returns the seconds taken (the whole body needs about 3.2 s)."""
        self.server.route("/slow", slow_body())
        response = self.http.open(URL + "/slow", control)
        started = time.monotonic()
        self.addCleanup(later(0.2, request).cancel)
        with response, self.assertRaises(Cancelled):
            for _ in response.chunks():
                pass
        return time.monotonic() - started

    def test_a_stop_from_another_thread_ends_a_read_in_progress_at_once(self) -> None:
        elapsed = self.read_until_cancelled(self.control, partial(self.control.request, "stop"))

        self.assertLess(elapsed, PROMPT_SECONDS, "the stop waited for the whole body")
        self.assertEqual(self.control.reason, "stop")

    def test_a_scope_abort_ends_a_read_in_progress_without_stopping_the_task(self) -> None:
        scope = Scope(self.control)

        elapsed = self.read_until_cancelled(scope, scope.abort)

        self.assertLess(elapsed, PROMPT_SECONDS, "the abort waited for the whole body")
        self.assertTrue(scope.aborted)
        self.assertFalse(self.control.requested)

    def test_a_stop_while_the_server_is_silent_ends_open_at_once(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)

        def held(seen: object, number: int) -> Reply:
            gate.wait(HOLD_SECONDS)
            return Reply(b"late")

        self.server.route("/held", held)
        self.addCleanup(later(0.2, partial(self.control.request, "cancel")).cancel)
        started = time.monotonic()

        with self.assertRaises(Cancelled):
            self.http.open(URL + "/held", self.control)

        self.assertLess(time.monotonic() - started, PROMPT_SECONDS, "the cancel waited for the server to answer")

    def test_open_on_a_task_already_asked_to_stop_never_looks_up_or_connects(self) -> None:
        self.control.request("stop")
        parent = ProcessControl()
        parent.request("cancel")
        for control in (self.control, Scope(parent)):
            with self.subTest(control=type(control).__name__), self.assertRaises(Cancelled):
                self.http.open(URL + "/v", control)

        self.assertEqual(self.lookups.calls, [])
        self.assertEqual(self.server.connections, [])

    def test_a_stop_between_redirect_hops_never_opens_the_next_hop(self) -> None:
        def stop_then_redirect(seen: object, number: int) -> Reply:
            self.control.request("stop")
            return Reply(status=302, headers={"Location": "/next"})

        self.server.route("/start", stop_then_redirect)
        self.server.route("/next", Reply(b"next"))

        with self.assertRaises(Cancelled):
            self.http.open(URL + "/start", self.control)

        self.assertEqual(self.server.count("/next"), 0)
        self.assertEqual(self.lookups.hosts, ["media.example"])


class ScopeTests(unittest.TestCase):
    def test_a_scope_follows_its_parent_and_runs_its_closers_on_a_parent_request(self) -> None:
        control = ProcessControl()
        scope = Scope(control)
        calls: list[str] = []
        scope.add_closer(lambda: calls.append("closed"))
        before = scope.requested

        control.request("stop")

        self.assertFalse(before)
        self.assertTrue(scope.requested)
        self.assertEqual(calls, ["closed"])

    def test_an_abort_stays_local_to_its_scope(self) -> None:
        control = ProcessControl()
        scope, sibling = Scope(control), Scope(control)

        scope.abort()

        self.assertTrue(scope.requested)
        self.assertFalse(control.requested)
        self.assertFalse(sibling.requested)

    def test_a_closed_scope_is_detached_from_its_parent(self) -> None:
        control = ProcessControl()
        scope = Scope(control)
        calls: list[str] = []
        scope.add_closer(lambda: calls.append("closed"))

        scope.close()
        control.request("stop")

        self.assertEqual(calls, [])
        self.assertFalse(scope.aborted)

    def test_closers_run_at_once_after_an_abort_and_removed_ones_never_run(self) -> None:
        def already_closed() -> None:
            raise OSError("socket already closed")

        scope = Scope(ProcessControl())
        calls: list[str] = []
        remove = scope.add_closer(lambda: calls.append("removed"))
        scope.add_closer(already_closed)
        scope.add_closer(lambda: calls.append("kept"))
        remove()

        scope.abort()
        scope.add_closer(lambda: calls.append("late"))

        self.assertEqual(calls, ["kept", "late"])

    def test_wait_ends_early_when_the_parent_is_asked_to_stop(self) -> None:
        control = ProcessControl()
        scope = Scope(control)
        self.addCleanup(later(0.1, partial(control.request, "stop")).cancel)
        started = time.monotonic()

        result = scope.wait(5)

        self.assertTrue(result)
        self.assertLess(time.monotonic() - started, PROMPT_SECONDS)
        self.assertFalse(Scope(ProcessControl()).wait(0.01))


class ProcessControlTests(unittest.TestCase):
    def test_a_closer_added_after_a_request_runs_at_once(self) -> None:
        control = ProcessControl()
        control.request("stop")
        calls: list[str] = []

        control.add_closer(lambda: calls.append("closed"))

        self.assertEqual(calls, ["closed"])
        self.assertTrue(control.wait(0))

    def test_a_cancel_overrides_an_earlier_stop_but_not_the_reverse(self) -> None:
        control = ProcessControl()

        control.request("stop")
        control.request("cancel")
        control.request("stop")

        self.assertEqual(control.reason, "cancel")


# -------------------------------------------------------------------------------------- retries
def failing_action(*errors: Exception, result: str = "done") -> tuple[Callable[[], str], list[int]]:
    """An action raising ``errors`` in order, then returning ``result``; the list counts the calls."""
    calls: list[int] = []

    def action() -> str:
        calls.append(len(calls) + 1)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return result
    return action, calls


def busy() -> HttpError:
    return HttpError("SERVER_BUSY", "media.example đang bận.", status=503, retryable=True)


class RetryTests(unittest.TestCase):
    def test_a_retryable_error_is_tried_again_and_reported(self) -> None:
        first, second = busy(), HttpError("NETWORK", "mất kết nối", retryable=True)
        action, calls = failing_action(first, second)
        retries: list[tuple[int, HttpError]] = []

        result = with_retries(action, ProcessControl(), attempts=3, base_delay=0.001,
                              on_retry=lambda number, error: retries.append((number, error)))

        self.assertEqual(result, "done")
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(retries, [(1, first), (2, second)])

    def test_the_last_error_is_raised_after_every_attempt(self) -> None:
        errors = [busy() for _ in range(4)]
        action, calls = failing_action(*errors)
        retries: list[int] = []

        with self.assertRaises(HttpError) as caught:
            with_retries(action, ProcessControl(), attempts=2, base_delay=0.001,
                         on_retry=lambda number, error: retries.append(number))

        self.assertIs(caught.exception, errors[2])
        self.assertEqual((calls, retries), ([1, 2, 3], [1, 2]))

    def test_errors_that_are_not_retryable_are_raised_at_once(self) -> None:
        for error in (HttpError("FORBIDDEN", "403", status=403), Cancelled(), ValueError("bug")):
            with self.subTest(error=type(error).__name__):
                action, calls = failing_action(error)
                retries: list[int] = []

                with self.assertRaises(type(error)):
                    with_retries(action, ProcessControl(), attempts=3, base_delay=0.001,
                                 on_retry=lambda number, failure: retries.append(number))

                self.assertEqual((calls, retries), ([1], []))

    def test_the_pause_doubles_up_to_the_maximum(self) -> None:
        control = QuietControl()
        action, _ = failing_action(*[busy() for _ in range(4)])

        with_retries(action, control, attempts=4, base_delay=1.0, max_delay=3.0)

        self.assertEqual(control.waits, [1.0, 2.0, 3.0, 3.0])

    def test_a_stop_during_the_pause_raises_cancelled_at_once(self) -> None:
        control = ProcessControl()
        action, calls = failing_action(*[busy() for _ in range(4)])
        self.addCleanup(later(0.1, partial(control.request, "stop")).cancel)
        started = time.monotonic()

        with self.assertRaises(Cancelled):
            with_retries(action, control, attempts=3, base_delay=5.0)

        self.assertLess(time.monotonic() - started, PROMPT_SECONDS)
        self.assertEqual(calls, [1])


class RetryOverHttpTests(ServerCase):
    def test_a_busy_server_is_retried_until_it_answers(self) -> None:
        def busy_twice(seen: object, number: int) -> Reply:
            return Reply(b"busy", 503, "text/plain") if number <= 2 else Reply(b"payload")

        self.server.route("/v", busy_twice)
        retries: list[str] = []

        body = with_retries(lambda: self.http.fetch(URL + "/v", self.control, limit=100)[0], self.control,
                            attempts=3, base_delay=0.001, on_retry=lambda number, error: retries.append(error.code))

        self.assertEqual(body, b"payload")
        self.assertEqual(retries, ["SERVER_BUSY", "SERVER_BUSY"])
        self.assertEqual(self.server.count("/v"), 3)


if __name__ == "__main__":
    unittest.main()
