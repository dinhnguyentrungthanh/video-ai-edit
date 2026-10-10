"""Source accounts: the real AccountSourceProvider through the worker and the real handler (SOURCE_ACCOUNTS_PLAN, M6).

The film branches of ``AccountSourceProvider.resolve`` (a film that goes straight to its file, one that waits
for a variant choice, one whose list was not read whole) and the provider's error codes, run by the real
DownloadService and DownloadWorker behind the real Control Center handler, with a pre-connected alpha
session and the self-made film sites of tests/account_source_fixtures.py on the HTTPS fixture server. No
dashboard browser here; the hidden runs use the session browser (headless Edge). The harness and what it
injects are described in tests/account_e2e_fixtures.py; run and stop like tests/test_download_account_e2e.py:

    E:/DungChung/BiliFlow/.venv/Scripts/python.exe -m unittest tests.test_download_account_crossing -v

C1a: the film paths. C1b: what a source's failures become through the worker (an expired ticket and a refused
file are asked for again; page, server, network and missing-file errors keep their own codes; only the
source's signed-out evidence waits for a sign-in), and the hidden runs of one source taking turns while the
handler, the store and the dispatcher keep answering. Every hidden run of alpha is serialized (one source), so
a test costs about 4 s per run (two runs per downloaded film); the network case also waits out the transfer's
own retries (about 23 s, while the other films' runs go on).

Test-only observers, never a change of behaviour: fixture routes that cut, refuse or hold one answer of the
fixture server, and wrappers that call the real ``download_account_runs._acquire`` and ``_run_browser`` and
record when each hidden run asked for its source's lock, got it and ran its browser.
"""
from __future__ import annotations

import itertools
import threading
import time
import unittest
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from biliflow import download_account_runs
from biliflow.download_account_http import REQUEST_SECONDS
from biliflow.download_account_sources import TICKET_ATTEMPTS
from biliflow.download_account_tickets import TicketCache
from biliflow.download_media_file import FILE_RETRIES, PART_NAME
from tests.account_e2e_fixtures import (
    CLIPS,
    EDGE_SECONDS,
    SNIFF,
    E2EBase,
    module_setup,
)
from tests.account_source_fixtures import FilmSite, Item, TicketBehaviour
from tests.source_fixtures import Reply, Seen
from tests.test_download_account_browser import CANARY_SID, FILES, FIRST, PORTAL, TICKETS, cookie

# States a task rests in until the user acts (the waits below end on any of them, then assert which).
SETTLED = ("COMPLETED", "FAILED", "NEEDS_CHOICE", "WAITING_LOGIN", "STOPPED", "INTERRUPTED", "CANCELLED",
           "EXPIRED")
BATCH_SECONDS = 3 * EDGE_SECONDS  # a batch of serialized hidden runs under load (a condition wait, not a bound)
# A held ticket page answers before the session browser's own deadline for one exchange (download_account_http).
HOLD_SECONDS = REQUEST_SECONDS - 5
# The fixture CA names only the account hosts and evil.example: here that one is just a public host of a direct
# media link (the anonymous DirectMediaProvider, cookie-free SafeHttp), not an account source.
DIRECT_HOST = "evil.example"
DIRECT_PATH = "/media/direct-clip.mkv"
DIRECT_URL = f"https://{DIRECT_HOST}{DIRECT_PATH}"
ACCOUNT_FIELDS = ("generation", "session_state", "error_code", "authenticated_at")
BETA_SESSION = f"{CANARY_SID}-beta"  # beta's session cookie (a secret of the scans like alpha's)


def setUpModule():
    module_setup()  # its resources are module cleanups (tests/account_e2e_fixtures.py)


# ------------------------------------------------------------------------------------- fixture routes
class FileScript:
    """The file route of one fixture file: the probe's sample reads (``SNIFF``) pass as they are; the n-th
    transfer request with a valid ticket (the site answered 200) gets ``answer(reply, n)`` instead. Every request
    is recorded as (kind, Range, If-Range, status, cut_after) of the answer sent."""

    def __init__(self, server: Any, key: str, answer: Callable[[Reply, int], Reply]):
        self.requests: list[tuple[str, str, str, int, int | None]] = []
        self._answer = answer
        self._count = 0
        self._lock = threading.Lock()
        path = f"/f/{key}"
        self._original = server.routes[path]
        server.route(path, self._route)

    def _route(self, seen: Seen, number: int) -> Reply:
        reply = self._original(seen, number)
        wanted = seen.headers.get("range", "")
        kind = "probe" if SNIFF.fullmatch(wanted) else "transfer"
        if kind == "transfer" and reply.status == 200:
            with self._lock:
                self._count += 1
                count = self._count
            reply = self._answer(reply, count)
        with self._lock:
            self.requests.append((kind, wanted, seen.headers.get("if-range", ""), reply.status, reply.cut_after))
        return reply

    def transfers(self) -> list[tuple[str, str, int, int | None]]:
        with self._lock:
            return [(wanted, if_range, status, cut) for kind, wanted, if_range, status, cut in self.requests
                    if kind == "transfer"]


class TicketHold:
    """Holds the first ticket page asked for among ``keys``: the fixture server answers it only once ``release``
    is set (at most ``limit`` seconds, then it answers anyway and ``expired`` says so). The hidden run that asked
    waits there, inside its browser, with its source's lock."""

    def __init__(self, server: Any, keys: list[str], limit: float):
        self.entered, self.release = threading.Event(), threading.Event()
        self.key: str | None = None
        self.resumed_at: float | None = None
        self.expired = False
        self._limit = limit
        self._lock = threading.Lock()
        self._taken = False
        for key in keys:
            self._wrap(server, key)

    def _wrap(self, server: Any, key: str) -> None:
        original = server.routes[f"/t/{key}"]

        def route(seen: Seen, number: int) -> Reply:
            with self._lock:
                first, self._taken = not self._taken, True
            if first:
                self.key = key
                self.entered.set()
                self.expired = not self.release.wait(self._limit)
                self.resumed_at = time.monotonic()
            return original(seen, number)
        server.route(f"/t/{key}", route)

    @property
    def holding(self) -> bool:
        return self.entered.is_set() and self.resumed_at is None


class RunWatch:
    """Watches the hidden runs without changing them: wraps download_account_runs._acquire (when a run asked
    for its source's lock and when it got it) and _run_browser (when its browser ran), each calling the real
    function, with the worker thread that ran it (``biliflow-download-<task id>``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.waits: list[dict[str, Any]] = []
        self.runs: list[tuple[str, str, float, float]] = []  # (source id, thread, started, ended)

    def install(self, case: unittest.TestCase) -> None:
        real_acquire, real_run = download_account_runs._acquire, download_account_runs._run_browser

        def acquire(lock: Any, control: Any, wait_seconds: float) -> None:
            entry: dict[str, Any] = {"thread": threading.current_thread().name, "asked": time.monotonic(),
                                     "got": None}
            with self._lock:
                self.waits.append(entry)
            real_acquire(lock, control, wait_seconds)
            with self._lock:
                entry["got"] = time.monotonic()

        def run_browser(source: Any, *args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                return real_run(source, *args, **kwargs)
            finally:
                with self._lock:
                    self.runs.append((source.id, threading.current_thread().name, started, time.monotonic()))
        for name, wrapper in (("_acquire", acquire), ("_run_browser", run_browser)):
            patcher = mock.patch.object(download_account_runs, name, wrapper)
            patcher.start()
            case.addCleanup(patcher.stop)

    def waiting(self, thread: str) -> bool:
        with self._lock:
            return any(entry["thread"] == thread and entry["got"] is None for entry in self.waits)

    def first_wait(self, thread: str) -> dict[str, Any]:
        with self._lock:
            return dict(next(entry for entry in self.waits if entry["thread"] == thread))

    def of(self, source_id: str) -> list[tuple[float, float, str]]:
        with self._lock:
            return sorted((started, ended, thread) for source, thread, started, ended in self.runs
                          if source == source_id)


class Ring:
    """What a whole rotating site accepts now: one session value for all its films (a FilmSite keeps its own),
    and every film page's request: (arrived, film, cookie sent, status, cookie set)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counter = itertools.count(1)
        self.value: str | None = None
        self.pages: list[tuple[float, str, str | None, int, str | None]] = []


@dataclass
class RingFilm(FilmSite):
    """A film of a rotating site (``rotating=True``) whose films share one session value (``ring``): a run that
    leased an older cookie than the site's latest is sent to the sign-in page (SignedOut)."""
    ring: Ring = field(default_factory=Ring)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.lock, self.counter = self.ring.lock, self.ring.counter

    @property  # type: ignore[override]
    def current(self) -> str | None:
        return self.ring.value

    @current.setter
    def current(self, value: str | None) -> None:
        self.ring.value = value

    def _film(self, seen: Seen, number: int) -> Reply:
        arrived = time.monotonic()
        sent = self._cookies(seen).get(self.cookie)
        reply = super()._film(seen, number)
        fresh = reply.headers.get("Set-Cookie", "").split(";", 1)[0].partition("=")[2] or None
        with self.ring.lock:
            self.ring.pages.append((arrived, self.film, sent, reply.status, fresh))
        return reply


# ------------------------------------------------------------------------------------------- tests
class RealProviderQueueTest(E2EBase):
    """C1: the production provider's film paths and codes through the worker and the handler."""

    def film(self, film: str, title: str, items: list[Item], files: dict[str, bytes], **options) -> FilmSite:
        return self.install_site(film=film, title=title, kind="film", pages=[items], files=files,
                                 session_value=CANARY_SID, **options)

    def test_films_go_straight_or_wait_for_a_variant(self):
        """C1a. A film with one variant in a complete list goes straight to input (one ticket load: the probe's
        ticket serves the transfer; no choice);
        one variant in a list with a "load more" button, and two variants, wait for the user's choice; the
        chosen 720p alone is fetched and lands with its own bytes; a video-only file fails at the probe and is
        never published."""
        sites = {
            "F3": self.film("F3", "Phim lẻ ba", [Item("m3", "v1080")], {"m3--v1080": CLIPS["a"]}),
            "F4": self.film("F4", "Phim lẻ bốn", [Item("m4", "v1080"), Item("m4", "v720", quality="720p")],
                            {"m4--v1080": CLIPS["a"], "m4--v720": CLIPS["b"]}),
            "F5": self.film("F5", "Phim lẻ năm", [Item("m5", "v720", quality="720p")], {"m5--v720": CLIPS["b"]},
                            more_button=True),
            "F11": self.film("F11", "Phim lẻ mười một", [Item("m11", "v1080")], {"m11--v1080": CLIPS["video_only"]}),
        }
        self.connect_alpha()
        status, answer = self.api("POST", "/api/downloads", {"urls": [site.url for site in sites.values()],
                                                             "rights_confirmed": True})
        self.assertEqual(status, 200)
        ids = {name: next(task["id"] for task in answer["tasks"] if task["url"] == site.url)
               for name, site in sites.items()}
        tasks = {name: self.wait_state(task_id, *SETTLED, timeout=2 * EDGE_SECONDS) for name, task_id in ids.items()}
        self.check("states", {name: (task["state"], task["error_code"]) for name, task in tasks.items()})

        # F3: one variant, list read whole: straight to input, no choice; one run: the probe's ticket is the one
        # the transfer uses (download_account_tickets).
        self.assertEqual(tasks["F3"]["state"], "COMPLETED")
        self.assertNotIn("NEEDS_CHOICE", self.kinds(ids["F3"]))
        self.assertEqual(sites["F3"].loads, {"m3--v1080": 1})
        f3_name = self.check("f3_name", Path(tasks["F3"]["output_path"]).name)
        self.assertEqual(f3_name, "Phim lẻ ba · 1080p · Vietsub.mkv")  # the film's title and its variant
        self.assertEqual((self.root / "input" / f3_name).read_bytes(), CLIPS["a"])

        # F5: one variant, but the page has a "load more" button: the user chooses, knowing the list is partial.
        self.assertEqual(tasks["F5"]["state"], "NEEDS_CHOICE")
        self.assertEqual([entry["title"] for entry in tasks["F5"]["entries"]],
                         ["720p · Vietsub · danh sách bản chưa đủ"])
        self.assertTrue(tasks["F5"]["probe"]["provider_choice"])
        self.assertEqual(sites["F5"].loads, {})  # no ticket before the choice

        # F11: a video without sound fails at the probe of its first ticket; nothing reaches input.
        self.assertEqual((tasks["F11"]["state"], tasks["F11"]["error_code"]), ("FAILED", "NO_AUDIO_STREAM"))
        self.assertIsNone(tasks["F11"]["output_path"])
        self.assertNotIn("COMPLETED", self.kinds(ids["F11"]))
        self.assertEqual(sites["F11"].loads, {"m11--v1080": 1})

        # F4: two variants wait for the choice; choosing 720p fetches only 720p and lands clip B.
        self.assertEqual(tasks["F4"]["state"], "NEEDS_CHOICE")
        titles = [entry["title"] for entry in tasks["F4"]["entries"]]
        self.assertEqual(self.check("f4_entries", titles), ["1080p · Vietsub", "720p · Vietsub"])
        self.assertEqual(sites["F4"].loads, {})
        status, snapshot = self.api("GET", "/api/downloads")
        self.assertEqual(status, 200)
        shown = next(task for task in snapshot["tasks"] if task["id"] == ids["F4"])
        self.assertEqual([entry["title"] for entry in shown["entries"]], titles)
        index = next(entry["index"] for entry in tasks["F4"]["entries"] if entry["title"] == "720p · Vietsub")
        status, chosen = self.api("POST", f"/api/downloads/{ids['F4']}/choose", {"entry_index": index})
        self.assertEqual(status, 200, chosen)
        f4 = self.wait_state(ids["F4"], *SETTLED, timeout=2 * EDGE_SECONDS)
        self.assertEqual((f4["state"], f4["chosen_entry"]), ("COMPLETED", index))
        self.assertEqual(sites["F4"].loads, {"m4--v720": 1})  # the probe of the chosen file; its ticket serves it
        self.assertNotIn("/f/m4--v1080", [item.path for item in self.server.requests])
        f4_name = self.check("f4_name", Path(f4["output_path"]).name)
        self.assertEqual(f4_name, "Phim lẻ bốn · 720p · Vietsub.mkv")
        self.assertEqual((self.root / "input" / f4_name).read_bytes(), CLIPS["b"])
        self.assertEqual(self.check("input", self.input_names()), sorted([f3_name, f4_name]))
        for name in ("F3", "F4"):
            verify = self.store.get(ids[name])["verify"]
            self.assertEqual((verify["ok"], verify["audio_codec"]), (True, "aac"))
        self.assertEqual(self.store.get(ids["F5"])["state"], "NEEDS_CHOICE")  # untouched by the other choice

        self.assert_kept_checks()
        self.results["completed"] = True

    # ------------------------------------------------------------------ C1b helpers
    def ring_film(self, ring: Ring, film: str, title: str, episode: str, clip: bytes) -> RingFilm:
        """A film of the rotating site, served and kept (every cookie it rotates to is a secret of the scans)."""
        return self.serve_site(RingFilm(film=film, title=title, kind="film", pages=[[Item(episode, "v1080")]],
                                        files={f"{episode}--v1080": clip}, rotating=True, ring=ring))

    def add(self, *urls: str) -> list[int]:
        """Paste ``urls`` through the real handler (rights confirmed); the new tasks' ids in that order."""
        status, answer = self.api("POST", "/api/downloads", {"urls": list(urls), "rights_confirmed": True})
        self.assertEqual(status, 200, answer)
        return [next(task["id"] for task in answer["tasks"] if task["url"] == url) for url in urls]

    def connect_beta(self) -> None:
        self.secrets.add(BETA_SESSION)
        attempt = self.manager.begin_login("beta")
        self.manager.complete_login(attempt, {"cookies": [cookie("sid", BETA_SESSION, ".beta.example")],
                                              "origins": []})

    def account_row(self, source_id: str = "alpha") -> dict[str, Any]:
        row = self.manager.store.get(source_id) or {}
        return {key: row.get(key) for key in ACCOUNT_FIELDS}

    def accounts_shown(self) -> dict[str, dict[str, Any]]:
        """The account panel's data in GET /api/downloads (the real handler)."""
        status, snapshot = self.api("GET", "/api/downloads")
        self.assertEqual(status, 200)
        return {item["id"]: {key: item[key] for key in ("state", "error_code", "waiting_tasks", "login_running",
                                                         "warnings")}
                for item in snapshot["accounts"]["sources"]}

    def output(self, task: dict[str, Any]) -> bytes:
        path = Path(task["output_path"])
        self.assertIn(path.name, self.input_names())
        return (self.root / "input" / path.name).read_bytes()

    def assert_never_asked_for_a_sign_in(self, *task_ids: int) -> None:
        for task_id in task_ids:
            task = self.store.get(task_id)
            self.assertNotEqual(task["error_code"], "SOURCE_LOGIN_REQUIRED", task_id)
            self.assertEqual((task["login_source"], task["login_reason"], task["login_generation"]),
                             (None, None, None), task_id)
            self.assertNotIn("WAITING_LOGIN", self.kinds(task_id))

    def assert_session_untouched(self, before: dict[str, Any]) -> None:
        """alpha's session is the one connected before (same generation and sign-in time, no error), still
        usable, and no sign-in window was ever asked for."""
        self.assertEqual(self.account_row(), before)
        self.assertEqual((before["session_state"], before["error_code"]), ("ACTIVE", None))
        self.assertEqual(self.manager.status("alpha")["state"], "CONNECTED")
        self.assertEqual(self.manager.session_gate("alpha"), (True, None, 1))
        self.assertEqual(self.windows.calls, [])
        self.assertFalse(self.runtime.busy())

    def assert_kept_checks(self, hosts: tuple[str, ...] = (PORTAL, TICKETS, FILES)) -> None:
        """No scan, no yt-dlp, no secret; every request went to ``hosts`` and the session browser looked up
        exactly alpha's portal and ticket hosts; only known POSTs, all answered."""
        self.assert_no_scan_and_no_yt_dlp()
        self.secret_scan()
        self.assert_network_kept(hosts, exact=True)
        self.assert_routes_kept()

    # ------------------------------------------------------------------ C1b
    def test_an_expired_ticket_or_a_refused_file_is_asked_for_again_never_a_sign_in(self):
        """C1b (R58, R59). F6: its first ticket page says the ticket expired, and the file host answers the first
        transfer with 401: one more ticket in the probe, one more in the middle of the transfer, and the film
        lands (three tickets: the probe's ready one serves the first transfer). F6R: the file host refuses the
        probe's ticket once (403): one more ticket in the probe, which then serves the transfer, and it lands. F6X: every ticket expires: the provider asks for TICKET_ATTEMPTS tickets, then the task fails
        TICKET_EXPIRED. None of them waits for a sign-in, and the session stays as it was."""
        f6 = self.film("F6", "Phim lẻ sáu", [Item("m6", "v1080")], {"m6--v1080": CLIPS["a"]},
                       tickets={"m6--v1080": TicketBehaviour(state="expired", times=1)})
        f6_file = FileScript(self.server, "m6--v1080",
                             lambda reply, count: Reply(b"sign in", 401, "text/plain") if count == 1 else reply)
        f6r = self.film("F6R", "Phim lẻ sáu từ chối", [Item("m6r", "v1080")], {"m6r--v1080": CLIPS["b"]},
                        refuse_files=1)
        f6x = self.film("F6X", "Phim lẻ sáu hết hạn", [Item("m6x", "v1080")], {"m6x--v1080": CLIPS["a"]},
                        tickets={"m6x--v1080": TicketBehaviour(state="expired")})
        self.connect_alpha()
        before = self.account_row()
        ids = dict(zip(("F6", "F6R", "F6X"), self.add(f6.url, f6r.url, f6x.url)))
        tasks = {name: self.wait_state(task_id, *SETTLED, timeout=BATCH_SECONDS) for name, task_id in ids.items()}
        self.check("states", {name: (task["state"], task["error_code"]) for name, task in tasks.items()})

        # F6: expired once in the probe and refused (401) once in the transfer: three tickets, then the file lands.
        self.assertEqual((tasks["F6"]["state"], tasks["F6"]["error_code"]), ("COMPLETED", None))
        self.assertEqual(f6.loads, {"m6--v1080": 3})  # expired + ready (probe, which serves the transfer), refreshed
        transfers = self.check("f6_transfers", f6_file.transfers())
        self.assertEqual([(status, wanted) for wanted, _if_range, status, _cut in transfers], [(401, ""), (200, "")])
        self.assertEqual(self.output(tasks["F6"]), CLIPS["a"])
        # F6R: the probe's link refused once (403): another ticket in the same probe, then the file lands.
        self.assertEqual((tasks["F6R"]["state"], tasks["F6R"]["error_code"]), ("COMPLETED", None))
        self.assertEqual((f6r.refused, f6r.loads), (1, {"m6r--v1080": 2}))
        self.assertEqual(self.output(tasks["F6R"]), CLIPS["b"])
        # F6X: tickets that always expire: a bounded number of them, then the task fails with that code.
        self.assertEqual((tasks["F6X"]["state"], tasks["F6X"]["error_code"]), ("FAILED", "TICKET_EXPIRED"))
        self.assertEqual(f6x.loads, {"m6x--v1080": TICKET_ATTEMPTS})
        self.assertIsNone(tasks["F6X"]["output_path"])
        self.assertEqual(self.server.count("/f/m6x--v1080"), 0)
        self.assertEqual(len(self.input_names()), 2)

        self.assert_never_asked_for_a_sign_in(*ids.values())
        self.assert_session_untouched(before)
        self.assertEqual(self.accounts_shown()["alpha"], {"state": "CONNECTED", "error_code": None,
                                                          "waiting_tasks": 0, "login_running": False,
                                                          "warnings": []})
        self.assert_kept_checks()
        self.results["completed"] = True

    def test_page_server_network_and_gone_errors_keep_their_codes_and_only_sign_out_evidence_waits(self):
        """C1b (R58, R60). F7: the film page answers 403 without sign-in evidence: FAILED FORBIDDEN. F8: it answers
        503: FAILED SERVER_BUSY. F10: the file host says the ticket's file is gone (404): FAILED UNAVAILABLE. F12:
        the transfer is cut halfway and the host keeps dropping the connection: after the transfer's own retries
        the task is INTERRUPTED (NETWORK) with its part kept, and Tiếp tục (POST …/resume) checks the link kept in
        memory with one sample read (no ticket) and continues that part with Range and If-Range. None of them waits for a sign-in or touches a session. Last, F9: the
        film page says the session is signed out: WAITING_LOGIN (SESSION_REJECTED), only alpha's generation 1 is
        marked invalid, beta's session stays, the gate then refuses, and no window opens."""
        clip = CLIPS["a"]
        half = len(clip) // 2
        healed = threading.Event()
        sites = {  # F12 first: its retries run while the others' hidden runs take their turns
            "F12": self.film("F12", "Phim lẻ mười hai", [Item("m12", "v1080")], {"m12--v1080": clip}),
            "F7": self.film("F7", "Phim lẻ bảy", [Item("m7", "v1080")], {"m7--v1080": clip}, status_of_page={1: 403}),
            "F8": self.film("F8", "Phim lẻ tám", [Item("m8", "v1080")], {"m8--v1080": clip}, status_of_page={1: 503}),
            "F10": self.film("F10", "Phim lẻ mười", [Item("m10", "v1080")], {"m10--v1080": clip},
                             gone={"m10--v1080"}),
        }
        f12_file = FileScript(self.server, "m12--v1080", lambda reply, count: reply if healed.is_set()
                              else replace(reply, cut_after=half if count == 1 else 0))
        etag = '"m12--v1080-1"'  # the fixture file's ETag, the same for every ticket
        # A still clock for the kept links: their idle limit (10 min) is not what this test checks, and the waits
        # below may take longer under load; the check of the kept link at Tiếp tục is.
        self.worker.tickets = TicketCache(clock=lambda: 0.0)
        self.connect_alpha()
        self.connect_beta()
        before = {source: self.account_row(source) for source in ("alpha", "beta")}
        ids = dict(zip(sites, self.add(*(site.url for site in sites.values()))))
        tasks = {name: self.wait_state(task_id, *SETTLED, timeout=BATCH_SECONDS) for name, task_id in ids.items()}
        self.check("states", {name: (task["state"], task["error_code"]) for name, task in tasks.items()})

        self.assertEqual({name: (task["state"], task["error_code"]) for name, task in tasks.items()},
                         {"F12": ("INTERRUPTED", "NETWORK"), "F7": ("FAILED", "FORBIDDEN"),
                          "F8": ("FAILED", "SERVER_BUSY"), "F10": ("FAILED", "UNAVAILABLE")})
        for name, task in tasks.items():
            self.wait_event(ids[name], task["state"])
        self.assertEqual({name: [event["payload"]["code"] for event in self.store.events(ids[name])
                                 if event["kind"] == "FAILED"] for name in ("F7", "F8", "F10")},
                         {"F7": ["FORBIDDEN"], "F8": ["SERVER_BUSY"], "F10": ["UNAVAILABLE"]})
        # No ticket for a page that failed; one for the gone file; the probe's for F12, which served its transfer.
        self.assertEqual({name: site.loads for name, site in sites.items()},
                         {"F12": {"m12--v1080": 1}, "F7": {}, "F8": {}, "F10": {"m10--v1080": 1}})
        # F12: cut at half, then every retry of the transfer (Range from the kept part) dropped before a byte.
        cut = self.check("f12_transfers", f12_file.transfers())
        self.assertEqual(cut, [("", "", 200, half)] + [(f"bytes={half}-", etag, 200, 0)] * FILE_RETRIES)
        self.assertEqual((Path(tasks["F12"]["temp_dir"]) / PART_NAME).read_bytes(), clip[:half])
        self.assertEqual([event["payload"] for event in self.store.events(ids["F12"])
                          if event["kind"] == "INTERRUPTED"], [{"code": "NETWORK"}])
        self.assertEqual(self.input_names(), [])
        self.assert_never_asked_for_a_sign_in(*ids.values())
        self.assert_session_untouched(before["alpha"])
        self.assertEqual(self.account_row("beta"), before["beta"])

        # Tiếp tục: the file host answers again; the kept link is checked by one sample read (no ticket, no
        # browser) and the kept part goes on from where it stopped.
        healed.set()
        samples = sum(1 for kind, *_ in f12_file.requests if kind == "probe")
        status, answer = self.api("POST", f"/api/downloads/{ids['F12']}/resume")
        self.assertEqual((status, answer["task"]["state"]), (200, "QUEUED"))
        f12 = self.wait_state(ids["F12"], *SETTLED, timeout=BATCH_SECONDS)
        self.assertEqual((f12["state"], f12["error_code"]), ("COMPLETED", None))
        self.assertEqual(f12_file.transfers()[len(cut):], [(f"bytes={half}-", etag, 200, None)])
        self.assertEqual(sum(1 for kind, *_ in f12_file.requests if kind == "probe") - samples, 1)
        self.assertEqual(sites["F12"].loads, {"m12--v1080": 1})
        self.assertIn("RESUMED", self.kinds(ids["F12"]))
        self.assertEqual(self.output(f12), clip)
        self.assert_never_asked_for_a_sign_in(*ids.values())
        self.assert_session_untouched(before["alpha"])
        self.assertEqual(self.accounts_shown()["alpha"], {"state": "CONNECTED", "error_code": None,
                                                          "waiting_tasks": 0, "login_running": False,
                                                          "warnings": []})

        # Last: the source's own page says the session is signed out. Only this waits for a sign-in.
        f9 = self.film("F9", "Phim lẻ chín", [Item("m9", "v1080")], {"m9--v1080": clip}, signed_out_page=True)
        f9_id = self.add(f9.url)[0]
        waiting = self.wait_state(f9_id, *SETTLED, timeout=2 * EDGE_SECONDS)
        fields = ("state", "error_code", "login_source", "login_reason", "login_generation", "account_owner")
        self.assertEqual(self.check("f9", {key: waiting[key] for key in fields}),
                         {"state": "WAITING_LOGIN", "error_code": "SOURCE_LOGIN_REQUIRED", "login_source": "alpha",
                          "login_reason": "SESSION_REJECTED", "login_generation": 1, "account_owner": FIRST})
        self.wait_event(f9_id, "WAITING_LOGIN")
        self.assertEqual(self.kinds(f9_id).count("WAITING_LOGIN"), 1)
        self.assertEqual((f9.loads, self.server.count("/film/F9")), ({}, 1))  # no ticket on a signed-out page
        self.assertEqual(self.account_row(), {**before["alpha"], "session_state": "INVALID",
                                              "error_code": "SESSION_REJECTED"})
        self.assertEqual(self.account_row("beta"), before["beta"])
        self.assertEqual(self.manager.session_gate("alpha"), (False, "SESSION_REJECTED", 1))
        self.assertEqual(self.manager.session_gate("beta"), (True, None, 1))
        shown = self.accounts_shown()
        self.assertEqual(shown["alpha"], {"state": "NEEDS_LOGIN", "error_code": "SESSION_REJECTED",
                                          "waiting_tasks": 1, "login_running": False, "warnings": []})
        self.assertEqual((shown["beta"]["state"], shown["beta"]["waiting_tasks"]), ("CONNECTED", 0))
        self.assertEqual(self.windows.calls, [])
        self.assertFalse(self.runtime.busy())
        self.assertEqual({name: self.store.get(task_id)["state"] for name, task_id in ids.items()},
                         {"F12": "COMPLETED", "F7": "FAILED", "F8": "FAILED", "F10": "FAILED"})
        self.assertEqual(self.server.count("/film/F9"), 1)  # still one: the waiting task asked nothing more
        self.assert_kept_checks()
        self.results["completed"] = True

    def test_runs_of_one_source_take_turns_and_never_hold_the_worker_or_the_store(self):
        """C1b (R55). Two film pages of one rotating site (each page wants the latest session cookie and sets the
        next one) pasted together. The first ticket page asked for is held at the fixture server: while that
        hidden run waits inside its browser, the other page's task has its slot but waits for the source's lock
        (no request, no second browser profile), and the real handler answers GET /api/downloads, a pasted direct
        link and slots 3, and the dispatcher starts that link in the third slot, which fetches its file. Then both
        pages complete: two hidden runs (each probe's ticket serves its transfer) that never overlapped, the second
        film page sent the cookie the first run saved, and the session's generation and sign-in time are
        unchanged."""
        ring = Ring()
        sites = {"F13": self.ring_film(ring, "F13", "Phim xoay một", "r13", CLIPS["a"]),
                 "F14": self.ring_film(ring, "F14", "Phim xoay hai", "r14", CLIPS["b"])}
        self.server.route(DIRECT_PATH, Reply(CLIPS["b"], content_type="video/x-matroska", etag='"direct-1"'))
        hold = TicketHold(self.server, ["r13--v1080", "r14--v1080"], HOLD_SECONDS)
        self.addCleanup(hold.release.set)  # a failed test never leaves the fixture server holding a page
        watch = RunWatch()
        watch.install(self)
        self.connect_alpha()
        before = self.account_row()
        ids = dict(zip(sites, self.add(*(site.url for site in sites.values()))))

        self.assertTrue(hold.entered.wait(BATCH_SECONDS), "no ticket page was asked for")
        held = next(name for name, site in sites.items() if hold.key in site.files)
        other = next(name for name in sites if name != held)
        held_thread, other_thread = (f"biliflow-download-{ids[name]}" for name in (held, other))
        self.wait_until(lambda: watch.waiting(other_thread), 30, "the other page's run waiting for the source")
        # The real handler answers while that run is held (each answer is checked to come before the hold ended).
        timings: dict[str, float] = {}

        def timed(name: str, call: Callable[[], Any]) -> Any:
            started = time.monotonic()
            value = call()
            timings[name] = round(time.monotonic() - started, 3)
            self.assertTrue(hold.holding, f"{name} answered only after the held run went on (limit {HOLD_SECONDS} s)")
            return value
        status, snapshot = timed("get", lambda: self.api("GET", "/api/downloads"))
        self.assertEqual(status, 200)
        # Both pages have a slot (PROBING); the other's run has not reached the source: no request, one browser.
        self.assertEqual({task["id"]: task["state"] for task in snapshot["tasks"]},
                         {ids[held]: "PROBING", ids[other]: "PROBING"})
        self.assertEqual((self.server.count(f"/film/{sites[other].film}"), sites[other].loads), (0, {}))
        self.assertEqual(len(list(self.manager.vault.browser_folder().iterdir())), 1)  # one browser profile
        direct = timed("post_link", lambda: self.add(DIRECT_URL))[0]
        self.assertEqual(timed("post_slots", lambda: self.api("POST", "/api/downloads/settings", {"slots": 3})),
                         (200, {"slots": 3}))
        # The dispatcher (the worker's lock) starts the direct link in the third slot, which fetches its file.
        timed("dispatch", lambda: self.wait_until(
            lambda: self.server.count(DIRECT_PATH) > 0 and self.store.get(direct)["state"] != "QUEUED",
            HOLD_SECONDS, "the direct link started in the third slot"))
        self.check("while_held", {"held": held, "seconds": timings, "direct_state": self.store.get(direct)["state"]})
        self.assertTrue(hold.holding)
        hold.release.set()

        tasks = {name: self.wait_state(task_id, *SETTLED, timeout=BATCH_SECONDS) for name, task_id in ids.items()}
        direct_task = self.wait_state(direct, *SETTLED, timeout=BATCH_SECONDS)
        self.assertFalse(hold.expired)
        self.check("states", {name: (task["state"], task["error_code"]) for name, task in
                              {**tasks, "direct": direct_task}.items()})
        self.assertEqual({name: (task["state"], task["error_code"]) for name, task in tasks.items()},
                         {"F13": ("COMPLETED", None), "F14": ("COMPLETED", None)})
        self.assertEqual((direct_task["state"], direct_task["probe"]["provider"]), ("COMPLETED", "direct"))
        self.assertEqual({name: site.loads for name, site in sites.items()},
                         {"F13": {"r13--v1080": 1}, "F14": {"r14--v1080": 1}})
        self.assertEqual([self.output(tasks["F13"]), self.output(tasks["F14"]), self.output(direct_task)],
                         [CLIPS["a"], CLIPS["b"], CLIPS["b"]])

        # The runs took turns: two browsers of alpha, never two at once; the other page's run asked for the source
        # while the held run had it, and got it only after that run's browser had closed.
        runs = self.check("runs", [(round(end - start, 2), thread) for start, end, thread in watch.of("alpha")])
        alpha = watch.of("alpha")
        self.assertEqual(Counter(thread for *_, thread in alpha), {held_thread: 1, other_thread: 1}, runs)
        for (_start, end, _thread), (start, _end, _next) in zip(alpha, alpha[1:]):
            self.assertLessEqual(end, start)
        held_end = next(end for _start, end, thread in alpha if thread == held_thread)
        asked = watch.first_wait(other_thread)
        self.assertLess(asked["asked"], held_end)
        self.assertGreaterEqual(asked["got"], held_end)
        # ... and kept each other's cookies: each film page carried the cookie the run before it saved. results.json
        # keeps only whether it did (never a cookie value).
        pages = sorted(ring.pages)
        sent_before = [CANARY_SID] + [fresh for *_, fresh in pages[:-1]]
        self.check("ring", [{"film": film, "status": status, "sent_previous": sent == before,
                             "set_new": bool(fresh) and fresh != sent}
                            for (_at, film, sent, status, fresh), before in zip(pages, sent_before)])
        self.assertEqual([status for *_, status, _fresh in pages], [200] * 2)
        self.assertEqual([sent for _at, _film, sent, _status, _fresh in pages], sent_before)
        self.assertEqual(self.server.count("/login"), 0)  # no stale cookie was ever sent to the sign-in page
        stored = {item["name"]: item["value"] for item in self.manager.session_for("alpha").state["cookies"]}
        self.assertEqual(stored["sid"], pages[-1][4])
        self.assertEqual(self.alpha.warnings, {})  # every rotated cookie was saved
        for task_id in (*ids.values(), direct):
            self.assert_never_asked_for_a_sign_in(task_id)
        self.assert_session_untouched(before)  # rotations never extend or replace the session
        self.assertEqual([item.path for item in self.server.requests
                          if item.host == DIRECT_HOST and "cookie" in item.headers], [])
        self.assert_kept_checks((PORTAL, TICKETS, FILES, DIRECT_HOST))
        self.results["completed"] = True


if __name__ == "__main__":
    unittest.main()
