"""The acceptance probe of a source account (M7, download_account_probe): one resolve on a test root, at most one
ticket and 1 MiB of the file, never a transfer.

``BudgetHttp`` runs against the local fixture server; the probe runs the self-made portal of
tests/release_forms_fixtures.py (``.example`` hosts, made-up ids and titles) on real headless Edge, as the
release-forms tests do. Nothing here is a real site, session or download.
"""
from __future__ import annotations

import io
import json
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from biliflow.control_center import SingleInstanceLock
from biliflow.download_account_listing import FileSelection
from biliflow.download_account_probe import (
    MARKER,
    PROBE_BUDGET_BYTES,
    BudgetHttp,
    ProbeRefused,
    main,
    probe,
)
from biliflow.download_http import HttpError, SafeHttp
from biliflow.download_media_file import FileTransfer
from biliflow.download_runner import ProcessControl
from biliflow.recycle_bin import INSTALL_ROOT
from tests.release_forms_fixtures import TOKEN_VALUE, Card, ReleaseSite
from tests.source_fixtures import FFPROBE, FixtureServer, Reply, public_resolver
from tests.test_download_account_browser import CANARY_SID, DATA, FILES, PORTAL, TEMP_PARENT, TICKETS
from tests.test_download_account_release_forms import (
    CLIP,
    ListOnlyReader,
    ReleaseCase,
    TicketReader,
    setUpModule as release_setup,
    tearDownModule as release_teardown,
)

MIB = 1024 * 1024
URL = "http://media.example/v"


def setUpModule():
    release_setup()


def tearDownModule():
    release_teardown()


class TrackedBody(bytes):
    """A body that records how far the fixture server sliced it to send (``furthest``: the end of the last piece
    the server tried to write)."""

    def __getitem__(self, key):
        if isinstance(key, slice) and key.stop is not None:
            self.furthest = max(getattr(self, "furthest", 0), key.stop)
        return super().__getitem__(key)


# ------------------------------------------------------------------------------------------ no browser
class BudgetHttpTest(unittest.TestCase):
    def setUp(self):
        self.server = FixtureServer()
        self.addCleanup(self.server.close)

    def client(self, budget: int = MIB) -> BudgetHttp:
        return BudgetHttp(self.server.http(), budget, lambda url: "files")

    def test_a_server_that_ignores_range_is_read_up_to_the_budget_and_closed(self):
        body = TrackedBody(bytes(range(256)) * (12 * 1024))  # 3 MiB
        self.server.route("/v", Reply(body, content_type="video/mp4", ranges=False, delay=0.005))
        client = self.client()

        with client.open(URL, ProcessControl(), headers={"Range": f"bytes=0-{MIB - 1}"}) as response:
            data = response.read_some(4 * MIB)  # the reader would take more: the budget stops it
            self.assertTrue(client.responses[0].closed_early)  # closed by the read itself, not by the with

        self.assertEqual(len(data), MIB)
        self.assertEqual(client.used, MIB)
        facts = client.responses[0]
        self.assertEqual((facts.status, facts.bytes_read, facts.accept_ranges, facts.content_range),
                         (200, MIB, "", None))
        self.assertEqual((facts.finished, facts.closed_early), (False, True))
        time.sleep(0.3)  # the server's next write after the close fails
        self.assertLess(body.furthest, len(body))  # the whole file was never sent
        with self.assertRaises(HttpError) as caught:
            client.open(URL, ProcessControl())
        self.assertEqual(caught.exception.code, "PROBE_BUDGET")
        self.assertEqual(self.server.count("/v"), 1)  # no request after the budget

    def test_a_read_never_asks_for_more_than_is_left(self):
        self.server.route("/v", Reply(bytes(300_000), content_type="video/mp4", ranges=False))
        client = self.client(100_000)
        with client.open(URL, ProcessControl()) as response:
            pieces = [len(piece) for piece in response.chunks(64 * 1024)]
        self.assertEqual(sum(pieces), 100_000)
        self.assertEqual(client.responses[0].closed_early, True)

    def test_a_short_body_ends_by_itself_and_a_range_answer_shows_its_numbers(self):
        self.server.route("/v", Reply(bytes(5000), content_type="video/x-matroska", etag='"v1"'))
        client = self.client()
        with client.open(URL, ProcessControl(), headers={"Range": "bytes=0-999"}) as response:
            self.assertEqual(len(response.read_some(MIB)), 1000)
        facts = client.responses[0]
        self.assertEqual((facts.status, facts.content_range, facts.accept_ranges, facts.etag),
                         (206, "0-999/5000", "bytes", "strong"))
        self.assertEqual((facts.bytes_read, facts.closed_early, client.used), (1000, False, 1000))

    def test_a_refused_answer_is_recorded_without_its_link(self):
        self.server.route("/v", Reply(b"no", 403, "text/plain"))
        client = self.client()
        with self.assertRaises(HttpError):
            client.open(URL, ProcessControl())
        self.assertEqual((client.responses[0].status, client.responses[0].error), (403, "FORBIDDEN"))
        self.assertNotIn("media.example", json.dumps([vars(item) for item in client.responses]))

    def test_the_budget_is_one_mib_at_most(self):
        for budget in (0, MIB + 1):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                BudgetHttp(self.server.http(), budget, lambda url: "files")


class RefusalTest(unittest.TestCase):
    def setUp(self):
        TEMP_PARENT.mkdir(parents=True, exist_ok=True)
        self._root = TemporaryDirectory(dir=TEMP_PARENT, prefix="account-probe-")
        self.addCleanup(self._root.cleanup)
        self.root = Path(self._root.name)

    def test_only_a_folder_inside_the_installs_temp_is_a_test_root(self):
        for root in (INSTALL_ROOT, TEMP_PARENT, INSTALL_ROOT / "docs", self.root / "missing"):
            with self.subTest(root=root), self.assertRaises(ProbeRefused) as caught:
                probe(root, f"https://{PORTAL}/phim/1")
            self.assertEqual(caught.exception.code, "TEST_ROOT_ONLY")
        self.assertFalse((INSTALL_ROOT / "docs" / "state").exists())

    def test_a_running_control_center_of_the_root_stops_the_probe(self):
        lock = SingleInstanceLock(self.root / "state" / "control-center.lock")
        self.addCleanup(lock.close)
        with self.assertRaises(ProbeRefused) as caught:
            probe(self.root, f"https://{PORTAL}/phim/1")
        self.assertEqual(caught.exception.code, "CONTROL_CENTER_RUNNING")
        self.assertFalse((self.root / "temp").exists())

    def test_the_deadline_is_finite_and_bounded_and_the_manager_is_the_roots(self):
        for seconds in (float("nan"), float("inf"), 0, -1, 901):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                probe(self.root, f"https://{PORTAL}/phim/1", seconds=seconds)
        with self.assertRaises(ValueError):
            probe(self.root, f"https://{PORTAL}/phim/1", manager=SimpleNamespace(root=INSTALL_ROOT))
        self.assertFalse((self.root / "state").exists())  # refused before the lock or anything else

    def test_a_root_that_may_have_spent_its_ticket_asks_for_no_other(self):
        (self.root / MARKER).parent.mkdir(parents=True)
        (self.root / MARKER).write_text("{}", encoding="utf-8")
        with self.assertRaises(ProbeRefused) as caught:
            probe(self.root, f"https://{PORTAL}/phim/1")
        self.assertEqual(caught.exception.code, "TICKET_ALREADY_ASKED")

    def test_a_link_of_no_configured_source_is_not_probed(self):
        (self.root / "config").mkdir()
        (self.root / "config" / "download_accounts.local.json").write_text(json.dumps({"sources": {"alpha": {
            "adapter": "release-forms", "label": "Nguồn alpha", "login_url": f"https://{PORTAL}/login",
            "hosts": {"portal": [PORTAL], "tickets": [TICKETS], "files": [FILES]}}}}), encoding="utf-8")
        with self.assertRaises(ProbeRefused) as caught:
            probe(self.root, "https://portal.beta.example/phim/1")
        self.assertEqual(caught.exception.code, "NOT_A_SOURCE")
        self.assertFalse((self.root / "state" / "source-accounts").exists())  # no account folder was made

    def test_the_command_line_refuses_without_reading_anything(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--root", str(INSTALL_ROOT / "docs"), "--url", f"https://{PORTAL}/phim/1"])
        self.assertEqual((code, json.loads(out.getvalue())["refused"]), (2, "TEST_ROOT_ONLY"))
        for argv in (["--budget", str(MIB + 1)], ["--file", "501/s1:e1"], ["--file", "501/s1:e1/22 01"],
                     ["--seconds", "nan"], ["--seconds", "0"], ["--seconds", "901"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                    mock.patch("sys.stderr", io.StringIO()):
                main(["--root", str(self.root), "--url", f"https://{PORTAL}/phim/1", *argv])


# ----------------------------------------------------------------------------------------- browser
class ProbeTest(ReleaseCase):
    def setUp(self):
        super().setUp()
        transfer = mock.patch.object(FileTransfer, "run", side_effect=AssertionError("a transfer started"))
        transfer.start()
        self.addCleanup(transfer.stop)

    def run_probe(self, site: ReleaseSite, reader=None, **options) -> dict:
        http = SafeHttp(resolver=public_resolver, connector=self.server.connector,
                        ssl_context=DATA["tls"].client_context())
        run = {"network": self.network, "browser_options": {"page_seconds": 10}, "run_seconds": 90,
               "grace_seconds": 2}
        return probe(self.root, site.url, http=http, manager=self.manager, reader=reader, run_options=run,
                     ffprobe=FFPROBE, **options)

    def file_requests(self):
        return [item for item in self.server.requests if item.host == FILES]

    def assert_nothing_secret(self, report: dict, site: ReleaseSite) -> None:
        text = json.dumps(report, ensure_ascii=False)
        for secret in ("https://", PORTAL, TICKETS, FILES, TOKEN_VALUE, CANARY_SID, "Film.Example", "Phim mẫu",
                       "download-links", *site.tokens):
            self.assertNotIn(secret, text)

    def test_a_one_file_film_resolves_alone_and_ends_after_its_one_ticket_and_sample(self):
        site = self.site(film_cards=[Card("3301", "Film.Example.mkv", None)])

        report = self.run_probe(site, TicketReader())

        self.assertEqual(report["outcome"], "RESOLVED")
        self.assertEqual(site.posts, ["3301"])  # the film's only file, chosen alone: one ticket
        requests = self.file_requests()
        self.assertEqual([item.headers.get("range") for item in requests], [f"bytes=0-{MIB - 1}"])
        self.assertEqual([(item.headers.get("cookie"), item.headers.get("referer")) for item in requests],
                         [(None, None)])
        [facts] = [item for item in report["responses"] if item["role"] == "files"]
        self.assertEqual((facts["status"], facts["bytes_read"]), (206, len(CLIP["mkv"])))
        self.assertTrue(facts["content_range"].startswith("0-"))
        self.assertLessEqual(report["budget"]["used"], PROBE_BUDGET_BYTES)
        self.assertEqual((report["file"]["container"], report["file"]["ranges"], report["file"]["validator"]),
                         ("matroska", True, "etag"))
        self.assertTrue(report["file"]["streams_checked"])
        self.assertEqual(list(self.root.rglob("media.part")), [])
        work = self.root / "temp" / "account-probe"
        self.assertEqual(list(work.iterdir()) if work.exists() else [], [])
        self.assert_nothing_secret(report, site)
        self.assertTrue(report["ticket_marker"])
        with self.assertRaises(ProbeRefused) as caught:  # a second probe of this root asks for no other ticket
            self.run_probe(site, TicketReader())
        self.assertEqual(caught.exception.code, "TICKET_ALREADY_ASKED")
        self.assertEqual(site.posts, ["3301"])

    def test_a_file_server_that_ignores_range_is_closed_at_the_budget(self):
        body = TrackedBody(CLIP["mkv"] + bytes(6 * MIB))
        site = ReleaseSite(body=body, film_cards=[Card("3302", "Film.Example.mkv", None)], file_ranges=False,
                           file_delay=0.01)
        site.install(self.server, PORTAL, TICKETS, FILES)

        report = self.run_probe(site, TicketReader())

        self.assertEqual(report["outcome"], "RESOLVED")
        [facts] = [item for item in report["responses"] if item["role"] == "files"]
        self.assertEqual((facts["status"], facts["bytes_read"], facts["closed_early"], facts["finished"]),
                         (200, PROBE_BUDGET_BYTES, True, False))
        self.assertEqual(report["budget"]["used"], PROBE_BUDGET_BYTES)
        self.assertFalse(report["file"]["ranges"])
        time.sleep(0.5)
        self.assertLess(body.furthest, len(body) // 2)  # the server stopped long before the whole file
        self.assertEqual((site.posts, len(self.file_requests())), (["3302"], 1))
        self.assertEqual(list(self.root.rglob("media.part")), [])
        self.assert_nothing_secret(report, site)

    def test_a_reader_that_reads_no_tickets_stops_before_the_click_and_leaves_no_marker(self):
        site = self.site(film_cards=[Card("3303", "Film.Example.mkv", None)])

        report = self.run_probe(site, ListOnlyReader())

        self.assertEqual(report["outcome"], "TICKET_UNSUPPORTED")
        self.assertEqual((site.posts, site.downloads, report["responses"]), ([], [], []))
        self.assertEqual((report["clicks"], report["ticket_marker"]), (0, False))
        self.assertFalse((self.root / MARKER).exists())
        self.assertEqual(self.file_requests(), [])

    def test_a_series_page_is_read_without_a_ticket_and_a_chosen_file_gets_exactly_its_own(self):
        site = self.two_seasons()

        listed = self.run_probe(site, TicketReader())

        self.assertEqual(listed["outcome"], "NEEDS_EPISODES")
        self.assertEqual({key: listed["listing"][key] for key in ("kind", "complete", "seasons", "files")},
                         {"kind": "series", "complete": True, "seasons": 2, "files": 6})
        self.assertEqual((site.posts, listed["ticket_marker"]), ([], False))
        self.assert_nothing_secret(listed, site)

        chosen = self.run_probe(site, TicketReader(), chosen=FileSelection("", "501", "s2:e2", "2201"))

        self.assertEqual(chosen["outcome"], "RESOLVED")
        self.assertEqual(site.posts, ["2201"])

    def test_the_probe_ends_at_its_deadline_without_taking_the_link(self):
        site = self.site(film_cards=[Card("3304", "Film.Example.mkv", None)], countdown=120)
        started = time.monotonic()

        report = self.run_probe(site, TicketReader(), seconds=30)

        self.assertEqual(report["outcome"], "DEADLINE")
        self.assertLess(time.monotonic() - started, 30 + 15)  # the run's grace and Edge's end included
        self.assertEqual((site.posts, site.downloads), (["3304"], []))
        self.assertTrue(report["ticket_marker"])

    def test_a_fresh_link_the_file_server_refuses_asks_for_no_second_ticket(self):
        site = self.site(film_cards=[Card("3305", "Film.Example.mkv", None)], file_status=403)

        report = self.run_probe(site, TicketReader())

        self.assertEqual(report["outcome"], "TICKET_REFUSED")
        self.assertEqual(site.posts, ["3305"])  # the provider would ask again; the probe's one attempt does not
        self.assertEqual([(item["role"], item["status"], item["error"]) for item in report["responses"]],
                         [("files", 403, "FORBIDDEN")])
        self.assertTrue(report["ticket_marker"])

    def test_the_marker_is_on_disk_before_the_ticket_page_is_read(self):
        seen: list[bool] = []
        root = self.root

        class MarkerReader(TicketReader):
            def ticket_page(self, view):  # only ever read after the click
                seen.append((root / MARKER).exists())
                return super().ticket_page(view)

        site = self.site(film_cards=[Card("3306", "Film.Example.mkv", None)])
        report = self.run_probe(site, MarkerReader())

        self.assertEqual(report["outcome"], "RESOLVED")
        self.assertTrue(seen)
        self.assertTrue(all(seen))  # written right before the click, not after resolve: a killed probe keeps it
        self.assertGreaterEqual(report["clicks"], 1)

    def test_a_probe_that_never_clicked_leaves_no_marker_even_when_it_fails(self):
        site = self.site(film_cards=[Card("3307", "Film.Example.mkv", None)], cookie="other")  # signed out

        report = self.run_probe(site, TicketReader())

        self.assertEqual(report["outcome"], "SOURCE_LOGIN_REQUIRED")
        self.assertEqual((report["clicks"], report["ticket_marker"], site.posts), (0, False, []))


if __name__ == "__main__":
    unittest.main()
