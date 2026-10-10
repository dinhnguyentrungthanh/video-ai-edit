"""A source account's ticket kept in memory for its task (Codex's TICKET-REUSE prompt; src/biliflow/
download_account_tickets.py and download_source_steps): the probe's ticket serves the first transfer; Dừng/Tiếp tục
checks the kept link with one bounded HTTP probe instead of a new ticket; a refused or changed link gets one new
ticket through the existing resolve; a network error stays INTERRUPTED; the cache never outlives its task, attempt,
choice, session or process. Also NO_INPUT_DIR keeping the finished file, and the validator kinds in the log.

The harness of tests/test_download_account_queue.py (real DownloadWorker, real AccountManager on a temporary root
under the install's temp/, fake vault, made-up film list, files from the fixture server on 127.0.0.1 through the
downloader's own cookie-free SafeHttp). ``TicketProvider`` mints a ticket per resolve: a canary token in the link,
so every request shows which ticket it used, and the canary must never reach the database, a file, a log, an event
or the API. ``RealProviderTest`` runs the real AccountSourceProvider instead (its resolve, lease check, probe and
issuer) with only the hidden browser run faked, which sends one real POST per ticket to a made-up endpoint of the
fixture server, so the POSTs are counted. No browser, no real site, no user state.
"""
from __future__ import annotations

import http.client
import json
import random
import threading
import unittest
from dataclasses import replace
from datetime import timedelta
from email.utils import formatdate
from pathlib import Path
from unittest import mock

from biliflow import download_account_sources
from biliflow.download_account_api import AccountRuntime
from biliflow.download_account_pages import Ticket
from biliflow.download_account_runs import SessionRun
from biliflow.download_account_sources import AccountSourceProvider, _Found
from biliflow.download_account_tickets import FRESH_SECONDS, IDLE_SECONDS, TicketCache
from biliflow.download_accounts import FALLBACK_TTL_SECONDS
from biliflow.download_api import DownloadService
from biliflow.download_media_file import PART_NAME
from biliflow.download_source_types import TicketIssuer
from biliflow.download_transfer import FINISHED_NAME
from tests import test_download_account_queue as queue
from tests.account_queue_fixtures import QueueProvider, connect, file_path, file_url, page_url
from tests.source_fixtures import Reply

CANARY = "BF-CANARY-reuse"
FILE = file_path("m1", "v1080")
PADDING = 3 * 1024 * 1024  # the part outgrows the 1 MiB comparison window of download_media_file
DIFFERENT_AT = 600_000  # a byte far from the part's last MiB


def setUpModule():
    queue.setUpModule()


def ticket_of(seen) -> str:
    return (seen.query.get("t") or [""])[0].split("-" + CANARY)[0]


class Mono:
    """A monotonic clock for the ticket cache that only moves when a test says so."""

    def __init__(self) -> None:
        self.now = 50_000.0

    def __call__(self) -> float:
        return self.now


class TicketProvider(QueueProvider):
    """QueueProvider whose every resolve mints a new ticket (``tok-N`` with the canary in the link) and names the
    session generation it was issued under, like the real provider's ``issuer``."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.minted = 0

    def link_for(self, episode: str, variant: str) -> str:
        with self._calls_lock:
            self.minted += 1
            number = self.minted
        return f"{file_url(self.id, episode, variant)}?t=tok-{number}-{CANARY}"

    def resolve(self, url, ctx):
        _usable, _reason, generation = self.manager.session_gate(self.id)  # the hidden run's lease comes first
        source = super().resolve(url, ctx)
        return replace(source, issuer=TicketIssuer(self.manager.owner, self.id, generation))


@unittest.skipUnless(queue.HAVE_FFMPEG, queue.NEED_FFMPEG)
class ReuseCase(queue.FileQueueCase):
    def setUp(self):
        super().setUp()
        self.alpha = TicketProvider(self.config.sources["alpha"], self.manager, self.make_listing())
        self.mono = Mono()
        self.worker = self.make_worker()  # the first one never started: no thread to stop
        self.body = queue.MEDIA["mkv"] + random.Random(7).randbytes(PADDING)
        self.etag: str | None = '"v1"'
        self.modified: str | None = None
        self.slow = False
        self.refuse: dict[str, int] = {}  # ticket -> status of every request with it
        self.cut_checks = 0  # the next checks of a kept link (1 MiB samples after the probe's) cut by the network
        self.on_sample = None  # called with the number of samples so far, before the sample is answered
        self.seen: list[tuple[str, str, str | None]] = []  # (ticket, "sample" or Range, If-Range)
        self.server.route(FILE, self.route)

    def make_worker(self, providers=None, **options):
        worker = super().make_worker(providers, **options)
        if hasattr(self, "mono"):
            worker.tickets = TicketCache(clock=self.mono)
        return worker

    def route(self, seen, number):
        ticket, wanted = ticket_of(seen), seen.headers.get("range", "")
        sample = bool(queue.SNIFF.fullmatch(wanted))
        self.seen.append((ticket, "sample" if sample else wanted, seen.headers.get("if-range")))
        if sample and self.on_sample is not None:
            self.on_sample(len(self.samples()))
        if ticket in self.refuse:
            return Reply(b"no", self.refuse[ticket], "text/plain")
        headers = {"Last-Modified": self.modified} if self.modified else {}
        if sample and self.cut_checks and len(self.samples()) > 1:
            self.cut_checks -= 1
            return Reply(self.body, content_type="video/x-matroska", etag=self.etag, headers=headers, cut_after=100)
        return Reply(self.body, content_type="video/x-matroska", etag=self.etag, headers=headers,
                     delay=0.004 if self.slow and not sample else 0.0, chunk=16 * 1024)

    # ------------------------------------------------------------------ helpers
    def samples(self):
        return [item for item in self.seen if item[1] == "sample"]

    def transfers_seen(self):
        return [item for item in self.seen if item[1] != "sample"]

    def part(self, task_id) -> Path:
        return self.root / "temp" / "downloads" / str(task_id) / PART_NAME

    def start_film(self):
        connect(self.manager)
        task, = self.add(page_url(film="m1"))
        return task["id"]

    def stop_mid_transfer(self, task_id, at=256 * 1024):
        """Dispatch the task with a slow file and press Dừng once ``at`` bytes are on disk."""
        self.slow = True
        self.worker.dispatch()
        part = self.part(task_id)
        self.assertTrue(queue.wait_until(lambda: part.is_file() and part.stat().st_size >= at),
                        self.store.events(task_id))
        self.worker.stop(task_id)
        self.assertTrue(self.worker.wait_idle(queue.WAIT))
        self.slow = False
        self.assertEqual(self.task(task_id)["state"], "STOPPED", self.store.events(task_id))
        return part.stat().st_size

    def resume(self, task_id):
        self.worker.resume(task_id)
        self.run_all(120)
        return self.task(task_id)

    def published(self) -> bytes:
        names = self.input_names()
        self.assertEqual(len(names), 1, names)
        return (self.root / "input" / names[0]).read_bytes()

    def log(self, task_id) -> list[str]:
        return self.store.log_lines(task_id)

    def event_texts(self, task_id) -> list[str]:
        return [event["message"] for event in self.store.events(task_id)]


class FirstTransferTest(ReuseCase):
    def test_the_probes_ticket_serves_the_first_transfer_one_ticket_in_all(self):
        task_id = self.start_film()
        self.run_all(120)
        self.assertEqual(self.task(task_id)["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual((len(self.alpha.calls), self.alpha.minted), (1, 1))  # one ticket for probe + transfer
        self.assertEqual(self.samples(), [("tok-1", "sample", None)])  # the probe's sample only, no check
        self.assertEqual({ticket for ticket, _, _ in self.transfers_seen()}, {"tok-1"})
        self.assertEqual(self.published(), self.body)
        texts = self.event_texts(task_id)
        self.assertTrue(any("Dùng link tải vừa lấy lúc thăm dò" in text for text in texts), texts)
        self.assertFalse(any("Lấy lại nguồn mới" in text for text in texts), texts)
        self.assertEqual(len(self.worker.tickets), 0)  # the task ended: its link is gone

    def test_a_probe_waiting_for_space_past_the_idle_limit_gets_a_new_ticket(self):
        self.space = (0, 100 * queue.GB)  # nothing free: the probed task waits for space
        task_id = self.start_film()
        self.worker.dispatch()
        self.assertTrue(queue.wait_until(lambda: self.task(task_id)["state"] == "WAITING_SPACE"))
        self.mono.now += IDLE_SECONDS  # the kept link has been idle since the probe ended
        self.space = (10_000 * queue.GB, 100 * queue.GB)
        self.assertTrue(self.worker.wait_idle(queue.WAIT))
        self.run_all(120)
        self.assertEqual(self.task(task_id)["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)
        self.assertEqual({ticket for ticket, _, _ in self.transfers_seen()}, {"tok-2"})


class WaitedProbeTest(ReuseCase):
    def test_a_probe_that_waited_for_space_checks_its_link_before_the_first_transfer(self):
        self.space = (0, 100 * queue.GB)
        task_id = self.start_film()
        self.worker.dispatch()
        self.assertTrue(queue.wait_until(lambda: self.task(task_id)["state"] == "WAITING_SPACE"))
        self.mono.now += FRESH_SECONDS  # within the idle limit, but no longer "just probed"
        self.space = (10_000 * queue.GB, 100 * queue.GB)
        self.assertTrue(self.worker.wait_idle(queue.WAIT))
        self.run_all(120)
        self.assertEqual(self.task(task_id)["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 1)  # still no second ticket
        self.assertEqual(self.samples(), [("tok-1", "sample", None)] * 2)  # the probe, then one check
        self.assertEqual(self.published(), self.body)


class StopResumeTest(ReuseCase):
    def test_tiep_tuc_checks_the_kept_link_once_and_continues_the_part_without_a_ticket(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual((len(self.alpha.calls), self.alpha.minted), (1, 1))  # no ticket after the probe
        self.assertEqual(self.samples(), [("tok-1", "sample", None)] * 2)  # the probe, then one check
        self.assertEqual(self.transfers_seen()[-1], ("tok-1", f"bytes={kept}-", '"v1"'))  # Range + If-Range
        self.assertEqual(self.published(), self.body)
        texts = self.event_texts(task_id)
        self.assertTrue(any("Kiểm lại link tải còn giữ" in text for text in texts), texts)
        self.assertFalse(any("Lấy lại nguồn mới" in text for text in texts), texts)
        self.assertIn("Kiểm lại link đã giữ: ETag mạnh, không có Last-Modified, dấu dùng được: ETag.",
                      self.log(task_id))

    def test_a_kept_link_idle_for_ten_minutes_is_a_miss_and_the_part_continues_with_a_new_ticket(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        self.mono.now += IDLE_SECONDS
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)
        self.assertEqual(self.samples(), [("tok-1", "sample", None), ("tok-2", "sample", None)])
        self.assertEqual(self.transfers_seen()[-1], ("tok-2", f"bytes={kept}-", '"v1"'))
        self.assertEqual(self.published(), self.body)

    def test_a_restart_forgets_every_link_and_the_part_continues_with_a_new_ticket(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        self.restart()
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)
        self.assertEqual(self.transfers_seen()[-1], ("tok-2", f"bytes={kept}-", '"v1"'))
        self.assertEqual(self.published(), self.body)

    def test_another_validator_on_the_kept_link_compares_the_whole_part_before_appending(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id, at=1536 * 1024)
        self.etag = '"v2"'  # the same link now shows another mark (a node with its own ETag)
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 1)
        self.assertEqual(self.transfers_seen()[-1], ("tok-1", "bytes=0-", None))  # compared from byte 0
        self.assertGreater(kept, 1024 * 1024)
        self.assertEqual(self.published(), self.body)
        self.assertTrue(any("Đã so xong" in line for line in self.log(task_id)), self.log(task_id))

    def test_a_kept_link_that_now_serves_other_bytes_is_never_mixed_with_the_part(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id, at=2 * 1024 * 1024)
        self.assertGreater(kept - DIFFERENT_AT, 1024 * 1024)  # the difference lies outside the part's last MiB
        changed = bytearray(self.body)
        changed[DIFFERENT_AT] ^= 0xFF
        self.body, self.etag = bytes(changed), '"v2"'
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.published(), self.body)  # the new version whole, never the old head
        self.assertEqual(self.alpha.minted, 1)
        self.assertTrue(any("Link mới cho nội dung khác phần đã tải" in line for line in self.log(task_id)))

    def test_a_kept_link_with_only_a_last_modified_continues_the_part_by_that_date(self):
        self.etag, self.modified = None, formatdate(usegmt=True)
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 1)
        self.assertEqual(self.transfers_seen()[-1], ("tok-1", f"bytes={kept}-", self.modified))
        self.assertEqual(self.published(), self.body)

    def test_a_kept_link_without_a_validator_starts_again_under_the_strict_rule(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.etag = None
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.transfers_seen()[-1], ("tok-1", "", None))  # the whole file again, from byte 0
        self.assertEqual(self.published(), self.body)
        self.assertEqual(self.alpha.minted, 1)


class FallbackTest(ReuseCase):
    def refused_kept_link(self, status):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        self.refuse["tok-1"] = status
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual((len(self.alpha.calls), self.alpha.minted), (2, 2))  # exactly one new ticket
        self.assertEqual(self.transfers_seen()[-1], ("tok-2", f"bytes={kept}-", '"v1"'))
        self.assertNotIn("WAITING_LOGIN", self.events(task_id))
        self.assertEqual(self.published(), self.body)
        self.assertTrue(any("Link tải đã giữ không còn dùng được" in line for line in self.log(task_id)))

    def test_a_kept_link_refused_with_403_gets_exactly_one_new_ticket_and_no_sign_in(self):
        self.refused_kept_link(403)

    def test_a_kept_link_refused_with_401_gets_exactly_one_new_ticket_and_no_sign_in(self):
        self.refused_kept_link(401)

    def test_a_kept_link_gone_with_404_gets_exactly_one_new_ticket(self):
        self.refused_kept_link(404)

    def test_a_network_error_while_checking_the_kept_link_is_interrupted_without_a_ticket_or_a_sign_in(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.cut_checks = 1
        cut = self.resume(task_id)
        self.assertEqual((cut["state"], cut["error_code"]), ("INTERRUPTED", "NETWORK"), self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 1)
        self.assertNotIn("WAITING_LOGIN", self.events(task_id))
        done = self.resume(task_id)  # the link is still kept: checked again, no ticket
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 1)
        self.assertEqual(self.published(), self.body)

    def test_a_kept_link_answered_with_503_is_dropped_and_the_next_tiep_tuc_takes_one_new_ticket(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        self.refuse["tok-1"] = 503  # the file server answers this ticket, busy: never pinned to it
        busy = self.resume(task_id)
        self.assertEqual((busy["state"], busy["error_code"]), ("INTERRUPTED", "SERVER_BUSY"), self.store.events(task_id))
        self.assertEqual((self.alpha.minted, len(self.worker.tickets)), (1, 0))
        self.assertNotIn("WAITING_LOGIN", self.events(task_id))
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)
        self.assertEqual(self.transfers_seen()[-1], ("tok-2", f"bytes={kept}-", '"v1"'))
        self.assertEqual(self.published(), self.body)

    def test_the_newest_link_after_a_refresh_is_the_one_kept_for_tiep_tuc(self):
        task_id = self.start_film()
        refused = threading.Event()

        def refuse_first_transfer(seen, number):
            if ticket_of(seen) == "tok-1" and not queue.SNIFF.fullmatch(seen.headers.get("range", "")) \
                    and not refused.is_set():
                refused.set()
                self.seen.append(("tok-1", seen.headers.get("range", ""), seen.headers.get("if-range")))
                return Reply(b"no", 403, "text/plain")
            return self.route(seen, number)
        self.server.route(FILE, refuse_first_transfer)
        self.stop_mid_transfer(task_id)  # refused at once, refreshed (tok-2), then stopped mid-way
        self.assertEqual(self.alpha.minted, 2)
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)
        self.assertEqual([ticket for ticket, _kind, _ in self.samples()], ["tok-1", "tok-2", "tok-2"])
        self.assertEqual({ticket for ticket, _, _ in self.transfers_seen()[1:]}, {"tok-2"})
        self.assertEqual(self.published(), self.body)


class LifecycleTest(ReuseCase):
    def test_thu_lai_never_takes_the_old_attempts_link(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.worker.retry(task_id)
        self.assertEqual(len(self.worker.tickets), 0)
        self.run_all(120)
        self.assertEqual(self.task(task_id)["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)  # attempt 2 probes again and uses its own ticket
        after = self.seen[[item[0] for item in self.seen].index("tok-2"):]
        self.assertEqual({ticket for ticket, _, _ in after}, {"tok-2"})

    def test_huy_drops_the_link(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.assertEqual(len(self.worker.tickets), 1)
        self.worker.cancel(task_id)
        self.assertEqual(len(self.worker.tickets), 0)

    def test_xoa_drops_the_link(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.worker.remove(task_id)
        self.assertEqual(len(self.worker.tickets), 0)

    def test_a_disconnect_drops_the_link_at_once_and_the_gate_still_waits_for_a_sign_in(self):
        runtime = AccountRuntime(self.root, manager=self.manager)
        self.closing.append(lambda: runtime.stop(5))
        runtime.attach(wake=self.worker.wake_logins, registry=self.worker.sources, waiting=lambda: {},
                       forget=self.worker.forget_source_tickets)
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.assertEqual(runtime.handle_post("alpha", "disconnect")[0], 200)
        self.assertEqual(len(self.worker.tickets), 0)  # dropped at once by the hook
        self.worker.resume(task_id)
        self.run_all()
        self.assertEqual(self.task(task_id)["state"], "WAITING_LOGIN")  # the cache never passes the gate
        connect(self.manager, value="v2")
        self.worker.wake_logins()
        self.run_all(120)
        self.assertEqual(self.task(task_id)["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)  # the new session's ticket
        self.assertEqual(self.published(), self.body)

    def test_a_link_of_an_older_session_is_never_taken_even_without_the_hook(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        connect(self.manager, value="v2")  # a new sign-in landed; nobody told the worker
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)
        self.assertEqual([ticket for ticket, _, _ in self.samples()], ["tok-1", "tok-2"])  # tok-1 never checked

    def test_an_expired_session_waits_for_a_sign_in_before_any_kept_link_is_used(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.clock.at(FALLBACK_TTL_SECONDS + 1)
        self.worker.resume(task_id)
        self.run_all()
        waiting = self.task(task_id)
        self.assertEqual((waiting["state"], waiting["login_reason"]), ("WAITING_LOGIN", "SESSION_EXPIRED"))
        self.assertEqual(len(self.worker.tickets), 0)
        self.assertEqual(len(self.samples()), 1)  # nothing asked of the file server

    def test_links_that_can_no_longer_be_taken_leave_memory_at_the_next_dispatch_pass(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        self.worker.dispatch()
        self.assertEqual(len(self.worker.tickets), 1)  # stopped a moment ago, its session usable: kept
        self.clock.at(FALLBACK_TTL_SECONDS + 1)  # the session expired: nobody told the cache
        self.worker.dispatch()
        self.assertEqual(len(self.worker.tickets), 0)
        connect(self.manager, value="v2")  # signed in again: the next run keeps the new session's link
        self.worker.resume(task_id)
        self.stop_mid_transfer(task_id, at=kept + 256 * 1024)
        self.assertEqual((self.alpha.minted, len(self.worker.tickets)), (2, 1))
        self.mono.now += IDLE_SECONDS  # then idle past the limit
        self.worker.dispatch()
        self.assertEqual(len(self.worker.tickets), 0)

    def test_a_cancel_between_the_probes_result_and_its_keeping_refuses_the_late_link(self):
        task_id = self.start_film()
        put, results = self.worker.tickets.put, []

        def cancel_first(*args, **kwargs):  # Hủy lands after the resolve returned, before its link is kept
            self.worker.cancel(task_id, wait=False)
            results.append(put(*args, **kwargs))
            return results[-1]
        with mock.patch.object(self.worker.tickets, "put", side_effect=cancel_first):
            self.run_all(120)
        self.assertEqual(results, [False])  # refused by the cancel's revocation, not only dropped later
        self.assertEqual(self.task(task_id)["state"], "CANCELLED")
        self.assertEqual(len(self.worker.tickets), 0)

    def test_don_file_tam_drops_the_link(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.assertEqual(self.worker.cleanup_temp([task_id])["tasks"], 1)
        self.assertEqual((self.task(task_id)["state"], len(self.worker.tickets)), ("EXPIRED", 0))

    def test_the_seven_day_sweep_drops_the_link(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.worker.sweep(now=self.store.clock() + timedelta(days=8))
        self.assertEqual((self.task(task_id)["state"], len(self.worker.tickets)), ("EXPIRED", 0))

    def test_a_cancel_during_the_probe_never_keeps_its_late_link(self):
        task_id = self.start_film()
        entered, release = threading.Event(), self.hold(threading.Event())

        def block(ctx):
            entered.set()
            release.wait(30)
        self.alpha.before = block
        self.worker.dispatch()
        self.assertTrue(entered.wait(30))
        self.worker.cancel(task_id, wait=False)
        release.set()
        self.assertTrue(self.worker.wait_idle(queue.WAIT))
        self.assertEqual(self.task(task_id)["state"], "CANCELLED")
        self.assertEqual(len(self.worker.tickets), 0)

    def test_a_cancel_during_a_refresh_never_keeps_the_refreshed_link(self):
        task_id = self.start_film()
        entered, release = threading.Event(), self.hold(threading.Event())

        def block_refresh(ctx):
            if ctx.previous and ctx.previous.get("kind") == "account-file":  # the refresh, not the probe
                entered.set()
                release.wait(30)

        def refuse_transfers(seen, number):
            if ticket_of(seen) == "tok-1" and not queue.SNIFF.fullmatch(seen.headers.get("range", "")):
                return Reply(b"no", 403, "text/plain")
            return self.route(seen, number)
        self.alpha.before = block_refresh
        self.server.route(FILE, refuse_transfers)
        self.worker.dispatch()
        self.assertTrue(entered.wait(30))
        self.worker.cancel(task_id, wait=False)
        release.set()
        self.assertTrue(self.worker.wait_idle(queue.WAIT))
        self.assertEqual(self.task(task_id)["state"], "CANCELLED")
        self.assertEqual(len(self.worker.tickets), 0)


class SessionRaceTest(ReuseCase):
    """A session change (a new sign-in, Ngắt kết nối) racing a resolve, a check or a refresh."""

    def sign_in_again(self, *, hook=True):
        connect(self.manager, value="v2")
        if hook:  # what AccountRuntime does after a sign-in that connected
            self.worker.forget_source_tickets(self.manager.owner, "alpha")

    def test_a_new_sign_in_during_the_probe_never_keeps_its_late_link(self):
        self.on_sample = lambda count: self.sign_in_again() if count == 1 else None
        self.space = (0, 100 * queue.GB)  # the probed task waits for space: its link would wait in the cache
        task_id = self.start_film()
        self.worker.dispatch()
        self.assertTrue(queue.wait_until(lambda: self.task(task_id)["state"] == "WAITING_SPACE"))
        self.assertEqual(len(self.worker.tickets), 0)
        self.space = (10_000 * queue.GB, 100 * queue.GB)
        self.assertTrue(self.worker.wait_idle(queue.WAIT))
        self.run_all(120)
        self.assertEqual(self.task(task_id)["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)  # the new session's ticket
        self.assertEqual({ticket for ticket, _, _ in self.transfers_seen()}, {"tok-2"})

    def test_a_new_sign_in_during_the_probe_is_refused_at_use_even_without_the_hook(self):
        self.on_sample = lambda count: self.sign_in_again(hook=False) if count == 1 else None
        task_id = self.start_film()
        self.run_all(120)
        self.assertEqual(self.task(task_id)["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)
        self.assertEqual({ticket for ticket, _, _ in self.transfers_seen()}, {"tok-2"})

    def test_ngat_ket_noi_while_the_kept_link_is_checked_never_starts_its_transfer(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        before = len(self.transfers_seen())

        def disconnect(count):
            if count == 2:  # the check of the kept link is on its way
                self.manager.disconnect("alpha")
                self.worker.forget_source_tickets(self.manager.owner, "alpha")
        self.on_sample = disconnect
        waiting = self.resume(task_id)
        self.assertEqual(waiting["state"], "WAITING_LOGIN", self.store.events(task_id))
        self.assertEqual(len(self.transfers_seen()), before)  # nothing transferred with the old session's link
        self.assertEqual((self.alpha.minted, len(self.worker.tickets)), (1, 0))

    def test_a_new_sign_in_while_the_kept_link_is_checked_takes_the_new_sessions_ticket(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        self.on_sample = lambda count: self.sign_in_again(hook=False) if count == 2 else None
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)
        self.assertEqual([ticket for ticket, _, _ in self.samples()], ["tok-1", "tok-1", "tok-2"])
        self.assertEqual(self.transfers_seen()[-1], ("tok-2", f"bytes={kept}-", '"v1"'))
        self.assertEqual(self.published(), self.body)

    def disconnect_at(self, sample):
        def disconnect(count):
            if count == sample:
                self.manager.disconnect("alpha")
                self.worker.forget_source_tickets(self.manager.owner, "alpha")
        return disconnect

    def test_ngat_ket_noi_while_a_new_link_is_fetched_never_starts_its_transfer(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.restart()  # no link kept: Tiếp tục fetches a new one
        before = len(self.transfers_seen())
        self.on_sample = self.disconnect_at(2)  # the probe of the new link (tok-2) is on its way
        waiting = self.resume(task_id)
        self.assertEqual(waiting["state"], "WAITING_LOGIN", self.store.events(task_id))
        self.assertEqual(len(self.transfers_seen()), before)  # tok-2 never reached a transfer
        self.assertEqual((self.alpha.minted, len(self.worker.tickets)), (2, 0))

    def test_a_new_sign_in_while_a_new_link_is_fetched_takes_the_new_sessions_ticket(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        self.restart()
        self.on_sample = lambda count: self.sign_in_again() if count == 2 else None
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 3)  # tok-2 of the session before is fetched again
        self.assertEqual(self.transfers_seen()[-1], ("tok-3", f"bytes={kept}-", '"v1"'))
        self.assertNotIn("tok-2", {ticket for ticket, kind, _ in self.seen if kind != "sample"})
        self.assertEqual(self.published(), self.body)

    def test_ngat_ket_noi_during_a_refresh_waits_for_a_sign_in_with_the_part_and_no_stale_link(self):
        cut = threading.Event()

        def cut_then_refuse(seen, number):
            if ticket_of(seen) == "tok-1" and not queue.SNIFF.fullmatch(seen.headers.get("range", "")) \
                    and not cut.is_set():
                cut.set()
                self.seen.append(("tok-1", seen.headers.get("range", ""), seen.headers.get("if-range")))
                self.refuse["tok-1"] = 403  # the retry is refused: the run refreshes the ticket
                return Reply(self.body, content_type="video/x-matroska", etag=self.etag, cut_after=512 * 1024)
            return self.route(seen, number)
        self.server.route(FILE, cut_then_refuse)
        self.on_sample = self.disconnect_at(2)  # Ngắt kết nối while the refreshed link (tok-2) is probed
        task_id = self.start_film()
        self.run_all(120)
        waiting = self.task(task_id)
        self.assertEqual(waiting["state"], "WAITING_LOGIN", self.store.events(task_id))
        self.assertGreaterEqual(self.part(task_id).stat().st_size, 512 * 1024)  # the part waits with the task
        self.assertNotIn("tok-2", {ticket for ticket, kind, _ in self.seen if kind != "sample"})
        self.assertEqual(len(self.worker.tickets), 0)

    def test_a_refresh_after_a_new_sign_in_mid_transfer_is_the_link_tiep_tuc_uses(self):
        signed = threading.Event()

        def sign_in_then_cut(seen, number):
            if ticket_of(seen) == "tok-1" and not queue.SNIFF.fullmatch(seen.headers.get("range", "")) \
                    and not signed.is_set():
                signed.set()
                self.seen.append(("tok-1", seen.headers.get("range", ""), seen.headers.get("if-range")))
                self.sign_in_again()  # drops the link this transfer uses; the transfer itself goes on
                self.refuse["tok-1"] = 403  # its next request is refused: the run refreshes with the new session
                return Reply(self.body, content_type="video/x-matroska", etag=self.etag, cut_after=512 * 1024)
            return self.route(seen, number)
        self.server.route(FILE, sign_in_then_cut)
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id, at=1024 * 1024)
        self.assertEqual((self.alpha.minted, len(self.worker.tickets)), (2, 1))  # the refreshed link is kept
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, 2)
        self.assertEqual([ticket for ticket, _, _ in self.samples()], ["tok-1", "tok-2", "tok-2"])
        self.assertEqual(self.transfers_seen()[-1], ("tok-2", f"bytes={kept}-", '"v1"'))
        self.assertEqual(self.published(), self.body)


class SecretTest(ReuseCase):
    def test_no_ticket_reaches_the_database_a_file_a_log_an_event_or_the_api(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        self.resume(task_id)
        service = DownloadService(self.root, cleanable=lambda: (0, 0), bin_reader=None, store=self.store,
                                  worker=self.worker)
        shown = json.dumps([service.snapshot(), service.task_detail(task_id), self.store.list_tasks(),
                            self.store.events(task_id), self.log(task_id), repr(self.worker.tickets)],
                           ensure_ascii=False, default=str)
        self.assertNotIn(CANARY, shown)
        self.assertNotIn("tok-1", shown)
        self.assertNotIn('"v1"', "\n".join(self.log(task_id)))  # validator kinds only, never a value
        for path in self.root.rglob("*"):
            if path.is_file() and path.parent.name != "input":
                self.assertNotIn(CANARY.encode(), path.read_bytes(), path)


class NoInputTest(ReuseCase):
    def held_without_input(self):
        (self.root / "input").rmdir()
        task_id = self.start_film()
        self.run_all(120)
        held = self.task(task_id)
        self.assertEqual((held["state"], held["error_code"]), ("INTERRUPTED", "NO_INPUT_DIR"),
                         self.store.events(task_id))
        self.assertEqual(len(self.worker.tickets), 0)  # the file is finished: its link has no use any more
        return task_id

    def test_a_missing_input_folder_keeps_the_checked_file_and_tiep_tuc_publishes_it_without_any_request(self):
        task_id = self.held_without_input()
        held = self.task(task_id)
        self.assertIn("tạo lại thư mục input", held["error_message"])
        self.assertFalse((self.root / "input").exists())  # never made by BiliFlow
        self.assertTrue((self.root / "temp" / "downloads" / str(task_id) / FINISHED_NAME).is_file())
        (self.root / "input").mkdir()
        self.clock.at(FALLBACK_TTL_SECONDS + 1)  # the session expired meanwhile: no sign-in is needed
        self.space = (0, 100 * queue.GB)  # and no space for a new download either
        requests, tickets = len(self.server.requests), self.alpha.minted
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual((len(self.server.requests), self.alpha.minted), (requests, tickets))  # no media, no ticket
        self.assertEqual(self.published(), self.body)
        self.assertNotIn("WAITING_LOGIN", self.events(task_id))

    def test_tiep_tuc_while_input_is_still_missing_says_so_again_without_checking_the_file(self):
        task_id = self.held_without_input()
        checks = []
        verify = self.worker.verifier

        def counted(*args, **kwargs):
            checks.append(1)
            return verify(*args, **kwargs)
        self.worker.verifier = counted
        again = self.resume(task_id)
        self.assertEqual((again["state"], again["error_code"]), ("INTERRUPTED", "NO_INPUT_DIR"))
        self.assertEqual(checks, [])  # a multi-GB file is not decoded and hashed again for nothing
        (self.root / "input").mkdir()
        self.assertEqual(self.resume(task_id)["state"], "COMPLETED")
        self.assertEqual(checks, [1])
        self.assertEqual(self.published(), self.body)

    def test_a_finished_file_is_moved_even_when_its_source_left_the_config(self):
        task_id = self.held_without_input()
        (self.root / "input").mkdir()
        self.restart(providers=[self.beta])  # alpha left the account config meanwhile
        requests = len(self.server.requests)
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(len(self.server.requests), requests)
        self.assertEqual(self.published(), self.body)

    def test_an_input_folder_removed_during_the_move_keeps_the_file_too(self):
        task_id = self.start_film()

        def vanish(source, target, control):
            (self.root / "input").rmdir()
            raise FileNotFoundError(2, "No such folder", str(target))
        with mock.patch.object(self.worker, "_rename", side_effect=vanish):
            self.run_all(120)
        held = self.task(task_id)
        self.assertEqual((held["state"], held["error_code"]), ("INTERRUPTED", "NO_INPUT_DIR"), self.store.events(task_id))
        self.assertTrue((self.root / "temp" / "downloads" / str(task_id) / FINISHED_NAME).is_file())
        (self.root / "input").mkdir()
        tickets = self.alpha.minted
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(self.alpha.minted, tickets)
        self.assertEqual(self.published(), self.body)

    def test_a_finished_file_replaced_while_it_waited_is_never_published(self):
        task_id = self.held_without_input()
        task_dir = self.root / "temp" / "downloads" / str(task_id)
        finished = task_dir / json.loads((task_dir / FINISHED_NAME).read_text(encoding="utf-8"))["name"]
        changed = bytearray(finished.read_bytes())
        changed[DIFFERENT_AT] ^= 0xFF  # same size: only the recorded checksum tells
        finished.write_bytes(bytes(changed))
        (self.root / "input").mkdir()
        done = self.resume(task_id)
        self.assertEqual((done["state"], done["error_code"]), ("FAILED", "FILE_CHANGED"), self.store.events(task_id))
        self.assertEqual(self.input_names(), [])


TICKETS = "/tickets/release"


class RealProviderTest(ReuseCase):
    """The real AccountSourceProvider (resolve, the session's lease check, the probe and its issuer); only the
    hidden browser run is faked: it takes the session lease as the real run does (no browser) and, when the run may
    get a ticket, sends one real POST to a made-up ticket endpoint of the fixture server, which counts them."""

    def setUp(self):
        super().setUp()
        self.posts: list[bytes] = []
        self.server.route(TICKETS, self.release)
        self.real = AccountSourceProvider(self.config.sources["alpha"], self.manager, reader=object())
        patcher = mock.patch.object(download_account_sources, "run_with_session", self.hidden_run)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.worker = self.make_worker([self.real, self.beta])  # the one before never started

    def release(self, seen, number):
        if seen.method != "POST":
            return Reply(b"no", 405, "text/plain")
        with self.server._lock:
            self.posts.append(seen.body)
            count = len(self.posts)
        return Reply(f"tok-{count}-{CANARY}".encode(), content_type="text/plain")

    def hidden_run(self, manager, source_id, action, *, control=None, page_script=False, **options):
        lease = manager.session_for(source_id)  # LoginRequired without a usable session, as the real run
        listing = self.alpha.listing
        episode = listing.episodes[0]
        variant = episode.variants[0]
        if not page_script:
            return SessionRun(_Found(listing), None, lease=lease)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        try:
            connection.request("POST", TICKETS, body=f"{episode.key}--{variant.id}".encode())
            token = connection.getresponse().read().decode()
        finally:
            connection.close()
        ticket = Ticket(f"{file_url('alpha', episode.key, variant.id)}?t={token}")
        return SessionRun(_Found(listing, listing.selection(episode, variant), episode, variant, ticket), None,
                          lease=lease)

    def test_the_probe_and_the_first_transfer_take_one_ticket_post(self):
        task_id = self.start_film()
        self.run_all(120)
        self.assertEqual(self.task(task_id)["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.samples(), [("tok-1", "sample", None)])
        self.assertEqual({ticket for ticket, _, _ in self.transfers_seen()}, {"tok-1"})
        self.assertEqual(self.published(), self.body)

    def test_dung_tiep_tuc_takes_no_ticket_post(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.samples(), [("tok-1", "sample", None)] * 2)
        self.assertEqual(self.transfers_seen()[-1], ("tok-1", f"bytes={kept}-", '"v1"'))
        self.assertEqual(self.published(), self.body)

    def test_a_refused_kept_link_takes_exactly_one_more_ticket_post(self):
        task_id = self.start_film()
        kept = self.stop_mid_transfer(task_id)
        self.refuse["tok-1"] = 403
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(self.transfers_seen()[-1], ("tok-2", f"bytes={kept}-", '"v1"'))

    def test_a_new_sign_in_before_tiep_tuc_takes_the_new_sessions_ticket(self):
        task_id = self.start_film()
        self.stop_mid_transfer(task_id)
        connect(self.manager, value="v2")
        done = self.resume(task_id)
        self.assertEqual(done["state"], "COMPLETED", self.store.events(task_id))
        self.assertEqual(len(self.posts), 2)
        self.assertEqual([ticket for ticket, _, _ in self.samples()], ["tok-1", "tok-2"])  # tok-1 never checked


class ValidatorLogTest(ReuseCase):
    def logged(self, etag, modified, words):
        self.etag, self.modified = etag, modified
        task_id = self.start_film()
        self.run_all(120)
        self.assertEqual(self.task(task_id)["state"], "COMPLETED", self.store.events(task_id))
        lines = self.log(task_id)
        self.assertIn(f"Thăm dò link tải: {words}.", lines)
        self.assertIn(f"Link của lượt này: {words}; chưa có phần đã tải.", lines)
        self.assertIn(f"Trả lời HTTP 200 (từ byte 0): {words}.", lines)
        text = "\n".join(lines)
        for value in filter(None, (etag, modified)):
            self.assertNotIn(value, text)

    def test_a_strong_etag_is_logged_as_its_kind(self):
        self.logged('"v1"', None, "ETag mạnh, không có Last-Modified, dấu dùng được: ETag")

    def test_a_last_modified_alone_is_logged_as_its_kind(self):
        self.logged(None, formatdate(usegmt=True), "không có ETag, có Last-Modified, dấu dùng được: Last-Modified")

    def test_a_weak_etag_alone_is_no_usable_mark(self):
        self.logged('W/"v1"', None, "ETag yếu, không có Last-Modified, dấu dùng được: không có")

    def test_an_answer_without_marks_says_so_for_that_answer(self):
        self.logged(None, None, "không có ETag, không có Last-Modified, dấu dùng được: không có")


if __name__ == "__main__":
    unittest.main()
