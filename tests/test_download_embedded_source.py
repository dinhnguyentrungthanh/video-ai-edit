"""Headless Edge with synthetic player pages; all network requests use the checked fixture HTTP client."""
import importlib.util
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from biliflow.download_embedded_source import EmbeddedMediaProvider, browser_source
from biliflow.download_http import Cancelled, HttpError
from biliflow.download_runner import ProcessControl
from biliflow.download_sources import HostList, SourceRegistry
from biliflow.download_source_types import ResolveContext, SourceError
from tests.source_fixtures import (FFPROBE, HAVE_FFMPEG, NEED_FFMPEG, FixtureServer, Reply, make_clip,
                                  public_resolver, remove_tree, temp_root)
from tests.test_download_provider_worker import ProviderMixin
from tests.test_download_worker import WorkerCase, wait_for

HAVE_BROWSER = importlib.util.find_spec("playwright") is not None and Path(
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe").is_file()
DATA = {}


def setUpModule():
    if HAVE_FFMPEG:
        root = temp_root("biliflow-browser-media-")
        DATA.update(root=root, clip=make_clip(root / "clip.mp4", seconds=4).read_bytes())


def tearDownModule():
    if "root" in DATA:
        remove_tree(DATA["root"])


def serve_player(server):
    server.route("/xem/demo", Reply(b'<h1>Fixture episode</h1><iframe src="http://ads.example/ad"></iframe>'
        b'<div id="playerled"><iframe src="http://player.example/outer"></iframe></div>', content_type="text/html"))
    server.route("/ad", Reply(b'<video src="http://ads.example/ad.mp4"></video>', content_type="text/html"))
    server.route("/outer", Reply(b'<iframe src="/inner"></iframe>', content_type="text/html"))
    server.route("/inner", Reply(b'<script>document.cookie="anonymous=test";</script>'
        b'<video src="http://media.example/clip.mp4?signature=SECRET"></video>', content_type="text/html"))
    server.route("/clip.mp4", Reply(DATA["clip"], content_type="video/mp4", etag='"fixture"'))


@unittest.skipUnless(HAVE_BROWSER and HAVE_FFMPEG, "installed Playwright, Edge and project FFmpeg required")
class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_root("biliflow-browser-probe-")
        self.server = FixtureServer()
        self.ctx = ResolveContext(self.server.http(), ProcessControl(), self.root, FFPROBE)
        serve_player(self.server)

    def tearDown(self):
        self.server.close()
        remove_tree(self.root)

    def test_nested_player_is_selected_ads_are_ignored_and_profile_is_removed(self):
        from playwright.sync_api import BrowserType
        launch = BrowserType.launch_persistent_context
        calls = []

        def observed(browser_type, *args, **kwargs):
            calls.append(kwargs)
            return launch(browser_type, *args, **kwargs)

        with patch.object(BrowserType, "launch_persistent_context", observed):
            source = EmbeddedMediaProvider(HostList(["site.example"])).resolve("http://site.example/xem/demo", self.ctx)
        self.assertTrue(calls[0]["headless"])
        self.assertEqual(calls[0]["channel"], "msedge")
        self.assertEqual((source.duration_seconds, source.video_codec, source.audio_codec), (4.0, "h264", "aac"))
        self.assertNotIn("SECRET", json.dumps(source.public()))
        self.assertEqual(self.server.count("/ad.mp4"), 0)
        self.assertFalse(any("cookie" in request.headers for request in self.server.requests))
        self.assertEqual(list(self.root.glob("browser-*")), [])

    def test_internal_iframe_request_is_refused_without_connection(self):
        self.server.route("/xem/demo", Reply(b'<div id="playerled"><iframe src="http://internal.example/secret"></iframe></div>',
                                                  content_type="text/html"))
        http = self.server.http(resolver=lambda host, port: ["127.0.0.1"] if host == "internal.example" else public_resolver(host, port))
        ctx = ResolveContext(http, ProcessControl(), self.root, FFPROBE)
        with self.assertRaises(HttpError) as caught:
            browser_source("http://site.example/xem/demo", ctx)
        self.assertEqual(caught.exception.code, "PRIVATE_ADDRESS")
        self.assertEqual(self.server.count("/secret"), 0)
        self.assertEqual(list(self.root.glob("browser-*")), [])

    def test_stop_during_page_read_closes_browser_and_removes_profile(self):
        self.server.route("/xem/demo", Reply(b"x" * 100_000, content_type="text/html", delay=.1, chunk=1000))
        failures = []

        def run():
            try:
                browser_source("http://site.example/xem/demo", self.ctx)
            except Exception as error:
                failures.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(wait_for(lambda: self.server.count("/xem/demo") > 0, 10))
            self.ctx.control.request("stop")
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertTrue(failures)
            self.assertIsInstance(failures[0], Cancelled)
            self.assertEqual(list(self.root.glob("browser-*")), [])
        finally:
            self.ctx.control.request("cancel")
            thread.join(15)

    def test_missing_library_has_a_clear_error(self):
        with patch.dict("sys.modules", {"playwright.sync_api": None}):
            with self.assertRaises(SourceError) as caught:
                browser_source("http://site.example/xem/demo", self.ctx)
        self.assertEqual(caught.exception.code, "BROWSER_RUNTIME_MISSING")


@unittest.skipUnless(HAVE_BROWSER and HAVE_FFMPEG, "installed Playwright, Edge and project FFmpeg required")
class BrowserWorkerTests(ProviderMixin, WorkerCase):
    def source_registry(self):
        return SourceRegistry([EmbeddedMediaProvider(HostList(["site.example"]))])

    def test_browser_provider_downloads_through_native_worker_and_publishes_verified_video(self):
        serve_player(self.server)
        task, = self.add("http://site.example/xem/demo")
        self.run_all(60)
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", (done, self.store.events(task["id"])))
        self.assertEqual(Path(done["output_path"]).read_bytes(), DATA["clip"])
        self.assertEqual(self.calls(), [])
