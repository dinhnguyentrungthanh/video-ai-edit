"""HLS links fetched by BiliFlow itself (``download_hls``): playlist policy, variant choice, parallel segments
joined in playlist order, stop/resume, expired links, retries and the size guard.

Every link is a ``.example`` link served by tests/source_fixtures.FixtureServer on 127.0.0.1 through the
policy-checked client; the segments are MPEG-TS made once per module by the project's FFmpeg from a test
pattern. Nothing reaches the network, a real site or the project's Control Center, state, input or output.
The parser and policy tests need no FFmpeg.
"""
from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import shutil
import subprocess
import threading
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence
from unittest import mock

from biliflow import download_hls, download_http
from biliflow.download_hls import (
    MANIFEST_NAME,
    MAX_REFRESHES,
    PROBE_SEGMENT_NAME,
    SEG_DIR,
    SEGMENT_RETRIES,
    TS_PACKET,
    MasterPlaylist,
    MediaPlaylist,
    SegmentManifest,
    TsChecker,
    parse_playlist,
    resolve_hls,
    segment_name,
    select_variant,
    variant_key,
)
from biliflow.download_http import HttpError
from biliflow.download_runner import DownloadOutcome, ProcessControl, Progress, SizeGuard
from biliflow.download_source_types import (
    ResolveContext,
    ResolvedSource,
    SourceChanged,
    SourceDeclined,
    SourceError,
    describe_change,
    identity_key,
    stable_url,
)
from biliflow.download_sources import DirectMediaProvider, SourceTransfers
from biliflow.download_transfer import remux_to_mp4
from tests.source_fixtures import (
    CREATE_NO_WINDOW,
    FFMPEG,
    FFPROBE,
    HAVE_FFMPEG,
    NEED_FFMPEG,
    FixtureServer,
    HlsMedia,
    Reply,
    make_hls,
    master_playlist,
    media_playlist,
    remove_tree,
    segment_route,
    temp_root,
)

URL = "http://media.example/show/index.m3u8"
MASTER_URL = "http://media.example/show/master.m3u8"
PLAYLIST_PATH = "/show/index.m3u8"
MASTER_PATH = "/show/master.m3u8"
HLS_TYPE = "application/vnd.apple.mpegurl"
LABEL = "Link HLS trực tiếp"
H264_AAC = "avc1.640028,mp4a.40.2"
HTML = b"<!DOCTYPE html><html><body>Access denied</body></html>"
SLOW_DELAY = 0.5  # seconds after each chunk: a slow segment takes several seconds
SLOW_CHUNK = 2048
STOP_SECONDS = 3.0  # a stop, cancel or size trip shuts the open sockets: far less than one slow segment (~7 s)
VIDEO_FRAMES = 150  # 6 s of a 25 fps test pattern
FAST_RETRIES = functools.partial(download_http.with_retries, base_delay=0.01, max_delay=0.02)
MEDIA: dict[str, Any] = {}


# ------------------------------------------------------------------------------------------ helpers
def packet_hashes(path: Path) -> list[str]:
    """FFmpeg's framemd5 of every video and audio packet (stream, dts, pts, duration, size, MD5), copied."""
    completed = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:v", "-map", "0:a",
         "-c", "copy", "-f", "framemd5", "-"],
        capture_output=True, check=True, timeout=120, creationflags=CREATE_NO_WINDOW)
    text = completed.stdout.decode("ascii", "replace")
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def reference_hashes(folder: Path, media: HlsMedia) -> list[str]:
    """The packets of the segments joined in playlist order by the same remux the transfer uses."""
    folder.mkdir(parents=True)
    parts = []
    for index, data in enumerate(media.segments, start=1):
        part = folder / segment_name(index)
        part.write_bytes(data)
        parts.append(part)
    output = folder / "reference.mp4"
    remux_to_mp4(FFMPEG, parts, output, ProcessControl())
    return packet_hashes(output)


def setUpModule() -> None:
    if not HAVE_FFMPEG:
        return
    base = temp_root("biliflow-hls-media-")
    MEDIA["base"] = base
    MEDIA["hls"] = make_media(base / "hls", seconds=6)
    MEDIA["video_only"] = make_media(base / "video-only", seconds=2, audio=False)
    MEDIA["audio_only"] = make_media(base / "audio-only", seconds=2, video=False)
    MEDIA["reference"] = reference_hashes(base / "reference", MEDIA["hls"])


def make_media(folder: Path, **options: Any) -> HlsMedia:
    return make_hls(folder, size="160x120", **options)


def tearDownModule() -> None:
    if "base" in MEDIA:
        remove_tree(MEDIA["base"])


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def playlist_reply(text: str) -> Reply:
    return Reply(text.encode("utf-8"), content_type=HLS_TYPE)


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """Size and modification time of every file under ``folder``."""
    result = {}
    for path in folder.rglob("*"):
        if path.is_file():
            info = path.stat()
            result[str(path.relative_to(folder))] = (info.st_size, info.st_mtime_ns)
    return result


def segment_threads() -> list[str]:
    return [thread.name for thread in threading.enumerate() if thread.name.startswith("download-segment")]


def ts_packets(count: int) -> bytes:
    """``count`` MPEG-TS-shaped packets: a sync byte, then a payload that also holds 0x47 bytes."""
    return b"".join(bytes([0x47]) + bytes((index * 7 + offset) % 256 for offset in range(TS_PACKET - 1))
                    for index in range(count))


def split(data: bytes, sizes: Sequence[int]) -> list[bytes]:
    pieces, position, turn = [], 0, 0
    while position < len(data):
        size = sizes[turn % len(sizes)]
        pieces.append(data[position:position + size])
        position += size
        turn += 1
    return pieces


def media_text(extra: Sequence[str] = (), segment_tags: dict[int, str] | None = None, *,
               ended: bool = True, count: int = 2) -> str:
    return media_playlist([f"{index}.ts" for index in range(1, count + 1)], [1.0] * count, ended=ended,
                          extra=tuple(extra), segment_tags=segment_tags)


def variant(uri: str, bandwidth: int, resolution: str | None = None, codecs: str | None = H264_AAC,
            audio: str | None = None) -> dict[str, Any]:
    return {"uri": uri, "bandwidth": bandwidth, "resolution": resolution, "codecs": codecs, "audio": audio}


def master(*variants: dict[str, Any], extra: Sequence[str] = ()) -> MasterPlaylist:
    playlist = parse_playlist(master_playlist(list(variants), extra=tuple(extra)), MASTER_URL)
    assert isinstance(playlist, MasterPlaylist)
    return playlist


@dataclass
class Run:
    """What one ``SourceTransfers.download`` call reported."""
    outcome: DownloadOutcome | None = None
    error: BaseException | None = None
    progress: list[Progress] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def on_progress(self, progress: Progress) -> None:
        self.progress.append(progress)

    def on_log(self, lines: list[str]) -> None:
        self.logs.extend(lines)


# ------------------------------------------------------------------------------- parser and policy
class MediaPlaylistParseTest(unittest.TestCase):
    def test_media_playlist_lists_segments_with_absolute_links_and_durations(self):
        # Arrange
        text = media_playlist(["a/1.ts", "/abs/2.ts", "//cdn.example/x/3.ts", "http://cdn.example/4.ts?q=1"],
                              [1.0, 2.5, 0.5, 4.0])
        # Act
        playlist = parse_playlist(text, URL)
        # Assert
        self.assertIsInstance(playlist, MediaPlaylist)
        self.assertEqual(playlist.url, URL)
        self.assertEqual([segment.index for segment in playlist.segments], [1, 2, 3, 4])
        self.assertEqual([segment.uri for segment in playlist.segments], [
            "http://media.example/show/a/1.ts", "http://media.example/abs/2.ts", "http://cdn.example/x/3.ts",
            "http://cdn.example/4.ts?q=1"])
        self.assertEqual([segment.duration for segment in playlist.segments], [1.0, 2.5, 0.5, 4.0])

    def test_byte_order_mark_and_harmless_tags_are_accepted(self):
        # Arrange
        text = "\ufeff" + media_text(extra=(
            "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS", "#EXT-X-DISCONTINUITY-SEQUENCE:0",
            "#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:00Z", "#EXT-X-KEY:METHOD=NONE", "# a comment"))
        # Act
        playlist = parse_playlist(text, URL)
        # Assert
        self.assertIsInstance(playlist, MediaPlaylist)
        self.assertEqual(len(playlist.segments), 2)

    def test_playlist_without_endlist_is_refused_as_live(self):
        # Arrange
        text = media_text(ended=False)
        # Act
        with self.assertRaises(SourceError) as caught:
            parse_playlist(text, URL)
        # Assert
        self.assertEqual(caught.exception.code, "LIVE")

    def test_malformed_playlists_are_bad(self):
        cases = {
            "no segments": "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-ENDLIST\n",
            "segment without EXTINF": "#EXTM3U\n#EXT-X-TARGETDURATION:1\n1.ts\n#EXT-X-ENDLIST\n",
            "empty text": "",
            "web page": "<!DOCTYPE html>\n<html><body>not a playlist</body></html>\n",
            "header not first": "#EXT-X-VERSION:3\n#EXTM3U\n#EXTINF:1,\n1.ts\n#EXT-X-ENDLIST\n",
            "master variant without bandwidth": "#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=640x360\nv.m3u8\n",
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(SourceError) as caught:
                parse_playlist(text, URL)
            self.assertEqual(caught.exception.code, "BAD_PLAYLIST", name)

    def test_unreadable_or_out_of_range_durations_are_bad(self):
        for value in ("abc", "", "0", "-1", "nan", "inf", "601"):
            text = f"#EXTM3U\n#EXTINF:{value},\n1.ts\n#EXT-X-ENDLIST\n"
            with self.subTest(value=value), self.assertRaises(SourceError) as caught:
                parse_playlist(text, URL)
            self.assertEqual(caught.exception.code, "BAD_PLAYLIST", value)

    def test_non_http_links_are_bad(self):
        links = ("file:///C:/Windows/win.ini", "ftp://media.example/1.ts", "data:video/mp2t;base64,R0c=")
        for link in links:
            with self.subTest(segment=link), self.assertRaises(SourceError) as caught:
                parse_playlist(media_playlist([link], [1.0]), URL)
            self.assertEqual(caught.exception.code, "BAD_PLAYLIST")
            with self.subTest(variant=link), self.assertRaises(SourceError) as caught:
                parse_playlist(master_playlist([variant(link, 1000)]), MASTER_URL)
            self.assertEqual(caught.exception.code, "BAD_PLAYLIST")

    def test_more_segments_than_the_limit_are_refused(self):
        # Arrange
        at_limit, over_limit = media_text(count=3), media_text(count=4)
        # Act
        with mock.patch.object(download_hls, "MAX_SEGMENTS", 3):
            accepted = parse_playlist(at_limit, URL)
            with self.assertRaises(SourceError) as caught:
                parse_playlist(over_limit, URL)
        # Assert
        self.assertEqual(len(accepted.segments), 3)
        self.assertEqual(caught.exception.code, "BAD_PLAYLIST")


class KeyPolicyTest(unittest.TestCase):
    DRM_KEYS = (
        '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key-1",KEYFORMAT="com.apple.streamingkeydelivery"',
        '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="https://keys.example/k1"',
        '#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,URI="data:text/plain;base64,AAAA",'
        'KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",KEYFORMATVERSIONS="1"',
        '#EXT-X-KEY:METHOD=AES-128,URI="skd://key-2",KEYFORMAT="com.apple.streamingkeydelivery"',
    )

    def test_method_none_key_is_accepted(self):
        # Arrange
        text = media_text(extra=("#EXT-X-KEY:METHOD=NONE",))
        # Act
        playlist = parse_playlist(text, URL)
        # Assert
        self.assertEqual(len(playlist.segments), 2)

    def test_aes128_with_identity_key_format_is_declined_to_ytdlp(self):
        keys = ('#EXT-X-KEY:METHOD=AES-128,URI="https://keys.example/k.bin"',
                '#EXT-X-KEY:METHOD=aes-128,URI="k.bin",KEYFORMAT="identity"')
        for key in keys:
            with self.subTest(key=key), self.assertRaises(SourceDeclined):
                parse_playlist(media_text(segment_tags={2: key}), URL)

    def test_sample_aes_and_vendor_key_formats_are_refused_as_drm(self):
        for key in self.DRM_KEYS:
            with self.subTest(key=key), self.assertRaises(SourceError) as caught:
                parse_playlist(media_text(extra=(key,)), URL)
            self.assertEqual(caught.exception.code, "DRM")

    def test_session_key_in_a_master_is_checked_like_a_segment_key(self):
        variants = [variant("v.m3u8", 1000, "640x360")]
        accepted = parse_playlist(master_playlist(variants, extra=("#EXT-X-SESSION-KEY:METHOD=NONE",)), MASTER_URL)
        self.assertIsInstance(accepted, MasterPlaylist)
        self.assertIsNone(accepted.declined)
        # AES-128 is declined only after the chosen variant showed no DRM (resolve_hls), so it is recorded here.
        aes = parse_playlist(master_playlist(variants, extra=(
            '#EXT-X-SESSION-KEY:METHOD=AES-128,URI="https://keys.example/k.bin"',)), MASTER_URL)
        self.assertEqual(aes.declined, "luồng HLS mã hóa AES-128")
        for key in self.DRM_KEYS:
            session_key = key.replace("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")
            with self.subTest(key=session_key), self.assertRaises(SourceError) as caught:
                parse_playlist(master_playlist(variants, extra=(session_key,)), MASTER_URL)
            self.assertEqual(caught.exception.code, "DRM")

    def test_drm_key_after_a_declined_tag_is_still_refused_as_drm(self):
        """The module docstring: DRM ends FAILED and is never handed to yt-dlp, whatever the tag order
        (a packager may write EXT-X-MAP before EXT-X-KEY)."""
        for declined in ('#EXT-X-MAP:URI="init.mp4"', "#EXT-X-DISCONTINUITY"):
            text = media_text(extra=(declined, self.DRM_KEYS[2]))
            with self.subTest(declined=declined):
                with self.assertRaises(Exception) as caught:
                    parse_playlist(text, URL)
                self.assertIsInstance(caught.exception, SourceError)
                self.assertEqual(getattr(caught.exception, "code", None), "DRM")


class DeclinedFormsTest(unittest.TestCase):
    def test_forms_the_provider_does_not_handle_are_declined_to_ytdlp(self):
        tags = ('#EXT-X-MAP:URI="init.mp4"', "#EXT-X-BYTERANGE:1000@0", "#EXT-X-DISCONTINUITY", "#EXT-X-GAP",
                '#EXT-X-PART:DURATION=0.5,URI="part-1.ts"', '#EXT-X-PRELOAD-HINT:TYPE=PART,URI="part-2.ts"')
        for tag in tags:
            with self.subTest(tag=tag), self.assertRaises(SourceDeclined) as caught:
                parse_playlist(media_text(segment_tags={2: tag}), URL)
            self.assertTrue(caught.exception.message)

    def test_a_live_stream_with_a_declined_tag_is_refused_not_declined(self):
        with self.assertRaises(SourceError) as caught:
            parse_playlist(media_text(extra=('#EXT-X-MAP:URI="init.mp4"',), ended=False), URL)
        self.assertEqual(caught.exception.code, "LIVE")


class PlaylistHardeningTest(unittest.TestCase):
    def test_a_huge_line_without_attributes_is_refused_quickly(self):
        text = "#EXTM3U\n#EXT-X-KEY:" + "A" * 200_000 + "\n#EXTINF:1.0,\n1.ts\n#EXT-X-ENDLIST\n"
        started = time.monotonic()
        with self.assertRaises(SourceError) as caught:
            parse_playlist(text, URL)
        self.assertEqual(caught.exception.code, "BAD_PLAYLIST")
        self.assertLess(time.monotonic() - started, 1.0)

    def test_attribute_parsing_stays_linear_on_long_lines_under_the_cap(self):
        line = "#EXT-X-KEY:" + "A" * (download_hls.MAX_LINE_CHARS - 20)
        started = time.monotonic()
        self.assertEqual(download_hls._attributes(line), {})
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(download_hls._attributes('#X:METHOD=AES-128,URI="k,1",IV=0x1'),
                         {"METHOD": "AES-128", "URI": "k,1", "IV": "0x1"})

    def test_a_master_with_many_variants_is_parsed_in_one_pass_and_capped(self):
        many = [variant(f"v{index}.m3u8", 1000 + index, "640x360") for index in range(download_hls.MAX_VARIANTS)]
        started = time.monotonic()
        self.assertEqual(len(master(*many).variants), download_hls.MAX_VARIANTS)
        self.assertLess(time.monotonic() - started, 1.0)
        with self.assertRaises(SourceError) as caught:
            master(*many, variant("extra.m3u8", 5, "640x360"))
        self.assertEqual(caught.exception.code, "BAD_PLAYLIST")

    def test_an_https_playlist_never_points_to_http(self):
        text = media_playlist(["http://media.example/1.ts"], [1.0])
        with self.assertRaises(SourceError) as caught:
            parse_playlist(text, "https://media.example/index.m3u8")
        self.assertEqual(caught.exception.code, "DOWNGRADE")
        self.assertIsInstance(parse_playlist(text, URL), download_hls.MediaPlaylist)  # http to http is fine

    def test_a_broken_link_in_a_playlist_is_a_bad_playlist(self):
        with self.assertRaises(SourceError) as caught:
            parse_playlist(media_playlist(["http://[broken/1.ts"], [1.0]), URL)
        self.assertEqual(caught.exception.code, "BAD_PLAYLIST")


class VariantSelectionTest(unittest.TestCase):
    def test_picks_the_largest_height_not_above_1080(self):
        # Arrange
        playlist = master(variant("v720.m3u8", 2_000_000, "1280x720"),
                          variant("v2160.m3u8", 15_000_000, "3840x2160"),
                          variant("v1080.m3u8", 5_000_000, "1920x1080"),
                          variant("v480.m3u8", 900_000, "854x480"))
        # Act
        chosen = select_variant(playlist)
        # Assert
        self.assertEqual(chosen.uri, "http://media.example/show/v1080.m3u8")
        self.assertEqual((chosen.width, chosen.height, chosen.bandwidth), (1920, 1080, 5_000_000))

    def test_equal_heights_prefer_h264_with_aac_then_the_highest_bandwidth(self):
        # Arrange
        playlist = master(variant("hevc.m3u8", 9_000_000, "1920x1080", "hvc1.1.6.L120.90,mp4a.40.2"),
                          variant("h264-only.m3u8", 8_000_000, "1920x1080", "avc1.640028"),
                          variant("low.m3u8", 4_000_000, "1920x1080"),
                          variant("high.m3u8", 6_000_000, "1920x1080"))
        # Act
        chosen = select_variant(playlist)
        # Assert
        self.assertEqual(chosen.uri, "http://media.example/show/high.m3u8")

    def test_only_variants_above_1080_pick_the_smallest(self):
        # Arrange
        playlist = master(variant("v2160.m3u8", 15_000_000, "3840x2160"),
                          variant("v1440-low.m3u8", 7_000_000, "2560x1440"),
                          variant("v1440-high.m3u8", 9_000_000, "2560x1440"))
        # Act
        chosen = select_variant(playlist)
        # Assert
        self.assertEqual(chosen.uri, "http://media.example/show/v1440-high.m3u8")

    def test_without_resolution_ranks_by_codecs_then_bandwidth(self):
        # Arrange
        playlist = master(variant("hevc.m3u8", 9_000_000, codecs="hvc1.1.6.L120.90,mp4a.40.2"),
                          variant("unknown.m3u8", 8_000_000, codecs=None),
                          variant("h264-a.m3u8", 2_000_000),
                          variant("h264-b.m3u8", 3_000_000))
        # Act
        chosen = select_variant(playlist)
        # Assert
        self.assertEqual(chosen.uri, "http://media.example/show/h264-b.m3u8")

    def test_variants_without_resolution_are_ignored_when_others_have_one(self):
        # Arrange
        playlist = master(variant("no-size.m3u8", 20_000_000), variant("v720.m3u8", 2_000_000, "1280x720"))
        # Act
        chosen = select_variant(playlist)
        # Assert
        self.assertEqual(chosen.uri, "http://media.example/show/v720.m3u8")

    def test_previous_choice_is_kept_across_fresh_tokens(self):
        # Arrange
        probed = master(variant("v720.m3u8?token=tokA1", 2_000_000, "1280x720"),
                        variant("v1080.m3u8?token=tokA1", 5_000_000, "1920x1080"))
        previous = json.loads(json.dumps(variant_key(probed.variants[0])))  # stored as JSON after the probe
        refreshed = master(variant("v1080.m3u8?token=tokB2", 5_000_000, "1920x1080"),
                           variant("v720.m3u8?token=tokB2", 2_000_000, "1280x720"))
        # Act
        chosen = select_variant(refreshed, previous)
        # Assert
        self.assertEqual(chosen.uri, "http://media.example/show/v720.m3u8?token=tokB2")
        self.assertEqual(previous, {"url": "http://media.example/show/v720.m3u8", "bandwidth": 2_000_000,
                                    "resolution": "1280x720"})

    def test_previous_choice_that_disappeared_raises_source_changed(self):
        # Arrange
        previous = variant_key(master(variant("v720.m3u8", 2_000_000, "1280x720")).variants[0])
        refreshed = {
            "variant removed": master(variant("v1080.m3u8", 5_000_000, "1920x1080")),
            "bandwidth changed": master(variant("v720.m3u8", 2_500_000, "1280x720")),
        }
        for name, playlist in refreshed.items():
            # Act
            with self.subTest(name), self.assertRaises(SourceChanged) as caught:
                select_variant(playlist, previous)
            # Assert
            self.assertEqual(caught.exception.code, "SOURCE_CHANGED")

    def test_separate_audio_groups_are_recorded_only_with_a_uri(self):
        # Arrange
        extra = ('#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="muxed",NAME="Main",DEFAULT=YES',
                 '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="separate",NAME="English",URI="audio/en.m3u8"',
                 '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",URI="subs/en.m3u8"')
        # Act
        playlist = master(variant("v.m3u8", 1000, "640x360", audio="separate"), extra=extra)
        # Assert
        self.assertEqual(playlist.audio_groups_with_uri, frozenset({"separate"}))
        self.assertEqual(playlist.variants[0].audio, "separate")


class TsCheckerTest(unittest.TestCase):
    SPLITS = ([1], [100], [187], [188], [189], [376], [100, 0, 88, 1, 377], [10_000])

    @staticmethod
    def check(pieces: Sequence[bytes]) -> TsChecker:
        checker = TsChecker(7)
        for piece in pieces:
            checker.feed(piece)
        checker.finish()
        return checker

    def assert_not_ts(self, pieces: Sequence[bytes]) -> None:
        with self.assertRaises(HttpError) as caught:
            self.check(pieces)
        self.assertEqual(caught.exception.code, "SEGMENT_NOT_TS")
        self.assertTrue(caught.exception.retryable)
        self.assertIsNone(caught.exception.status)

    def test_whole_packets_pass_in_any_chunk_split(self):
        data = ts_packets(5)
        for sizes in self.SPLITS:
            with self.subTest(sizes=sizes):
                self.assertEqual(self.check(split(data, sizes)).size, len(data))

    def test_a_web_page_is_rejected(self):
        self.assert_not_ts([HTML])

    def test_a_broken_sync_byte_in_a_later_packet_is_rejected_in_any_split(self):
        data = bytearray(ts_packets(5))
        data[3 * TS_PACKET] = 0x00
        for sizes in self.SPLITS:
            with self.subTest(sizes=sizes):
                self.assert_not_ts(split(bytes(data), sizes))

    def test_a_truncated_last_packet_is_rejected(self):
        data = ts_packets(3)[:-1]
        for sizes in ([len(data)], [188], [50]):
            with self.subTest(sizes=sizes):
                self.assert_not_ts(split(data, sizes))

    def test_empty_input_is_rejected(self):
        self.assert_not_ts([])
        self.assert_not_ts([b""])


class StableIdentityTest(unittest.TestCase):
    def test_signature_like_query_values_are_left_out_of_the_stable_link(self):
        # Arrange
        link = ("HTTP://Media.Example/v/Seg-1.ts?quality=hd&token=abc&expires=1700000000&signature=s1&Policy=p"
                "&Key-Pair-Id=k&hdnts=st%3D1~exp%3D2&X-Amz-Signature=a&x-goog-signature=g&e=9&exp=9&st=1&ts=2&t=3"
                "&hmac=h&nonce=n&sig=s&auth_key=a&id=7#start")
        # Act
        stable = stable_url(link)
        # Assert
        self.assertEqual(stable, "http://media.example/v/Seg-1.ts?id=7&quality=hd")

    def test_content_choosing_query_values_are_kept(self):
        self.assertNotEqual(stable_url("http://media.example/v/1.ts?quality=hd&token=a"),
                            stable_url("http://media.example/v/1.ts?quality=sd&token=a"))
        self.assertEqual(stable_url("http://media.example/v/1.ts?b=2&a=1"),
                         stable_url("http://media.example/v/1.ts?a=1&b=2"))

    def test_identity_key_does_not_depend_on_key_order(self):
        self.assertEqual(identity_key({"kind": "hls", "count": 3}), identity_key({"count": 3, "kind": "hls"}))
        self.assertNotEqual(identity_key({"kind": "hls", "count": 3}), identity_key({"kind": "hls", "count": 4}))


# ------------------------------------------------------------------------------- with a fixture server
class ServerCase(unittest.TestCase):
    """A fixture server, a policy-checked client and a temporary folder for each test."""

    def setUp(self) -> None:
        self.server = FixtureServer()
        self.addCleanup(self.server.close)
        self.http = self.server.http()
        self.root = temp_root("biliflow-hls-test-")
        self.addCleanup(remove_tree, self.root)

    def task_dir(self, name: str = "task") -> Path:
        path = self.root / name
        path.mkdir()
        return path

    def serve_segments(self, media: HlsMedia, prefix: str = "/seg", **options: Any) -> list[str]:
        route = segment_route(media, **options)
        paths = [f"{prefix}/{index}.ts" for index in range(1, len(media.segments) + 1)]
        for path in paths:
            self.server.route(path, route)
        return paths

    def serve_playlist(self, media: HlsMedia, paths: Sequence[str]) -> str:
        self.server.route(PLAYLIST_PATH, playlist_reply(media_playlist(list(paths), media.durations)))
        return URL

    def resolve(self, url: str, task_dir: Path, control: ProcessControl | None = None, *,
                ffprobe: Path | None = None, previous: Any = None,
                headers: dict[str, str] | None = None) -> ResolvedSource:
        ctx = ResolveContext(http=self.http, control=control or ProcessControl(), task_dir=task_dir,
                             ffprobe=ffprobe, previous=previous)
        return resolve_hls(url, ctx, provider="direct", label=LABEL, headers=headers)

    def refresher(self, url: str, task_dir: Path, control: ProcessControl, probed: ResolvedSource,
                  calls: list[float]) -> Callable[[], ResolvedSource]:
        """What the worker passes as ``refresh``: fresh links, and SourceChanged when the identity moved."""
        def refresh() -> ResolvedSource:
            calls.append(time.monotonic())
            fresh = self.resolve(url, task_dir, control, previous=probed.identity)
            if fresh.identity_key != probed.identity_key:
                raise SourceChanged(describe_change(probed.identity, fresh.identity))
            return fresh
        return refresh

    @staticmethod
    def no_refresh() -> ResolvedSource:
        raise AssertionError("the playlist was resolved again although no segment link expired")

    def start_download(self, source: ResolvedSource, task_dir: Path, control: ProcessControl, *,
                       refresh: Callable[[], ResolvedSource] | None = None, guard: SizeGuard | None = None,
                       workers: int = 4) -> tuple[threading.Thread, Run]:
        run = Run()
        transfers = SourceTransfers(self.http, ffmpeg=FFMPEG, ffprobe=FFPROBE, segment_workers=workers)

        def target() -> None:
            try:
                run.outcome = transfers.download(source, refresh or self.no_refresh, task_dir, control,
                                                 on_progress=run.on_progress, on_log=run.on_log, guard=guard)
            except BaseException as error:  # noqa: BLE001 - reported by the test
                run.error = error
        thread = threading.Thread(target=target, name="test-hls-download", daemon=True)
        thread.start()
        return thread, run

    def download(self, source: ResolvedSource, task_dir: Path, control: ProcessControl,
                 **options: Any) -> Run:
        thread, run = self.start_download(source, task_dir, control, **options)
        thread.join(60)
        self.assertFalse(thread.is_alive(), "download() did not return")
        if run.error is not None:
            raise run.error
        return run

    def assert_checked_segments_only(self, task_dir: Path, source: ResolvedSource, media: HlsMedia) -> set[int]:
        """No part file anywhere; every segment file on disk is recorded with its size and SHA-256."""
        seg_dir = task_dir / SEG_DIR
        self.assertEqual([], sorted(path.name for path in task_dir.rglob("*.part")))
        manifest = json.loads((seg_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(manifest["identity"], source.identity_key)
        records = {int(index): tuple(value) for index, value in manifest["segments"].items()}
        files = {int(path.name[:6]): path for path in seg_dir.glob("*.ts")}
        self.assertEqual(set(records), set(files))
        for index, path in files.items():
            data = path.read_bytes()
            self.assertEqual(sha256(data), sha256(media.segments[index - 1]), f"segment {index}")
            self.assertEqual(records[index], (len(data), sha256(data)), f"segment {index}")
        return set(records)


class HlsIdentityTest(ServerCase):
    def test_fresh_tokens_keep_the_identity_and_a_new_segment_list_changes_it(self):
        # Arrange
        state = {"token": "tokA1", "count": 4}

        def playlist(seen: Any, number: int) -> Reply:
            uris = [f"/seg/{index}.ts?token={state['token']}&expires={1000 + number}"
                    for index in range(1, state["count"] + 1)]
            return playlist_reply(media_playlist(uris, [1.0] * state["count"]))
        self.server.route(PLAYLIST_PATH, playlist)
        task_dir = self.task_dir()
        # Act
        probed = self.resolve(URL, task_dir)
        state["token"] = "tokB2"
        refreshed = self.resolve(URL, task_dir, previous=probed.identity)
        state["count"] = 5
        changed = self.resolve(URL, task_dir, previous=probed.identity)
        # Assert
        self.assertNotEqual(probed.plan.segments[0].uri, refreshed.plan.segments[0].uri)
        self.assertEqual(probed.identity_key, refreshed.identity_key)
        self.assertNotEqual(probed.identity_key, changed.identity_key)
        self.assertNotIn("tokA1", json.dumps(probed.public()))
        self.assertEqual(sum(self.server.count(f"/seg/{index}.ts") for index in range(1, 6)), 0)

    def test_a_content_choosing_query_value_changes_the_identity(self):
        # Arrange
        state = {"quality": "hd"}
        self.server.route(PLAYLIST_PATH, lambda seen, number: playlist_reply(
            media_playlist([f"/seg/{index}.ts?quality={state['quality']}" for index in (1, 2)], [1.0, 1.0])))
        task_dir = self.task_dir()
        # Act
        first = self.resolve(URL, task_dir)
        state["quality"] = "sd"
        second = self.resolve(URL, task_dir)
        # Assert
        self.assertNotEqual(first.identity_key, second.identity_key)


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class ResolveHlsTest(ServerCase):
    def serve_master(self, media: HlsMedia, extra: Sequence[str] = (), audio: str | None = None) -> list[str]:
        paths = self.serve_segments(media)
        for name in ("v720", "v1080", "v2160"):
            self.server.route(f"/show/{name}/index.m3u8", playlist_reply(media_playlist(paths, media.durations)))
        self.server.route(MASTER_PATH, playlist_reply(master_playlist([
            variant("v720/index.m3u8?token=tok720", 2_000_000, "1280x720"),
            variant("v1080/index.m3u8?token=tok1080", 5_000_000, "1920x1080", audio=audio),
            variant("v2160/index.m3u8?token=tok2160", 15_000_000, "3840x2160"),
        ], extra=tuple(extra))))
        return paths

    def test_master_playlist_picks_1080p_and_reads_only_that_variant_and_the_first_segment(self):
        # Arrange
        media = MEDIA["hls"]
        paths = self.serve_master(media)
        task_dir = self.task_dir()
        headers = {"Referer": "http://page.example/watch/42"}
        # Act
        source = self.resolve(MASTER_URL, task_dir, ffprobe=FFPROBE, headers=headers)
        # Assert: requests
        self.assertEqual(self.server.count(MASTER_PATH), 1)
        self.assertEqual(self.server.count("/show/v1080/index.m3u8"), 1)
        self.assertEqual(self.server.count("/show/v720/index.m3u8"), 0)
        self.assertEqual(self.server.count("/show/v2160/index.m3u8"), 0)
        self.assertEqual([self.server.count(path) for path in paths], [1] + [0] * (len(paths) - 1))
        self.assertEqual(self.server.seen(paths[0])[0].headers.get("referer"), "http://page.example/")  # its origin
        self.assertFalse((task_dir / PROBE_SEGMENT_NAME).exists())
        # Assert: the resolved source
        self.assertEqual(source.transport, "hls")
        self.assertEqual(source.fragments, len(media.segments))
        self.assertEqual(len(source.plan.segments), len(media.segments))
        self.assertEqual((source.video_codec, source.audio_codec), ("h264", "aac"))
        self.assertEqual((source.width, source.height), (160, 120))  # the probed segment, not the label
        self.assertAlmostEqual(source.duration_seconds, media.duration, places=3)
        self.assertEqual(source.estimated_bytes, int(5_000_000 * source.duration_seconds / 8))
        self.assertEqual(source.media_url, "http://media.example/show/v1080/index.m3u8?token=tok1080")
        self.assertEqual(source.title, "show")
        self.assertEqual(source.identity["variant"], {"url": "http://media.example/show/v1080/index.m3u8",
                                                      "bandwidth": 5_000_000, "resolution": "1920x1080"})
        # Assert: what a page or the database may keep
        public = source.public()
        text = json.dumps(public, ensure_ascii=False) + repr(source)
        self.assertNotIn(source.media_url, text)
        self.assertNotIn("tok1080", text)
        self.assertNotIn("page.example", text)
        self.assertEqual(public["identity"], source.identity_key)
        self.assertEqual((public["transport"], public["fragments_total"]), ("hls", len(media.segments)))

    def test_separate_audio_rendition_is_declined_before_any_media_request(self):
        # Arrange
        media = MEDIA["hls"]
        paths = self.serve_master(media, audio="aud", extra=(
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",DEFAULT=YES,URI="audio/en.m3u8"',))
        task_dir = self.task_dir()
        # Act
        with self.assertRaises(SourceDeclined):
            self.resolve(MASTER_URL, task_dir, ffprobe=FFPROBE)
        # Assert
        self.assertEqual(self.server.count(MASTER_PATH), 1)
        self.assertEqual(self.server.count("/show/v1080/index.m3u8"), 0)
        self.assertEqual(self.server.count("/show/audio/en.m3u8"), 0)
        self.assertEqual(sum(self.server.count(path) for path in paths), 0)

    def test_first_segment_without_audio_or_video_is_refused(self):
        for name, code in (("video_only", "NO_AUDIO_STREAM"), ("audio_only", "NO_VIDEO_STREAM")):
            with self.subTest(name):
                # Arrange
                media = MEDIA[name]
                url = self.serve_playlist(media, self.serve_segments(media, prefix=f"/{name}"))
                task_dir = self.task_dir(name)
                # Act
                with self.assertRaises(SourceError) as caught:
                    self.resolve(url, task_dir, ffprobe=FFPROBE)
                # Assert
                self.assertEqual(caught.exception.code, code)
                self.assertFalse((task_dir / PROBE_SEGMENT_NAME).exists())

    def test_first_segment_that_is_a_web_page_is_refused(self):
        # Arrange
        media = MEDIA["hls"]
        paths = self.serve_segments(media)
        self.server.route(paths[0], Reply(HTML, content_type="text/html"))
        url = self.serve_playlist(media, paths)
        task_dir = self.task_dir()
        # Act
        with self.assertRaises(SourceError) as caught:
            self.resolve(url, task_dir, ffprobe=FFPROBE)
        # Assert
        self.assertEqual(caught.exception.code, "SEGMENT_NOT_TS")
        self.assertEqual(sum(self.server.count(path) for path in paths[1:]), 0)
        self.assertEqual(list(task_dir.iterdir()), [])

    def test_refresh_mode_without_ffprobe_fetches_no_segment(self):
        # Arrange
        media = MEDIA["hls"]
        paths = self.serve_master(media)
        url = self.serve_playlist(media, paths)
        task_dir = self.task_dir()
        # Act
        from_master = self.resolve(MASTER_URL, task_dir)
        from_media = self.resolve(url, task_dir)
        # Assert
        self.assertEqual(sum(self.server.count(path) for path in paths), 0)
        self.assertEqual((from_master.video_codec, from_master.audio_codec), ("h264", "aac"))  # CODECS
        self.assertEqual((from_master.width, from_master.height), (1920, 1080))
        self.assertEqual(from_master.fragments, len(media.segments))
        self.assertEqual((from_media.video_codec, from_media.estimated_bytes, from_media.height), (None, None, None))
        self.assertIsNone(from_media.identity["variant"])
        self.assertEqual(from_media.fragments, len(media.segments))


@unittest.skipUnless(HAVE_FFMPEG, NEED_FFMPEG)
class HlsTransferTest(ServerCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(download_hls, "with_retries", FAST_RETRIES)  # 10 ms pauses, same attempts
        patcher.start()
        self.addCleanup(patcher.stop)
        self.media: HlsMedia = MEDIA["hls"]
        self.count = len(self.media.segments)
        self.sizes = [len(data) for data in self.media.segments]

    def slow(self, indexes: Sequence[int]) -> dict[str, Any]:
        return {"delays": {index: SLOW_DELAY for index in indexes}, "chunk": SLOW_CHUNK}

    def assert_joined_in_playlist_order(self, path: Path) -> None:
        hashes = packet_hashes(path)
        self.assertEqual(sum(1 for line in hashes if line.split(",")[0].strip() == "0"), VIDEO_FRAMES)
        self.assertEqual(hashes, MEDIA["reference"])

    def test_segments_finishing_out_of_order_are_joined_in_playlist_order(self):
        # Arrange
        paths = self.serve_segments(self.media, delays={1: 0.1, 2: 0.1}, chunk=4096)
        url = self.serve_playlist(self.media, paths)
        task_dir, control = self.task_dir(), ProcessControl()
        source = DirectMediaProvider().resolve(url, ResolveContext(http=self.http, control=control,
                                                                   task_dir=task_dir, ffprobe=FFPROBE))
        finished_order: list[int] = []
        original_add = SegmentManifest.add

        def recording_add(manifest: SegmentManifest, index: int, size: int, digest: str) -> None:
            finished_order.append(index)
            original_add(manifest, index, size, digest)
        # Act
        with mock.patch.object(SegmentManifest, "add", recording_add):
            run = self.download(source, task_dir, control)
        # Assert
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(run.outcome.final_path, task_dir / "media.mp4")
        self.assertEqual(sorted(finished_order), list(range(1, self.count + 1)))
        self.assertNotEqual(finished_order, sorted(finished_order), "segments did not finish out of order")
        self.assert_joined_in_playlist_order(run.outcome.final_path)
        self.assertFalse((task_dir / SEG_DIR).exists())
        self.assertEqual([], list(task_dir.rglob("*.part")))
        self.assertTrue((task_dir / "media-done.json").is_file())
        last = run.progress[-1]
        self.assertEqual((last.basis, last.stage), ("fragments", "remuxing"))
        self.assertEqual((last.fragments_done, last.fragments_total), (self.count, self.count))
        self.assertEqual(last.downloaded_bytes, sum(self.sizes))
        # A second call reuses the finished file without any request.
        requests = len(self.server.requests)
        again = self.download(source, task_dir, ProcessControl())
        self.assertTrue(again.outcome.ok)
        self.assertEqual(again.outcome.final_path, task_dir / "media.mp4")
        self.assertEqual(len(self.server.requests), requests)

    def test_stopped_transfer_keeps_checked_segments_and_resume_fetches_only_the_rest(self):
        # Arrange
        paths = self.serve_segments(self.media, **self.slow(range(3, self.count + 1)))
        url = self.serve_playlist(self.media, paths)
        task_dir, control = self.task_dir(), ProcessControl()
        seg_dir = task_dir / SEG_DIR
        source = self.resolve(url, task_dir, control)
        # Act: stop once the two fast segments are finished
        thread, first = self.start_download(source, task_dir, control)
        self.assertTrue(wait_for(lambda: all((seg_dir / segment_name(i)).is_file() for i in (1, 2))))
        control.request("stop")
        stopped_at = time.monotonic()
        thread.join(30)
        returned_after = time.monotonic() - stopped_at
        before = snapshot(task_dir)
        time.sleep(0.5)
        after = snapshot(task_dir)
        # Assert: the stop
        self.assertFalse(thread.is_alive())
        self.assertIsNone(first.error)
        self.assertFalse(first.outcome.ok)
        self.assertEqual(first.outcome.code, "STOPPED")
        self.assertLess(returned_after, STOP_SECONDS)
        self.assertEqual(segment_threads(), [])
        self.assertEqual(before, after, "a file changed after download() returned")
        self.assertFalse((task_dir / "media.mp4").exists())
        finished = self.assert_checked_segments_only(task_dir, source, self.media)
        self.assertTrue({1, 2} <= finished < set(range(1, self.count + 1)), finished)
        # Act: resume with fresh links (fast segments now)
        self.serve_segments(self.media)
        fresh = self.resolve(url, task_dir, previous=source.identity)
        second = self.download(fresh, task_dir, ProcessControl())
        # Assert: the resume
        self.assertEqual(fresh.identity_key, source.identity_key)
        self.assertTrue(second.outcome.ok, second.outcome)
        for index in finished:
            self.assertEqual(self.server.count(paths[index - 1]), 1, f"segment {index} fetched again")
        for index in set(range(1, self.count + 1)) - finished:
            self.assertGreaterEqual(self.server.count(paths[index - 1]), 1)
        self.assertEqual(second.progress[0].fragments_done, len(finished))
        self.assertEqual(second.progress[0].downloaded_bytes, sum(self.sizes[index - 1] for index in finished))
        self.assertTrue(any(f"{len(finished)}/{self.count}" in line for line in second.logs), second.logs)
        self.assertEqual(second.progress[-1].fragments_done, self.count)
        self.assert_joined_in_playlist_order(second.outcome.final_path)

    def seed_segments(self, task_dir: Path, identity: str, indexes: Sequence[int]) -> Path:
        """Finished segments of an earlier run, recorded in ``seg/manifest.json`` under ``identity``."""
        seg_dir = task_dir / SEG_DIR
        seg_dir.mkdir()
        manifest = SegmentManifest(seg_dir, identity)
        for index in indexes:
            data = self.media.segments[index - 1]
            (seg_dir / segment_name(index)).write_bytes(data)
            manifest.add(index, len(data), sha256(data))
        manifest.save()
        return seg_dir

    def test_tampered_finished_segment_is_fetched_again_on_resume(self):
        # Arrange
        paths = self.serve_segments(self.media)
        url = self.serve_playlist(self.media, paths)
        task_dir = self.task_dir()
        source = self.resolve(url, task_dir)
        seg_dir = self.seed_segments(task_dir, source.identity_key, (1, 2, 3))
        (seg_dir / segment_name(2)).write_bytes(bytes([0x47]) * self.sizes[1])  # same size, other bytes
        # Act
        run = self.download(source, task_dir, ProcessControl())
        # Assert
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual([self.server.count(path) for path in paths], [0, 1, 0] + [1] * (self.count - 3))
        self.assertEqual(run.progress[0].fragments_done, 2)
        self.assert_joined_in_playlist_order(run.outcome.final_path)

    def test_segments_of_another_source_are_never_reused(self):
        # Arrange
        paths = self.serve_segments(self.media)
        url = self.serve_playlist(self.media, paths)
        task_dir = self.task_dir()
        source = self.resolve(url, task_dir)
        self.seed_segments(task_dir, "0" * 64, range(1, self.count + 1))
        # Act
        run = self.download(source, task_dir, ProcessControl())
        # Assert
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual([self.server.count(path) for path in paths], [1] * self.count)
        self.assertEqual(run.progress[0].fragments_done, 0)

    def serve_with_rotating_tokens(self, rotate_at: Sequence[int]) -> tuple[list[str], list[str]]:
        """Segments need the newest token; the first request of each segment in ``rotate_at`` makes a new one
        (that request already carries the old one: 403). The playlist always lists the newest token."""
        tokens = ["tokA1"]
        inner = segment_route(self.media, token=lambda: tokens[-1])

        def route(seen: Any, number: int) -> Reply:
            if int(Path(seen.path).stem) in rotate_at and number == 1:
                tokens.append(f"tokR{len(tokens) + 1}")
            return inner(seen, number)
        paths = [f"/seg/{index}.ts" for index in range(1, self.count + 1)]
        for path in paths:
            self.server.route(path, route)
        self.server.route(PLAYLIST_PATH, lambda seen, number: playlist_reply(media_playlist(
            [f"{path}?token={tokens[-1]}" for path in paths], self.media.durations)))
        return paths, tokens

    def assert_no_token_in_logs(self, run: Run, tokens: Sequence[str]) -> None:
        self.assertFalse(any(token in line for token in tokens for line in run.logs), run.logs)

    def test_expired_segment_links_refresh_the_playlist_and_finish(self):
        # Arrange
        paths, tokens = self.serve_with_rotating_tokens(rotate_at=(3,))
        task_dir, control = self.task_dir(), ProcessControl()
        source = self.resolve(URL, task_dir, control)
        calls: list[float] = []
        # Act
        run = self.download(source, task_dir, control, refresh=self.refresher(URL, task_dir, control, source, calls))
        # Assert
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.server.count(PLAYLIST_PATH), 2)
        self.assertEqual(len(tokens), 2)
        self.assertEqual(self.server.count(paths[2]), 2)  # 403 with the old token, then the fresh link
        for path in paths:
            self.assertIn(self.server.seen(path)[-1].query.get("token"), ([tokens[0]], [tokens[1]]))
        self.assertEqual(self.server.seen(paths[2])[-1].query.get("token"), [tokens[1]])
        self.assert_no_token_in_logs(run, tokens)
        self.assert_joined_in_playlist_order(run.outcome.final_path)

    def test_tokens_rotating_several_times_while_segments_finish_still_complete(self):
        """Only refreshes in a row without a new segment are limited (MAX_REFRESHES); a long film whose
        links expire now and then needs more refreshes than that in total."""
        # Arrange: one worker, so each rotation is met in its own wave after some segments finished
        paths, tokens = self.serve_with_rotating_tokens(rotate_at=(2, 4, 6))
        task_dir, control = self.task_dir(), ProcessControl()
        source = self.resolve(URL, task_dir, control)
        calls: list[float] = []
        # Act
        run = self.download(source, task_dir, control, workers=1,
                            refresh=self.refresher(URL, task_dir, control, source, calls))
        # Assert
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(len(tokens), 4)
        self.assertEqual(len(calls), 3)
        self.assertGreater(len(calls), MAX_REFRESHES)
        self.assertEqual(self.server.count(PLAYLIST_PATH), 1 + len(calls))
        for index in (2, 4, 6):
            self.assertEqual(self.server.seen(paths[index - 1])[0].query.get("token"),
                             [tokens[(index // 2) - 1]], f"segment {index} first request")
        self.assert_no_token_in_logs(run, tokens)
        self.assert_joined_in_playlist_order(run.outcome.final_path)

    def test_refresh_with_a_different_segment_list_ends_source_changed(self):
        # Arrange
        paths = self.serve_segments(self.media)
        other = self.serve_segments(self.media, prefix="/other")
        normal = segment_route(self.media)
        self.server.route(paths[2], lambda seen, number: Reply(b"expired", 403, "text/plain") if number == 1
                          else normal(seen, number))
        self.server.route(PLAYLIST_PATH, lambda seen, number: playlist_reply(
            media_playlist(paths if number == 1 else other, self.media.durations)))
        task_dir, control = self.task_dir(), ProcessControl()
        source = self.resolve(URL, task_dir, control)
        calls: list[float] = []
        # Act
        run = self.download(source, task_dir, control, refresh=self.refresher(URL, task_dir, control, source, calls))
        # Assert
        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "SOURCE_CHANGED")
        self.assertFalse(run.outcome.resumable)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sum(self.server.count(path) for path in other), 0)
        self.assertFalse((task_dir / "media.mp4").exists())

    def test_refreshes_in_a_row_without_a_new_segment_end_forbidden(self):
        """Wave 1 finishes the other segments (the count starts again); then MAX_REFRESHES refreshes in a row
        bring no new segment: FORBIDDEN, not resumable."""
        # Arrange
        paths = self.serve_segments(self.media)
        self.server.route(paths[2], Reply(b"expired", 403, "text/plain"))
        url = self.serve_playlist(self.media, paths)
        task_dir, control = self.task_dir(), ProcessControl()
        source = self.resolve(url, task_dir, control)
        calls: list[float] = []
        # Act
        run = self.download(source, task_dir, control, refresh=self.refresher(url, task_dir, control, source, calls))
        # Assert
        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "FORBIDDEN")
        self.assertFalse(run.outcome.resumable)
        self.assertEqual(len(calls), MAX_REFRESHES)
        self.assertEqual(self.server.count(PLAYLIST_PATH), 1 + MAX_REFRESHES)
        self.assertEqual(self.server.count(paths[2]), 1 + MAX_REFRESHES)
        self.assertNotIn(3, self.assert_checked_segments_only(task_dir, source, self.media))

    def test_busy_segment_is_retried_and_finishes(self):
        # Arrange
        paths = self.serve_segments(self.media, fail={2: 1})
        url = self.serve_playlist(self.media, paths)
        task_dir = self.task_dir()
        source = self.resolve(url, task_dir)
        # Act
        run = self.download(source, task_dir, ProcessControl())
        # Assert
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(self.server.count(paths[1]), 2)
        self.assertTrue(any(f"Thử lại 1/{SEGMENT_RETRIES}" in line for line in run.logs), run.logs)
        self.assert_joined_in_playlist_order(run.outcome.final_path)

    def test_segment_busy_after_every_retry_ends_resumable_server_busy(self):
        # Arrange
        paths = self.serve_segments(self.media, fail={2: 100})
        url = self.serve_playlist(self.media, paths)
        task_dir = self.task_dir()
        source = self.resolve(url, task_dir)
        # Act
        run = self.download(source, task_dir, ProcessControl())
        # Assert
        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "SERVER_BUSY")
        self.assertTrue(run.outcome.resumable)
        self.assertEqual(self.server.count(paths[1]), SEGMENT_RETRIES + 1)
        self.assertNotIn(2, self.assert_checked_segments_only(task_dir, source, self.media))
        self.assertFalse((task_dir / "media.mp4").exists())

    def test_cut_segment_is_retried_and_its_partial_bytes_are_not_counted(self):
        # Arrange
        paths = self.serve_segments(self.media)
        normal = segment_route(self.media)
        self.server.route(paths[1], lambda seen, number: dataclasses.replace(
            normal(seen, number), cut_after=1000 if number == 1 else None))
        url = self.serve_playlist(self.media, paths)
        task_dir = self.task_dir()
        source = self.resolve(url, task_dir)
        # Act
        run = self.download(source, task_dir, ProcessControl())
        # Assert
        self.assertTrue(run.outcome.ok, run.outcome)
        self.assertEqual(self.server.count(paths[1]), 2)
        self.assertEqual(run.progress[-1].downloaded_bytes, sum(self.sizes))
        self.assert_joined_in_playlist_order(run.outcome.final_path)

    def test_segment_that_is_a_web_page_on_every_retry_ends_segment_not_ts(self):
        # Arrange
        paths = self.serve_segments(self.media)
        self.server.route(paths[1], Reply(HTML, content_type="text/html"))
        url = self.serve_playlist(self.media, paths)
        task_dir = self.task_dir()
        source = self.resolve(url, task_dir)
        # Act
        run = self.download(source, task_dir, ProcessControl())
        # Assert
        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "SEGMENT_NOT_TS")
        self.assertFalse(run.outcome.resumable)
        self.assertEqual(self.server.count(paths[1]), SEGMENT_RETRIES + 1)
        self.assertNotIn(2, self.assert_checked_segments_only(task_dir, source, self.media))

    def test_size_guard_on_reported_bytes_ends_too_large(self):
        # Arrange
        paths = self.serve_segments(self.media)
        url = self.serve_playlist(self.media, paths)
        task_dir = self.task_dir()
        source = self.resolve(url, task_dir)
        guard = SizeGuard(task_dir, self.sizes[0] + self.sizes[1], None)
        # Act
        run = self.download(source, task_dir, ProcessControl(), guard=guard, workers=1)
        # Assert
        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "TOO_LARGE")
        self.assertFalse(run.outcome.resumable)
        self.assertTrue(guard.tripped)
        self.assertLess(sum(self.server.count(path) for path in paths), self.count)
        self.assertFalse((task_dir / "media.mp4").exists())
        self.assert_checked_segments_only(task_dir, source, self.media)

    def test_size_guard_on_disk_ends_too_large_without_waiting_for_slow_segments(self):
        # Arrange
        paths = self.serve_segments(self.media, **self.slow(range(3, self.count + 1)))
        url = self.serve_playlist(self.media, paths)
        task_dir = self.task_dir()
        source = self.resolve(url, task_dir)
        guard = SizeGuard(task_dir, None, (self.sizes[0] + self.sizes[1]) * 3 // 4, interval=0.05)
        # Act
        started = time.monotonic()
        run = self.download(source, task_dir, ProcessControl(), guard=guard)
        elapsed = time.monotonic() - started
        # Assert
        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "TOO_LARGE")
        self.assertTrue(guard.tripped)
        self.assertLess(elapsed, STOP_SECONDS)
        self.assertEqual(segment_threads(), [])
        self.assert_checked_segments_only(task_dir, source, self.media)

    def test_cancel_mid_transfer_returns_quickly_and_leaves_no_open_file(self):
        # Arrange
        paths = self.serve_segments(self.media, **self.slow(range(1, self.count + 1)))
        url = self.serve_playlist(self.media, paths)
        task_dir, control = self.task_dir(), ProcessControl()
        source = self.resolve(url, task_dir, control)
        thread, run = self.start_download(source, task_dir, control)
        self.assertTrue(wait_for(lambda: any((task_dir / SEG_DIR).glob("*.ts.part"))))
        # Act
        control.request("cancel")
        cancelled_at = time.monotonic()
        thread.join(30)
        returned_after = time.monotonic() - cancelled_at
        shutil.rmtree(task_dir)  # raises on Windows while any file of the task is still open
        # Assert
        self.assertFalse(thread.is_alive())
        self.assertIsNone(run.error)
        self.assertFalse(run.outcome.ok)
        self.assertEqual(run.outcome.code, "CANCELLED")
        self.assertLess(returned_after, STOP_SECONDS)
        self.assertEqual(segment_threads(), [])
        self.assertFalse(task_dir.exists())


if __name__ == "__main__":
    unittest.main()
