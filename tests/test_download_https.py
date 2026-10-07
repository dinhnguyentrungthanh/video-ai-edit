"""HTTPS through the checked client and the download queue: certificate and host-name checks, a direct
MP4 and an HLS playlist over TLS, a stop during a TLS read.

The server is tests/source_fixtures.FixtureServer behind TLS, with a certificate of a throwaway test CA
made at run time (tests/tls_fixtures.py); the client trusts only that CA unless a test says otherwise.
Links are ``.example`` links served on 127.0.0.1: no network, no real site, no certificate installed.
"""
from __future__ import annotations

import time
import unittest

from biliflow.download_http import HttpError, SafeHttp
from biliflow.download_media_file import SNIFF_BYTES
from biliflow.download_runner import ProcessControl
from tests.source_fixtures import (
    HAVE_FFMPEG,
    NEED_FFMPEG,
    FixtureServer,
    Reply,
    make_clip,
    make_hls,
    public_resolver,
    remove_tree,
    temp_root,
)
from tests.test_download_provider_worker import ProviderMixin
from tests.test_download_worker import WorkerCase, wait_for
from tests.tls_fixtures import HAVE_TLS, NEED_TLS, make_tls_files

SHARED: dict[str, object] = {}


def setUpModule():
    if not HAVE_TLS:
        return
    base = temp_root("biliflow-https-")
    SHARED["base"] = base
    SHARED["tls"] = make_tls_files(base / "tls", ("media.example",))
    if HAVE_FFMPEG:
        SHARED["clip"] = make_clip(base / "clip.mp4", seconds=3, size="160x120").read_bytes()
        SHARED["long"] = make_clip(base / "long.mp4", seconds=8, size="320x240").read_bytes()
        SHARED["hls"] = make_hls(base / "hls", seconds=6, size="160x120")


def tearDownModule():
    if "base" in SHARED:
        remove_tree(SHARED["base"])


@unittest.skipUnless(HAVE_TLS, NEED_TLS)
class HttpsClientTests(unittest.TestCase):
    def setUp(self):
        self.server = FixtureServer(tls=SHARED["tls"].server_context())
        self.control = ProcessControl()

    def tearDown(self):
        self.server.close()

    def trusted(self) -> SafeHttp:
        return self.server.http(ssl_context=SHARED["tls"].client_context())

    def refused(self, http: SafeHttp, url: str) -> HttpError:
        with self.assertRaises(HttpError) as caught:
            http.fetch(url, self.control, limit=100)
        return caught.exception

    def test_a_certificate_of_the_trusted_ca_for_the_link_host_is_accepted(self):
        self.server.route("/hello", Reply(b"hello", content_type="text/plain"))

        body, response = self.trusted().fetch("https://media.example/hello", self.control, limit=100)

        self.assertEqual((body, response.status), (b"hello", 200))
        self.assertEqual(self.server.seen("/hello")[0].host, "media.example")

    def test_a_certificate_for_another_host_name_is_a_tls_error_and_no_request_is_sent(self):
        self.server.route("/hello", Reply(b"hello"))

        error = self.refused(self.trusted(), "https://cdn.example/hello")

        self.assertEqual((error.code, error.retryable), ("TLS_ERROR", False))
        self.assertEqual(self.server.count("/hello"), 0)

    def test_a_certificate_of_an_unknown_ca_is_a_tls_error(self):
        self.server.route("/hello", Reply(b"hello"))
        system_only = SafeHttp(resolver=public_resolver, connector=self.server.connector)  # Windows' CAs

        error = self.refused(system_only, "https://media.example/hello")

        self.assertEqual(error.code, "TLS_ERROR")
        self.assertEqual(self.server.count("/hello"), 0)

    def test_a_redirect_from_https_to_http_is_refused_before_the_plain_request(self):
        self.server.route("/v", Reply(b"", 302, headers={"Location": "http://media.example/plain"}))

        error = self.refused(self.trusted(), "https://media.example/v")

        self.assertEqual(error.code, "DOWNGRADE")
        self.assertEqual(self.server.count("/plain"), 0)

    def test_a_redirect_to_another_https_host_needs_that_host_s_certificate(self):
        self.server.route("/v", Reply(b"", 302, headers={"Location": "https://cdn.example/x"}))
        self.server.route("/x", Reply(b"other"))

        error = self.refused(self.trusted(), "https://media.example/v")

        self.assertEqual(error.code, "TLS_ERROR")
        self.assertEqual(self.server.count("/x"), 0)


@unittest.skipUnless(HAVE_TLS and HAVE_FFMPEG, f"{NEED_TLS}; {NEED_FFMPEG}")
class HttpsDownloadTests(ProviderMixin, WorkerCase):
    def make_server(self):
        return FixtureServer(tls=SHARED["tls"].server_context())

    def http_options(self):
        return {"ssl_context": SHARED["tls"].client_context()}

    def test_a_direct_mp4_over_https_is_moved_into_input_unchanged(self):
        self.server.route("/v/clip.mp4", Reply(SHARED["clip"], content_type="video/mp4", etag='"v1"'))

        task, = self.add("https://media.example/v/clip.mp4")
        self.run_all(60)

        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", (done, self.store.events(task["id"])))
        self.assertEqual((self.root / "input" / "clip.mp4").read_bytes(), SHARED["clip"])
        self.assertEqual((done["verify"]["video_codec"], done["verify"]["audio_codec"]), ("h264", "aac"))
        self.assertEqual(self.calls(), [])  # yt-dlp never ran

    def test_an_hls_playlist_over_https_is_joined_from_every_segment(self):
        media = SHARED["hls"]
        self.serve_hls(media)

        task, = self.add("https://media.example/show/index.m3u8")
        self.run_all(90)

        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", (done, self.store.events(task["id"])))
        self.assertEqual((done["fragments_done"], done["fragments_total"]), (len(media.segments),) * 2)
        self.assertTrue((self.root / "input" / "show.mp4").is_file())
        self.assertAlmostEqual(done["verify"]["duration_seconds"], media.duration, delta=0.5)
        for index in range(1, len(media.segments) + 1):
            self.assertGreaterEqual(self.server.count(f"/seg/{index}.ts"), 1, index)

    def test_a_stop_during_a_tls_read_keeps_the_part_and_resume_continues_it(self):
        body = SHARED["long"]

        def route(seen, number):  # the probe's first-MiB reads come at once, the transfer slowly
            slow = seen.headers.get("range") != f"bytes=0-{SNIFF_BYTES - 1}"
            return Reply(body, content_type="video/mp4", etag='"v1"', delay=0.05 if slow else 0.0, chunk=4096)
        self.server.route("/v/long.mp4", route)
        task, = self.add("https://media.example/v/long.mp4")
        self.worker.dispatch()
        # The bytes show while they arrive (pieces of one socket read), long before the file is whole.
        self.assertTrue(wait_for(lambda: self.store.get(task["id"])["state"] == "DOWNLOADING"
                                 and 16 * 1024 <= (self.store.get(task["id"])["downloaded_bytes"] or 0) < len(body) // 2,
                                 30), self.store.get(task["id"]))

        started = time.monotonic()
        stopped = self.worker.stop(task["id"])
        self.assertTrue(self.worker.wait_idle(10))
        elapsed = time.monotonic() - started

        self.assertEqual(stopped["state"], "STOPPED", stopped)
        self.assertLess(elapsed, 5.0)  # the blocked TLS read ended at once, not at the read timeout
        part = self.task_dir(task["id"]) / "media.part"
        kept = part.stat().st_size
        self.assertGreater(kept, 0)
        self.worker.resume(task["id"])
        self.run_all(90)
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", (done, self.store.events(task["id"])))
        self.assertEqual((self.root / "input" / "long.mp4").read_bytes(), body)
        self.assertIn(f"bytes={kept}-", [seen.headers.get("range") for seen in self.server.seen("/v/long.mp4")])


@unittest.skipUnless(HAVE_TLS and HAVE_FFMPEG, f"{NEED_TLS}; {NEED_FFMPEG}")
class UntrustedHttpsDownloadTests(ProviderMixin, WorkerCase):
    """The worker keeps Windows' CAs only (the production default): the test CA is unknown to it."""

    def make_server(self):
        return FixtureServer(tls=SHARED["tls"].server_context())

    def test_an_unknown_certificate_fails_the_task_at_probing_without_yt_dlp(self):
        self.server.route("/v/clip.mp4", Reply(SHARED["clip"], content_type="video/mp4"))

        task, = self.add("https://media.example/v/clip.mp4")
        self.run_all(30)

        done = self.store.get(task["id"])
        self.assertEqual((done["state"], done["error_code"]), ("FAILED", "TLS_ERROR"), done)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.server.count("/v/clip.mp4"), 0)
        self.assertEqual(list((self.root / "input").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
