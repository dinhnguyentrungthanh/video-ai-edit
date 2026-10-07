"""Source providers: direct media links, the provider interface and the resumable file transfer.

Only self-made media (FFmpeg test patterns) behind the local fixture server (``tests/source_fixtures.py``):
no network, no real site. Links use ``.example`` hosts on the default port.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import struct
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from biliflow.download_hls import resolve_hls
from biliflow.download_http import allowed_headers
from biliflow.download_media_file import (
    FILE_RETRIES,
    PART_NAME,
    SNIFF_BYTES,
    STATE_NAME,
    FilePlan,
    PlaylistLink,
    container_of,
    content_range,
    filename_title,
    resolve_file,
)
from biliflow.download_runner import DownloadOutcome, ProcessControl, Progress, SizeGuard, mask_line, stop_reason
from biliflow.download_source_types import (
    MAX_TITLE_LENGTH,
    ResolveContext,
    ResolvedSource,
    SourceDeclined,
    SourceError,
    codec_names,
    describe_change,
    identity_key,
    is_volatile_query_name,
    stable_url,
    title_from_url,
)
from biliflow.download_sources import (
    RESUMABLE_CODES,
    DirectMediaProvider,
    HostList,
    SourceTransfers,
)
from biliflow.download_transfer import FINISHED_NAME, LogBuffer, ProgressMeter, probe_streams, stream_summary
from tests.source_fixtures import (
    FFMPEG,
    FFPROBE,
    HAVE_FFMPEG,
    NEED_FFMPEG,
    PUBLIC_ADDRESS,
    FixtureServer,
    Reply,
    Seen,
    make_clip,
    media_playlist,
    remove_tree,
    temp_root,
)

CLIP_SECONDS = 3
CLIP_SIZE = "160x120"
PADDING_BYTES = 2 * 1024 * 1024
MEDIA_PATH = "/v/clip.mp4"
MEDIA_URL = "http://media.example" + MEDIA_PATH
TS_PATH = "/v/clip.ts"
TS_URL = "http://media.example" + TS_PATH
PAGE_URL = "http://video.example/watch?v=abc"
PAGE_ORIGIN = "http://video.example/"  # the Referer a media host on another name gets, as from a browser
PLAYER_PATH = "/api/player"
ETAG = '"v1"'

_ROOT: Path | None = None
MEDIA: dict[str, bytes] = {}


def padded_mp4(clip: bytes, padding: int) -> bytes:
    """``clip`` followed by an MP4 'free' box: still a valid MP4, larger than the sniff window."""
    return clip + struct.pack(">I", padding) + b"free" + bytes(padding - 8)


def setUpModule() -> None:  # noqa: N802 - unittest API
    global _ROOT
    _ROOT = temp_root("biliflow-test-sources-")
    if not HAVE_FFMPEG:
        return
    clips = {"mp4": ("mp4", {}), "video_only": ("mp4", {"audio": False}), "audio_only": ("mp4", {"video": False}),
             "mkv": ("mkv", {}), "ts": ("ts", {}), "ts_video_only": ("ts", {"audio": False})}
    for name, (container, options) in clips.items():
        path = make_clip(_ROOT / "media" / f"{name}.{container}", seconds=CLIP_SECONDS, size=CLIP_SIZE,
                         container=container, **options)
        MEDIA[name] = path.read_bytes()
    MEDIA["big"] = padded_mp4(MEDIA["mp4"], PADDING_BYTES)


def tearDownModule() -> None:  # noqa: N802 - unittest API
    if _ROOT is not None:
        remove_tree(_ROOT)


def scratch_dir() -> Path:
    return Path(tempfile.mkdtemp(dir=_ROOT))


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def no_refresh() -> ResolvedSource:
    raise AssertionError("refresh() was not expected")


def replies(*answers: Reply) -> Callable[[Seen, int], Reply]:
    """A route giving ``answers`` in order to the requests it receives, then repeating the last one."""
    lock = threading.Lock()
    calls: list[Seen] = []

    def route(seen: Seen, _number: int) -> Reply:
        with lock:
            index = len(calls)
            calls.append(seen)
        return answers[min(index, len(answers) - 1)]
    return route


# ------------------------------------------------------------------ a site provider as a contributor adds one
class ExampleSiteProvider:
    """Exact hosts from the local config, public player data, then the shared resolvers with a Referer."""

    id = "example-site"
    label = "Trang mẫu"
    extra_headers: dict[str, str] = {}

    def __init__(self, hosts: HostList):
        self.hosts = hosts

    def claims(self, url: str) -> bool:
        return self.hosts.matches(url)

    def resolve(self, url: str, ctx: ResolveContext) -> ResolvedSource:
        video_id = parse_qs(urlsplit(url).query).get("v", [""])[0]
        if not video_id:
            raise SourceDeclined("không có mã video")
        body, _ = ctx.http.fetch(f"http://video.example{PLAYER_PATH}?id={video_id}", ctx.control, limit=64 * 1024)
        player = json.loads(body.decode("utf-8"))
        headers = {"Referer": url, **self.extra_headers}
        resolver = resolve_hls if player["format"] == "hls" else resolve_file
        return resolver(player["src"], ctx, provider=self.id, label=self.label, headers=headers)


class CookieSiteProvider(ExampleSiteProvider):
    extra_headers = {"Cookie": "session=abc"}


# ------------------------------------------------------------------------------------------- pure checks
class DirectLinkClaimsTest(unittest.TestCase):
    def test_claims_media_file_and_playlist_paths_in_any_case_and_with_a_query(self):
        provider = DirectMediaProvider()
        links = ["http://media.example/v/clip.mp4", "https://media.example/v/CLIP.M4V", "http://media.example/a/b.mov",
                 "http://media.example/v/clip.MKV?quality=720", "http://media.example/v/clip.webm#t=3",
                 "http://media.example/v/clip.ts?token=abc&expires=1", "http://media.example/hls/Index.M3U8?sig=x"]
        for link in links:
            with self.subTest(link=link):
                self.assertTrue(provider.claims(link))

    def test_does_not_claim_pages_or_a_media_suffix_outside_the_path(self):
        provider = DirectMediaProvider()
        links = ["http://media.example/watch?v=abc", "http://media.example/video.mp4.html",
                 "http://media.example/get?file=x.mp4", "http://media.example/#clip.mp4",
                 "http://media.example/v/clip.mp4/", "http://media.example/v/clip.mpd",
                 "http://media.example/v/song.mp3", "http://media.example/"]
        for link in links:
            with self.subTest(link=link):
                self.assertFalse(provider.claims(link))


class RequestHeaderTest(unittest.TestCase):
    def test_only_allow_listed_request_headers_pass(self):
        self.assertEqual(allowed_headers({"Referer": PAGE_URL, "Range": "bytes=0-"}),
                         {"Referer": PAGE_URL, "Range": "bytes=0-"})
        for name in ("Cookie", "Authorization", "User-Agent", "X-Forwarded-For"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                allowed_headers({name: "x"})
        with self.assertRaises(ValueError):
            allowed_headers({"Referer": "http://video.example/\r\nCookie: a=b"})


class ContainerSniffTest(unittest.TestCase):
    @staticmethod
    def ftyp(brand: bytes) -> bytes:
        return struct.pack(">I", 24) + b"ftyp" + brand + b"\x00\x00\x02\x00" + b"isomiso2"

    def test_mp4_and_mov_brands(self):
        self.assertEqual(container_of(self.ftyp(b"isom")), "mp4")
        self.assertEqual(container_of(self.ftyp(b"qt  ")), "mov")
        self.assertEqual(container_of(struct.pack(">I", 8) + b"moov" + bytes(8)), "mp4")
        self.assertIsNone(container_of(struct.pack(">I", 8) + b"ftyp"))  # too short to read a brand

    def test_matroska_and_webm_doc_types(self):
        ebml = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08\x42\x82"

        self.assertEqual(container_of(ebml + b"\x88matroska\x42\x87\x81\x04"), "matroska")
        self.assertEqual(container_of(ebml + b"\x84webm\x42\x87\x81\x04"), "webm")

    def test_mpegts_needs_a_sync_byte_every_188_bytes(self):
        packet = b"\x47" + bytes(187)

        self.assertEqual(container_of(packet * 3), "mpegts")
        self.assertEqual(container_of(packet * 2 + b"\x47"), "mpegts")
        self.assertIsNone(container_of(packet * 2))
        self.assertIsNone(container_of(packet + bytes(188) + packet))

    def test_pages_documents_and_playlists_are_not_media(self):
        for sample in (b"<!DOCTYPE html><html><body>clip.mp4</body></html>", b'{"src": "http://cdn.example/x.mp4"}',
                       b"#EXTM3U\n#EXT-X-VERSION:3\n", b"", b"\x00\x00"):
            with self.subTest(sample=sample[:20]):
                self.assertIsNone(container_of(sample))

    def test_content_range_parsing(self):
        self.assertEqual(content_range("bytes 0-1048575/5000000"), (0, 1048575, 5000000))
        self.assertEqual(content_range("bytes 100-199/*"), (100, 199, None))
        self.assertEqual(content_range("BYTES 1-2/3"), (1, 2, 3))
        for value in ("bytes */5000", "", None, "garbage"):
            with self.subTest(value=value):
                self.assertIsNone(content_range(value))

    def test_filename_title_from_content_disposition(self):
        cases = {
            "attachment; filename*=UTF-8''Phim%20hay%20t%E1%BA%ADp%201.mp4": "Phim hay tập 1",
            'attachment; filename="My Clip.mkv"': "My Clip",
            "attachment; filename=clip.final.mp4": "clip.final",
            'attachment; filename="..\\..\\Windows\\evil.mp4"': "evil",
            'attachment; filename="/etc/passwd"': "passwd",
            "attachment; filename=\"fallback.mp4\"; filename*=UTF-8''real%20name.mp4": "real name",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(filename_title(value), expected)
        for value in ("inline", "", 'attachment; filename=".mp4"'):
            with self.subTest(value=value):
                self.assertIsNone(filename_title(value))
        self.assertEqual(filename_title(f'attachment; filename="{"a" * 400}.mp4"'), "a" * MAX_TITLE_LENGTH)


class SourceIdentityTest(unittest.TestCase):
    SIGNED = ("http://Media.Example/v/clip.mp4?token=SECRET1&quality=720&expires=1700000000&Signature=abc"
              "&Policy=p&Key-Pair-Id=k&X-Amz-Signature=s&X-Amz-Date=20260101&x-goog-date=1&hdnts=st%3D1&id=7#t=3")

    @staticmethod
    def source(url: str, size: int | None, **options: Any) -> ResolvedSource:
        return ResolvedSource(provider="direct", transport="http_file", media_url=url,
                              identity={"kind": "file", "url": stable_url(url), "size": size}, title="clip",
                              label="Link file trực tiếp · MP4", **options)

    def test_stable_url_drops_signature_like_query_values_and_sorts_the_rest(self):
        self.assertEqual(stable_url(self.SIGNED), "http://media.example/v/clip.mp4?id=7&quality=720")
        self.assertEqual(stable_url(MEDIA_URL + "?quality=720&id=7"), stable_url(MEDIA_URL + "?id=7&quality=720"))
        self.assertEqual(stable_url("http://media.example/V/Clip.mp4"), "http://media.example/V/Clip.mp4")

    def test_volatile_query_names(self):
        for name in ("token", "access_token", "sig", "Signature", "Policy", "Key-Pair-Id", "Expires", "exp", "e",
                     "st", "t", "ts", "hdnts", "hdnea", "acl", "X-Amz-Credential", "X-Amz-Date", "x-goog-date",
                     "hmac", "nonce", "session_id", "auth"):
            with self.subTest(name=name):
                self.assertTrue(is_volatile_query_name(name))
        for name in ("id", "v", "quality", "file", "format", "itag", "res", "lang"):
            with self.subTest(name=name):
                self.assertFalse(is_volatile_query_name(name))

    def test_volatile_names_match_whole_parts_never_a_word_inside_another(self):
        for name in ("auth_key", "wsSecret", "accessToken", "accesstoken", "sessionid", "md5", "Key-Pair-Id",
                     "upload.signature", "password"):
            with self.subTest(name=name):
                self.assertTrue(is_volatile_query_name(name))
        for name in ("author", "design", "keyframe", "passage", "hashtag", "episode", "season", "monkey"):
            with self.subTest(name=name):
                self.assertFalse(is_volatile_query_name(name))
        self.assertEqual(stable_url(MEDIA_URL + "?author=a&auth_key=x"), MEDIA_URL + "?author=a")

    def test_identity_key_follows_the_size_and_ignores_the_token(self):
        first = self.source(MEDIA_URL + "?token=one", 100)
        fresh = self.source(MEDIA_URL + "?token=two", 100)
        bigger = self.source(MEDIA_URL + "?token=one", 101)

        self.assertEqual(first.identity_key, fresh.identity_key)
        self.assertNotEqual(first.identity_key, bigger.identity_key)
        self.assertRegex(first.identity_key, r"^[0-9a-f]{64}$")
        self.assertEqual(identity_key({"b": 1, "a": 2}), identity_key({"a": 2, "b": 1}))

    def test_describe_change_names_the_changed_fields_in_vietnamese(self):
        old = {"kind": "file", "url": "http://media.example/v/clip.mp4", "size": 100}

        self.assertEqual(describe_change(old, {**old, "size": 200}), "dung lượng")
        self.assertEqual(describe_change(old, {**old, "size": 200, "url": "http://media.example/v/x.mp4"}),
                         "dung lượng, đường dẫn file")
        self.assertEqual(describe_change(None, old), "thiếu thông tin lúc thăm dò")
        self.assertEqual(describe_change(old, dict(old)), "khác dấu nhận diện")
        self.assertEqual(describe_change(old, {**old, "custom": 1}), "custom")
        self.assertNotIn("http", describe_change(old, {**old, "url": "http://media.example/v/x.mp4?token=SECRET"}))

    def test_repr_and_public_never_show_the_media_link_or_headers(self):
        media_url = "http://cdn.example/v/clip.mp4?token=SECRET123&expires=99"
        source = self.source(media_url, 100, headers={"Referer": "http://video.example/watch?v=abc&sig=HEADERSECRET"},
                             plan=FilePlan("mp4", 100, True, '"etag-secret"'))

        text = repr(source)
        public = json.dumps(source.public(), ensure_ascii=False)

        for secret in ("SECRET123", "HEADERSECRET", "token=", "Referer", "etag-secret"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, text)
                self.assertNotIn(secret, public)
        self.assertEqual(source.public(), {
            "provider": "direct", "transport": "http_file", "source_label": "Link file trực tiếp · MP4",
            "identity": source.identity_key, "fragments_total": None,
            "identity_detail": {"kind": "file", "url": "http://cdn.example/v/clip.mp4", "size": 100}})

    def test_unknown_transport_is_refused(self):
        with self.assertRaises(ValueError):
            ResolvedSource(provider="direct", transport="dash", media_url=MEDIA_URL, identity={}, title="x", label="x")

    def test_titles_from_links_and_codec_names_from_hls_attributes(self):
        self.assertEqual(title_from_url("http://media.example/films/My%20Film/index.m3u8"), "My Film")
        self.assertEqual(title_from_url(MEDIA_URL + "?token=x"), "clip")
        self.assertEqual(title_from_url("http://media.example/master.m3u8"), "video")
        self.assertEqual(codec_names("avc1.640028,mp4a.40.2"), ("h264", "aac"))
        self.assertEqual(codec_names("hvc1.1.6.L93.B0,ec-3"), ("hevc", "eac3"))
        self.assertEqual(codec_names("mp4a.40.2"), (None, "aac"))
        self.assertEqual(codec_names(None), (None, None))


class LogMaskingTest(unittest.TestCase):
    def test_log_buffer_masks_tokens_and_long_secrets_before_on_log(self):
        lines: list[str] = []
        log = LogBuffer(lines.extend, flush_seconds=0.0)
        raw = ["GET http://cdn.example/v/clip.mp4?token=SECRET123&quality=720",
               "key " + "QUJD" * 15,
               "Authorization: Bearer abc.def"]

        for line in raw:
            log(line)
        log.flush()

        joined = "\n".join(lines)
        self.assertNotIn("SECRET123", joined)
        self.assertNotIn("QUJD" * 15, joined)
        self.assertNotIn("Bearer", joined)
        self.assertEqual(lines, ["GET http://cdn.example/v/clip.mp4?token=***&quality=720", "key ***",
                                 "Authorization: ***"])
        self.assertEqual(lines, [mask_line(line) for line in raw])

    def test_log_buffer_holds_lines_until_it_is_flushed(self):
        batches: list[list[str]] = []
        log = LogBuffer(batches.append, flush_seconds=3600.0)
        log.flush()  # nothing pending: no call, starts the interval

        log("một")
        log("hai")
        self.assertEqual(batches, [])
        log.flush()

        self.assertEqual(batches, [["một", "hai"]])


class ProgressMeterTest(unittest.TestCase):
    """The speed is the bytes of the last SPEED_WINDOW_SECONDS over their span (a running sum since pieces
    became small); a stage clears it."""

    def setUp(self):
        self.now = [0.0]
        self.reports: list[Progress] = []
        self.meter = ProgressMeter(self.reports.append, clock=lambda: self.now[0], emit_seconds=0.0)

    def advance_at(self, moment: float, size: int) -> None:
        self.now[0] = moment
        self.meter.advance("file", size)

    def speed_at(self, moment: float) -> float | None:
        self.now[0] = moment
        return self.meter.snapshot().speed

    def test_speed_is_the_bytes_of_the_window_over_its_span(self):
        self.advance_at(0.0, 1000)
        self.assertIsNone(self.speed_at(0.5))  # one piece: no span yet
        self.advance_at(1.0, 3000)

        self.assertEqual(self.speed_at(2.0), 4000 / 2.0)
        self.assertEqual(self.meter.snapshot().downloaded_bytes, 4000)
        self.assertEqual(self.reports[-1].downloaded_bytes, 4000)

    def test_pieces_older_than_the_window_leave_the_speed(self):
        self.advance_at(0.0, 1000)
        self.advance_at(1.0, 3000)
        self.advance_at(6.5, 500)  # both earlier pieces are now outside the window
        self.advance_at(7.5, 1500)

        self.assertEqual(self.speed_at(8.5), (500 + 1500) / 2.0)
        self.assertIsNone(self.speed_at(20.0))  # everything expired
        self.assertEqual(self.meter.snapshot().downloaded_bytes, 6000)  # the bytes stay counted

    def test_a_stage_clears_the_window_and_its_sum(self):
        self.advance_at(0.0, 10_000)
        self.advance_at(1.0, 10_000)
        self.meter.stage("remuxing")
        self.assertIsNone(self.speed_at(1.5))  # no speed while joining
        self.meter.stage("downloading")

        self.advance_at(2.0, 100)
        self.advance_at(3.0, 100)

        self.assertEqual(self.speed_at(4.0), 200 / 2.0)


# ------------------------------------------------------------------------------ fixture server + FFmpeg
@dataclass(frozen=True)
class Run:
    outcome: DownloadOutcome
    progress: list[Progress]
    logs: list[str]
    starts: list[int]


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class ServerCase(unittest.TestCase):
    def setUp(self):
        self.server = FixtureServer()
        self.addCleanup(self.server.close)
        self.http = self.server.http()
        self.root = scratch_dir()
        self.provider = DirectMediaProvider()

    def serve(self, body: bytes | None = None, path: str = MEDIA_PATH, **options: Any) -> None:
        options.setdefault("content_type", "video/mp4")
        options.setdefault("etag", ETAG)
        self.server.route(path, Reply(MEDIA["big"] if body is None else body, **options))

    def task(self, name: str = "task") -> Path:
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def context(self, task_dir: Path, *, probing: bool = True) -> ResolveContext:
        return ResolveContext(http=self.http, control=ProcessControl(), task_dir=task_dir,
                              ffprobe=FFPROBE if probing else None)

    def resolve(self, url: str = MEDIA_URL, task_dir: Path | None = None) -> ResolvedSource:
        return self.provider.resolve(url, self.context(task_dir or self.task()))

    def download(self, source: ResolvedSource, task_dir: Path, *, refresh: Callable[[], ResolvedSource] | None = None,
                 control: ProcessControl | None = None, guard: SizeGuard | None = None, retries: int | None = None,
                 on_start: Callable[[int, float], None] | None = None) -> Run:
        transfers = SourceTransfers(self.http, ffmpeg=FFMPEG, ffprobe=FFPROBE)
        if retries is not None:
            transfers.files.retries = retries
        progress: list[Progress] = []
        logs: list[str] = []
        starts: list[int] = []

        def started(pid: int, created: float) -> None:
            starts.append(pid)
            if on_start is not None:
                on_start(pid, created)
        outcome = transfers.download(source, refresh or no_refresh, task_dir, control or ProcessControl(),
                                     on_progress=progress.append, on_log=logs.extend, on_start=started, guard=guard)
        return Run(outcome, progress, logs, starts)

    def assert_no_cookie_was_sent(self) -> None:
        for request in self.server.requests:
            self.assertNotIn("cookie", request.headers)


class ResolveFileTest(ServerCase):
    def test_mp4_probe_reads_only_the_first_sniff_bytes(self):
        body = MEDIA["big"]
        self.serve(body)
        task = self.task()

        source = self.resolve(MEDIA_URL, task)

        [request] = self.server.seen(MEDIA_PATH)
        self.assertEqual(request.headers["range"], "bytes=0-1048575")
        self.assertGreater(len(body), SNIFF_BYTES)
        self.assertEqual((source.provider, source.transport), ("direct", "http_file"))
        self.assertEqual(source.estimated_bytes, len(body))  # Content-Range total; the answer itself was 1 MiB
        self.assertEqual(source.identity, {"kind": "file", "url": MEDIA_URL, "size": len(body)})
        self.assertEqual((source.video_codec, source.audio_codec), ("h264", "aac"))
        self.assertEqual((source.width, source.height), (160, 120))
        self.assertAlmostEqual(source.duration_seconds, CLIP_SECONDS, delta=0.3)
        self.assertEqual(source.plan, FilePlan("mp4", len(body), True, ETAG))
        self.assertEqual((source.title, source.label), ("clip", "Link file trực tiếp · MP4"))
        self.assertEqual(list(task.iterdir()), [])  # the sniffed sample is removed
        self.assertEqual(set(self.server.connections), {(PUBLIC_ADDRESS, 80)})

    def test_weak_etag_is_not_a_validator_and_last_modified_is_the_fallback(self):
        modified = "Tue, 06 Oct 2026 08:00:00 GMT"
        cases = [({"etag": 'W/"weak"', "headers": {"Last-Modified": modified}}, modified),
                 ({"etag": 'W/"weak"'}, None),
                 ({"etag": None}, None),
                 ({"etag": '"strong"', "headers": {"Last-Modified": modified}}, '"strong"')]
        for options, expected in cases:
            with self.subTest(options=options):
                self.serve(MEDIA["mp4"], **options)
                self.assertEqual(self.resolve().plan.validator, expected)

    def test_title_comes_from_content_disposition(self):
        self.serve(MEDIA["mp4"], headers={"Content-Disposition": "attachment; filename*=UTF-8''T%E1%BA%ADp%201.mp4"})

        self.assertEqual(self.resolve().title, "Tập 1")

    def test_real_clips_sniff_as_their_containers(self):
        expected = {"mp4": "mp4", "big": "mp4", "mkv": "matroska", "ts": "mpegts"}
        for name, container in expected.items():
            with self.subTest(name=name):
                self.assertEqual(container_of(MEDIA[name][:SNIFF_BYTES]), container)

    def test_mp4_without_audio_or_without_video_fails_before_the_download(self):
        for name, code in (("video_only", "NO_AUDIO_STREAM"), ("audio_only", "NO_VIDEO_STREAM")):
            with self.subTest(name=name):
                self.serve(MEDIA[name])
                with self.assertRaises(SourceError) as caught:
                    self.resolve()
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.server.count(MEDIA_PATH), 2)  # one sniff each, never the whole file

    def test_page_or_document_behind_a_media_link_is_declined(self):
        for body, kind in ((b"<!doctype html><title>clip</title>", "text/html; charset=utf-8"),
                           (b'{"error": "not here"}', "application/json")):
            with self.subTest(kind=kind):
                self.serve(body, content_type=kind)
                with self.assertRaises(SourceDeclined) as caught:
                    self.resolve()
                self.assertIn(kind.split(";")[0], caught.exception.message)

    def test_playlist_behind_a_media_link_goes_to_the_hls_resolver(self):
        playlist = media_playlist(["/hls/clip.ts"], [float(CLIP_SECONDS)]).encode()
        self.server.route("/hls/clip.ts", Reply(MEDIA["ts"], content_type="video/mp2t"))
        cases = [("body", playlist, "application/octet-stream"),
                 ("content type", b"\n" + playlist, "application/vnd.apple.mpegurl")]
        for name, body, kind in cases:
            with self.subTest(name=name):
                self.serve(body, content_type=kind, etag=None)
                with self.assertRaises(PlaylistLink):
                    resolve_file(MEDIA_URL, self.context(self.task()), provider="direct", label="x")
                source = self.resolve()
                self.assertEqual((source.transport, source.fragments), ("hls", 1))
                self.assertEqual(source.label, "Link HLS trực tiếp")
                self.assertEqual((source.video_codec, source.audio_codec), ("h264", "aac"))

    def test_partial_answer_that_does_not_start_at_byte_zero_is_a_bad_response(self):
        head = MEDIA["mp4"][:4096]
        for content_range_value in (f"bytes 100-{100 + len(head) - 1}/{len(MEDIA['mp4'])}", None):
            with self.subTest(content_range=content_range_value):
                headers = {"Content-Range": content_range_value} if content_range_value else {}
                self.serve(head, status=206, ranges=False, headers=headers)
                with self.assertRaises(SourceError) as caught:
                    self.resolve()
                self.assertEqual(caught.exception.code, "BAD_RESPONSE")
                self.assertNotIn("clip.mp4", caught.exception.message)  # the host only, never the link


class SiteProviderTest(ServerCase):
    def setUp(self):
        super().setUp()
        self.site = ExampleSiteProvider(HostList(["video.example"]))

    def player(self, kind: str, src: str) -> None:
        body = json.dumps({"format": kind, "src": src}).encode("utf-8")
        self.server.route(PLAYER_PATH, Reply(body, content_type="application/json"))

    def test_site_provider_reads_player_data_and_sends_its_referer_origin_with_every_media_request(self):
        self.player("file", "http://cdn.example/v/abc.mp4")
        self.serve(path="/v/abc.mp4")
        task = self.task()

        source = self.site.resolve(PAGE_URL, self.context(task))
        run = self.download(source, task)

        [player] = self.server.seen(PLAYER_PATH)
        self.assertEqual((player.host, player.query), ("video.example", {"id": ["abc"]}))
        self.assertEqual((source.provider, source.transport, source.label), ("example-site", "http_file", "Trang mẫu · MP4"))
        self.assertEqual(dict(source.headers), {"Referer": PAGE_URL})
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])
        media_requests = self.server.seen("/v/abc.mp4")
        self.assertEqual(len(media_requests), 2)  # the sniff, then the transfer
        for request in media_requests:
            self.assertEqual((request.host, request.headers.get("referer")), ("cdn.example", PAGE_ORIGIN))
        self.assert_no_cookie_was_sent()

    def test_site_provider_can_hand_an_hls_player_link_to_the_hls_resolver(self):
        self.player("hls", "http://cdn.example/hls/abc/index.m3u8")
        self.server.route("/hls/abc/index.m3u8", Reply(media_playlist(["seg-1.ts"], [float(CLIP_SECONDS)]).encode(),
                                                       content_type="application/vnd.apple.mpegurl"))
        self.server.route("/hls/abc/seg-1.ts", Reply(MEDIA["ts"], content_type="video/mp2t"))

        source = self.site.resolve(PAGE_URL, self.context(self.task()))

        self.assertEqual((source.provider, source.transport, source.fragments), ("example-site", "hls", 1))
        self.assertEqual((source.video_codec, source.audio_codec), ("h264", "aac"))
        for path in ("/hls/abc/index.m3u8", "/hls/abc/seg-1.ts"):
            with self.subTest(path=path):
                [request] = self.server.seen(path)
                self.assertEqual(request.headers.get("referer"), PAGE_ORIGIN)

    def test_a_provider_cannot_send_a_cookie_header(self):
        self.player("file", "http://cdn.example/v/abc.mp4")
        self.serve(path="/v/abc.mp4")
        site = CookieSiteProvider(HostList(["video.example"]))

        with self.assertRaises(ValueError) as caught:
            site.resolve(PAGE_URL, self.context(self.task()))

        self.assertIn("Cookie", str(caught.exception))
        self.assertEqual(self.server.count("/v/abc.mp4"), 0)
        self.assert_no_cookie_was_sent()


class FileTransferTest(ServerCase):
    def test_full_mp4_download_is_byte_identical_with_bytes_progress(self):
        body = MEDIA["big"]
        self.serve(body)
        task = self.task()
        source = self.resolve(MEDIA_URL, task)

        run = self.download(source, task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(run.outcome.final_path, task / "media.mp4")
        self.assertEqual(run.outcome.final_path.read_bytes(), body)
        done = json.loads((task / FINISHED_NAME).read_text(encoding="utf-8"))
        self.assertEqual(done, {"identity": source.identity_key, "name": "media.mp4", "size": len(body)})
        self.assertFalse((task / PART_NAME).exists())
        self.assertFalse((task / STATE_NAME).exists())
        self.assertNotIn("range", self.server.seen(MEDIA_PATH)[-1].headers)
        last = run.progress[-1]
        self.assertEqual((last.basis, last.stage), ("bytes", "downloading"))
        self.assertEqual((last.downloaded_bytes, last.total_bytes), (len(body), len(body)))
        done_bytes = [item.downloaded_bytes for item in run.progress]
        self.assertEqual(done_bytes, sorted(done_bytes))
        self.assertLessEqual(max(done_bytes), len(body))

    def test_mkv_keeps_its_container(self):
        self.serve(MEDIA["mkv"], path="/v/clip.mkv", content_type="video/x-matroska")
        task = self.task()
        source = self.resolve("http://media.example/v/clip.mkv", task)

        run = self.download(source, task)

        self.assertEqual(source.label, "Link file trực tiếp · MKV")
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(run.outcome.final_path, task / "media.mkv")
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["mkv"])
        self.assertEqual(run.starts, [])  # no FFmpeg for a container that is kept

    def test_mpegts_file_is_copied_into_mp4_and_its_part_removed(self):
        self.serve(MEDIA["ts"], path=TS_PATH, content_type="video/mp2t")
        task = self.task()
        source = self.resolve(TS_URL, task)

        run = self.download(source, task)

        self.assertEqual((source.plan.container, source.label), ("mpegts", "Link file trực tiếp · MPEG-TS"))
        self.assertIsNone(source.video_codec)  # a TS stream list may start after the sample: checked by the remux
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(run.outcome.final_path, task / "media.mp4")
        summary = stream_summary(probe_streams(FFPROBE, run.outcome.final_path))
        self.assertEqual((summary["video_codec"], summary["audio_codec"]), ("h264", "aac"))
        self.assertFalse((task / PART_NAME).exists())
        self.assertFalse((task / "media.mp4.part").exists())
        self.assertEqual(len(run.starts), 1)
        self.assertEqual((run.progress[-1].stage, run.progress[-1].downloaded_bytes), ("remuxing", len(MEDIA["ts"])))

    def test_mpegts_file_without_audio_is_refused_before_the_remux(self):
        self.serve(MEDIA["ts_video_only"], path=TS_PATH, content_type="video/mp2t")
        task = self.task()
        source = self.resolve(TS_URL, task)

        run = self.download(source, task)

        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "NO_AUDIO_STREAM")
        self.assertFalse((task / "media.mp4").exists())

    def test_server_without_range_support_downloads_from_byte_zero(self):
        body = MEDIA["big"]
        self.serve(body, ranges=False, etag=None)
        task = self.task()
        source = self.resolve(MEDIA_URL, task)

        run = self.download(source, task)

        self.assertEqual(source.plan, FilePlan("mp4", len(body), False, None))
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(run.outcome.final_path.read_bytes(), body)


class ResumeTest(ServerCase):
    def cut_first_attempt(self, task: Path, **options: Any) -> tuple[ResolvedSource, Run, int]:
        """Resolve, then a first transfer whose connection drops at half the file (no retry)."""
        self.serve(**options)
        source = self.resolve(MEDIA_URL, task)
        cut = len(MEDIA["big"]) // 2
        self.serve(cut_after=cut, **options)
        return source, self.download(source, task, retries=0), cut

    def test_cut_transfer_ends_resumable_and_continues_with_range_and_if_range(self):
        task = self.task()
        source, first, cut = self.cut_first_attempt(task)

        self.assertFalse(first.outcome.ok)
        self.assertEqual(first.outcome.code, "NETWORK")
        self.assertTrue(first.outcome.resumable)
        self.assertIn(first.outcome.code, RESUMABLE_CODES)
        self.assertEqual((task / PART_NAME).stat().st_size, cut)
        self.assertEqual(json.loads((task / STATE_NAME).read_text(encoding="utf-8")),
                         {"identity": source.identity_key, "validator": ETAG})
        self.assertEqual(first.progress[-1].downloaded_bytes, cut)
        self.assertFalse((task / "media.mp4").exists())

        self.serve()
        before = self.server.count(MEDIA_PATH)
        second = self.download(source, task)

        self.assertTrue(second.outcome.ok, second.outcome)
        self.assertEqual(self.server.count(MEDIA_PATH), before + 1)
        request = self.server.seen(MEDIA_PATH)[before]
        self.assertEqual((request.headers["range"], request.headers["if-range"]), (f"bytes={cut}-", ETAG))
        self.assertEqual(second.outcome.final_path.read_bytes(), MEDIA["big"])
        self.assertTrue(any(f"Tải nối từ {cut} byte" in line for line in second.logs), second.logs)
        self.assertEqual(second.progress[-1].downloaded_bytes, len(MEDIA["big"]))

    def test_changed_etag_makes_the_server_resend_the_file_and_the_transfer_restarts(self):
        task = self.task()
        source, first, cut = self.cut_first_attempt(task)
        self.assertEqual(first.outcome.code, "NETWORK")

        self.serve(etag='"v2"')
        second = self.download(source, task)

        request = self.server.seen(MEDIA_PATH)[-1]
        self.assertEqual((request.headers["range"], request.headers["if-range"]), (f"bytes={cut}-", ETAG))
        self.assertTrue(second.outcome.ok, second.outcome)
        self.assertEqual(second.outcome.final_path.read_bytes(), MEDIA["big"])
        self.assertTrue(any("tải lại từ đầu" in line for line in second.logs), second.logs)

    def test_a_part_without_a_validator_is_not_continued_by_a_later_run(self):
        task = self.task()
        source, first, _cut = self.cut_first_attempt(task, ranges=False, etag=None)
        self.assertTrue(first.outcome.resumable)

        self.serve(ranges=False, etag=None)
        second = self.download(source, task)

        request = self.server.seen(MEDIA_PATH)[-1]
        self.assertNotIn("range", request.headers)  # a replaced file of the same size could not be told apart
        self.assertTrue(second.outcome.ok, second.outcome)
        self.assertEqual(second.outcome.final_path.read_bytes(), MEDIA["big"])
        self.assertTrue(any("tải lại từ đầu" in line for line in second.logs), second.logs)

    def test_server_ignoring_range_on_resume_restarts_from_byte_zero(self):
        task = self.task()
        source, first, _cut = self.cut_first_attempt(task)
        self.assertTrue(first.outcome.resumable)

        self.serve(ranges=False)
        second = self.download(source, task)

        request = self.server.seen(MEDIA_PATH)[-1]
        self.assertIn("range", request.headers)
        self.assertEqual(request.headers.get("if-range"), ETAG)
        self.assertTrue(second.outcome.ok, second.outcome)
        self.assertEqual(second.outcome.final_path.read_bytes(), MEDIA["big"])
        self.assertTrue(any("tải lại từ đầu" in line for line in second.logs), second.logs)

    def test_dropped_connection_is_retried_within_the_same_run(self):
        task = self.task()
        self.serve()
        source = self.resolve(MEDIA_URL, task)
        cut = len(MEDIA["big"]) // 3
        self.server.route(MEDIA_PATH, replies(Reply(MEDIA["big"], content_type="video/mp4", etag=ETAG, cut_after=cut),
                                              Reply(MEDIA["big"], content_type="video/mp4", etag=ETAG)))

        run = self.download(source, task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])
        transfer_requests = self.server.seen(MEDIA_PATH)[1:]
        self.assertEqual(len(transfer_requests), 2)
        self.assertEqual(transfer_requests[1].headers["range"], f"bytes={cut}-")
        self.assertTrue(any(f"Thử lại 1/{FILE_RETRIES}" in line for line in run.logs), run.logs)

    def test_body_longer_than_the_probed_size_is_a_changed_source(self):
        task = self.task()
        self.serve()
        source = self.resolve(MEDIA_URL, task)
        self.serve(MEDIA["big"] + bytes(100_000))

        run = self.download(source, task)

        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "SOURCE_CHANGED")  # its Content-Length, before a byte is written
        self.assertFalse(run.outcome.resumable)
        self.assertFalse((task / "media.mp4").exists())
        self.assertFalse((task / PART_NAME).exists())  # refused before the first byte was written

    def test_resume_answer_with_another_total_size_is_a_changed_source(self):
        task = self.task()
        source, first, _cut = self.cut_first_attempt(task)
        self.assertEqual(first.outcome.code, "NETWORK")

        self.serve(MEDIA["big"] + bytes(100_000))
        second = self.download(source, task)

        self.assertFalse(second.outcome.ok)
        self.assertEqual(second.outcome.code, "SOURCE_CHANGED")
        self.assertIn("dung lượng", second.outcome.message)
        self.assertFalse(second.outcome.resumable)
        self.assertFalse((task / "media.mp4").exists())

    def test_body_shorter_than_the_probed_size_is_never_taken_as_complete(self):
        task = self.task()
        self.serve()
        source = self.resolve(MEDIA_URL, task)
        self.serve(MEDIA["big"][:-100_000])

        run = self.download(source, task)

        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "SOURCE_CHANGED")  # not a dropped connection retried into HTTP 416
        self.assertFalse(run.outcome.resumable)
        self.assertFalse((task / "media.mp4").exists())
        self.assertFalse((task / FINISHED_NAME).exists())


class RefreshTest(ServerCase):
    """A 403 asks for one fresh source; another one is only allowed after the part grew since the last attempt."""

    def setUp(self):
        super().setUp()
        self.lock = threading.Lock()
        self.valid: dict[str, list[Reply]] = {"old": [self.whole()]}  # token -> its answers in order; others: 403

        def route(seen: Seen, _number: int) -> Reply:
            with self.lock:
                answers = self.valid.get(seen.query.get("token", [None])[0])
                if not answers:
                    return Reply(b"expired", 403, "text/plain")
                return answers.pop(0) if len(answers) > 1 else answers[0]
        self.server.route(MEDIA_PATH, route)

    @staticmethod
    def whole(**options: Any) -> Reply:
        return Reply(MEDIA["big"], content_type="video/mp4", etag=ETAG, **options)

    def expire(self, valid: dict[str, list[Reply]]) -> None:
        with self.lock:
            self.valid = valid

    def refresher(self, source: ResolvedSource, tokens: list[str]) -> tuple[Callable[[], ResolvedSource], list[str]]:
        """``refresh`` giving ``source`` with the next token of ``tokens``, and the tokens it gave."""
        given: list[str] = []

        def refresh() -> ResolvedSource:
            given.append(tokens[len(given)])
            return dataclasses.replace(source, media_url=f"{MEDIA_URL}?token={given[-1]}")
        return refresh, given

    def test_forbidden_answer_refreshes_the_source_once_then_succeeds(self):
        task = self.task()
        source = self.resolve(MEDIA_URL + "?token=old", task)
        self.expire({"new": [self.whole()]})
        calls: list[ResolvedSource] = []

        def refresh() -> ResolvedSource:
            fresh = self.provider.resolve(MEDIA_URL + "?token=new", self.context(task, probing=False))
            calls.append(fresh)
            return fresh

        run = self.download(source, task, refresh=refresh)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].identity_key, source.identity_key)  # the token is not part of the identity
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])
        self.assertEqual(self.server.seen(MEDIA_PATH)[-1].query["token"], ["new"])
        self.assertTrue(any("lấy lại nguồn" in line for line in run.logs), run.logs)
        for line in run.logs:
            self.assertNotIn("token", line)

    def test_refreshed_source_still_forbidden_at_once_fails_without_a_resume(self):
        task = self.task()
        source = self.resolve(MEDIA_URL + "?token=old", task)
        self.expire({})
        refresh, given = self.refresher(source, ["new"])

        run = self.download(source, task, refresh=refresh)

        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "FORBIDDEN")
        self.assertFalse(run.outcome.resumable)
        self.assertEqual(given, ["new"])
        self.assertIn("media.example", run.outcome.message)
        self.assertNotIn("token", run.outcome.message)

    def test_part_that_grew_since_the_last_refresh_allows_another_refresh(self):
        task = self.task()
        source = self.resolve(MEDIA_URL + "?token=old", task)
        cut = len(MEDIA["big"]) // 3
        forbidden = Reply(b"expired", 403, "text/plain")
        # "b": bytes arrive, the connection drops, then the link has expired; "c" serves the rest.
        self.expire({"b": [self.whole(cut_after=cut), forbidden], "c": [self.whole()]})
        refresh, given = self.refresher(source, ["b", "c"])

        run = self.download(source, task, refresh=refresh)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(given, ["b", "c"])
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])
        last = self.server.seen(MEDIA_PATH)[-1]
        self.assertEqual(last.query["token"], ["c"])
        self.assertEqual((last.headers["range"], last.headers["if-range"]), (f"bytes={cut}-", ETAG))
        self.assertEqual(sum("lấy lại nguồn" in line for line in run.logs), 2, run.logs)
        self.assertTrue(any(f"Thử lại 1/{FILE_RETRIES}" in line for line in run.logs), run.logs)

    def test_refreshes_stay_bounded_when_the_part_stops_growing(self):
        task = self.task()
        source = self.resolve(MEDIA_URL + "?token=old", task)
        cut = len(MEDIA["big"]) // 3
        self.expire({"b": [self.whole(cut_after=cut), Reply(b"expired", 403, "text/plain")]})
        refresh, given = self.refresher(source, ["b", "c", "d"])

        run = self.download(source, task, refresh=refresh)

        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "FORBIDDEN")
        self.assertFalse(run.outcome.resumable)
        self.assertEqual(given, ["b", "c"])  # "c" is refused at once: no third refresh
        self.assertEqual((task / PART_NAME).stat().st_size, cut)


class FinishedMarkerTest(ServerCase):
    def finished_download(self) -> tuple[Path, ResolvedSource, Run]:
        self.serve()
        task = self.task()
        source = self.resolve(MEDIA_URL, task)
        run = self.download(source, task)
        self.assertTrue(run.outcome.ok, run.outcome)
        return task, source, run

    def test_finished_download_is_reused_without_a_new_request(self):
        task, source, first = self.finished_download()
        requests = len(self.server.requests)

        second = self.download(source, task)

        self.assertTrue(second.outcome.ok, second.outcome)
        self.assertEqual(second.outcome.final_path, first.outcome.final_path)
        self.assertEqual(len(self.server.requests), requests)
        self.assertTrue(any("Dùng lại media.mp4" in line for line in second.logs), second.logs)
        self.assertEqual(second.progress[-1].downloaded_bytes, len(MEDIA["big"]))

    def test_another_identity_or_a_changed_file_is_fetched_again(self):
        task, source, _first = self.finished_download()
        other = dataclasses.replace(source, identity={**source.identity, "url": "http://media.example/v/other.mp4"})
        before = self.server.count(MEDIA_PATH)

        run = self.download(other, task)

        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(self.server.count(MEDIA_PATH), before + 1)
        self.assertEqual(run.outcome.final_path.read_bytes(), MEDIA["big"])
        done = json.loads((task / FINISHED_NAME).read_text(encoding="utf-8"))
        self.assertEqual(done["identity"], other.identity_key)

        with run.outcome.final_path.open("ab") as handle:
            handle.write(b"x")
        before = self.server.count(MEDIA_PATH)
        again = self.download(other, task)

        self.assertTrue(again.outcome.ok, again.outcome)
        self.assertEqual(self.server.count(MEDIA_PATH), before + 1)
        self.assertEqual(again.outcome.final_path.read_bytes(), MEDIA["big"])


class StopAndLimitTest(ServerCase):
    def slow_source(self, task: Path) -> ResolvedSource:
        self.serve()
        source = self.resolve(MEDIA_URL, task)
        self.serve(chunk=32 * 1024, delay=0.02)  # about 1.4 s for the whole file
        return source

    def start(self, source: ResolvedSource, task: Path, control: ProcessControl) -> tuple[threading.Thread, dict]:
        result: dict[str, Any] = {}

        def work() -> None:
            try:
                result["run"] = self.download(source, task, control=control)
            except BaseException as error:  # noqa: BLE001 - reported by the test
                result["error"] = error
        thread = threading.Thread(target=work, name="test-download", daemon=True)
        thread.start()
        return thread, result

    def finish(self, thread: threading.Thread, result: dict) -> Run:
        thread.join(15)
        self.assertFalse(thread.is_alive(), "download() did not return")
        self.assertNotIn("error", result)
        return result["run"]

    def test_size_guard_byte_limit_ends_the_transfer_as_too_large(self):
        self.serve()
        task = self.task()
        source = self.resolve(MEDIA_URL, task)
        guard = SizeGuard(task, max_bytes=512 * 1024, max_disk_bytes=None)

        run = self.download(source, task, guard=guard)

        self.assertFalse(run.outcome.ok)
        self.assertEqual((run.outcome.code, run.outcome.message), ("TOO_LARGE", guard.message()))
        self.assertFalse(run.outcome.resumable)
        self.assertTrue(guard.tripped)
        self.assertFalse((task / "media.mp4").exists())

    def test_size_guard_disk_limit_aborts_the_open_transfer_as_too_large(self):
        task = self.task()
        source = self.slow_source(task)
        guard = SizeGuard(task, max_bytes=None, max_disk_bytes=600 * 1024, interval=0.05)

        run = self.download(source, task, guard=guard)

        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "TOO_LARGE")
        self.assertTrue(guard.tripped)
        self.assertLess((task / PART_NAME).stat().st_size, len(MEDIA["big"]))

    def test_stop_keeps_the_part_and_a_later_run_continues_it(self):
        task = self.task()
        source = self.slow_source(task)
        control = ProcessControl()
        part = task / PART_NAME
        thread, result = self.start(source, task, control)
        self.assertTrue(wait_for(lambda: part.is_file() and part.stat().st_size > 0))

        control.request("stop")
        stopped = self.finish(thread, result)

        self.assertFalse(stopped.outcome.ok)
        self.assertEqual((stopped.outcome.code, stopped.outcome.message), stop_reason(control))
        self.assertEqual(stopped.outcome.code, "STOPPED")
        kept = part.stat().st_size
        self.assertTrue(0 < kept < len(MEDIA["big"]))
        self.assertTrue((task / STATE_NAME).is_file())

        self.serve()
        resumed = self.download(source, task)

        self.assertTrue(resumed.outcome.ok, resumed.outcome)
        self.assertEqual(self.server.seen(MEDIA_PATH)[-1].headers["range"], f"bytes={kept}-")
        self.assertEqual(resumed.outcome.final_path.read_bytes(), MEDIA["big"])

    def test_cancel_leaves_no_writer_behind_and_the_task_folder_can_be_removed(self):
        task = self.task()
        source = self.slow_source(task)
        control = ProcessControl()
        part = task / PART_NAME
        thread, result = self.start(source, task, control)
        self.assertTrue(wait_for(lambda: part.is_file() and part.stat().st_size > 0))

        control.request("cancel")
        cancelled = self.finish(thread, result)
        sizes = {path.name: path.stat().st_size for path in task.rglob("*") if path.is_file()}
        time.sleep(0.5)

        self.assertEqual(cancelled.outcome.code, "CANCELLED")
        self.assertEqual({path.name: path.stat().st_size for path in task.rglob("*") if path.is_file()}, sizes)
        shutil.rmtree(task)
        self.assertFalse(task.exists())

    def test_stop_during_the_mpegts_remux_is_finished_by_the_next_run(self):
        self.serve(MEDIA["ts"], path=TS_PATH, content_type="video/mp2t")
        task = self.task()
        source = self.resolve(TS_URL, task)
        control = ProcessControl()

        first = self.download(source, task, control=control, on_start=lambda pid, created: control.request("stop"))

        self.assertEqual(first.outcome.code, "STOPPED")
        self.assertEqual((task / PART_NAME).stat().st_size, len(MEDIA["ts"]))  # every byte had arrived
        second = self.download(source, task)
        # A complete part must be remuxed again, not asked for with "Range: bytes=<size>-" (HTTP 416).
        self.assertTrue(second.outcome.ok, second.outcome)
        self.assertEqual(second.outcome.final_path, task / "media.mp4")


if __name__ == "__main__":
    unittest.main()
