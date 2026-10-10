"""The M6 full-stack harness of source accounts (docs/SOURCE_ACCOUNTS_PLAN.md, M6): helpers only, no test.

``E2EBase`` (a TestCase without tests; the E2E modules subclass it) builds one temporary Control Center:

- a root ``m6-e2e-*`` under the install's temp/ (checked before every recursive delete), with its own
  downloads database, Control Center database, vault (fake protector and ACL of one made-up Windows account,
  ``FIRST``), input/ and temp/;
- the real ``_handler_class`` of a ``ControlCenter.__new__`` center on 127.0.0.1:0 with a random token of its
  own, subclassed only to log every request, its status and JSON answer (before the answer's bytes are written)
  and its Set-Cookie/Location headers (``RequestLog``); the AI status, the AI audit and the Tailscale manager are
  instance stubs (no codex, sc.exe, netsh, PowerShell or tailscale), the scheduler is an autospec mock that is
  never started;
- the real ``DownloadService`` (``start()`` runs the real dispatch thread) with the real ``DownloadWorker``,
  ``DownloadStore``, ``AccountRuntime`` and ``AccountManager`` (a frozen clock at the real now);
- the REAL ``AccountSourceProvider`` of alpha and beta with the test's ``FixtureReader`` over ``FilmSite``s on
  one HTTPS ``FixtureServer`` (portal, ticket and file hosts of alpha; a throwaway CA), reached only through
  ``SessionNetwork`` / ``SafeHttp`` with an injected resolver, connector and TLS context: the public-address,
  redirect, byte-limit, host and cookie rules stay the production ones, and so do the deadlines of a hidden run
  and of a sign-in (download_account_runs.HIDDEN_RUN_SECONDS and GRACE_SECONDS, download_account_browser's
  LAUNCH_SECONDS and PAGE_SECONDS, download_accounts.LOGIN_ATTEMPT_MAX_SECONDS): a loaded machine slows a test,
  it never ends a run for a reason the test does not look at;
- the sign-in coordinator of ``AccountRuntime`` with ``SignInWindows`` as its launcher: ``edge`` (the headless
  stand-in of the window: the same ``SessionBrowser`` without the HeadedPermit, so never visible), ``fake``
  (``FakeWindow``, no Edge) or ``refuse``; its verifier is the plan's (``FixtureVerifier`` / ``Answers``),
  asked every 0.2 s (how often, not how long);
- the fake yt-dlp (its call log must stay absent), the production ``verify_video`` and an injected disk probe
  (the real 100 GB reserve of drive E: is not this harness's subject);
- optionally headless Edge (Playwright, installed channel ``msedge``, ``--no-proxy-server``) on Dashboard V2,
  each context in its own profile under a separate ``m6-dash-*`` folder outside the root, and the real phone
  listener on 127.0.0.1 (only its bind-address and port validators stubbed) opened by typing the code. Each
  context carries a test-only init script and binding (``DOM_WATCH``) that note every change of the account
  buttons and badge in order, any ``<img>`` that is not one of the dashboard's own assets and any Content
  Security Policy violation (``dom_notes``), so "never shown" covers the moments between two looks too; ``look``
  makes it note the current values again.

Nothing here adds a route, enables a fake source in production code or loosens a check: the fixture
reader, verifier, launcher and network exist only as constructor arguments. Hosts are ``.example`` only;
titles, cookies, passwords and clips are made up. Every secret the fixtures hand out (cookie values, rotated
ones, ticket tokens, the phone code and cookie) is looked for in the answers, response headers, pages, the
pages' requests and console, every file of the root (the vault's encrypted files too) and results.json.

``module_setup`` refuses to run on another checkout's code or dashboard, or with the system temp folder outside
the install. Without Playwright, Edge, pycryptodomex or FFmpeg the tests are skipped with the missing piece
named; ``BILIFLOW_REQUIRE_E2E=1`` makes that a failure instead. ``BILIFLOW_E2E_SHOTS`` (an absolute folder
outside the repository, empty or made by this harness) turns on PNG screenshots plus ``results.json``.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from secrets import token_hex
from typing import Any, Callable, Iterable
from unittest import mock
from urllib.parse import urlsplit

import biliflow
from biliflow import control_center
from biliflow.control_center import ControlCenter, _handler_class, _phone_access, _phone_handler_class
from biliflow.download_account_api import AccountRuntime
from biliflow.download_account_browser import SessionBrowser
from biliflow.download_account_http import SessionNetwork
from biliflow.download_account_login import LOGIN_VERIFIERS
from biliflow.download_account_pages import PAGE_READERS
from biliflow.download_account_release_forms import RELEASE_FORMS_READER, RELEASE_FORMS_VERIFIER
from biliflow.download_account_sources import AccountSourceProvider
from biliflow.download_account_vault import SessionVault
from biliflow.download_accounts import AccountManager
from biliflow.download_api import DownloadService
from biliflow.download_runner import YtDlpRunner
from biliflow.download_sources import DirectMediaProvider, SourceRegistry, SourceTransfers
from biliflow.download_store import DownloadStore
from biliflow.download_worker import DownloadWorker
from biliflow.job_store import JobStore
from biliflow.review_evidence import ReviewFrameCache
from biliflow.scheduler import JobScheduler
from biliflow.tailscale_manager import TailscaleManager
from tests.account_source_fixtures import TOKEN_MARK, FilmSite, FixtureReader
from tests.source_fixtures import (
    FFMPEG,
    FFPROBE,
    HAVE_FFMPEG,
    INSTALL_ROOT,
    PUBLIC_ADDRESS,
    FixtureServer,
    Reply,
    make_clip,
    public_resolver,
)
from tests.test_dashboard_v2_phone_hardening import FAKE_LAN
from tests.test_download_account_browser import (
    ALPHA,
    BETA,
    CANARY_PW,
    CANARY_SID,
    CONFIG,
    DATA,
    FILES,
    FIRST,
    PORTAL,
    TEMP_PARENT,
    TICKETS,
    cookie,
    cookies_of,
    edge_command_lines,
    page_reply,
    redirect,
)
from tests.test_download_account_browser import setUpModule as browser_setup
from tests.test_download_account_browser import tearDownModule as browser_teardown
from tests.test_download_account_http import Resolver
from tests.test_download_account_login import FakeWindow
from tests.test_download_accounts import Clock, FakeProtector, GoodAcl
from tests.test_download_worker import FAKE, GB
from tests.tls_fixtures import HAVE_TLS

REPO_ROOT = Path(__file__).resolve().parents[1]
MS = 1000
REQUIRE_ENV = "BILIFLOW_REQUIRE_E2E"  # "1": a missing requirement fails the module instead of skipping its tests


def _importable(name: str) -> bool:
    """Whether ``name`` can be imported (a missing parent package is "no", never an error)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def edge_paths() -> list[Path]:
    """Where Playwright's ``msedge`` channel looks for the installed Edge on Windows."""
    bases = (os.environ.get(name) for name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"))
    return [Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe" for base in bases if base]


# What the E2E tests need and nothing installs: each missing piece is named in the skip reason (or the failure).
MISSING_E2E = tuple(piece for piece, present in (
    ("Playwright's sync API (playwright.sync_api)", _importable("playwright.sync_api")),
    ("the installed Microsoft Edge (msedge.exe for Playwright's msedge channel)",
     any(path.is_file() for path in edge_paths())),
    ("pycryptodomex (the throwaway CA of tests/tls_fixtures.py)", HAVE_TLS),
    (f"FFmpeg and FFprobe ({FFMPEG}; BILIFLOW_FFMPEG)", HAVE_FFMPEG)) if not present)
HAVE_E2E = not MISSING_E2E
NEED_E2E = "missing: " + "; ".join(MISSING_E2E) + " (nothing is installed)" if MISSING_E2E else ""
ROOT_PREFIX, DASH_PREFIX, CLIPS_PREFIX = "m6-e2e-", "m6-dash-", "m6-clips-"
SHOTS_ENV = "BILIFLOW_E2E_SHOTS"
SHOTS_MARKER = ".biliflow-m6-e2e-shots"  # written into a shots folder this harness made (never another folder)
FIXTURE_ADAPTER = "ticket-files"
CLIPS: dict[str, bytes] = {}  # a: 2 s MKV, b: another 1 s MKV, video_only: an MKV without audio
_MODULE: dict[str, Any] = {}  # set while a module's setup is live: "active", and its clip folder ("clips")
SNIFF = re.compile(r"bytes=0-\d+")  # the probe's sample request of a file (download_media_file.SNIFF_BYTES)
# Every POST a page or a step of these tests may make, written out by hand (never built from production's route
# lists, so a new route is not accepted by itself): Thêm, the settings' slots (C1b), a film's variant (C1a) and
# Tiếp tục (C1b), Chọn tập's draft and confirm, a group's Dừng and Tiếp tục (E2), and the account buttons and
# forced account POSTs (refused on the phone and for an unsupported source). The phone's code form is separate.
POST_ALLOWED = tuple(re.compile(pattern) for pattern in (
    r"/api/downloads",
    r"/api/downloads/settings",
    r"/api/downloads/\d+/(choose|resume)",
    r"/api/downloads/\d+/episodes/(draft|confirm)",
    r"/api/downloads/groups/\d+/(stop|resume)",
    r"/api/download-accounts/(alpha|demo-portal)/(login|cancel-login|disconnect)",
))
# What must never reach a page, an API answer (PC or phone), a header, a page's request or console, a file of the
# root or results.json; each test adds the values the fixtures issued (E2EBase.secret_values).
SECRET_MARKERS = (CANARY_SID, CANARY_PW, TOKEN_MARK, "token=", "/f/", f"https://{FILES}", "storage_state")
LOGIN_PAGE = (f'<form method="post" action="/session"><input type="password" name="pw" value="{CANARY_PW}">'
              "</form><script>if (!location.search) setTimeout(() => document.forms[0].submit(), 300)</script>")
EDGE_SECONDS = 240  # the most one browser-side expectation waits while hidden Edge runs start (load, not a bound)
PAGE_ACTION_SECONDS = 60  # the dashboard contexts' default wait of one Playwright action
# Test instrumentation of every dashboard page (an init script and a binding, never part of the page's code): what
# the page showed between two looks of the test, even for a moment. Each change of the number of account buttons
# ([data-action="dl-account"]) and of the account badge's text is noted in order, and so is every <img> that is
# not one of the dashboard's own assets/*.svg (E2EBase.dom_notes). Every Content Security Policy violation is noted
# too (kind ``csp``): markup drawn from a source's text would try an inline handler, which Dashboard V2's CSP
# (script-src 'self') blocks, so "the handler never ran" proves nothing and the violation is what is checked.
# window.__m6look() notes the current values again and resolves once the notes reached the test (E2EBase.look).
DOM_WATCH = """(() => {
  let buttons = -1, badge = null;
  const images = new WeakSet();
  const send = (kind, value) => {
    try {
      if (typeof window.__m6note !== 'function') return null;
      const sent = window.__m6note(kind, value);
      return sent && typeof sent.then === 'function' ? sent.catch(() => {}) : Promise.resolve();
    } catch (_) { return null; }
  };
  const look = () => {
    const sent = [];
    const note = (kind, value) => { const one = send(kind, value); if (one) sent.push(one); return !!one; };
    const count = document.querySelectorAll('[data-action="dl-account"]').length;
    if (count !== buttons && note('account-buttons', String(count))) buttons = count;
    const node = document.querySelector('#dl-account-state .badge');
    const text = node ? node.textContent : '';
    if (text !== badge && note('badge', text)) badge = text;
    document.querySelectorAll('img').forEach(img => {
      const src = img.getAttribute('src') || '';
      if (!images.has(img) && !/^assets\\/[\\w.-]+\\.svg$/.test(src) && note('img', src)) images.add(img);
    });
    return Promise.all(sent);
  };
  new MutationObserver(look).observe(document, {subtree: true, childList: true, characterData: true,
                                                attributes: true, attributeFilter: ['src']});
  document.addEventListener('securitypolicyviolation',
                            event => send('csp', event.effectiveDirective + ' ' + event.blockedURI), true);
  Object.defineProperty(window, '__m6look', {value: () => { buttons = -1; badge = null; return look(); }});
})();"""
# POST from inside a page as its own scripts would (the session token, the page's origin and cookies).
PAGE_POST = """async ([path, body]) => {
  const session = await (await fetch('/api/session', {cache: 'no-store'})).json();
  const answer = await fetch(path, {method: 'POST', cache: 'no-store', body,
    headers: {'X-BiliFlow-Token': session.token, 'Content-Type': 'application/json'}});
  let code = null;
  try { code = (await answer.json()).code || null; } catch (_) { code = null; }
  return [answer.status, code];
}"""
# The page's and its open dialogs' widths (a phone layout must never scroll sideways).
SIDE_SCROLL = """() => {
  const doc = document.documentElement;
  const out = {page: [doc.scrollWidth, window.innerWidth], dialogs: []};
  document.querySelectorAll('dialog[open]').forEach(dialog => {
    const box = dialog.getBoundingClientRect(), body = dialog.querySelector('.modal-body');
    out.dialogs.push([dialog.id, Math.floor(box.left), Math.ceil(box.right), dialog.scrollWidth, dialog.clientWidth,
                      body ? body.scrollWidth : 0, body ? body.clientWidth : 0]);
  });
  return out;
}"""


# ------------------------------------------------------------------------------------------- module
def check_registries() -> None:
    """The production registries hold exactly the "release-forms" reader and verifier (M7), before and after
    the tests: no test leaves a fixture reader or verifier in them, and "ticket-files" (the example source's
    adapter) has neither."""
    expected = ({"release-forms": RELEASE_FORMS_VERIFIER}, {"release-forms": RELEASE_FORMS_READER})
    if (dict(LOGIN_VERIFIERS), dict(PAGE_READERS)) != expected:
        raise AssertionError(f"production registries changed: {dict(LOGIN_VERIFIERS)!r} {dict(PAGE_READERS)!r}")


def _inside(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def check_tree() -> None:
    """The code under test is this checkout's: the biliflow package from REPO_ROOT/src (the venv's editable
    install points at the main checkout), the Dashboard V2 folder the Control Center serves is REPO_ROOT's, and
    the system temp folder (Playwright's and the libraries' own files) is inside the install, never on drive C."""
    problems = []
    package, src = Path(biliflow.__file__).resolve(), (REPO_ROOT / "src").resolve()
    if not _inside(package, src):
        problems.append(f"biliflow is imported from {package}, not from {src}: PYTHONPATH must name this "
                        "checkout's src first")
    served, dashboard = Path(control_center.DASHBOARD_V2_DIR).resolve(), (REPO_ROOT / "dashboard_v2").resolve()
    if served != dashboard:
        problems.append(f"the Control Center serves {served}, not {dashboard}")
    system_temp, install = Path(tempfile.gettempdir()).resolve(), INSTALL_ROOT.resolve()
    if not _inside(system_temp, install):
        problems.append(f"the system temp folder {system_temp} is outside the install {install}: set TEMP and TMP "
                        f"to {install / 'temp'}")
    if problems:
        raise RuntimeError("the M6 E2E tests would test the wrong tree: " + "; ".join(problems))


def module_setup() -> None:
    """The E2E modules' setUpModule: the tree check, the requirements (BILIFLOW_REQUIRE_E2E=1 turns a missing one
    into a failure), the throwaway CA of the browser tests, then the clips (FFmpeg). Each resource is handed to
    unittest.addModuleCleanup as soon as it exists, so a setup that fails half-way still removes what it made and
    clears the module state; a second call before those cleanups ran is refused."""
    check_tree()
    if MISSING_E2E and os.environ.get(REQUIRE_ENV) == "1":
        raise RuntimeError(f"{REQUIRE_ENV}=1 but the M6 E2E tests cannot run: {NEED_E2E}")
    if _MODULE or CLIPS:
        raise RuntimeError("module_setup ran twice before its module cleanups")
    check_registries()
    _MODULE["active"] = True
    unittest.addModuleCleanup(_module_reset)  # runs last
    if not HAVE_E2E:
        return  # every E2E test is skipped with NEED_E2E; nothing to make
    unittest.addModuleCleanup(browser_teardown)
    browser_setup()
    folder = make_temp(CLIPS_PREFIX)
    _MODULE["clips"] = folder
    unittest.addModuleCleanup(remove_tree, folder)
    CLIPS["a"] = make_clip(folder / "a.mkv", seconds=2, size="160x120", container="mkv").read_bytes()
    CLIPS["b"] = make_clip(folder / "b.mkv", seconds=1, size="176x144", container="mkv").read_bytes()
    CLIPS["video_only"] = make_clip(folder / "v.mkv", seconds=1, size="160x120", container="mkv",
                                    audio=False).read_bytes()


def _module_reset() -> None:
    """The last module cleanup: the module state is cleared whatever failed before; the registries are still
    empty."""
    try:
        check_registries()
    finally:
        _MODULE.clear()
        CLIPS.clear()


# -------------------------------------------------------------------------------------------- paths
def require_inside(path: Path, parent: Path = TEMP_PARENT) -> Path:
    """``path`` resolved, refused unless it lies strictly inside ``parent`` (never the parent itself)."""
    resolved, base = Path(path).resolve(), Path(parent).resolve()
    if not resolved.is_absolute() or resolved == base or base not in resolved.parents:
        raise AssertionError(f"refusing a path outside the test temp folder: {resolved}")
    return resolved


def make_temp(prefix: str) -> Path:
    TEMP_PARENT.mkdir(parents=True, exist_ok=True)
    return require_inside(Path(tempfile.mkdtemp(dir=TEMP_PARENT, prefix=prefix)))


def remove_tree(path: Path, *, timeout: float = 30.0) -> None:
    """Delete a folder this run made, after the absolute-path check; Windows may hold a file a moment (Edge
    closing, a scanner on a new MKV), so it is tried again until ``timeout``, then the test fails loudly."""
    target = require_inside(path)
    deadline = time.monotonic() + timeout
    while os.path.lexists(target):
        try:
            shutil.rmtree(target)
        except OSError as error:
            if time.monotonic() > deadline:
                raise AssertionError(f"could not delete the test folder {target}: {error}") from None
            time.sleep(0.5)


def shots_folder() -> Path | None:
    """The screenshot folder of ``BILIFLOW_E2E_SHOTS``: absolute, outside this repository and outside the
    install's tracked folders (its temp/ is allowed), and either made by this harness (its marker file) or new
    or empty (the marker is then written); None when the variable is not set."""
    value = os.environ.get(SHOTS_ENV, "").strip()
    if not value:
        return None
    folder = Path(value)
    if not folder.is_absolute():
        raise AssertionError(f"{SHOTS_ENV} must be an absolute folder")
    folder = folder.resolve()
    install = INSTALL_ROOT.resolve()
    if _inside(folder, REPO_ROOT) or (_inside(folder, install) and not _inside(folder, install / "temp")):
        raise AssertionError(f"{SHOTS_ENV} must be outside the repository: {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / SHOTS_MARKER
    if not marker.is_file():
        if any(folder.iterdir()):
            raise AssertionError(f"{SHOTS_ENV} names a folder that is not empty and was not made by this harness "
                                 f"(no {SHOTS_MARKER}): {folder}")
        marker.write_text("Screenshots and results.json of the BiliFlow M6 E2E tests "
                          "(tests/account_e2e_fixtures.py).\n", encoding="utf-8")
    return folder


# ---------------------------------------------------------------------------------------- the clock
class E2EClock(Clock):
    """The account manager's clock, frozen at the real now (the page's "còn khoảng N phút" uses the browser's
    own clock); ``at(seconds)`` / ``advance(seconds)`` move it from that start."""

    def __init__(self) -> None:
        super().__init__(datetime.now(timezone.utc).replace(microsecond=0))
        self.start = self.now

    def at(self, seconds: float) -> None:
        self.now = self.start + timedelta(seconds=seconds)

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


# ------------------------------------------------------------------------------------- the sign-in
class SignInWindows:
    """The coordinator's launcher by the test's plan (``use``): ``edge`` builds the headless stand-in of the
    window (the same SessionBrowser with its production launch and page deadlines; the HeadedPermit is dropped,
    never passed on), ``fake`` a FakeWindow (no Edge), ``refuse`` fails the test's expectation (``calls`` must
    stay empty). It is also the plan's verifier (``signed_in``): FixtureVerifier for ``edge``, Answers for
    ``fake``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.plan, self.verifier, self.options = "refuse", None, {}
        self.calls: list[str] = []
        self.permits: list[str] = []  # the type of what the coordinator handed over (never kept)
        self.windows: list[Any] = []

    def use(self, plan: str, verifier: Any = None, **options: Any) -> None:
        if plan not in ("edge", "fake", "refuse"):
            raise ValueError(plan)
        with self._lock:
            self.plan, self.verifier, self.options = plan, verifier, options

    def __call__(self, source, http, vault, control, permit, stop_at):
        with self._lock:
            plan, options = self.plan, dict(self.options)
            self.calls.append(plan)
            self.permits.append(type(permit).__name__)
        if plan == "edge":
            window = SessionBrowser(source, http, vault, control, stop_at=stop_at)
        elif plan == "fake":
            options.setdefault("state", {"cookies": [cookie("sid", CANARY_SID, ".alpha.example")], "origins": []})
            window = FakeWindow(control, **options)
        else:
            raise AssertionError("no sign-in window may open in this test")
        with self._lock:
            self.windows.append(window)
        return window

    def signed_in(self, view: Any) -> bool:
        with self._lock:
            verifier = self.verifier
        if verifier is None:
            raise AssertionError("no verifier for this plan")
        return verifier.signed_in(view)


def serve_sign_in(server: FixtureServer) -> None:
    """The fixture source's own sign-in on the portal (after every FilmSite.install, which serves its own
    ``/login``): the form submits itself only on the bare sign-in URL (a hidden run sent to /login?next=… never
    signs in), the session cookie is Domain=alpha.example (the ticket host sees it too), the home page tells
    the source it is ready, and /api/me (the verifier's evidence) answers from the cookie."""
    server.route("/login", page_reply(LOGIN_PAGE))
    server.route("/session", redirect(302, "/home", **{
        "Set-Cookie": f"sid={CANARY_SID}; Domain=alpha.example; Path=/; Secure; HttpOnly; SameSite=Lax"}))
    server.route("/home", page_reply("<p>home</p><script>fetch('/api/ready')</script>"))
    server.route("/api/ready", Reply(b"ok", content_type="text/plain"))

    def me(seen, _number):
        signed = cookies_of(seen).get("sid") == CANARY_SID and server.count("/api/ready") > 0
        return Reply(json.dumps({"signedIn": signed}).encode(), content_type="application/json")
    server.route("/api/me", me)


# ----------------------------------------------------------------------------------- file gates
class GatedReply:
    """A file answer of the fixture server held by ``gate``: the first ``chunk`` bytes go out, then, while the gate
    is closed, only ``trickle`` bytes per ``hold`` seconds (each wait shorter than the client's read timeout, so
    the connection lives on, and a held answer never drains by itself: clip A would take hours), then the rest at
    once. ``entered`` is set once the first chunk went out. FixtureServer._send reads ``chunk`` and ``delay`` for
    every piece it writes. A held transfer counts its first bytes as downloaded at once, while its part file may
    stay empty on disk until the transfer ends (it is written through a buffer the trickle never fills)."""

    def __init__(self, reply: Reply, gate: threading.Event, entered: threading.Event, *, chunk: int = 512,
                 trickle: int = 4, hold: float = 5.0):
        self.body, self.status, self.content_type, self.headers = reply.body, reply.status, reply.content_type, \
            reply.headers
        self.ranges, self.etag, self.cut_after, self.length = reply.ranges, reply.etag, reply.cut_after, reply.length
        self._first, self._trickle, self._started = chunk, trickle, False
        self._gate, self._entered, self._hold = gate, entered, hold

    @property
    def chunk(self) -> int:
        if self._gate.is_set():
            return max(1, len(self.body))
        if not self._started:
            self._started = True
            return self._first
        return self._trickle

    @property
    def delay(self) -> float:
        self._entered.set()
        if not self._gate.is_set():
            self._gate.wait(self._hold)
        return 0.0


@dataclass
class Gate:
    entered: threading.Event
    release: threading.Event
    requests: list
    used: bool = False  # the held answer was given (a gate holds one answer only)

    def open(self) -> None:
        self.release.set()


# ------------------------------------------------------------------------------------- the log
class RequestLog:
    """Every request a logged listener answered: listener, method, path, status, the JSON text of its answer,
    when it was handled (``started``) and when its answer was ready (``ended``: before any byte of it went out,
    so an entry is there by the time a page or a call sees the answer); ``status`` None: no answer at all. Also
    the Set-Cookie and Location headers each answer carried (``headers``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[dict[str, Any]] = []
        self._headers: list[tuple[str, str, str, str]] = []

    def add(self, **item: Any) -> dict[str, Any]:
        with self._lock:
            self._items.append(item)
        return item

    def add_header(self, listener: str, path: str, name: str, value: str) -> None:
        with self._lock:
            self._headers.append((listener, path, name, value))

    def items(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._items)

    def headers(self) -> list[tuple[str, str, str, str]]:
        with self._lock:
            return list(self._headers)

    def count(self, method: str | None = None, path: str | Callable[[str], bool] | None = None,
              listener: str | None = None) -> int:
        return len(self.find(method, path, listener))

    def find(self, method: str | None = None, path: str | Callable[[str], bool] | None = None,
             listener: str | None = None) -> list[dict[str, Any]]:
        def wanted(item: dict[str, Any]) -> bool:
            if method is not None and item["method"] != method:
                return False
            if listener is not None and item["listener"] != listener:
                return False
            if path is None:
                return True
            return path(item["path"]) if callable(path) else item["path"] == path
        return [item for item in self.items() if wanted(item)]


def logged(base: type, log: RequestLog, listener: str) -> type:
    """``base`` (the real Control Center or phone handler) recording each request it answered in ``log``, before
    the answer's bytes are written: a JSON answer when ``send_json`` is called, any other answer (a page, a file,
    a redirect, an error) at its status line, and a request that got no answer once its handler returned."""

    class Logged(base):  # type: ignore[misc, valid-type]
        _m6_active = False
        _m6_entry: dict[str, Any] | None = None

        def _m6_record(self, status: int | None, text: str | None) -> None:
            if not self._m6_active or self._m6_entry is not None:
                return
            self._m6_entry = log.add(listener=listener, method=self._m6_method, path=self._m6_path, status=status,
                                     json=text, started=self._m6_started, ended=time.monotonic())

        def send_response(self, code, message=None):
            self._m6_record(code, None)
            super().send_response(code, message)

        def send_header(self, keyword, value):
            if self._m6_active and str(keyword).lower() in ("set-cookie", "location"):
                log.add_header(listener, self._m6_path, str(keyword), str(value))
            super().send_header(keyword, value)

        def send_json(self, status, payload):
            self._m6_record(status, json.dumps(payload, ensure_ascii=False))
            super().send_json(status, payload)

        def _m6_handle(self, method: str, handle: Callable[[], None]) -> None:
            try:
                path = urlsplit(self.path).path
            except ValueError:
                path = self.path
            self._m6_method, self._m6_path, self._m6_started = method, path, time.monotonic()
            self._m6_entry, self._m6_active = None, True
            try:
                handle()
            finally:
                self._m6_record(None, None)  # nothing was answered (no-op when an answer was recorded)
                self._m6_active = False

        def do_GET(self):  # noqa: N802 - http.server API
            self._m6_handle("GET", super().do_GET)

        def do_POST(self):  # noqa: N802 - http.server API
            self._m6_handle("POST", super().do_POST)
    return Logged


def no_process(*_args: Any, **_kwargs: Any) -> Any:
    raise OSError("no process in the M6 test center")


def _path_of(url: str) -> str:
    try:
        return urlsplit(url).path
    except ValueError:
        return url


# ---------------------------------------------------------------------------------------- the base
@unittest.skipUnless(HAVE_E2E, NEED_E2E)
class E2EBase(unittest.TestCase):
    """See the module docstring. Subclasses add tests; this class has none (importing it is harmless)."""

    def setUp(self) -> None:
        self.root = make_temp(ROOT_PREFIX)
        self.addCleanup(remove_tree, self.root, timeout=60.0)
        from biliflow import job_purge
        self.assertTrue(job_purge.allowed_project_root(self.root))
        (self.root / "config").mkdir()
        (self.root / "input").mkdir()
        self.init_records()
        self.addCleanup(self._write_results)
        self.addCleanup(self._edge_left)
        self.server = FixtureServer(tls=DATA["tls"].server_context())
        self.addCleanup(self.server.close)
        self.resolver = Resolver()
        self.network = SessionNetwork(resolver=self.resolver, connector=self.server.connector,
                                      ssl_context=DATA["tls"].client_context())
        self.http = self.server.http(ssl_context=DATA["tls"].client_context())
        self.clock = E2EClock()
        self.manager = AccountManager(self.root, CONFIG, vault=SessionVault(
            self.root, protector=FakeProtector(), acl=GoodAcl(FIRST)), clock=self.clock)
        self.windows = SignInWindows()
        # The coordinator's and the hidden runs' deadlines are production's (module docstring); only the network
        # is the fixture's, and the verifier is asked every 0.2 s.
        self.runtime = AccountRuntime(self.root, manager=self.manager, coordinator_options={
            "launcher": self.windows, "verifiers": {FIXTURE_ADAPTER: self.windows}, "network": self.network,
            "poll_seconds": 0.2})
        run = {"network": self.network}
        self.alpha = AccountSourceProvider(ALPHA, self.manager, reader=FixtureReader(), run_options=run)
        self.beta = AccountSourceProvider(BETA, self.manager, reader=FixtureReader(), run_options=run)
        self.registry = SourceRegistry([self.alpha, self.beta, DirectMediaProvider()],
                                       site_hosts={"alpha": self.alpha.hosts, "beta": self.beta.hosts})
        self.store = DownloadStore(self.root / "state" / "downloads.sqlite3")
        runner = YtDlpRunner(self.root, command_prefix=[sys.executable, str(FAKE)], deno_path=None,
                             env_extra={"FAKE_YTDLP_SCENARIO": str(self.root / "no-scenario.json"),
                                        "FAKE_YTDLP_LOG": str(self.root / "ytdlp-calls.jsonl")})
        self.worker = DownloadWorker(self.root, self.store, runner=runner, ffmpeg=FFMPEG, ffprobe=FFPROBE,
                                     space_probe=lambda root: (10_000 * GB, 100 * GB), resolver=public_resolver,
                                     cache_pruner=lambda root: {"removed_files": 0}, sources=self.registry,
                                     http=self.http, transfers=SourceTransfers(self.http, ffmpeg=FFMPEG,
                                                                               ffprobe=FFPROBE))
        self.service = DownloadService(self.root, cleanable=lambda: (0, 0), bin_reader=None, store=self.store,
                                       worker=self.worker, accounts=self.runtime)
        self.jobs = JobStore(self.root / "state" / "control-center.sqlite3")
        self.addCleanup(self.jobs.close)
        self.center = self._center()
        self.log = RequestLog()
        self.pc = ThreadingHTTPServer(("127.0.0.1", 0), logged(_handler_class(self.center), self.log, "pc"))
        self.pc.daemon_threads = True
        self.pc_port = self.pc.server_address[1]
        threading.Thread(target=self.pc.serve_forever, name="m6-pc", daemon=True).start()
        self.addCleanup(self.pc.server_close)
        self.addCleanup(self.pc.shutdown)
        self.addCleanup(self._disable_phone)
        self.service.start()
        self.addCleanup(self._stop_service)
        self.assertIsNone(self.service.start_error)

    def init_records(self) -> None:
        """What a test keeps for its checks and results.json (E6 calls it from its own setUp too)."""
        self.token = token_hex(16)  # this Control Center's own token (never a fixed value)
        self.results: dict[str, Any] = {"checks": {}, "shots": [], "completed": False}
        self.secrets: set[str] = set()  # more values the fixtures issued (FilmSite.issued is read at scan time)
        self.sites: list[FilmSite] = []
        self.phone_status: dict[str, Any] | None = None
        self.phone_cookie: str | None = None
        self.page_texts: list[tuple[str, str]] = []  # (page name, page.content())
        self.page_events: dict[str, list] = {"requests": [], "pageerrors": [], "dialogs": [], "console": []}
        self.console_expected: list[tuple[str, str]] = []  # (page name, regex): a harness step's own console error
        self.dom_notes: list[tuple[str, str, str]] = []  # (page name, kind, value) from DOM_WATCH, in order
        self._page_names: dict[int, str] = {}
        self._pw: Any = None
        self._dash: Path | None = None
        self.pump: Any = None  # the page whose events a wait keeps pumping (Playwright's sync API)

    def _center(self) -> ControlCenter:
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.host, center.token, center.store = self.root, "127.0.0.1", self.token, self.jobs
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        self.scheduler = mock.create_autospec(JobScheduler, instance=True)  # never started; /api/status reads it
        self.scheduler.queue_order.return_value = []
        self.scheduler.active = None
        center.scheduler = self.scheduler
        center.recovered = 0
        center.downloads = self.service
        center.ai_status = mock.Mock(return_value={"ready": False, "message": "AI tắt trong test", "login_running":
                                                   False, "config": {"enabled": False, "model": "",
                                                                     "reasoning_effort": ""}})
        center.start_ai_audit = mock.Mock(side_effect=AssertionError("no AI audit"))
        center._audit_jobs, center._audit_lock = {}, threading.Lock()
        # program_files: a folder that does not exist (no Tailscale), outside the root (its path is shown).
        center.tailscale = TailscaleManager(self.root, run=no_process, popen=no_process, fetch=no_process,
                                            elevate=no_process, image_path=lambda: None,
                                            program_files=str(TEMP_PARENT / "m6-no-program-files"))
        self.addCleanup(center.tailscale.close)
        return center

    # ------------------------------------------------------------------ cleanup (reverse order of setUp)
    def _stop_service(self) -> None:
        self.service.stop()
        self.assertTrue(self.service.wait_closed(60), "the download service did not close within 60 s")
        self.assertIsNone(self.service.close_error)

    def _disable_phone(self) -> None:
        if self.phone_status is not None:
            _phone_access(self.center).disable()

    def _edge_left(self) -> None:
        """No Edge of this root is left (bounded wait) and the session browser left no profile."""
        if os.name == "nt":
            deadline = time.monotonic() + 30
            while (left := edge_command_lines(str(self.root))) and time.monotonic() < deadline:
                time.sleep(1)
            self.assertEqual(left, [], "an Edge process of this test root is still running")
        folder = self.manager.vault.browser_folder()
        self.assertEqual(list(folder.iterdir()) if folder.exists() else [], [])

    def _write_results(self) -> None:
        """This test's entry in results.json (BILIFLOW_E2E_SHOTS), refused when it would hold a secret."""
        folder = shots_folder()
        if folder is None:
            return
        entry = {**self.results, "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        text = json.dumps(entry, ensure_ascii=False)
        leaked = sorted(index for index, value in enumerate(sorted(self.secret_values() | self.phone_secrets()))
                        if value in text)
        if leaked:
            raise AssertionError(f"results.json not written: this test's entry holds {len(leaked)} secret value(s)")
        target = folder / "results.json"
        try:
            known = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        except ValueError:
            known = {}
        known[self.id()] = entry
        target.write_bytes(json.dumps(known, ensure_ascii=False, indent=2).encode("utf-8"))

    # ------------------------------------------------------------------ results and screenshots
    def check(self, name: str, value: Any) -> Any:
        """Keep an assertion's subject for results.json (the assertion itself is the test's); never a secret
        (``_write_results`` refuses one)."""
        self.results["checks"][name] = value
        return value

    def shot(self, page: Any, name: str, *, full_page: bool = True) -> None:
        """A PNG of ``page`` (the whole page from its top, or only the viewport, e.g. under an open dialog)."""
        folder = shots_folder()
        if folder is None:
            return
        short = self.id().rsplit(".", 1)[-1][:40]
        path = folder / f"{short}-{name}.png"
        if full_page:
            page.evaluate("() => window.scrollTo(0, 0)")  # fixed parts are drawn where the page is scrolled
        # CSS transitions (a theme just switched, a hovered button) are drawn at their end state.
        page.screenshot(path=str(path), full_page=full_page, animations="disabled")
        self.results["shots"].append(path.name)

    # ------------------------------------------------------------------ waiting
    def wait_until(self, predicate: Callable[[], Any], timeout: float = 120.0, what: str = "") -> Any:
        """Poll ``predicate`` until it is true (a generous deadline, never a fixed sleep before an assert). While
        a dashboard page is open, the wait goes through it so its events keep coming in."""
        deadline = time.monotonic() + timeout
        while True:
            value = predicate()
            if value:
                return value
            if time.monotonic() > deadline:
                self.fail(f"timed out after {timeout:.0f} s waiting for {what or predicate}; tasks: "
                          f"{[(t['id'], t['state'], t['error_code']) for t in self.store.list_tasks()]}")
            if self.pump is not None:
                self.pump.wait_for_timeout(200)
            else:
                time.sleep(0.2)

    def wait_state(self, task_id: int, *states: str, timeout: float = 180.0) -> dict[str, Any]:
        return self.wait_until(lambda: (task := self.store.get(task_id)) is not None and task["state"] in states
                               and task, timeout, f"task {task_id} in {states}")

    def kinds(self, task_id: int) -> list[str]:
        return [event["kind"] for event in self.store.events(task_id)]

    def wait_event(self, task_id: int, kind: str, timeout: float = 30.0) -> None:
        """Until the task has an event of ``kind``. The worker writes a task's event just after its state, so a
        test that has seen the state waits for the event before it reads the events."""
        self.wait_until(lambda: kind in self.kinds(task_id), timeout, f"event {kind} of task {task_id}")

    def input_names(self) -> list[str]:
        return sorted(path.name for path in (self.root / "input").iterdir())

    # ------------------------------------------------------------------ the fixture source
    def serve_site(self, site: FilmSite, portal: str = PORTAL, tickets: str = TICKETS, files: str = FILES) -> Any:
        """Serve ``site`` on the fixture server with the source's sign-in, and keep it: every value it issues is
        a secret of the scans."""
        site.install(self.server, portal, tickets, files)
        serve_sign_in(self.server)
        self.sites.append(site)
        return site

    def install_site(self, **options: Any) -> FilmSite:
        return self.serve_site(FilmSite(**options))

    def connect_alpha(self) -> dict[str, Any]:
        """A session saved through the manager's own sign-in steps (no window), valid for the fixture site."""
        attempt = self.manager.begin_login("alpha")
        return self.manager.complete_login(attempt, {"cookies": [cookie("sid", CANARY_SID, ".alpha.example")],
                                                     "origins": []})

    def gate_file(self, key: str, *, request: int = 1, transfer: bool = False) -> Gate:
        """Hold the ``request``-th answer of the file ``/f/<key>`` (it trickles until ``open``); with
        ``transfer``, the first answer that is not a probe's sample read (``SNIFF``) instead: the transfer itself,
        after the probe and the fresh link's probe, so the episode is DOWNLOADING with a part while held."""
        path = f"/f/{key}"
        original = self.server.routes[path]
        gate = Gate(threading.Event(), threading.Event(), [])

        def route(seen, number):
            reply = original(seen, number)
            asked = seen.headers.get("range", "")
            gate.requests.append((number, asked))
            wanted = not SNIFF.fullmatch(asked) if transfer else number == request
            if wanted and not gate.used and reply.status == 200:
                gate.used = True
                return GatedReply(reply, gate.release, gate.entered)
            return reply
        self.server.route(path, route)
        self.addCleanup(gate.open)  # before the service stops: a held answer never outlives the test
        return gate

    # ------------------------------------------------------------------ HTTP through the real handler
    def api(self, method: str, path: str, payload: Any = None, *, port: int | None = None,
            headers: dict[str, str] | None = None, token: str | None = None) -> tuple[int, Any]:
        """One request through a logged listener (the PC's by default) with this center's token (``token=""``:
        none)."""
        port = port or self.pc_port
        token = self.token if token is None else token
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        try:
            body = json.dumps(payload).encode() if payload is not None else (b"{}" if method == "POST" else None)
            sent = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", **(headers or {})}
            if token:
                sent["X-BiliFlow-Token"] = token
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            connection.close()

    # ------------------------------------------------------------------ dashboard (Playwright, this thread only)
    def _playwright(self) -> Any:
        if self._pw is None:
            from playwright.sync_api import sync_playwright
            self._dash = make_temp(DASH_PREFIX)
            self.addCleanup(remove_tree, self._dash, timeout=60.0)
            self._pw = sync_playwright().start()
            self.addCleanup(self._pw.stop)
        return self._pw

    def open_page(self, name: str, url: str, *, color_scheme: str = "light", viewport: tuple[int, int] = (1440, 900),
                  mobile: bool = False) -> Any:
        """A new headless Edge context (its own profile folder) on ``url``; its events are recorded. The launch
        may take up to EDGE_SECONDS on a loaded machine; one page action waits PAGE_ACTION_SECONDS by default."""
        pw = self._playwright()
        assert self._dash is not None
        profile = self._dash / name
        options: dict[str, Any] = {"channel": "msedge", "headless": True, "color_scheme": color_scheme,
                                   "viewport": {"width": viewport[0], "height": viewport[1]},
                                   "args": ["--no-proxy-server"], "accept_downloads": False,
                                   "timeout": EDGE_SECONDS * MS}
        if mobile:
            options.update(is_mobile=True, has_touch=True)
        context = pw.chromium.launch_persistent_context(str(profile), **options)
        self.addCleanup(context.close)
        context.set_default_timeout(PAGE_ACTION_SECONDS * MS)
        notes = self.dom_notes
        context.expose_function("__m6note", lambda kind, value: notes.append((name, str(kind), str(value))))
        context.add_init_script(DOM_WATCH)
        page = context.pages[0] if context.pages else context.new_page()
        self._page_names[id(page)] = name
        events = self.page_events

        def dialog(item):
            events["dialogs"].append((name, item.type, item.message))
            item.dismiss()

        def console(message):
            if message.type == "error":
                events["console"].append((name, message.text, _path_of((message.location or {}).get("url") or "")))
        page.on("request", lambda request: events["requests"].append(
            (name, request.method, request.url, request.post_data if request.method == "POST" else None)))
        page.on("pageerror", lambda error: events["pageerrors"].append((name, str(error))))
        page.on("dialog", dialog)
        page.on("console", console)
        page.goto(url)
        return page

    def open_dashboard(self, name: str = "pc-light", *, color_scheme: str = "light") -> Any:
        page = self.open_page(name, f"http://127.0.0.1:{self.pc_port}/dashboard-v2/#downloads",
                              color_scheme=color_scheme)
        from playwright.sync_api import expect
        expect(page.locator("#dl-accounts")).to_be_visible(timeout=60_000)
        expect(page.locator("#dl-mode")).to_have_text("Control Center", timeout=60_000)
        self.theme(page, color_scheme)
        return page

    @staticmethod
    def theme(page: Any, scheme: str) -> None:
        """Dashboard V2 keeps its own theme (the user's Tối/Sáng button, stored per browser profile), not the
        system's: a dark context presses Tối like a user."""
        from playwright.sync_api import expect
        if scheme == "dark":
            page.click("#theme-toggle")
            expect(page.locator("html")).to_have_attribute("data-theme", "dark")

    def open_phone(self) -> dict[str, Any]:
        """The real phone listener on 127.0.0.1:0 (only the bind-address and port checks stubbed; requests keep
        every check: Host, cookie from the typed code, Origin, token, PHONE_ALLOWED_POSTS, PC-only refusals). Its
        code and cookie value are secrets of the scans (the code may only be on the PC and in the code form)."""
        phone = _phone_access(self.center)
        phone._lan = lambda: FAKE_LAN  # never the real Wi-Fi
        self.phone_status = phone.enable(lambda access: logged(_phone_handler_class(self.center, access), self.log,
                                                               "phone"),
                                         address=FAKE_LAN, check_address=lambda _a: None,
                                         check_port=lambda _p: None, port=0)
        self.phone_cookie = phone._cookie_value()  # noqa: SLF001 - the value the code form's answer sets
        return self.phone_status

    def open_phone_page(self, *, width: int = 390, height: int = 844, color_scheme: str = "light") -> Any:
        """A phone-sized context on the phone listener (named ``phone-<width>-<scheme>``), signed in by typing the
        code like a user: the code page, the form's POST, then Dashboard V2 on #downloads, confirmed as the phone
        by the listener's own /api/phone-mode answer."""
        from playwright.sync_api import expect
        status = self.phone_status or self.open_phone()
        base = f"http://127.0.0.1:{status['port']}"
        name = f"phone-{width}-{color_scheme}"
        # The code page is the listener's 401 answer to a device without the cookie (the browser logs it).
        self.console_expected.append((name, r"status of 401 \(Unauthorized\) @ /$"))
        page = self.open_page(name, f"{base}/", color_scheme=color_scheme, viewport=(width, height), mobile=True)
        code = page.locator('input[name="code"]')
        expect(code).to_be_visible(timeout=60_000)  # the code page, not a page the cookie of another device opened
        code.fill(status["code"])
        logins = self.log.count("POST", "/phone-login", "phone")
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard-v2/", timeout=60_000)
        # One typed code (logged before its answer went out, so it is there once the page moved on).
        self.assertEqual(self.log.count("POST", "/phone-login", "phone"), logins + 1)
        page.goto(f"{base}/dashboard-v2/#downloads")
        expect(page.locator("#dl-mode")).to_have_text("Qua điện thoại", timeout=60_000)
        expect(page.locator("#dl-accounts")).to_be_visible(timeout=60_000)
        self.theme(page, color_scheme)
        return page

    # ------------------------------------------------------------------ what a page showed and did
    def notes(self, page_name: str, kind: str, since: int = 0) -> list[str]:
        """The values DOM_WATCH noted for ``page_name`` (kind ``account-buttons``, ``badge``, ``img`` or ``csp``)
        from note ``since`` on (``len(self.dom_notes)`` taken earlier); notes reach Python while a Playwright call
        runs."""
        return [value for name, noted, value in self.dom_notes[since:] if name == page_name and noted == kind]

    @staticmethod
    def look(page: Any) -> None:
        """DOM_WATCH notes the page's current account buttons, badge and foreign images again; the notes are in
        ``dom_notes`` when this returns. A "never shown since the mark" check that follows cannot pass on an empty
        list: the watch is alive in this document and saw the current state."""
        page.evaluate("() => window.__m6look()")

    def page_polls(self, page_name: str) -> int:
        """How many list polls (GET /api/downloads) the page ``page_name`` sent (the test's own calls excluded)."""
        return sum(1 for name, method, url, _body in self.page_events["requests"]
                   if name == page_name and method == "GET" and urlsplit(url).path == "/api/downloads")

    def wait_polls(self, page_name: str, count: int, timeout: float = 60.0) -> None:
        """Until the page ``page_name`` sent ``count`` more list polls (a condition, never a fixed sleep)."""
        start = self.page_polls(page_name)
        self.wait_until(lambda: self.page_polls(page_name) >= start + count, timeout,
                        f"{count} more list polls of {page_name}")

    @staticmethod
    def page_post(page: Any, path: str, body: str = "{}") -> list:
        """POST ``path`` from inside ``page`` (its token, origin and cookies): [status, the answer's code]."""
        return page.evaluate(PAGE_POST, [path, body])

    def assert_no_side_scroll(self, page: Any, where: str) -> dict[str, Any]:
        """The page, and any open dialog, fits its width: no sideways scroll (1 px of rounding allowed in a
        dialog, as in the dashboard's own browser checks)."""
        sizes = page.evaluate(SIDE_SCROLL)
        width, inner = sizes["page"]
        self.assertLessEqual(width, inner, f"{where}: the page scrolls sideways {sizes}")
        for ident, left, right, scroll, client, body_scroll, body_client in sizes["dialogs"]:
            self.assertGreaterEqual(left, 0, f"{where}: dialog {ident} {sizes}")
            self.assertLessEqual(right, inner, f"{where}: dialog {ident} {sizes}")
            self.assertLessEqual(scroll, client + 1, f"{where}: dialog {ident} {sizes}")
            self.assertLessEqual(body_scroll, body_client + 1, f"{where}: dialog {ident} {sizes}")
        self.results["checks"].setdefault("side_scroll", {})[where] = sizes
        return sizes

    def keep_page_text(self, page: Any) -> None:
        """A snapshot of the page's DOM for the secret scan."""
        self.page_texts.append((self._page_names.get(id(page), "?"), page.content()))

    # ------------------------------------------------------------------ the checks every E2E test keeps
    def secret_values(self) -> set[str]:
        """SECRET_MARKERS, the values the test registered and every value its fixture sites issued so far."""
        values = set(SECRET_MARKERS) | self.secrets
        for site in self.sites:
            values.update(site.issued)
        return {value for value in values if value}

    def phone_secrets(self) -> set[str]:
        """The phone listener's code and cookie value (empty while the phone mode was not opened)."""
        return {value for value in ((self.phone_status or {}).get("code"), self.phone_cookie) if value}

    def secret_scan(self) -> None:
        """No secret (``secret_values``: cookies, rotated cookies, the password, tickets, signed file links,
        storage state; the phone's code and cookie) and no local path in any answer of the logged listeners (PC
        and phone), any Set-Cookie or Location they sent, any page snapshot, or any request URL, POST body or
        console error of a page; no secret in any file of the root, the vault's encrypted session files included
        (no plaintext), and no unfinished .tmp in the vault. Allowed: the phone code in the PC's own
        /api/phone-mode answer and PC pages (the user reads it there) and in the code form's POST; the phone cookie
        in the code form's Set-Cookie. A file byte-equal to one of this module's clips (an episode in input) holds
        nothing else. The downloads database and the stored links must be among the bytes read."""
        secrets, phone = self.secret_values(), self.phone_secrets()
        code = {(self.phone_status or {}).get("code")} & phone
        answers = [item for item in self.log.items() if item["json"]]
        self.assertTrue(answers, "no JSON answer was recorded to scan")
        texts: list[tuple[str, str, set[str]]] = []  # (where, text, phone secrets allowed there)
        for item in answers:
            own = (item["listener"], item["method"], item["path"]) == ("pc", "GET", "/api/phone-mode")
            texts.append((f"{item['listener']} {item['method']} {item['path']}", item["json"], code if own else set()))
        for listener, path, name, value in self.log.headers():
            own = (listener, path, name.lower()) == ("phone", "/phone-login", "set-cookie")
            texts.append((f"{listener} {path} {name}", value, {self.phone_cookie} & phone if own else set()))
        for page_name, content in self.page_texts:
            texts.append((f"page {page_name}", content, set() if page_name.startswith("phone-") else code))
        for page_name, method, url, body in self.page_events["requests"]:
            texts.append((f"{page_name} {method} {_path_of(url)}", url, set()))
            if body:
                own = method == "POST" and _path_of(url) == "/phone-login"
                texts.append((f"{page_name} {method} {_path_of(url)} body", body, code if own else set()))
        for page_name, text, where in self.page_events["console"]:
            texts.append((f"{page_name} console", f"{text} {where}", set()))
        locals_ = [self.root, self.manager.vault.source_folder("alpha"), self.manager.vault.browser_folder()]
        spellings = {spelling.casefold() for local in locals_ for spelling in
                     (str(local), str(local).replace("\\", "/"), json.dumps(str(local))[1:-1])}
        groups: dict[frozenset[str], list[tuple[str, str]]] = {}
        for where, text, allowed in texts:
            groups.setdefault(frozenset(allowed), []).append((where, text))
        for allowed, items in groups.items():  # each value is searched once per group, then located
            joined = "\n\x00".join(text for _where, text in items)
            folded = joined.casefold()
            for value, haystack, fold in [(value, joined, False) for value in (secrets | phone) - allowed] + \
                    [(spelling, folded, True) for spelling in spellings]:
                if value in haystack:
                    where, text = next(item for item in items if value in (item[1].casefold() if fold else item[1]))
                    self.assert_absent(value, text.casefold() if fold else text, where)
        scanned = self.scan_root_files(secrets | phone)
        self.check("secret_scan", {"answers": len(answers), "texts": len(texts), "files": len(scanned),
                                   "values": len(secrets | phone)})

    def scan_root_files(self, values: Iterable[str]) -> list[str]:
        """Every file of the root holds none of ``values`` (the vault's session files too: the fake protector
        scrambles them, so a plaintext value there is a leak); the vault keeps no .tmp. The scanned paths."""
        vault_base = self.root / "state" / "source-accounts"
        self.assertEqual(sorted(path.relative_to(self.root).as_posix() for path in vault_base.rglob("*.tmp"))
                         if vault_base.exists() else [], [])
        database = self.root / "state" / "downloads.sqlite3"
        self.assertTrue(database.is_file())
        clips = set(CLIPS.values())
        scanned: list[str] = []
        data = bytearray()
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                path = Path(folder) / name
                content = path.read_bytes()
                if content in clips:
                    continue
                scanned.append(path.relative_to(self.root).as_posix())
                data += content + b"\n"
        self.assertIn("state/downloads.sqlite3", scanned)
        missing = [task["url"] for task in self.store.list_tasks() if task["url"].encode("utf-8") not in data]
        self.assertEqual(missing, [], "the stored links were not among the bytes scanned")
        blob = bytes(data)
        for value in values:
            self.assert_absent(value.encode("utf-8"), blob, "the root's files")
        return scanned

    def assert_absent(self, marker: Any, text: Any, where: str = "") -> None:
        """``marker`` is not in ``text``; a failure names where and shows only the surroundings (the found value
        itself is masked, never every answer)."""
        at = text.find(marker)
        if at >= 0:
            before, after = text[max(0, at - 160):at], text[at + len(marker):at + len(marker) + 60]
            self.fail(f"a secret or local path ({len(marker)} chars) found in {where or 'the text'}: "
                      f"...{before!r} <here> {after!r}...")

    def assert_network_kept(self, hosts: tuple[str, ...] = (PORTAL, TICKETS, FILES), *,
                            looked_up: tuple[str, ...] = (PORTAL, TICKETS), exact: bool = False) -> None:
        """Every fixture request went to ``hosts`` (the sources' own) through the checked public address (no
        request reached the fixture server without a checked connection); no cookie reached the file host; the
        session browser looked up ``looked_up`` only (by default alpha's portal and ticket hosts; a test with
        another source's portal names it in both), with ``exact`` each of them."""
        requests = list(self.server.requests)
        self.assertLessEqual({item.host for item in requests}, set(hosts))
        self.assertEqual([item.path for item in requests if item.host == FILES and "cookie" in item.headers], [])
        addresses = {address for address, _port in self.server.connections}
        self.assertLessEqual(addresses, {PUBLIC_ADDRESS})
        if requests:
            self.assertEqual(addresses, {PUBLIC_ADDRESS}, "requests without a checked connection")
        if exact:
            self.assertEqual(set(self.resolver.calls), set(looked_up))
        else:
            self.assertLessEqual(set(self.resolver.calls), set(looked_up))

    def assert_routes_kept(self, *expected: tuple[str, int | None]) -> None:
        """Every POST any page or call made is on the hand-written list POST_ALLOWED (or the phone's code form),
        every request got an answer, no answer was a server error, and every Dashboard V2 file was found.
        ``expected``: (path, status) of a step's own forced POST and its expected refusal (e.g. 503
        ACCOUNT_NOT_READY); each must have been answered exactly once, with that status, and is then left out of
        the other checks."""
        for path, status in expected:
            self.assertEqual([item["status"] for item in self.log.find("POST", path)], [status], path)
        items = [item for item in self.log.items() if (item["path"], item["status"]) not in expected]
        for item in items:
            if item["method"] != "POST" or (item["listener"] == "phone" and item["path"] == "/phone-login"):
                continue
            self.assertTrue(any(pattern.fullmatch(item["path"]) for pattern in POST_ALLOWED), item["path"])
        self.assertEqual([(item["listener"], item["method"], item["path"]) for item in items
                          if item["status"] is None], [], "requests that got no answer")
        self.assertEqual([(item["path"], item["status"]) for item in items if (item["status"] or 0) >= 500], [])
        self.assertEqual([item["path"] for item in items
                          if item["path"].startswith("/dashboard-v2") and item["status"] == 404], [])

    def assert_no_scan(self) -> None:
        """The downloader never queued a scan (the scheduler was only read for /api/status) and made no job."""
        self.assertEqual({name for name, *_ in self.scheduler.mock_calls} - {"queue_order"}, set())
        self.assertEqual(self.jobs.list_jobs(), [])

    def assert_no_scan_and_no_yt_dlp(self) -> None:
        """No scan, and the fake yt-dlp (which logs every call before anything else) was never run."""
        self.assert_no_scan()
        self.assertFalse((self.root / "ytdlp-calls.jsonl").exists())

    def assert_page_clean(self, *console: str) -> None:
        """No page error, no dialog, no ``<img>`` other than the dashboard's own assets and no Content Security
        Policy violation on any page at any moment (DOM_WATCH), and every console error is one a step caused on
        purpose: ``console`` holds regular expressions searched in "<message> @ <path of the URL it names>" (on any
        page), ``console_expected`` the harness's own (the phone's code page) for one page."""
        self.assertEqual(self.page_events["pageerrors"], [])
        self.assertEqual(self.page_events["dialogs"], [])
        self.assertEqual([note for note in self.dom_notes if note[1] == "img"], [])
        self.assertEqual([note for note in self.dom_notes if note[1] == "csp"], [])
        patterns = [re.compile(pattern) for pattern in console]
        errors = [(name, f"{text} @ {where}") for name, text, where in self.page_events["console"]]
        self.check("console_errors", len(errors))

        def expected(name: str, text: str) -> bool:
            return any(pattern.search(text) for pattern in patterns) or any(
                page == name and re.search(pattern, text) for page, pattern in self.console_expected)
        self.assertEqual([(name, text) for name, text in errors if not expected(name, text)], [])
