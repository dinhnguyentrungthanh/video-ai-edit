"""The session browser of a source account (docs/SOURCE_ACCOUNTS_PLAN.md, M2a, design B; results in 9.9).

Headless Edge through Playwright, for one source, with its sandbox on (``chromium_sandbox=True``; otherwise
Playwright starts it with ``--no-sandbox``) and a minimal environment. It runs in a fresh profile inside the
Windows account's private folder (``SessionVault.new_browser_profile``), deleted when the context closes; a
file held open inside it meanwhile (``pin_browser_profile``) keeps the profile and every folder above it
from being renamed or swapped. Edge never reaches the network by itself:

- Every request of a page, frame or popup goes to one route handler (``context.route``). The handler
  either refuses it (``route.abort``) or answers it with one checked exchange of ``SessionHttp``
  (``route.fulfill``). That exchange is https to a host of that source only, to public addresses with DNS
  pinned at the connection, with TLS checked, a byte limit and a deadline per request. The handler never
  uses ``route.continue_`` or ``route.fallback``; an error inside it refuses the request. A multipart
  request with a file part is refused (UPLOAD_REFUSED): Playwright leaves a file's bytes out of the body it
  hands over, so the request would go out cut short. The session browser never uploads.
- A 3xx is never handed to Edge as a redirect, because Edge follows a fulfilled redirect by itself, outside
  the interception (M2a spike). A redirected navigation gets a small page instead. That page replaces itself
  with the checked target and sends no Referer, so the next hop is a new routed request, checked like any
  other, carrying the browser's own cookies for that URL (a target that differs from the redirecting URL by
  its fragment only is loaded again with a GET). At most MAX_HOPS such pages in a row per frame.
  Refused: a redirect of a non-navigation request, a target outside the source or over http, and a 307/308
  that would resend a request body (a sign-in form) to another URL. ``navigate`` returns the final URL and
  status after the hops, as they were when that document came in (``page.goto`` alone ends at the first
  hop page).
- Not everything reaches the handler: Playwright continues a request that has no frame by itself (a shared
  worker's fetch), worker WebSockets are not routed, and Edge has background traffic of its own. The real
  guard for all of that is the black-hole proxy with the resolver rule:
  - the only proxy, loopback included, is a black hole on 127.0.0.1 that closes every connection and
    forwards nothing;
  - the host resolver maps every name to nothing;
  - QUIC, background networking, pings and DNS prefetch are off;
  - WebRTC may not use UDP outside the proxy, so it has none: PROFILE_PREFERENCES, written into the fresh
    profile before Edge starts. Edge does not read the command-line switch for it; without the preference
    STUN and TURN datagrams leave (M2a tests).
  Defence in depth on top: the WebRTC page API is removed by an init script; page WebSockets are routed to
  a mock that never connects (``WebSocketRoute.close`` inside the handler hangs Playwright 1.63, M2a
  spike); service workers are blocked, downloads refused, media and event streams aborted; Alt-Svc and
  reporting headers (NEL, Report-To, Reporting-Endpoints) are dropped.
- Cookies stay in Edge's own jar, which applies domain, path, secure, expiry and SameSite. A session is
  loaded with ``set_storage_state`` after ``own_state`` (only the source's own hosts) and read back the same
  way. One context holds one source, so a cookie of another source is never in it. Playwright adds
  Access-Control-Allow-Origin/Credentials to fulfilled cross-origin answers, so CORS between the source's
  own hosts is off (they are one trust domain).
- At most MAX_PAGES pages at once; a popup beyond that is closed.
- M7 exception A (the user chose on 2026-10-10 to keep it; AGENTS.md, Network): a hidden run that may ask for
  a ticket, of a source that configures ``page_script``, gets a ``PageScript``, and every request to that
  script's host goes to it:
  exactly that one script is answered, through its own checked client without the session's cookies, and
  never redirected; anything else on that host is refused (download_account_page_script). The sign-in
  window never has one. Requests the script makes in turn are checked like any other.

Nothing here logs. ``refusals`` keeps, in memory, the code, the host and the resource type of each refused
request, never its path, query, headers or body; errors raised to the caller (``BrowserFailed``) name the
host only; Playwright errors (whose text repeats whole URLs) are never passed on or kept as context. It does
not start while DEBUG names Playwright or PWDEBUG is set (the driver would print its protocol). For the
ticket's provenance (M7, download_account_pages) the handler also keeps, in memory and for this run only, each
navigation request's method, URL without fragment, frame and how it was answered (``navigations``, at most
MAX_NAVIGATIONS): private like every URL of a run, never logged, stored or shown. The film list and ticket
reader (M3, download_account_pages) uses ``navigate``, ``pages``, ``read`` (attributes and text of matching
elements, bounded), ``submission`` (the form owner, action and method a control would submit, for the check
right before the ticket click), ``click``, ``wait``, ``close_page``, ``storage_state``, ``navigations``,
``opener`` and ``main_frame`` only: nothing adds routes, continues a request or uses
``context.request``/``page.request`` (Node-side HTTP outside ``SessionHttp``). ``request_hook`` (the M7
acceptance observation only, download_account_observe) is called with each request's method, URL and resource
type before the handler answers it; a code it returns refuses that request. It can only refuse: it never
answers a request or lets one out.

The HTTP client covers the source's portal hosts and no host outside the source: a hidden run may leave the
file hosts out, so the session never reaches the file servers (the file is fetched by the cookie-free
downloader). Headless always, except the sign-in window (M2b): the same browser with a ``HeadedPermit``,
which only ``download_account_login.LoginCoordinator`` makes for a sign-in the user asked for. The hidden
runs (``download_account_runs.run_with_session``) have no way to ask for one. The edge pieces it starts with
(flags, preferences, environment, black hole) are in ``download_account_edge``.

A run that hangs is ended from another thread in two steps (by download_account_runs and the sign-in
coordinator, after a stop): ``force_close`` kills the run's own Edge; if the run still has not closed one grace
later, ``end_driver`` ends the run's own Playwright driver (download_account_driver: only the driver this
browser's runtime started, checked before it is ended). Its blocked call, ``context.close()`` and the runtime's
stop then fail or return at once, and ``hang`` tells which phase the run was in and where it waited.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urldefrag, urljoin

from biliflow.download_account_config import SourceAccount
# The launch pieces and the guards outside the route handler (split out in M3, unchanged), also importable
# from here under their old names.
from biliflow.download_account_edge import (  # noqa: F401 - names other modules import from here
    EDGE_ENVIRONMENT,
    HARDENING_ARGS,
    MAX_REFUSALS,
    NO_WEBRTC_SCRIPT,
    PROFILE_PREFERENCES,
    BlackHoleProxy,
    BrowserFailed,
    BrowserUnavailable,
    Refusal,
    _driver_debug_on,
    _host_of,
    _is_upload,
    _never_connect,
    _playwright_error,
    _playwright_timeout,
    edge_environment,
    edge_processes,
    failure_kind,
    hop_page,
    indexed_db_cause,
    write_preferences,
)
from biliflow.download_account_driver import DriverHang, DriverProcess, blocked_at, driver_popen
from biliflow.download_account_http import SessionHttp, SessionReply
from biliflow.download_account_page_script import PageScript
from biliflow.download_account_vault import SessionVault, VaultError
from biliflow.download_accounts import LoginAttempt, check_storage_state, own_state
from biliflow.download_http import Cancelled, HttpError, Interruptible
from biliflow.download_runner import kill_process_tree

LAUNCH_SECONDS = 30.0
PAGE_SECONDS = 30.0  # Playwright's default wait of one page action, and ``navigate``'s
MAX_HOPS = 10  # hop pages in a row in one frame (Chromium allows 20 HTTP redirects, SafeHttp follows 5)
MAX_PAGES = 8  # pages open at once in one context, popups included
MAX_NAVIGATIONS = 200  # navigation requests one run keeps (``navigations``); past it the record says it is cut
MAX_WAIT_SECONDS = 600.0  # the most ``launch_seconds`` or ``page_seconds`` may be
MAX_READ_ITEMS = 2000  # elements one ``read`` returns
MAX_READ_CHARS = 2048  # of one attribute value, and READ_TEXT_CHARS of an element's text
READ_TEXT_CHARS = 500
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Resource types answered through SessionHttp; anything else (media, websocket, eventsource, ping,
# manifest, texttrack, prefetch, other) is refused.
ANSWERED_TYPES = frozenset({"document", "stylesheet", "script", "image", "font", "xhr", "fetch"})
# Answered in the user's sign-in window only: a hidden run's readers read text and structure, and every answer
# is one more exchange through SessionHttp, one at a time (the measured cost of a hidden run is its requests).
HIDDEN_SKIPPED_TYPES = frozenset({"image", "font"})
# No HTTP/3 hint (nothing may leave over QUIC) and no reporting endpoint (reports are sent in the background).
DROPPED_RESPONSE_HEADERS = frozenset({"alt-svc", "nel", "report-to", "reporting-endpoints"})
_ABORT_CODES = {"TIMEOUT": "timedout", "CANCELLED": "aborted", "NETWORK": "connectionfailed",
                "TLS_ERROR": "connectionfailed", "DNS_FAILED": "namenotresolved"}


# The attributes and text of matching elements: what ``read`` returns, nothing else of the page.
READ_SCRIPT = """(elements, [names, limit, valueChars, textChars]) => elements.slice(0, limit).map(element => {
  const item = {text: String(element.innerText || element.textContent || '').trim().slice(0, textChars)};
  for (const name of names) {
    let value = element.getAttribute(name);
    if (name === 'href' && value !== null) {
      try { value = new URL(value, document.baseURI).href; } catch (e) { value = null; }
    }
    item[name] = value === null ? null : String(value).slice(0, valueChars);
  }
  return item;
})"""

# What submitting the form of each matching control would send (``submission``): the control, its own form
# overrides and its form owner (the ``form`` attribute's form, else the closest form), all read as attributes,
# never through the form's IDL properties (an input named "action" or "method" shadows those).
SUBMISSION_SCRIPT = """(elements, [limit, valueChars]) => elements.slice(0, limit).map(element => {
  const attr = (node, name) => node && node.hasAttribute(name) ? String(node.getAttribute(name)).slice(0, valueChars)
                                                               : null;
  const named = element.hasAttribute('form') ? document.getElementById(element.getAttribute('form'))
                                             : element.closest('form');
  const form = named && named.tagName === 'FORM' ? named : null;
  return {
    tag: element.tagName.toLowerCase(), type: attr(element, 'type'),
    disabled: element.hasAttribute('disabled') || !!element.closest('fieldset[disabled]'),
    formaction: attr(element, 'formaction'), formmethod: attr(element, 'formmethod'),
    formtarget: attr(element, 'formtarget'), onclick: element.hasAttribute('onclick'),
    form: !!form, action: attr(form, 'action'), method: attr(form, 'method'), target: attr(form, 'target'),
    onsubmit: !!form && form.hasAttribute('onsubmit'),
    document: String(document.URL).slice(0, valueChars), base: String(document.baseURI).slice(0, valueChars)
  };
})"""
FORM_METHODS = ("get", "post", "dialog")


def effective_submission(item: Mapping[str, Any]) -> tuple[str, str] | None:
    """The request a click on one control read by ``submission`` sends, as HTML defines it: (absolute action URL
    without its fragment, method in capitals); None when the control does not submit a form (not a submit
    control, disabled, no form owner, a ``dialog`` method, an action that is not a URL). ``formaction`` and
    ``formmethod`` of the control win over its form's; an empty or missing action is the document's URL; a
    missing or unknown method is GET."""
    tag, kind = item.get("tag"), (item.get("type") or "").strip().lower()
    submits = (tag == "button" and kind not in ("reset", "button")) or (tag == "input" and kind in ("submit", "image"))
    if not submits or item.get("disabled") or not item.get("form"):
        return None
    action = item.get("formaction") if item.get("formaction") is not None else item.get("action")
    action = (action or "").strip()
    method = item.get("formmethod") if item.get("formmethod") is not None else item.get("method")
    method = (method or "").strip().lower()
    method = method if method in FORM_METHODS else "get"
    if method == "dialog":
        return None
    try:
        url = str(item.get("document") or "") if not action else urljoin(str(item.get("base") or ""), action)
    except ValueError:
        return None
    return urldefrag(url)[0], method.upper()


@dataclass(frozen=True)
class HeadedPermit:
    """Leave for a visible window: made only by the sign-in coordinator (download_account_login) for the
    sign-in the user asked for, which ``attempt`` is. A hidden run never has one, so it is always headless."""
    attempt: LoginAttempt = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, LoginAttempt):
            raise TypeError("A visible window needs the user's sign-in attempt")


@dataclass(frozen=True)
class Navigation:
    """Where ``navigate`` ended: the final URL (after the hop pages) and its HTTP status."""
    url: str = field(repr=False)
    status: int


@dataclass(frozen=True)
class NavigationStep:
    """One navigation request the route handler saw in this run, numbered in order (private like every URL of a
    run: never logged, stored or shown). ``url`` is without its fragment; ``frame`` the frame it was for, None
    for a popup's first request (it comes before its frame exists). How it was answered: ``target`` (a
    redirect, shown as a hop page to that checked URL), ``status`` (a document) or ``refused`` (its code)."""
    number: int
    method: str
    url: str = field(repr=False)
    frame: Any = field(default=None, repr=False, compare=False)
    target: str | None = field(default=None, repr=False)
    status: int | None = None
    refused: str | None = None


@dataclass
class _FrameState:
    hops: int = 0
    status: int | None = None  # the status of the last answered navigation (not a hop page)
    url: str = ""  # that answer's URL, without a fragment
    # The first such document that came into the frame (set once): its URL and status then.
    landed: Navigation | None = None
    refused: str | None = None


class SessionBrowser:
    """See the module docstring. Use as a context manager; one thread (Playwright's sync API).
    ``profile_left`` is True when the profile could not be deleted at close (the manager's
    ``clear_browser_profiles`` deletes it; M4 calls that at start). Headless unless ``headed`` is the
    ``HeadedPermit`` of the user's sign-in (M2b); everything else (profile, network, sandbox) is the same.
    ``stop_at`` (``time.monotonic``): one deadline for the whole run (the sign-in's): the launch, every
    ``navigate`` and ``pause`` end by then and the handler refuses every request after it."""

    INIT_SCRIPTS: tuple[str, ...] = (NO_WEBRTC_SCRIPT,)

    def __init__(self, source: SourceAccount, http: SessionHttp, vault: SessionVault, control: Interruptible, *,
                 state: Mapping[str, Any] | None = None, launch_seconds: float = LAUNCH_SECONDS,
                 page_seconds: float = PAGE_SECONDS, headed: HeadedPermit | None = None,
                 stop_at: float | None = None, page_script: PageScript | None = None,
                 before_click: Callable[[], None] | None = None,
                 request_hook: Callable[[str, str, str], str | None] | None = None):
        if not set(source.hosts["portal"]) <= http.hosts <= source.all_hosts:
            raise ValueError("The session HTTP client must cover the source's portal and no host outside it")
        if headed is not None and not isinstance(headed, HeadedPermit):
            raise TypeError("A visible window needs a HeadedPermit (the user's sign-in), not a flag")
        if headed is not None and headed.attempt.source_id != source.id:
            raise ValueError("The sign-in attempt belongs to another source")
        if page_script is not None and (headed is not None or page_script.host in source.all_hosts):
            raise ValueError("A page script is for hidden runs only, on a host that is not the source's")
        self.headed = headed
        self.page_script = page_script  # M7 exception A: hidden runs of a source that configures one only
        # Called right before every click of the run (the acceptance probe records that a ticket may be asked
        # for; download_account_probe); when it raises, nothing is clicked.
        self.before_click = before_click
        # Called with (method, URL, resource type) for every request the handler is about to answer (after the
        # stop and deadline checks); a code it returns refuses that request. Only the acceptance observation
        # passes one (download_account_observe: it counts requests and refuses file and player paths); it can
        # refuse more, never allow anything the checks below would refuse.
        if request_hook is not None and not callable(request_hook):
            raise TypeError("request_hook must be callable")
        self.request_hook = request_hook
        for name, value in (("launch_seconds", launch_seconds), ("page_seconds", page_seconds)):
            if not 0 < value <= MAX_WAIT_SECONDS:
                raise ValueError(f"{name} must be above 0 and at most {MAX_WAIT_SECONDS:g}")
        self.source = source
        self.http = http
        self.vault = vault
        self.control = control
        self._state = None if state is None else own_state(source, check_storage_state(state))
        self.launch_seconds = launch_seconds
        self.page_seconds = page_seconds
        self.refusals: list[Refusal] = []
        self.proxy: BlackHoleProxy | None = None
        self.context: Any = None
        self.profile_left = False
        self._runtime: Any = None
        self._profile: Any = None
        self._pin: Any = None
        self.stop_at = stop_at
        # Requests after it are refused: ``stop_at``, or the running ``navigate``'s own deadline when sooner.
        self._deadline: float | None = stop_at
        self._frames: dict[Any, _FrameState] = {}
        self._navigations: list[NavigationStep] = []
        self._navigation_number = 0
        self.navigations_dropped = False  # a navigation request came after MAX_NAVIGATIONS and was not kept
        self._watched: set[Any] = set()
        self._closed = False  # set when the close begins (the route handler refuses everything from then on)
        self._finished = False  # set when the close has ended: no driver of this run is left to end
        self._context_closed = False  # the browser went away (the user closed the last window)
        self._playwright: Any = None  # this run's own ``sync_playwright()``, which starts its own driver
        self._greenlet: Any = None  # the run thread's greenlet: where the run waits while the driver answers
        self._thread_id: int | None = None  # the run thread, when that greenlet itself waits (the runtime's stop)
        self._phase = "new"  # open, page, storage_state, close-context, close-runtime, closed
        # The run's step for a failure's report (fixed words: LAUNCH, LOAD_SESSION, PAGE, the page readers' own
        # steps, SAVE_SESSION); the readers set theirs (download_account_pages).
        self.step = "LAUNCH"
        self.launch_taken = 0.0  # seconds from the start of ``open`` until the session was loaded
        self.answered = 0  # requests answered through SessionHttp or the page script's client
        self.network_seconds = 0.0  # the time those exchanges took, one after another
        self.skipped = 0  # HIDDEN_SKIPPED_TYPES requests not fetched
        self.indexed_db_kept = False  # the state was read without IndexedDB (``_state_without_indexed_db``)
        self.session_loaded = 0  # how the saved state was loaded: 0 whole, 1 without IndexedDB, 2 cookies only
        self._loaded_origins: list[Any] = []  # the origin storage that loaded (``_load_session``)
        # ``indexed_db_cause`` of the IndexedDB failure when the session was loaded, and when it was read.
        self.load_cause = ""
        self.save_cause = ""
        self.driver: DriverProcess | None = None
        self.hang: DriverHang | None = None  # set when ``end_driver`` had to end the driver

    def __enter__(self) -> SessionBrowser:
        try:
            self.open()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def open(self) -> None:
        try:
            from greenlet import getcurrent
            from playwright.sync_api import Error as PlaywrightError, sync_playwright
        except ImportError:
            raise BrowserUnavailable("Thiếu Playwright trong môi trường BiliFlow.") from None
        if _driver_debug_on():
            raise BrowserUnavailable("Tắt DEBUG/PWDEBUG của Playwright trước khi dùng phiên nguồn.")
        self._greenlet = getcurrent()
        self._thread_id = threading.get_ident()
        self._phase = "open"
        self.step = "LAUNCH"
        started = time.monotonic()
        self.proxy = BlackHoleProxy()
        self._profile = self.vault.new_browser_profile()
        # Held open until Edge has gone: neither the profile nor a folder above it can be renamed meanwhile.
        self._pin = self.vault.pin_browser_profile(self._profile)
        profile = str(self._profile)
        write_preferences(self._profile)
        failure = ""
        try:
            self._playwright = sync_playwright()
            self._runtime = self._playwright.start()
            self.driver = DriverProcess.record(driver_popen(self._playwright))  # this run's own, as it started
            if self.driver is None:  # a run whose driver is unknown could hang for ever: it is not started
                failure = "DriverUnknown"
            else:
                self.context = self._launch(profile)
                self._set_up()
        except PlaywrightError as error:
            failure = type(error).__name__  # raised below, outside the handler: its text is not kept as context
        if failure:
            raise BrowserUnavailable(f"Không mở được Edge ({failure}).")
        if self._state is not None:
            self.step = "LOAD_SESSION"
            failed = self._load_session()
            if failed:
                raise BrowserFailed("SESSION_LOAD_FAILED", detail=failed)
        self._phase = "page"
        self.step = "PAGE"
        self.launch_taken = time.monotonic() - started

    def _load_session(self) -> str:
        """Load the saved state into the context; the kind of the failure (``failure_kind``), or "". When
        Playwright cannot restore an origin's IndexedDB (seen on the first real source with a state saved after a
        ticket), the state is loaded again without IndexedDB, then with its cookies only. Each stage loads less of
        the same state, never anything else. Playwright restores every origin it has already visited too, the
        failed stage's own included, so the last stage only adds the cookies. ``session_loaded`` keeps the stage,
        ``_loaded_origins`` what loaded of the origins' storage. A run that loaded less saves less: what did not
        restore would fail the same way in every later run, and the smaller state loads whole (a token that lived
        only there then asks for a new sign-in, as it would have anyway)."""
        assert self._state is not None
        cookies = self._state["cookies"]
        stages = (self._state.get("origins") or [],
                  [{key: value for key, value in item.items() if key != "indexedDB"}
                   for item in self._state.get("origins") or []])
        for stage, origins in enumerate(stages):
            if stage and origins == stages[0]:
                continue  # no origin had IndexedDB: the same state again would fail the same way
            failed = ""
            try:
                self.context.set_storage_state({"cookies": cookies, "origins": origins})
            except _playwright_error() as error:
                failed = failure_kind(error)  # its text may repeat a cookie or an origin of the session
                if failed == "INDEXEDDB" and not self.load_cause:
                    self.load_cause = indexed_db_cause(error)
            if not failed:
                self.session_loaded, self._loaded_origins = stage, origins
                return ""
            if failed != "INDEXEDDB":
                return failed
        try:
            self.context.clear_cookies()
            self.context.add_cookies(cookies)
        except _playwright_error() as error:
            return failure_kind(error)
        self.session_loaded = 2
        return ""

    def _set_up(self) -> None:
        """The context's guards: deadline, init scripts, the route handler for every request and socket."""
        self.context.on("close", self._on_context_closed)
        self.context.set_default_timeout(self.page_seconds * 1000)
        for script in self.INIT_SCRIPTS:
            self.context.add_init_script(script)
        self.context.route_web_socket("**/*", _never_connect)
        self.context.route("**/*", self._handle)
        self.context.on("page", self._on_page)
        for page in self.context.pages:
            self._watch(page)

    def _launch(self, profile: str) -> Any:
        """Edge in ``profile``, through this run's own runtime."""
        return self._runtime.chromium.launch_persistent_context(
            profile, channel="msedge", headless=self.headed is None, chromium_sandbox=True,
            args=list(HARDENING_ARGS),
            proxy={"server": self.proxy.server, "bypass": "<-loopback>"}, service_workers="block",
            accept_downloads=False, downloads_path=profile, artifacts_dir=profile,
            env=edge_environment(profile), timeout=self._limit(self.launch_seconds) * 1000)

    def page(self) -> Any:
        """The context's first page (a persistent context starts with one)."""
        pages = self.context.pages
        return pages[0] if pages else self.context.new_page()

    def navigate(self, url: str, page: Any = None) -> Navigation:
        """Open ``url`` in ``page`` (the first page by default) and wait until the document of the final
        answer (not a hop page) is in the page and its DOM is ready. The shown document's own URL with a
        fragment only scrolls (no request): it returns at once, with that document's status. Cancelled on a
        stop or a cancel; BrowserFailed (host only) when the URL is out of scope, a step is refused, it times
        out or the browser fails."""
        host = _host_of(url)
        try:
            self.http.in_scope(url)
        except HttpError as error:
            raise BrowserFailed(error.code, host) from None
        page = page or self.page()
        self._watch(page)
        shown = self._frames.get(page.main_frame)
        if (not self.control.requested and shown is not None and shown.status is not None and "#" in url
                and urldefrag(url)[0] == urldefrag(page.url)[0] == shown.url):
            return self._scroll_to(page, url, shown.status, host)
        state = self._frames[page.main_frame] = _FrameState()
        # The whole navigation, hops and the page's own requests included. Handlers run one at a time, so
        # ``_land`` sees the time only between them: the handler itself refuses what comes after it.
        self._deadline = time.monotonic() + self._limit(self.page_seconds)
        try:
            landed, refused, failure = self._land(page, url, state, self._deadline)
        finally:
            self._deadline = self.stop_at
        if self.control.requested or (landed is None and refused == "CANCELLED"):
            raise Cancelled()
        if landed is None and refused is not None:  # a refusal after the landing is the page's own next step
            raise BrowserFailed(refused, host)
        if failure is not None:
            raise BrowserFailed(failure, host)
        if landed is None:
            raise BrowserFailed("NAVIGATION_TIMEOUT", host)
        return landed

    @staticmethod
    def _scroll_to(page: Any, url: str, status: int, host: str) -> Navigation:
        """``url`` is the shown document's own URL with a fragment: the browser only scrolls (no request, no
        new document), so it is not waited for like a navigation. ``status`` is the shown document's."""
        failed = False
        try:
            page.goto(url, wait_until="commit")
        except _playwright_error():
            failed = True  # its text names the URL
        if failed:
            raise BrowserFailed("BROWSER_FAILED", host)
        return Navigation(page.url, status)

    def _land(self, page: Any, url: str, state: _FrameState,
              deadline: float) -> tuple[Navigation | None, str | None, str | None]:
        """``navigate``'s wait: where it landed, the refusal that ended it, and the code of a failure (the DOM
        not ready in time, or the browser failed)."""
        try:
            page.goto(url, wait_until="commit")
        except _playwright_error():
            pass  # a refused step leaves its code in the frame's state; anything else times out below
        landed: Navigation | None = None
        refused: str | None = None
        try:
            while state.refused is None and state.landed is None:
                if self.control.requested or time.monotonic() > deadline:
                    break
                page.wait_for_timeout(50)
            landed, refused = state.landed, state.refused  # the page may navigate on by itself from here
            if landed is not None:
                left = max(deadline - time.monotonic(), 0.001)  # 0 would mean no limit to Playwright
                page.wait_for_load_state("domcontentloaded", timeout=left * 1000)
        except _playwright_timeout():
            return landed, refused, "NAVIGATION_TIMEOUT"
        except _playwright_error():  # the page closed or the browser went away; its text names URLs
            return landed, refused, "BROWSER_FAILED"
        return landed, refused, None

    def storage_state(self) -> dict[str, Any]:
        """The context's state, only what belongs to the source's own hosts (``own_state``): cookies,
        localStorage and IndexedDB (a session that keeps its token there is not lost; ``set_storage_state``
        loads it back)."""
        state, kind = None, "OTHER"
        self._phase = "storage_state"
        self.step = "SAVE_SESSION"
        try:
            state = self.context.storage_state(indexed_db=True)
        except _playwright_error() as error:
            kind = failure_kind(error)  # e.g. a closed page's origin whose IndexedDB Playwright cannot read
            if kind == "INDEXEDDB":
                self.save_cause = indexed_db_cause(error)
        finally:
            self._phase = "page"
        if state is None and kind == "INDEXEDDB":
            state = self._state_without_indexed_db()
        if state is None:
            raise BrowserFailed("SESSION_READ_FAILED", detail=kind)
        return own_state(self.source, check_storage_state(state))

    def _state_without_indexed_db(self) -> dict[str, Any] | None:
        """The context's cookies when Playwright could not read an origin's IndexedDB (it reopens a closed page's
        origin to read it; a ticket page's did not read on the first real source), with only the origins this
        run loaded (``_loaded_origins``): each keeps the IndexedDB it loaded and takes its localStorage now.
        An origin first seen in this run is left out: Playwright could not read it, and on the first real source
        it could not restore it either, so a state holding it no longer loaded. A run that loaded no state (the
        sign-in window) keeps the localStorage it read. None when this read fails too."""
        self._phase = "storage_state"
        try:
            state = self.context.storage_state()
        except _playwright_error():
            return None
        finally:
            self._phase = "page"
        if not isinstance(state, dict):
            return None
        self.indexed_db_kept = True
        if self._state is None:
            return state
        now = {item.get("origin"): item.get("localStorage") or [] for item in state.get("origins") or []
               if isinstance(item, dict)}
        origins = [{**item, "localStorage": now.get(item.get("origin"), [])} for item in self._loaded_origins]
        return {**state, "origins": origins}

    @property
    def window_closed(self) -> bool:
        """No page is left, or the browser went away: in the sign-in window, the user closed it."""
        if self.context is None or self._context_closed:
            return True
        try:
            return not self.context.pages
        except _playwright_error():
            return True

    def pause(self, seconds: float) -> bool:
        """Let the browser handle its events for ``seconds`` (the sync API runs them only while Playwright is
        called). False when no page is left to wait on."""
        if self.window_closed:
            return False
        try:
            self.context.pages[0].wait_for_timeout(self._limit(max(seconds, 0.001)) * 1000)
        except _playwright_error():  # that page closed meanwhile
            return not self.window_closed
        return True

    def force_close(self) -> int:
        """Kill this run's Edge, from any thread: the last resort when a Playwright call hangs (a page stuck in
        a script, a hung browser), which no deadline or cancel can end. The blocked call then fails in the run's
        own thread, which closes the rest as usual. Only the processes of this run's own profile are touched;
        returns how many were found."""
        profile = self._profile
        if profile is None:
            return 0
        pids = edge_processes(profile)
        for pid in pids:
            kill_process_tree(pid)
        return len(pids)

    def end_driver(self) -> bool:
        """End this run's own Playwright driver, from any thread: the step after ``force_close`` for a run that
        still hangs because the driver did not see its Edge go (download_account_driver). Only the driver this
        browser's runtime started, checked again before it is ended; nothing once the browser has closed, and
        no Playwright call. Keeps ``hang``: the phase the run was in and where it waited. True when ended."""
        runtime = self._playwright
        if runtime is None or self._finished:
            return False
        if self.driver is None:  # still starting: recorded now, from the same runtime
            self.driver = DriverProcess.record(driver_popen(runtime))
        driver = self.driver
        if driver is None:
            return False
        hang = DriverHang(self._phase, blocked_at(self._greenlet, thread_id=self._thread_id))

        def report() -> None:  # before the kill: the freed call may end the run at once
            self.hang = hang
        if not driver.end(before=report):
            if self.hang is hang:
                self.hang = None
            return False
        try:
            self.force_close()  # an Edge started after the first kill (a slow launch) goes too
        except Exception:  # noqa: BLE001 - psutil only; the run closes the rest
            pass
        return True

    # Page actions of the film list and ticket reader (M3) ------------------------------------------------

    def pages(self) -> list[Any]:
        """The open pages, the first one and popups, oldest first ([] once the browser has gone)."""
        if self.context is None:
            return []
        try:
            return list(self.context.pages)
        except _playwright_error():
            return []

    def read(self, page: Any, selector: str, attributes: tuple[str, ...] = (),
             limit: int = MAX_READ_ITEMS) -> list[dict[str, str | None]]:
        """The elements matching ``selector`` in ``page``, at most ``limit``: each as its ``attributes`` (an
        ``href`` resolved against the page) and its ``text``, all cut short. BrowserFailed (host only) when the
        page has gone or the selector is wrong. No timeout of its own: a page stuck in a script is ended by the
        run's deadline (download_account_runs)."""
        host, failed = _host_of(self.page_url(page)), False
        try:
            items = page.eval_on_selector_all(selector, READ_SCRIPT, [list(attributes), max(0, min(limit,
                                              MAX_READ_ITEMS)), MAX_READ_CHARS, READ_TEXT_CHARS])
        except _playwright_error():
            failed = True  # its text names the page's URL
        if failed or not isinstance(items, list):
            raise BrowserFailed("PAGE_READ_FAILED", host)
        return [{key: value if value is None else str(value) for key, value in item.items()}
                for item in items if isinstance(item, dict)]

    def submission(self, page: Any, selector: str, limit: int = 2) -> list[dict[str, Any]]:
        """What clicking each control matching ``selector`` would submit (``SUBMISSION_SCRIPT``; at most
        ``limit`` controls): attributes only, ``effective_submission`` turns one into its request. BrowserFailed
        (host only) when the page has gone or the selector is wrong. Like ``read``, the page's own scripts run
        in the same world and could lie; the click's request itself is checked again afterwards (the ticket's
        provenance, download_account_pages)."""
        host, failed = _host_of(self.page_url(page)), False
        try:
            items = page.eval_on_selector_all(selector, SUBMISSION_SCRIPT, [max(0, min(limit, MAX_READ_ITEMS)),
                                                                            MAX_READ_CHARS])
        except _playwright_error():
            failed = True  # its text names the page's URL
        if failed or not isinstance(items, list):
            raise BrowserFailed("PAGE_READ_FAILED", host)
        return [dict(item) for item in items if isinstance(item, dict)]

    def click(self, page: Any, selector: str) -> None:
        """Click the first element matching ``selector`` as a user would (it must be visible and enabled),
        within the page wait and the run's deadline; BrowserFailed (host only) when it cannot be."""
        host, failed = _host_of(self.page_url(page)), False
        if self.before_click is not None:
            self.before_click()
        try:
            page.locator(selector).first.click(timeout=self._limit(self.page_seconds) * 1000, no_wait_after=True)
        except _playwright_error():
            failed = True
        if failed:
            raise BrowserFailed("CLICK_FAILED", host)

    def wait(self, page: Any, seconds: float) -> bool:
        """Let ``page`` run for ``seconds`` (cut to the run's deadline); False when it has closed."""
        try:
            page.wait_for_timeout(self._limit(max(seconds, 0.001)) * 1000)
        except _playwright_error():
            return False
        return True

    def close_page(self, page: Any) -> None:
        try:
            page.close()
        except _playwright_error():  # already closed
            pass

    @staticmethod
    def page_url(page: Any) -> str:
        """The page's current URL ('' when it has gone); private, like every URL of a run."""
        try:
            return page.url or ""
        except _playwright_error():
            return ""

    @property
    def navigation_number(self) -> int:
        """The number of the last navigation request the route handler saw (0 before the first)."""
        return self._navigation_number

    def navigations(self, after: int = 0) -> tuple[NavigationStep, ...]:
        """The navigation requests of this run numbered above ``after``, oldest first (``NavigationStep``).
        Whether the record is whole: ``navigations_dropped``."""
        return tuple(step for step in self._navigations if step.number > after)

    @staticmethod
    def opener(page: Any) -> Any:
        """The page that opened ``page`` (a popup's), or None."""
        try:
            return page.opener()
        except _playwright_error():
            return None

    @staticmethod
    def main_frame(page: Any) -> Any:
        try:
            return page.main_frame
        except _playwright_error():
            return None

    def _limit(self, seconds: float) -> float:
        """``seconds``, cut to what is left before ``stop_at``; never 0 (that means no limit to Playwright)."""
        if self.stop_at is None:
            return seconds
        return max(min(seconds, self.stop_at - time.monotonic()), 0.001)

    def _on_context_closed(self, context: Any) -> None:
        self._context_closed = True

    def close(self) -> None:
        """Close Edge and Playwright (their errors are dropped: the browser goes away either way), then the black
        hole, then delete the profile (``profile_left`` when that fails). Only then is the browser finished:
        the context's close and the runtime's stop (which waits for the driver to exit) can hang as well, and
        ``end_driver`` stays possible until they returned."""
        self._closed = True
        try:
            for phase, stop in (("close-context", self.context.close if self.context is not None else None),
                                ("close-runtime", self._runtime.stop if self._runtime is not None else None)):
                if stop is None:
                    continue
                self._phase = phase
                try:
                    stop()
                except Exception:  # noqa: BLE001 - best effort: the runtime still stops, the profile still goes
                    pass
        finally:
            self.context = self._runtime = None
            if self.proxy is not None:
                self.proxy.close()
            if self._pin is not None:
                try:
                    self._pin.close()
                except OSError:  # an empty file opened for its handle only: nothing is lost
                    pass
                self._pin = None
            if self._profile is not None:  # this run's own profile folder, nothing else
                try:
                    self.profile_left = not self.vault.remove_browser_profile(self._profile)
                except VaultError:
                    self.profile_left = True
                self._profile = None
            self._phase = "closed"
            self._finished = True

    # Pages and the route handler ----------------------------------------------------------------------

    def _on_page(self, page: Any) -> None:
        if self.context is not None and len(self.context.pages) > MAX_PAGES:
            page.close()
            return
        self._watch(page)

    def _watch(self, page: Any) -> None:
        if page not in self._watched:
            self._watched.add(page)
            page.on("framenavigated", self._on_navigated)

    def _on_navigated(self, frame: Any) -> None:
        """A document came into ``frame``: the final answer's when that answer was given and the frame has its
        URL; its URL and status are kept as they were then. Events arrive in order, so a hop page's commit
        always comes before the request it leads to; the URL check holds for a hop page with another URL too."""
        state = self._frames.get(frame)
        if (state is not None and state.status is not None and state.landed is None
                and urldefrag(frame.url)[0] == state.url):
            state.landed = Navigation(frame.url, state.status)

    def _frame_state(self, request: Any) -> _FrameState | None:
        if not request.is_navigation_request():
            return None
        try:
            frame = request.frame
        except _playwright_error():  # a popup's first request comes before its frame exists: counted alone
            return _FrameState()
        return self._frames.setdefault(frame, _FrameState())

    def _note_navigation(self, request: Any, *, target: str | None = None, status: int | None = None,
                         refused: str | None = None) -> None:
        """Keep one navigation request and how it was answered (``navigations``), in memory only."""
        try:
            if not request.is_navigation_request():
                return
            method, url = request.method, urldefrag(request.url)[0]
        except _playwright_error():
            return
        try:
            frame = request.frame
        except _playwright_error():  # a popup's first request comes before its frame exists
            frame = None
        self._navigation_number += 1
        if len(self._navigations) >= MAX_NAVIGATIONS:
            self.navigations_dropped = True
            return
        self._navigations.append(NavigationStep(self._navigation_number, method, url, frame, target, status, refused))

    def _refuse(self, route: Any, code: str, host: str) -> None:
        request = route.request
        if len(self.refusals) < MAX_REFUSALS:
            self.refusals.append(Refusal(code, host, request.resource_type))
        state = self._frame_state(request)
        if state is not None:
            state.refused = code
        self._note_navigation(request, refused=code)
        route.abort(_ABORT_CODES.get(code, "blockedbyclient"))

    def _count_exchange(self, started: float) -> None:
        self.answered += 1
        self.network_seconds += time.monotonic() - started

    def run_stats(self) -> dict[str, float | int | str]:
        """How the run went: numbers and fixed words only (download_account_sources writes them to the task's
        log)."""
        return {"launch_seconds": round(self.launch_taken, 1), "answered": self.answered,
                "network_seconds": round(self.network_seconds, 1), "skipped": self.skipped,
                "indexed_db_kept": int(self.indexed_db_kept), "session_loaded": self.session_loaded,
                "load_cause": self.load_cause, "save_cause": self.save_cause}

    def _handle(self, route: Any) -> None:
        try:
            self._answer(route)
        except Exception:  # noqa: BLE001 - a bug or a closed page must never leave a request hanging or continue it
            try:
                self._refuse(route, "HANDLER_ERROR", _host_of(route.request.url))
            except Exception:  # noqa: BLE001 - refused without the bookkeeping, or the page is gone already
                try:
                    route.abort("blockedbyclient")
                except Exception:  # noqa: BLE001
                    pass

    def _answer(self, route: Any) -> None:
        request = route.request
        url, kind = request.url, request.resource_type
        host = _host_of(url)
        if self._closed or self.control.requested:
            self._refuse(route, "CANCELLED", host)
            return
        if self._deadline is not None and time.monotonic() > self._deadline:
            self._refuse(route, "TIMEOUT", host)
            return
        if self.request_hook is not None:
            refused = self.request_hook(request.method, url, kind)
            if refused is not None:
                self._refuse(route, str(refused)[:40], host)
                return
        if self.page_script is not None and self.page_script.owns(url):
            self._answer_page_script(route, host)
            return
        if kind not in ANSWERED_TYPES:
            self._refuse(route, "TYPE_BLOCKED", host)
            return
        body = request.post_data_buffer
        if _is_upload(request.headers, body):
            self._refuse(route, "UPLOAD_REFUSED", host)
            return
        try:
            host = self.http.in_scope(url)
        except HttpError as error:
            self._refuse(route, error.code, host)
            return
        if self.headed is None and kind in HIDDEN_SKIPPED_TYPES:  # after every refusal above, which it keeps
            self.skipped += 1  # not a refusal (``refusals`` keeps those): nothing is wrong with the request
            route.abort("blockedbyclient")
            return
        started = time.monotonic()
        try:
            reply = self.http.send(url, self.control, method=request.method, headers=request.headers, body=body)
        except Cancelled:
            self._refuse(route, "CANCELLED", host)
            return
        except HttpError as error:
            self._refuse(route, error.code, host)
            return
        finally:
            self._count_exchange(started)
        if reply.status in REDIRECT_STATUSES:
            self._redirect(route, reply, host)
            return
        state = self._frame_state(request)
        if state is not None:
            state.hops, state.status, state.url, state.refused = 0, reply.status, urldefrag(url)[0], None
        self._note_navigation(request, status=reply.status)
        route.fulfill(status=reply.status, headers=self._headers(reply.headers), body=reply.body)

    def _answer_page_script(self, route: Any, host: str) -> None:
        """A request to the host of the source's page script (download_account_page_script): exactly that
        script, through its own checked client, without the session's cookies, never redirected; anything
        else on that host is refused."""
        request = route.request
        script = self.page_script
        assert script is not None
        code = script.refusal(request)
        if code is not None:
            self._refuse(route, code, host)
            return
        started = time.monotonic()
        try:
            reply = script.fetch(request.headers, self.control)  # the configured URL: the page's query stays here
        except Cancelled:
            self._refuse(route, "CANCELLED", host)
            return
        except HttpError as error:
            self._refuse(route, error.code, host)
            return
        finally:
            self._count_exchange(started)
        if reply.status in REDIRECT_STATUSES:
            self._refuse(route, "REDIRECT_REFUSED", host)  # never followed, even to the same URL
            return
        if reply.status != 200 or not script.javascript(reply):
            self._refuse(route, "PAGE_SCRIPT_FAILED", host)
            return
        route.fulfill(status=200, headers={"content-type": reply.header("Content-Type"), "cache-control": "no-store"},
                      body=reply.body)  # no other header of the answer: no Set-Cookie reaches the jar

    def _redirect(self, route: Any, reply: SessionReply, host: str) -> None:
        request = route.request
        state = self._frame_state(request)
        location = reply.header("Location")
        if not location:
            self._refuse(route, "BAD_REDIRECT", host)
            return
        if state is None or (reply.status in (307, 308) and request.method not in ("GET", "HEAD")):
            self._refuse(route, "REDIRECT_REFUSED", host)  # not a navigation, or would resend the body
            return
        try:
            target = urljoin(request.url, location)
        except ValueError:
            self._refuse(route, "BAD_REDIRECT", host)
            return
        try:
            self.http.in_scope(target)
        except HttpError as error:
            self._refuse(route, error.code, _host_of(target))
            return
        state.hops += 1
        if state.hops > MAX_HOPS:
            self._refuse(route, "TOO_MANY_REDIRECTS", host)
            return
        state.status = None
        headers = {"content-type": "text/html; charset=utf-8", "cache-control": "no-store",
                   "referrer-policy": "no-referrer"}
        cookies = reply.values("Set-Cookie")
        if cookies:  # set by the redirecting URL itself, which is the URL of this page
            headers["set-cookie"] = "\n".join(cookies)
        self._note_navigation(request, target=target)
        route.fulfill(status=200, headers=headers, body=hop_page(target))

    @staticmethod
    def _headers(received: tuple[tuple[str, str], ...]) -> dict[str, str]:
        headers: dict[str, str] = {}
        cookies: list[str] = []
        for name, value in received:
            key = name.lower()
            if key in DROPPED_RESPONSE_HEADERS:
                continue
            if key == "set-cookie":
                cookies.append(value)
            elif key in headers:
                headers[key] = f"{headers[key]}, {value}"
            else:
                headers[key] = value
        if cookies:
            headers["set-cookie"] = "\n".join(cookies)
        return headers
