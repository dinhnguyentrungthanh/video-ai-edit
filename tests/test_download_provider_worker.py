"""Direct MP4/HLS links through the real download queue (DownloadWorker, store, API, Control Center routes).

Every link is a ``.example`` link served by tests/source_fixtures.FixtureServer on 127.0.0.1 (the
policy-checked client connects the checked public address to it). Media are made by the project's
FFmpeg; the real ``verify_video`` checks each file before it is moved into the temporary root's input.
yt-dlp is the fake of tests/fake_yt_dlp.py. Nothing reaches the network, a real site or the project's
own Control Center, state, input or output.
"""
from __future__ import annotations

import errno
import json
import sqlite3
import sys
import threading
import time
import unittest

from biliflow.download_api import public_task, public_url
from biliflow.download_files import verify_video
from biliflow.download_hls import MANIFEST_NAME, SEG_DIR
from biliflow.download_runner import ProcessControl, YtDlpRunner
from biliflow.download_sources import SourceTransfers
from biliflow.download_store import DownloadStore
from biliflow.download_worker import DownloadWorker
from tests.source_fixtures import (
    FFMPEG,
    FFPROBE,
    HAVE_FFMPEG,
    NEED_FFMPEG,
    FixtureServer,
    Reply,
    make_clip,
    make_hls,
    media_playlist,
    public_resolver,
    remove_tree,
    segment_route,
    temp_root,
)
from tests.test_download_routes import RouteCase
from tests.test_download_worker import FAKE, GB, WorkerCase, ok_verifier, video, wait_for

MEDIA: dict[str, object] = {}
HLS_URL = "http://media.example/show/index.m3u8"
HLS_TYPE = "application/vnd.apple.mpegurl"


def setUpModule():
    if not HAVE_FFMPEG:
        return
    base = temp_root("biliflow-provider-media-")
    MEDIA["base"] = base
    MEDIA["clip"] = make_clip(base / "clip.mp4", seconds=3, size="160x120").read_bytes()
    MEDIA["silent"] = make_clip(base / "silent.mp4", seconds=3, audio=False, size="160x120").read_bytes()
    MEDIA["hls"] = make_hls(base / "hls", seconds=6, size="160x120")
    MEDIA["hls_other"] = make_hls(base / "hls-other", seconds=4, size="160x120")


def tearDownModule():
    if "base" in MEDIA:
        remove_tree(MEDIA["base"])


class ProviderMixin:
    """A WorkerCase whose worker fetches provider links from the fixture server and verifies for real."""

    def setUp(self):
        self.server = self.make_server()
        super().setUp()

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            self.server.close()

    def make_server(self):
        return FixtureServer()

    def http_options(self):
        """Options of the worker's SafeHttp (an HTTPS case gives its test CA)."""
        return {}

    def source_registry(self):
        """The worker's providers; None gives the code's own (default_registry of the temporary root)."""
        return None

    def make_worker(self, verifier=None):
        runner = YtDlpRunner(self.root, command_prefix=[sys.executable, str(FAKE)], deno_path=None,
                             env_extra={"FAKE_YTDLP_SCENARIO": str(self.scenario_path),
                                        "FAKE_YTDLP_LOG": str(self.log_path)})
        http = self.server.http(**self.http_options())
        return DownloadWorker(self.root, self.store, runner=runner, verifier=verifier or verify_video,
                              ffmpeg=FFMPEG, ffprobe=FFPROBE, space_probe=lambda root: tuple(self.space),
                              resolver=public_resolver, space_retry_seconds=0.05,
                              cache_pruner=lambda root: {"removed_files": 0}, sources=self.source_registry(),
                              http=http, transfers=SourceTransfers(http, ffmpeg=FFMPEG, ffprobe=FFPROBE))

    # ------------------------------------------------------------------ routes
    def serve_hls(self, media, *, delay=0.0, chunk=16 * 1024, prefix="/seg"):
        uris = [f"{prefix}/{index}.ts" for index in range(1, len(media.segments) + 1)]
        self.server.route("/show/index.m3u8", Reply(media_playlist(uris, media.durations).encode(),
                                                    content_type=HLS_TYPE))
        delays = {index: delay for index in range(1, len(media.segments) + 1)} if delay else None
        route = segment_route(media, delays=delays, chunk=chunk)
        for uri in uris:
            self.server.route(uri, route)

    def task_dir(self, task_id):
        return self.root / "temp" / "downloads" / str(task_id)

    def events(self, task_id):
        return [event["kind"] for event in self.store.events(task_id)]

    def wait_fragments(self, task_id, count, timeout=30.0):
        self.assertTrue(wait_for(lambda: (self.store.get(task_id)["fragments_done"] or 0) >= count, timeout),
                        self.store.get(task_id))


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class DirectLinkTests(ProviderMixin, WorkerCase):
    def test_a_direct_mp4_link_is_fetched_checked_and_moved_into_input(self):
        self.server.route("/v/clip.mp4", Reply(MEDIA["clip"], content_type="video/mp4", etag='"v1"'))
        task, = self.add("http://media.example/v/clip.mp4")
        self.run_all(60)
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", (done, self.store.events(task["id"])))
        target = self.root / "input" / "clip.mp4"
        self.assertEqual(target.read_bytes(), MEDIA["clip"])
        self.assertEqual((done["probe"]["provider"], done["probe"]["transport"]), ("direct", "http_file"))
        self.assertEqual((done["verify"]["video_codec"], done["verify"]["audio_codec"]), ("h264", "aac"))
        self.assertIn("SOURCE_RESOLVED", self.events(task["id"]))
        self.assertEqual(self.calls(), [])  # yt-dlp never ran
        self.assertFalse(self.task_dir(task["id"]).exists())

    def test_an_hls_link_is_joined_in_playlist_order_and_its_progress_counts_segments(self):
        media = MEDIA["hls"]
        self.serve_hls(media)
        task, = self.add(HLS_URL)
        self.run_all(90)
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", (done, self.store.events(task["id"])))
        self.assertTrue((self.root / "input" / "show.mp4").is_file())
        count = len(media.segments)
        self.assertEqual((done["progress_basis"], done["fragments_done"], done["fragments_total"]),
                         ("fragments", count, count))
        self.assertEqual(done["downloaded_bytes"], sum(len(segment) for segment in media.segments))
        self.assertAlmostEqual(done["verify"]["duration_seconds"], media.duration, delta=0.5)
        public = public_task(done)
        self.assertEqual((public["fragments_done"], public["fragments_total"]), (count, count))
        self.assertEqual(public["media"]["provider"], "direct")
        self.assertEqual(public["media"]["transport"], "hls")
        self.assertEqual(public["media"]["source_label"], "Link HLS trực tiếp")
        self.assertEqual(self.calls(), [])

    def test_a_file_without_audio_fails_at_probing_and_is_never_downloaded(self):
        self.server.route("/v/silent.mp4", Reply(MEDIA["silent"], content_type="video/mp4"))
        task, = self.add("http://media.example/v/silent.mp4")
        self.run_all()
        done = self.store.get(task["id"])
        self.assertEqual((done["state"], done["error_code"]), ("FAILED", "NO_AUDIO_STREAM"))
        self.assertEqual([seen.headers.get("range") for seen in self.server.seen("/v/silent.mp4")],
                         ["bytes=0-1048575"])  # the probe's sample only
        self.assertEqual(self.calls(), [])
        self.assertEqual(list((self.root / "input").iterdir()), [])

    def test_a_link_the_provider_declines_goes_to_yt_dlp_unchanged(self):
        self.server.route("/watch/page.mp4", Reply(b"<!doctype html><title>page</title>", content_type="text/html"))
        self.scenario(probe={"json": video("Trang")}, download={"id": "abc"})
        self.worker.verifier = ok_verifier  # the fake yt-dlp writes placeholder bytes, not a video
        task, = self.add("http://media.example/watch/page.mp4")
        self.run_all()
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", done)
        self.assertIn("SOURCE_DECLINED", self.events(task["id"]))
        self.assertEqual(len(self.calls("probe")), 1)
        self.assertIn("http://media.example/watch/page.mp4", self.calls("probe")[0]["argv"])
        self.assertIsNone(done["probe"].get("provider"))

    def test_drm_and_live_streams_fail_and_are_never_handed_to_yt_dlp(self):
        segment = "#EXTINF:4.0,\n/seg/1.ts\n"
        drm = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI=\"skd://key\"\n" + segment + \
              "#EXT-X-ENDLIST\n"
        live = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n" + segment
        self.server.route("/drm/index.m3u8", Reply(drm.encode(), content_type=HLS_TYPE))
        self.server.route("/live/index.m3u8", Reply(live.encode(), content_type=HLS_TYPE))
        drm_task, live_task = self.add("http://media.example/drm/index.m3u8", "http://media.example/live/index.m3u8")
        self.run_all()
        self.assertEqual(self.store.get(drm_task["id"])["error_code"], "DRM")
        self.assertEqual(self.store.get(live_task["id"])["error_code"], "LIVE")
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.server.count("/seg/1.ts"), 0)

    def test_the_public_task_hides_the_link_token_and_the_media_link(self):
        self.server.route("/v/clip.mp4", Reply(MEDIA["clip"], content_type="video/mp4"))
        task, = self.add("http://media.example/v/clip.mp4?token=SECRETVALUE123&quality=hd")
        self.run_all(60)
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", done)
        public = json.dumps(public_task(done), ensure_ascii=False)
        self.assertNotIn("SECRETVALUE123", public)
        self.assertIn("quality=hd", public)
        self.assertNotIn("SECRETVALUE123", json.dumps(done["probe"]))
        events = json.dumps(self.store.events(task["id"]), ensure_ascii=False)
        self.assertNotIn("SECRETVALUE123", events + "\n".join(self.store.log_lines(task["id"])))

    def test_a_source_estimate_waits_for_disk_space_like_any_download(self):
        self.server.route("/v/clip.mp4", Reply(MEDIA["clip"], content_type="video/mp4"))
        self.space = [100 * GB, 100 * GB]  # nothing above the reserve
        task, = self.add("http://media.example/v/clip.mp4")
        self.worker.dispatch()
        self.assertTrue(wait_for(lambda: self.store.get(task["id"])["state"] == "WAITING_SPACE"))
        self.assertTrue(wait_for(lambda: "WAITING_SPACE" in self.events(task["id"])))
        self.assertEqual(self.store.get(task["id"])["estimated_bytes"], len(MEDIA["clip"]))
        self.space = [10_000 * GB, 100 * GB]
        self.run_all(60)
        self.assertEqual(self.state(task["id"]), "COMPLETED")


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class StopResumeCancelTests(ProviderMixin, WorkerCase):
    def finished_segments(self, task_id):
        manifest = self.task_dir(task_id) / SEG_DIR / MANIFEST_NAME
        return {int(key) for key in json.loads(manifest.read_text(encoding="utf-8"))["segments"]}

    def test_stop_then_resume_fetches_only_the_missing_segments(self):
        media = MEDIA["hls"]
        self.serve_hls(media, delay=0.15, chunk=4096)
        task, = self.add(HLS_URL)
        self.worker.dispatch()
        self.wait_fragments(task["id"], 2)
        stopped = self.worker.stop(task["id"])
        self.assertEqual(stopped["state"], "STOPPED", stopped)
        kept = self.finished_segments(task["id"])
        self.assertGreaterEqual(len(kept), 2)
        self.assertEqual(list((self.task_dir(task["id"]) / SEG_DIR).glob("*.part")), [])
        self.worker.resume(task["id"])
        self.run_all(90)
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", (done, self.store.events(task["id"])))
        for index in kept:  # segment 1 was also read once by the probe
            self.assertEqual(self.server.count(f"/seg/{index}.ts"), 2 if index == 1 else 1, index)
        self.assertIn("Dùng lại", "\n".join(self.store.log_lines(task["id"])))

    def test_cancel_mid_download_removes_the_temp_folder_and_nothing_writes_afterwards(self):
        self.serve_hls(MEDIA["hls"], delay=0.15, chunk=4096)
        task, = self.add(HLS_URL)
        self.worker.dispatch()
        self.wait_fragments(task["id"], 1)
        cancelled = self.worker.cancel(task["id"])
        self.assertEqual(cancelled["state"], "CANCELLED", cancelled)
        self.assertTrue(self.worker.wait_idle(10))
        time.sleep(0.6)
        self.assertFalse(self.task_dir(task["id"]).exists())

    def test_a_network_failure_after_the_retries_ends_interrupted_and_resume_continues_the_part(self):
        body = MEDIA["clip"]
        cut = {"done": False}

        def route(seen, number):
            wanted = seen.headers.get("range")
            if wanted is None and not cut["done"]:
                cut["done"] = True
                return Reply(body, content_type="video/mp4", etag='"v1"', cut_after=len(body) // 2)
            return Reply(body, content_type="video/mp4", etag='"v1"')
        self.server.route("/v/clip.mp4", route)
        self.worker.transfers.files.retries = 0
        task, = self.add("http://media.example/v/clip.mp4")
        self.run_all(60)
        interrupted = self.store.get(task["id"])
        self.assertEqual((interrupted["state"], interrupted["error_code"]), ("INTERRUPTED", "NETWORK"), interrupted)
        part = self.task_dir(task["id"]) / "media.part"
        self.assertEqual(part.stat().st_size, len(body) // 2)
        self.worker.resume(task["id"])
        self.run_all(60)
        self.assertEqual(self.state(task["id"]), "COMPLETED")
        self.assertEqual((self.root / "input" / "clip.mp4").read_bytes(), body)
        last = self.server.seen("/v/clip.mp4")[-1]
        self.assertEqual((last.headers.get("range"), last.headers.get("if-range")),
                         (f"bytes={len(body) // 2}-", '"v1"'))

    def test_a_source_that_changed_before_resume_fails_and_retry_starts_over(self):
        self.serve_hls(MEDIA["hls"], delay=0.15, chunk=4096)
        task, = self.add(HLS_URL)
        self.worker.dispatch()
        self.wait_fragments(task["id"], 1)
        self.assertEqual(self.worker.stop(task["id"])["state"], "STOPPED")
        self.serve_hls(MEDIA["hls_other"], prefix="/seg2")
        self.worker.resume(task["id"])
        self.run_all(60)
        changed = self.store.get(task["id"])
        self.assertEqual((changed["state"], changed["error_code"]), ("FAILED", "SOURCE_CHANGED"), changed)
        self.worker.retry(task["id"])
        self.run_all(90)
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", (done, self.store.events(task["id"])))
        self.assertEqual(done["fragments_total"], len(MEDIA["hls_other"].segments))

    def test_a_restart_settles_the_running_download_and_resume_reuses_its_segments(self):
        self.serve_hls(MEDIA["hls"], delay=0.15, chunk=4096)
        task, = self.add(HLS_URL)
        self.worker.dispatch()
        self.wait_fragments(task["id"], 2)
        self.worker.shutdown(20)  # the Control Center stops
        self.assertEqual(self.state(task["id"]), "INTERRUPTED")
        kept = self.finished_segments(task["id"])
        self.worker = self.make_worker()  # the next start
        self.worker.recover()
        self.worker.resume(task["id"])
        self.run_all(90)
        self.assertEqual(self.state(task["id"]), "COMPLETED", self.store.events(task["id"]))
        for index in kept:
            self.assertEqual(self.server.count(f"/seg/{index}.ts"), 2 if index == 1 else 1, index)

    def test_a_resume_after_a_stop_while_verifying_reuses_the_file_even_when_the_link_expired(self):
        body, expired = MEDIA["clip"], threading.Event()

        def route(seen, number):
            if expired.is_set():
                return Reply(b"expired", 403, "text/plain")
            return Reply(body, content_type="video/mp4", etag='"v1"')
        self.server.route("/v/clip.mp4", route)
        entered, release = threading.Event(), threading.Event()

        def slow_verifier(*args, **kwargs):
            entered.set()
            release.wait(30)
            return verify_video(*args, **kwargs)
        self.worker.verifier = slow_verifier
        task, = self.add("http://media.example/v/clip.mp4")
        self.worker.dispatch()
        self.assertTrue(entered.wait(30))
        stopper = threading.Thread(target=self.worker.shutdown, args=(30,))
        stopper.start()  # the Control Center stops while the file is checked
        self.assertTrue(wait_for(self.worker._stopping.is_set))
        time.sleep(0.3)
        release.set()
        stopper.join(30)
        self.assertEqual(self.state(task["id"]), "INTERRUPTED")
        expired.set()
        asked = self.server.count("/v/clip.mp4")
        self.worker = self.make_worker()
        self.worker.recover()
        self.worker.resume(task["id"])
        self.run_all(60)
        self.assertEqual(self.state(task["id"]), "COMPLETED", self.store.events(task["id"]))
        self.assertEqual(self.server.count("/v/clip.mp4"), asked)  # the expired link was never needed
        self.assertEqual((self.root / "input" / "clip.mp4").read_bytes(), body)

    def test_a_resume_while_the_network_is_down_ends_interrupted_and_keeps_the_part(self):
        body, cut = MEDIA["clip"], {"done": False}

        def route(seen, number):  # the first whole-file request drops at half; the samples are whole
            first = "range" not in seen.headers and not cut["done"]
            cut["done"] = cut["done"] or first
            return Reply(body, content_type="video/mp4", etag='"v1"', cut_after=len(body) // 2 if first else None)
        self.server.route("/v/clip.mp4", route)
        self.worker.transfers.files.retries = 0
        task, = self.add("http://media.example/v/clip.mp4")
        self.run_all(60)
        self.assertEqual(self.store.get(task["id"])["error_code"], "NETWORK")

        def offline(host, port):
            raise OSError("no network")
        self.worker.http.resolver = offline
        self.worker.resume(task["id"])
        self.run_all(60)
        down = self.store.get(task["id"])
        self.assertEqual((down["state"], down["error_code"]), ("INTERRUPTED", "DNS_FAILED"), down)
        self.assertEqual((self.task_dir(task["id"]) / "media.part").stat().st_size, len(body) // 2)

        self.worker.http.resolver = public_resolver
        self.worker.resume(task["id"])
        self.run_all(60)
        self.assertEqual(self.state(task["id"]), "COMPLETED")
        self.assertEqual((self.root / "input" / "clip.mp4").read_bytes(), body)

    def test_a_task_left_downloading_by_a_crash_is_interrupted_at_start(self):
        self.serve_hls(MEDIA["hls"])
        task, = self.add(HLS_URL)
        self.store.transition(task["id"], {"QUEUED"}, "DOWNLOADING")
        self.worker.recover()
        self.assertEqual(self.state(task["id"]), "INTERRUPTED")


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class ControlCenterRouteTests(ProviderMixin, RouteCase):
    """The page's own routes on a stub Control Center (temporary root, its own port); the loop runs."""

    def wait_state(self, task_id, *states, timeout=90.0):
        def reached():
            _, detail = self.get(f"/api/downloads/{task_id}")
            return detail["task"]["state"] in states
        self.assertTrue(wait_for(reached, timeout), self.get(f"/api/downloads/{task_id}")[1])
        return self.get(f"/api/downloads/{task_id}")[1]

    def test_an_hls_link_pasted_on_the_page_is_stopped_resumed_and_completed(self):
        self.serve_hls(MEDIA["hls"], delay=0.1, chunk=4096)
        self.worker.start()
        status, created = self.batch(HLS_URL)
        self.assertEqual(status, 200, created)
        task_id = created["tasks"][0]["id"]
        self.assertTrue(wait_for(lambda: (self.get(f"/api/downloads/{task_id}")[1]["task"]["fragments_done"]
                                          or 0) >= 1, 60))
        self.assertEqual(self.post(f"/api/downloads/{task_id}/stop")[0], 200)
        self.wait_state(task_id, "STOPPED")
        self.assertEqual(self.post(f"/api/downloads/{task_id}/resume")[0], 200)
        detail = self.wait_state(task_id, "COMPLETED")
        task = detail["task"]
        self.assertEqual(task["output_name"], "show.mp4")
        self.assertEqual(task["media"]["source_label"], "Link HLS trực tiếp")
        self.assertEqual(task["fragments_done"], task["fragments_total"])
        kinds = [event["kind"] for event in detail["events"]]
        for kind in ("QUEUED", "SOURCE_RESOLVED", "STOPPED", "RESUMED", "COMPLETED"):
            self.assertIn(kind, kinds)
        snapshot = self.get("/api/downloads")[1]
        self.assertEqual(snapshot["counts"], {"COMPLETED": 1})


class PublicLinkAndOutcomeTests(unittest.TestCase):
    def test_the_page_link_masks_signatures_and_tokens_but_keeps_ordinary_links_readable(self):
        cases = {
            "https://cdn.videos.example/2024/05/some-long-folder-name/another-folder/episode-12-final-cut.mp4":
                "https://cdn.videos.example/2024/05/some-long-folder-name/another-folder/episode-12-final-cut.mp4",
            "http://media.example/v/a.mp4?token=SECRET1&quality=hd&e=17000&hmac=abc&author=x":
                "http://media.example/v/a.mp4?token=***&quality=hd&e=***&hmac=***&author=x",
            "https://media.example/hls/Ab3dEf9hIjKlMn0pQrStUvWxYz12345678/index.m3u8":
                "https://media.example/hls/***/index.m3u8",
            "https://media.example/s/0123456789abcdef0123456789abcdef/a.mp4#t=3": "https://media.example/s/***/a.mp4",
            "https://www.example.com/watch?v=abc123&list=x": "https://www.example.com/watch?v=abc123&list=x",
        }
        for url, shown in cases.items():
            with self.subTest(url=url):
                self.assertEqual(public_url(url), shown)

    def test_a_held_file_or_a_full_disk_keeps_the_parts_for_resume(self):
        control = ProcessControl()
        held = SourceTransfers._failed(PermissionError(13, "held"), control, None)
        full = SourceTransfers._failed(OSError(errno.ENOSPC, "full"), control, None)
        broken = SourceTransfers._failed(OSError(errno.EIO, "io"), control, None)
        self.assertEqual((held.code, held.resumable), ("FILE_HELD", True))
        self.assertEqual((full.code, full.resumable), ("DISK_FULL", True))
        self.assertEqual((broken.code, broken.resumable), ("DISK_ERROR", False))


class StoreMigrationTests(unittest.TestCase):
    def test_an_older_database_gets_the_progress_columns_and_keeps_its_rows(self):
        root = temp_root("biliflow-download-migrate-")
        try:
            path = root / "downloads.sqlite3"
            old = sqlite3.connect(path)
            old.execute("CREATE TABLE download_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, "
                        "state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, "
                        "updated_at TEXT NOT NULL, queued_at TEXT NOT NULL, state_since TEXT NOT NULL, "
                        "finished_at TEXT, desired_name TEXT, original_title TEXT, video_id TEXT, "
                        "duration_seconds REAL, estimated_bytes INTEGER, entries_json TEXT, chosen_entry INTEGER, "
                        "probe_json TEXT, downloaded_bytes INTEGER NOT NULL DEFAULT 0, total_bytes INTEGER, "
                        "speed REAL, eta REAL, error_code TEXT, error_message TEXT, temp_dir TEXT, temp_file TEXT, "
                        "output_path TEXT, output_sha256 TEXT, output_size INTEGER, verify_json TEXT, "
                        "name_locked INTEGER NOT NULL DEFAULT 0, pid INTEGER, pid_created REAL)")
            old.execute("INSERT INTO download_tasks (url, state, created_at, updated_at, queued_at, state_since, "
                        "downloaded_bytes) VALUES ('https://clips.example/v/1', 'DOWNLOADING', 't', 't', 't', 't', 7)")
            old.commit()
            old.close()
            store = DownloadStore(path)
            try:
                task = store.get(1)
                self.assertEqual((task["url"], task["downloaded_bytes"]), ("https://clips.example/v/1", 7))
                self.assertIsNone(task["fragments_done"])
                self.assertTrue(store.update_progress(1, 1, downloaded_bytes=9, total_bytes=None, speed=None,
                                                      eta=None, progress_basis="fragments", fragments_done=2,
                                                      fragments_total=5, transfer_stage="downloading"))
                task = store.get(1)
                self.assertEqual((task["fragments_done"], task["fragments_total"], task["progress_basis"]),
                                 (2, 5, "fragments"))
            finally:
                store.close()
        finally:
            remove_tree(root)

    def test_progress_of_another_attempt_or_state_is_ignored(self):
        root = temp_root("biliflow-download-progress-")
        try:
            store = DownloadStore(root / "downloads.sqlite3")
            try:
                task, = store.add_tasks(["https://clips.example/v/2"])
                kwargs = dict(downloaded_bytes=1, total_bytes=None, speed=None, eta=None, fragments_done=1)
                self.assertFalse(store.update_progress(task["id"], 1, **kwargs))  # QUEUED
                store.transition(task["id"], {"QUEUED"}, "DOWNLOADING")
                self.assertFalse(store.update_progress(task["id"], 2, **kwargs))  # an older attempt's thread
                self.assertTrue(store.update_progress(task["id"], 1, **kwargs))
            finally:
                store.close()
        finally:
            remove_tree(root)


if __name__ == "__main__":
    unittest.main()
