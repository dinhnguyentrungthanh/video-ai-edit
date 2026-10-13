"""Automatic retries of passing network errors, per-host pacing of source lookups and "Thử lại các lượt lỗi mạng"
(docs/VIDEO_DOWNLOAD_PLAN.md 4.15).

Links are ``.example`` hosts served by tests/source_fixtures.FixtureServer (a dropped connection, a stalled answer,
429/503 with Retry-After) or read by the fake yt-dlp of tests/fake_yt_dlp.py; nothing reaches the network. The
worker's store runs on a fake clock: a task waiting in WAITING_RETRY moves on only when the test moves the clock.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from biliflow.download_http import HttpError, parse_retry_after
from biliflow.download_retry import HostPacer, NetworkPolicy, is_transient, retry_delay
from biliflow.download_runner import ProcessControl, YtDlpRunner
from biliflow.download_sources import DirectMediaProvider, SourceRegistry, SourceTransfers
from biliflow.download_store import FINAL_STATES, SLOT_STATES, STATES, DownloadStore
from biliflow.download_worker import STOPPABLE, DownloadActionError, DownloadWorker
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
    temp_root,
)
from tests.test_download_worker import CLIP, FAKE, WorkerCase, video

URL = "http://media.example/v/clip.mp4"
MEDIA: dict[str, object] = {}
FAST = NetworkPolicy(host_gap=0.0)  # the real schedule (20/60/180 s on the fake clock), no pacing pause
DROP = Reply(drop=True)


def setUpModule():
    if HAVE_FFMPEG:
        base = temp_root("download-retry-")
        MEDIA["base"] = base
        MEDIA["clip"] = make_clip(base / "clip.mp4", seconds=3, size="160x120").read_bytes()


def tearDownModule():
    if "base" in MEDIA:
        remove_tree(MEDIA["base"])


def first_answers(*replies: Reply, then: Reply | None = None):
    """A route whose first answers are ``replies`` (one per request) and every later one ``then`` (the clip)."""
    def route(seen, number):
        if number <= len(replies):
            return replies[number - 1]
        return then or Reply(MEDIA["clip"], content_type="video/mp4", etag='"v1"')
    return route


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ------------------------------------------------------------------------------------------- pure checks
class TransientTest(unittest.TestCase):
    def test_only_passing_network_errors_are_retried(self):
        for code, status in (("NETWORK", None), ("DNS_FAILED", None), ("SERVER_BUSY", 429), ("SERVER_BUSY", 502),
                             ("SERVER_BUSY", 503), ("SERVER_BUSY", 504), ("SERVER_BUSY", None)):
            with self.subTest(code=code, status=status):
                self.assertTrue(is_transient(code, status))
        for code, status in (("SERVER_BUSY", 500), ("SERVER_BUSY", 501), ("UNAVAILABLE", 404), ("UNAVAILABLE", 410),
                             ("LOGIN_REQUIRED", 401), ("FORBIDDEN", 403), ("TLS_ERROR", None), ("DISK_FULL", None),
                             ("SOURCE_CHANGED", None), ("PLAYER_CHANGED", None), ("DRM", None), ("UNSUPPORTED", None),
                             ("INTERNAL_ERROR", None), (None, None)):
            with self.subTest(code=code, status=status):
                self.assertFalse(is_transient(code, status))


class RetryDelayTest(unittest.TestCase):
    def test_the_schedule_with_jitter_then_none(self):
        policy = NetworkPolicy()
        self.assertEqual([retry_delay(policy, done, rand=0.5) for done in range(4)], [20.0, 60.0, 180.0, None])
        self.assertAlmostEqual(retry_delay(policy, 0, rand=0.0), 16.0)
        self.assertAlmostEqual(retry_delay(policy, 0, rand=1.0), 24.0)
        self.assertAlmostEqual(retry_delay(policy, 2, rand=0.0), 144.0)

    def test_retry_after_is_honoured_up_to_the_limit(self):
        policy = NetworkPolicy()
        self.assertEqual(retry_delay(policy, 0, retry_after=100.0, rand=0.5), 100.0)
        self.assertEqual(retry_delay(policy, 1, retry_after=5.0, rand=0.5), 60.0)  # never sooner than the schedule
        self.assertEqual(retry_delay(policy, 0, retry_after=900.0, rand=0.5), 900.0)
        self.assertIsNone(retry_delay(policy, 0, retry_after=901.0, rand=0.5))
        self.assertIsNone(retry_delay(policy, 3, retry_after=1.0, rand=0.5))


class ParseRetryAfterTest(unittest.TestCase):
    def test_seconds_and_http_dates(self):
        now = datetime(2026, 10, 13, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(parse_retry_after("120", now), 120.0)
        self.assertEqual(parse_retry_after(" 0 ", now), 0.0)
        self.assertEqual(parse_retry_after(format_datetime(now + timedelta(seconds=90), usegmt=True), now), 90.0)
        self.assertEqual(parse_retry_after(format_datetime(now - timedelta(hours=1), usegmt=True), now), 0.0)
        for value in (None, "", "soon", "-5", "1.5", "9" * 40, "Mon, 99 Foo 2026"):
            with self.subTest(value=value):
                self.assertIsNone(parse_retry_after(value, now))

    def test_a_busy_answer_carries_its_retry_after(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=300)
        with FixtureServer() as server:
            server.route("/busy", Reply(b"busy", 429, "text/plain", headers={"Retry-After": "30"}))
            server.route("/later", Reply(b"later", 503, "text/plain",
                                         headers={"Retry-After": format_datetime(when, usegmt=True)}))
            server.route("/broken", Reply(b"broken", 500, "text/plain", headers={"Retry-After": "30"}))
            http = server.http()
            control = ProcessControl()
            for path, status in (("/busy", 429), ("/later", 503), ("/broken", 500)):
                with self.subTest(path=path), self.assertRaises(HttpError) as caught:
                    http.fetch(f"http://busy.example{path}", control, limit=1024)
                self.assertEqual((caught.exception.code, caught.exception.status), ("SERVER_BUSY", status))
                if path == "/busy":
                    self.assertEqual(caught.exception.retry_after, 30.0)
                elif path == "/later":
                    self.assertTrue(280 <= caught.exception.retry_after <= 300, caught.exception.retry_after)
                else:
                    self.assertIsNone(caught.exception.retry_after)

    def test_a_dropped_connection_and_a_stalled_answer_are_network_errors(self):
        with FixtureServer() as server:
            server.route("/drop", DROP)
            server.route("/stall", Reply(b"late", stall=1.0))
            http = server.http(read_timeout=0.3)
            control = ProcessControl()
            with self.assertRaises(HttpError) as dropped:
                http.fetch("http://media.example/drop", control, limit=1024)
            self.assertEqual((dropped.exception.code, dropped.exception.retryable), ("NETWORK", True))
            self.assertIn("RemoteDisconnected", dropped.exception.message)
            with self.assertRaises(HttpError) as stalled:
                http.fetch("http://media.example/stall", control, limit=1024)
            self.assertEqual((stalled.exception.code, stalled.exception.retryable), ("NETWORK", True))
            self.assertIn("quá thời gian chờ", stalled.exception.message)


class FakeMonotonic:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class HostPacerTest(unittest.TestCase):
    def test_one_lookup_per_host_and_a_pause_after_it(self):
        clock = FakeMonotonic()
        pacer = HostPacer(5.0, clock=clock)
        self.assertTrue(pacer.try_acquire("a.example", 1))
        self.assertFalse(pacer.try_acquire("a.example", 2))
        self.assertTrue(pacer.try_acquire("b.example", 3))  # another host is not held back
        self.assertTrue(pacer.try_acquire("a.example", 1))  # the holder again: idempotent
        pacer.release("a.example", 2)  # not the holder: nothing changes
        self.assertFalse(pacer.try_acquire("a.example", 2))
        pacer.release("a.example", 1)
        self.assertFalse(pacer.try_acquire("a.example", 2))
        clock.now = 1004.9
        self.assertFalse(pacer.try_acquire("a.example", 2))
        clock.now = 1005.0
        self.assertTrue(pacer.try_acquire("a.example", 2))

    def test_a_waiting_run_goes_before_a_new_probe(self):
        pacer = HostPacer(0.0)
        self.assertTrue(pacer.try_acquire("a.example", 1))
        control = ProcessControl()
        got = threading.Event()
        waiter = threading.Thread(target=lambda: pacer.wait("a.example", 2, control, poll=0.01) and got.set())
        waiter.start()
        self.assertTrue(wait_until(lambda: pacer.waiting("a.example") == 1))
        self.assertFalse(pacer.try_acquire("a.example", 3))  # the dispatcher yields to the waiting run
        pacer.release("a.example", 1)
        waiter.join(5)
        self.assertTrue(got.is_set())
        self.assertFalse(pacer.try_acquire("a.example", 3))
        pacer.release("a.example", 2)
        self.assertTrue(pacer.try_acquire("a.example", 3))

    def test_a_stop_ends_the_wait(self):
        pacer = HostPacer(0.0)
        self.assertTrue(pacer.try_acquire("a.example", 1))
        control = ProcessControl()
        threading.Timer(0.1, control.request, args=("stop",)).start()
        self.assertFalse(pacer.wait("a.example", 2, control, poll=0.01))
        self.assertEqual(pacer.waiting("a.example"), 0)


class StoreTest(unittest.TestCase):
    def test_the_waiting_state_holds_no_slot_and_is_not_final(self):
        self.assertIn("WAITING_RETRY", STATES)
        self.assertNotIn("WAITING_RETRY", SLOT_STATES)
        self.assertNotIn("WAITING_RETRY", FINAL_STATES)
        self.assertIn("WAITING_RETRY", STOPPABLE)

    def test_the_retry_columns_are_kept_across_opens(self):
        root = temp_root("download-retry-store-")
        try:
            path = root / "downloads.sqlite3"
            store = DownloadStore(path)
            task, = store.add_tasks([CLIP])
            store.close()
            store = DownloadStore(path)
            moved = store.transition(task["id"], {"QUEUED"}, "WAITING_RETRY", retry_count=1,
                                     retry_at="2026-10-13T08:00:20+00:00", retry_phase="PROBING")
            self.assertEqual((moved["retry_count"], moved["retry_phase"]), (1, "PROBING"))
            store.close()
        finally:
            remove_tree(root)


# ------------------------------------------------------------------------------------------- worker cases
class RetryWorkerCase(WorkerCase):
    """A worker with automatic retries (``policy``) whose jitter is fixed at the middle (factor 1.0)."""

    policy: NetworkPolicy | None = FAST

    def make_worker(self, verifier=None):
        runner = YtDlpRunner(self.root, command_prefix=[sys.executable, str(FAKE)], deno_path=None,
                             env_extra={"FAKE_YTDLP_SCENARIO": str(self.scenario_path),
                                        "FAKE_YTDLP_LOG": str(self.log_path)})
        options = {"resolver": lambda host, port: ["93.184.215.14"], **self.worker_options()}
        worker = DownloadWorker(self.root, self.store, runner=runner, verifier=verifier or self.verifier,
                                ffmpeg=FFMPEG, ffprobe=FFPROBE, space_probe=lambda root: tuple(self.space),
                                space_retry_seconds=0.05, cache_pruner=lambda root: {"removed_files": 0},
                                network_policy=self.policy, **options)
        worker.retry_random = lambda: 0.5
        return worker

    def worker_options(self):
        return {}

    def advance(self, seconds):
        self.clock.now += timedelta(seconds=seconds)

    def task(self, task_id):
        return self.store.get(task_id)

    def kinds(self, task_id):
        return [event["kind"] for event in self.store.events(task_id)]

    def assert_waiting(self, task_id, count, seconds, phase="PROBING"):
        task = self.task(task_id)
        self.assertEqual((task["state"], task["retry_count"], task["retry_phase"]), ("WAITING_RETRY", count, phase),
                         (task, self.store.events(task_id)))
        self.assertEqual(datetime.fromisoformat(task["retry_at"]), self.clock.now + timedelta(seconds=seconds))
        self.assertNotIn(task_id, self.worker.running_ids())
        self.assertNotIn(task_id, [item["id"] for item in self.store.tasks_in(SLOT_STATES)])


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class SourceRetryTests(RetryWorkerCase):
    """Direct MP4 links: BiliFlow probes and fetches them through SafeHttp (the fixture server)."""

    read_timeout: float | None = None

    def setUp(self):
        self.server = FixtureServer()
        super().setUp()

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            self.server.close()

    def worker_options(self):
        http = self.server.http(**({} if self.read_timeout is None else {"read_timeout": self.read_timeout}))
        return {"resolver": public_resolver, "http": http, "sources": self.registry(),
                "transfers": SourceTransfers(http, ffmpeg=FFMPEG, ffprobe=FFPROBE)}

    def registry(self):
        return SourceRegistry([DirectMediaProvider()])

    def test_a_dropped_probe_waits_without_a_slot_then_retries_on_schedule(self):
        self.server.route("/v/clip.mp4", first_answers(DROP, DROP))
        task, = self.add(URL)
        self.run_all(60)
        self.assert_waiting(task["id"], 1, 20)
        self.assertEqual(self.task(task["id"])["error_code"], "NETWORK")
        self.advance(19)
        self.run_all(60)
        self.assertEqual(self.task(task["id"])["state"], "WAITING_RETRY")  # not due yet
        self.advance(1)
        self.run_all(60)
        self.assert_waiting(task["id"], 2, 60)
        self.advance(60)
        self.run_all(60)
        self.assertEqual(self.task(task["id"])["state"], "COMPLETED", self.store.events(task["id"]))
        self.assertEqual((self.root / "input" / "clip.mp4").read_bytes(), MEDIA["clip"])
        kinds = self.kinds(task["id"])
        self.assertEqual((kinds.count("AUTO_RETRY_SCHEDULED"), kinds.count("AUTO_RETRY")), (2, 2))
        scheduled = [event for event in self.store.events(task["id"]) if event["kind"] == "AUTO_RETRY_SCHEDULED"]
        self.assertEqual(scheduled[0]["level"], "WARNING")
        self.assertIn("lần 1/3", scheduled[0]["message"])

    def test_a_waiting_task_leaves_its_slot_to_the_next_one(self):
        self.store.set_setting("slots", "1")
        self.server.route("/v/clip.mp4", first_answers(DROP))
        self.server.route("/v/other.mp4", Reply(MEDIA["clip"], content_type="video/mp4", etag='"v1"'))
        first, second = self.add(URL, "http://media.example/v/other.mp4")
        self.run_all(60)
        self.assert_waiting(first["id"], 1, 20)
        self.assertEqual(self.task(second["id"])["state"], "COMPLETED")

    def test_after_three_retries_the_probe_fails_with_a_note(self):
        self.server.route("/v/clip.mp4", first_answers(then=DROP))
        task, = self.add(URL)
        for count, seconds in ((1, 20), (2, 60), (3, 180)):
            self.run_all(60)
            self.assert_waiting(task["id"], count, seconds)
            self.advance(seconds)
        self.run_all(60)
        failed = self.task(task["id"])
        self.assertEqual((failed["state"], failed["error_code"]), ("FAILED", "NETWORK"))
        self.assertIn("Đã tự thử lại 3 lần", failed["error_message"])
        self.assertEqual(self.server.count("/v/clip.mp4"), 4)

    def test_lasting_errors_are_never_retried(self):
        cases = {"/v/gone.mp4": (Reply(b"gone", 404, "text/plain"), "UNAVAILABLE"),
                 "/v/login.mp4": (Reply(b"login", 401, "text/plain"), "LOGIN_REQUIRED"),
                 "/v/denied.mp4": (Reply(b"no", 403, "text/plain"), "FORBIDDEN"),
                 "/v/broken.mp4": (Reply(b"broken", 500, "text/plain"), "SERVER_BUSY")}
        for path, (reply, _) in cases.items():
            self.server.route(path, reply)
        tasks = self.add(*[f"http://media.example{path}" for path in cases])
        self.run_all(60)
        for task, (path, (_, code)) in zip(tasks, cases.items()):
            with self.subTest(path=path):
                ended = self.task(task["id"])
                self.assertEqual((ended["state"], ended["error_code"]), ("FAILED", code))
                self.assertNotIn("AUTO_RETRY_SCHEDULED", self.kinds(task["id"]))
                self.assertEqual(self.server.count(path), 1)

    def test_retry_after_sets_the_wait_and_a_too_long_one_ends_the_task(self):
        self.server.route("/v/clip.mp4", first_answers(
            Reply(b"busy", 429, "text/plain", headers={"Retry-After": "100"})))
        self.server.route("/v/later.mp4", Reply(b"later", 503, "text/plain", headers={"Retry-After": "3600"}))
        task, later = self.add(URL, "http://media.example/v/later.mp4")
        self.run_all(60)
        self.assert_waiting(task["id"], 1, 100)
        self.assertEqual(self.task(task["id"])["error_code"], "SERVER_BUSY")
        ended = self.task(later["id"])
        self.assertEqual((ended["state"], ended["error_code"]), ("FAILED", "SERVER_BUSY"))
        self.assertIn("hẹn thử lại sau khoảng 60 phút", ended["error_message"])
        self.advance(100)
        self.run_all(60)
        self.assertEqual(self.task(task["id"])["state"], "COMPLETED")

    def test_a_stalled_page_is_retried_like_a_dropped_one(self):
        self.read_timeout = 0.3
        self.worker.shutdown(0)
        self.worker = self.make_worker()
        self.server.route("/v/clip.mp4", first_answers(Reply(b"late", stall=1.0)))
        task, = self.add(URL)
        self.run_all(60)
        self.assert_waiting(task["id"], 1, 20)
        self.assertIn("quá thời gian chờ", self.task(task["id"])["error_message"])

    def test_a_cut_download_waits_and_continues_its_part(self):
        body, cut = MEDIA["clip"], {"done": False}

        def route(seen, number):  # the first whole-file request drops at half; the probe's samples are whole
            first = "range" not in seen.headers and not cut["done"]
            cut["done"] = cut["done"] or first
            return Reply(body, content_type="video/mp4", etag='"v1"', cut_after=len(body) // 2 if first else None)
        self.server.route("/v/clip.mp4", route)
        self.worker.transfers.files.retries = 0
        task, = self.add(URL)
        self.run_all(60)
        self.assert_waiting(task["id"], 1, 20, phase="DOWNLOADING")
        part = self.root / "temp" / "downloads" / str(task["id"]) / "media.part"
        self.assertEqual(part.stat().st_size, len(body) // 2)
        self.advance(20)
        self.run_all(60)
        self.assertEqual(self.task(task["id"])["state"], "COMPLETED", self.store.events(task["id"]))
        self.assertEqual((self.root / "input" / "clip.mp4").read_bytes(), body)
        self.assertEqual(self.server.seen("/v/clip.mp4")[-1].headers.get("range"), f"bytes={len(body) // 2}-")

    def test_stop_and_cancel_while_waiting(self):
        self.server.route("/v/clip.mp4", first_answers(DROP))
        self.server.route("/v/other.mp4", first_answers(DROP))
        stopped, cancelled = self.add(URL, "http://media.example/v/other.mp4")
        self.run_all(60)
        self.assert_waiting(stopped["id"], 1, 20)
        self.assert_waiting(cancelled["id"], 1, 20)
        moved = self.worker.stop(stopped["id"])
        self.assertEqual((moved["state"], moved["retry_at"]), ("STOPPED", None))
        self.assertEqual(self.worker.cancel(cancelled["id"])["state"], "CANCELLED")
        self.advance(30)
        self.run_all(60)
        self.assertEqual(self.task(stopped["id"])["state"], "STOPPED")  # a stop is never woken
        resumed = self.worker.resume(stopped["id"])
        self.assertEqual((resumed["state"], resumed["retry_count"]), ("QUEUED", None))
        self.run_all(60)
        self.assertEqual(self.task(stopped["id"])["state"], "COMPLETED")

    def test_a_restart_keeps_the_wait(self):
        self.server.route("/v/clip.mp4", first_answers(DROP))
        task, = self.add(URL)
        self.run_all(60)
        self.assert_waiting(task["id"], 1, 20)
        self.worker.shutdown(5)
        self.worker = self.make_worker()
        self.worker.recover()
        self.assert_waiting(task["id"], 1, 20)
        self.advance(20)
        self.run_all(60)
        self.assertEqual(self.task(task["id"])["state"], "COMPLETED", self.store.events(task["id"]))


class YtDlpRetryTests(RetryWorkerCase):
    def test_a_probe_timeout_waits_then_the_next_probe_reads_the_page(self):
        self.scenario(probe={"exit": 1, "stderr": "ERROR: Unable to download webpage: timed out"})
        task, = self.add()
        self.run_all()
        self.assert_waiting(task["id"], 1, 20)
        self.scenario(probe={"json": video("Phim")}, download={"id": "abc"})
        self.advance(20)
        self.run_all()
        self.assertEqual(self.task(task["id"])["state"], "COMPLETED", self.store.events(task["id"]))

    def test_a_missing_page_is_not_retried(self):
        self.scenario(probe={"exit": 1, "stderr": "ERROR: Unable to download webpage: HTTP Error 404: Not Found"})
        task, = self.add()
        self.run_all()
        ended = self.task(task["id"])
        self.assertEqual((ended["state"], ended["error_code"]), ("FAILED", "UNAVAILABLE"))
        self.assertEqual(len(self.calls("probe")), 1)

    def test_a_download_cut_by_the_network_continues_with_its_part(self):
        self.scenario(probe={"json": video("Phim")},
                      download={"id": "abc", "progress": [[16, 64, None, 1, 1]], "exit": 1,
                                "stderr": "ERROR: unable to download video data: <urlopen error timed out>"})
        task, = self.add()
        self.run_all()
        self.assert_waiting(task["id"], 1, 20, phase="DOWNLOADING")
        self.scenario(probe={"json": video("Phim")}, download={"id": "abc"})
        self.advance(20)
        self.run_all()
        self.assertEqual(self.task(task["id"])["state"], "COMPLETED", self.store.events(task["id"]))
        _first, second = self.calls("download")
        self.assertIn("--continue", second["argv"])
        self.assertEqual(len(self.calls("probe")), 1)  # the probe is not repeated

    def test_a_failed_name_lookup_before_the_download_is_retried(self):
        self.scenario(probe={"json": video("Phim")}, download={"id": "abc"})
        task, = self.add()

        def failing(host, port):
            raise OSError("no network")
        self.worker.resolver = failing
        self.run_all()
        waiting = self.task(task["id"])
        self.assertEqual((waiting["state"], waiting["error_code"]), ("WAITING_RETRY", "DNS_FAILED"))
        self.worker.resolver = lambda host, port: ["93.184.215.14"]
        self.advance(20)
        self.run_all()
        self.assertEqual(self.task(task["id"])["state"], "COMPLETED", self.store.events(task["id"]))

    def test_without_a_policy_nothing_waits(self):
        self.policy = None
        self.worker.shutdown(0)
        self.worker = self.make_worker()
        self.scenario(probe={"exit": 1, "stderr": "ERROR: Unable to download webpage: timed out"})
        task, = self.add()
        self.run_all()
        self.assertEqual((self.task(task["id"])["state"], self.task(task["id"])["error_code"]), ("FAILED", "NETWORK"))


class PacingTests(RetryWorkerCase):
    policy = NetworkPolicy(host_gap=0.3)

    def probe_starts(self):
        starts: dict[str, list[float]] = {}
        for call in self.calls("probe"):
            url = call["argv"][call["argv"].index("--") + 1]
            starts.setdefault(url.split("/")[2], []).append(call["at"])
        return {host: sorted(times) for host, times in starts.items()}

    def test_one_lookup_per_host_at_a_time_with_a_pause_while_other_hosts_go_ahead(self):
        self.store.set_setting("slots", "3")
        self.scenario(probe={"sleep": 0.4, "exit": 1, "stderr": "ERROR: Unsupported URL: x"})
        self.add("https://clips.example/v/1", "https://clips.example/v/2", "https://clips.example/v/3",
                 "https://other.example/v/1")
        self.run_all(30)
        starts = self.probe_starts()
        self.assertEqual(len(starts["clips.example"]), 3)
        for earlier, later in zip(starts["clips.example"], starts["clips.example"][1:]):
            self.assertGreaterEqual(later - earlier, 0.65)  # 0.4 s lookup + 0.3 s pause, never overlapping
        self.assertLess(starts["other.example"][0], starts["clips.example"][1])  # it did not wait behind them
        self.assertTrue(all(task["state"] == "FAILED" for task in self.store.list_tasks()))


class RecordingDirect(DirectMediaProvider):
    """The direct provider, recording when each lookup of a link (probe or fresh link) ran."""

    def __init__(self):
        super().__init__()
        self.runs: list[tuple[float, float]] = []
        self.lock = threading.Lock()

    def resolve(self, url, ctx):
        began = time.monotonic()
        try:
            return super().resolve(url, ctx)
        finally:
            with self.lock:
                self.runs.append((began, time.monotonic()))


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class FreshLinkPacingTests(RetryWorkerCase):
    policy = NetworkPolicy(host_gap=0.2)

    def setUp(self):
        self.server = FixtureServer()
        self.provider = RecordingDirect()
        super().setUp()

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            self.server.close()

    def worker_options(self):
        http = self.server.http()
        return {"resolver": public_resolver, "http": http, "sources": SourceRegistry([self.provider]),
                "transfers": SourceTransfers(http, ffmpeg=FFMPEG, ffprobe=FFPROBE)}

    def test_probes_and_fresh_links_of_one_host_never_overlap(self):
        self.store.set_setting("slots", "3")
        for name in ("a", "b", "c"):
            self.server.route(f"/v/{name}.mp4", Reply(MEDIA["clip"], content_type="video/mp4", etag='"v1"'))
        tasks = self.add(*[f"http://media.example/v/{name}.mp4" for name in ("a", "b", "c")])
        self.run_all(90)
        self.assertEqual([self.task(task["id"])["state"] for task in tasks], ["COMPLETED"] * 3)
        runs = sorted(self.provider.runs)
        self.assertEqual(len(runs), 6)  # each task: its probe and the fresh link before the transfer
        for (_, ended), (began, _) in zip(runs, runs[1:]):
            self.assertGreaterEqual(began - ended, 0.19)


class RetryNetworkActionTests(WorkerCase):
    """"Thử lại các lượt lỗi mạng": only tasks that ended on a passing network error go back to the queue."""

    def end(self, url, state, code):
        task, = self.add(url)
        return self.store.transition(task["id"], {"QUEUED"}, state, error_code=code, error_message="x")

    def test_failed_ones_start_again_and_interrupted_ones_continue(self):
        failed = self.end("https://clips.example/v/1", "FAILED", "NETWORK")
        dns = self.end("https://clips.example/v/2", "FAILED", "DNS_FAILED")
        busy = self.end("https://clips.example/v/3", "INTERRUPTED", "SERVER_BUSY")
        gone = self.end("https://clips.example/v/4", "FAILED", "UNAVAILABLE")
        restarted = self.end("https://clips.example/v/5", "INTERRUPTED", None)
        stopped = self.end("https://clips.example/v/6", "STOPPED", "NETWORK")
        self.assertEqual(self.worker.network_failures(), 3)
        result = self.worker.retry_network()
        self.assertEqual(result, {"retried": [failed["id"], dns["id"]], "resumed": [busy["id"]], "skipped": []})
        self.assertEqual([self.store.get(item["id"])["attempt"] for item in (failed, dns, busy)], [2, 2, 1])
        self.assertEqual([self.state(item["id"]) for item in (failed, dns, busy)], ["QUEUED"] * 3)
        self.assertEqual([self.state(item["id"]) for item in (gone, restarted, stopped)],
                         ["FAILED", "INTERRUPTED", "STOPPED"])
        self.assertEqual(self.worker.network_failures(), 0)

    def test_one_that_cannot_follow_is_skipped_and_named(self):
        failed = self.end("https://clips.example/v/1", "FAILED", "NETWORK")
        message = "Tập này đã có một lượt tải khác đang chờ hoặc đang tải."

        def refusing(task_id):
            raise DownloadActionError(message)
        self.worker.retry = refusing
        result = self.worker.retry_network()
        del self.worker.retry
        self.assertEqual(result, {"retried": [], "resumed": [], "skipped": [{"id": failed["id"], "reason": message}]})
        self.assertEqual(self.state(failed["id"]), "FAILED")


class ProductionWiringTest(unittest.TestCase):
    def test_the_control_center_worker_retries_and_paces(self):
        from biliflow.download_api import DownloadService

        root = temp_root("download-retry-service-")
        try:
            (root / "config").mkdir()
            service = DownloadService(root, cleanable=lambda: (0, 0), bin_reader=None)
            try:
                self.assertEqual(service.worker.network_policy, NetworkPolicy())
                self.assertEqual((NetworkPolicy().schedule, NetworkPolicy().host_gap), ((20.0, 60.0, 180.0), 5.0))
                snapshot = service.snapshot()
                self.assertEqual((snapshot["settings"]["auto_retries"], snapshot["network_failures"]), (3, 0))
            finally:
                service.worker.shutdown(0)
                service.store.close()
        finally:
            remove_tree(root)


if __name__ == "__main__":
    unittest.main()
