"""FileTransfer with ``strict_versions`` (the plan of an account source's file, M3), without a browser.

A ticket of an account source gives a new signed link for every resolve. Bytes already in the part are
continued only when the new link shows the same version of the file (the same validator, sent as If-Range);
otherwise the part is dropped and the file starts again from byte 0, before any request. A range answer whose
own version marks contradict the part (a server that ignores If-Range) is never appended either. These tests
use the self-made clips and the fixture server of tests/test_download_sources.py (``.example`` hosts, no
network). The plain (non-strict) path of the other providers keeps its own tests there; the regressions here
show where the two differ.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import re
import threading
import time
import unittest
from typing import Any, Callable, Iterator
from unittest import mock

from biliflow import download_media_file
from biliflow.download_http import Scope
from biliflow.download_media_file import (
    FILE_RETRIES,
    MAX_VERSION_RESTARTS,
    PART_NAME,
    STATE_NAME,
    FileTransfer,
    other_version,
)
from biliflow.download_runner import ProcessControl
from biliflow.download_source_types import ResolvedSource
from tests.source_fixtures import Reply, Seen
from tests.test_download_sources import (
    ETAG,
    MEDIA,
    MEDIA_PATH,
    MEDIA_URL,
    ServerCase,
    replies,
    setUpModule as sources_setup,
    tearDownModule as sources_teardown,
    wait_for,
)

NEW_ETAG = '"v2"'
OLD_DATE = "Tue, 06 Oct 2026 08:00:00 GMT"
NEW_DATE = "Wed, 07 Oct 2026 08:00:00 GMT"
_RANGE = re.compile(r"bytes=(\d+)-$")
WALL_SECONDS = 120.0  # a transfer still running after this is cancelled and fails its test, never hangs it
REQUEST_CAP = 40  # past this many requests a whole-file server answers 410: a looping run ends with another code


def setUpModule() -> None:  # noqa: N802 - unittest API
    sources_setup()


def tearDownModule() -> None:  # noqa: N802 - unittest API
    sources_teardown()


def strict(source: ResolvedSource, validator: str | None = ETAG, url: str | None = None) -> ResolvedSource:
    """``source`` as an account source gives it: strict plan, ``validator`` of its link, ``url``."""
    return dataclasses.replace(source, media_url=url or source.media_url,
                               plan=dataclasses.replace(source.plan, strict_versions=True, validator=validator))


def changed_body() -> bytes:
    """Another version of the file with the same size (bytes past the first quarter differ)."""
    body = MEDIA["big"]
    start = len(body) // 4
    return body[:start] + bytes(byte ^ 0x5A for byte in body[start:])


def ignoring_if_range(body: bytes) -> Callable[[Seen, int], Reply]:
    """A server that answers a range of ``body`` whatever If-Range says (it names the asked validator)."""
    return lambda seen, number: Reply(body, content_type="video/mp4", etag=seen.headers.get("if-range") or NEW_ETAG)


def answering(body: bytes, marks: dict[str, str], *, cut_after: int | None = None) -> Callable[[Seen, int], Reply]:
    """A server that answers a range of ``body`` with 206 whatever If-Range says, and a request without a
    range with the whole ``body`` (cut after ``cut_after`` bytes); both carry the version ``marks`` given
    (ETag, Last-Modified), so the answer itself tells which version it is."""
    def route(seen: Seen, number: int) -> Reply:
        wanted = _RANGE.match(seen.headers.get("range", ""))
        if wanted is None:
            return Reply(body, content_type="video/mp4", headers=dict(marks), ranges=False, cut_after=cut_after)
        start = int(wanted.group(1))
        return Reply(body[start:], 206, "video/mp4", ranges=False,
                     headers={**marks, "Content-Range": f"bytes {start}-{len(body) - 1}/{len(body)}"})
    return route


def in_turn(*routes: Callable[[Seen, int], Reply]) -> Callable[[Seen, int], Reply]:
    """One route per request, in order."""
    left: Iterator[Callable[[Seen, int], Reply]] = iter(routes)
    return lambda seen, number: next(left)(seen, number)


def asked(requests: list[Seen]) -> list[tuple[str | None, str | None]]:
    return [(item.headers.get("range"), item.headers.get("if-range")) for item in requests]


class VersionMarksTest(unittest.TestCase):
    def test_which_marks_of_a_range_answer_show_another_version(self):
        cases = [  # (the part's validator, ETag, Last-Modified of the answer, another version)
            ('"v1"', '"v1"', "", False), ('"v1"', '"v2"', "", True), ('"v1"', '"v1"', NEW_DATE, False),
            ('"v1"', 'W/"v1"', "", False),  # a weak tag that matches proves nothing, and is no conflict
            ('"v1"', 'W/"v2"', "", True),  # a weak tag that differs is another version
            ('"v1"', "", NEW_DATE, False), ('"v1"', "", "", False),  # no ETag: nothing to compare with
            (OLD_DATE, "", OLD_DATE, False), (OLD_DATE, "", NEW_DATE, True), (OLD_DATE, '"v2"', OLD_DATE, False),
            (OLD_DATE, '"v2"', "", False), (OLD_DATE, "", "", False),  # an ETag says nothing against a date
            ("plain-tag", "plain-tag", "", False), ("plain-tag", "other-tag", "", True),  # an unquoted strong tag
            # A Last-Modified that is not an RFC date, next to an ETag the probe did not take (weak, or none):
            # the same Last-Modified on the answer names the same version.
            ("2026-10-06T08:00:00Z", '"x"', "2026-10-06T08:00:00Z", False),
            ("2026-10-06T08:00:00Z", 'W/"x"', "2026-10-06T08:00:00Z", False),
        ]
        for validator, etag, modified, expected in cases:
            with self.subTest(validator=validator, etag=etag, modified=modified):
                self.assertIs(other_version(validator, etag, modified), expected)


class StrictVersionTest(ServerCase):
    def cut_run(self, task, *, strict_plan: bool = True, etag: str | None = ETAG,
                cut: int | None = None) -> tuple[ResolvedSource, int]:
        """Resolve, then a first transfer whose connection drops at ``cut`` (a third of the file; no retry)."""
        self.serve(etag=etag)
        source = self.resolve(MEDIA_URL, task)
        if strict_plan:
            source = strict(source, source.plan.validator)
        cut = cut or len(MEDIA["big"]) // 3
        self.serve(etag=etag, cut_after=cut)
        first = self.download(source, task, retries=0)
        self.assertEqual(first.outcome.code, "NETWORK")
        self.assertEqual((task / PART_NAME).stat().st_size, cut)
        return source, cut

    def transfer_requests(self, after: int) -> list[Seen]:
        return self.server.seen(MEDIA_PATH)[after:]

    def test_a_new_ticket_with_the_same_validator_continues_the_part(self):
        task = self.task()
        source, cut = self.cut_run(task)
        self.serve()
        before = self.server.count(MEDIA_PATH)

        run = self.download(strict(source, ETAG, MEDIA_URL + "?token=new"), task)

        self.assertTrue(run.outcome.ok, run.outcome)
        request = self.transfer_requests(before)[0]
        self.assertEqual((request.headers["range"], request.headers["if-range"]), (f"bytes={cut}-", ETAG))
        self.assertEqual(request.query["token"], ["new"])
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])

    def test_a_new_ticket_for_another_version_restarts_without_appending_any_byte_of_it(self):
        """The new link's server ignores If-Range: the plain path would append the new version's tail to the
        old head (test_the_plain_path_trusts_the_servers_range_answer). The strict one asks the link with
        another validator from byte 0 without If-Range; the part's bytes differ, so nothing of that answer is
        written and the new version is fetched whole."""
        body = changed_body()
        self.assertEqual(len(body), len(MEDIA["big"]))
        task = self.task()
        source, cut = self.cut_run(task)
        self.server.route(MEDIA_PATH, ignoring_if_range(body))
        before = self.server.count(MEDIA_PATH)

        run = self.download(strict(source, NEW_ETAG, MEDIA_URL + "?token=new"), task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [("bytes=0-", None), (None, None)])
        self.assertEqual(run.outcome.final_path.read_bytes(), body)  # the new version whole, never a mix
        self.assertTrue(any("Link mới cho nội dung khác" in line for line in run.logs), run.logs)

    def test_a_new_ticket_with_another_etag_for_the_same_bytes_continues_the_part(self):
        """A host whose nodes give each copy of a file its own ETag (measured on the first real source): the new
        link's answer from byte 0 brings every byte of the part again, so the rest of that same answer is
        appended (one request, no If-Range) and the part's validator becomes the link's. The compared bytes
        are not progress: while they come the stage is "comparing" and the count stays at the part."""
        task = self.task()
        source, cut = self.cut_run(task)
        self.serve(etag=NEW_ETAG)
        before = self.server.count(MEDIA_PATH)

        run = self.download(strict(source, NEW_ETAG, MEDIA_URL + "?token=new"), task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [("bytes=0-", None)])
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])
        self.assertTrue(any("tải nối tiếp" in line for line in run.logs), run.logs)
        self.assertFalse(any("tải lại từ đầu" in line for line in run.logs), run.logs)
        comparing = [item for item in run.progress if item.stage == "comparing"]
        self.assertTrue(comparing, run.progress)
        self.assertEqual({(item.downloaded_bytes, item.speed) for item in comparing}, {(cut, None)})
        self.assertLessEqual(max(item.downloaded_bytes for item in run.progress), len(MEDIA["big"]))
        self.assertEqual((run.progress[-1].downloaded_bytes, run.progress[-1].stage),
                         (len(MEDIA["big"]), "downloading"))

    def test_a_link_whose_bytes_differ_only_just_before_the_resume_point_restarts(self):
        """The same size and start, but the part's last bytes differ: another version, never appended."""
        task = self.task()
        source, cut = self.cut_run(task)
        body = bytearray(MEDIA["big"])
        body[cut - 10:cut] = bytes(value ^ 0xFF for value in body[cut - 10:cut])
        body = bytes(body)
        self.serve(body, etag=NEW_ETAG)
        before = self.server.count(MEDIA_PATH)

        run = self.download(strict(source, NEW_ETAG, MEDIA_URL + "?token=new"), task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [("bytes=0-", None), (None, None)])
        self.assertEqual(run.outcome.final_path.read_bytes(), body)
        self.assertTrue(any("Link mới cho nội dung khác" in line for line in run.logs), run.logs)

    def test_a_difference_far_before_the_parts_last_mebibyte_is_never_mixed_with_the_new_tail(self):
        """Review P1 (2026-10-10): the part is longer than OVERLAP_CHECK_BYTES and the new version differs at
        byte 128 and after the part, while the part's last 1 MiB is the same. Comparing only that window joined
        the old head to the new tail; the whole part is compared now, so the new version is fetched whole."""
        task = self.task()
        window = download_media_file.OVERLAP_CHECK_BYTES
        source, cut = self.cut_run(task, cut=len(MEDIA["big"]) - 256 * 1024)
        self.assertGreater(cut, window + 128)
        body = bytearray(MEDIA["big"])
        body[128] ^= 0xFF
        body[cut + 128] ^= 0xFF
        body = bytes(body)
        self.assertEqual(body[cut - window:cut], MEDIA["big"][cut - window:cut])  # the old window matches
        self.serve(body, etag=NEW_ETAG)
        before = self.server.count(MEDIA_PATH)

        run = self.download(strict(source, NEW_ETAG, MEDIA_URL + "?token=new"), task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [("bytes=0-", None), (None, None)])
        self.assertEqual(run.outcome.final_path.read_bytes(), body)  # never MEDIA["big"][:cut] + body[cut:]
        self.assertFalse(any("tải nối tiếp" in line for line in run.logs), run.logs)

    def test_a_replaced_head_of_the_same_size_and_identity_is_fetched_whole(self):
        """The new link's file has the size and source identity of the part's, and the same bytes from 4 KiB
        on, but another head: appending its tail would give back the old file under the new validator."""
        task = self.task()
        source, cut = self.cut_run(task, cut=len(MEDIA["big"]) - 256 * 1024)
        body = bytes(value ^ 0x5A for value in MEDIA["big"][:4096]) + MEDIA["big"][4096:]
        self.assertEqual(len(body), len(MEDIA["big"]))
        self.serve(body, etag=NEW_ETAG)
        fresh = strict(source, NEW_ETAG, MEDIA_URL + "?token=new")
        self.assertEqual(fresh.identity_key, source.identity_key)
        before = self.server.count(MEDIA_PATH)

        run = self.download(fresh, task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [("bytes=0-", None), (None, None)])
        self.assertEqual(run.outcome.final_path.read_bytes(), body)
        self.assertNotEqual(body, MEDIA["big"])

    def test_an_answer_cut_inside_the_compared_bytes_writes_nothing_and_is_asked_again(self):
        task = self.task()
        source, cut = self.cut_run(task, cut=len(MEDIA["big"]) - 256 * 1024)  # longer than OVERLAP_CHECK_BYTES
        self.server.route(MEDIA_PATH, replies(Reply(MEDIA["big"], content_type="video/mp4", etag=NEW_ETAG,
                                                    cut_after=cut // 2),
                                              Reply(MEDIA["big"], content_type="video/mp4", etag=NEW_ETAG)))
        before = self.server.count(MEDIA_PATH)

        run = self.download(strict(source, NEW_ETAG, MEDIA_URL + "?token=new"), task, retries=1)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [("bytes=0-", None)] * 2)
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])

    def test_the_new_validator_is_taken_only_once_every_byte_of_the_part_was_compared(self):
        """An answer cut one byte before the part's end leaves the part and its validator as they were; one cut
        right at the part's end has compared every byte: the validator is the new link's, and the next run
        goes on with a range under it (no second comparison)."""
        task = self.task()
        source, cut = self.cut_run(task, cut=len(MEDIA["big"]) - 256 * 1024)  # longer than OVERLAP_CHECK_BYTES
        fresh = strict(source, NEW_ETAG, MEDIA_URL + "?token=new")
        for sent, validator in ((cut - 1, ETAG), (cut, NEW_ETAG)):
            with self.subTest(sent=sent):
                self.serve(etag=NEW_ETAG, cut_after=sent)

                run = self.download(fresh, task, retries=0)

                self.assertEqual(run.outcome.code, "NETWORK")
                self.assertEqual("chưa so hết" in run.outcome.message, validator == ETAG, run.outcome.message)
                self.assertEqual((task / PART_NAME).read_bytes(), MEDIA["big"][:cut])
                self.assertEqual(json.loads((task / STATE_NAME).read_text(encoding="utf-8"))["validator"], validator)
                self.assertEqual(any("tải nối tiếp" in line for line in run.logs), validator == NEW_ETAG)
        self.serve(etag=NEW_ETAG)
        before = self.server.count(MEDIA_PATH)

        run = self.download(fresh, task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [(f"bytes={cut}-", NEW_ETAG)])
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])

    def test_a_whole_file_answer_to_the_comparison_is_compared_and_continued_and_another_2xx_is_refused(self):
        """A server without ranges answers the comparison's ``bytes=0-`` with 200 and the whole file: that is
        what is compared, so the same bytes go on in the same answer; a 203 shows nothing and is refused."""
        for status, ok in ((200, True), (203, False)):
            with self.subTest(status=status):
                task = self.task(f"task-{status}")
                source, cut = self.cut_run(task)
                self.server.route(MEDIA_PATH, lambda seen, number, status=status: Reply(
                    MEDIA["big"], status, "video/mp4", etag=NEW_ETAG, ranges=False))
                before = self.server.count(MEDIA_PATH)

                run = self.download(strict(source, NEW_ETAG, MEDIA_URL + "?token=new"), task, retries=0)

                self.assertEqual(asked(self.transfer_requests(before)), [("bytes=0-", None)])
                if ok:
                    self.assertTrue(run.outcome.ok, run.outcome)
                    self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])
                else:
                    self.assertEqual(run.outcome.code, "BAD_RESPONSE")
                    self.assertEqual((task / PART_NAME).read_bytes(), MEDIA["big"][:cut])

    def test_an_answer_under_the_parts_own_validator_is_not_compared_again_after_a_cut_in_its_tail(self):
        """The link's probe showed another validator, but its answer carries the part's own (a host whose nodes
        differ). Once every byte of the part compared equal, a cut in the tail resumes with a range under that
        validator, never with a second comparison from byte 0."""
        task = self.task()
        source, cut = self.cut_run(task)
        sent = cut + 64 * 1024
        self.server.route(MEDIA_PATH, replies(Reply(MEDIA["big"], content_type="video/mp4", etag=ETAG, cut_after=sent),
                                              Reply(MEDIA["big"], content_type="video/mp4", etag=ETAG)))
        before = self.server.count(MEDIA_PATH)

        run = self.download(strict(source, NEW_ETAG, MEDIA_URL + "?token=new"), task, retries=1)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [("bytes=0-", None), (f"bytes={sent}-", ETAG)])
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])

    def test_a_comparison_answer_that_ends_early_without_a_known_size_is_never_a_finished_file(self):
        """No size from the probe and a 200 without Content-Length that ends inside the part: a cut (the part and
        its validator kept, a message that says so), never a complete file."""
        task = self.task()
        source, cut = self.cut_run(task)
        unsized = dataclasses.replace(source, media_url=MEDIA_URL + "?token=new",
                                      plan=dataclasses.replace(source.plan, total=None, validator=NEW_ETAG))
        self.server.route(MEDIA_PATH, lambda seen, number: Reply(MEDIA["big"], content_type="video/mp4", etag=NEW_ETAG,
                                                                 ranges=False, length=False, cut_after=cut // 2))

        run = self.download(unsized, task, retries=0)

        self.assertEqual(run.outcome.code, "NETWORK")
        self.assertIn("chưa so hết", run.outcome.message)
        self.assertEqual((task / PART_NAME).read_bytes(), MEDIA["big"][:cut])
        self.assertEqual(json.loads((task / STATE_NAME).read_text(encoding="utf-8"))["validator"], ETAG)
        self.assertEqual(sorted(path.name for path in task.iterdir()), sorted([PART_NAME, STATE_NAME]))  # no media.mp4

    def test_a_comparison_answer_that_does_not_start_at_byte_0_is_refused(self):
        task = self.task()
        source, cut = self.cut_run(task)
        body = MEDIA["big"]
        self.server.route(MEDIA_PATH, lambda seen, number: Reply(
            body[1:], 206, "video/mp4", etag=NEW_ETAG, ranges=False,
            headers={"Content-Range": f"bytes 1-{len(body) - 1}/{len(body)}"}))

        run = self.download(strict(source, NEW_ETAG, MEDIA_URL + "?token=new"), task, retries=0)

        self.assertEqual(run.outcome.code, "BAD_RESPONSE")
        self.assertEqual((task / PART_NAME).read_bytes(), body[:cut])
        self.assertEqual(json.loads((task / STATE_NAME).read_text(encoding="utf-8"))["validator"], ETAG)

    def test_a_stop_while_comparing_keeps_the_part_and_its_validator(self):
        """Dừng while the part's bytes come again: the run ends STOPPED with nothing written and the old
        validator, and the next run compares from byte 0 again before it appends."""
        task = self.task()
        source, cut = self.cut_run(task)
        fresh = strict(source, NEW_ETAG, MEDIA_URL + "?token=new")
        self.serve(etag=NEW_ETAG, chunk=16 * 1024, delay=0.05)  # the part's bytes take about 2 s to come
        before = self.server.count(MEDIA_PATH)
        control = ProcessControl()
        result: list[Any] = []
        thread = threading.Thread(target=lambda: result.append(self.download(fresh, task, control=control)))
        thread.start()
        self.assertTrue(wait_for(lambda: self.server.count(MEDIA_PATH) > before))
        time.sleep(0.3)  # some of the part's bytes have been compared

        control.request("stop")
        thread.join(WALL_SECONDS)

        self.assertFalse(thread.is_alive())
        [stopped] = result
        self.assertEqual(stopped.outcome.code, "STOPPED")
        self.assertIn("comparing", {item.stage for item in stopped.progress})
        self.assertEqual({item.downloaded_bytes for item in stopped.progress}, {cut})
        self.assertEqual((task / PART_NAME).read_bytes(), MEDIA["big"][:cut])
        self.assertEqual(json.loads((task / STATE_NAME).read_text(encoding="utf-8"))["validator"], ETAG)
        self.assertFalse(any("tải nối tiếp" in line for line in stopped.logs), stopped.logs)
        self.serve(etag=NEW_ETAG)
        before = self.server.count(MEDIA_PATH)

        resumed = self.download(fresh, task)

        self.assertTrue(resumed.outcome.ok, resumed.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [("bytes=0-", None)])
        self.assertEqual(resumed.outcome.final_path.read_bytes(), MEDIA["big"])

    def test_the_plain_path_trusts_the_servers_range_answer(self):
        """Regression of the other providers' path, unchanged: it resumes with If-Range and keeps what a server
        answering 206 sends (the case the strict plan exists for)."""
        body = changed_body()
        task = self.task()
        source, cut = self.cut_run(task, strict_plan=False)
        self.server.route(MEDIA_PATH, ignoring_if_range(body))
        before = self.server.count(MEDIA_PATH)

        run = self.download(source, task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(self.transfer_requests(before)[0].headers["range"], f"bytes={cut}-")
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"][:cut] + body[cut:])

    def test_without_a_validator_every_try_starts_from_zero_and_the_tries_stay_bounded(self):
        task = self.task()
        self.serve(etag=None)
        source = strict(self.resolve(MEDIA_URL, task), None)
        cut = len(MEDIA["big"]) // 3
        self.serve(etag=None, cut_after=cut)
        before = self.server.count(MEDIA_PATH)

        run = self.download(source, task, retries=2)

        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "NETWORK")
        requests = self.transfer_requests(before)
        self.assertEqual(len(requests), 3)  # the first try and two retries: dropped bytes never reset the count
        for request in requests:
            self.assertNotIn("range", request.headers)

    def test_a_range_answer_of_another_etag_is_never_appended_and_the_file_starts_again(self):
        """The new link shows the part's version, but its server ignores If-Range and answers the range from
        another version, as its own ETag says: no byte of it reaches the part."""
        body = changed_body()
        task = self.task()
        source, cut = self.cut_run(task)
        self.server.route(MEDIA_PATH, answering(body, {"ETag": NEW_ETAG}))
        before = self.server.count(MEDIA_PATH)

        run = self.download(strict(source, ETAG, MEDIA_URL + "?token=new"), task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [(f"bytes={cut}-", ETAG), (None, None)])
        self.assertEqual(run.outcome.final_path.read_bytes(), body)  # the new version whole, never a mix
        self.assertTrue(any("phiên bản file khác" in line for line in run.logs), run.logs)

    def test_a_second_range_answer_of_another_version_in_one_run_ends_it(self):
        body, third = changed_body(), bytes(byte ^ 0x33 for byte in MEDIA["big"])
        task = self.task()
        source, cut = self.cut_run(task)
        self.server.route(MEDIA_PATH, in_turn(answering(body, {"ETag": NEW_ETAG}),
                                              answering(body, {"ETag": NEW_ETAG}, cut_after=cut),
                                              answering(third, {"ETag": '"v3"'})))
        before = self.server.count(MEDIA_PATH)

        run = self.download(strict(source, ETAG, MEDIA_URL + "?token=new"), task, retries=1)

        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "SOURCE_CHANGED")
        # The restart took the whole file's own ETag (its bytes are all of that version), and asked with it.
        self.assertEqual(asked(self.transfer_requests(before)),
                         [(f"bytes={cut}-", ETAG), (None, None), (f"bytes={cut}-", NEW_ETAG)])
        self.assertEqual((task / PART_NAME).read_bytes(), body[:cut])  # nothing of the third version appended

    def test_a_retry_in_the_same_run_appends_only_a_range_answer_that_does_not_show_another_version(self):
        cut = len(MEDIA["big"]) // 3
        body = changed_body()
        cases = (({"ETag": ETAG}, False), ({"ETag": 'W/"v1"'}, False), ({}, False),
                 ({"ETag": NEW_ETAG}, True), ({"ETag": 'W/"v2"'}, True))
        for index, (marks, other) in enumerate(cases):
            with self.subTest(marks=marks):
                task = self.task(f"task-{index}")
                self.serve()
                source = strict(self.resolve(MEDIA_URL, task))
                shown = body if other else MEDIA["big"]  # the bytes of the version the marks name
                self.server.route(MEDIA_PATH, in_turn(answering(MEDIA["big"], {"ETag": ETAG}, cut_after=cut),
                                                      answering(shown, marks), answering(shown, marks)))
                before = self.server.count(MEDIA_PATH)

                run = self.download(source, task, retries=1)

                self.assertTrue(run.outcome.ok, run.outcome)
                expected = [(None, None), (f"bytes={cut}-", ETAG)] + ([(None, None)] if other else [])
                self.assertEqual(asked(self.transfer_requests(before)), expected)
                self.assertEqual(run.outcome.final_path.read_bytes(), shown)  # one version, never a mix

    def test_a_whole_file_answer_to_a_range_starts_the_part_again(self):
        body = changed_body()
        task = self.task()
        source, cut = self.cut_run(task)
        self.serve(body, etag=NEW_ETAG)  # honours If-Range: v1 is not its version, so it sends the whole file
        before = self.server.count(MEDIA_PATH)

        run = self.download(strict(source, ETAG, MEDIA_URL + "?token=new"), task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [(f"bytes={cut}-", ETAG)])
        self.assertEqual(run.outcome.final_path.read_bytes(), body)

    def test_a_part_continues_with_the_version_of_the_answer_its_bytes_came_from(self):
        """The bytes of a part started at byte 0 are that answer's version, whatever the probe saw (a server farm
        whose nodes give their own ETag for the same file). The next range asks with that answer's validator and
        continues with a 206. Before, the plain path asked with the probe's validator: the 200 that answered
        restarted the part, and could use the run's one reset of the progress mark on bytes that never changed.
        On the strict path and on the plain one."""
        cut = len(MEDIA["big"]) // 3
        for strict_plan in (True, False):
            with self.subTest(strict=strict_plan):
                task = self.task(f"farm-{strict_plan}")
                self.serve()
                source = self.resolve(MEDIA_URL, task)
                self.assertEqual(source.plan.validator, ETAG)
                if strict_plan:
                    source = strict(source, ETAG)
                self.server.route(MEDIA_PATH, in_turn(
                    lambda seen, number: Reply(MEDIA["big"], content_type="video/mp4", etag=NEW_ETAG, cut_after=cut),
                    lambda seen, number: Reply(MEDIA["big"], content_type="video/mp4", etag=NEW_ETAG)))
                before = self.server.count(MEDIA_PATH)

                run = self.download(source, task, retries=1)

                self.assertTrue(run.outcome.ok, run.outcome)
                self.assertEqual(asked(self.transfer_requests(before)), [(None, None), (f"bytes={cut}-", NEW_ETAG)])
                self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])
                self.assertFalse(any("tải lại từ đầu" in line for line in run.logs), run.logs)

    def test_a_range_answered_with_another_2xx_is_never_appended(self):
        """A range answered with a 2xx other than 200 or 206 (here 203, as from a proxy that rewrites answers:
        the file from byte 0, without Content-Length) does not show where its bytes start. Nothing of it
        reaches the part, and the run ends BAD_RESPONSE at once, without a retry, with the part as it was.
        Before, its bytes were appended at the end of the part. With a Content-Length it ends the same way, never
        SOURCE_CHANGED for its length. On the strict path and on the plain one."""
        for strict_plan, length in ((True, False), (False, False), (True, True), (False, True)):
            with self.subTest(strict=strict_plan, length=length):
                task = self.task(f"other-2xx-{strict_plan}-{length}")
                source, cut = self.cut_run(task, strict_plan=strict_plan)
                self.server.route(MEDIA_PATH, Reply(MEDIA["big"], 203, "video/mp4", etag=ETAG, ranges=False,
                                                    length=length))
                before = self.server.count(MEDIA_PATH)
                chosen = strict(source, ETAG, MEDIA_URL + "?token=new") if strict_plan else source

                run = self.download(chosen, task)

                self.assertEqual((run.outcome.ok, run.outcome.code), (False, "BAD_RESPONSE"), run.outcome)
                self.assertEqual(asked(self.transfer_requests(before)), [(f"bytes={cut}-", ETAG)])
                self.assertEqual((task / PART_NAME).read_bytes(), MEDIA["big"][:cut])

    def test_a_part_with_a_last_modified_is_checked_by_the_answers_last_modified(self):
        body = changed_body()
        cut = len(MEDIA["big"]) // 3
        cases = (({"Last-Modified": OLD_DATE}, False), ({"Last-Modified": NEW_DATE}, True),
                 ({"ETag": NEW_ETAG}, False))  # an ETag says nothing against a Last-Modified
        for index, (marks, other) in enumerate(cases):
            with self.subTest(marks=marks):
                task = self.task(f"task-{index}")
                self.serve(etag=None, headers={"Last-Modified": OLD_DATE})
                source = self.resolve(MEDIA_URL, task)
                self.assertEqual(source.plan.validator, OLD_DATE)
                source = strict(source, OLD_DATE)
                self.serve(etag=None, headers={"Last-Modified": OLD_DATE}, cut_after=cut)
                self.assertEqual(self.download(source, task, retries=0).outcome.code, "NETWORK")
                shown = body if other else MEDIA["big"]
                self.server.route(MEDIA_PATH, answering(shown, marks))
                before = self.server.count(MEDIA_PATH)

                run = self.download(strict(source, OLD_DATE, MEDIA_URL + "?token=new"), task)

                self.assertTrue(run.outcome.ok, run.outcome)
                expected = [(f"bytes={cut}-", OLD_DATE)] + ([(None, None)] if other else [])
                self.assertEqual(asked(self.transfer_requests(before)), expected)
                self.assertEqual(run.outcome.final_path.read_bytes(), shown)

    def test_without_a_validator_a_later_run_starts_again(self):
        task = self.task()
        source, _cut = self.cut_run(task, etag=None)
        self.serve(etag=None)
        before = self.server.count(MEDIA_PATH)

        run = self.download(source, task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertNotIn("range", self.transfer_requests(before)[0].headers)
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])


class StrictRefreshTest(ServerCase):
    """A ticket that expires mid-transfer: the refreshed source continues the part only for the same version."""

    def setUp(self):
        super().setUp()
        self.lock = threading.Lock()
        self.valid: dict[str, Callable[[Seen, int], Reply]] = {}
        self.refused_status = 403

        def route(seen: Seen, number: int) -> Reply:
            with self.lock:
                answer = self.valid.get(seen.query.get("token", [None])[0])
                status = self.refused_status
            return answer(seen, number) if answer else Reply(b"expired", status, "text/plain")
        self.server.route(MEDIA_PATH, route)

    def start(self, task, body: bytes, etag: str) -> tuple[ResolvedSource, int]:
        """The probe of the first ticket ("old"); its transfer is cut at a third, and then the ticket has
        expired and a new one ("new") serves ``body`` with ``etag``."""
        cut = len(MEDIA["big"]) // 3
        probe_then_cut = replies(Reply(MEDIA["big"], content_type="video/mp4", etag=ETAG),
                                 Reply(MEDIA["big"], content_type="video/mp4", etag=ETAG, cut_after=cut))

        def old(seen: Seen, number: int) -> Reply:
            reply = probe_then_cut(seen, number)
            if reply.cut_after is not None:
                with self.lock:
                    self.valid = {"new": lambda seen, number: Reply(body, content_type="video/mp4", etag=etag)}
            return reply
        with self.lock:
            self.valid = {"old": old}
        return strict(self.resolve(MEDIA_URL + "?token=old", task)), cut

    @staticmethod
    def refresher(source: ResolvedSource, validator: str) -> tuple[Callable[[], ResolvedSource], list[int]]:
        calls: list[int] = []

        def refresh() -> ResolvedSource:
            calls.append(1)
            return strict(source, validator, MEDIA_URL + "?token=new")
        return refresh, calls

    def test_the_same_version_after_a_refresh_is_continued(self):
        task = self.task()
        source, cut = self.start(task, MEDIA["big"], ETAG)
        refresh, calls = self.refresher(source, ETAG)

        run = self.download(source, task, refresh=refresh, retries=1)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(calls, [1])
        last = self.server.seen(MEDIA_PATH)[-1]
        self.assertEqual((last.query["token"], last.headers["range"], last.headers["if-range"]),
                         (["new"], f"bytes={cut}-", ETAG))
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])

    def test_the_same_bytes_under_another_etag_after_a_refresh_are_continued(self):
        task = self.task()
        source, cut = self.start(task, MEDIA["big"], NEW_ETAG)
        refresh, calls = self.refresher(source, NEW_ETAG)

        run = self.download(source, task, refresh=refresh, retries=1)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(calls, [1])
        last = self.server.seen(MEDIA_PATH)[-1]
        self.assertEqual((last.query["token"], last.headers["range"], last.headers.get("if-range")),
                         (["new"], "bytes=0-", None))
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])
        self.assertTrue(any("tải nối tiếp" in line for line in run.logs), run.logs)

    def test_another_version_after_a_refresh_starts_from_zero(self):
        body = changed_body()
        task = self.task()
        source, _cut = self.start(task, body, NEW_ETAG)
        refresh, calls = self.refresher(source, NEW_ETAG)

        run = self.download(source, task, refresh=refresh, retries=1)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(calls, [1])
        last = self.server.seen(MEDIA_PATH)[-1]
        self.assertEqual(last.query["token"], ["new"])
        self.assertNotIn("range", last.headers)
        self.assertEqual(run.outcome.final_path.read_bytes(), body)
        self.assertTrue(any("tải lại từ đầu" in line for line in run.logs), run.logs)

    def test_a_401_asks_for_a_new_ticket_only_on_the_strict_path(self):
        self.refused_status = 401
        for strict_plan in (True, False):
            with self.subTest(strict=strict_plan):
                task = self.task(f"task-{strict_plan}")
                source, _cut = self.start(task, MEDIA["big"], ETAG)
                if not strict_plan:
                    source = dataclasses.replace(source, plan=dataclasses.replace(source.plan, strict_versions=False))
                refresh, calls = self.refresher(source, ETAG)

                run = self.download(source, task, refresh=refresh, retries=1)

                self.assertEqual((run.outcome.ok, bool(calls)), (strict_plan, strict_plan), run.outcome)
                if not strict_plan:
                    self.assertEqual(run.outcome.code, "LOGIN_REQUIRED")  # the plain path: no cookie, no refresh

    def test_a_new_version_after_every_refresh_still_ends_source_changed(self):
        """A file host whose every ticket shows its own ETag (per ticket or per CDN node) and whose tickets end
        before the transfer: each link sends a quarter of the file, then cuts, and refuses its next request;
        every refresh shows another version. Bytes move forward between two refreshes, so the retry counts
        start again each time. A refreshed link with another validator is first asked from byte 0 (no
        If-Range) to compare the whole part; its bytes differ, so that is a version restart like a range answer
        of another version, and byte 0 of the new version may take one more ticket (this one is used up). The run ends
        SOURCE_CHANGED once MAX_VERSION_RESTARTS are used, never starting from byte 0 again after every
        refresh. Past ``limit`` requests the server answers 410, so a run without that bound ends fast with
        another code."""
        quarter, limit = len(MEDIA["big"]) // 4, 20
        versions: dict[int, bytes] = {1: MEDIA["big"]}  # number -> the bytes of that version (all one size)
        asked_by: dict[str, int] = {}
        refreshes: list[int] = []

        def tag(number: int) -> str:
            return f'"v{number}"'

        def version(number: int) -> bytes:
            with self.lock:
                if number not in versions:  # every byte differs from the other versions'
                    versions[number] = MEDIA["big"].translate(bytes(value ^ number for value in range(256)))
                return versions[number]

        def route(seen: Seen, number: int) -> Reply:
            token = seen.query.get("token", [""])[0]
            if number > limit:
                return Reply(b"gone", 410, "text/plain")
            if token == "probe":
                return Reply(MEDIA["big"], content_type="video/mp4", etag=tag(1))
            with self.lock:
                asked_by[token] = asked_by.get(token, 0) + 1
                first = asked_by[token] == 1
            if not first:  # the ticket has expired
                return Reply(b"expired", 403, "text/plain")
            return Reply(version(int(token)), content_type="video/mp4", etag=tag(int(token)), cut_after=quarter)
        self.server.route(MEDIA_PATH, route)
        task = self.task()
        probed = self.resolve(MEDIA_URL + "?token=probe", task)
        self.assertEqual(probed.plan.validator, tag(1))

        def refresh() -> ResolvedSource:
            refreshes.append(1)
            number = len(refreshes) + 1
            return strict(probed, tag(number), MEDIA_URL + f"?token={number}")

        run = self.download(strict(probed, tag(1), MEDIA_URL + "?token=1"), task, refresh=refresh)

        self.assertEqual(run.outcome.code, "SOURCE_CHANGED", f"{run.outcome}; {len(refreshes)} refreshes")
        self.assertEqual(len(refreshes), MAX_VERSION_RESTARTS + 2)  # one more ticket for byte 0 after the restart
        transfers = [item for item in self.server.seen(MEDIA_PATH) if item.query["token"] != ["probe"]]
        started = [item.query["token"][0] for item in transfers if "range" not in item.headers]
        self.assertEqual(started, [str(number) for number in range(1, MAX_VERSION_RESTARTS + 3)])
        compared = [item.query["token"][0] for item in transfers if "range" in item.headers
                    and "if-range" not in item.headers]
        self.assertEqual(compared, ["2", "4"])  # each refreshed link with another validator: its bytes compared
        for item in transfers:  # an appended range is only asked of the link (and version) the part came from
            if "range" in item.headers and "if-range" in item.headers:
                self.assertEqual(item.headers.get("if-range"), tag(int(item.query["token"][0])))
        saved = json.loads((task / STATE_NAME).read_text(encoding="utf-8"))["validator"]
        part = (task / PART_NAME).read_bytes()
        self.assertTrue(part)
        self.assertEqual(part, version(int(saved.strip('"')[1:]))[:len(part)])  # one version, never a mix


class WholeFileAnswerTest(ServerCase):
    """R68: a server that answers range requests with 200 and the whole file (no range support, If-Range
    ignored, or a new version of the file), cut before the end, on the strict path and on the plain one. Each
    such answer restarts the part from byte 0 with that answer's own version (``FileTransfer._receive``).
    Plan 9.13 counts only 206 answers of another version and refreshes in MAX_VERSION_RESTARTS, so these
    restarts never end a run SOURCE_CHANGED: R67's bound is not what this class checks. A try starts the count
    of FILE_RETRIES again only when it leaves the part longer than any part kept before in the run, from the
    part the run resumed (a strict part without a validator is never kept); any other try uses one, also when
    it leaves the part longer than it found it after a 200 had shortened it. So a cut that rises ends only by
    the file's size, and cuts that fall back and rise in turn end by FILE_RETRIES. A part dropped for another
    version starts that mark again from 0, so the new version's cut answers are progress: a 206 or a refresh
    of another version (at most MAX_VERSION_RESTARTS), or a 200 whose own marks show another version (at most
    MAX_NEW_VERSION_RESETS in a run). Scope.wait is patched in this class only: the waits between tries are
    recorded, not slept. A wall-clock guard and a server that answers 410 past REQUEST_CAP requests make a run
    that would not end fail its test, never hang it."""

    cut_run = StrictVersionTest.cut_run
    transfer_requests = StrictVersionTest.transfer_requests

    def setUp(self):
        super().setUp()
        self.waits: list[float] = []

        def no_sleep(scope: Scope, seconds: float) -> bool:
            self.waits.append(seconds)
            return scope.requested
        patcher = mock.patch.object(Scope, "wait", autospec=True, side_effect=no_sleep)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.versions = (changed_body(), MEDIA["big"])  # another version of the same size, then the part's own

    def version(self, pattern: str, number: int) -> tuple[bytes, str]:
        """The body and ETag of answer ``number`` (0 first): always the part's version ("same"), or another
        version first and then the part's in turn ("alternating")."""
        if pattern == "same" or number % 2:
            return self.versions[1], ETAG
        return self.versions[0], NEW_ETAG

    def serve_numbered(self, answer: Callable[[Seen, int], Reply]) -> None:
        """Request ``number`` (0 first) of the media route gets ``answer(seen, number)``; past REQUEST_CAP, 410."""
        lock, served = threading.Lock(), [0]

        def route(seen: Seen, _total: int) -> Reply:
            with lock:
                number = served[0]
                served[0] += 1
            return Reply(b"gone", 410, "text/plain") if number >= REQUEST_CAP else answer(seen, number)
        self.server.route(MEDIA_PATH, route)

    def serve_whole_files(self, answer: Callable[[int], tuple[bytes, str | None, int | None] | None]) -> None:
        """Every request, with a range or not, gets 200 and the whole body of ``answer(number)``: (body, ETag or
        None for no version mark, the byte its connection is cut after or None for the whole body); None
        instead refuses the request with 403, as for an expired ticket."""
        def whole(_seen: Seen, number: int) -> Reply:
            given = answer(number)
            if given is None:
                return Reply(b"expired", 403, "text/plain")
            body, tag, cut = given
            return Reply(body, content_type="video/mp4", etag=tag, ranges=False, cut_after=cut)
        self.serve_numbered(whole)

    @staticmethod
    def cut_answers(body: bytes, tag: str, piece: int) -> Callable[[Seen, int], Reply]:
        """A host of ``body`` (ETag ``tag``) that ends every connection after ``piece`` bytes: a range with that
        If-Range gets a 206, any other request the whole file with 200 (fixture rules)."""
        return lambda seen, number: Reply(body, content_type="video/mp4", etag=tag, cut_after=piece)

    def bounded_download(self, source: ResolvedSource, task,
                         refresh: Callable[[], ResolvedSource] | None = None) -> Any:
        """``download`` in its own thread: still running after WALL_SECONDS, it is cancelled and the test fails."""
        control, ended = ProcessControl(), []

        def run() -> None:
            try:
                ended.append(self.download(source, task, refresh=refresh, control=control))
            except BaseException as error:  # noqa: BLE001 - raised again in the test's thread
                ended.append(error)
        thread = threading.Thread(target=run, name="whole-file-transfer", daemon=True)
        thread.start()
        thread.join(WALL_SECONDS)
        if thread.is_alive():
            control.request("cancel")
            thread.join(30)
            self.fail(f"the transfer was still running after {WALL_SECONDS:g} s")
        if isinstance(ended[0], BaseException):
            raise ended[0]
        return ended[0]

    def test_a_whole_file_cut_at_the_same_byte_every_time_ends_network_once_the_tries_are_used(self):
        """Every answer is cut at half the file. The first leaves the part longer than the third it found (the
        count starts again); the next FILE_RETRIES leave it as long as before. The run ends NETWORK after
        1 + FILE_RETRIES requests, waiting 1, 2, 4, 8 and 8 s between them; with another version in every
        other answer it is NETWORK too, not SOURCE_CHANGED (plan 9.13: a 200 is no version restart), and only
        its first answer of another version sets the mark back (MAX_NEW_VERSION_RESETS), which changes nothing
        here: that answer is past the third anyway. Every answer restarted the part from byte 0: it holds only
        the last answer's version, under that ETag. On the strict path and on the plain one."""
        half = len(MEDIA["big"]) // 2
        cases = [(pattern, strict_plan) for strict_plan in (True, False) for pattern in ("same", "alternating")]
        for index, (pattern, strict_plan) in enumerate(cases):
            with self.subTest(pattern=pattern, strict=strict_plan):
                self.waits.clear()
                task = self.task(f"same-cut-{index}")
                source, cut = self.cut_run(task, strict_plan=strict_plan)
                self.serve_whole_files(lambda number, pattern=pattern: (*self.version(pattern, number), half))
                before = self.server.count(MEDIA_PATH)
                chosen = strict(source, ETAG, MEDIA_URL + "?token=new") if strict_plan else source

                run = self.bounded_download(chosen, task)

                self.assertEqual((run.outcome.ok, run.outcome.code), (False, "NETWORK"), run.outcome)
                requests = self.transfer_requests(before)
                self.assertEqual(len(requests), 1 + FILE_RETRIES)
                tags = [self.version(pattern, number)[1] for number in range(len(requests))]
                self.assertEqual(asked(requests),
                                 [(f"bytes={cut}-", ETAG)] + [(f"bytes={half}-", tag) for tag in tags[:-1]])
                self.assertEqual(self.waits, [min(8.0, 2.0 ** number) for number in range(FILE_RETRIES)])
                body, tag = self.version(pattern, len(requests) - 1)
                self.assertEqual((task / PART_NAME).read_bytes(), body[:half])  # one version, never a mix
                self.assertEqual(json.loads((task / STATE_NAME).read_text(encoding="utf-8"))["validator"], tag)

    def test_a_whole_file_cut_a_little_later_every_time_is_bounded_by_its_size_and_ends_whole(self):
        """Every answer, all of the part's own version, is cut 1/16 of the file later than the one before (from
        half the file); the ninth is whole. Each try leaves the part longer than it found it, so the count of
        FILE_RETRIES starts again every time (each wait is the first one, 1 s): the file's size bounds this run,
        not the retries. Here 9 requests, more than 1 + FILE_RETRIES, each restarting from byte 0 (a 200 that
        extended the part would not fit the size), and the file is whole. One version only, so this run holds
        whatever plan 9.13 says of a 200 of another version; the test above checks that none is mixed in."""
        size = len(MEDIA["big"])
        cuts = [size // 2 + number * (size // 16) for number in range(8)]
        self.assertLess(cuts[-1], size)
        task = self.task()
        source, cut = self.cut_run(task)
        self.serve_whole_files(lambda number: (*self.version("same", number),
                                               cuts[number] if number < len(cuts) else None))
        before = self.server.count(MEDIA_PATH)

        run = self.bounded_download(strict(source, ETAG, MEDIA_URL + "?token=new"), task)

        self.assertTrue(run.outcome.ok, run.outcome)
        requests = self.transfer_requests(before)
        self.assertEqual(len(requests), len(cuts) + 1)
        self.assertGreater(len(requests), 1 + FILE_RETRIES)
        self.assertEqual(asked(requests), [(f"bytes={at}-", ETAG) for at in [cut, *cuts]])
        self.assertEqual(self.waits, [1.0] * len(cuts))
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])

    def test_whole_files_that_fall_back_in_turn_end_network_once_the_tries_are_used(self):
        """R68, a regression: the answers fall back every other time, cut at 3/4 and at 1/2 of the file in turn,
        or cut at 1/2 with the ETag and at 1/4 without any version mark in turn (a strict part without a mark
        is dropped before the next try). Each 200 restarts the part from byte 0, so after the first answer no
        try leaves it longer than the longest part of the run: on the strict path and on the plain one the run
        ends NETWORK after 1 + FILE_RETRIES requests, waiting 1, 2, 4, 8 and 8 s, never at REQUEST_CAP."""
        size = len(MEDIA["big"])
        servers = {
            "3/4 then 1/2": lambda number: (MEDIA["big"], ETAG, (size * 3 // 4, size // 2)[number % 2]),
            "mark every other answer": lambda number: (MEDIA["big"], (ETAG, None)[number % 2],
                                                       (size // 2, size // 4)[number % 2]),
        }
        for index, (server, answer) in enumerate(servers.items()):
            for strict_plan in (True, False):
                with self.subTest(server=server, strict=strict_plan):
                    self.waits.clear()
                    task = self.task(f"falling-{index}-{strict_plan}")
                    source, _cut = self.cut_run(task, strict_plan=strict_plan)
                    self.serve_whole_files(answer)
                    before = self.server.count(MEDIA_PATH)
                    chosen = strict(source, ETAG, MEDIA_URL + "?token=new") if strict_plan else source

                    run = self.bounded_download(chosen, task)

                    self.assertEqual((run.outcome.ok, run.outcome.code), (False, "NETWORK"), run.outcome)
                    self.assertEqual(len(self.transfer_requests(before)), 1 + FILE_RETRIES)
                    self.assertEqual(self.waits, [min(8.0, 2.0 ** number) for number in range(FILE_RETRIES)])

    def test_a_refresh_between_falling_cuts_does_not_start_the_tries_again(self):
        """R68, a regression: the cuts at 3/4 and 1/2 in turn, and before each pair the link is refused (403,
        an expired ticket); every refresh gives a link of the same version. A refresh keeps the longest part of
        the run (only a part dropped for another version sets it back), so no try after the second refresh
        counts as progress and the third 403 ends the run: FORBIDDEN after 7 requests and 2 refreshes, waiting
        1, 2, 4 and 8 s (with FILE_RETRIES = 5, pinned below). On the strict path and on the plain one."""
        size = len(MEDIA["big"])
        cuts = (size * 3 // 4, size // 2)
        for strict_plan in (True, False):
            with self.subTest(strict=strict_plan):
                self.waits.clear()
                task = self.task(f"refresh-{strict_plan}")
                source, _cut = self.cut_run(task, strict_plan=strict_plan)
                self.serve_whole_files(lambda number: None if number % 3 == 0
                                       else (MEDIA["big"], ETAG, cuts[number % 3 - 1]))
                before = self.server.count(MEDIA_PATH)
                chosen = strict(source, ETAG, MEDIA_URL + "?token=new") if strict_plan else source
                refreshes: list[ResolvedSource] = []

                def refresh(chosen: ResolvedSource = chosen, refreshes: list = refreshes) -> ResolvedSource:
                    refreshes.append(dataclasses.replace(chosen, media_url=MEDIA_URL + "?token=fresh"))
                    return refreshes[-1]

                run = self.bounded_download(chosen, task, refresh)

                self.assertEqual((run.outcome.ok, run.outcome.code), (False, "FORBIDDEN"), run.outcome)
                self.assertEqual((len(self.transfer_requests(before)), len(refreshes)), (7, 2))
                self.assertEqual(self.waits, [1.0, 2.0, 4.0, 8.0])

    def test_a_whole_file_of_another_version_is_downloaded_again_with_a_fresh_count(self):
        """After the review of R68: the file changed after the part was cut (a new ETag), and its host ends
        every connection after 1/24 of it. The range gets a 200 of the new version, which restarts the part
        from byte 0. Its marks show another version than the part (``other_version``), so the mark goes back to
        0 and every later cut 206 is progress: the run ends with the new version whole, each wait the first
        one (1 s). Before, the mark stayed at the old part's third and the run ended NETWORK after
        1 + FILE_RETRIES requests. The same holds for "Tiếp tục" after the file changed and for a change within
        a run. On the strict path and on the plain one."""
        pieces = 24
        piece = -(-len(MEDIA["big"]) // pieces)
        body = changed_body()
        for strict_plan in (True, False):
            with self.subTest(strict=strict_plan):
                self.waits.clear()
                task = self.task(f"new-version-{strict_plan}")
                source, cut = self.cut_run(task, strict_plan=strict_plan)
                self.serve_numbered(self.cut_answers(body, NEW_ETAG, piece))
                before = self.server.count(MEDIA_PATH)
                chosen = strict(source, ETAG, MEDIA_URL + "?token=new") if strict_plan else source

                run = self.bounded_download(chosen, task)

                self.assertTrue(run.outcome.ok, run.outcome)
                self.assertEqual(asked(self.transfer_requests(before)), [(f"bytes={cut}-", ETAG)]
                                 + [(f"bytes={number * piece}-", NEW_ETAG) for number in range(1, pieces)])
                self.assertEqual(self.waits, [1.0] * (pieces - 1))
                self.assertEqual(run.outcome.final_path.read_bytes(), body)

    def test_a_refreshed_link_that_brings_another_version_starts_the_count_again(self):
        """After the review of R68: a signed link expires twice around a new version of the file. The run's
        link is refused (403). The refreshed one answers the range with a 200 of the new version cut at 1/10
        of the file, then a 206 cut 1/10 later, then 403; a third link sends the rest. The 200 sets the mark
        back to 0, so both cut answers are progress and the second 403 gets a new link: the run ends with the
        new version whole after 5 requests and 2 refreshes, waiting 1 s twice. Before, the second 403 ended it
        FORBIDDEN, which is not resumable. A refreshed strict link shows the version its own probe saw: the
        part's for the second link (the file changed after that probe), the new one for the third."""
        piece = len(MEDIA["big"]) // 10
        body = changed_body()
        refused, cut_reply = Reply(b"expired", 403, "text/plain"), self.cut_answers(body, NEW_ETAG, piece)
        whole = Reply(body, content_type="video/mp4", etag=NEW_ETAG)
        script = (refused, None, None, refused, whole)  # None: a cut answer of the new version
        for strict_plan in (True, False):
            with self.subTest(strict=strict_plan):
                self.waits.clear()
                task = self.task(f"refreshed-version-{strict_plan}")
                source, cut = self.cut_run(task, strict_plan=strict_plan)
                self.serve_numbered(lambda seen, number: (script[number] or cut_reply(seen, number))
                                    if number < len(script) else whole)
                before = self.server.count(MEDIA_PATH)
                links = [("a", ETAG), ("b", ETAG), ("c", NEW_ETAG), ("d", NEW_ETAG), ("e", NEW_ETAG)]

                def link(token: str, tag: str, strict_plan: bool = strict_plan,
                         source: ResolvedSource = source) -> ResolvedSource:
                    url = f"{MEDIA_URL}?token={token}"
                    return strict(source, tag, url) if strict_plan else dataclasses.replace(source, media_url=url)
                refreshes: list[str] = []

                def refresh(links: list = links, refreshes: list = refreshes, link=link) -> ResolvedSource:
                    token, tag = links[len(refreshes) + 1]
                    refreshes.append(token)
                    return link(token, tag)

                run = self.bounded_download(link(*links[0]), task, refresh)

                self.assertTrue(run.outcome.ok, run.outcome)
                requests = self.transfer_requests(before)
                self.assertEqual([item.query["token"][0] for item in requests], ["a", "b", "b", "b", "c"])
                self.assertEqual(asked(requests), [(f"bytes={cut}-", ETAG), (f"bytes={cut}-", ETAG),
                                                   (f"bytes={piece}-", NEW_ETAG)]
                                 + [(f"bytes={2 * piece}-", NEW_ETAG)] * 2)
                self.assertEqual((refreshes, self.waits), (["b", "c"], [1.0, 1.0]))
                self.assertEqual(run.outcome.final_path.read_bytes(), body)

    def test_after_a_range_answer_of_another_version_the_new_versions_cut_answers_are_progress(self):
        """After the review of R68: a range answer of another version (its own ETag) drops the strict part (the
        run's one version restart) and sets the mark back to 0. The new version then comes in answers cut
        after 1/24 of the file, each one progress (waits of 1 s), and the run ends with it whole. Without that
        reset the mark would stay at the dropped part's third. Strict path only: the plain one appends such an
        answer (StrictVersionTest)."""
        pieces = 24
        piece = -(-len(MEDIA["big"]) // pieces)
        body = changed_body()
        task = self.task()
        source, cut = self.cut_run(task)
        other, cut_reply = answering(body, {"ETag": NEW_ETAG}), self.cut_answers(body, NEW_ETAG, piece)
        self.serve_numbered(lambda seen, number: other(seen, number) if number == 0 else cut_reply(seen, number))
        before = self.server.count(MEDIA_PATH)

        run = self.bounded_download(strict(source, ETAG, MEDIA_URL + "?token=new"), task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [(f"bytes={cut}-", ETAG), (None, None)]
                         + [(f"bytes={number * piece}-", NEW_ETAG) for number in range(1, pieces)])
        self.assertEqual(self.waits, [1.0] * (pieces - 1))
        self.assertEqual(run.outcome.final_path.read_bytes(), body)

    def test_a_whole_file_of_another_version_is_no_version_restart(self):
        """Plan 9.13: a 200 of another version restarts the part but is not counted in MAX_VERSION_RESTARTS. The
        range gets a 200 of a second version cut at 1/10 of the file, then the next range a 206 of a third one
        (its server ignores If-Range): that is the run's first version restart, so the part starts again once
        more and the run ends with the third version whole, never SOURCE_CHANGED. Strict path only."""
        piece = len(MEDIA["big"]) // 10
        second, third = changed_body(), bytes(byte ^ 0x33 for byte in MEDIA["big"])
        task = self.task()
        source, cut = self.cut_run(task)
        script = (lambda seen, number: Reply(second, content_type="video/mp4", etag=NEW_ETAG, cut_after=piece),
                  answering(third, {"ETag": '"v3"'}))
        self.serve_numbered(lambda seen, number: script[number](seen, number) if number < len(script)
                            else Reply(third, content_type="video/mp4", etag='"v3"'))
        before = self.server.count(MEDIA_PATH)

        run = self.bounded_download(strict(source, ETAG, MEDIA_URL + "?token=new"), task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)),
                         [(f"bytes={cut}-", ETAG), (f"bytes={piece}-", NEW_ETAG), (None, None)])
        self.assertEqual(run.outcome.final_path.read_bytes(), third)

    def test_a_strict_part_without_a_validator_never_starts_the_count_again(self):
        """Plan 9.12: on a strict plan without a validator every try starts from byte 0 and the count of retries
        is never started again. The answers carry no version mark and are cut later every time (from half the
        file, 1/16 more each time), so each one leaves the part longer than the one before; none is kept, so
        none is progress: the run ends NETWORK after 1 + FILE_RETRIES requests, all without a range, waiting
        1, 2, 4, 8 and 8 s."""
        size = len(MEDIA["big"])
        cuts = [size // 2 + number * (size // 16) for number in range(8)]
        task = self.task()
        source, _cut = self.cut_run(task, etag=None)
        self.serve_whole_files(lambda number: (MEDIA["big"], None, cuts[number] if number < len(cuts) else None))
        before = self.server.count(MEDIA_PATH)

        run = self.bounded_download(strict(source, None, MEDIA_URL + "?token=new"), task)

        self.assertEqual((run.outcome.ok, run.outcome.code), (False, "NETWORK"), run.outcome)
        self.assertEqual(asked(self.transfer_requests(before)), [(None, None)] * (1 + FILE_RETRIES))
        self.assertEqual(self.waits, [min(8.0, 2.0 ** number) for number in range(FILE_RETRIES)])

    def test_a_whole_file_cut_before_the_end_of_the_resumed_part_is_never_progress(self):
        """The mark starts at the part the run resumes (a third of the file). Answers of the part's own version
        send the whole file cut a little later each time (from a quarter, 1/96 of the file more each time), all
        before the end of that part: none is progress, so the run ends NETWORK after 1 + FILE_RETRIES requests,
        waiting 1, 2, 4, 8 and 8 s. A mark that started at 0 would count every rising cut as progress. On the
        strict path and on the plain one."""
        size = len(MEDIA["big"])
        cuts = [size // 4 + number * (size // 96) for number in range(8)]
        self.assertLess(cuts[-1], size // 3)
        for strict_plan in (True, False):
            with self.subTest(strict=strict_plan):
                self.waits.clear()
                task = self.task(f"resumed-{strict_plan}")
                source, _cut = self.cut_run(task, strict_plan=strict_plan)
                self.serve_whole_files(lambda number: (MEDIA["big"], ETAG, cuts[min(number, len(cuts) - 1)]))
                before = self.server.count(MEDIA_PATH)
                chosen = strict(source, ETAG, MEDIA_URL + "?token=new") if strict_plan else source

                run = self.bounded_download(chosen, task)

                self.assertEqual((run.outcome.ok, run.outcome.code), (False, "NETWORK"), run.outcome)
                self.assertEqual(len(self.transfer_requests(before)), 1 + FILE_RETRIES)
                self.assertEqual(self.waits, [min(8.0, 2.0 ** number) for number in range(FILE_RETRIES)])


class RequirementValuesTest(unittest.TestCase):
    """The plan's values of download_media_file, pinned where the code keeps them."""

    def test_another_version_restarts_a_strict_file_at_most_once_per_run(self):
        """R67 (plan 9.13: "tối đa một lần mỗi lượt (MAX_VERSION_RESTARTS = 1)"; the second in one run ends
        SOURCE_CHANGED)."""
        self.assertEqual(download_media_file.MAX_VERSION_RESTARTS, 1)

    def test_a_file_transfer_tries_again_a_bounded_five_times(self):
        """R68 ("within a bounded number of attempts"). The plans give no number (9.12: "có giới hạn số lần"),
        so the code's value is pinned: FILE_RETRIES = 5, the default of every FileTransfer."""
        self.assertEqual(download_media_file.FILE_RETRIES, 5)
        self.assertEqual(inspect.signature(FileTransfer).parameters["retries"].default, 5)


if __name__ == "__main__":
    unittest.main()
