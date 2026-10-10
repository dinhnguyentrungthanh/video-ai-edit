"""The session browser (download_account_browser) on real headless Edge, with self-made pages only.

Every page comes from a local HTTPS fixture server with a throwaway CA. The browser reaches it only
through its route handler and ``SessionHttp``, whose resolver, connector and TLS context are the test's
dependency injection. Nothing else can reach the fixture, so what the server, the resolver and the
connector record is exactly what went out. Listeners on 127.0.0.1 (TCP and UDP) and the black-hole
proxy show what tried to leave outside the handler.

The browser tests need Playwright and the installed Edge. Without them they are skipped and reported
as skipped, never as passed; nothing is installed or downloaded. Headless only: no window opens.
Made-up hosts (.example), cookies and passwords only, in temporary roots under the install's temp/.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow import download_account_browser as browser_module
from biliflow import download_account_edge as edge_module
from biliflow import download_account_runs as runs_module
from biliflow import recycle_bin
from biliflow.download_account_browser import (
    HARDENING_ARGS,
    MAX_HOPS,
    MAX_PAGES,
    MAX_WAIT_SECONDS,
    PROFILE_PREFERENCES,
    BlackHoleProxy,
    BrowserFailed,
    BrowserUnavailable,
    Refusal,
    SessionBrowser,
    edge_environment,
    hop_page,
    indexed_db_cause,
    write_preferences,
)
from biliflow.download_account_runs import run_with_session
from biliflow.download_account_config import AccountConfig, AdapterSpec, SourceAccount
from biliflow.download_account_http import SessionHttp, SessionNetwork
from biliflow.download_account_vault import SessionVault
from biliflow.download_account_winsec import PrivateFolderAcl
from biliflow.download_accounts import AccountManager, StaleLogin
from biliflow.download_http import Cancelled
from biliflow.download_runner import ProcessControl
from tests.source_fixtures import PUBLIC_ADDRESS, FixtureServer, Reply
from tests.test_download_account_http import Resolver, rebinding
from tests.test_download_accounts import Clock, FakeProtector, GoodAcl
from tests.tls_fixtures import HAVE_TLS, make_tls_files

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
WINDOWS = os.name == "nt"
HAVE_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
HAVE_BROWSER = HAVE_PLAYWRIGHT and EDGE.is_file()
NEED_BROWSER = "the installed Playwright, Edge and pycryptodomex are required (nothing is installed)"
TEMP_PARENT = recycle_bin.INSTALL_ROOT / "temp"
PORTAL, TICKETS, FILES, CDN = ("portal.alpha.example", "tickets.alpha.example", "files.alpha.example",
                               "cdn.alpha.example")
BETA_PORTAL = "portal.beta.example"
# cdn.alpha.example: a name mismatch; ads.example: the host of a source's page script (M7 exception A).
CERT_NAMES = (PORTAL, TICKETS, FILES, BETA_PORTAL, "evil.example", "ads.example")
ALPHA = SourceAccount("alpha", AdapterSpec("ticket-files", "ttl"), "Nguồn alpha", f"https://{PORTAL}/login",
                      {"portal": (PORTAL,), "tickets": (TICKETS,), "files": (FILES, CDN)})
BETA = SourceAccount("beta", AdapterSpec("ticket-files", "ttl"), "Nguồn beta", f"https://{BETA_PORTAL}/login",
                     {"portal": (BETA_PORTAL,), "tickets": (), "files": ()})
CONFIG = AccountConfig({"alpha": ALPHA, "beta": BETA})
CANARY_PW = "BF-CANARY-password-91c3"
CANARY_SID = "BF-CANARY-sid-5e07"
FIRST = "S-1-5-21-1-2-3-1001"
SECOND = "S-1-5-21-9-8-7-2002"
HTML = "text/html; charset=utf-8"
DATA: dict = {}


def FAKE_DRIVER():
    """A fake Playwright runtime has no driver process: stand in a recorded one (never ended in these tests)."""
    return mock.patch.object(browser_module.DriverProcess, "record", return_value=mock.Mock(name="driver"))


def setUpModule():
    if HAVE_TLS:
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        DATA["tls_dir"] = TemporaryDirectory(dir=TEMP_PARENT, prefix="session-browser-tls-")
        DATA["tls"] = make_tls_files(Path(DATA["tls_dir"].name), CERT_NAMES)


def tearDownModule():
    if "tls_dir" in DATA:
        DATA["tls_dir"].cleanup()


def page_reply(body: str, **headers: str) -> Reply:
    return Reply(body.encode("utf-8"), content_type=HTML, headers=headers)


def redirect(status: int, location: str, **headers: str) -> Reply:
    return Reply(b"", status, HTML, headers={"Location": location, **headers})


def cookie(name: str, value: str, domain: str, path: str = "/", expires: float = -1) -> dict:
    return {"name": name, "value": value, "domain": domain, "path": path, "expires": expires, "httpOnly": True,
            "secure": True, "sameSite": "Lax"}


def cookies_of(seen) -> dict[str, str]:
    header = seen.headers.get("cookie", "")
    return dict(part.strip().split("=", 1) for part in header.split(";") if "=" in part)


def edge_command_lines(marker: str) -> list[str]:
    """The command lines of the running msedge.exe processes that name ``marker`` (read only, through CIM)."""
    script = "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | ForEach-Object { $_.CommandLine }"
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=60, check=False)
    return [line for line in result.stdout.splitlines() if marker.lower() in line.lower()]


RTC_PROBE = """async ([udp, tcp]) => {
    const pc = new RTCPeerConnection({iceServers: [
        {urls: `stun:127.0.0.1:${udp}`},
        {urls: `turn:127.0.0.1:${udp}?transport=udp`, username: 'u', credential: 'c'},
        {urls: `turn:127.0.0.1:${tcp}?transport=tcp`, username: 'u', credential: 'c'}]});
    const kinds = [];
    pc.onicecandidate = e => { if (e.candidate) kinds.push(e.candidate.type || 'unknown'); };
    pc.createDataChannel('d');
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise(r => setTimeout(r, 3000));
    pc.close();
    return {api: typeof RTCPeerConnection, kinds};
}"""


class Listener:
    """A TCP listener on 127.0.0.1 that only counts connections (never answers)."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.count = 0
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                connection, _ = self.sock.accept()
            except OSError:
                return
            self.count += 1
            connection.close()

    def close(self):
        self.sock.close()


class UdpListener:
    """A UDP socket on 127.0.0.1 that counts the datagrams that reach it (a STUN, TURN or QUIC attempt)."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]

    def received(self) -> int:
        count = 0
        while True:
            try:
                self.sock.recvfrom(4096)
                count += 1
            except OSError:
                return count

    def close(self):
        self.sock.close()


class KeepRtcBrowser(SessionBrowser):
    """The session browser without the init script that removes the WebRTC API: shows that the launch
    policy and the black hole alone keep WebRTC off the network."""
    INIT_SCRIPTS = ()


class UnitTest(unittest.TestCase):
    """The parts that need no browser."""

    def test_the_hop_page_cannot_be_broken_out_of(self):
        body = hop_page("https://portal.alpha.example/a?x=</script><script>alert(1)</script>").decode()
        self.assertEqual(body.count("<script>"), 1)
        self.assertIn("\\u003c/script>", body)
        self.assertIn('content="no-referrer"', body)
        # The browser decides between going on and loading its own URL again (a target that differs only by
        # its fragment), on its own parsed URLs.
        self.assertIn("location.replace(t)", body)
        self.assertIn("history.replaceState(null, '', t); location.reload();", body)

    def test_a_state_compares_by_every_cookie_attribute_but_not_by_order(self):
        comparable = runs_module._comparable
        base = {"name": "sid", "value": "v", "domain": PORTAL, "path": "/", "expires": 1893456000.25,
                "httpOnly": False, "secure": True, "sameSite": "Lax"}
        other = {"name": "pref", "value": "p", "domain": ".alpha.example", "path": "/", "expires": -1,
                 "httpOnly": False, "secure": True, "sameSite": "None"}
        storage = {"origin": f"https://{PORTAL}", "localStorage": [{"name": "a", "value": "1"},
                                                                    {"name": "b", "value": "2"}]}
        state = {"cookies": [base, other], "origins": [storage]}
        reordered = {"origins": [{"localStorage": list(reversed(storage["localStorage"])), "origin": storage["origin"]}],
                     "cookies": [dict(reversed(list(other.items()))), dict(reversed(list(base.items())))]}
        self.assertEqual(comparable(state), comparable(reordered))  # the order of cookies, keys or items
        changes = {"httpOnly": True, "secure": False, "sameSite": "Strict", "expires": 1893456000.5,
                   "partitionKey": "https://alpha.example", "value": "w"}
        for key, value in changes.items():  # each one alone is a change to save (0.25 s apart: no rounding)
            changed = {"cookies": [{**base, key: value}, other], "origins": [storage]}
            self.assertNotEqual(comparable(state), comparable(changed), key)
        storage_changed = {"cookies": [base, other], "origins": [{**storage, "localStorage": [
            {"name": "a", "value": "1"}, {"name": "b", "value": "3"}]}]}
        self.assertNotEqual(comparable(state), comparable(storage_changed))

    def test_a_state_whose_indexeddb_cannot_be_read_keeps_the_new_cookies_and_only_the_storage_that_loaded(self):
        # On the first real source Playwright could not read the closed ticket page's IndexedDB after a ticket,
        # and a state that held that page's origin no longer loaded: the state is read again without IndexedDB,
        # every origin the run loaded keeps the IndexedDB it loaded, and an origin first seen in the run is left out.
        from playwright.sync_api import Error as PlaywrightError
        vault = SessionVault(TEMP_PARENT, protector=FakeProtector(), acl=GoodAcl(FIRST))  # never used here
        origin, other, new = f"https://{PORTAL}", f"https://{CDN}", f"https://{TICKETS}"
        databases = [{"name": "tok", "version": 1, "stores": []}]
        loaded = {"cookies": [cookie("sid", "old", PORTAL)],
                  "origins": [{"origin": origin, "localStorage": [{"name": "a", "value": "0"}], "indexedDB": databases},
                              {"origin": other, "localStorage": [{"name": "b", "value": "0"}], "indexedDB": databases}]}

        class Context:
            def __init__(self, failure: str):
                self.failure, self.calls = failure, []

            def set_storage_state(self, state):
                pass

            def storage_state(self, indexed_db: bool = False):
                self.calls.append(indexed_db)
                if indexed_db or "IndexedDB" not in self.failure:
                    raise PlaywrightError(self.failure)
                return {"cookies": [cookie("sid", "new", PORTAL)],
                        "origins": [{"origin": origin, "localStorage": [{"name": "a", "value": "1"}]},
                                    {"origin": new, "localStorage": [{"name": "t", "value": "1"}]}]}
        browser = SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl(), state=loaded)
        browser.context = Context("Unable to serialize IndexedDB: Database version is unset")
        self.assertEqual(browser._load_session(), "")
        state = browser.storage_state()
        self.assertEqual(browser.context.calls, [True, False])
        self.assertEqual([item["value"] for item in state["cookies"]], ["new"])
        self.assertEqual(state["origins"], [  # localStorage as read now; no localStorage left is none
            {"origin": origin, "localStorage": [{"name": "a", "value": "1"}], "indexedDB": databases},
            {"origin": other, "localStorage": [], "indexedDB": databases}])
        stats = browser.run_stats()
        self.assertEqual((stats["indexed_db_kept"], stats["save_cause"], stats["session_loaded"]),
                         (1, "VERSION_UNSET", 0))

        browser = SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl(), state=None)
        browser.context = Context("Unable to serialize IndexedDB: Database name is empty")  # the sign-in window
        self.assertEqual([item["origin"] for item in browser.storage_state()["origins"]], [origin, new])

        browser = SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl(), state=loaded)
        browser.context = Context("Target page, context or browser has been closed")  # not IndexedDB: no 2nd read
        with self.assertRaises(BrowserFailed) as caught:
            browser.storage_state()
        self.assertEqual((browser.context.calls, caught.exception.code, caught.exception.detail),
                         ([True], "SESSION_READ_FAILED", "CLOSED"))

    def test_the_indexeddb_cause_reads_only_the_browsers_own_words(self):
        from playwright.sync_api import Error as PlaywrightError
        cases = (("Unable to serialize IndexedDB: Database version is unset", "VERSION_UNSET"),
                 ("Error setting storage state:\nUnable to restore IndexedDB: Key already exists in the object store.",
                  "KEY_EXISTS"),
                 # Playwright's call log after the message names hosts: "key" or "internal" there is not the cause
                 ("Unable to restore IndexedDB: The operation failed\nCall log:\n  - navigating to "
                  "\"https://key.internal.example/\"", "OTHER"),
                 ("Target page, context or browser has been closed", "OTHER"))
        for text, cause in cases:
            self.assertEqual(indexed_db_cause(PlaywrightError(text)), cause, text)

    def test_a_saved_state_that_does_not_restore_loads_again_with_less_of_itself(self):
        # The state saved after the first real source's ticket could not be restored ("Unable to restore
        # IndexedDB"): it loads again without IndexedDB, then with its cookies alone, and only an IndexedDB
        # failure is tried again.
        from playwright.sync_api import Error as PlaywrightError
        vault = SessionVault(TEMP_PARENT, protector=FakeProtector(), acl=GoodAcl(FIRST))  # never used here
        databases = [{"name": "tok", "version": 1, "stores": []}]
        storage = {"origin": f"https://{PORTAL}", "localStorage": [{"name": "a", "value": "1"}]}
        cookies = [cookie("sid", "v", PORTAL)]
        denied = "Error setting storage state:\nUnable to restore IndexedDB: Access to the IndexedDB API is denied"
        closed = "Target page, context or browser has been closed"

        class Context:
            def __init__(self, *failures: str):
                self.failures, self.calls = list(failures), []

            def set_storage_state(self, state):
                self.calls.append(("set", [sorted(item) for item in state["origins"]]))
                if self.failures:
                    raise PlaywrightError(self.failures.pop(0))

            def clear_cookies(self):
                self.calls.append(("clear",))

            def add_cookies(self, added):
                self.calls.append(("add", [item["value"] for item in added]))

        def load(state, context):
            browser = SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl(), state=state)
            browser.context = context
            return browser, browser._load_session()

        whole = {"cookies": cookies, "origins": [{**storage, "indexedDB": databases}]}
        with_db, without_db = ["indexedDB", "localStorage", "origin"], ["localStorage", "origin"]
        cases = (  # the saved state, the failures in turn, the calls, the result, the stage, the cause
            (whole, (), [("set", [with_db])], "", 0, ""),
            (whole, (denied,), [("set", [with_db]), ("set", [without_db])], "", 1, "DENIED"),
            (whole, (denied, denied), [("set", [with_db]), ("set", [without_db]), ("clear",), ("add", ["v"])],
             "", 2, "DENIED"),
            (whole, (closed,), [("set", [with_db])], "CLOSED", 0, ""),  # not IndexedDB: nothing else is tried
            (whole, (denied, closed), [("set", [with_db]), ("set", [without_db])], "CLOSED", 0, "DENIED"),
            # no origin has IndexedDB: the same state is not tried twice
            ({"cookies": cookies, "origins": [storage]}, (denied,),
             [("set", [without_db]), ("clear",), ("add", ["v"])], "", 2, "DENIED"))
        for state, failures, calls, result, stage, cause in cases:
            with self.subTest(failures=failures, origins=state["origins"]):
                browser, failed = load(state, Context(*failures))
                self.assertEqual((browser.context.calls, failed, browser.session_loaded, browser.load_cause),
                                 (calls, result, stage, cause))
                if not failed:  # what loaded of the origins' storage is what a state read without IndexedDB keeps
                    self.assertEqual(browser._loaded_origins,
                                     [] if stage == 2 else [{key: value for key, value in item.items()
                                                             if stage == 0 or key != "indexedDB"}
                                                            for item in state["origins"]])

    def test_the_webrtc_preference_is_written_into_a_fresh_profile(self):
        self.assertEqual(PROFILE_PREFERENCES["webrtc"], {"ip_handling_policy": "disable_non_proxied_udp",
                                                         "multiple_routes_enabled": False,
                                                         "nonproxied_udp_enabled": False})
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=TEMP_PARENT, prefix="session-browser-prefs-") as folder:
            write_preferences(Path(folder))
            written = json.loads((Path(folder) / "Default" / "Preferences").read_text(encoding="utf-8"))
        self.assertEqual(written, PROFILE_PREFERENCES)

    def test_the_waits_must_be_positive_and_bounded(self):
        vault = SessionVault(TEMP_PARENT, protector=FakeProtector(), acl=GoodAcl(FIRST))  # never used here
        for name in ("launch_seconds", "page_seconds"):
            for value in (0, -1, float("nan"), MAX_WAIT_SECONDS + 1):
                with self.assertRaises(ValueError):
                    SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl(), **{name: value})
        SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl(), launch_seconds=MAX_WAIT_SECONDS,
                       page_seconds=0.5)

    def test_playwright_debug_output_keeps_the_browser_from_starting(self):
        vault = mock.Mock()  # nothing may be made before the check
        for extra in ({"DEBUG": "pw:protocol"}, {"DEBUG": "*"}, {"PWDEBUG": "1"}):
            with mock.patch.dict(os.environ, extra):
                browser = SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl())
                with self.assertRaises(BrowserUnavailable):
                    browser.open()
            self.assertIsNone(browser.proxy)
        self.assertEqual(vault.mock_calls, [])

    @unittest.skipUnless(HAVE_PLAYWRIGHT, "the installed Playwright is required (nothing is installed)")
    def test_a_launch_failure_keeps_no_playwright_text_and_no_profile(self):
        from playwright.sync_api import Error as PlaywrightError

        class Chromium:
            @staticmethod
            def launch_persistent_context(*args, **kwargs):
                raise PlaywrightError(f"launch failed: https://evil.example/x?ticket={CANARY_SID}")

        class Runtime:
            chromium = Chromium()

            def stop(self):
                pass
        starter = mock.Mock()
        starter.return_value.start.return_value = Runtime()
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=TEMP_PARENT, prefix="session-browser-launch-") as folder:
            vault = SessionVault(Path(folder), protector=FakeProtector(), acl=GoodAcl(FIRST))
            with mock.patch("playwright.sync_api.sync_playwright", starter), FAKE_DRIVER():
                with self.assertRaises(BrowserUnavailable) as caught:
                    with SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl()):
                        pass
            left = list(vault.browser_folder().iterdir())
        error = caught.exception
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertNotIn(CANARY_SID, f"{error} {error!r}")
        self.assertIn("Error", str(error))  # the launch failed (its class name), not the driver check before it
        self.assertNotIn("DriverUnknown", str(error))
        self.assertEqual(left, [])

    @unittest.skipUnless(HAVE_PLAYWRIGHT, "the installed Playwright is required (nothing is installed)")
    def test_a_runtime_whose_driver_is_unknown_is_not_started(self):
        """No driver of its own to end if it hangs (plan 9.13): Edge is never launched, the run fails closed."""
        runtime = mock.Mock()
        starter = mock.Mock()
        starter.return_value.start.return_value = runtime  # a runtime without the driver's handle
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=TEMP_PARENT, prefix="session-browser-driver-") as folder:
            vault = SessionVault(Path(folder), protector=FakeProtector(), acl=GoodAcl(FIRST))
            with mock.patch("playwright.sync_api.sync_playwright", starter):
                browser = SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl())
                with self.assertRaises(BrowserUnavailable) as caught:
                    with browser:
                        pass
            left = list(vault.browser_folder().iterdir())
        self.assertIn("DriverUnknown", str(caught.exception))
        runtime.chromium.launch_persistent_context.assert_not_called()
        runtime.stop.assert_called_once()  # the runtime still stops
        self.assertIsNone(browser.driver)
        self.assertFalse(browser.end_driver())  # nothing that could be ended
        self.assertEqual(left, [])

    def test_response_headers_keep_cookies_apart_and_drop_alt_svc_and_reporting(self):
        headers = SessionBrowser._headers((
            ("Set-Cookie", "a=1; Path=/"), ("Alt-Svc", 'h3=":443"'), ("NEL", "{}"), ("Report-To", "{}"),
            ("Reporting-Endpoints", 'r="https://evil.example/r"'), ("Set-Cookie", "b=2; Path=/"),
            ("Vary", "Accept"), ("Vary", "Cookie")))
        self.assertEqual(headers, {"set-cookie": "a=1; Path=/\nb=2; Path=/", "vary": "Accept, Cookie"})

    def test_the_http_client_covers_the_portal_and_nothing_outside_the_source(self):
        vault = SessionVault(TEMP_PARENT, protector=FakeProtector(), acl=GoodAcl(FIRST))  # never used here
        with self.assertRaises(ValueError):
            SessionBrowser(ALPHA, SessionHttp((TICKETS, FILES)), vault, ProcessControl())  # no portal
        with self.assertRaises(ValueError):
            SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts | {BETA_PORTAL}), vault, ProcessControl())
        # The file hosts may be left out (a hidden run never reaches the file servers, M3).
        SessionBrowser(ALPHA, SessionHttp((PORTAL, TICKETS)), vault, ProcessControl())
        SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl())

    def test_a_handler_that_fails_twice_still_aborts_the_request(self):
        class BrokenRequest:  # even the bookkeeping of the refusal fails
            url, resource_type, method, headers, post_data_buffer = f"https://{PORTAL}/x", "document", "GET", {}, None

            def is_navigation_request(self):
                raise RuntimeError("broken")

        class Route:
            request = BrokenRequest()
            aborted: list = []

            def abort(self, code):
                self.aborted.append(code)

            def fulfill(self, **kwargs):
                raise AssertionError("never answered")

            def continue_(self, **kwargs):
                raise AssertionError("never continued")
        vault = SessionVault(TEMP_PARENT, protector=FakeProtector(), acl=GoodAcl(FIRST))
        browser = SessionBrowser(ALPHA, SessionHttp(ALPHA.all_hosts), vault, ProcessControl())
        browser.http.send = mock.Mock(side_effect=RuntimeError("a bug"))  # no network: the bug comes first
        route = Route()
        browser._handle(route)
        self.assertEqual(route.aborted, ["blockedbyclient"])
        self.assertEqual(browser.refusals, [Refusal("HANDLER_ERROR", PORTAL, "document")])

    def test_the_launch_hardening(self):
        for flag in ("--disable-quic", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                     "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1", "--proxy-bypass-list=<-loopback>",
                     "--dns-prefetch-disable", "--disable-background-networking", "--no-pings"):
            self.assertIn(flag, HARDENING_ARGS)

    def test_edge_gets_only_the_windows_basics_of_the_environment(self):
        extra = {"BILIFLOW_TEST_SECRET": CANARY_SID, "PYTHONPATH": "x", "SystemRoot": r"C:\Windows"}
        with mock.patch.dict(os.environ, extra):
            env = edge_environment(r"E:\profile")
        names = {name.upper() for name in env}
        self.assertNotIn("BILIFLOW_TEST_SECRET", names)
        self.assertNotIn("PYTHONPATH", names)
        self.assertIn("SYSTEMROOT", names)
        self.assertEqual((env["TEMP"], env["TMP"]), (r"E:\profile", r"E:\profile"))
        self.assertNotIn(CANARY_SID, json.dumps(env))

    def test_the_black_hole_forwards_nothing_and_keeps_only_host_names(self):
        proxy = BlackHoleProxy()
        self.addCleanup(proxy.close)
        for line in (b"CONNECT portal.alpha.example:443 HTTP/1.1\r\n\r\n",
                     b"GET http://evil.example/path?token=" + CANARY_SID.encode() + b" HTTP/1.1\r\n\r\n"):
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=2) as client:
                client.sendall(line)
                client.settimeout(2)
                self.assertEqual(client.recv(100), b"")  # closed without an answer
        deadline = time.monotonic() + 2
        while len(proxy.hosts) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(proxy.hosts, ["portal.alpha.example", "evil.example"])
        self.assertNotIn(CANARY_SID, repr(proxy.hosts))


@unittest.skipUnless(HAVE_BROWSER and HAVE_TLS, NEED_BROWSER)
class BrowserCase(unittest.TestCase):
    def setUp(self):
        self._root = TemporaryDirectory(dir=TEMP_PARENT, prefix="session-browser-")
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name)
        self.server = FixtureServer(tls=DATA["tls"].server_context())
        self.addCleanup(self.server.close)
        self.resolver = Resolver()
        self.network = SessionNetwork(resolver=self.resolver, connector=self.server.connector,
                                      ssl_context=DATA["tls"].client_context())
        self.clock = Clock()
        self.manager = self.new_manager(self.root)
        self.control = ProcessControl()
        self.direct = Listener()
        self.addCleanup(self.direct.close)
        self.udp = UdpListener()
        self.addCleanup(self.udp.close)

    def new_manager(self, root: Path, user_sid: str = FIRST) -> AccountManager:
        manager = AccountManager(root, CONFIG, vault=SessionVault(root, protector=FakeProtector(),
                                                                  acl=GoodAcl(user_sid)), clock=self.clock)
        self.addCleanup(manager.close)
        return manager

    def browser(self, source: SourceAccount = ALPHA, *, state=None, network=None, manager=None,
                browser_class=SessionBrowser, **http_options) -> SessionBrowser:
        http = SessionHttp(source.all_hosts, network or self.network, **http_options)
        return browser_class(source, http, (manager or self.manager).vault, self.control, state=state,
                             page_seconds=10)

    def run_session(self, action, manager=None, **http_options):
        return run_with_session(manager or self.manager, "alpha", action, control=self.control,
                                network=self.network, http_options=http_options,
                                browser_options={"page_seconds": 10})

    def connect(self, manager: AccountManager, state: dict) -> None:
        manager.complete_login(manager.begin_login("alpha"), state)

    def hosts_seen(self) -> set[str]:
        return {item.host for item in self.server.requests}

    def assert_no_profile_left(self, manager=None):
        self.assertEqual(list((manager or self.manager).vault.browser_folder().iterdir()), [])

    def assert_no_secret_on_disk(self, *secrets: str) -> None:
        """No secret in plain text in any file of the test root: the database and its journal files, the vault
        (the fake protector's files hold no plain text either), a left profile, a log or a temporary file. The
        account database must be among them, so the scan can never pass on an empty or moved folder."""
        files = [path for path in self.root.rglob("*") if path.is_file()]
        self.assertIn(self.root / "state" / "downloads.sqlite3", files)
        for path in files:
            data = path.read_bytes()
            for secret in secrets:
                self.assertFalse(secret.encode() in data, f"{secret} in {path.relative_to(self.root).as_posix()}")

    @staticmethod
    def fetch_status(page, url: str, **init) -> object:
        return page.evaluate("async ([u, init]) => { try { return (await fetch(u, init)).status; }"
                             " catch (e) { return 'failed'; } }", [url, init])


class LaunchTest(BrowserCase):
    @unittest.skipUnless(WINDOWS, "the Windows process list")
    def test_edge_runs_headless_in_its_sandbox_with_the_hardening_flags(self):
        self.server.route("/page", page_reply("<p>page</p>"))
        with self.browser() as browser:
            browser.navigate(f"https://{PORTAL}/page")
            lines = edge_command_lines(str(browser._profile))
            proxy = browser.proxy.server
        self.assertGreaterEqual(len(lines), 3)  # the browser and its helper processes
        self.assertEqual([line for line in lines if "--no-sandbox" in line], [])
        main = [line for line in lines if "--type=" not in line]
        self.assertEqual(len(main), 1)
        for flag in (*HARDENING_ARGS, "--headless", f"--proxy-server={proxy}"):
            self.assertIn(flag, main[0])
        self.assertEqual(edge_command_lines(str(self.root)), [])  # all gone after close


class SignInAndReuseTest(BrowserCase):
    def serve_sign_in(self):
        self.server.route("/login", page_reply(
            f'<form method="post" action="/session"><input name="pw" value="{CANARY_PW}"><button>Vào</button></form>'))
        self.server.route("/session", redirect(302, "/home", **{
            "Set-Cookie": f"sid={CANARY_SID}; Path=/; Secure; HttpOnly; SameSite=Lax"}))
        self.server.route("/home", page_reply("<p>home</p><script>localStorage.setItem('tok', 't1')</script>",
                                              **{"Set-Cookie": "pref=p1; Domain=alpha.example; Path=/; Secure"}))
        self.server.route("/account/set", page_reply("<p>acct</p>", **{"Set-Cookie": "acct=a1; Path=/account; Secure"}))
        for path in ("/account/info", "/other"):
            self.server.route(path, page_reply("<p>ok</p>"))
        self.server.route("/t", page_reply("<p>tickets</p>"))

    def test_fake_sign_in_redirect_cookies_and_reuse_in_a_second_context(self):
        self.serve_sign_in()
        attempt = self.manager.begin_login("alpha")
        with self.browser() as browser:
            page = browser.page()
            page.goto(f"https://{PORTAL}/login")
            page.click("button")
            page.wait_for_url(f"https://{PORTAL}/home")
            page.wait_for_function("localStorage.getItem('tok') === 't1'")
            browser.navigate(f"https://{PORTAL}/account/set")
            state = browser.storage_state()
            refusals, proxy_hosts = list(browser.refusals), list(browser.proxy.hosts)
        self.assert_no_profile_left()
        self.assertEqual(refusals, [])
        self.assertEqual(self.manager.complete_login(attempt, state)["state"], "CONNECTED")
        posted = self.server.seen("/session")
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0].body, f"pw={CANARY_PW}".encode())
        self.assertNotIn("cookie", posted[0].headers)  # nothing was set before the sign-in
        self.assertEqual(cookies_of(self.server.seen("/home")[0]), {"sid": CANARY_SID})  # the hop carried it

        def second_context(browser):
            browser.navigate(f"https://{PORTAL}/account/info")
            token = browser.page().evaluate("localStorage.getItem('tok')")
            browser.navigate(f"https://{PORTAL}/other")
            browser.navigate(f"https://{TICKETS}/t")
            return token
        run = self.run_session(second_context)
        self.assertEqual((run.value, run.saved, run.profile_left), ("t1", None, False))
        self.assert_no_profile_left()
        self.assertEqual(cookies_of(self.server.seen("/account/info")[0]),
                         {"sid": CANARY_SID, "pref": "p1", "acct": "a1"})
        self.assertEqual(cookies_of(self.server.seen("/other")[0]), {"sid": CANARY_SID, "pref": "p1"})  # path
        self.assertEqual(cookies_of(self.server.seen("/t")[0]), {"pref": "p1"})  # host-only sid stays on the portal
        self.assertEqual(self.hosts_seen(), {PORTAL, TICKETS})
        self.assertEqual(set(self.resolver.calls), {PORTAL, TICKETS})
        self.assertEqual({address for address, _ in self.server.connections}, {PUBLIC_ADDRESS})
        # No secret anywhere it could leak: the refusals, the black hole, the status, the saved state (cookies
        # and storage only), and no file of the root (the database included, no profile left on the disk).
        saved = json.dumps(self.manager.session_for("alpha").state)
        self.assertNotIn(CANARY_PW, saved)
        for place in (repr(refusals), repr(proxy_hosts), json.dumps(self.manager.statuses())):
            self.assertNotIn(CANARY_PW, place)
            self.assertNotIn(CANARY_SID, place)
        self.assert_no_secret_on_disk(CANARY_PW, CANARY_SID)

    def test_rotated_cookies_are_saved_under_the_same_lease(self):
        self.connect(self.manager, {"cookies": [cookie("sid", "old", PORTAL)], "origins": []})
        before = self.manager.status("alpha")
        self.server.route("/rotate", page_reply("<p>r</p>", **{"Set-Cookie": "sid=new; Path=/; Secure; HttpOnly"}))
        run = self.run_session(lambda browser: browser.navigate(f"https://{PORTAL}/rotate").status)
        self.assertEqual((run.value, run.saved), (200, True))
        self.assertEqual(cookies_of(self.server.seen("/rotate")[0]), {"sid": "old"})
        lease = self.manager.session_for("alpha")
        self.assertEqual((lease.generation, [c["value"] for c in lease.state["cookies"]]), (1, ["new"]))
        self.assertEqual(self.manager.status("alpha")["authenticated_at"], before["authenticated_at"])

    def test_a_loaded_session_holds_only_its_own_unexpired_cookies(self):
        self.server.route("/plain", page_reply("<p>p</p>"))
        state = {"cookies": [cookie("sid", "s1", PORTAL), cookie("acct", "a1", PORTAL, "/account"),
                             cookie("old", "o1", PORTAL, expires=1.0), cookie("bsid", "b1", BETA_PORTAL)],
                 "origins": [{"origin": f"https://{BETA_PORTAL}", "localStorage": [{"name": "k", "value": "v"}]}]}
        with self.browser(state=state) as browser:
            browser.navigate(f"https://{PORTAL}/plain")
            names = sorted(item["name"] for item in browser.context.cookies())
            origins = [item["origin"] for item in browser.context.storage_state()["origins"]]
        self.assertNotIn("bsid", names)  # the other source's cookie and storage never enter this context
        self.assertNotIn(f"https://{BETA_PORTAL}", origins)
        self.assertEqual(cookies_of(self.server.seen("/plain")[0]), {"sid": "s1"})  # no expired, no other path

    def test_a_saved_state_whose_indexeddb_does_not_restore_still_loads_its_cookies_and_local_storage(self):
        # Playwright refuses a whole state when one origin's IndexedDB does not restore (here a record twice under
        # one key; on the first real source the state saved after a ticket): it loads again without IndexedDB.
        self.server.route("/plain", page_reply("<p>p</p>"))
        store = {"name": "s", "autoIncrement": False, "keyPath": "id", "indexes": [],
                 "records": [{"value": {"id": 1}}, {"value": {"id": 1}}]}
        state = {"cookies": [cookie("sid", "s1", PORTAL)],
                 "origins": [{"origin": f"https://{PORTAL}", "localStorage": [{"name": "tok", "value": "t1"}],
                              "indexedDB": [{"name": "db", "version": 1, "stores": [store]}]}]}
        with self.browser(state=state) as browser:
            browser.navigate(f"https://{PORTAL}/plain")
            token = browser.page().evaluate("localStorage.getItem('tok')")
            stats = browser.run_stats()
        self.assertEqual((token, stats["session_loaded"], stats["load_cause"]), ("t1", 1, "KEY_EXISTS"))
        self.assertEqual(cookies_of(self.server.seen("/plain")[0]), {"sid": "s1"})

    def test_the_browser_applies_its_own_cookie_rules(self):
        self.server.route("/page", page_reply("<p>page</p>"))
        for name, domain in (("beta", "beta.example"), ("evil", "evil.example"), ("parent", "alpha.example")):
            self.server.route(f"/set-{name}", Reply(b"ok", content_type="text/plain", headers={
                "Set-Cookie": f"{name}=1; Domain={domain}; Path=/; Secure"}))
        for path in ("/api/omit", "/api/same"):
            self.server.route(path, Reply(b"ok", content_type="text/plain"))
        with self.browser(state={"cookies": [cookie("sid", "s1", PORTAL)], "origins": []}) as browser:
            browser.navigate(f"https://{PORTAL}/page")
            page = browser.page()
            for name in ("beta", "evil", "parent"):
                self.assertEqual(self.fetch_status(page, f"/set-{name}"), 200)
            self.assertEqual(self.fetch_status(page, "/api/omit", credentials="omit"), 200)
            self.assertEqual(self.fetch_status(page, "/api/same"), 200)
            names = sorted(item["name"] for item in browser.context.cookies())
        self.assertEqual(names, ["parent", "sid"])  # a Domain outside the answering host is dropped
        self.assertNotIn("cookie", self.server.seen("/api/omit")[0].headers)
        self.assertEqual(cookies_of(self.server.seen("/api/same")[0]), {"sid": "s1", "parent": "1"})


class RedirectTest(BrowserCase):
    def test_a_redirect_inside_the_source_is_a_new_checked_request(self):
        self.server.route("/go", redirect(302, f"https://{TICKETS}/landing?from=portal",
                                          **{"Set-Cookie": "hop=1; Path=/; Secure"}))
        self.server.route("/landing", page_reply("<p>landing</p>"))
        self.server.route("/check", page_reply("<p>check</p>"))
        with self.browser() as browser:
            page = browser.page()
            page.goto(f"https://{PORTAL}/go")
            page.wait_for_url(f"https://{TICKETS}/landing?from=portal")
            page.goto(f"https://{PORTAL}/check")
            refusals = list(browser.refusals)
        self.assertEqual(refusals, [])
        landing = self.server.seen("/landing")[0]
        self.assertEqual((landing.host, landing.query), (TICKETS, {"from": ["portal"]}))
        self.assertNotIn("cookie", landing.headers)  # the host-only cookie of the portal stays there
        self.assertNotIn("referer", landing.headers)  # the hop page sends no Referer
        self.assertEqual(cookies_of(self.server.seen("/check")[0]), {"hop": "1"})  # set by the redirect itself
        self.assertEqual(self.resolver.calls.count(TICKETS), 1)

    def test_navigate_waits_for_the_final_document_after_the_hops(self):
        self.server.route("/go", redirect(302, f"https://{TICKETS}/mid"))
        self.server.route("/mid", redirect(303, f"https://{PORTAL}/end?x=1#part"))
        self.server.route("/end", page_reply("<p id=end>end</p>"))
        self.server.route("/same", lambda seen, number: redirect(302, "/same") if number == 1
                          else page_reply("<p id=same>same</p>"))  # the hop page has the final URL too
        with self.browser() as browser:
            end = browser.navigate(f"https://{PORTAL}/go")
            text = browser.page().inner_text("#end")
            same = browser.navigate(f"https://{PORTAL}/same")
            same_found = browser.page().locator("#same").count()
            missing = browser.navigate(f"https://{PORTAL}/missing")
            refusals = list(browser.refusals)
        self.assertEqual((end.url, end.status, text), (f"https://{PORTAL}/end?x=1#part", 200, "end"))
        self.assertEqual((same.url, same.status, same_found), (f"https://{PORTAL}/same", 200, 1))
        self.assertEqual(missing.status, 404)  # an HTTP error is an answer, not a failure
        self.assertEqual(refusals, [])
        self.assertEqual(self.server.count("/same"), 2)

    def test_navigate_reports_the_document_it_waited_for_when_the_page_moves_on(self):
        self.server.route("/moving", page_reply("<script>location.replace('/after')</script>"))
        self.server.route("/after", Reply(b"<p>after</p>", 202, HTML))
        with self.browser() as browser:
            landed = browser.navigate(f"https://{PORTAL}/moving")
            browser.page().wait_for_url(f"https://{PORTAL}/after")
        self.assertEqual((landed.url, landed.status), (f"https://{PORTAL}/moving", 200))

    def test_a_redirect_to_its_own_url_with_a_fragment_loads_it_again_with_a_get(self):
        self.server.route("/frag", lambda seen, number: redirect(
            302, "/frag#part", **{"Set-Cookie": "seen=1; Path=/; Secure"}) if number == 1 else page_reply("<p>frag</p>"))
        self.server.route("/form", page_reply(
            f'<form method="post" action="/done"><input name="pw" value="{CANARY_PW}"><button>Gửi</button></form>'))
        self.server.route("/done", lambda seen, number: redirect(303, "/done#ok") if number == 1
                          else page_reply("<p id=done>done</p>"))
        with self.browser() as browser:
            frag = browser.navigate(f"https://{PORTAL}/frag")
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/form")
            page.click("button")
            page.wait_for_selector("#done")
            done_url = page.url
            refusals = list(browser.refusals)
        self.assertEqual((frag.url, frag.status), (f"https://{PORTAL}/frag#part", 200))
        self.assertEqual(cookies_of(self.server.seen("/frag")[1]), {"seen": "1"})
        self.assertEqual(done_url, f"https://{PORTAL}/done#ok")
        posted, again = self.server.seen("/done")
        self.assertEqual((posted.method, posted.body), ("POST", f"pw={CANARY_PW}".encode()))
        self.assertEqual((again.method, again.body), ("GET", b""))  # the form is not sent again
        self.assertEqual(refusals, [])

    def test_a_redirect_loop_to_its_own_url_with_a_fragment_ends_after_max_hops(self):
        self.server.route("/x", redirect(302, "/x#a"))
        with self.browser() as browser:
            with self.assertRaises(BrowserFailed) as caught:
                browser.navigate(f"https://{PORTAL}/x")
        self.assertEqual(caught.exception.code, "TOO_MANY_REDIRECTS")
        self.assertEqual(self.server.count("/x"), MAX_HOPS + 1)

    def test_navigating_to_the_shown_page_with_a_fragment_only_scrolls(self):
        self.server.route("/doc", page_reply("<p id=top>top</p><p id=part>part</p>"))
        self.server.route("/gone", Reply(b"<p>gone</p>", 410, HTML))
        with self.browser() as browser:
            browser.navigate(f"https://{PORTAL}/doc")
            started = time.monotonic()
            moved = browser.navigate(f"https://{PORTAL}/doc#part")
            again = browser.navigate(f"https://{PORTAL}/doc#part")
            elapsed = time.monotonic() - started
            gone = browser.navigate(f"https://{PORTAL}/gone")
            gone_part = browser.navigate(f"https://{PORTAL}/gone#x")
        self.assertEqual((moved.url, moved.status), (f"https://{PORTAL}/doc#part", 200))
        self.assertEqual((again.url, again.status), (f"https://{PORTAL}/doc#part", 200))
        self.assertEqual((gone.status, gone_part.status), (410, 410))  # the shown document's own status
        self.assertLess(elapsed, 3.0)  # not a wait for a document that never comes
        self.assertEqual((self.server.count("/doc"), self.server.count("/gone")), (1, 1))

    def test_a_location_with_non_ascii_characters_stays_inside_the_source(self):
        self.server.route("/vn", redirect(302, "/tập-1?q=phim".encode("utf-8").decode("latin-1")))
        self.server.route("/t%E1%BA%ADp-1", page_reply("<p>tap</p>"))
        with self.browser() as browser:
            landed = browser.navigate(f"https://{PORTAL}/vn")
            refusals = list(browser.refusals)
        self.assertEqual((landed.url, landed.status), (f"https://{PORTAL}/t%E1%BA%ADp-1?q=phim", 200))
        self.assertEqual(refusals, [])
        self.assertEqual(self.hosts_seen(), {PORTAL})

    def test_odd_location_values_are_resolved_inside_the_source_or_refused(self):
        leak = {"Set-Cookie": "leak=1; Path=/; Secure"}  # a refused redirect's cookie is never set
        refused = {"/proto": ("//evil.example/x", "HOST_NOT_ALLOWED"), "/js": ("javascript:alert(1)", "BAD_SCHEME"),
                   "/data": ("data:text/html,<p>x</p>", "BAD_SCHEME"),
                   "/userinfo": (f"https://{PORTAL}\\@evil.example/", "USERINFO")}
        for path, (location, _) in refused.items():
            self.server.route(path, redirect(302, location, **leak))
        self.server.route("/dir/rel", redirect(302, "next?a=1"))
        self.server.route("/dir/next", page_reply("<p>next</p>"))
        with self.browser() as browser:
            codes = {}
            for path in refused:
                with self.assertRaises(BrowserFailed) as caught:
                    browser.navigate(f"https://{PORTAL}{path}", browser.context.new_page())
                codes[path] = caught.exception.code
            relative = browser.navigate(f"https://{PORTAL}/dir/rel")
            names = [item["name"] for item in browser.context.cookies()]
        self.assertEqual(codes, {path: code for path, (_, code) in refused.items()})
        self.assertEqual((relative.url, relative.status), (f"https://{PORTAL}/dir/next?a=1", 200))
        self.assertNotIn("leak", names)
        self.assertNotIn("evil.example", self.resolver.calls)

    def test_a_redirect_loop_ends_after_max_hops(self):
        self.server.route("/loop", redirect(302, "/loop"))
        with self.browser() as browser:
            with self.assertRaises(BrowserFailed) as caught:
                browser.navigate(f"https://{PORTAL}/loop")
            refusals = [(item.code, item.host) for item in browser.refusals]
        self.assertEqual((caught.exception.code, caught.exception.host), ("TOO_MANY_REDIRECTS", PORTAL))
        self.assertEqual(refusals, [("TOO_MANY_REDIRECTS", PORTAL)])
        self.assertEqual(self.server.count("/loop"), MAX_HOPS + 1)

    def test_a_failed_navigation_names_the_host_only(self):
        self.server.route("/go", redirect(302, f"https://evil.example/t?ticket={CANARY_SID}"))
        cases = {f"https://evil.example/x?ticket={CANARY_SID}": ("HOST_NOT_ALLOWED", "evil.example"),
                 f"https://{PORTAL}/go?ticket={CANARY_SID}": ("HOST_NOT_ALLOWED", PORTAL),
                 f"http://{PORTAL}/{CANARY_SID}": ("NOT_HTTPS", PORTAL)}
        with self.browser() as browser:
            for url, expected in cases.items():
                with self.assertRaises(BrowserFailed) as caught:
                    browser.navigate(url)
                error = caught.exception
                self.assertEqual((error.code, error.host), expected)
                for text in (str(error), error.message, repr(error), str(error.__context__ or "")):
                    self.assertNotIn(CANARY_SID, text)
            refusals = repr(browser.refusals)
        self.assertNotIn(CANARY_SID, refusals)
        self.assertNotIn("evil.example", self.resolver.calls)

    def test_redirects_out_of_the_source_down_to_http_or_resending_a_body_are_refused(self):
        routes = {"/off": "https://evil.example/x", "/beta": f"https://{BETA_PORTAL}/x",
                  "/down": f"http://{PORTAL}/x", "/api/r": "/api/target"}
        for path, location in routes.items():
            self.server.route(path, redirect(302, location))
        self.server.route("/post307", redirect(307, "/elsewhere"))
        self.server.route("/form", page_reply(
            f'<form method="post" action="/post307"><input name="pw" value="{CANARY_PW}"><button>Gửi</button></form>'))
        self.server.route("/page", page_reply("<p>page</p>"))
        with self.browser() as browser:
            for path in ("/off", "/beta", "/down"):
                with self.assertRaises(BrowserFailed):
                    browser.navigate(f"https://{PORTAL}{path}", browser.context.new_page())
            page = browser.context.new_page()
            browser.navigate(f"https://{PORTAL}/page", page)
            self.assertEqual(self.fetch_status(page, "/api/r"), "failed")
            browser.navigate(f"https://{PORTAL}/form", page)
            page.click("button")
            page.wait_for_timeout(500)
            refusals = {(item.code, item.host) for item in browser.refusals}
        self.assertEqual(refusals, {("HOST_NOT_ALLOWED", "evil.example"), ("HOST_NOT_ALLOWED", BETA_PORTAL),
                                    ("NOT_HTTPS", PORTAL), ("REDIRECT_REFUSED", PORTAL)})
        self.assertEqual(self.hosts_seen(), {PORTAL})
        self.assertNotIn("evil.example", self.resolver.calls)
        self.assertNotIn(BETA_PORTAL, self.resolver.calls)
        for never in ("/x", "/api/target", "/elsewhere"):
            self.assertEqual(self.server.count(never), 0)
        self.assertEqual(len(self.server.seen("/post307")), 1)  # the body went out once, to its own URL

    def test_a_fulfilled_redirect_would_escape_the_interception_so_none_is_used(self):
        """Why 3xx answers become hop pages: Edge follows a redirect fulfilled by a route outside every
        route (seen in the M2a spike). Shown here with page routes that fulfill a 302 themselves; the escaped
        requests end at the black hole, never at a server or the loopback listener, because of the launch
        hardening."""
        self.server.route("/target", page_reply("<p>target</p>"))
        escapes = {"/escape": f"https://{TICKETS}/target", "/escape-local": f"http://127.0.0.1:{self.direct.port}/x"}

        def redirect_to(location):  # one parameter: Playwright passes (route, request) to a two-parameter handler
            return lambda route: route.fulfill(status=302, headers={"Location": location}, body="")
        with self.browser() as browser:
            for path, location in escapes.items():
                page = browser.context.new_page()
                followed = []
                page.on("request", lambda request, followed=followed: followed.append(request.url))
                page.route(f"**{path}", redirect_to(location))
                with self.assertRaises(Exception):
                    page.goto(f"https://{PORTAL}{path}")
                self.assertEqual(followed, [f"https://{PORTAL}{path}", location])  # Edge followed it itself
            handled = list(browser.refusals)
            proxy_hosts = list(browser.proxy.hosts)
        self.assertEqual(self.server.count("/target"), 0)  # not through the handler, and not around it
        self.assertEqual(handled, [])
        self.assertIn(TICKETS, proxy_hosts)  # Edge tried the network itself and met the black hole
        self.assertIn("127.0.0.1", proxy_hosts)
        self.assertEqual(self.direct.count, 0)
        self.assertNotIn(TICKETS, self.resolver.calls)


class BlockTest(BrowserCase):
    def test_hosts_outside_the_source_are_never_looked_up_or_reached(self):
        self.server.route("/page", page_reply(
            '<img src="https://evil.example/i.png"><script src="https://cdn.evil.example/s.js"></script>'
            f'<link rel="stylesheet" href="https://evil.example/c.css"><iframe src="https://{BETA_PORTAL}/f"></iframe>'
            f"<script>fetch('https://{BETA_PORTAL}/api', {{credentials: 'include'}}).catch(() => {{}});"
            "window.open('https://evil.example/pop');</script>"))
        with self.browser(state={"cookies": [cookie("sid", CANARY_SID, PORTAL)], "origins": []}) as browser:
            browser.navigate(f"https://{PORTAL}/page")
            browser.page().wait_for_timeout(1500)
            refused = {(item.code, item.host) for item in browser.refusals}
        self.assertTrue({("HOST_NOT_ALLOWED", "evil.example"), ("HOST_NOT_ALLOWED", "cdn.evil.example"),
                         ("HOST_NOT_ALLOWED", BETA_PORTAL)} <= refused)
        self.assertEqual({code for code, _ in refused}, {"HOST_NOT_ALLOWED"})
        self.assertEqual(self.resolver.calls, [PORTAL])
        self.assertEqual(self.hosts_seen(), {PORTAL})
        self.assertEqual(self.direct.count, 0)

    def test_an_internal_address_and_dns_rebinding_are_refused(self):
        self.resolver.answers.update({FILES: ["10.0.0.5"],
                                      TICKETS: rebinding([PUBLIC_ADDRESS], ["127.0.0.1"])})
        self.server.route("/page", page_reply("<p>page</p>"))
        self.server.route("/t1", page_reply("<p>t1</p>"))
        with self.browser() as browser:
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/page")
            self.assertEqual(self.fetch_status(page, f"https://{FILES}/f"), "failed")
            browser.navigate(f"https://{TICKETS}/t1")  # the first lookup is public
            self.assertEqual(self.fetch_status(page, f"https://{TICKETS}/t2"), "failed")  # then it rebinds
            refused = [(item.code, item.host) for item in browser.refusals]
        self.assertEqual(refused, [("PRIVATE_ADDRESS", FILES), ("PRIVATE_ADDRESS", TICKETS)])
        self.assertEqual({address for address, _ in self.server.connections}, {PUBLIC_ADDRESS})
        self.assertEqual(self.server.count("/t2"), 0)

    def test_tls_errors_are_refused(self):
        self.server.route("/page", page_reply("<p>page</p>"))
        untrusted = SessionNetwork(resolver=self.resolver, connector=self.server.connector,
                                   ssl_context=ssl.create_default_context())
        with self.browser(network=untrusted) as browser:
            with self.assertRaises(BrowserFailed) as caught:
                browser.navigate(f"https://{PORTAL}/page")
            refused = [(item.code, item.host) for item in browser.refusals]
        self.assertEqual(caught.exception.code, "TLS_ERROR")
        self.assertEqual(refused, [("TLS_ERROR", PORTAL)])
        self.assertEqual(self.server.requests, [])
        with self.browser() as browser:  # a certificate that does not name cdn.alpha.example
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/page")
            self.assertEqual(self.fetch_status(page, f"https://{CDN}/lib.js"), "failed")
            refused = [(item.code, item.host) for item in browser.refusals]
        self.assertEqual(refused, [("TLS_ERROR", CDN)])
        self.assertNotIn(CDN, self.hosts_seen())

    def test_too_large_and_too_slow_answers_are_refused_and_the_page_goes_on(self):
        self.server.route("/page", page_reply("<p>page</p>"))
        self.server.route("/big", Reply(b"x" * 300_000, content_type="text/plain"))
        self.server.route("/slow", Reply(b"x" * 400, content_type="text/plain", chunk=1, delay=0.05))
        self.server.route("/ok", Reply(b"fine", content_type="text/plain"))
        with self.browser(max_body=100_000, request_seconds=1.5) as browser:
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/page")
            started = time.monotonic()
            results = [self.fetch_status(page, path) for path in ("/big", "/slow", "/ok")]
            elapsed = time.monotonic() - started
            refused = [(item.code, item.host) for item in browser.refusals]
        self.assertEqual(results, ["failed", "failed", 200])
        self.assertEqual(refused, [("TOO_LARGE_RESPONSE", PORTAL), ("TIMEOUT", PORTAL)])
        self.assertLess(elapsed, 6.0)

    def test_a_navigation_stops_answering_once_its_time_is_up(self):
        """Handlers run one at a time: without the deadline inside the handler, slow requests queued behind
        each other would hold ``navigate`` for the sum of their own limits (here about 9 s)."""
        count = 6
        self.server.route("/page", page_reply("<p>page</p>" + "".join(
            f'<script src="/s{i}.js"></script>' for i in range(count))))
        for i in range(count):  # 1.5 s each: the last of its 4 bytes comes after three pauses
            self.server.route(f"/s{i}.js", Reply(b"/**/", content_type="text/javascript", chunk=1, delay=0.5))
        http = SessionHttp(ALPHA.all_hosts, self.network)
        with SessionBrowser(ALPHA, http, self.manager.vault, self.control, page_seconds=2) as browser:
            started = time.monotonic()
            with self.assertRaises(BrowserFailed) as caught:  # its DOM was not ready in time
                browser.navigate(f"https://{PORTAL}/page")
            elapsed = time.monotonic() - started
            refused = [(item.code, item.kind) for item in browser.refusals]
            browser.page().evaluate("1")  # the page is still usable
        self.assertEqual((caught.exception.code, caught.exception.host), ("NAVIGATION_TIMEOUT", PORTAL))
        self.assertIn(("TIMEOUT", "script"), refused)
        self.assertLess(elapsed, 5.0)  # at most one script past the time
        self.assertLess(sum(self.server.count(f"/s{i}.js") for i in range(count)), count)

    def test_an_error_inside_the_handler_refuses_the_request(self):
        self.server.route("/page", page_reply("<p>page</p>"))
        with self.browser() as browser:
            browser.http.send = mock.Mock(side_effect=RuntimeError("a bug"))
            with self.assertRaises(BrowserFailed) as caught:
                browser.navigate(f"https://{PORTAL}/page")
            refused = [(item.code, item.host) for item in browser.refusals]
        self.assertEqual(caught.exception.code, "HANDLER_ERROR")
        self.assertEqual(refused, [("HANDLER_ERROR", PORTAL)])
        self.assertEqual((self.server.requests, self.direct.count), ([], 0))

    def test_uploads_are_refused_and_other_bodies_go_out_whole(self):
        """Playwright leaves a file's bytes out of the body it hands the handler (M2a test), so a request with
        a file part is refused rather than sent cut short."""
        self.server.route("/form", page_reply(
            '<form id=up method="post" action="/up" enctype="multipart/form-data"><input name="a" value="1">'
            '<input type="file" name="f"><button>Up</button></form>'
            '<form id=plain method="post" action="/plain" enctype="multipart/form-data"><input name="b" value="2">'
            '<input type="file" name="none"><button>Plain</button></form>'))  # a file input left empty
        self.server.route("/up", page_reply("<p>up</p>"))
        self.server.route("/plain", page_reply("<p id=plain>plain</p>"))
        for path in ("/api/form", "/api/blob"):
            self.server.route(path, Reply(b"ok", content_type="text/plain"))
        upload = self.root / "upload.bin"
        upload.write_bytes(b"FILE-CONTENT-" * 100)
        with self.browser() as browser:
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/form")
            fetched = page.evaluate("""async () => {
                const data = new FormData();
                data.append('f', new Blob(['abc']), 'a.bin');
                const send = async (u, body) => {
                    try { return (await fetch(u, {method: 'POST', body})).status; } catch (e) { return 'failed'; } };
                return [await send('/api/form', data), await send('/api/blob', new Blob(['raw-blob-body']))];
            }""")
            page.set_input_files("input[name=f]", str(upload))
            page.click("#up button")
            page.wait_for_timeout(1000)
            browser.navigate(f"https://{PORTAL}/form")
            page.click("#plain button")
            page.wait_for_selector("#plain")
            refused = [(item.code, item.host, item.kind) for item in browser.refusals]
        self.assertEqual(fetched[0], "failed")
        self.assertEqual(refused, [("UPLOAD_REFUSED", PORTAL, "fetch"), ("UPLOAD_REFUSED", PORTAL, "document")])
        self.assertEqual((self.server.count("/up"), self.server.count("/api/form")), (0, 0))
        plain = self.server.seen("/plain")[0].body
        self.assertIn(b'name="b"\r\n\r\n2\r\n', plain)
        self.assertIn(b'name="none"; filename=""', plain)  # no file chosen: nothing was cut, so it goes
        self.assertEqual((fetched[1], [item.body for item in self.server.seen("/api/blob")]),
                         (200, [b"raw-blob-body"]))  # a plain Blob body is handed over whole


class OutsideInterceptionTest(BrowserCase):
    def test_service_workers_websockets_and_webrtc_never_reach_the_network(self):
        self.server.route("/page", page_reply(
            "<iframe id=f></iframe><script>window.r = [];"
            "navigator.serviceWorker.register('/sw.js').then(() => r.push('sw resolved'), e => r.push('sw ' + e.name));"
            f"for (const u of ['wss://{PORTAL}/socket', 'ws://127.0.0.1:{self.direct.port}/x']) {{"
            "  try { const w = new WebSocket(u); w.onerror = () => r.push('ws error'); } catch (e) { r.push('ws ' + e.name); } }"
            "</script>"))
        self.server.route("/sw.js", Reply(b"self.addEventListener('fetch', () => {})", content_type="text/javascript"))
        with self.browser() as browser:
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/page")
            page.wait_for_timeout(1500)
            rtc = page.evaluate("""() => {
                const popup = window.open('about:blank');
                const frame = document.getElementById('f').contentWindow;
                return [typeof RTCPeerConnection, typeof frame.RTCPeerConnection,
                        popup ? typeof popup.RTCPeerConnection : 'no popup', typeof RTCDataChannel];
            }""")
            workers = list(browser.context.service_workers)
            page.wait_for_timeout(500)
        self.assertEqual(rtc, ["undefined", "undefined", "undefined", "undefined"])
        self.assertEqual(workers, [])
        self.assertEqual((self.server.count("/sw.js"), self.server.count("/socket")), (0, 0))
        self.assertEqual((self.direct.count, self.udp.received()), (0, 0))

    def test_workers_cannot_open_sockets_or_fetch_around_the_handler(self):
        port = self.direct.port
        work = (f"for (const u of ['wss://{PORTAL}/socket', 'ws://127.0.0.1:{port}/w']) {{"
                "  try { new WebSocket(u); } catch (e) {} }"
                f"fetch('http://127.0.0.1:{port}/f').catch(() => {{}});"
                "fetch('https://evil.example/f').catch(() => {});"
                "if ('onconnect' in self) { self.onconnect = e => e.ports[0].postMessage('shared ran'); }"
                " else { postMessage('worker ran'); }")
        self.server.route("/page", page_reply("<p>page</p>"))
        with self.browser() as browser:
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/page")
            ran = page.evaluate("""async (code) => {
                const url = URL.createObjectURL(new Blob([code], {type: 'text/javascript'}));
                const got = [];
                const worker = new Worker(url);
                worker.onmessage = e => got.push(e.data);
                try {
                    const shared = new SharedWorker(url);
                    shared.port.onmessage = e => got.push(e.data);
                    shared.port.start();
                } catch (e) { got.push('shared ' + e.name); }
                await new Promise(r => setTimeout(r, 2000));
                return got.sort();
            }""", work)
            proxy_hosts = set(browser.proxy.hosts)
        self.assertEqual(ran, ["shared ran", "worker ran"])  # the code ran in both kinds of worker
        self.assertEqual((self.direct.count, self.udp.received()), (0, 0))
        self.assertEqual(self.server.count("/socket"), 0)
        self.assertNotIn("evil.example", self.resolver.calls)
        self.assertEqual(self.hosts_seen(), {PORTAL})
        # A shared worker's requests and a worker's sockets go around the route handler (Playwright 1.63):
        # they end at the black hole (Edge's own background requests end there too).
        self.assertTrue({"127.0.0.1", "evil.example"} <= proxy_hosts)

    def probe_webrtc(self) -> dict:
        """Gather ICE candidates for 3 s with a STUN and two TURN servers on the loopback listeners."""
        self.server.route("/page", page_reply("<p>rtc</p>"))
        with self.browser(browser_class=KeepRtcBrowser) as browser:
            browser.navigate(f"https://{PORTAL}/page")
            return browser.page().evaluate(RTC_PROBE, [self.udp.port, self.direct.port])

    def test_webrtc_with_its_api_kept_still_sends_nothing(self):
        result = self.probe_webrtc()
        self.assertEqual(result["api"], "function")  # the page API was there this time
        self.assertEqual((self.udp.received(), self.direct.count), (0, 0))
        self.assertNotIn("srflx", result["kinds"])
        self.assertNotIn("host", result["kinds"])

    def test_without_the_profile_preference_webrtc_would_send_udp(self):
        """Why PROFILE_PREFERENCES exists: Edge does not read --force-webrtc-ip-handling-policy, so with the
        API kept and the launch flags only, STUN/TURN datagrams leave (here to the loopback listener only)."""
        with mock.patch.object(edge_module, "PROFILE_PREFERENCES", {}):
            result = self.probe_webrtc()
        self.assertEqual(result["api"], "function")
        self.assertGreater(self.udp.received(), 0)

    def test_hints_speculation_pings_and_event_streams_never_reach_the_network(self):
        port = self.direct.port
        self.server.route("/page", page_reply(
            '<script type="speculationrules">{"prefetch": [{"source": "list", "urls": ["/spec",'
            ' "https://evil.example/sp"]}], "prerender": [{"source": "list", "urls": ["/pre"]}]}</script>'
            f'<a id="a" ping="http://127.0.0.1:{port}/ping" href="/target">a</a>'
            f"<script>new EventSource('/events'); try {{ new EventSource('http://127.0.0.1:{port}/es'); }}"
            " catch (e) {}</script>",
            Link=(f"<https://cdn.evil.example>; rel=preconnect, <http://127.0.0.1:{port}>; rel=preconnect, "
                  "</pre.js>; rel=preload; as=script")))
        for path in ("/spec", "/pre"):
            self.server.route(path, page_reply(f"<p>{path}</p>"))
        self.server.route("/target", page_reply("<p>target</p>", Refresh=f"0; url=http://127.0.0.1:{port}/refresh"))
        self.server.route("/pre.js", Reply(b"", content_type="text/javascript"))
        self.server.route("/events", Reply(b"data: x\n\n", content_type="text/event-stream"))
        with self.browser() as browser:
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/page")
            page.wait_for_timeout(1000)
            page.click("#a")
            page.wait_for_url(f"https://{PORTAL}/target")
            page.wait_for_timeout(1000)
            refused = {(item.code, item.host, item.kind) for item in browser.refusals}
        self.assertEqual((self.direct.count, self.udp.received()), (0, 0))
        self.assertEqual(set(self.resolver.calls), {PORTAL})
        self.assertEqual(self.hosts_seen(), {PORTAL})
        self.assertEqual(self.server.count("/events"), 0)
        self.assertIn(("TYPE_BLOCKED", PORTAL, "eventsource"), refused)
        self.assertIn(("127.0.0.1", "document"), {(host, kind) for _, host, kind in refused})  # the Refresh

    def test_alt_svc_and_reporting_headers_never_reach_the_page(self):
        hints = {"Alt-Svc": f'h3=":{self.udp.port}"; ma=86400', "NEL": '{"report_to": "r", "max_age": 86400}',
                 "Report-To": '{"group": "r", "max_age": 86400, "endpoints": [{"url": "https://evil.example/r"}]}',
                 "Reporting-Endpoints": 'r="https://evil.example/r"', "X-Kept": "1"}
        self.server.route("/page", page_reply("<p>page</p>", **hints))
        self.server.route("/hdr", Reply(b"ok", content_type="text/plain", headers=hints))
        with self.browser() as browser:
            browser.navigate(f"https://{PORTAL}/page")
            seen = browser.page().evaluate(
                "async () => { const r = await fetch('/hdr'); return ['alt-svc', 'nel', 'report-to',"
                " 'reporting-endpoints', 'x-kept'].map(n => r.headers.get(n)); }")
        self.assertEqual(seen, [None, None, None, None, "1"])

    def test_popups_are_checked_like_any_page(self):
        self.server.route("/page", page_reply(
            "<script>window.open('/popup'); window.open('https://evil.example/p');</script>"))
        self.server.route("/popup", page_reply("<p>popup</p>"))
        with self.browser(state={"cookies": [cookie("sid", "s1", PORTAL)], "origins": []}) as browser:
            with browser.context.expect_page() as opened:
                browser.navigate(f"https://{PORTAL}/page")
            opened.value.wait_for_load_state()
            browser.page().wait_for_timeout(800)
            refused = {(item.code, item.host) for item in browser.refusals}
        self.assertEqual(cookies_of(self.server.seen("/popup")[0]), {"sid": "s1"})
        self.assertIn(("HOST_NOT_ALLOWED", "evil.example"), refused)
        self.assertEqual(self.hosts_seen(), {PORTAL})

    def test_a_popup_whose_first_request_redirects_lands_on_the_checked_target(self):
        """A popup's first request comes before its frame exists: its redirect still gets a hop page."""
        self.server.route("/page", page_reply("<script>window.open('/pop-go')</script>"))
        self.server.route("/pop-go", redirect(302, f"https://{TICKETS}/pop-end",
                                              **{"Set-Cookie": "hop=1; Path=/; Secure"}))
        self.server.route("/pop-end", page_reply("<p>end</p>"))
        self.server.route("/check", page_reply("<p>check</p>"))
        with self.browser() as browser:
            with browser.context.expect_page() as opened:
                browser.navigate(f"https://{PORTAL}/page")
            opened.value.wait_for_url(f"https://{TICKETS}/pop-end")
            browser.navigate(f"https://{PORTAL}/check")
            refusals = list(browser.refusals)
        self.assertEqual(refusals, [])
        self.assertEqual((self.server.count("/pop-go"), self.server.count("/pop-end")), (1, 1))
        self.assertNotIn("referer", self.server.seen("/pop-end")[0].headers)
        self.assertEqual(cookies_of(self.server.seen("/check")[0]), {"hop": "1"})

    def test_at_most_max_pages_pages_are_open(self):
        self.server.route("/page", page_reply(
            f"<script>for (let i = 0; i < {MAX_PAGES + 4}; i++) window.open('/popup?n=' + i);</script>"))
        self.server.route("/popup", page_reply("<p>popup</p>"))
        with self.browser() as browser:
            browser.navigate(f"https://{PORTAL}/page")
            browser.page().wait_for_timeout(2000)
            open_pages = len(browser.context.pages)
        self.assertLessEqual(open_pages, MAX_PAGES)
        self.assertGreater(open_pages, 1)  # popups did open, up to the limit

    def test_nothing_leaves_outside_the_route_handler(self):
        self.server.route("/page", page_reply(
            '<link rel="preconnect" href="https://cdn.evil.example"><link rel="dns-prefetch" href="https://dns.evil.example">'
            f'<img src="http://127.0.0.1:{self.direct.port}/i.png"><img src="https://{PORTAL}/pixel.png">'
            f"<script>fetch('http://127.0.0.1:{self.direct.port}/y').catch(() => {{}});"
            "navigator.sendBeacon && navigator.sendBeacon('/beacon', 'x');</script>"))
        self.server.route("/pixel.png", Reply(b"\x89PNG", content_type="image/png"))
        with self.browser() as browser:
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/page")
            page.reload()
            page.wait_for_timeout(1500)
            refused = {(item.code, item.host, item.kind) for item in browser.refusals}
        self.assertEqual((self.direct.count, self.udp.received()), (0, 0))
        self.assertEqual(set(self.resolver.calls), {PORTAL})
        self.assertEqual(self.hosts_seen(), {PORTAL})
        self.assertEqual(self.server.count("/beacon"), 0)
        self.assertIn("127.0.0.1", {host for _, host, _ in refused})


    def test_a_hidden_run_fetches_no_image_or_font_of_the_source_and_counts_its_exchanges(self):
        # The cost of a hidden run is its exchanges, one at a time: images and fonts of the source's own hosts
        # are not fetched (the readers read text and structure); other hosts are refused as before.
        self.server.route("/page", page_reply(
            f'<style>@font-face {{ font-family: T; src: url("https://{PORTAL}/f.woff2"); }} p {{ font-family: T; }}'
            f'</style><img src="https://{PORTAL}/pixel.png"><img src="https://evil.example/i.png"><p>text</p>'
            f'<script src="https://{PORTAL}/s.js"></script>'))
        self.server.route("/pixel.png", Reply(b"\x89PNG", content_type="image/png"))
        self.server.route("/f.woff2", Reply(b"wOF2", content_type="font/woff2"))
        self.server.route("/s.js", Reply(b"window.ran = 1;", content_type="text/javascript"))
        with self.browser() as browser:
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/page")
            page.wait_for_function("window.ran === 1")
            page.wait_for_timeout(800)
            stats = browser.run_stats()
            refused = {(item.code, item.host, item.kind) for item in browser.refusals}
        self.assertEqual((self.server.count("/pixel.png"), self.server.count("/f.woff2"), self.server.count("/s.js")),
                         (0, 0, 1))
        self.assertEqual(stats["skipped"], 2)
        self.assertGreaterEqual(stats["answered"], 2)  # the page and its script
        self.assertGreater(stats["launch_seconds"], 0)
        self.assertEqual(refused, {("HOST_NOT_ALLOWED", "evil.example", "image")})  # still refused, still kept


class BindingTest(BrowserCase):
    def test_a_sign_in_cancelled_while_the_browser_runs_is_never_saved(self):
        self.server.route("/set", page_reply("<p>s</p>", **{"Set-Cookie": "sid=late; Path=/; Secure"}))
        attempt = self.manager.begin_login("alpha")
        with self.browser() as browser:
            browser.navigate(f"https://{PORTAL}/set")
            self.manager.cancel_login("alpha")  # the user pressed Hủy while the page was open
            state = browser.storage_state()
        self.assertEqual([item["value"] for item in state["cookies"]], ["late"])
        self.assertFalse(self.manager.attempt_is_current(attempt))
        with self.assertRaises(StaleLogin):
            self.manager.complete_login(attempt, state)
        self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")
        folder = self.manager.vault.source_folder("alpha")
        self.assertEqual(list(folder.glob("session-*")) if folder.exists() else [], [])

    def test_a_disconnect_during_a_hidden_run_refuses_its_rotated_cookies(self):
        self.connect(self.manager, {"cookies": [cookie("sid", "old", PORTAL)], "origins": []})
        self.server.route("/rotate", page_reply("<p>r</p>", **{"Set-Cookie": "sid=new; Path=/; Secure"}))

        def action(browser):
            self.manager.disconnect("alpha")
            browser.navigate(f"https://{PORTAL}/rotate")
        run = self.run_session(action)
        self.assertIs(run.saved, False)
        self.assertEqual(self.manager.status("alpha")["state"], "NOT_CONNECTED")
        self.assertEqual(list(self.manager.vault.source_folder("alpha").glob("session-*")), [])

    def test_a_new_sign_in_during_a_hidden_run_keeps_the_new_session(self):
        self.connect(self.manager, {"cookies": [cookie("sid", "first", PORTAL)], "origins": []})
        self.server.route("/rotate", page_reply("<p>r</p>", **{"Set-Cookie": "sid=from-old-run; Path=/; Secure"}))

        def action(browser):
            self.connect(self.manager, {"cookies": [cookie("sid", "second", PORTAL)], "origins": []})
            browser.navigate(f"https://{PORTAL}/rotate")
        run = self.run_session(action)
        self.assertIs(run.saved, False)
        lease = self.manager.session_for("alpha")
        self.assertEqual((lease.generation, lease.state["cookies"][0]["value"]), (2, "second"))

    def test_two_managers_with_the_same_source_and_generation_never_take_each_others_results(self):
        other_dir = TemporaryDirectory(dir=TEMP_PARENT, prefix="session-browser-other-")
        self.addCleanup(other_dir.cleanup)
        other_root = self.new_manager(Path(other_dir.name))           # another project root, same account
        other_account = self.new_manager(self.root, user_sid=SECOND)  # the same root, another Windows account
        managers = {"a": self.manager, "b": other_root, "c": other_account}
        for name, manager in managers.items():
            self.connect(manager, {"cookies": [cookie("sid", name, PORTAL)], "origins": []})
        leases = {name: manager.session_for("alpha") for name, manager in managers.items()}
        self.assertEqual({lease.generation for lease in leases.values()}, {1})
        self.server.route("/rotate", page_reply("<p>r</p>", **{"Set-Cookie": "sid=a2; Path=/; Secure"}))
        run = self.run_session(lambda browser: browser.navigate(f"https://{PORTAL}/rotate").status)
        self.assertEqual((run.value, run.saved), (200, True))
        forged = {"cookies": [cookie("sid", "forged", PORTAL)], "origins": []}
        for manager in (other_root, other_account):
            self.assertFalse(manager.save_rotated(leases["a"], forged))
            self.assertFalse(manager.mark_invalid(leases["a"]))
            self.assertFalse(manager.record_check(leases["a"], "invalid"))
            attempt = self.manager.begin_login("alpha")
            self.assertTrue(self.manager.attempt_is_current(attempt))
            self.assertFalse(manager.attempt_is_current(attempt))
            with self.assertRaises(StaleLogin):
                manager.complete_login(attempt, forged)
            self.assertFalse(manager.end_login(attempt, "LOGIN_WINDOW_CLOSED"))
            self.assertTrue(self.manager.cancel_login("alpha"))
        for name in ("b", "c"):
            self.assertFalse(self.manager.save_rotated(leases[name], forged))
        values = {name: manager.session_for("alpha").state["cookies"][0]["value"]
                  for name, manager in managers.items()}
        self.assertEqual(values, {"a": "a2", "b": "b", "c": "c"})
        self.assertEqual({manager.status("alpha")["state"] for manager in managers.values()}, {"CONNECTED"})
        self.assertEqual({lease.generation for lease in (m.session_for("alpha") for m in managers.values())}, {1})

    def test_a_failing_action_names_no_url_and_saves_nothing(self):
        self.connect(self.manager, {"cookies": [cookie("sid", "s1", PORTAL)], "origins": []})

        def leaky(browser):  # a Playwright error repeats the whole URL, ticket included
            browser.page().goto(f"https://evil.example/x?ticket={CANARY_SID}")
        with self.assertRaises(BrowserFailed) as caught:
            self.run_session(leaky)
        error = caught.exception
        self.assertEqual(error.code, "BROWSER_FAILED")
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)
        self.assertNotIn(CANARY_SID, f"{error} {error.message} {error!r}")
        self.assert_no_profile_left()

        def broken(browser):
            raise ValueError("an adapter bug")
        with self.assertRaises(ValueError):  # the caller's own error goes through as it is
            self.run_session(broken)
        self.assert_no_profile_left()
        lease = self.manager.session_for("alpha")
        self.assertEqual((lease.generation, lease.state["cookies"][0]["value"]), (1, "s1"))

    def tightened(self, name: str = "sid", value: str = "same-fake-value") -> dict:
        """A Lax, script-readable cookie; ``/tighten`` sends it back with the same value, HttpOnly and Strict."""
        self.server.route("/tighten", page_reply("<p>t</p>", **{
            "Set-Cookie": f"{name}={value}; Path=/; Secure; HttpOnly; SameSite=Strict"}))
        return {**cookie(name, value, PORTAL), "httpOnly": False, "sameSite": "Lax"}

    @staticmethod
    def protection(state: dict) -> list:
        return [(item["name"], item["value"], item["httpOnly"], item["sameSite"]) for item in state["cookies"]]

    def test_a_cookie_whose_attributes_change_but_not_its_value_is_saved_and_loaded_next_time(self):
        self.connect(self.manager, {"cookies": [self.tightened()], "origins": []})
        before = self.manager.status("alpha")
        lease = self.manager.session_for("alpha")
        run = self.run_session(lambda browser: (browser.navigate(f"https://{PORTAL}/tighten").status,
                                                self.protection(browser.storage_state())))
        self.assertEqual(run.value, (200, [("sid", "same-fake-value", True, "Strict")]))  # what Edge took
        self.assertIs(run.saved, True)
        saved = self.manager.session_for("alpha")
        self.assertEqual(self.protection(saved.state), [("sid", "same-fake-value", True, "Strict")])
        self.assertEqual(saved.generation, lease.generation)
        self.assertEqual(saved.authenticated_at, lease.authenticated_at)
        self.assertEqual(self.manager.status("alpha"), before)  # authenticated_at, recheck_at (TTL), state
        self.server.route("/plain", page_reply("<p>p</p>"))

        def next_context(browser):  # the next hidden run loads what was saved, attributes included
            loaded = [(item["name"], item["value"], item["httpOnly"], item["sameSite"])
                      for item in browser.context.cookies()]
            browser.navigate(f"https://{PORTAL}/plain")
            return loaded
        again = self.run_session(next_context)
        self.assertEqual((again.value, again.saved), ([("sid", "same-fake-value", True, "Strict")], None))
        self.assertEqual(cookies_of(self.server.seen("/plain")[0]), {"sid": "same-fake-value"})

    def test_an_attribute_change_of_a_stale_or_foreign_lease_is_refused(self):
        original = self.tightened()
        self.connect(self.manager, {"cookies": [original], "origins": []})

        def action(browser):  # a new sign-in while the hidden run goes on: its lease is stale now
            self.connect(self.manager, {"cookies": [{**original, "value": "second"}], "origins": []})
            browser.navigate(f"https://{PORTAL}/tighten")
            return browser.storage_state()
        run = self.run_session(action)
        self.assertIs(run.saved, False)
        kept = self.manager.session_for("alpha")
        self.assertEqual((kept.generation, self.protection(kept.state)), (2, [("sid", "second", False, "Lax")]))
        other_dir = TemporaryDirectory(dir=TEMP_PARENT, prefix="session-browser-other-")
        self.addCleanup(other_dir.cleanup)
        other = self.new_manager(Path(other_dir.name))  # another root, same source and generation
        self.connect(other, {"cookies": [original], "origins": []})
        foreign = other.session_for("alpha")
        self.assertEqual(foreign.generation, 1)
        self.assertFalse(self.manager.save_rotated(foreign, run.value))
        self.assertFalse(other.save_rotated(kept, run.value))
        self.assertEqual(self.protection(other.session_for("alpha").state), [("sid", "same-fake-value", False, "Lax")])
        self.assertEqual(self.protection(self.manager.session_for("alpha").state), [("sid", "second", False, "Lax")])

    def test_a_session_that_did_not_change_is_not_saved_again(self):
        self.connect(self.manager, {"cookies": [cookie("pref", "p", PORTAL)], "origins": []})
        self.server.route("/persist", page_reply("<p>p</p>", **{
            "Set-Cookie": "sid=kept; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=3600"}))
        self.server.route("/plain", page_reply("<p>p</p>"))

        def expiry() -> float:
            return next(item["expires"] for item in self.manager.session_for("alpha").state["cookies"]
                        if item["name"] == "sid")
        first = self.run_session(lambda browser: browser.navigate(f"https://{PORTAL}/persist").status)
        self.assertEqual((first.value, first.saved), (200, True))
        expires = expiry()
        self.assertNotEqual(expires, round(expires))  # a fraction of a second, kept as Edge gave it
        second = self.run_session(lambda browser: browser.navigate(f"https://{PORTAL}/plain").status)
        self.assertEqual((second.value, second.saved), (200, None))  # Edge gives the expiry back exactly
        self.assertEqual(expiry(), expires)

    def test_a_session_whose_cookies_are_all_gone_is_not_saved(self):
        self.connect(self.manager, {"cookies": [cookie("sid", "s1", PORTAL)], "origins": []})
        self.server.route("/logout", page_reply("<p>bye</p>", **{"Set-Cookie": "sid=; Path=/; Secure; Max-Age=0"}))
        run = self.run_session(lambda browser: browser.navigate(f"https://{PORTAL}/logout").status)
        self.assertEqual((run.value, run.saved), (200, False))
        lease = self.manager.session_for("alpha")
        self.assertEqual((lease.generation, lease.state["cookies"][0]["value"]), (1, "s1"))

    def test_a_database_error_while_saving_is_reported_not_raised(self):
        self.connect(self.manager, {"cookies": [cookie("sid", "old", PORTAL)], "origins": []})
        self.server.route("/rotate", page_reply("<p>r</p>", **{"Set-Cookie": "sid=new; Path=/; Secure"}))
        locked = sqlite3.OperationalError("database is locked")
        with mock.patch.object(self.manager, "save_rotated", side_effect=locked):
            run = self.run_session(lambda browser: browser.navigate(f"https://{PORTAL}/rotate").status)
        self.assertEqual((run.value, run.saved), (200, False))
        self.assertEqual(self.manager.session_for("alpha").state["cookies"][0]["value"], "old")

    def test_a_profile_left_behind_is_reported_and_cleared_at_the_next_start(self):
        self.connect(self.manager, {"cookies": [cookie("sid", "s1", PORTAL)], "origins": []})
        self.server.route("/page", page_reply("<p>page</p>"))
        with mock.patch.object(self.manager.vault, "remove_browser_profile", return_value=False):
            run = self.run_session(lambda browser: browser.navigate(f"https://{PORTAL}/page").status)
        self.assertEqual((run.value, run.profile_left), (200, True))
        left = list(self.manager.vault.browser_folder().iterdir())
        self.assertEqual(len(left), 1)
        self.assertRegex(left[0].name, r"^run-[0-9a-f]{16}$")
        self.assertTrue(self.manager.clear_browser_profiles())
        self.assert_no_profile_left()

    @unittest.skipUnless(WINDOWS, "Windows ACLs")
    def test_the_profile_of_a_run_is_private_and_removed_afterwards(self):
        """With the real ACL (the protector stays fake: no session is encrypted here)."""
        vault = SessionVault(self.root, protector=FakeProtector())
        acl = PrivateFolderAcl()
        http = SessionHttp(ALPHA.all_hosts, self.network)
        self.server.route("/page", page_reply("<p>page</p>", **{"Set-Cookie": "sid=s1; Path=/; Secure"}))
        with SessionBrowser(ALPHA, http, vault, self.control, page_seconds=10) as browser:
            browser.navigate(f"https://{PORTAL}/page")
            profile = browser._profile
            self.assertEqual(profile.parent, vault.browser_folder())
            self.assertEqual(acl.inspect(profile.parent).problems(acl.user_sid, protected=True), [])
            self.assertEqual(acl.inspect(profile).problems(acl.user_sid, protected=False), [])
            self.assertTrue(any(profile.iterdir()))  # Edge's profile, with the cookie, lives there meanwhile
            for folder in (profile, profile.parent):  # pinned while Edge runs: neither can be swapped
                with self.assertRaises(PermissionError):
                    folder.rename(folder.with_name(folder.name + "-moved"))
        self.assertFalse(profile.exists())
        self.assertFalse(browser.profile_left)
        self.assertEqual(list(vault.browser_folder().iterdir()), [])

    def test_a_cancel_stops_every_request(self):
        self.server.route("/page", page_reply("<p>page</p>"))
        with self.browser() as browser:
            page = browser.page()
            browser.navigate(f"https://{PORTAL}/page")
            self.control.request("cancel")
            with self.assertRaises(Exception):
                page.goto(f"https://{PORTAL}/page?again=1")
            with self.assertRaises(Cancelled):
                browser.navigate(f"https://{PORTAL}/page?again=2")
            refused = [(item.code, item.host) for item in browser.refusals]
        self.assertEqual(refused, [("CANCELLED", PORTAL), ("CANCELLED", PORTAL)])
        self.assertEqual(self.server.count("/page"), 1)


if __name__ == "__main__":
    unittest.main()
