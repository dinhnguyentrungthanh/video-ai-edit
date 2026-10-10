"""The in-memory link cache of source accounts (src/biliflow/download_account_tickets.py; Codex's TICKET-REUSE
prompt): bindings, the monotonic idle limit, the entry limit, revocation races and what a repr shows. A fake clock
and made-up links only; nothing touches the disk or the network."""
from __future__ import annotations

import threading
import unittest

from biliflow.download_account_tickets import FRESH_SECONDS, IDLE_SECONDS, MAX_ENTRIES, TicketCache
from biliflow.download_media_file import FilePlan
from biliflow.download_source_types import ResolvedSource, TicketIssuer

OWNER = ("e:\\biliflow-test-root", "S-1-5-21-1-2-3-1001")
OTHER_OWNER = ("e:\\biliflow-test-root", "S-1-5-21-1-2-3-1002")
OTHER_ROOT = ("e:\\another-root", "S-1-5-21-1-2-3-1001")
CANARY = "BF-CANARY-ticket"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def source(n: int = 1, *, generation: int = 3, size: int = 1000, owner: tuple[str, str] = OWNER,
           source_id: str = "alpha", transport: str = "http_file", issuer: bool = True) -> ResolvedSource:
    identity = {"kind": "account-file", "source": source_id, "film": "m1", "episode": "m1", "variant": "v1080",
                "version": "", "size": size}
    return ResolvedSource(
        provider=source_id, transport=transport, media_url=f"https://files.alpha.example/f/{n}?t=tok-{n}-{CANARY}",
        identity=identity, title="Phim thử", label="Nguồn alpha · MKV", headers={"X-Ticket": f"{CANARY}-{n}"},
        estimated_bytes=size, plan=FilePlan("matroska", size, True, f'"etag-{n}"', strict_versions=True),
        issuer=TicketIssuer(owner, source_id, generation) if issuer else None)


def take(cache: TicketCache, task_id: int = 1, *, attempt: int = 1, identity: str | None = None,
         owner: tuple[str, str] = OWNER, source_id: str = "alpha", generation: int | None = 3):
    return cache.take(task_id, attempt=attempt, identity=identity or source().identity_key, owner=owner,
                      source_id=source_id, generation=generation)


class BindingTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cache = TicketCache(clock=self.clock)

    def test_the_probe_result_is_taken_fresh_once_then_needs_a_check(self):
        kept = source()
        self.assertTrue(self.cache.put(1, 1, kept, self.cache.begin(), fresh=True))
        first = take(self.cache)
        self.assertIs(first.source, kept)
        self.assertTrue(first.fresh)
        self.cache.idle(1)  # the run stopped
        again = take(self.cache)
        self.assertIs(again.source, kept)
        self.assertFalse(again.fresh)

    def test_every_binding_must_match_and_a_mismatch_drops_the_entry(self):
        cases = {
            "another task": dict(task_id=2),
            "another attempt (Thử lại)": dict(attempt=2),
            "another file (choice, version, size)": dict(identity=source(size=999).identity_key),
            "another Windows account": dict(owner=OTHER_OWNER),
            "another project root": dict(owner=OTHER_ROOT),
            "another source": dict(source_id="beta"),
            "a newer session (sign-in again)": dict(generation=4),
            "no usable session (disconnected, expired, rejected)": dict(generation=None),
        }
        for name, changed in cases.items():
            with self.subTest(name):
                cache = TicketCache(clock=self.clock)
                cache.put(1, 1, source(), cache.begin(), fresh=True)
                task_id = changed.pop("task_id", 1)
                self.assertIsNone(take(cache, task_id, **changed))
                if task_id == 1:
                    self.assertIsNone(take(cache), "a mismatch drops the entry")

    def test_only_a_source_accounts_file_with_an_issuer_is_kept(self):
        self.assertFalse(self.cache.put(1, 1, source(issuer=False), self.cache.begin(), fresh=True))
        self.assertFalse(self.cache.put(2, 1, source(transport="hls"), self.cache.begin(), fresh=True))
        self.assertEqual(len(self.cache), 0)

    def test_a_session_change_of_one_source_revokes_only_its_links(self):
        self.cache.put(1, 1, source(), self.cache.begin(), fresh=True)
        self.cache.put(2, 1, source(source_id="beta"), self.cache.begin(), fresh=True)
        self.cache.put(3, 1, source(owner=OTHER_OWNER), self.cache.begin(), fresh=True)
        self.assertEqual(self.cache.revoke_source(OWNER, "alpha"), 1)
        self.assertIsNone(take(self.cache, 1))
        self.assertIsNotNone(take(self.cache, 2, source_id="beta", identity=source(source_id="beta").identity_key))
        self.assertIsNotNone(take(self.cache, 3, owner=OTHER_OWNER))


class LimitTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cache = TicketCache(clock=self.clock)

    def test_defaults_are_100_entries_and_10_minutes(self):
        self.assertEqual((MAX_ENTRIES, IDLE_SECONDS, FRESH_SECONDS), (100, 600.0, 120.0))

    def test_the_probes_link_serves_unchecked_only_soon_after_the_probe(self):
        self.cache.put(1, 1, source(), self.cache.begin(), fresh=True)
        self.clock.now += FRESH_SECONDS  # the task waited for space meanwhile
        late = take(self.cache)
        self.assertEqual(late.source.media_url, source().media_url)  # still kept (idle limit not reached)
        self.assertFalse(late.fresh, "a link that waited is checked before use")

    def test_an_idle_entry_expires_on_the_monotonic_clock(self):
        self.cache.put(1, 1, source(), self.cache.begin(), fresh=True)  # the probe ended: idle from now
        self.clock.now += IDLE_SECONDS - 1
        self.assertIsNotNone(take(self.cache))
        self.cache.idle(1)  # stopped: idle again from now
        self.clock.now += IDLE_SECONDS
        self.assertIsNone(take(self.cache))
        self.assertEqual(len(self.cache), 0)

    def test_an_entry_in_use_never_expires_so_a_long_transfer_is_never_cut(self):
        self.cache.put(1, 1, source(), self.cache.begin(), fresh=False)  # a running transfer's own link
        self.clock.now += 10 * IDLE_SECONDS
        self.assertIsNotNone(take(self.cache))
        self.clock.now += 10 * IDLE_SECONDS  # taken again: still in use until its run ends
        self.assertIsNotNone(take(self.cache))
        self.cache.idle(1)
        self.clock.now += IDLE_SECONDS
        self.assertIsNone(take(self.cache))

    def test_expired_links_leave_memory_at_the_next_call_not_only_when_taken(self):
        self.cache.put(1, 1, source(1), self.cache.begin(), fresh=True)
        self.clock.now += IDLE_SECONDS
        self.cache.put(2, 1, source(2), self.cache.begin(), fresh=True)
        self.assertEqual(len(self.cache), 1)

    def test_the_101st_entry_evicts_the_oldest_idle_one_never_a_running_one(self):
        self.cache.put(0, 1, source(0), self.cache.begin(), fresh=False)  # in use since the start
        for task_id in range(1, MAX_ENTRIES):
            self.clock.now += 1
            self.cache.put(task_id, 1, source(task_id), self.cache.begin(), fresh=True)
        self.assertEqual(len(self.cache), MAX_ENTRIES)
        self.clock.now += 1
        self.cache.put(MAX_ENTRIES, 1, source(MAX_ENTRIES), self.cache.begin(), fresh=True)
        self.assertEqual(len(self.cache), MAX_ENTRIES)
        self.assertIsNotNone(take(self.cache, 0), "the running transfer's link stays")
        self.assertIsNone(take(self.cache, 1), "the oldest idle one went")
        self.assertIsNotNone(take(self.cache, 2))
        self.assertIsNotNone(take(self.cache, MAX_ENTRIES))

    def test_limits_must_be_positive(self):
        for options in ({"max_entries": 0}, {"idle_seconds": 0}, {"fresh_seconds": -1}, {"max_revoked": 0}):
            with self.subTest(options), self.assertRaises(ValueError):
                TicketCache(**options)


class RaceTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cache = TicketCache(clock=self.clock)

    def test_a_resolve_that_began_before_a_revoke_is_never_kept(self):
        token = self.cache.begin()  # a probe or a refresh starts
        self.cache.revoke(1)  # Hủy, Xóa, Thử lại, a choice or a sign-in wait meanwhile
        self.assertFalse(self.cache.put(1, 1, source(), token, fresh=True))
        self.assertIsNone(take(self.cache))
        self.assertTrue(self.cache.put(1, 1, source(), self.cache.begin(), fresh=True), "a later resolve is kept")

    def test_a_revoke_of_another_task_does_not_refuse_this_one(self):
        token = self.cache.begin()
        self.cache.revoke(2)
        self.assertTrue(self.cache.put(1, 1, source(), token, fresh=True))

    def test_a_late_result_never_replaces_a_newer_link(self):
        older = self.cache.begin()
        self.cache.revoke(9)  # moves the sequence on
        newer = self.cache.begin()
        self.assertTrue(self.cache.put(1, 1, source(2), newer, fresh=False))
        self.assertFalse(self.cache.put(1, 1, source(1), older, fresh=False))
        self.assertEqual(take(self.cache).source.media_url, source(2).media_url)

    def test_the_same_run_replaces_its_own_link_after_a_refresh(self):
        token = self.cache.begin()
        self.cache.put(1, 1, source(1), token, fresh=False)
        self.cache.discard(1)  # the file server refused it
        self.assertTrue(self.cache.put(1, 1, source(2), token, fresh=False))
        self.assertEqual(take(self.cache).source.media_url, source(2).media_url)

    def test_shutdown_clears_everything_and_refuses_every_resolve_still_running(self):
        token = self.cache.begin()
        self.cache.put(1, 1, source(1), token, fresh=True)
        self.cache.clear()
        self.assertEqual(len(self.cache), 0)
        self.assertFalse(self.cache.put(2, 1, source(2), token, fresh=True))
        self.assertTrue(self.cache.put(2, 1, source(2), self.cache.begin(), fresh=True))

    def test_forgotten_revocations_refuse_older_tokens_for_every_task(self):
        cache = TicketCache(clock=self.clock, max_revoked=2)
        token = cache.begin()
        for task_id in (5, 6, 7):  # the record of task 5 is pruned: its token can no longer be told apart
            cache.revoke(task_id)
        self.assertFalse(cache.put(5, 1, source(), token, fresh=True))
        self.assertFalse(cache.put(8, 1, source(), token, fresh=True), "a miss, never a wrong hit")
        self.assertTrue(cache.put(8, 1, source(), cache.begin(), fresh=True))

    def test_a_session_change_refuses_every_resolve_of_the_source_that_began_before_it(self):
        token = self.cache.begin()  # a probe of task 5 runs (no entry yet), and the run of task 1 holds a link
        self.cache.put(1, 1, source(1), token, fresh=False)
        self.assertEqual(self.cache.revoke_source(OWNER, "alpha"), 1)  # Ngắt kết nối, or a new sign-in
        self.assertFalse(self.cache.put(5, 1, source(5), token, fresh=True))
        self.assertTrue(self.cache.put(6, 1, source(6, source_id="beta"), token, fresh=True), "another source")
        self.assertTrue(self.cache.put(7, 1, source(7, owner=OTHER_OWNER), token, fresh=True), "another account")
        self.assertTrue(self.cache.put(5, 1, source(5), self.cache.begin(), fresh=True), "a later resolve")

    def test_a_refresh_that_began_after_a_new_sign_in_is_kept_by_the_same_run(self):
        token = self.cache.begin()  # the run starts with a link of generation 3
        self.cache.put(1, 1, source(1), token, fresh=False)
        self.cache.revoke_source(OWNER, "alpha")  # the user signs in again: generation 4
        began = self.cache.begin()  # the link is refused, the run refreshes it with the new session
        self.assertTrue(self.cache.put(1, 1, source(2, generation=4), token, fresh=False, began=began))
        self.assertEqual(take(self.cache, generation=4).source.media_url, source(2).media_url)
        self.cache.revoke(1)  # a revoke of the task still refuses the run's later results
        self.assertFalse(self.cache.put(1, 1, source(3, generation=4), token, fresh=False, began=self.cache.begin()))

    def test_forgotten_session_changes_refuse_older_tokens_for_every_source(self):
        cache = TicketCache(clock=self.clock, max_revoked=1)
        token = cache.begin()
        cache.revoke_source(OWNER, "alpha")
        cache.revoke_source(OWNER, "beta")  # the record of alpha is pruned: its tokens can no longer be told apart
        self.assertFalse(cache.put(1, 1, source(), token, fresh=True))
        self.assertFalse(cache.put(2, 1, source(source_id="gamma"), token, fresh=True), "a miss, never a wrong hit")

    def test_concurrent_revokes_and_puts_leave_no_entry_from_before_a_revoke(self):
        tokens = [self.cache.begin() for _ in range(50)]
        threads = [threading.Thread(target=self.cache.revoke, args=(1,)) for _ in range(10)]
        threads += [threading.Thread(target=self.cache.put, args=(1, 1, source(n), tokens[n]),
                                     kwargs={"fresh": True}) for n in range(50)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        # Every token is from before every revoke: a put before the first revoke is dropped by a later one, and
        # every put after it is refused.
        self.assertIsNone(take(self.cache))
        self.cache.revoke(1)
        self.assertFalse(any(self.cache.put(1, 1, source(n), tokens[n], fresh=True) for n in range(50)))


class SecretTest(unittest.TestCase):
    def test_no_repr_shows_a_link_a_header_or_the_owner(self):
        cache = TicketCache(clock=Clock())
        kept = source()
        cache.put(1, 1, kept, cache.begin(), fresh=True)
        reuse = take(cache)
        shown = " ".join([repr(cache), repr(reuse), repr(kept), repr(kept.issuer), str(kept.public())])
        for secret in (CANARY, "files.alpha.example", "X-Ticket", OWNER[0], OWNER[1], '"etag-1"'):
            self.assertNotIn(secret, shown)
        self.assertNotIn("issuer", kept.public())


if __name__ == "__main__":
    unittest.main()
