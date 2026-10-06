"""Native adapters against self-made MP4/HLS, checked fixture networking and the real worker."""
import json
import struct
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from biliflow.download_http import HttpError
from biliflow.download_page_sources import ArticleMp4Provider, PlayerHlsProvider, SourceNeedsChoice, direct_sources
from biliflow.download_png_ts import PNG, MAX_COVER, ts_payload
from biliflow.download_runner import ProcessControl
from biliflow.download_source_types import ResolveContext, SourceError
from biliflow.download_sources import HostList, SourceRegistry, default_registry
from tests.source_fixtures import (FFPROBE, HAVE_FFMPEG, NEED_FFMPEG, FixtureServer, Reply,
                                  make_clip, make_hls, media_playlist, remove_tree, temp_root)
from tests.test_download_provider_worker import ProviderMixin
from tests.test_download_worker import WorkerCase
from tests.test_download_worker import wait_for, GB

DATA = {}


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def cover():
    return PNG + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IEND", b"")


def setUpModule():
    if HAVE_FFMPEG:
        root = temp_root("biliflow-native-media-")
        DATA.update(root=root, clip=make_clip(root / "clip.mp4", seconds=4).read_bytes(),
                    hls=make_hls(root / "hls", seconds=3))
        # Make a real moov-at-end MP4 so bounded metadata seeking is exercised.
        from tests.source_fixtures import run_ffmpeg
        run_ffmpeg("-i", root / "clip.mp4", "-c", "copy", root / "late.mp4")
        DATA["late"] = (root / "late.mp4").read_bytes()


def tearDownModule():
    if "root" in DATA:
        remove_tree(DATA["root"])


def serve_player(server, episode_id=7, token="secret-one", png_only=False):
    server.route("/phim/demo/tap-latest", Reply(b'<iframe id="embed-player" src="http://player.example/embed/7"></iframe>'))
    media = f"http://media.example/index.m3u8?signature={token}"
    key = "public-url-key"
    encoded = bytes(byte ^ ord(key[i % len(key)]) for i, byte in enumerate(media.encode())).hex()
    episode = {"id": episode_id, "movieId": 3, "name": "Tap 1", "server": "Server 1",
               "encrypted_url": encoded, "http_referer": "http://player.example/"}
    embed = '<title>Fixture film</title><script>const episode = ' + json.dumps(episode) + ';</script>'
    embed += '<script src="/media_player.js"></script>'
    server.route("/embed/7", Reply(embed.encode()))
    server.route("/media_player.js", Reply(f"hexXorDecrypt(episode.encrypted_url, '{key}')".encode()))
    hls = DATA["hls"]
    uris = [f"http://media.example/seg/{i}.png?signature={token}" for i in range(len(hls.segments))]
    server.route("/index.m3u8", Reply(media_playlist(uris, hls.durations).encode()))
    for i, body in enumerate(hls.segments):
        server.route(f"/seg/{i}.png", Reply(cover() if png_only else cover() + b"padding" + body, chunk=37))


def serve_article(server, *, multiple=False, late=False, token="private+%2B"):
    source = "http://media.example/film.mp4?signature=" + token
    versions = [{"name": "Full", "link": "http://wrapper.example/temp?s=" + quote(source, safe="")}]
    if multiple:
        versions.append({"name": "Other", "link": "http://media.example/other.mp4"})
    film = {"article_code": "demo", "article_title": "Fixture film",
            "main_url": "http://ad.example/ad.mp4", "extra_info": versions}
    server.route("/webapi/index", Reply(json.dumps([film]).encode(), content_type="application/json"))
    server.route("/film.mp4", Reply(DATA["late" if late else "clip"], content_type="video/mp4", etag='"same-file"'))
    server.route("/other.mp4", Reply(DATA["clip"], content_type="video/mp4", etag='"other-file"'))


class PrefixTests(unittest.TestCase):
    def test_streamed_cover_and_padding_are_removed(self):
        ts = (b"G" + b"\0" * 187) * 10
        raw = cover() + b"padding" + ts
        self.assertEqual(b"".join(ts_payload(raw[i:i+3] for i in range(0, len(raw), 3))), ts)

    def test_plain_ts_passes_through(self):
        ts = (b"G" + b"\0" * 187) * 10
        self.assertEqual(b"".join(ts_payload([ts])), ts)

    def test_image_only_corrupt_crc_large_cover_and_arbitrary_prefix_fail(self):
        invalid = bytearray(cover())
        invalid[20] ^= 1
        for raw in (cover(), bytes(invalid), PNG + struct.pack(">I", MAX_COVER) + b"IHDRxxxx",
                    b"abc" + (b"G" + b"\0" * 187) * 10):
            if raw.startswith(PNG):
                with self.assertRaises(HttpError):
                    b"".join(ts_payload([raw]))
            else:
                # Non-PNG is left to the regular TS validator, never searched for a later stream.
                self.assertEqual(b"".join(ts_payload([raw])), raw)

    def test_wrapper_is_decoded_once(self):
        source = "http://cdn.example/file.mp4?sig=a+b%2Bc"
        self.assertEqual(direct_sources({"link": "http://wrapper.example/?s=" + quote(source, safe="")}), [source])


class PostTests(unittest.TestCase):
    def test_post_body_same_origin_redirect_and_cross_origin_refusal(self):
        with FixtureServer() as server:
            server.route("/api", Reply(b"", status=307, headers={"Location": "/final"}))
            server.route("/final", Reply(b"ok"))
            client, control = server.http(), ProcessControl()
            body, _ = client.fetch("http://api.example/api", control, limit=20, method="POST", body=b"{}",
                                   headers={"Content-Type": "application/json"})
            self.assertEqual(body, b"ok")
            self.assertEqual(server.seen("/final")[0].body, b"{}")
            server.route("/api", Reply(b"", status=307, headers={"Location": "http://other.example/final"}))
            with self.assertRaises(HttpError) as caught:
                client.fetch("http://api.example/api", control, limit=20, method="POST", body=b"{}")
            self.assertEqual(caught.exception.code, "CROSS_ORIGIN_POST")
            self.assertEqual(server.count("/final"), 1)

    def test_post_303_becomes_get_without_body_and_large_payload_is_refused(self):
        with FixtureServer() as server:
            server.route("/api", Reply(b"", status=303, headers={"Location": "/final"}))
            server.route("/final", Reply(b"ok"))
            client, control = server.http(), ProcessControl()
            client.fetch("http://api.example/api", control, limit=20, method="POST", body=b"{}")
            seen = server.seen("/final")[0]
            self.assertEqual((seen.method, seen.body), ("GET", b""))
            with self.assertRaises(ValueError):
                client.open("http://api.example/api", control, method="POST", body=b"a" * (1024 * 1024 + 1))


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_root("biliflow-native-probe-")
        self.server = FixtureServer()
        self.ctx = ResolveContext(self.server.http(), ProcessControl(), self.root, ffprobe=FFPROBE)

    def tearDown(self):
        self.server.close()
        remove_tree(self.root)

    def test_player_refresh_preserves_identity_and_hides_signature(self):
        provider = PlayerHlsProvider(HostList(["site.example"]))
        serve_player(self.server)
        first = provider.resolve("http://site.example/phim/demo/tap-latest", self.ctx)
        serve_player(self.server, token="secret-two")
        fresh = provider.resolve("http://site.example/phim/demo/tap-latest", self.ctx)
        self.assertEqual(first.identity_key, fresh.identity_key)
        self.assertNotIn("secret-one", json.dumps(first.public()))
        self.assertEqual((first.video_codec, first.audio_codec), ("h264", "aac"))
        self.assertTrue(first.plan.strip_png)
        serve_player(self.server, episode_id=8)
        other = provider.resolve("http://site.example/phim/demo/tap-latest", self.ctx)
        self.assertNotEqual(first.identity_key, other.identity_key)

    def test_player_pure_png_is_rejected(self):
        serve_player(self.server, png_only=True)
        with self.assertRaises(SourceError):
            PlayerHlsProvider(HostList(["site.example"])).resolve("http://site.example/phim/demo/tap-latest", self.ctx)

    def test_article_reads_post_and_moov_at_end_without_ad_request(self):
        serve_article(self.server, late=True)
        provider = ArticleMp4Provider(HostList(["site.example"]))
        provider.minimum_seconds = 1
        source = provider.resolve("http://site.example/videoinfo?id=demo", self.ctx)
        self.assertAlmostEqual(source.duration_seconds, 4, delta=.1)
        self.assertEqual(source.audio_codec, "aac")
        self.assertEqual(self.server.seen("/webapi/index")[0].method, "POST")
        self.assertFalse(any(seen.host == "ad.example" for seen in self.server.requests))
        self.assertNotIn("private", json.dumps(source.public()))

    def test_article_rejects_short_source_and_asks_for_multiple_versions(self):
        serve_article(self.server)
        provider = ArticleMp4Provider(HostList(["site.example"]))
        with self.assertRaises(SourceError) as caught:
            provider.resolve("http://site.example/videoinfo?id=demo", self.ctx)
        self.assertEqual(caught.exception.code, "SOURCE_TOO_SHORT")
        serve_article(self.server, multiple=True)
        with self.assertRaises(SourceNeedsChoice) as caught:
            provider.resolve("http://site.example/videoinfo?id=demo", self.ctx)
        self.assertEqual([item["title"] for item in caught.exception.choices], ["Full", "Other"])
        self.assertNotIn("http", json.dumps(caught.exception.choices))


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class NativeWorkerTests(ProviderMixin, WorkerCase):
    def source_registry(self):
        return SourceRegistry([PlayerHlsProvider(HostList(["site.example"])),
                               ArticleMp4Provider(HostList(["site.example"]))])

    def test_player_hls_lands_in_input_with_audio_video(self):
        serve_player(self.server)
        task, = self.add("http://site.example/phim/demo/tap-latest")
        self.run_all(60)
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", (done, self.store.events(task["id"])))
        self.assertTrue(Path(done["output_path"]).is_file())
        self.assertEqual(self.calls(), [])

    def test_article_choice_probes_selected_version_before_download(self):
        serve_article(self.server, multiple=True)
        with patch.object(ArticleMp4Provider, "minimum_seconds", 1):
            task, = self.add("http://site.example/videoinfo?id=demo")
            self.run_all()
            self.assertEqual(self.state(task["id"]), "NEEDS_CHOICE")
            self.assertEqual(self.server.count("/film.mp4"), 0)
            self.worker.choose(task["id"], 2)
            self.run_all(60)
        done = self.store.get(task["id"])
        self.assertEqual(done["state"], "COMPLETED", (done, self.store.events(task["id"])))
        self.assertGreater(self.server.count("/other.mp4"), 0)
        self.assertEqual(self.server.count("/film.mp4"), 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.worker.choose(task["id"], 2)["state"], "COMPLETED")

    def test_changed_latest_episode_is_refused_before_resume(self):
        serve_player(self.server)
        self.space = [100 * GB, 100 * GB]
        task, = self.add("http://site.example/phim/demo/tap-latest")
        self.worker.dispatch()
        self.assertTrue(wait_for(lambda: self.state(task["id"]) == "WAITING_SPACE"))
        self.assertEqual(self.worker.stop(task["id"])["state"], "STOPPED")
        serve_player(self.server, episode_id=99)
        self.space = [10_000 * GB, 100 * GB]
        self.worker.resume(task["id"])
        self.run_all(60)
        done = self.store.get(task["id"])
        self.assertEqual((done["state"], done["error_code"]), ("FAILED", "SOURCE_CHANGED"), done)
        self.assertEqual(list((self.root / "input").iterdir()), [])

    def test_registered_adapters_are_enabled_by_exact_local_hosts(self):
        path = self.root / "config" / "download_providers.local.json"
        path.write_text(json.dumps({"providers": {"player-hls": {"hosts": ["site.example"]}}}), encoding="utf-8")
        registry = default_registry(self.root)
        self.assertEqual(registry.provider_for("http://site.example/phim/demo/tap-1").id, "player-hls")
        self.assertIsNone(registry.provider_for("http://child.site.example/phim/demo/tap-1"))
