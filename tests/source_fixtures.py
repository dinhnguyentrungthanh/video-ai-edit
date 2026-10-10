"""Self-made media and a local HTTP server for the source-provider tests: no network, no real site.

Links use ``.example`` host names on the default port. ``FixtureServer.http()`` gives a SafeHttp whose
resolver answers a public address and whose connector then connects that checked address to this
local server: the production policy (public DNS answer, default port, checked redirects) stays on.
Media are made by the project's FFmpeg (``BILIFLOW_FFMPEG``) from test patterns (testsrc + sine).
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from biliflow.download_http import SafeHttp

ROOT = Path(__file__).resolve().parents[1]


def _install_root() -> Path:
    """This checkout, or the BiliFlow install around it (a worktree lives in ``<install>\\temp``)."""
    for folder in (ROOT, *ROOT.parents):
        if (folder / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe").is_file():
            return folder
    return ROOT


INSTALL_ROOT = _install_root()
FFMPEG = Path(os.environ.get("BILIFLOW_FFMPEG") or INSTALL_ROOT / "tools/ffmpeg/bin/ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe.exe")
HAVE_FFMPEG = FFMPEG.exists() and FFPROBE.exists()
NEED_FFMPEG = "project FFmpeg is required (BILIFLOW_FFMPEG)"
PUBLIC_ADDRESS = "93.184.215.14"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_RANGE = re.compile(r"bytes=(\d+)-(\d*)$")


def public_resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_ADDRESS]


# ----------------------------------------------------------------------------------------- media
def run_ffmpeg(*args: Any) -> None:
    subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True,
                   timeout=180, creationflags=CREATE_NO_WINDOW, capture_output=True)


def _sources(seconds: float, video: bool, audio: bool, size: str = "320x240") -> list[str]:
    args: list[str] = []
    if video:
        args += ["-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}"]
    if video:
        args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "25"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "64k"]
    return args


def make_clip(path: Path, *, seconds: float = 4, video: bool = True, audio: bool = True,
              container: str = "mp4", size: str = "320x240") -> Path:
    """A test pattern with a tone: MP4 (index first), MKV or MPEG-TS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tail = {"mp4": ["-movflags", "+faststart", "-f", "mp4"], "mkv": ["-f", "matroska"],
            "ts": ["-f", "mpegts"]}[container]
    run_ffmpeg(*_sources(seconds, video, audio, size), *tail, path)
    return path


@dataclass(frozen=True)
class HlsMedia:
    segments: tuple[bytes, ...]
    durations: tuple[float, ...]

    @property
    def duration(self) -> float:
        return sum(self.durations)


def make_hls(directory: Path, *, seconds: float = 6, segment_seconds: float = 1, video: bool = True,
             audio: bool = True, size: str = "320x240") -> HlsMedia:
    """MPEG-TS segments of a test pattern (FFmpeg's HLS muxer); the server writes its own playlists."""
    directory.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(*_sources(seconds, video, audio, size), "-f", "hls", "-hls_time", segment_seconds,
               "-hls_playlist_type", "vod", "-hls_segment_type", "mpegts",
               "-hls_segment_filename", directory / "%03d.ts", directory / "index.m3u8")
    text = (directory / "index.m3u8").read_text(encoding="utf-8")
    durations = tuple(float(value) for value in re.findall(r"#EXTINF:([\d.]+)", text))
    names = [line for line in text.splitlines() if line.endswith(".ts")]
    return HlsMedia(tuple((directory / name).read_bytes() for name in names), durations)


def media_playlist(uris: list[str], durations: tuple[float, ...] | list[float], *, ended: bool = True,
                   extra: tuple[str, ...] = (), segment_tags: dict[int, str] | None = None) -> str:
    target = max(1, int(max(durations) + 0.999))
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}", "#EXT-X-MEDIA-SEQUENCE:0", *extra]
    for index, (uri, duration) in enumerate(zip(uris, durations), start=1):
        if segment_tags and index in segment_tags:
            lines.append(segment_tags[index])
        lines += [f"#EXTINF:{duration:.6f},", uri]
    if ended:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def master_playlist(variants: list[dict[str, Any]], *, extra: tuple[str, ...] = ()) -> str:
    lines = ["#EXTM3U", *extra]
    for variant in variants:
        attributes = [f"BANDWIDTH={variant['bandwidth']}"]
        if variant.get("resolution"):
            attributes.append(f"RESOLUTION={variant['resolution']}")
        if variant.get("codecs"):
            attributes.append(f'CODECS="{variant["codecs"]}"')
        if variant.get("audio"):
            attributes.append(f'AUDIO="{variant["audio"]}"')
        lines += ["#EXT-X-STREAM-INF:" + ",".join(attributes), variant["uri"]]
    return "\n".join(lines) + "\n"


def temp_root(prefix: str) -> Path:
    """A folder under the system temp; scripts/env.ps1 points TEMP at the project's temp folder."""
    return Path(tempfile.mkdtemp(prefix=prefix))


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------------------- server
@dataclass
class Reply:
    """One answer of the fixture server. ``cut_after``: close the socket after that many body bytes.
    ``length``: False sends no Content-Length (the server speaks HTTP/1.0: the body ends when it closes)."""

    body: bytes = b""
    status: int = 200
    content_type: str = "application/octet-stream"
    headers: dict[str, str] = field(default_factory=dict)
    ranges: bool = True
    etag: str | None = None
    delay: float = 0.0
    chunk: int = 64 * 1024
    cut_after: int | None = None
    length: bool = True


@dataclass(frozen=True)
class Seen:
    host: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    method: str = "GET"
    body: bytes = b""


Route = Callable[[Seen, int], Reply]


class FixtureServer:
    """A local HTTP server for ``.example`` links; routes match the path without its query.

    ``tls``: serve HTTPS with that server context (tests/tls_fixtures.py); the handshake runs when a
    connection is accepted and a refused one is dropped. ``routes``: share the routes of another server
    (the same paths over http and https)."""

    def __init__(self, *, tls: ssl.SSLContext | None = None, routes: dict[str, Reply | Route] | None = None) -> None:
        self.routes: dict[str, Reply | Route] = {} if routes is None else routes
        self.requests: list[Seen] = []
        self.connections: list[tuple[str, int]] = []
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                self.do_GET()

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or "0")
                if length > 1024 * 1024:
                    self.send_error(413)
                    return
                body = self.rfile.read(length) if length else b""
                parts = urlsplit(self.path)
                seen = Seen(self.headers.get("Host", ""), parts.path, parse_qs(parts.query),
                            {key.lower(): value for key, value in self.headers.items()}, self.command, body)
                with server._lock:
                    server.requests.append(seen)
                    number = sum(1 for item in server.requests if item.path == parts.path)
                    route = server.routes.get(parts.path)
                reply = Reply(b"not found", 404, "text/plain") if route is None else (
                    route(seen, number) if callable(route) else route)
                try:
                    server._send(self, reply, seen)
                except (ConnectionError, OSError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if tls is not None:
            self.httpd.socket = tls.wrap_socket(self.httpd.socket, server_side=True)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="fixture-http", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> FixtureServer:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def route(self, path: str, reply: Reply | Route) -> None:
        with self._lock:
            self.routes[path] = reply

    def count(self, path: str) -> int:
        with self._lock:
            return sum(1 for item in self.requests if item.path == path)

    def seen(self, path: str) -> list[Seen]:
        with self._lock:
            return [item for item in self.requests if item.path == path]

    def connector(self, address: str, port: int, timeout: float) -> socket.socket:
        """Every checked address goes to this server (the address the policy checked is recorded)."""
        with self._lock:
            self.connections.append((address, port))
        return socket.create_connection(("127.0.0.1", self.port), timeout=timeout)

    def http(self, resolver: Callable[[str, int], list[str]] = public_resolver, **options: Any) -> SafeHttp:
        return SafeHttp(resolver=resolver, connector=self.connector, **options)

    @staticmethod
    def _send(handler: BaseHTTPRequestHandler, reply: Reply, seen: Seen) -> None:
        body, status = reply.body, reply.status
        headers = {"Content-Type": reply.content_type, **reply.headers}
        if reply.etag:
            headers["ETag"] = reply.etag
        wanted = _RANGE.match(seen.headers.get("range", ""))
        if_range = seen.headers.get("if-range")
        if 200 <= status < 300 and reply.ranges:
            headers["Accept-Ranges"] = "bytes"
            # If-Range names the version by its ETag or by its Last-Modified date (as servers compare them).
            if wanted and (if_range is None or if_range in (reply.etag, reply.headers.get("Last-Modified"))):
                start = int(wanted.group(1))
                end = min(int(wanted.group(2)) if wanted.group(2) else len(body) - 1, len(body) - 1)
                if start >= len(body):
                    status, body = 416, b""
                    headers["Content-Range"] = f"bytes */{len(reply.body)}"
                else:
                    status = 206
                    headers["Content-Range"] = f"bytes {start}-{end}/{len(reply.body)}"
                    body = body[start:end + 1]
        if reply.length:
            headers["Content-Length"] = str(len(body))
        handler.send_response(status)
        for name, value in headers.items():
            handler.send_header(name, value)
        handler.end_headers()
        limit = len(body) if reply.cut_after is None else min(len(body), reply.cut_after)
        sent = 0
        while sent < limit:
            piece = body[sent:min(limit, sent + reply.chunk)]
            handler.wfile.write(piece)
            handler.wfile.flush()
            sent += len(piece)
            if reply.delay:
                time.sleep(reply.delay)
        if reply.cut_after is not None:
            handler.close_connection = True
            try:
                handler.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def segment_route(media: HlsMedia, *, token: Callable[[], str] | None = None,
                  delays: dict[int, float] | None = None, fail: dict[int, int] | None = None,
                  chunk: int = 16 * 1024) -> Route:
    """``/seg/N.ts``: segment N (1-based). With ``token``, a link without the current token gets 403;
    the first ``fail[N]`` answers for segment N are 503; ``delays[N]`` slows each chunk of segment N."""

    def route(seen: Seen, number: int) -> Reply:
        index = int(Path(seen.path).stem)
        if token is not None and seen.query.get("token", [None])[0] != token():
            return Reply(b"expired", 403, "text/plain")
        if fail and number <= fail.get(index, 0):
            return Reply(b"busy", 503, "text/plain")
        return Reply(media.segments[index - 1], content_type="video/mp2t", delay=(delays or {}).get(index, 0.0),
                     chunk=chunk)
    return route
