"""M7 exception A: a source's one page script (download_account_page_script) in the session browser.

Kept by the user's choice on 2026-10-10 (AGENTS.md, Network). Real headless Edge with the HTTPS fixture server;
``ads.example`` plays the script's host and every name is ``.example``: nothing here is a real site, script,
session or download. The cases follow Codex's design review: the exact script loads without the session;
anything else on its host, a redirect, a private address, DNS rebinding, a bad certificate, a too large, slow,
failed or cancelled answer is refused; the script's own requests to other hosts are refused; its host is never
the source's (not claimed, not in the vault, not in the session client's scope); the sign-in window and list
runs never load it; at most MAX_SCRIPT_REQUESTS answers per run; and a film page whose gate needs it can be
clicked only with it.
"""
from __future__ import annotations

import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace

from biliflow.download_account_browser import HeadedPermit, SessionBrowser
from biliflow.download_account_config import AccountConfig
from biliflow.download_account_http import SessionHttp, SessionReply
from biliflow.download_account_page_script import MAX_SCRIPT_REQUESTS, PageScript
from biliflow.download_account_runs import run_with_session
from biliflow.download_account_sources import AccountSourceProvider
from biliflow.download_account_vault import SessionVault
from biliflow.download_accounts import AccountManager, own_state
from biliflow.download_http import Cancelled, HttpError, SafeHttp
from biliflow.download_runner import ProcessControl
from biliflow.download_source_types import ResolveContext, SourceError
from tests.release_forms_fixtures import Card, ReleaseSite
from tests.source_fixtures import FFPROBE, HAVE_FFMPEG, NEED_FFMPEG, PUBLIC_ADDRESS, Reply, public_resolver
from tests.test_download_account_browser import (
    ALPHA,
    BETA,
    CANARY_SID,
    DATA,
    FILES,
    FIRST,
    PORTAL,
    TEMP_PARENT,
    TICKETS,
    BrowserCase,
    cookie,
    page_reply,
    redirect,
)
from tests.test_download_account_http import rebinding
from tests.test_download_account_release_forms import CLIP, TicketReader
from tests.test_download_account_release_forms import setUpModule as release_setup
from tests.test_download_account_release_forms import tearDownModule as release_teardown
from tests.test_download_accounts import FakeProtector, GoodAcl

SCRIPT_HOST = "ads.example"
SCRIPT_PATH = "/js/gate.js"
SCRIPT_URL = f"https://{SCRIPT_HOST}{SCRIPT_PATH}"
STAMP = 1760000000000  # what the page adds: ?_=<Date.now()>
SCRIPT_BODY = b"window.gateOk = (window.gateOk || 0) + 1;"
AD_COOKIE = "BF-CANARY-ad-91c"
JAR_COOKIE = "BF-CANARY-jar-4d2"
GATED = replace(ALPHA, page_script=SCRIPT_URL)
SENT_HEADERS = {"host", "accept", "accept-encoding", "user-agent", "connection"}  # http.client adds host, encoding


def setUpModule():
    release_setup()


def tearDownModule():
    release_teardown()


def script_tag(url: str) -> str:
    return f'<script src="{url}"></script>'


def stamped(offset: int = 0) -> str:
    return f"{SCRIPT_URL}?_={STAMP + offset}"


def script_reply(body: bytes = SCRIPT_BODY, **changes) -> Reply:
    return replace(Reply(body, content_type="text/javascript; charset=utf-8",
                         headers={"Set-Cookie": f"ad={AD_COOKIE}; Path=/; Secure; SameSite=None"}), **changes)


# ------------------------------------------------------------------------------------------ no browser
class RefusalTest(unittest.TestCase):
    """``PageScript.refusal`` alone: exactly the configured script, nothing else on its host."""

    @staticmethod
    def request(url: str, *, kind: str = "script", method: str = "GET", body: bytes | None = None):
        return SimpleNamespace(url=url, resource_type=kind, method=method, post_data_buffer=body)

    def test_only_the_exact_script_with_at_most_a_timestamp_is_answered(self):
        script = PageScript(SCRIPT_URL)
        for url in (SCRIPT_URL, stamped(), f"https://ADS.example{SCRIPT_PATH}?_=1760000000",
                    f"https://{SCRIPT_HOST}:443{SCRIPT_PATH}"):
            with self.subTest(url=url):
                self.assertTrue(script.owns(url))
                self.assertIsNone(script.refusal(self.request(url)))
        cases = {
            "image": (self.request(SCRIPT_URL, kind="image"), "PAGE_SCRIPT_TYPE"),
            "fetch": (self.request(SCRIPT_URL, kind="fetch"), "PAGE_SCRIPT_TYPE"),
            "document": (self.request(SCRIPT_URL, kind="document"), "PAGE_SCRIPT_TYPE"),
            "POST": (self.request(SCRIPT_URL, method="POST"), "PAGE_SCRIPT_METHOD"),
            "a body": (self.request(SCRIPT_URL, body=b"x"), "PAGE_SCRIPT_METHOD"),
            "http": (self.request(f"http://{SCRIPT_HOST}{SCRIPT_PATH}"), "PAGE_SCRIPT_URL"),
            "another port": (self.request(f"https://{SCRIPT_HOST}:8443{SCRIPT_PATH}"), "PAGE_SCRIPT_URL"),
            "a user name": (self.request(f"https://user:pw@{SCRIPT_HOST}{SCRIPT_PATH}"), "PAGE_SCRIPT_URL"),
            "another path": (self.request(f"https://{SCRIPT_HOST}/js/other.js"), "PAGE_SCRIPT_URL"),
            "a longer path": (self.request(f"{SCRIPT_URL}/x"), "PAGE_SCRIPT_URL"),
            "letters": (self.request(f"{SCRIPT_URL}?_=abc"), "PAGE_SCRIPT_URL"),
            "a short number": (self.request(f"{SCRIPT_URL}?_=123"), "PAGE_SCRIPT_URL"),
            "a long number": (self.request(f"{SCRIPT_URL}?_=12345678901234567"), "PAGE_SCRIPT_URL"),
            "another key": (self.request(f"{SCRIPT_URL}?x={STAMP}"), "PAGE_SCRIPT_URL"),
            "two keys": (self.request(f"{stamped()}&x=1"), "PAGE_SCRIPT_URL"),
        }
        for name, (request, code) in cases.items():
            with self.subTest(name):
                self.assertEqual(script.refusal(request), code)
        script.answered = MAX_SCRIPT_REQUESTS
        self.assertEqual(script.refusal(self.request(SCRIPT_URL)), "PAGE_SCRIPT_LIMIT")
        self.assertFalse(script.owns(f"https://cdn.{SCRIPT_HOST}{SCRIPT_PATH}"))  # not its host: not its role

    def test_a_page_script_url_is_exact(self):
        for url in (stamped(), f"{SCRIPT_URL}#x", f"http://{SCRIPT_HOST}{SCRIPT_PATH}",
                    f"https://user@{SCRIPT_HOST}{SCRIPT_PATH}"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                PageScript(url)


class NotTheSourcesTest(unittest.TestCase):
    def test_the_script_host_is_not_claimed_kept_or_fetched_as_the_sources(self):
        self.assertNotIn(SCRIPT_HOST, GATED.all_hosts)
        provider = AccountSourceProvider(GATED)
        self.assertFalse(provider.claims(SCRIPT_URL))  # never taken as a link of the source
        self.assertTrue(provider.claims(f"https://{PORTAL}/phim/1"))
        with self.assertRaises(HttpError) as caught:  # the client of LoginView.fetch and of every hidden run
            SessionHttp(GATED.all_hosts).in_scope(SCRIPT_URL)
        self.assertEqual(caught.exception.code, "HOST_NOT_ALLOWED")
        state = {"cookies": [cookie("ad", AD_COOKIE, SCRIPT_HOST), cookie("sid", CANARY_SID, PORTAL)],
                 "origins": [{"origin": f"https://{SCRIPT_HOST}", "localStorage": [{"name": "a", "value": "b"}]}]}
        kept = own_state(GATED, state)  # what the vault may ever get
        self.assertEqual([item["domain"] for item in kept["cookies"]], [PORTAL])
        self.assertEqual(kept["origins"], [])


class HandlerTest(unittest.TestCase):
    """``SessionBrowser._answer_page_script`` without a browser: what leaves and what the page is handed."""

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        vault = SessionVault(TEMP_PARENT, protector=FakeProtector(), acl=GoodAcl(FIRST))  # never used here
        self.browser = SessionBrowser(GATED, SessionHttp(GATED.all_hosts), vault, ProcessControl(),
                                      page_script=PageScript(SCRIPT_URL))
        self.sent: list = []

    def answer(self, status: int, *extra: tuple[str, str]):
        def send(url, control, *, method, headers):
            self.sent.append((url, method, dict(headers)))
            return SessionReply(url, status, (("Content-Type", "text/javascript"), *extra), SCRIPT_BODY)

        self.browser.page_script.http.send = send
        route = FakeRoute(stamped())
        self.browser._handle(route)
        return route

    def test_only_accept_and_user_agent_leave_and_only_the_content_type_comes_back(self):
        route = self.answer(200, ("Set-Cookie", f"ad={AD_COOKIE}; Path=/; Secure; SameSite=None"),
                            ("Access-Control-Allow-Origin", "*"), ("Link", "<https://evil.example/x>; rel=preload"),
                            ("Content-Security-Policy", "script-src *"), ("Refresh", "0; url=https://evil.example/"))
        # The configured URL itself: the page's ?_= query (anything a script in the page wrote there) stays here.
        self.assertEqual(self.sent, [(SCRIPT_URL, "GET", {"accept": "*/*", "user-agent": "UA-test"})])
        self.assertEqual(route.fulfilled, [{"status": 200, "body": SCRIPT_BODY, "headers": {
            "content-type": "text/javascript", "cache-control": "no-store"}}])
        self.assertEqual((route.aborted, self.browser.refusals), ([], []))

    def test_a_redirect_or_a_failed_answer_is_never_handed_to_the_page(self):
        for status, code in ((302, "REDIRECT_REFUSED"), (307, "REDIRECT_REFUSED"), (204, "PAGE_SCRIPT_FAILED"),
                             (500, "PAGE_SCRIPT_FAILED")):
            with self.subTest(status=status):
                self.browser.refusals.clear()
                route = self.answer(status, ("Location", SCRIPT_URL))
                self.assertEqual((route.fulfilled, len(route.aborted)), ([], 1))
                self.assertEqual([item.code for item in self.browser.refusals], [code])


class FakeRoute:
    """A Playwright route of a script request on the script's host, carrying what a page would add."""

    def __init__(self, url: str):
        self.request = SimpleNamespace(
            url=url, resource_type="script", method="GET", post_data_buffer=None, frame=None,
            headers={"cookie": f"sid={CANARY_SID}", "referer": f"https://{PORTAL}/film", "origin": f"https://{PORTAL}",
                     "x-csrf-token": "BF-CANARY-csrf-2b8", "authorization": "Basic BF-CANARY", "user-agent": "UA-test"},
            is_navigation_request=lambda: False)
        self.fulfilled: list = []
        self.aborted: list = []

    def fulfill(self, **kwargs):
        self.fulfilled.append(kwargs)

    def abort(self, code):
        self.aborted.append(code)


# ----------------------------------------------------------------------------------------- browser
class PageScriptCase(BrowserCase):
    def gated_manager(self) -> AccountManager:
        """A manager of the same root whose config gives source alpha the page script (one database: the
        session connected here is the one ``self.manager`` sees too)."""
        manager = AccountManager(self.root, AccountConfig({"alpha": GATED, "beta": BETA}), clock=self.clock,
                                 vault=SessionVault(self.root, protector=FakeProtector(), acl=GoodAcl(FIRST)))
        self.addCleanup(manager.close)
        self.connect(manager, {"cookies": [cookie("sid", CANARY_SID, ".alpha.example")], "origins": []})
        return manager

    def script(self, url: str = SCRIPT_URL, **options) -> PageScript:
        return PageScript(url, self.network, **options)

    def gated_browser(self, script: PageScript | None) -> SessionBrowser:
        http = SessionHttp(GATED.all_hosts, self.network)
        return SessionBrowser(GATED, http, self.manager.vault, self.control, page_seconds=10, page_script=script)

    def film(self, *tags: str) -> None:
        self.server.route("/film", page_reply("<p>film</p>" + "".join(tags)))

    def load(self, script: PageScript | None, *tags: str, wait: float = 1.0):
        """The film page with ``tags``, with a session cookie and a cookie of the script's host in the jar:
        (how often the script ran, the refusals as (code, host), the context's cookies afterwards)."""
        self.film(*tags)
        with self.gated_browser(script) as browser:
            browser.context.add_cookies([cookie("sid", CANARY_SID, ".alpha.example"),
                                         cookie("jar", JAR_COOKIE, SCRIPT_HOST)])
            browser.navigate(f"https://{PORTAL}/film")
            browser.page().wait_for_timeout(wait * 1000)
            ran = browser.page().evaluate("window.gateOk || 0")
            jar = browser.context.cookies()
            refused = [(item.code, item.host) for item in browser.refusals]
        return ran, refused, jar


class PageScriptTest(PageScriptCase):
    def test_exactly_the_configured_script_loads_without_the_session(self):
        self.server.route(SCRIPT_PATH, script_reply())
        ran, refused, jar = self.load(self.script(), script_tag(stamped()), script_tag(SCRIPT_URL))
        self.assertEqual((ran, refused), (2, []))
        seen = self.server.seen(SCRIPT_PATH)
        self.assertEqual([(item.host, item.method, item.query) for item in seen], [(SCRIPT_HOST, "GET", {})] * 2)
        for item in seen:  # no cookie (not even its own host's), credential, CSRF, Referer, Origin or Sec-Fetch
            self.assertLessEqual(set(item.headers), SENT_HEADERS, item.headers)
            self.assertEqual(item.headers["accept"], "*/*")
        sid = [item for item in self.server.seen("/film") if CANARY_SID in item.headers.get("cookie", "")]
        self.assertEqual(len(sid), 1)  # the page itself got its session
        self.assertNotIn(AD_COOKIE, repr(jar))  # the script's Set-Cookie never reached the jar
        self.assertIn(JAR_COOKIE, repr(jar))

    def test_anything_else_on_the_script_host_is_refused(self):
        self.server.route(SCRIPT_PATH, script_reply())
        self.server.route("/js/other.js", script_reply())
        ran, refused, _jar = self.load(
            self.script(),
            script_tag(f"https://{SCRIPT_HOST}/js/other.js?_={STAMP}"), script_tag(f"{SCRIPT_URL}?_=abc"),
            script_tag(f"{SCRIPT_URL}?_=123"), script_tag(f"{SCRIPT_URL}?x=1"), script_tag(f"{stamped(1)}&x=1"),
            script_tag(f"https://{SCRIPT_HOST}:8443{SCRIPT_PATH}"),
            f'<img src="{stamped(2)}">', f'<link rel="stylesheet" href="{stamped(3)}">',
            f"<script>fetch('{stamped(4)}').catch(() => null);</script>")
        self.assertEqual(ran, 0)
        self.assertEqual(sorted(refused), sorted([("PAGE_SCRIPT_URL", SCRIPT_HOST)] * 6
                                                 + [("PAGE_SCRIPT_TYPE", SCRIPT_HOST)] * 3))
        self.assertEqual((self.server.count(SCRIPT_PATH), self.server.count("/js/other.js")), (0, 0))

    def test_a_redirect_is_never_followed_even_to_the_same_url(self):
        self.server.route("/own.js", script_reply())
        for status, target in ((302, stamped()), (301, SCRIPT_URL), (307, f"https://{PORTAL}/own.js")):
            with self.subTest(status=status):
                self.server.requests.clear()
                self.server.route(SCRIPT_PATH, redirect(status, target))
                ran, refused, _jar = self.load(self.script(), script_tag(SCRIPT_URL), wait=0.5)
                self.assertEqual((ran, refused), (0, [("REDIRECT_REFUSED", SCRIPT_HOST)]))
                self.assertEqual((self.server.count(SCRIPT_PATH), self.server.count("/own.js")), (1, 0))

    def test_a_private_address_dns_rebinding_and_a_bad_certificate_are_refused(self):
        self.server.route(SCRIPT_PATH, script_reply())
        self.resolver.answers[SCRIPT_HOST] = ["10.0.0.5"]
        ran, refused, _jar = self.load(self.script(), script_tag(SCRIPT_URL), wait=0.5)
        self.assertEqual((ran, refused), (0, [("PRIVATE_ADDRESS", SCRIPT_HOST)]))
        self.assertEqual(self.server.count(SCRIPT_PATH), 0)

        self.resolver.answers[SCRIPT_HOST] = rebinding([PUBLIC_ADDRESS], ["127.0.0.1"])
        ran, refused, _jar = self.load(self.script(), script_tag(stamped()), script_tag(SCRIPT_URL), wait=0.5)
        self.assertEqual((ran, refused), (1, [("PRIVATE_ADDRESS", SCRIPT_HOST)]))
        self.assertEqual(self.server.count(SCRIPT_PATH), 1)

        bad = "https://adsbad.example/js/gate.js"  # a name the fixture's certificate does not hold
        ran, refused, _jar = self.load(self.script(bad), script_tag(bad), wait=0.5)
        self.assertEqual((ran, refused), (0, [("TLS_ERROR", "adsbad.example")]))
        self.assertNotIn("adsbad.example", self.hosts_seen())

    def test_a_too_large_a_slow_a_failed_or_a_non_script_answer_is_refused(self):
        cases = [("too large", script_reply(), {"max_body": 16}, "TOO_LARGE_RESPONSE"),
                 ("slow", script_reply(delay=0.5, chunk=4), {"request_seconds": 1}, "TIMEOUT"),
                 ("missing", Reply(b"no", 404, "text/plain"), {}, "PAGE_SCRIPT_FAILED"),
                 ("not a script", script_reply(content_type="text/html"), {}, "PAGE_SCRIPT_FAILED")]
        for name, reply, options, code in cases:
            with self.subTest(name):
                self.server.route(SCRIPT_PATH, reply)
                ran, refused, _jar = self.load(self.script(**options), script_tag(SCRIPT_URL), wait=0.5)
                self.assertEqual((ran, refused), (0, [(code, SCRIPT_HOST)]))

    def test_a_cancel_while_the_script_loads_ends_its_request(self):
        self.server.route(SCRIPT_PATH, script_reply(delay=0.5, chunk=2))  # about 10 s
        self.film(script_tag(SCRIPT_URL))
        timer = threading.Timer(1.0, self.control.request, ("cancel",))
        self.addCleanup(timer.cancel)
        with self.gated_browser(self.script()) as browser:
            timer.start()
            with self.assertRaises(Cancelled):
                browser.navigate(f"https://{PORTAL}/film")
            refused = [(item.code, item.host) for item in browser.refusals]
        self.assertEqual(refused[0], ("CANCELLED", SCRIPT_HOST))

    def test_the_scripts_own_requests_to_other_hosts_are_refused(self):
        body = (b"window.gateOk = 1; fetch('https://track.example/t').catch(() => null);"
                b"new Image().src = 'https://evil.example/p.gif';"
                b"const s = document.createElement('script'); s.src = 'https://ads.example/js/next.js';"
                b"document.head.appendChild(s);")
        self.server.route(SCRIPT_PATH, script_reply(body))
        ran, refused, _jar = self.load(self.script(), script_tag(SCRIPT_URL), wait=1.5)
        self.assertEqual(ran, 1)
        self.assertEqual(sorted(refused), sorted([("HOST_NOT_ALLOWED", "track.example"),
                                                  ("HOST_NOT_ALLOWED", "evil.example"),
                                                  ("PAGE_SCRIPT_URL", SCRIPT_HOST)]))
        self.assertEqual(set(self.resolver.calls), {PORTAL, SCRIPT_HOST})
        self.assertEqual(self.hosts_seen(), {PORTAL, SCRIPT_HOST})

    def test_at_most_the_run_limit_of_answers(self):
        self.server.route(SCRIPT_PATH, script_reply())
        tags = [script_tag(stamped(index)) for index in range(MAX_SCRIPT_REQUESTS + 2)]
        ran, refused, _jar = self.load(self.script(), *tags, wait=0.5)
        self.assertEqual((ran, refused), (MAX_SCRIPT_REQUESTS, [("PAGE_SCRIPT_LIMIT", SCRIPT_HOST)] * 2))
        self.assertEqual(self.server.count(SCRIPT_PATH), MAX_SCRIPT_REQUESTS)

    def test_without_one_it_is_refused_and_a_sign_in_window_or_a_source_host_cannot_have_one(self):
        self.server.route(SCRIPT_PATH, script_reply())
        ran, refused, _jar = self.load(None, script_tag(SCRIPT_URL), wait=0.5)
        self.assertEqual((ran, refused), (0, [("HOST_NOT_ALLOWED", SCRIPT_HOST)]))  # as in every sign-in window
        http = SessionHttp(GATED.all_hosts, self.network)
        with self.assertRaises(ValueError):  # a script on a host of the source: not this role
            SessionBrowser(GATED, http, self.manager.vault, self.control,
                           page_script=PageScript(f"https://{PORTAL}/gate.js", self.network))
        with self.assertRaises(ValueError):  # the sign-in window never gets one
            SessionBrowser(GATED, http, self.manager.vault, self.control,
                           headed=HeadedPermit(self.manager.begin_login("alpha")), page_script=self.script())
        self.assertEqual(self.server.count(SCRIPT_PATH), 0)


class RunTest(PageScriptCase):
    """Through ``run_with_session``: only a run that asks for it, of a source that configures it."""

    def run_film(self, manager: AccountManager, page_script: bool):
        def action(browser):
            browser.navigate(f"https://{PORTAL}/film")
            browser.page().wait_for_timeout(500)
            return (browser.page().evaluate("window.gateOk || 0"),
                    [(item.code, item.host) for item in browser.refusals],
                    {item["domain"].lstrip(".") for item in browser.context.cookies()})
        return run_with_session(manager, "alpha", action, control=self.control, network=self.network,
                                browser_options={"page_seconds": 10}, page_script=page_script).value

    def test_only_a_run_that_asks_for_it_loads_the_configured_script(self):
        self.server.route(SCRIPT_PATH, script_reply())
        self.film(script_tag(stamped()))
        manager = self.gated_manager()
        self.assertEqual(self.run_film(manager, True), (1, [], {"alpha.example"}))  # no cookie of its host
        self.assertEqual(self.run_film(manager, False), (0, [("HOST_NOT_ALLOWED", SCRIPT_HOST)], {"alpha.example"}))
        self.assertEqual(self.run_film(self.manager, True)[:2], (0, [("HOST_NOT_ALLOWED", SCRIPT_HOST)]))  # none set
        self.assertEqual(self.server.count(SCRIPT_PATH), 1)
        with self.assertRaises(ValueError):
            run_with_session(manager, "alpha", lambda browser: None, control=self.control, network=self.network,
                             browser_options={"page_script": self.script()})


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class GatedFilmTest(PageScriptCase):
    def test_a_film_page_whose_gate_needs_the_script_can_be_clicked_only_with_it(self):
        site = ReleaseSite(body=CLIP["mkv"], film_cards=[Card("7801", "Film.Example.mkv", None)],
                           gate_script=SCRIPT_URL)
        site.install(self.server, PORTAL, TICKETS, FILES)
        self.server.route(SCRIPT_PATH, script_reply(b"window.gateOk = 1;"))
        http = SafeHttp(resolver=public_resolver, connector=self.server.connector,
                        ssl_context=DATA["tls"].client_context())
        task = self.root / "task"
        task.mkdir()
        context = ResolveContext(http=http, control=self.control, task_dir=task, ffprobe=FFPROBE)
        run = {"network": self.network, "browser_options": {"page_seconds": 3}, "run_seconds": 60,
               "grace_seconds": 2}
        manager = self.gated_manager()

        with self.assertRaises(SourceError) as caught:  # the source without its page script: the gate stays
            AccountSourceProvider(ALPHA, self.manager, reader=TicketReader(), run_options=run).resolve(
                site.url, context)
        self.assertEqual((caught.exception.code, site.posts), ("CLICK_FAILED", []))

        resolved = AccountSourceProvider(GATED, manager, reader=TicketReader(), run_options=run).resolve(
            site.url, context)
        self.assertEqual(site.posts, ["7801"])
        self.assertEqual(resolved.identity["variant"], "7801")
        self.assertEqual({token for token, _at in site.downloads}, set(site.tokens_of("7801")))
        self.assertEqual(self.hosts_seen() - {PORTAL, TICKETS, FILES, SCRIPT_HOST}, set())


if __name__ == "__main__":
    unittest.main()
