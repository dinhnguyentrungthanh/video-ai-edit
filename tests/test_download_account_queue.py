"""Source accounts in the real download queue (M4, docs/SOURCE_ACCOUNTS_PLAN.md 9.14): WAITING_LOGIN, episode
previews and groups, names in input, warnings and errors, through DownloadWorker and the Control Center routes.

The provider is tests/account_queue_fixtures.QueueProvider: the real provider's queue side with a made-up list
and the real AccountManager (fake vault, fake clock) of a temporary root under the install's temp/. Files are
self-made clips served by the fixture server on 127.0.0.1 through the downloader's own cookie-free SafeHttp.
No browser, no sign-in window, no real site, no project state: yt-dlp is the fake and is never called.

M6 crossings (plan section 9.18) on the same harness: a 500-episode group through the routes and the running
worker (BigGroupTest, the 100 cap checked inside every write transaction), group routes over HTTP on every
member state, a failed episode and the removal of its group, a removal refused while members wait, a list read
again (STALE_PREVIEW), episodes the page lists later, "all" on an incomplete list, the phone listener's
refusals before any body (also while a PC sign-in runs), another Windows account's task, a restart of the whole
DownloadService, a stop that waits for a real sign-in thread (the coordinator with a fake window and a held
check) and a full disk on the account path.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from biliflow.control_center import ControlCenter, _handler_class, _phone_access, _phone_handler_class
from biliflow.download_account_api import AccountRuntime
from biliflow.download_account_config import AccountConfig, DroppedSource
from biliflow.download_account_driver import DriverHang
from biliflow.download_account_runs import SessionRun
from biliflow.download_account_sources import WARNINGS
from biliflow.download_accounts import FALLBACK_TTL_SECONDS
from biliflow.download_api import DownloadService
from biliflow.download_http import HttpError
from biliflow.download_links import DownloadBatchError
from biliflow.download_media_file import PART_NAME
from biliflow.download_provider_config import HostList
from biliflow.download_runner import YtDlpRunner
from biliflow.download_source_types import SourceChanged, SourceDeclined, SourceError, SourceLoginRequired
from biliflow.download_sources import DirectMediaProvider, SourceRegistry, SourceTransfers, resumable_error
from biliflow.download_store import CLOSED_STATES, FINAL_STATES, SLOT_STATES, DownloadStore
from biliflow.download_worker import DownloadActionError, DownloadWorker
from biliflow.http_guards import REQUEST_TIMEOUT_SECONDS
from biliflow.job_store import JobStore
from biliflow.phone_access import PC_ONLY_SOURCE_ACCOUNTS
from biliflow.review_evidence import ReviewFrameCache
from biliflow.scheduler import JobScheduler
from tests.account_queue_fixtures import (
    OTHER_SID,
    SID,
    TITLE,
    QueueProvider,
    account_manager,
    account_source,
    connect,
    episode_of,
    file_path,
    film,
    page_url,
    series,
)
from tests.source_fixtures import (
    FFMPEG,
    FFPROBE,
    HAVE_FFMPEG,
    NEED_FFMPEG,
    FixtureServer,
    Reply,
    make_clip,
    public_resolver,
    remove_tree,
)
from tests.test_dashboard_v2_phone_hardening import FAKE_LAN, http, raw
from tests.test_download_account_login import FakeWindow, GatedVerifier
from tests.test_download_accounts import TEMP_PARENT, Clock, state
from tests.test_download_worker import FAKE, GB, ok_verifier

MEDIA: dict[str, object] = {}
KIND_1080 = "1080p|vietsub"
TOKEN = "test-token"
SNIFF = re.compile(r"bytes=0-\d+")
WAIT = 60.0
# An account route sent to the phone listener with a Content-Length and no body. A handler that read the body
# would hold its reply for the request timeout (the production REQUEST_TIMEOUT_SECONDS, 20 s) and then answer
# 408; waiting half of it leaves a loaded machine 10 s to send the refusal (normally milliseconds, plus the 1 s
# drain before the close) and still proves that the answer never waited for the body.
NO_BODY_WAIT = REQUEST_TIMEOUT_SECONDS / 2
REMOVE_REFUSED = "Nhóm còn tập chưa tải xong hoặc đang chờ; bấm Hủy nhóm trước"


def setUpModule():
    if not HAVE_FFMPEG:
        return
    TEMP_PARENT.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="account-queue-media-", dir=TEMP_PARENT))
    # Registered before any clip is made: unittest runs module cleanups even when setUpModule fails.
    unittest.addModuleCleanup(remove_tree, inside_temp(base))
    MEDIA["base"] = base
    MEDIA["mp4"] = make_clip(base / "clip.mp4", seconds=2, size="160x120").read_bytes()
    MEDIA["mkv"] = make_clip(base / "clip.mkv", seconds=2, size="160x120", container="mkv").read_bytes()


def inside_temp(path: Path) -> Path:
    """``path`` (absolute, resolved) when it lies strictly inside the install's temp/; a recursive delete of
    anything else is refused."""
    resolved, parent = Path(path).resolve(), TEMP_PARENT.resolve()
    if parent not in resolved.parents:
        raise AssertionError(f"refusing to delete outside {parent}: {resolved}")
    return resolved


def wait_until(predicate, timeout: float = WAIT, step: float = 0.01) -> bool:
    """Poll ``predicate`` until it holds or ``timeout`` passes (a condition wait, never a fixed sleep)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())


class PublicPages:
    """A public site provider of the local config (no login gate): it declines, so yt-dlp would read it."""
    id = "pub"
    label = "Trang công khai"

    def claims(self, url):
        return True

    def resolve(self, url, ctx):
        raise SourceDeclined("không phải trang phim")


class NoWindow:
    """A sign-in launcher that must never run in these tests."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("no sign-in window may open here")


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class QueueCase(unittest.TestCase):
    """A worker whose account sources are alpha (a 12-episode series) and beta, with the real manager."""

    def make_listing(self):
        return series(12)

    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._temp = TemporaryDirectory(dir=TEMP_PARENT, prefix="account-queue-")
        self.root = Path(self._temp.name)
        (self.root / "config").mkdir()
        (self.root / "input").mkdir()
        self.server = FixtureServer()
        self.clock = Clock()
        self.config = AccountConfig({item.id: item for item in (account_source("alpha"), account_source("beta"))})
        self.manager = account_manager(self.root, self.config, clock=self.clock)
        self.alpha = QueueProvider(self.config.sources["alpha"], self.manager, self.make_listing())
        self.beta = QueueProvider(self.config.sources["beta"], self.manager, series(2, source_id="beta"))
        self.dropped: tuple = ()
        self.extra_providers: list = []
        self.transfers: list[str] = []
        self.on_transfer = None
        self.space = (10_000 * GB, 100 * GB)  # (free, reserve) the worker's space probe reports
        self.closing: list = []  # closed by tearDown before the temporary root goes (other managers, runtimes)
        self.held: list[threading.Event] = []  # set first by tearDown (see hold)
        self.store = DownloadStore(self.root / "state" / "downloads.sqlite3")
        self.worker = self.make_worker()
        self.serve(self.alpha.listing)
        self.serve(self.beta.listing)

    def hold(self, event: threading.Event) -> threading.Event:
        """An event a fixture route, a file check or a sign-in check waits on. tearDown sets it before anything
        stops (unittest runs tearDown before the addCleanup callbacks), so a test that fails while something
        waits on it never shuts the worker, a service or a sign-in down under a held request."""
        self.held.append(event)
        return event

    def release_held(self):
        for event in self.held:
            event.set()

    def tearDown(self):
        try:
            self.release_held()
            for task in self.store.list_tasks():
                try:
                    self.worker.cancel(task["id"])
                except DownloadActionError:
                    pass
            self.worker.shutdown(10)
            self.store.close()
            for close in reversed(self.closing):
                close()
            self.manager.close()
        finally:
            self.server.close()
            inside_temp(self.root)  # checked before the recursive delete
            self._temp.cleanup()

    def registry(self, providers=None):
        providers = providers if providers is not None else [self.alpha, self.beta]
        hosts = {provider.id: provider.hosts for provider in providers}
        hosts.update({provider.id: HostList(("gamma.example",)) for provider in self.extra_providers})
        return SourceRegistry([*providers, *self.extra_providers, DirectMediaProvider()], site_hosts=hosts,
                              dropped=self.dropped)

    def make_worker(self, providers=None, *, store=None, verifier=ok_verifier):
        runner = YtDlpRunner(self.root, command_prefix=[sys.executable, str(FAKE)], deno_path=None,
                             env_extra={"FAKE_YTDLP_SCENARIO": str(self.root / "no-scenario.json"),
                                        "FAKE_YTDLP_LOG": str(self.root / "ytdlp-calls.jsonl")})
        http_client = self.server.http()
        return DownloadWorker(self.root, store or self.store, runner=runner, verifier=verifier, ffmpeg=FFMPEG,
                              ffprobe=FFPROBE, space_probe=lambda root: self.space,
                              resolver=public_resolver, space_retry_seconds=0.05,
                              cache_pruner=lambda root: {"removed_files": 0}, sources=self.registry(providers),
                              http=http_client, transfers=SourceTransfers(http_client, ffmpeg=FFMPEG, ffprobe=FFPROBE))

    def restart(self, providers=None):
        self.worker.shutdown(10)
        self.store.close()
        self.store = DownloadStore(self.root / "state" / "downloads.sqlite3")
        self.worker = self.make_worker(providers)

    # ------------------------------------------------------------------ files
    def serve(self, listing, media="mp4", delays=None):
        for episode in listing.episodes:
            for variant in episode.variants:
                self.server.route(file_path(episode.key, variant.id),
                                  self.file_route(MEDIA[media], media, (delays or {}).get(episode.key, 0.0)))

    def file_route(self, body, media, delay):
        content_type = "video/x-matroska" if media == "mkv" else "video/mp4"

        def route(seen, number):
            wanted = seen.headers.get("range", "")
            if not SNIFF.fullmatch(wanted):  # a transfer request, not the probe's sample
                self.transfers.append(wanted)
                if self.on_transfer is not None:
                    reply = self.on_transfer(len(self.transfers), body)
                    if reply is not None:
                        return reply
            return Reply(body, content_type=content_type, etag='"v1"', delay=delay, chunk=8 * 1024)
        return route

    def file_requests(self):
        return sum(1 for item in self.server.requests if item.path.startswith("/files/"))

    # ------------------------------------------------------------------ queue
    def add(self, *urls):
        return self.worker.add(list(urls or [page_url()]), rights_confirmed=True)

    def run_all(self, timeout=60.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.worker.dispatch()
            self.assertTrue(self.worker.wait_idle(timeout))
            if not self.store.next_queued(exclude=self.worker.running_ids()) and not self.worker.running_ids():
                return
        self.fail(f"tasks did not settle: {[(t['id'], t['state']) for t in self.store.list_tasks()]}")

    def step(self):
        """One dispatch pass with one slot: the oldest queued task, run to its end."""
        started = self.worker.dispatch()
        self.assertEqual(len(started), 1, [(t["id"], t["state"]) for t in self.store.list_tasks()])
        self.assertTrue(self.worker.wait_idle(WAIT))
        return started[0]

    def task(self, task_id):
        return self.store.get(task_id)

    def events(self, task_id):
        return [event["kind"] for event in self.store.events(task_id)]

    def ytdlp_called(self):
        return (self.root / "ytdlp-calls.jsonl").exists()

    def waiting_page(self):
        connect(self.manager)
        task, = self.add()
        self.run_all()
        self.assertEqual(self.task(task["id"])["state"], "NEEDS_CHOICE", self.store.events(task["id"]))
        return self.task(task["id"])

    def confirm(self, task_id, selection=None, key="key-00000001", fingerprint=None, **extra):
        fingerprint = fingerprint or self.worker.episodes(task_id)["fingerprint"]
        body = {"selection": selection or {"mode": "all", "variant_kind": KIND_1080},
                "fingerprint": fingerprint, "idempotency_key": key, **extra}
        return self.worker.confirm_episodes(task_id, body)

    def input_names(self):
        return sorted(path.name for path in (self.root / "input").iterdir())

    def input_files(self):
        return {name: (self.root / "input" / name).read_bytes() for name in self.input_names()}

    def vault_files(self, sid):
        """Every file of one Windows account's session folder (made-up sessions of this temporary root)."""
        folder = self.root / "state" / "source-accounts" / sid
        return {path.relative_to(folder).as_posix(): path.read_bytes()
                for path in sorted(folder.rglob("*")) if path.is_file()}


class WaitingLoginTest(QueueCase):
    def test_a_page_without_a_session_waits_without_a_slot_while_other_links_run(self):
        self.worker.set_slots(1)
        self.server.route("/v/clip.mp4", Reply(MEDIA["mp4"], content_type="video/mp4", etag='"c1"'))
        page, other = self.add(page_url(), "http://media.example/v/clip.mp4")
        self.assertEqual(page["account_owner"], SID)
        self.run_all()
        waiting = self.task(page["id"])
        self.assertEqual((waiting["state"], waiting["login_source"], waiting["login_reason"], waiting["error_code"]),
                         ("WAITING_LOGIN", "alpha", "NOT_CONNECTED", "SOURCE_LOGIN_REQUIRED"))
        self.assertIn("không tự mở cửa sổ đăng nhập", waiting["error_message"])
        self.assertEqual(self.task(other["id"])["state"], "COMPLETED")
        self.assertEqual(self.alpha.calls, [])  # parked before any slot: no hidden run at all
        requests = len(self.server.requests)
        for _ in range(5):  # no loop: more passes change nothing and add no event
            self.worker.dispatch()
        for _ in range(5):  # a sign-in callback without a session (the check runs at once) changes nothing either
            self.worker.wake_logins()
            self.worker.dispatch()
        self.assertEqual(self.events(page["id"]).count("WAITING_LOGIN"), 1)
        self.assertNotIn(page["id"], self.worker.running_ids())
        self.assertEqual(self.task(page["id"])["state"], "WAITING_LOGIN")
        self.assertEqual(self.alpha.calls, [])  # still no hidden run after the extra passes
        self.assertEqual(len(self.server.requests), requests)
        self.assertFalse(self.ytdlp_called())

    def test_a_sign_in_wakes_only_its_source_and_account_keeping_the_queue_place(self):
        alpha, beta = self.add(page_url(), page_url("beta"))
        other, = self.store.add_tasks([page_url(film="f9")], [OTHER_SID])  # added under another Windows account
        self.run_all()
        self.assertEqual([self.task(t["id"])["state"] for t in (alpha, beta, other)], ["WAITING_LOGIN"] * 3)
        self.assertEqual(self.task(other["id"])["login_reason"], "OTHER_ACCOUNT")
        queued_at = self.task(alpha["id"])["queued_at"]
        connect(self.manager, "alpha")
        self.worker.wake_logins()
        self.run_all()
        woken = self.task(alpha["id"])
        self.assertEqual((woken["state"], woken["queued_at"]), ("NEEDS_CHOICE", queued_at))
        self.assertIn("LOGIN_RESUMED", self.events(alpha["id"]))
        self.assertEqual(self.task(beta["id"])["state"], "WAITING_LOGIN")
        self.assertEqual(self.task(other["id"])["state"], "WAITING_LOGIN")  # never this account's session

    def account_service(self, manager):
        """The downloads page's side of one Windows account: its runtime (no window) over the current worker."""
        runtime = AccountRuntime(self.root, manager=manager,
                                 coordinator_options={"launcher": NoWindow(), "verifiers": {}})
        self.closing.append(lambda: runtime.stop(5))
        return DownloadService(self.root, cleanable=lambda: (0, 0), bin_reader=None, store=self.store,
                               worker=self.worker, accounts=runtime)

    @staticmethod
    def waiting(service):
        return {item["id"]: item["waiting_tasks"] for item in service.snapshot()["accounts"]["sources"]}

    def test_another_accounts_task_is_not_counted_here_and_wakes_only_under_its_own_account(self):
        connect(self.manager)  # this Windows account (SID) is signed in to alpha, not to beta
        own, own_beta = self.add(page_url(), page_url("beta"))
        other, = self.store.add_tasks([page_url(film="f9")], [OTHER_SID])  # added under another Windows account
        self.run_all()
        self.assertEqual(self.task(own["id"])["state"], "NEEDS_CHOICE")
        self.assertEqual(self.task(own_beta["id"])["login_reason"], "NOT_CONNECTED")
        parked = self.task(other["id"])
        self.assertEqual((parked["state"], parked["login_source"], parked["login_reason"]),
                         ("WAITING_LOGIN", "alpha", "OTHER_ACCOUNT"))
        self.assertEqual(len(self.alpha.calls), 1)  # the own page's probe: never a run for the other task
        # The panel counts only what this account's sign-in can wake: alpha's waiting task is the other account's.
        self.assertEqual(self.waiting(self.account_service(self.manager)), {"alpha": 0, "beta": 1})
        kept = (self.manager.status("alpha"), self.manager.session_gate("alpha"), self.vault_files(SID))
        self.assertTrue(kept[2])
        # The other Windows account starts BiliFlow on the same root: its own manager, providers and worker.
        manager = account_manager(self.root, self.config, user_sid=OTHER_SID, clock=self.clock)
        self.closing.append(manager.close)
        alpha = QueueProvider(self.config.sources["alpha"], manager, self.alpha.listing)
        beta = QueueProvider(self.config.sources["beta"], manager, self.beta.listing)
        seen = []
        alpha.before = lambda ctx: seen.append((int(ctx.task_dir.name), manager.session_gate("alpha")[2]))
        self.restart(providers=[alpha, beta])
        second = self.account_service(manager)
        self.assertEqual(self.waiting(second), {"alpha": 1, "beta": 0})  # its own task, for its own sign-in
        self.run_all()
        self.assertEqual(self.task(other["id"])["state"], "WAITING_LOGIN")  # that account is not signed in yet
        self.assertEqual(seen, [])
        connect(manager, value="b1")
        connect(manager, value="b2")  # generation 2 of that account; the first account keeps generation 1
        connect(manager, "beta", value="b3")
        self.worker.wake_logins()
        self.run_all()
        woken = self.task(other["id"])
        self.assertEqual(woken["state"], "NEEDS_CHOICE", self.store.events(other["id"]))
        self.assertIn("LOGIN_RESUMED", self.events(other["id"]))
        result = self.confirm(other["id"], {"mode": "pick", "episodes": ["s1e1"], "variant_kind": KIND_1080})
        self.run_all(90)
        episode, = self.worker.groups.tasks_of(result["group"]["id"])
        self.assertEqual((episode["state"], episode["account_owner"]), ("COMPLETED", OTHER_SID),
                         self.store.events(episode["id"]))
        self.assertEqual({task_id for task_id, _ in seen}, {other["id"], episode["id"]})
        self.assertEqual({generation for _, generation in seen}, {2})  # that account's own session every time
        self.assertEqual(self.input_names(), [f"001 - {TITLE} - S01E01.mp4"])
        self.assertEqual(self.waiting(second), {"alpha": 0, "beta": 0})
        # The first account's tasks and session stay as they were: its page waits for its user, its beta link
        # for its own sign-in (another account's beta session never wakes it).
        self.assertEqual(self.task(own["id"])["state"], "NEEDS_CHOICE")
        self.assertEqual(self.task(own_beta["id"])["state"], "WAITING_LOGIN")
        self.assertEqual(beta.calls, [])
        self.assertEqual((self.manager.status("alpha"), self.manager.session_gate("alpha"), self.vault_files(SID)),
                         kept)

    def test_an_expired_session_waits_and_only_a_newer_sign_in_wakes_it_even_without_a_callback(self):
        connect(self.manager)
        generation = self.manager.session_gate("alpha")[2]
        self.clock.at(FALLBACK_TTL_SECONDS + 1)
        task, = self.add()
        self.run_all()
        waiting = self.task(task["id"])
        self.assertEqual((waiting["state"], waiting["login_reason"], waiting["login_generation"]),
                         ("WAITING_LOGIN", "SESSION_EXPIRED", generation))
        self.worker.wake_logins()  # a stale callback: nothing newer than the failed session
        self.run_all()
        self.assertEqual(self.task(task["id"])["state"], "WAITING_LOGIN")
        connect(self.manager, value="v2")
        self.worker._login_check_at = 0.0  # the periodic check (a restart, a callback that never came)
        self.run_all()
        self.assertEqual(self.task(task["id"])["state"], "NEEDS_CHOICE")

    def test_stopped_or_cancelled_waiting_tasks_are_never_woken(self):
        stopped, cancelled, kept = self.add(page_url(film="f1"), page_url(film="f2"), page_url(film="f3"))
        self.run_all()
        self.worker.stop(stopped["id"])
        self.worker.cancel(cancelled["id"])
        connect(self.manager)
        self.worker.wake_logins()
        self.run_all()
        self.assertEqual([self.task(t["id"])["state"] for t in (stopped, cancelled, kept)],
                         ["STOPPED", "CANCELLED", "NEEDS_CHOICE"])
        self.worker.resume(stopped["id"])  # the user's own Tiếp tục still works
        self.run_all()
        self.assertEqual(self.task(stopped["id"])["state"], "NEEDS_CHOICE")


    def test_a_waiting_task_whose_source_left_the_config_ends_failed(self):
        task, = self.add()
        self.run_all()
        self.assertEqual(self.task(task["id"])["state"], "WAITING_LOGIN")
        self.restart(providers=[self.beta])  # alpha left the account config while the task waited
        self.run_all()
        done = self.task(task["id"])
        self.assertEqual((done["state"], done["error_code"]), ("FAILED", "ACCOUNT_SOURCE_GONE"))
        self.assertFalse(self.ytdlp_called())


class FileQueueCase(QueueCase):
    def make_listing(self):
        return film()

    def run_film(self):
        connect(self.manager)
        task, = self.add(page_url(film="m1"))
        self.run_all(90)
        return self.task(task["id"])


class TransferTest(FileQueueCase):
    def test_a_session_lost_mid_transfer_keeps_the_part_and_continues_after_sign_in(self):
        def transfer(number, body):
            if number == 1:
                self.clock.at(FALLBACK_TTL_SECONDS + 1)  # the session expires while the file comes in
                return Reply(body, content_type="video/mp4", etag='"v1"', cut_after=len(body) // 2)
            if number == 2:
                return Reply(b"no", 403, "text/plain")  # the fresh link needs the (now expired) session
            return None
        self.on_transfer = transfer
        task = self.run_film()
        self.assertEqual((task["state"], task["login_reason"]), ("WAITING_LOGIN", "SESSION_EXPIRED"),
                         self.store.events(task["id"]))
        part = self.root / "temp" / "downloads" / str(task["id"]) / PART_NAME
        kept = part.stat().st_size
        self.assertGreater(kept, 0)
        connect(self.manager, value="v2")
        self.worker.wake_logins()
        self.run_all(90)
        done = self.task(task["id"])
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task["id"]))
        self.assertEqual(self.transfers[-1], f"bytes={kept}-")  # continued, not started again
        self.assertEqual(len(self.input_names()), 1)

    def test_a_transfer_running_past_the_ttl_is_never_cut(self):
        def transfer(number, body):
            self.clock.at(FALLBACK_TTL_SECONDS + 600)
            return Reply(body, content_type="video/mp4", etag='"v1"', delay=0.02, chunk=4 * 1024)
        self.on_transfer = transfer
        task = self.run_film()
        self.assertEqual(task["state"], "COMPLETED", self.store.events(task["id"]))
        self.assertNotIn("WAITING_LOGIN", self.events(task["id"]))

    def test_a_single_403_is_not_a_sign_in(self):
        self.on_transfer = lambda number, body: Reply(b"no", 403, "text/plain") if number == 1 else None
        task = self.run_film()
        self.assertEqual(task["state"], "COMPLETED", self.store.events(task["id"]))
        self.assertNotIn("WAITING_LOGIN", self.events(task["id"]))
        self.assertEqual(len(self.alpha.calls), 3)  # probe, fresh source, one refresh after the 403

    def test_an_expired_ticket_or_a_network_error_keeps_its_own_code(self):
        self.alpha.errors = [SourceError("TICKET_EXPIRED", "Vé tải hết hạn cả lần lấy lại.")]
        task = self.run_film()
        self.assertEqual((task["state"], task["error_code"]), ("FAILED", "TICKET_EXPIRED"))

        def network(ctx):
            if ctx.previous and ctx.previous.get("kind") == "account-file":
                raise HttpError("NETWORK", "Mất mạng.", retryable=True)
        self.alpha.before = network
        self.worker.retry(task["id"])
        self.run_all()
        self.assertEqual((self.task(task["id"])["state"], self.task(task["id"])["error_code"]),
                         ("INTERRUPTED", "NETWORK"))
        self.assertNotIn("WAITING_LOGIN", self.events(task["id"]))

    def test_a_hidden_browser_that_does_not_run_for_a_fresh_link_keeps_the_part_for_tiep_tuc(self):
        # On the first real source a fresh link's hidden run failed (BROWSER_FAILED, SESSION_LOAD_FAILED, INDEXEDDB)
        # and the task ended FAILED, where only "Thử lại" from byte 0 was left. Such a passing failure, during the
        # transfer (a refresh) or before it (the fresh source of a Tiếp tục), now ends INTERRUPTED with the part.
        def transfer(number, body):
            if number == 1:
                return Reply(body, content_type="video/mp4", etag='"v1"', cut_after=len(body) // 2)
            if number == 2:
                return Reply(b"no", 403, "text/plain")  # the link ran out: a refresh, whose hidden run fails
            return None

        def browser_fails(ctx):
            if len(self.alpha.calls) in (3, 4):  # the refresh, then the fresh source of the first Tiếp tục
                raise SourceError("BROWSER_FAILED", "Trình duyệt ẩn của nguồn không chạy được (BROWSER_FAILED).")
        self.on_transfer = transfer
        self.alpha.before = browser_fails
        task = self.run_film()
        part = self.root / "temp" / "downloads" / str(task["id"]) / PART_NAME
        kept = part.stat().st_size
        self.assertGreater(kept, 0)
        for turn in (1, 2):
            interrupted = self.task(task["id"])
            self.assertEqual((interrupted["state"], interrupted["error_code"]), ("INTERRUPTED", "BROWSER_FAILED"),
                             (turn, self.store.events(task["id"])))
            self.assertIn("bấm Tiếp tục", interrupted["error_message"])
            self.assertEqual(part.stat().st_size, kept)
            self.worker.resume(task["id"])
            self.run_all(90)
        done = self.task(task["id"])
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task["id"]))
        self.assertEqual(self.transfers[-1], f"bytes={kept}-")  # continued, not started again
        self.assertEqual((len(self.alpha.calls), len(self.input_names())), (5, 1))
        self.assertNotIn("WAITING_LOGIN", self.events(task["id"]))

    def test_a_changed_source_or_a_refused_ticket_still_ends_failed(self):
        for error in (SourceChanged("phiên bản file"), SourceError("TICKET_FAILED", "Trang vé báo lỗi.")):
            with self.subTest(code=error.code):
                self.assertFalse(resumable_error(error))
        for code in ("BROWSER_FAILED", "BROWSER_UNAVAILABLE", "ACCOUNT_STATE_ERROR", "TICKET_TIMEOUT"):
            self.assertTrue(resumable_error(SourceError(code, "x")), code)
        self.assertTrue(resumable_error(HttpError("NETWORK", "x", retryable=True)))
        self.assertFalse(resumable_error(SourceLoginRequired("alpha", 1, "SESSION_EXPIRED", "Nguồn alpha")))

        def refused(ctx):
            if len(self.alpha.calls) == 3:  # the refresh after the link ran out
                raise SourceError("TICKET_FAILED", "Trang vé báo lỗi.")
        self.on_transfer = lambda number, body: Reply(b"no", 403, "text/plain") if number == 1 else None
        self.alpha.before = refused
        task = self.run_film()
        self.assertEqual((task["state"], task["error_code"]), ("FAILED", "TICKET_FAILED"), self.store.events(task["id"]))

    def test_a_full_disk_waits_for_space_and_never_for_a_sign_in(self):
        connect(self.manager)
        kept = (self.manager.status("alpha"), self.manager.session_gate("alpha"), self.vault_files(SID))
        self.space = (100 * GB, 100 * GB)  # only the reserve is free: no room for the file
        task, = self.add(page_url(film="m1"))
        self.assertEqual(len(self.worker.dispatch()), 1)
        self.assertTrue(wait_until(lambda: "WAITING_SPACE" in self.events(task["id"])))
        waiting = self.task(task["id"])
        self.assertEqual(waiting["state"], "WAITING_SPACE")
        self.assertTrue(waiting["error_message"].startswith("Chờ chỗ trống: cần "), waiting["error_message"])
        self.assertNotIn("đăng nhập", waiting["error_message"])
        self.assertEqual((waiting["login_source"], waiting["login_reason"], waiting["login_generation"],
                          waiting["error_code"]), (None, None, None, None))
        self.assertNotIn("WAITING_LOGIN", self.events(task["id"]))
        self.assertEqual(len(self.alpha.calls), 1)  # the probe only: no fresh link and no transfer while it waits
        self.assertEqual(self.transfers, [])
        self.space = (10_000 * GB, 100 * GB)  # space came back
        self.run_all(90)
        done = self.task(task["id"])
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task["id"]))
        self.assertEqual(self.events(task["id"]).count("WAITING_SPACE"), 1)
        self.assertNotIn("WAITING_LOGIN", self.events(task["id"]))
        self.assertEqual(len(self.input_names()), 1)
        self.assertEqual((self.manager.status("alpha"), self.manager.session_gate("alpha"), self.vault_files(SID)),
                         kept)

    def test_a_hung_run_leaves_its_phase_and_frames_never_the_error_text(self):
        error = HttpError("NETWORK", "BF-CANARY-hang-text", retryable=True)
        error.hang = DriverHang("page", ("playwright._impl._sync_base:_sync:113", "x" * 500))
        self.alpha.errors = [error]
        task = self.run_film()
        hung = [event for event in self.store.events(task["id"]) if event["kind"] == "SOURCE_RUN_HUNG"]
        self.assertEqual(len(hung), 1)
        payload = hung[0]["payload"]
        self.assertEqual((payload["phase"], payload["at"][0]), ("page", "playwright._impl._sync_base:_sync:113"))
        self.assertLessEqual(len(payload["at"][1]), 160)
        self.assertNotIn("BF-CANARY-hang-text", hung[0]["message"])

    def test_warnings_of_the_hidden_run_are_shown_and_never_touch_the_session(self):
        events = []
        self.alpha._warn(SessionRun(None, False, True), lambda code, message: events.append(code))
        self.alpha._warn(SessionRun(None, None, False), lambda code, message: events.append(code))
        self.alpha._warn(SessionRun(None, True, False), None)
        self.assertEqual(events, ["SESSION_ROTATION_NOT_SAVED", "PROFILE_LEFT"])
        self.assertEqual(set(self.alpha.warnings), {"SESSION_ROTATION_NOT_SAVED", "PROFILE_LEFT"})
        self.alpha.warnings.clear()
        self.alpha.before = lambda ctx: ctx.notice("SESSION_ROTATION_NOT_SAVED",
                                                   WARNINGS["SESSION_ROTATION_NOT_SAVED"])
        task = self.run_film()
        self.assertEqual(task["state"], "COMPLETED")
        warned = [event for event in self.store.events(task["id"]) if event["kind"] == "SESSION_ROTATION_NOT_SAVED"]
        self.assertEqual(warned[0]["level"], "WARNING")
        self.assertTrue(self.manager.session_gate("alpha")[0])  # the session is kept as it was

    def test_a_fresh_link_shows_its_stage_until_the_transfer_writes_progress(self):
        # A source account's fresh link takes a hidden browser run: the row says so instead of standing still.
        connect(self.manager)
        stages: list[str | None] = []
        task, = self.add(page_url(film="m1"))
        self.alpha.before = lambda ctx: stages.append(self.task(task["id"])["transfer_stage"])
        self.run_all(90)
        done = self.task(task["id"])
        self.assertEqual(done["state"], "COMPLETED")
        self.assertEqual(stages, [None, "resolving"])  # the probe, then the download's fresh source
        self.assertNotEqual(done["transfer_stage"], "resolving")

    def test_a_state_unread_after_the_run_is_warned_in_fixed_words(self):
        events: list[tuple[str, str]] = []
        self.alpha._warn(SessionRun(None, None, False, None, "SESSION_READ_FAILED, INDEXEDDB"),
                         lambda code, message: events.append((code, message)))
        self.assertEqual(events, [("SESSION_STATE_UNREAD",
                                   f"{WARNINGS['SESSION_STATE_UNREAD']} (SESSION_READ_FAILED, INDEXEDDB)")])
        self.assertEqual(self.alpha.warnings["SESSION_STATE_UNREAD"]["message"], events[0][1])

    def test_a_task_whose_source_left_the_config_ends_failed_never_in_yt_dlp(self):
        connect(self.manager)
        task, = self.add(page_url(film="m1"))
        self.restart(providers=[self.beta])  # alpha left the account config
        self.run_all()
        done = self.task(task["id"])
        self.assertEqual((done["state"], done["error_code"]), ("FAILED", "ACCOUNT_SOURCE_GONE"))
        self.assertFalse(self.ytdlp_called())

    def test_a_link_of_a_dropped_source_is_refused_at_paste_unless_a_public_provider_keeps_it(self):
        self.dropped = (DroppedSource("gamma", "Nguồn gamma bị bỏ qua: host trùng.", ("gamma.example",)),)
        self.restart()
        with self.assertRaises(DownloadBatchError) as caught:
            self.add("http://gamma.example/film/x")
        error = caught.exception.errors[0]
        self.assertEqual(error["code"], "ACCOUNT_SOURCE_INVALID")
        self.assertIn("host trùng", error["message"])
        self.extra_providers = [PublicPages()]
        self.restart()
        task, = self.add("http://gamma.example/film/x")
        self.assertIsNone(task["account_owner"])
        self.assertIn("ACCOUNT_SOURCE_IGNORED", self.events(task["id"]))


class EpisodeQueueTest(QueueCase):
    def test_a_series_page_waits_for_episodes_and_its_list_survives_a_restart(self):
        page = self.waiting_page()
        self.assertEqual(page["probe"]["choice_kind"], "episodes")
        self.assertNotIn(page["id"], self.worker.running_ids())
        listing = self.worker.episodes(page["id"])
        self.assertEqual((listing["listing"]["episode_count"], listing["revision"], listing["draft"]), (12, 0, None))
        body = {"selection": {"mode": "pick", "episodes": ["s1e2", "s1e3"], "variant_kind": KIND_1080},
                "fingerprint": listing["fingerprint"], "revision": 0}
        saved = self.worker.save_episode_draft(page["id"], body)
        self.assertEqual((saved["revision"], saved["plan"]["count"], saved["plan"]["confirm_label"]),
                         (1, 2, "Tải 2 tập"))
        self.restart()
        again = self.worker.episodes(page["id"])
        self.assertEqual((again["revision"], again["draft"]["episodes"]), (1, ["s1e2", "s1e3"]))
        with self.assertRaises(DownloadActionError):
            self.worker.choose(page["id"], 1)
        self.assertEqual(len(self.alpha.calls), 1)  # the probe only
        self.assertEqual(self.file_requests(), 0)  # no ticket, no file before "Tải N tập"

    def test_every_episode_lands_under_its_group_name_whatever_order_it_finished_in(self):
        first = self.server.routes[file_path("s1e1", "v1080")]
        waited = []  # one entry per transfer request of the first episode: did the other two finish first?

        def last(seen, number):  # the first episode's file comes only once the other two have finished
            if not SNIFF.fullmatch(seen.headers.get("range", "")):
                waited.append(wait_until(
                    lambda: sum("COMPLETED" in self.events(task["id"]) for task in self.store.list_tasks()) >= 2))
            return first(seen, number)
        self.server.route(file_path("s1e1", "v1080"), last)
        page = self.waiting_page()
        self.worker.set_slots(3)
        picked = {"mode": "pick", "episodes": ["s1e1", "s1e2", "s1e10"], "variant_kind": KIND_1080}
        fingerprint = self.worker.episodes(page["id"])["fingerprint"]
        result = self.confirm(page["id"], picked, fingerprint=fingerprint)
        self.assertEqual((result["group"]["total"], result["replay"]), (3, False))
        again = self.confirm(page["id"], picked, fingerprint=fingerprint)  # the same request again
        self.assertEqual((again["replay"], again["group"]["id"]), (True, result["group"]["id"]))
        self.run_all(120)
        # The held transfer really waited for the other two (a timed-out wait would make the order below luck).
        self.assertTrue(waited)
        self.assertTrue(all(waited), waited)
        self.assertEqual(self.input_names(), [f"001 - {TITLE} - S01E01.mp4", f"002 - {TITLE} - S01E02.mp4",
                                              f"003 - {TITLE} - S01E10.mp4"])
        tasks = [task["id"] for task in self.worker.groups.tasks_of(result["group"]["id"])]  # group order
        finished = sorted(tasks, key=lambda task_id: next(event["id"] for event in self.store.events(task_id)
                                                          if event["kind"] == "COMPLETED"))
        self.assertEqual(finished[-1], tasks[0])  # the first episode really finished last
        self.assertNotEqual(finished, tasks)
        summary = self.worker.groups.summary(result["group"]["id"])
        self.assertEqual((summary["done"], summary["total"], summary["percent"], summary["finished"]),
                         (3, 3, 100, True))
        self.assertEqual(self.task(page["id"])["state"], "EXPANDED")
        before = self.file_requests()
        self.worker.group_action(summary["id"], "retry")  # finished episodes are never downloaded again
        self.run_all()
        self.assertEqual(self.file_requests(), before)

    def test_retry_keeps_the_episode_place_and_rename_changes_only_the_film_part(self):
        page = self.waiting_page()
        self.worker.set_slots(1)
        failed = []

        def first_try_fails(ctx):
            account_file = (ctx.previous or {}).get("account_file") or {}
            if account_file.get("episode") == "s1e2" and not failed:
                failed.append(True)
                raise SourceError("FILE_GONE", "File không còn trên máy chủ.")
        self.alpha.before = first_try_fails
        result = self.confirm(page["id"], {"mode": "pick", "episodes": ["s1e1", "s1e2", "s1e3"],
                                           "variant_kind": KIND_1080})
        tasks = self.worker.groups.tasks_of(result["group"]["id"])
        self.worker.rename(tasks[2]["id"], "Tên khác")
        self.run_all(120)
        # One failed episode leaves the others done, before any retry.
        self.assertEqual([self.task(t["id"])["state"] for t in tasks], ["COMPLETED", "FAILED", "COMPLETED"])
        self.assertEqual(self.task(tasks[1]["id"])["error_code"], "FILE_GONE")
        self.assertEqual(self.input_names(), [f"001 - {TITLE} - S01E01.mp4", "003 - Tên khác - S01E03.mp4"])
        self.worker.group_action(result["group"]["id"], "retry")
        retried = self.task(tasks[1]["id"])
        self.assertEqual((retried["state"], retried["probe"]["account_file"]["episode"]), ("QUEUED", "s1e2"))
        self.run_all(120)
        self.assertEqual(self.input_names(), [f"001 - {TITLE} - S01E01.mp4", f"002 - {TITLE} - S01E02.mp4",
                                              "003 - Tên khác - S01E03.mp4"])

    def test_group_actions_handle_pending_members_first_and_keep_finished_episodes(self):
        self.store.add_tasks([f"https://clips.example/v/{n}" for n in range(97)])
        for task in self.store.list_tasks():
            self.store.transition(task["id"], {"QUEUED"}, "STOPPED")
        page = self.waiting_page()  # the 98th unfinished task, until it is expanded
        result = self.confirm(page["id"], {"mode": "pick", "episodes": [f"s1e{n}" for n in range(1, 7)],
                                           "variant_kind": KIND_1080})
        group_id = result["group"]["id"]
        self.assertEqual((result["group"]["counts"]["queued"], result["group"]["counts"]["pending"]), (3, 3))
        self.worker.set_slots(1)
        done = self.step()  # one episode finishes before any group action
        self.assertEqual(self.task(done)["state"], "COMPLETED")
        finished = self.input_files()
        self.assertEqual(list(finished), [f"001 - {TITLE} - S01E01.mp4"])
        stopped = self.worker.group_action(group_id, "stop")["group"]
        self.assertEqual((stopped["counts"]["stopped"], stopped["counts"]["held"], stopped["counts"]["completed"]),
                         (2, 3, 1))
        with self.assertRaises(DownloadActionError):
            self.worker.group_action(group_id, "remove")  # still waiting: never removed
        resumed = self.worker.group_action(group_id, "resume")["group"]
        self.assertEqual((resumed["counts"]["queued"], resumed["counts"]["pending"], resumed["counts"]["completed"]),
                         (2, 3, 1))
        cancelled = self.worker.group_action(group_id, "cancel")["group"]
        self.assertEqual((cancelled["state"], cancelled["counts"]["cancelled"], cancelled["counts"]["completed"]),
                         ("CANCELLED", 5, 1))
        self.assertEqual(self.task(done)["state"], "COMPLETED")  # the finished episode is kept
        self.assertEqual(self.worker.groups.fill(), [])
        with self.assertRaises(Exception) as caught:
            self.worker.group_action(group_id, "retry")
        self.assertEqual(getattr(caught.exception, "code", None), "GROUP_CANCELLED")
        removed = self.worker.group_action(group_id, "remove")
        self.assertTrue(removed["removed"])
        self.assertIsNone(self.worker.groups.group(group_id))
        self.assertIsNone(self.task(page["id"]))
        self.assertIsNone(self.task(done))
        self.assertEqual(self.store.unfinished_count(), 97)
        self.assertEqual(self.input_files(), finished)  # its file stays in input, byte for byte

    def test_a_retried_episode_never_duplicates_a_file_already_open_elsewhere(self):
        page = self.waiting_page()
        first = self.confirm(page["id"], {"mode": "pick", "episodes": ["s1e1"], "variant_kind": KIND_1080})
        episode, = self.worker.groups.tasks_of(first["group"]["id"])
        self.worker.cancel(episode["id"])  # its own Hủy: the group stays active, so Thử lại is allowed
        second_page, = self.add(page_url() + "?again=1")
        self.run_all()
        self.confirm(second_page["id"], {"mode": "pick", "episodes": ["s1e1"], "variant_kind": KIND_1080},
                     key="key-00000002")
        with self.assertRaises(DownloadActionError) as caught:
            self.worker.retry(episode["id"])
        self.assertIn("đã có một lượt tải khác", str(caught.exception))


    def test_a_cancelled_page_drops_its_stored_list(self):
        page = self.waiting_page()
        self.worker.cancel(page["id"])
        self.worker.dispatch()
        self.assertIsNone(self.worker.groups.preview(page["id"]))
        self.assertEqual(self.worker.groups.preview_summaries(), {})

    def test_a_page_whose_group_is_already_gone_can_still_be_removed(self):
        page = self.waiting_page()
        folder = self.root / "temp" / "downloads" / str(page["id"])
        self.assertTrue(folder.is_dir())  # made by the page's probe
        result = self.confirm(page["id"], {"mode": "pick", "episodes": ["s1e1"], "variant_kind": KIND_1080})
        self.worker.group_action(result["group"]["id"], "cancel")
        self.worker.groups.delete_group(result["group"]["id"], None)  # as if the rest was interrupted
        self.assertEqual(self.worker.remove(page["id"])["removed"], True)
        self.assertIsNone(self.task(page["id"]))
        self.assertFalse(folder.exists())  # its temp folder goes with its row

    def test_a_single_retry_keeps_the_100_cap(self):
        page = self.waiting_page()
        result = self.confirm(page["id"], {"mode": "pick", "episodes": ["s1e1"], "variant_kind": KIND_1080})
        episode, = self.worker.groups.tasks_of(result["group"]["id"])
        self.worker.cancel(episode["id"])  # its own Hủy in an active group (a cancelled group refuses Thử lại)
        self.store.add_tasks([f"https://clips.example/v/{n}" for n in range(100)])
        for task in self.store.tasks_in({"QUEUED"}):
            self.store.transition(task["id"], {"QUEUED"}, "STOPPED")
        with self.assertRaises(DownloadActionError) as caught:
            self.worker.retry(episode["id"])
        self.assertIn("100 lượt", str(caught.exception))
        self.assertEqual(self.task(episode["id"])["state"], "CANCELLED")

    def test_a_cancelled_group_never_wakes_its_waiting_episodes_after_an_interruption(self):
        page = self.waiting_page()
        result = self.confirm(page["id"], {"mode": "pick", "episodes": ["s1e1", "s1e2", "s1e3"],
                                           "variant_kind": KIND_1080})
        tasks = self.worker.groups.tasks_of(result["group"]["id"])
        self.clock.at(FALLBACK_TTL_SECONDS + 1)  # the session ran out: the episodes wait for a sign-in
        self.run_all()
        self.assertEqual([self.task(t["id"])["state"] for t in tasks], ["WAITING_LOGIN"] * 3)
        resolves = len(self.alpha.calls)
        with mock.patch.object(self.worker, "_group_task_action", side_effect=Interrupted):
            with self.assertRaises(Interrupted):  # Hủy nhóm stored, then the process died
                self.worker.group_action(result["group"]["id"], "cancel")
        self.restart()
        self.worker.recover()  # before any dispatch or wake
        self.assertEqual([self.task(t["id"])["state"] for t in tasks], ["CANCELLED"] * 3)
        connect(self.manager, value="v2")  # a newer sign-in lands late, with its callback
        self.worker.wake_logins()
        self.run_all()
        self.assertEqual([self.task(t["id"])["state"] for t in tasks], ["CANCELLED"] * 3)
        self.assertTrue(all("LOGIN_RESUMED" not in self.events(t["id"]) for t in tasks))
        self.assertEqual((len(self.alpha.calls), self.file_requests()), (resolves, 0))  # no page read, no ticket


class Interrupted(BaseException):
    """A crash between two steps of an action (it passes every ``except Exception``)."""


class MkvQueueTest(QueueCase):
    def make_listing(self):
        return series(2, variants=(("v1080", "1080p"),))

    def setUp(self):
        super().setUp()
        self.serve(self.alpha.listing, media="mkv")

    def test_an_mkv_episode_stays_mkv_and_a_taken_name_is_never_overwritten(self):
        taken = self.root / "input" / f"001 - {TITLE} - S01E01.mkv"
        taken.write_bytes(b"old file")
        page = self.waiting_page()
        self.confirm(page["id"], {"mode": "all"})
        self.run_all(120)
        self.assertEqual(taken.read_bytes(), b"old file")
        self.assertEqual(self.input_names(), [f"001 - {TITLE} - S01E01 (2).mkv", f"001 - {TITLE} - S01E01.mkv",
                                              f"002 - {TITLE} - S01E02.mkv"])


class RouteCase(QueueCase):
    """The Control Center handler (PC listener, and the phone listener on demand) over the worker, with the
    account runtime of the root's manager (a fake launcher; no window ever opens). No tests of its own."""

    def setUp(self):
        super().setUp()
        self.launcher = NoWindow()
        self.runtime = AccountRuntime(self.root, manager=self.manager,
                                      coordinator_options={"launcher": self.launcher, "verifiers": {}})
        self.jobs = JobStore(self.root / "state" / "control-center.sqlite3")
        center = ControlCenter.__new__(ControlCenter)
        center.root, center.host, center.token, center.store = self.root, "127.0.0.1", TOKEN, self.jobs
        center.frame_cache = ReviewFrameCache(self.root, self.root / "missing-ffmpeg.exe")
        center._stopping = threading.Event()
        center.scheduler = JobScheduler(self.root, self.jobs)  # never started
        center.recovered = 0
        center.downloads = self.service()
        self.center = center
        self.handler = _handler_class(center)
        self.pc = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.pc.daemon_threads = True
        self.pc_port = self.pc.server_address[1]
        self.pc_host = f"127.0.0.1:{self.pc_port}"
        threading.Thread(target=self.pc.serve_forever, daemon=True).start()
        self.phone = _phone_access(center)
        self.phone._lan = lambda: FAKE_LAN  # never the real Wi-Fi

    def tearDown(self):
        self.release_held()
        self.phone.disable()
        self.pc.shutdown()
        self.pc.server_close()
        self.runtime.stop(5)
        self.jobs.close()
        super().tearDown()

    def service(self):
        """The downloads service of the current store and worker (rebuilt after a restart of the worker)."""
        return DownloadService(self.root, cleanable=lambda: (0, 0), bin_reader=None, store=self.store,
                               worker=self.worker, accounts=self.runtime)

    def call(self, method, path, payload=None, *, token=TOKEN, port=None, host=None, headers=None):
        extra = {"Content-Type": "application/json", **(headers or {})}
        if token:
            extra["X-BiliFlow-Token"] = token
        body = json.dumps(payload).encode() if payload is not None else b""
        status, _, reply = http(port or self.pc_port, method, path, host=host or self.pc_host, headers=extra,
                                body=body)
        return status, json.loads(reply or b"null")

    def open_phone(self):
        status = self.phone.enable(lambda access: _phone_handler_class(self.center, access), address=FAKE_LAN,
                                   check_address=lambda _a: None, check_port=lambda _p: None, port=0)
        port, code = status["port"], status["code"]
        host = f"127.0.0.1:{port}"
        _, headers, _ = http(port, "POST", "/phone-login", host=host,
                             headers={"Content-Type": "application/x-www-form-urlencoded"},
                             body=f"code={code}".encode())
        cookie = headers["set-cookie"][0].split(";", 1)[0]
        return port, host, {"Cookie": cookie, "Origin": f"http://{host}"}

    def refused_on_phone(self, phone, source_id, action, length):
        """POST an account route to the phone listener (cookie, Origin and token all valid) declaring ``length``
        body bytes and sending none; it must answer 403 within NO_BODY_WAIT. Its JSON answer."""
        port, host, headers = phone
        head = (f"POST /api/download-accounts/{source_id}/{action} HTTP/1.1\r\nHost: {host}\r\n"
                f"Cookie: {headers['Cookie']}\r\nOrigin: {headers['Origin']}\r\nX-BiliFlow-Token: {TOKEN}\r\n"
                f"Content-Type: application/json\r\nContent-Length: {length}\r\n\r\n").encode()
        reply = raw(port, head, wait=NO_BODY_WAIT)
        status_line, _, rest = reply.partition(b"\r\n")
        self.assertRegex(status_line, rb"^HTTP/1\.[01] 403 ", (source_id, action, length, reply[:120]))
        return json.loads(rest.partition(b"\r\n\r\n")[2])


class RouteTest(RouteCase):
    """The episode and group routes through the Control Center handler, on the PC and on the phone, and the
    PC-only account routes."""

    def test_a_dispatch_pass_between_storing_the_list_and_the_choice_keeps_the_list(self):
        """R43/R34: a series page's probe stores its episode list, then moves the task to NEEDS_CHOICE. A
        dispatch pass of another thread landing between the two (its upkeep drops the lists of pages that no
        longer wait for a choice) must not drop the list of a page still being probed: the page then waits with
        its list and Chọn tập opens. Seen once under load as a GET episodes without a fingerprint right after
        NEEDS_CHOICE; a page left without its list could never be chosen."""
        connect(self.manager)
        groups = self.worker.groups
        original = groups.save_preview
        passes = []

        def upkeep():
            with self.worker._lock:  # noqa: SLF001 - a dispatch pass holds the worker lock
                self.worker._account_upkeep()  # noqa: SLF001
            passes.append(True)

        def save_then_dispatch(*args, **kwargs):
            saved = original(*args, **kwargs)
            other = threading.Thread(target=upkeep, name="dispatch-pass", daemon=True)
            other.start()
            other.join(5)  # a pass blocked by a lock the probe holds runs right after the choice is stored
            return saved

        with mock.patch.object(groups, "save_preview", save_then_dispatch):
            task, = self.add()
            self.run_all()
        self.assertTrue(wait_until(lambda: passes == [True]))
        self.assertEqual(self.task(task["id"])["state"], "NEEDS_CHOICE")
        self.assertIsNotNone(groups.preview(task["id"]))
        status, listing = self.call("GET", f"/api/downloads/{task['id']}/episodes")
        self.assertEqual((status, listing.get("code")), (200, None), listing)
        self.assertEqual(listing["listing"]["episode_count"], 12)

    def test_the_episode_routes_preview_draft_and_confirm_once(self):
        page = self.waiting_page()
        status, snapshot = self.call("GET", "/api/downloads")
        self.assertEqual(status, 200)
        shown = next(task for task in snapshot["tasks"] if task["id"] == page["id"])
        self.assertEqual((shown["choice_kind"], shown["episodes"]["episode_count"]), ("episodes", 12))
        self.assertEqual(snapshot["accounts"]["sources"][0]["waiting_tasks"], 0)
        status, listing = self.call("GET", f"/api/downloads/{page['id']}/episodes")
        self.assertEqual((status, listing["listing"]["episode_count"]), (200, 12))
        draft = {"selection": {"mode": "all", "variant_kind": KIND_1080}, "fingerprint": listing["fingerprint"],
                 "revision": listing["revision"]}
        status, saved = self.call("POST", f"/api/downloads/{page['id']}/episodes/draft", draft)
        self.assertEqual((status, saved["plan"]["confirm_label"]), (200, "Tải 12 tập"))
        status, stale = self.call("POST", f"/api/downloads/{page['id']}/episodes/draft", draft)
        self.assertEqual((status, stale["code"]), (409, "STALE_DRAFT"))
        status, fake = self.call("POST", f"/api/downloads/{page['id']}/episodes/confirm", {
            "selection": {"mode": "pick", "episodes": ["s1e99"], "variant_kind": KIND_1080},
            "fingerprint": listing["fingerprint"], "idempotency_key": "key-00000009"})
        self.assertEqual((status, fake["code"], fake["unknown"]), (400, "BAD_SELECTION", ["s1e99"]))
        confirm = {"selection": draft["selection"], "fingerprint": listing["fingerprint"],
                   "idempotency_key": "key-00000001"}
        replies = []

        def post():
            replies.append(self.call("POST", f"/api/downloads/{page['id']}/episodes/confirm", confirm))

        threads = [threading.Thread(target=post) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual({status for status, _ in replies}, {200})
        self.assertEqual(len({reply["group"]["id"] for _, reply in replies}), 1)
        self.assertEqual(sorted(reply["replay"] for _, reply in replies), [False, True, True, True])
        group_id = replies[0][1]["group"]["id"]
        status, detail = self.call("GET", f"/api/downloads/groups/{group_id}")
        self.assertEqual((status, len(detail["members"]), detail["members"][0]["code"]), (200, 12, "S01E01"))
        self.assertNotIn("selection_json", json.dumps(detail))
        status, stopped = self.call("POST", f"/api/downloads/groups/{group_id}/stop", {})
        self.assertEqual((status, stopped["group"]["counts"]["stopped"]), (200, 12))
        status, missing = self.call("GET", "/api/downloads/groups/999")
        self.assertEqual(status, 404)

    def test_group_routes_reach_running_waiting_interrupted_and_expired_episodes(self):
        page = self.waiting_page()
        self.assertEqual(self.call("POST", "/api/downloads/settings", {"slots": 1}), (200, {"slots": 1}))
        body = MEDIA["mp4"]
        for gone in ("s1e2", "s1e3"):  # the file is gone (HTTP 404) until the retry
            self.server.route(file_path(gone, "v1080"), Reply(b"gone", 404, "text/plain"))
        transfers = []

        def cut_then_refused(seen, number):  # s1e5: half the file, then the link runs out (403)
            wanted = seen.headers.get("range", "")
            if SNIFF.fullmatch(wanted):
                return Reply(body, content_type="video/mp4", etag='"v1"')
            transfers.append((wanted, seen.headers.get("if-range")))
            if len(transfers) == 1:
                return Reply(body, content_type="video/mp4", etag='"v1"', cut_after=len(body) // 2)
            if len(transfers) == 2:
                return Reply(b"no", 403, "text/plain")
            return Reply(body, content_type="video/mp4", etag='"v1"')
        self.server.route(file_path("s1e5", "v1080"), cut_then_refused)
        fresh = []

        def network_down(ctx):  # s1e5's link refreshed after the 403 meets a network error
            if episode_of(ctx) == "s1e5" and (ctx.previous or {}).get("kind") == "account-file":
                fresh.append(True)
                if len(fresh) == 2:
                    raise HttpError("NETWORK", "Mất mạng.", retryable=True)
        self.alpha.before = network_down
        entered, gate = threading.Event(), self.hold(threading.Event())

        def gated(seen, number):  # s1e6: its transfer runs until the gate opens
            if not SNIFF.fullmatch(seen.headers.get("range", "")):
                entered.set()
                gate.wait(WAIT)
            return Reply(body, content_type="video/mp4", etag='"v1"')
        self.server.route(file_path("s1e6", "v1080"), gated)
        status, listing = self.call("GET", f"/api/downloads/{page['id']}/episodes")
        status, created = self.call("POST", f"/api/downloads/{page['id']}/episodes/confirm", {
            "selection": {"mode": "pick", "episodes": [f"s1e{n}" for n in range(1, 8)], "variant_kind": KIND_1080},
            "fingerprint": listing["fingerprint"], "idempotency_key": "key-00000002"})
        self.assertEqual(status, 200, created)
        group_id = created["group"]["id"]
        tasks = [task["id"] for task in self.worker.groups.tasks_of(group_id)]
        self.assertEqual(len(tasks), 7)

        def states():
            return [self.task(task_id)["state"] for task_id in tasks]

        def post(action, task_id=None):
            where = f"/api/downloads/{task_id}" if task_id is not None else f"/api/downloads/groups/{group_id}"
            return self.call("POST", f"{where}/{action}", {})

        self.step()  # s1e1 COMPLETED
        self.step()  # s1e2 FAILED (404), then EXPIRED after 7 days
        self.assertEqual(states()[:2], ["COMPLETED", "FAILED"])
        self.assertEqual(self.task(tasks[1])["error_code"], "UNAVAILABLE")
        self.assertEqual(self.worker.sweep(self.store.clock() + timedelta(days=8))["expired"], 1)
        self.step()  # s1e3 FAILED (404)
        generation = self.manager.session_gate("alpha")[2]
        self.alpha.errors = [SourceLoginRequired("alpha", generation, "SESSION_REJECTED", "Nguồn alpha")]
        self.step()  # s1e4 WAITING_LOGIN
        self.step()  # s1e5 INTERRUPTED with its part
        part = self.root / "temp" / "downloads" / str(tasks[4]) / PART_NAME
        kept = part.stat().st_size
        self.assertGreater(kept, 0)
        self.assertEqual(self.task(tasks[4])["error_code"], "NETWORK")
        self.assertEqual(len(self.worker.dispatch()), 1)  # s1e6 takes the only slot
        self.assertTrue(entered.wait(WAIT))
        self.assertEqual(self.worker.dispatch(), [])  # s1e7 waits for it
        self.assertEqual(states(), ["COMPLETED", "EXPIRED", "FAILED", "WAITING_LOGIN", "INTERRUPTED", "DOWNLOADING",
                                    "QUEUED"])
        members = {member["ordinal"]: member["task_id"] for member in self.worker.groups.members(group_id)}
        self.assertEqual(members, dict(enumerate(tasks, start=1)))

        status, stopped = post("stop")  # the running, waiting and queued episodes; the interrupted one stays
        self.assertEqual(status, 200, stopped)
        self.assertTrue(self.worker.wait_idle(WAIT))
        self.assertEqual(states(), ["COMPLETED", "EXPIRED", "FAILED", "STOPPED", "INTERRUPTED", "STOPPED", "STOPPED"])
        gate.set()
        for gone in ("s1e2", "s1e3"):  # the files are back
            self.server.route(file_path(gone, "v1080"), self.file_route(body, "mp4", 0.0))
        first_file = self.server.count(file_path("s1e1", "v1080"))
        status, retried = post("retry")  # only the FAILED and EXPIRED episodes, on their own task ids
        self.assertEqual(status, 200, retried)
        self.assertEqual(states(), ["COMPLETED", "QUEUED", "QUEUED", "STOPPED", "INTERRUPTED", "STOPPED", "STOPPED"])
        self.assertEqual(part.stat().st_size, kept)  # Thử lại nhóm left the interrupted part alone
        status, resumed = post("resume")  # the stopped and the interrupted episodes
        self.assertEqual(status, 200, resumed)
        self.assertEqual(states(), ["COMPLETED"] + ["QUEUED"] * 6)
        for _ in range(4):  # s1e2 and s1e3 (retried first), then s1e4 and s1e5
            self.step()
        self.assertEqual(states(), ["COMPLETED"] * 5 + ["QUEUED", "QUEUED"])
        self.assertEqual(transfers[-1], (f"bytes={kept}-", '"v1"'))  # the interrupted part was continued
        self.assertEqual(len(transfers), 3)
        self.assertEqual(self.server.count(file_path("s1e1", "v1080")), first_file)  # never asked for again
        self.assertEqual(self.input_names(), [f"00{n} - {TITLE} - S01E0{n}.mp4" for n in range(1, 6)])
        files = self.input_files()
        self.assertTrue(all(value == body for value in files.values()))
        self.assertEqual({member["ordinal"]: member["task_id"] for member in self.worker.groups.members(group_id)},
                         members)

        self.assertEqual(post("stop", tasks[6])[0], 200)  # s1e7: its own Dừng
        entered.clear()
        gate.clear()
        self.assertEqual(self.worker.dispatch(), [tasks[5]])  # s1e6 runs again and waits at the gate
        self.assertTrue(entered.wait(WAIT))
        status, cancelled = post("cancel")  # the running and the stopped episode; the finished files stay
        self.assertEqual((status, cancelled["group"]["state"]), (200, "CANCELLED"))
        self.assertTrue(self.worker.wait_idle(WAIT))
        gate.set()
        self.assertEqual(states(), ["COMPLETED"] * 5 + ["CANCELLED", "CANCELLED"])
        self.assertEqual(self.input_files(), files)
        for action in ("resume", "retry"):
            status, refused = post(action)
            self.assertEqual((status, refused["code"]), (409, "GROUP_CANCELLED"))
            for task_id in tasks[5:]:
                self.assertEqual(post(action, task_id)[0], 409)
        self.assertIn("đã hủy", post("retry", tasks[6])[1]["error"])
        self.assertEqual(states(), ["COMPLETED"] * 5 + ["CANCELLED", "CANCELLED"])
        self.assertFalse(self.ytdlp_called())

    def test_one_failed_episode_leaves_the_others_done_and_removal_keeps_their_files(self):
        self.server.route(file_path("s1e2", "v1080"), Reply(b"gone", 404, "text/plain"))
        page = self.waiting_page()
        status, listing = self.call("GET", f"/api/downloads/{page['id']}/episodes")
        status, created = self.call("POST", f"/api/downloads/{page['id']}/episodes/confirm", {
            "selection": {"mode": "pick", "episodes": ["s1e1", "s1e2", "s1e3"], "variant_kind": KIND_1080},
            "fingerprint": listing["fingerprint"], "idempotency_key": "key-00000003"})
        self.assertEqual(status, 200, created)
        group_id = created["group"]["id"]
        tasks = [task["id"] for task in self.worker.groups.tasks_of(group_id)]
        self.run_all(120)
        self.assertEqual([self.task(task_id)["state"] for task_id in tasks], ["COMPLETED", "FAILED", "COMPLETED"])
        self.assertEqual(self.task(tasks[1])["error_code"], "UNAVAILABLE")
        self.assertNotIn("WAITING_LOGIN", self.events(tasks[1]))
        files = self.input_files()
        self.assertEqual(list(files), [f"001 - {TITLE} - S01E01.mp4", f"003 - {TITLE} - S01E03.mp4"])
        self.assertTrue(all(value == MEDIA["mp4"] for value in files.values()))
        status, detail = self.call("GET", f"/api/downloads/groups/{group_id}")
        group = detail["group"]
        self.assertEqual((status, group["done"], group["total"], group["counts"]["failed"], group["finished"]),
                         (200, 2, 3, 1, True))
        self.assertEqual([member["task_state"] for member in detail["members"]], ["COMPLETED", "FAILED", "COMPLETED"])
        downloads = self.root / "temp" / "downloads"
        self.assertTrue((downloads / str(page["id"])).is_dir())  # made by the page's probe
        self.assertTrue((downloads / str(tasks[1])).is_dir())  # the failed episode's own folder
        other = downloads / "999999"  # a folder of something else: never touched
        other.mkdir()
        (other / "keep.bin").write_bytes(b"other")
        status, removed = self.call("POST", f"/api/downloads/groups/{group_id}/remove", {})
        self.assertEqual((status, removed["removed"]), (200, True))
        self.assertEqual(self.input_files(), files)  # the published files stay, byte for byte
        self.assertIsNone(self.worker.groups.group(group_id))
        self.assertEqual(self.worker.groups.members(group_id), [])
        self.assertIsNone(self.task(page["id"]))
        self.assertEqual([self.task(task_id) for task_id in tasks], [None] * 3)
        self.assertEqual(self.call("GET", f"/api/downloads/groups/{group_id}")[0], 404)
        self.assertEqual([(downloads / str(task_id)).exists() for task_id in tasks], [False] * 3)
        self.assertFalse((downloads / str(page["id"])).exists())  # the page's temp folder went with its row
        self.assertEqual((other / "keep.bin").read_bytes(), b"other")

    def test_a_list_read_again_refuses_the_old_fingerprint_on_draft_and_confirm(self):
        """R44, server side: the page is read again and its list changed (Hủy then Thử lại of the page, while the
        site added an episode). A draft or a "Tải N tập" sent with the old list's fingerprint, as a tab opened
        before would send it, is refused 409 STALE_PREVIEW and writes nothing; the new fingerprint works."""
        page = self.waiting_page()
        episodes = f"/api/downloads/{page['id']}/episodes"
        status, old = self.call("GET", episodes)
        self.assertEqual((status, old["listing"]["episode_count"]), (200, 12))
        picked = {"mode": "pick", "episodes": ["s1e1", "s1e2"], "variant_kind": KIND_1080}
        status, saved = self.call("POST", f"{episodes}/draft", {
            "selection": picked, "fingerprint": old["fingerprint"], "revision": old["revision"]})
        self.assertEqual((status, saved["revision"]), (200, 1))
        longer = series(13)  # the site now lists one more episode
        self.alpha.show(longer)
        self.serve(longer)
        self.assertEqual(self.call("POST", f"/api/downloads/{page['id']}/cancel", {})[0], 200)
        self.assertEqual(self.call("POST", f"/api/downloads/{page['id']}/retry", {})[0], 200)
        self.run_all()
        self.assertEqual(self.task(page["id"])["state"], "NEEDS_CHOICE", self.store.events(page["id"]))
        self.assertEqual(len(self.alpha.calls), 2)  # the page was read twice: its first probe and the retry's
        status, new = self.call("GET", episodes)
        self.assertEqual((status, new["listing"]["episode_count"], new["draft"]), (200, 13, None))
        self.assertNotEqual(new["fingerprint"], old["fingerprint"])
        kept = (self.store.list_tasks(), self.worker.groups.summaries(), self.worker.episodes(page["id"]))
        for revision in (saved["revision"], new["revision"]):  # the old tab's revision, then the stored one
            status, stale = self.call("POST", f"{episodes}/draft", {
                "selection": picked, "fingerprint": old["fingerprint"], "revision": revision})
            self.assertEqual((status, stale.get("code")), (409, "STALE_PREVIEW"), (revision, stale))
        for number, selection in enumerate((picked, {"mode": "all", "variant_kind": KIND_1080}), start=1):
            status, stale = self.call("POST", f"{episodes}/confirm", {
                "selection": selection, "fingerprint": old["fingerprint"], "idempotency_key": f"stale-{number:04d}"})
            self.assertEqual((status, stale.get("code")), (409, "STALE_PREVIEW"), (selection, stale))
        # No group, no task, no draft: the stored list, its revision and the queue are as they were.
        self.assertEqual((self.store.list_tasks(), self.worker.groups.summaries(),
                          self.worker.episodes(page["id"])), kept)
        status, fresh = self.call("POST", f"{episodes}/draft", {
            "selection": picked, "fingerprint": new["fingerprint"], "revision": new["revision"]})
        self.assertEqual((status, fresh["plan"]["confirm_label"]), (200, "Tải 2 tập"))
        status, created = self.call("POST", f"{episodes}/confirm", {  # a key the refused request never used up
            "selection": {"mode": "all", "variant_kind": KIND_1080}, "fingerprint": new["fingerprint"],
            "idempotency_key": "stale-0001"})
        self.assertEqual((status, created["group"]["total"], created["replay"]), (200, 13, False), created)
        members = self.worker.groups.members(created["group"]["id"])
        self.assertEqual([member["code"] for member in members], [f"S01E{n:02d}" for n in range(1, 14)])

    def test_a_group_whose_members_still_wait_is_never_removed_even_when_every_task_is_final(self):
        """R76 through the routes: every episode task of the group is final (COMPLETED or STOPPED) while members
        still wait without a task, PENDING (the list is full) and then HELD (Dừng nhóm). Xóa nhóm and the
        page's own Xóa answer 409 and change nothing (rows, temp folders, files in input). After Hủy nhóm the
        same route removes the group and its page and keeps the published file."""
        fillers = self.store.add_tasks([f"https://clips.example/v/{n}" for n in range(97)])
        for task in fillers:
            self.store.transition(task["id"], {"QUEUED"}, "STOPPED")
        page = self.waiting_page()
        status, listing = self.call("GET", f"/api/downloads/{page['id']}/episodes")
        status, created = self.call("POST", f"/api/downloads/{page['id']}/episodes/confirm", {
            "selection": {"mode": "pick", "episodes": [f"s1e{n}" for n in range(1, 7)], "variant_kind": KIND_1080},
            "fingerprint": listing["fingerprint"], "idempotency_key": "remove-0001"})
        self.assertEqual(status, 200, created)
        group_id = created["group"]["id"]
        self.assertEqual((created["group"]["counts"]["queued"], created["group"]["counts"]["pending"]), (3, 3))
        self.worker.set_slots(1)
        done = self.step()  # s1e1 COMPLETED: its file is in input
        self.assertEqual(self.task(done)["state"], "COMPLETED")
        extra, = self.store.add_tasks(["https://clips.example/v/97"])  # the list is full again: 100 unfinished
        self.store.transition(extra["id"], {"QUEUED"}, "STOPPED")
        tasks = [task["id"] for task in self.worker.groups.tasks_of(group_id)]
        self.assertEqual(tasks[0], done)
        for task_id in tasks[1:]:  # s1e2 and s1e3: their own Dừng
            self.assertEqual(self.call("POST", f"/api/downloads/{task_id}/stop", {})[0], 200)
        self.assertEqual((self.worker.dispatch(), self.worker.groups.fill()), ([], []))  # no room: nothing new
        group_url = f"/api/downloads/groups/{group_id}"
        downloads = self.root / "temp" / "downloads"

        def kept():
            return (self.store.list_tasks(), self.worker.groups.members(group_id), self.worker.groups.group(group_id),
                    sorted(path.name for path in downloads.iterdir()), self.input_files())

        for waiting in ("pending", "held"):
            status, detail = self.call("GET", group_url)
            group = detail["group"]
            self.assertEqual((status, group["counts"][waiting], group["counts"]["stopped"],
                              group["counts"]["completed"], group["finished"]), (200, 3, 2, 1, False), waiting)
            self.assertEqual([member["status"] for member in detail["members"]],
                             ["CREATED"] * 3 + [waiting.upper()] * 3)
            self.assertEqual([self.task(task_id)["state"] in FINAL_STATES for task_id in tasks], [True] * 3)
            before = kept()
            for path in (f"{group_url}/remove", f"/api/downloads/{page['id']}/remove"):
                status, refused = self.call("POST", path, {})
                self.assertEqual((status, set(refused)), (409, {"error"}), (waiting, path, refused))
                self.assertTrue(refused["error"].startswith(REMOVE_REFUSED), refused)
            self.assertEqual(kept(), before)  # nothing removed: no row, no temp folder, no file
            if waiting == "pending":
                status, stopped = self.call("POST", f"{group_url}/stop", {})  # Dừng nhóm: PENDING becomes HELD
                self.assertEqual(status, 200, stopped)
        files = self.input_files()
        self.assertEqual(list(files), [f"001 - {TITLE} - S01E01.mp4"])
        status, cancelled = self.call("POST", f"{group_url}/cancel", {})
        self.assertEqual((status, cancelled["group"]["state"], cancelled["group"]["finished"]),
                         (200, "CANCELLED", True))
        status, removed = self.call("POST", f"{group_url}/remove", {})
        self.assertEqual((status, removed["removed"]), (200, True), removed)
        self.assertIsNone(self.worker.groups.group(group_id))
        self.assertEqual(self.worker.groups.members(group_id), [])
        self.assertEqual([self.task(task_id) for task_id in (page["id"], *tasks)], [None] * 4)
        self.assertEqual([(downloads / str(task_id)).exists() for task_id in (page["id"], *tasks)], [False] * 4)
        self.assertEqual(self.input_files(), files)  # the published file stays, byte for byte
        self.assertEqual(self.store.unfinished_count(), 98)  # the other tasks stay

    def test_episodes_the_page_lists_later_never_join_a_confirmed_group(self):
        """Plan P5: "Tải tất cả" is the list seen when it was pressed. The page then lists two more episodes while
        seven members still wait for room: the dispatch passes that fill them and every episode's own read of
        the page leave the same twelve members; no member, task or file for the new episodes, the page is not
        read again (its list route answers NOT_WAITING) and a repeat of the confirm returns the same group."""
        fillers = self.store.add_tasks([f"https://clips.example/v/{n}" for n in range(95)])
        for task in fillers:
            self.store.transition(task["id"], {"QUEUED"}, "STOPPED")
        page = self.waiting_page()
        episodes = f"/api/downloads/{page['id']}/episodes"
        status, listing = self.call("GET", episodes)
        confirm = {"selection": {"mode": "all", "variant_kind": KIND_1080}, "fingerprint": listing["fingerprint"],
                   "idempotency_key": "later-0001"}
        status, created = self.call("POST", f"{episodes}/confirm", confirm)
        self.assertEqual((status, created["group"]["total"]), (200, 12), created)
        group_id = created["group"]["id"]
        self.assertEqual((created["group"]["counts"]["queued"], created["group"]["counts"]["pending"]), (5, 7))

        def shape():
            return [(member["ordinal"], member["item_key"], member["code"], member["selection_json"])
                    for member in self.worker.groups.members(group_id)]
        members = shape()
        later = series(14)  # the site now lists s1e13 and s1e14 on the same page
        new_keys = {later.selection(episode, variant).key for episode in later.episodes[12:]
                    for variant in episode.variants}
        self.assertEqual(len(new_keys), 4)
        self.alpha.show(later)
        self.serve(later)
        read = []
        self.alpha.before = lambda ctx: read.append(episode_of(ctx))
        for task in fillers:  # room comes back: the dispatch passes below fill the seven waiting members
            self.worker.cancel(task["id"])
        self.worker.set_slots(3)
        self.run_all(180)
        self.assertEqual(shape(), members)
        tasks = self.worker.groups.tasks_of(group_id)
        self.assertEqual([(task["state"], task["item_key"]) for task in tasks],
                         [("COMPLETED", item_key) for _, item_key, _, _ in members])
        self.assertEqual([task for task in self.store.list_tasks() if task["item_key"] in new_keys], [])
        self.assertEqual(sorted(read), sorted(f"s1e{n}" for n in range(1, 13) for _ in range(2)))  # probe, fresh
        self.assertEqual(self.input_names(), [f"{n:03d} - {TITLE} - S01E{n:02d}.mp4" for n in range(1, 13)])
        summary = self.worker.groups.summary(group_id)
        self.assertEqual((summary["total"], summary["done"], summary["finished"]), (12, 12, True))
        status, again = self.call("GET", episodes)
        self.assertEqual((status, again["code"], again["group_id"], again["state"]),
                         (409, "NOT_WAITING", group_id, "EXPANDED"))
        status, replay = self.call("POST", f"{episodes}/confirm", confirm)
        self.assertEqual((status, replay["replay"], replay["group"]["id"], replay["group"]["total"]),
                         (200, True, group_id, 12))
        status, refused = self.call("POST", f"{episodes}/confirm", {**confirm, "idempotency_key": "later-0002"})
        self.assertEqual((status, refused["code"]), (409, "NOT_WAITING"))
        rows = self.store.list_tasks()
        self.assertEqual((self.worker.dispatch(), self.worker.groups.fill()), ([], []))  # more passes add nothing
        self.assertEqual((shape(), self.store.list_tasks()), (members, rows))

    def test_all_on_an_incomplete_list_needs_the_seen_episodes_confirmed(self):
        """Mode "all" on a list the reader could not read whole (E5 checks "pick"): without confirm_scope true the
        confirm route answers 400 SCOPE_NOT_CONFIRMED with the count and the label to confirm and writes
        nothing; with it the group is made of the episodes seen, marked incomplete with its note."""
        self.alpha.show(series(12, complete=False))
        page = self.waiting_page()
        episodes = f"/api/downloads/{page['id']}/episodes"
        status, listing = self.call("GET", episodes)
        self.assertEqual((status, listing["listing"]["complete"], listing["listing"]["episode_count"]),
                         (200, False, 12))
        kept = (self.store.list_tasks(), self.worker.groups.summaries(), self.worker.episodes(page["id"]))
        selection = {"mode": "all", "variant_kind": KIND_1080}
        for number, scope in enumerate((None, False, "true", 1), start=1):  # only the JSON true confirms
            body = {"selection": selection, "fingerprint": listing["fingerprint"],
                    "idempotency_key": f"scope-{number:04d}"}
            if scope is not None:
                body["confirm_scope"] = scope
            status, refused = self.call("POST", f"{episodes}/confirm", body)
            self.assertEqual((status, refused.get("code"), refused.get("count"), refused.get("confirm_label")),
                             (400, "SCOPE_NOT_CONFIRMED", 12, "Tải 12 tập đã thấy"), (scope, refused))
            self.assertIn("chỉ gồm 12 tập", refused["error"])
        self.assertEqual((self.store.list_tasks(), self.worker.groups.summaries(),
                          self.worker.episodes(page["id"])), kept)  # nothing created; the page still waits
        status, created = self.call("POST", f"{episodes}/confirm", {
            "selection": selection, "fingerprint": listing["fingerprint"], "idempotency_key": "scope-0009",
            "confirm_scope": True})
        group = created.get("group") or {}
        self.assertEqual((status, group.get("total"), group.get("complete"), created.get("replay")),
                         (200, 12, False, False), created)
        self.assertIn("chỉ gồm 12 tập", group["note"])
        self.assertEqual([member["code"] for member in self.worker.groups.members(group["id"])],
                         [f"S01E{n:02d}" for n in range(1, 13)])
        self.assertEqual(self.task(page["id"])["state"], "EXPANDED")

    def test_account_routes_are_pc_only_take_only_a_configured_id_and_ignore_the_body(self):
        bait = {"login_url": "https://evil.example/login", "cookie": "BF-CANARY-bait", "module": "os",
                "command": "calc.exe", "password": "BF-CANARY-pw"}
        self.assertEqual(self.call("POST", "/api/download-accounts/alpha/login", bait, token=None)[0], 403)
        self.assertEqual(self.call("POST", "/api/download-accounts/alpha/login", bait,
                                   host="evil.example")[0], 403)
        status, unknown = self.call("POST", "/api/download-accounts/zzz/login", bait)
        self.assertEqual((status, unknown["code"]), (404, "ACCOUNT_UNKNOWN"))
        self.assertEqual(self.call("POST", "/api/download-accounts/ALPHA/login", bait)[0], 404)
        status, unsupported = self.call("POST", "/api/download-accounts/alpha/login", bait)
        self.assertEqual((status, unsupported["code"]), (409, "LOGIN_UNSUPPORTED"))
        self.assertEqual(self.launcher.calls, 0)  # no sign-in check for the adapter: no window
        with mock.patch.object(self.handler, "loopback_client", return_value=False):
            status, refused = self.call("POST", "/api/download-accounts/alpha/disconnect", bait)
        self.assertEqual((status, refused["code"]), (403, "pc_only"))
        connect(self.manager)
        status, done = self.call("POST", "/api/download-accounts/alpha/disconnect", bait)
        self.assertEqual((status, done["source"]["state"]), (200, "NOT_CONNECTED"))
        status, snapshot = self.call("GET", "/api/downloads")
        text = json.dumps(snapshot)
        self.assertNotIn("BF-CANARY", text)
        self.assertNotIn("evil.example", text)
        self.assertEqual([item["id"] for item in snapshot["accounts"]["sources"]], ["alpha", "beta"])

    def test_the_phone_drives_episodes_and_groups_but_never_signs_in(self):
        page = self.waiting_page()
        port, host, headers = self.open_phone()
        phone = {"port": port, "host": host, "headers": headers}
        status, refused = self.call("POST", "/api/download-accounts/alpha/login", {"cookie": "BF-CANARY"}, **phone)
        self.assertEqual(status, 403)
        self.assertIn("Chỉ làm trên PC", refused["error"])
        self.assertEqual((refused["code"], refused["error"]), ("pc_only", PC_ONLY_SOURCE_ACCOUNTS))
        status, listing = self.call("GET", f"/api/downloads/{page['id']}/episodes", **phone)
        self.assertEqual((status, listing["listing"]["episode_count"], listing["draft"]), (200, 12, None))
        selection = {"mode": "all", "variant_kind": KIND_1080}
        status, saved = self.call("POST", f"/api/downloads/{page['id']}/episodes/draft", {
            "selection": selection, "fingerprint": listing["fingerprint"], "revision": listing["revision"]}, **phone)
        self.assertEqual((status, saved["revision"], saved["plan"]["confirm_label"]), (200, 1, "Tải 12 tập"))
        self.assertEqual(self.worker.episodes(page["id"])["draft"]["mode"], "all")  # kept by the store
        status, created = self.call("POST", f"/api/downloads/{page['id']}/episodes/confirm", {
            "selection": selection, "fingerprint": listing["fingerprint"], "idempotency_key": "phone-0001"}, **phone)
        self.assertEqual(status, 200, created)
        status, stopped = self.call("POST", f"/api/downloads/groups/{created['group']['id']}/stop", {}, **phone)
        self.assertEqual(status, 200, stopped)
        self.assertEqual(self.launcher.calls, 0)

    def test_the_phone_listener_refuses_every_account_route_before_reading_a_body(self):
        page = self.waiting_page()  # alpha signed in
        before = (self.manager.status("alpha"), self.manager.session_gate("alpha"))
        port, host, headers = self.open_phone()
        for action in ("login", "cancel-login", "disconnect"):
            for length in (1024 * 1024, 50):  # the body never comes: an answer within NO_BODY_WAIT did not read it
                answer = self.refused_on_phone((port, host, headers), "alpha", action, length)
                self.assertEqual((answer["code"], answer["error"]), ("pc_only", PC_ONLY_SOURCE_ACCOUNTS))
        self.assertEqual(self.launcher.calls, 0)
        self.assertEqual(self.runtime.coordinator.active(), [])
        self.assertEqual((self.manager.status("alpha"), self.manager.session_gate("alpha")), before)
        phone = {"port": port, "host": host, "headers": headers}
        status, listing = self.call("GET", f"/api/downloads/{page['id']}/episodes", **phone)
        self.assertEqual((status, listing["revision"]), (200, 0))
        selection = {"mode": "pick", "episodes": ["s1e1", "s1e2"], "variant_kind": KIND_1080}
        status, saved = self.call("POST", f"/api/downloads/{page['id']}/episodes/draft", {
            "selection": selection, "fingerprint": listing["fingerprint"], "revision": 0}, **phone)
        self.assertEqual((status, saved["revision"], saved["plan"]["confirm_label"]), (200, 1, "Tải 2 tập"))
        status, created = self.call("POST", f"/api/downloads/{page['id']}/episodes/confirm", {
            "selection": selection, "fingerprint": listing["fingerprint"], "idempotency_key": "phone-0002"}, **phone)
        self.assertEqual((status, created["group"]["total"]), (200, 2))
        group_id = created["group"]["id"]
        status, cancelled = self.call("POST", f"/api/downloads/groups/{group_id}/cancel", {}, **phone)
        self.assertEqual((status, cancelled["group"]["state"], cancelled["group"]["counts"]["cancelled"]),
                         (200, "CANCELLED", 2))
        for action in ("retry", "resume", "stop"):
            status, refused = self.call("POST", f"/api/downloads/groups/{group_id}/{action}", {}, **phone)
            self.assertEqual((status, refused["code"]), (409, "GROUP_CANCELLED"))
        status, detail = self.call("GET", f"/api/downloads/groups/{group_id}", **phone)
        self.assertEqual((status, [member["task_state"] for member in detail["members"]]),
                         (200, ["CANCELLED", "CANCELLED"]))
        self.assertEqual(self.launcher.calls, 0)
        self.assertEqual((self.manager.status("alpha"), self.manager.session_gate("alpha")), before)


class CapProbe:
    """TEMP triggers on a store's own connection (they live only on that connection and no production code is
    patched, as in test_download_groups' rollback test). After every task row inserted and every state change,
    inside the write transaction, they report how many tasks count toward the 100 cap (every state but the
    closed ones, as ``unfinished_count`` counts them) and how many hold a slot. A count over the cap only until
    its transaction ends, or only between two polls, is seen here; a reader on another connection never sees
    an uncommitted row."""

    def __init__(self):
        self._lock = threading.Lock()
        self.max_unfinished = self.max_running = self.inserts = self.changes = 0

    def _seen(self, kind: str, unfinished: int, running: int) -> int:
        with self._lock:
            self.max_unfinished = max(self.max_unfinished, unfinished)
            self.max_running = max(self.max_running, running)
            if kind == "insert":
                self.inserts += 1
            else:
                self.changes += 1
        return 0

    def install(self, store: DownloadStore) -> None:
        def listed(states):
            return ", ".join(f"'{state}'" for state in sorted(states))
        counts = (f"(SELECT COUNT(*) FROM download_tasks WHERE state NOT IN ({listed(CLOSED_STATES)})), "
                  f"(SELECT COUNT(*) FROM download_tasks WHERE state IN ({listed(SLOT_STATES)}))")
        db = store._connection  # noqa: SLF001 - the one connection every writer of the downloader uses
        with store._lock:  # noqa: SLF001 - no write runs while the triggers are made
            db.create_function("test_cap_seen", 3, self._seen)
            db.executescript(
                "CREATE TEMP TRIGGER test_cap_insert AFTER INSERT ON download_tasks "
                f"BEGIN SELECT test_cap_seen('insert', {counts}); END;"
                "CREATE TEMP TRIGGER test_cap_state AFTER UPDATE OF state ON download_tasks "
                f"BEGIN SELECT test_cap_seen('state', {counts}); END;")


class BigGroupTest(RouteCase):
    """A 500-episode group through the routes and the running worker. QueueProvider (no browser), one tiny MKV
    body for every file and the fake file check, for speed; the real check runs in the end-to-end tests."""

    def make_listing(self):
        return series(501, variants=(("v1080", "1080p"),))

    def setUp(self):
        super().setUp()
        self.gate = self.hold(threading.Event())
        self.flight_lock = threading.Lock()
        self.in_flight = self.max_in_flight = 0
        for episode in self.alpha.listing.episodes:
            self.server.route(file_path(episode.key, "v1080"), self.gated_file)

    def gated_file(self, seen, number):
        """Every file request (probe sample or transfer) waits for the gate; ``in_flight`` counts them."""
        with self.flight_lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            self.gate.wait(WAIT)
            return Reply(MEDIA["mkv"], content_type="video/x-matroska", etag='"v1"')
        finally:
            with self.flight_lock:
                self.in_flight -= 1

    def completed(self) -> int:
        return len(self.store.tasks_in({"COMPLETED"}))

    def test_a_500_episode_group_keeps_every_member_and_fills_under_the_cap_in_order(self):
        page = self.waiting_page()
        status, listing = self.call("GET", f"/api/downloads/{page['id']}/episodes")
        self.assertEqual((status, listing["listing"]["episode_count"], listing["max_episodes"]), (200, 501, 500))
        rows = (len(self.store.list_tasks()), self.worker.groups.summaries(), self.worker.episodes(page["id"]))
        status, refused = self.call("POST", f"/api/downloads/{page['id']}/episodes/confirm", {
            "selection": {"mode": "all", "variant_kind": KIND_1080}, "fingerprint": listing["fingerprint"],
            "idempotency_key": "big-00000001"})
        self.assertEqual((status, refused["code"], refused["count"]), (400, "GROUP_TOO_LARGE", 501))
        self.assertEqual((len(self.store.list_tasks()), self.worker.groups.summaries(),
                          self.worker.episodes(page["id"])), rows)  # nothing created, the page still waits
        picked = [f"s1e{n}" for n in range(1, 501)]
        probe = CapProbe()  # from the first episode task on: every write of a task row is counted in its transaction
        probe.install(self.store)
        status, created = self.call("POST", f"/api/downloads/{page['id']}/episodes/confirm", {
            "selection": {"mode": "pick", "episodes": picked, "variant_kind": KIND_1080},
            "fingerprint": listing["fingerprint"], "idempotency_key": "big-00000002"})
        self.assertEqual((status, created["group"]["total"], created["replay"]), (200, 500, False), created)
        group_id = created["group"]["id"]
        members = self.worker.groups.members(group_id)
        self.assertEqual(self.store.unfinished_count(), 100)
        self.assertEqual(sum(1 for member in members if member["status"] == "CREATED"), 100)
        self.assertEqual(sum(1 for member in members if member["status"] == "PENDING" and member["task_id"] is None),
                         400)
        resolved = []
        self.alpha.before = lambda ctx: resolved.append(int(ctx.task_dir.name))
        self.worker.start()
        self.assertTrue(wait_until(lambda: self.in_flight == 2))
        self.assertEqual(self.worker.dispatch(), [])  # both default slots are taken: nothing else starts
        running = {task["id"] for task in self.store.tasks_in(SLOT_STATES)}
        self.assertEqual(running, {member["task_id"] for member in members[:2]})  # the first two, in order
        self.assertEqual(set(resolved), running)  # a resolve only for a task that took a slot
        self.assertEqual(self.store.unfinished_count(), 100)
        self.assertEqual(self.call("POST", "/api/downloads/settings", {"slots": 3}), (200, {"slots": 3}))
        self.assertTrue(wait_until(lambda: self.in_flight == 3))
        self.assertEqual(self.worker.dispatch(), [])
        self.assertEqual(self.call("POST", "/api/downloads/settings", {"slots": 4})[0], 400)
        self.assertEqual((self.worker.slots(), self.max_in_flight), (3, 3))
        self.assertEqual(set(resolved), {member["task_id"] for member in members[:3]})
        self.gate.set()
        self.assertTrue(wait_until(lambda: self.completed() >= 250, 600, step=0.1), self.completed())
        self.gate.clear()  # the next requests wait: the stop below surely cuts three running episodes
        self.assertTrue(wait_until(lambda: self.in_flight == 3))
        self.restart()  # the worker shuts down mid-run; the store is opened again
        probe.install(self.store)  # the new connection, before its worker fills anything
        self.assertEqual((len(self.store.tasks_in({"INTERRUPTED"})), self.store.tasks_in(SLOT_STATES)), (3, []))
        self.gate.set()
        self.assertTrue(wait_until(lambda: self.in_flight == 0))
        self.center.downloads = self.service()
        self.worker.start()
        status, resumed = self.call("POST", f"/api/downloads/groups/{group_id}/resume", {})  # what the stop cut
        self.assertEqual(status, 200, resumed)
        self.assertTrue(wait_until(lambda: self.completed() == 500, 900, step=0.1), self.completed())
        # Never over the cap or the slots, even inside a transaction; and both were reached, so the probe saw them.
        self.assertEqual((probe.max_unfinished, probe.max_running), (100, 3))
        self.assertEqual(probe.inserts, 500)  # every episode task was written while the probe watched
        self.assertGreaterEqual(probe.changes, 2 * 500)  # and each one's steps: at least a slot, then COMPLETED
        self.assertLessEqual(self.max_in_flight, 3)
        status, detail = self.call("GET", f"/api/downloads/groups/{group_id}")
        group = detail["group"]
        self.assertEqual((status, group["done"], group["total"], group["finished"]), (200, 500, 500, True))
        members = self.worker.groups.members(group_id)
        self.assertEqual([member["ordinal"] for member in members], list(range(1, 501)))
        ids = [member["task_id"] for member in members]
        self.assertEqual(ids, sorted(ids))  # task ids rise with the ordinal
        self.assertEqual(len(set(ids)), 500)
        self.assertEqual(len({member["item_key"] for member in members}), 500)
        self.assertEqual(self.store.unfinished_count(), 0)
        self.assertTrue(set(resolved) <= set(ids))
        expected = [f"{member['ordinal']:03d} - {TITLE} - {member['code']}.mkv" for member in members]
        self.assertEqual(self.input_names(), expected)  # width-3 names sort in group order
        self.assertEqual(expected[99], f"100 - {TITLE} - S01E100.mkv")
        self.assertFalse(self.ytdlp_called())


class ServiceCrossingTest(RouteCase):
    """Whole DownloadService objects on the same root, each with its own manager, store and worker: a restart that
    keeps a valid session, and a stop that waits for a real sign-in thread and a running task."""

    def setUp(self):
        super().setUp()
        self.movie = film()
        self.serve(self.movie)
        self.services: list[DownloadService] = []

    def tearDown(self):
        try:
            self.release_held()  # a held file check or sign-in check would otherwise outlive the stop below
            for service in self.services:  # stop is repeatable: it only checks the close again
                service.stop()
                self.assertTrue(service.wait_closed(WAIT))
        finally:
            super().tearDown()

    def full_service(self, *, launcher=None, verifiers=None, verifier=ok_verifier) -> DownloadService:
        """A service as the Control Center makes it, on this root: a new manager (same Windows account), the
        runtime of its sign-ins, a store and a worker; the handler then uses it."""
        manager = account_manager(self.root, self.config, clock=self.clock)
        alpha = QueueProvider(self.config.sources["alpha"], manager, self.alpha.listing, more=(self.movie,))
        beta = QueueProvider(self.config.sources["beta"], manager, self.beta.listing)
        store = DownloadStore(self.root / "state" / "downloads.sqlite3")
        worker = self.make_worker([alpha, beta], store=store, verifier=verifier)
        runtime = AccountRuntime(self.root, manager=manager, coordinator_options={
            "launcher": launcher or NoWindow(), "verifiers": verifiers or {}, "poll_seconds": 0.01})
        service = DownloadService(self.root, cleanable=lambda: (0, 0), bin_reader=None, store=store, worker=worker,
                                  accounts=runtime)
        self.services.append(service)
        self.center.downloads = service
        return service

    @staticmethod
    def kinds(service, task_id):
        return [event["kind"] for event in service.store.events(task_id)]

    def test_a_restart_keeps_a_valid_session_and_settles_an_open_sign_in_without_a_window(self):
        first = self.full_service()
        connect(first.accounts.manager)  # at T0
        signed = first.accounts.manager.status("alpha")
        generation = first.accounts.manager.session_gate("alpha")[2]
        first.start()
        page, = first.worker.add([page_url()], rights_confirmed=True)
        self.assertTrue(wait_until(lambda: first.store.get(page["id"])["state"] == "NEEDS_CHOICE"))
        status, listing = self.call("GET", f"/api/downloads/{page['id']}/episodes")
        self.assertEqual(status, 200, listing)  # the dispatch thread runs: its upkeep must keep the stored list
        selection = {"mode": "pick", "episodes": ["s1e2", "s1e5"], "variant_kind": KIND_1080}
        status, saved = self.call("POST", f"/api/downloads/{page['id']}/episodes/draft", {
            "selection": selection, "fingerprint": listing["fingerprint"], "revision": 0})
        self.assertEqual((status, saved["revision"]), (200, 1))
        first.accounts.manager.begin_login("alpha")  # a sign-in still open when the PC stops
        self.assertEqual(first.accounts.manager.status("alpha")["state"], "LOGGING_IN")
        first.worker.shutdown(10)  # the dispatcher is gone: a film pasted now stays queued
        movie, = first.worker.add([page_url(film="m1")], rights_confirmed=True)
        first.stop()
        self.assertTrue(first.wait_closed(WAIT))
        self.assertIsNone(first.close_error)

        self.clock.at(1000)
        launcher = NoWindow()
        second = self.full_service(launcher=launcher)
        status_before = second.accounts.manager.status("alpha")
        self.assertEqual((status_before["state"], status_before["error_code"]), ("CONNECTED", "LOGIN_TIMEOUT"))
        second.start()
        self.assertIsNone(second.start_error)
        self.assertEqual(second.accounts.recovered, {"unsettled": [], "profiles_cleared": True})
        settled = second.accounts.manager.status("alpha")
        self.assertEqual((settled["state"], settled["error_code"], settled["authenticated_at"]),
                         ("CONNECTED", "LOGIN_INTERRUPTED", signed["authenticated_at"]))
        self.assertEqual(second.accounts.manager.session_gate("alpha"), (True, None, generation))
        self.assertTrue(wait_until(lambda: second.store.get(movie["id"])["state"] == "COMPLETED", 90),
                        second.store.events(movie["id"]))
        self.assertNotIn("WAITING_LOGIN", self.kinds(second, movie["id"]))
        self.assertEqual(len(self.input_names()), 1)
        status, again = self.call("GET", f"/api/downloads/{page['id']}/episodes")
        self.assertEqual((status, again["revision"], again["draft"]["episodes"], again["fingerprint"]),
                         (200, 1, ["s1e2", "s1e5"], listing["fingerprint"]))
        self.assertEqual(again["plan"]["confirm_label"], "Tải 2 tập")
        status, snapshot = self.call("GET", "/api/downloads")
        alpha = next(item for item in snapshot["accounts"]["sources"] if item["id"] == "alpha")
        self.assertEqual((status, alpha["state"], alpha["login_running"], alpha["last_login"]),
                         (200, "CONNECTED", False, None))
        self.assertEqual(launcher.calls, 0)  # no window at start, while recovering or while dispatching
        self.assertEqual(second.accounts.manager.session_gate("alpha"), (True, None, generation))

    def test_stop_waits_for_a_real_sign_in_thread_and_a_running_task_then_routes_answer_503(self):
        verifier = GatedVerifier()
        windows = []

        def launcher(source, http_client, vault, control, permit, stop_at):
            windows.append(FakeWindow(control, state=state("v9", source.id)))
            return windows[-1]
        checking, release = threading.Event(), self.hold(threading.Event())
        self.hold(verifier.release)

        def held_check(*args, **kwargs):  # the file check takes no stop request: it outlives a bounded stop
            checking.set()
            release.wait(WAIT)
            return ok_verifier(*args, **kwargs)
        service = self.full_service(launcher=launcher, verifiers={"ticket-files": verifier}, verifier=held_check)
        manager = service.accounts.manager
        connect(manager)  # alpha signed in: its film runs
        service.start()
        movie, = service.worker.add([page_url(film="m1")], rights_confirmed=True)
        self.assertTrue(checking.wait(WAIT))
        status, started = self.call("POST", "/api/download-accounts/beta/login", {})
        self.assertEqual((status, started["login"], started["source"]["state"]), (202, "STARTED", "LOGGING_IN"))
        self.assertTrue(verifier.entered.wait(WAIT))
        quick = mock.patch.object(service.worker, "shutdown",
                                  side_effect=lambda timeout=20.0: DownloadWorker.shutdown(service.worker, 1.0))
        quick.start()
        self.addCleanup(quick.stop)
        stopper = threading.Thread(target=service.stop, name="service-stop", daemon=True)
        stopper.start()
        # The windows were asked to close: the sign-in's attempt ended, its check is still held.
        self.assertTrue(wait_until(lambda: manager.status("beta")["state"] != "LOGGING_IN"))
        self.assertTrue(stopper.is_alive())
        self.assertFalse(service.wait_closed(0))
        self.assertTrue(service.accounts.busy())
        self.assertEqual(service.store.get(movie["id"])["state"], "VERIFYING")  # the store still answers
        self.assertTrue(manager.session_gate("alpha")[0])  # and the manager too
        verifier.release.set()  # the check now says "signed in": too late, the stop came first
        stopper.join(WAIT)
        self.assertFalse(stopper.is_alive())
        self.assertTrue(wait_until(lambda: "beta" in service.accounts.last_login))  # the callback follows the run
        last = service.accounts.last_login["beta"]
        self.assertEqual((last["code"], last["connected"], last["failure"]), ("LOGIN_CANCELLED", False, None))
        self.assertEqual(manager.session_gate("beta"), (False, "NOT_CONNECTED", None))  # nothing saved
        self.assertEqual(sorted(manager.vault.source_folder("beta").glob("session-*")), [])
        self.assertFalse(service.wait_closed(0))  # the film's check still runs: store and manager stay open
        self.assertEqual(service.store.get(movie["id"])["state"], "VERIFYING")
        release.set()
        self.assertTrue(service.wait_closed(30))
        self.assertIsNone(service.close_error)
        self.assertIsNone(service.worker.last_error)
        self.assertIsNone(service.accounts.error)
        self.assertEqual(len(windows), 1)
        self.assertTrue(windows[0].closed)
        status, answer = self.call("GET", "/api/downloads")  # the closed store: a fixed message, never its text
        self.assertEqual((status, answer["code"], answer["kind"]), (503, "DOWNLOAD_STATE_ERROR", "ProgrammingError"))
        self.assertNotIn("closed", answer["error"])

    def test_the_phone_cannot_cancel_or_disconnect_while_a_pc_sign_in_runs(self):
        """R19 while a sign-in runs (Q4 checks the phone with none open): the PC's Đăng nhập for beta has its
        window open and its check held. cancel-login and disconnect sent to the phone listener, for beta and for
        the signed-in alpha, are refused 403 pc_only before any body is read. The PC sign-in keeps running and
        then ends CONNECTED on the source's evidence; alpha's session never changes."""
        verifier = GatedVerifier()
        self.hold(verifier.release)
        windows = []

        def launcher(source, http_client, vault, control, permit, stop_at):
            windows.append(FakeWindow(control, state=state("v9", source.id)))
            return windows[-1]
        service = self.full_service(launcher=launcher, verifiers={"ticket-files": verifier})
        manager = service.accounts.manager
        connect(manager)  # alpha signed in
        service.start()

        def alpha_session():
            files = {name: data for name, data in self.vault_files(SID).items() if name.startswith("alpha/")}
            return manager.status("alpha"), manager.session_gate("alpha"), files
        kept = alpha_session()
        self.assertTrue(kept[1][0] and kept[2])
        status, started = self.call("POST", "/api/download-accounts/beta/login", {})
        self.assertEqual((status, started["login"], started["source"]["state"]), (202, "STARTED", "LOGGING_IN"))
        self.assertTrue(verifier.entered.wait(WAIT))  # the window is open and its check is held
        phone = self.open_phone()
        for source_id in ("beta", "alpha"):
            for action in ("cancel-login", "disconnect"):
                answer = self.refused_on_phone(phone, source_id, action, 1024 * 1024)
                self.assertEqual((answer["code"], answer["error"]), ("pc_only", PC_ONLY_SOURCE_ACCOUNTS))
        # Nothing reached the sign-in: it still runs, its window is open, no outcome yet, alpha as it was.
        self.assertEqual(service.accounts.coordinator.active(), ["beta"])
        self.assertEqual(manager.status("beta")["state"], "LOGGING_IN")
        self.assertEqual((len(windows), windows[0].closed, windows[0].user_closed), (1, False, False))
        self.assertNotIn("beta", service.accounts.last_login)
        self.assertEqual(alpha_session(), kept)
        verifier.release.set()  # the source's evidence: the sign-in the PC started ends on its own
        self.assertTrue(wait_until(lambda: "beta" in service.accounts.last_login))
        last = service.accounts.last_login["beta"]
        self.assertEqual((last["code"], last["connected"], last["failure"]), ("CONNECTED", True, None))
        self.assertEqual(manager.session_gate("beta")[:2], (True, None))
        self.assertEqual(manager.status("beta")["state"], "CONNECTED")
        self.assertTrue(wait_until(lambda: windows[0].closed))
        self.assertEqual(alpha_session(), kept)


if __name__ == "__main__":
    unittest.main()
