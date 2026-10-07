"""Try direct MP4 / HLS links on the "Tải video" page: a test Control Center with self-made media.

Run from the worktree (or the checkout) in PowerShell; nothing else needs to be set:

    E:\\DungChung\\BiliFlow\\.venv\\Scripts\\python.exe -m tests.try_source_downloads --port 8797

then open http://127.0.0.1:8797/dashboard-v2/#downloads and paste the links it prints.

- The Control Center runs this checkout's code on a NEW root under the install's temp folder
  (``temp\\try-source-downloads-*``): its own state, input and temp, nothing imported. The real Control
  Center, its input, state, output and archive are never touched; its port (8765) is refused and the
  chosen port must be free.
- The media are made by the project's FFmpeg (test pattern + tone) and served on 127.0.0.1 under
  ``https://media.example`` and ``http://media.example`` links. The HTTPS ones use a throwaway test CA
  that only this process trusts (tests/tls_fixtures.py); nothing is installed in Windows.
- Any other link (a direct link you may use) goes out like on the real Control Center: real DNS, public
  addresses only, Windows' certificates.
- ``--probe URL``: resolve one link as PROBING does (the first MiB of a file, or the playlist and its
  first segment), print what the downloader would do, delete that sample. No Control Center, no download.
- ``--check-input ROOT``: the files of a test root's input with their video and audio streams.
- Ctrl+C stops it and lists its input the same way. The temporary root is kept to be looked at; delete
  it afterwards.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # this checkout's code, whatever PYTHONPATH says

import argparse  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

from biliflow import recycle_bin  # noqa: E402
from biliflow.control_center import ControlCenter  # noqa: E402
from biliflow.download_api import DownloadService, public_url  # noqa: E402
from biliflow.download_http import HttpError, SafeHttp, default_connector  # noqa: E402
from biliflow.download_links import LinkRejected, check_link, default_resolver  # noqa: E402
from biliflow.download_media_file import SNIFF_BYTES  # noqa: E402
from biliflow.download_runner import ProcessControl, mask_line  # noqa: E402
from biliflow.download_source_steps import describe_source  # noqa: E402
from biliflow.download_source_types import ResolveContext, SourceDeclined, SourceError  # noqa: E402
from biliflow.download_sources import SourceTransfers, default_registry  # noqa: E402
from biliflow.download_store import DownloadStore  # noqa: E402
from biliflow.download_transfer import probe_streams, stream_summary  # noqa: E402
from biliflow.download_worker import DownloadWorker  # noqa: E402
from tests.source_fixtures import (  # noqa: E402
    FFMPEG,
    FFPROBE,
    HAVE_FFMPEG,
    INSTALL_ROOT,
    PUBLIC_ADDRESS,
    FixtureServer,
    Reply,
    Route,
    make_clip,
    make_hls,
    master_playlist,
    media_playlist,
    remove_tree,
    segment_route,
)
from tests.tls_fixtures import HAVE_TLS, NEED_TLS, make_tls_files  # noqa: E402

HOST = "media.example"
HLS_TYPE = "application/vnd.apple.mpegurl"
REAL_PORT = 8765
PIECE = 16 * 1024
TOKEN_SECONDS = 6.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
WORK_PREFIX = "try-source-downloads-"
LINKS = (
    ("/clips/demo.mp4", "MP4 20 giây, tải trong vài giây: tải xong vào input"),
    ("/clips/long.mp4", "MP4 2 phút, tải chậm khoảng 30 giây: thử Hủy (hoặc Dừng / Tiếp tục)"),
    ("/slow/index.m3u8", "HLS 2 phút, 60 đoạn, tải chậm khoảng 1 phút: thử Dừng rồi Tiếp tục"),
    ("/show/index.m3u8", "HLS có 2 chất lượng (360p, 720p): tự chọn 720p"),
    ("/token/index.m3u8", f"HLS có link đoạn hết hạn mỗi {TOKEN_SECONDS:.0f} giây: tự lấy lại playlist"),
    ("/no-audio/clip.mp4", "MP4 không có tiếng: báo lỗi lúc thăm dò, không tải"),
    ("/drm/index.m3u8", "HLS có DRM: từ chối"),
    ("/live/index.m3u8", "HLS đang phát trực tiếp: từ chối"),
    ("/not-ts/index.m3u8", "HLS có đoạn là ảnh PNG, không phải MPEG-TS: từ chối"),
)


# ------------------------------------------------------------------------------------------ media
def paced_file(body: bytes, seconds: float, *, etag: str) -> Route:
    """The whole file in about ``seconds``; the probe's first-MiB reads come at once."""
    delay = seconds / max(1, len(body) // PIECE)

    def route(seen, number):
        sniff = seen.headers.get("range") == f"bytes=0-{SNIFF_BYTES - 1}"
        return Reply(body, content_type="video/mp4", etag=etag, delay=0.0 if sniff else delay, chunk=PIECE)
    return route


def serve_files(server: FixtureServer, media_dir: Path) -> None:
    clip = make_clip(media_dir / "demo.mp4", seconds=20, size="640x360").read_bytes()
    server.route("/clips/demo.mp4", paced_file(clip, 4.0, etag='"demo-1"'))
    long = make_clip(media_dir / "long.mp4", seconds=120, size="640x360").read_bytes()
    server.route("/clips/long.mp4", paced_file(long, 30.0, etag='"long-1"'))
    silent = make_clip(media_dir / "silent.mp4", seconds=6, audio=False).read_bytes()
    server.route("/no-audio/clip.mp4", Reply(silent, content_type="video/mp4"))


def serve_media_playlist(server: FixtureServer, prefix: str, media, *, seconds_per_segment: float = 0.0,
                         token=None) -> None:
    """``<prefix>/index.m3u8`` and its segments ``<prefix>/seg/N.ts``, each sent in about that many seconds
    (segment 1 at once: the probe reads it)."""
    count = len(media.segments)
    delays = {index: seconds_per_segment / max(1, len(media.segments[index - 1]) // PIECE)
              for index in range(2, count + 1)} if seconds_per_segment else None

    def playlist(seen, number):
        suffix = f"?token={token()}" if token else ""
        uris = [f"{prefix}/seg/{index}.ts{suffix}" for index in range(1, count + 1)]
        return Reply(media_playlist(uris, media.durations).encode(), content_type=HLS_TYPE)
    server.route(f"{prefix}/index.m3u8", playlist)
    route = segment_route(media, token=token, delays=delays, chunk=PIECE)
    for index in range(1, count + 1):
        server.route(f"{prefix}/seg/{index}.ts", route)


def serve_playlists(server: FixtureServer, media_dir: Path) -> None:
    slow = make_hls(media_dir / "slow", seconds=120, segment_seconds=2, size="640x360")
    serve_media_playlist(server, "/slow", slow, seconds_per_segment=4.0)
    variants = []
    for name, size, bandwidth in (("360", "640x360", 800_000), ("720", "1280x720", 2_500_000)):
        media = make_hls(media_dir / f"show-{name}", seconds=40, segment_seconds=2, size=size)
        serve_media_playlist(server, f"/show/{name}", media, seconds_per_segment=0.3)
        variants.append({"uri": f"/show/{name}/index.m3u8", "bandwidth": bandwidth, "resolution": size,
                         "codecs": "avc1.64001f,mp4a.40.2"})
    server.route("/show/index.m3u8", Reply(master_playlist(variants).encode(), content_type=HLS_TYPE))
    token_media = make_hls(media_dir / "token", seconds=30, segment_seconds=2)
    started = time.monotonic()
    serve_media_playlist(server, "/token", token_media, seconds_per_segment=2.0,
                         token=lambda: f"t{int((time.monotonic() - started) // TOKEN_SECONDS)}")
    images = [PNG_SIGNATURE + segment for segment in token_media.segments[:3]]
    uris = [f"/not-ts/seg/{index}.png" for index in range(1, 4)]
    server.route("/not-ts/index.m3u8", Reply(media_playlist(uris, token_media.durations[:3]).encode(),
                                             content_type=HLS_TYPE))
    for uri, image in zip(uris, images):
        server.route(uri, Reply(image, content_type="image/png"))


def serve_refused(server: FixtureServer) -> None:
    drm = ("#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI=\"skd://key\"\n"
           "#EXTINF:4.0,\n/drm/1.ts\n#EXT-X-ENDLIST\n")
    server.route("/drm/index.m3u8", Reply(drm.encode(), content_type=HLS_TYPE))
    live = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\n/live/1.ts\n"
    server.route("/live/index.m3u8", Reply(live.encode(), content_type=HLS_TYPE))


# ------------------------------------------------------------------------------------- the center
def work_dir(prefix: str) -> Path:
    """A new folder in the install's temp (never drive C)."""
    base = INSTALL_ROOT / "temp"
    base.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))


def make_root() -> Path:
    root = work_dir(WORK_PREFIX)
    for name in ("input", "state", "temp", "config"):
        (root / name).mkdir(exist_ok=True)
    for item in (ROOT / "config").iterdir():
        if item.is_file() and ".local." not in item.name:
            (root / "config" / item.name).write_bytes(item.read_bytes())
    return root


def demo_resolver(host: str, port: int) -> list[str]:
    """``*.example`` links answer the address the local servers stand for; other hosts use DNS."""
    return [PUBLIC_ADDRESS] if host.endswith(".example") else default_resolver(host, port)


def demo_connector(servers: dict[int, FixtureServer]):
    """That address on port 80 / 443 goes to the local http / https server; any other to the network."""
    def connect(address: str, port: int, timeout: float) -> socket.socket:
        if address == PUBLIC_ADDRESS and port in servers:
            return socket.create_connection(("127.0.0.1", servers[port].port), timeout=timeout)
        return default_connector(address, port, timeout)
    return connect


def build_center(root: Path, port: int, http: SafeHttp) -> ControlCenter:
    center = ControlCenter(root, host="127.0.0.1", port=port, stable_seconds=5.0, import_existing=False)
    center.downloads.stop()  # its own worker never started; ours replaces it on the same database
    store = DownloadStore(root / "state" / "downloads.sqlite3")
    worker = DownloadWorker(root, store, ffmpeg=FFMPEG, ffprobe=FFPROBE, resolver=demo_resolver, http=http,
                            transfers=SourceTransfers(http, ffmpeg=FFMPEG, ffprobe=FFPROBE))
    center.downloads = DownloadService(root, cleanable=center.cleanable_sources,
                                       bin_reader=recycle_bin.volume_bin_info, store=store, worker=worker)
    return center


def port_is_free(port: int) -> bool:
    with socket.socket() as probe:  # no SO_REUSEADDR: a port another program listens on is refused
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def report_input(root: Path) -> None:
    """The files of ``root``'s input with their streams (the project's ffprobe)."""
    files = sorted(path for path in (root / "input").iterdir() if path.is_file())
    if not files:
        print("input của root thử chưa có file.", flush=True)
        return
    print(f"File trong {root / 'input'}:", flush=True)
    for path in files:
        probe = probe_streams(FFPROBE, path)
        if probe is None:
            print(f"  {path.name}: ffprobe không đọc được", flush=True)
            continue
        info = stream_summary(probe)
        video = f"hình {info['video_codec']} {info['width']}x{info['height']}" if info["has_video"] else "KHÔNG có hình"
        audio = f"tiếng {info['audio_codec']}" if info["has_audio"] else "KHÔNG có tiếng"
        seconds = info["duration_seconds"] or 0
        print(f"  {path.name}: {path.stat().st_size / 1024**2:.1f} MB · {seconds:.1f} giây · {video} · {audio}",
              flush=True)


def serve(port: int) -> int:
    root = make_root()
    print("Đang tạo video mẫu bằng FFmpeg của dự án…", flush=True)
    files = make_tls_files(root / "tls", (HOST,), days=7)
    plain = FixtureServer()
    secure = FixtureServer(tls=files.server_context(), routes=plain.routes)
    try:
        media_dir = root / "fixture-media"
        serve_files(plain, media_dir)
        serve_playlists(plain, media_dir)
        serve_refused(plain)
        http = SafeHttp(resolver=demo_resolver, connector=demo_connector({80: plain, 443: secure}),
                        ssl_context=files.client_context(with_system=True))
        center = build_center(root, port, http)
        print(f"\nTrang thử: http://127.0.0.1:{port}/dashboard-v2/#downloads", flush=True)
        print("Link mẫu (chỉ có trên máy này; đổi https:// thành http:// để thử không mã hóa):", flush=True)
        for path, text in LINKS:
            print(f"  https://{HOST}{path:<20} {text}", flush=True)
        print(f"\nRoot tạm: {root}", flush=True)
        print(f"Kiểm tra hình/tiếng: .venv\\Scripts\\python.exe -m tests.try_source_downloads --check-input {root}",
              flush=True)
        print("Ctrl+C để dừng (root tạm được giữ lại để xem, xóa sau).\n", flush=True)
        center.serve()
    except KeyboardInterrupt:
        pass
    finally:
        secure.close()
        plain.close()
        print("\nĐã dừng Control Center thử.", flush=True)
        report_input(root)
        print(f"Root tạm: {root}", flush=True)
    return 0


# -------------------------------------------------------------------------------------- one link
def probe_only(url: str) -> int:
    """Resolve ``url`` once as PROBING does (real DNS, Windows' certificates) and print the outcome."""
    print(f"Link: {public_url(url)}", flush=True)
    try:
        check_link(url)
    except LinkRejected as error:
        print(f"Bị từ chối trước khi tải ({error.code}): {error.message}")
        return 2
    work = work_dir("try-source-probe-")
    try:
        provider = default_registry(work).provider_for(url)
        if provider is None:
            print("Không phải link file video hay playlist HLS: trên trang nó đi yt-dlp như mọi link khác.")
            return 0
        ctx = ResolveContext(http=SafeHttp(), control=ProcessControl(), task_dir=work, ffprobe=FFPROBE,
                             log=lambda line: print(f"  {mask_line(line)}", flush=True))
        try:
            source = provider.resolve(url, ctx)
        except SourceDeclined as declined:
            print(f"{provider.label}: {declined.message}; trên trang link này sẽ đi yt-dlp.")
            return 0
        except (SourceError, HttpError) as error:
            print(f"Không tải ({error.code}): {error.message}")
            return 1
    finally:
        remove_tree(work)
    print(f"Nhận được: {describe_source(source)}")
    if source.transport == "hls":
        print(f"  {source.fragments} đoạn MPEG-TS; ước tính {(source.estimated_bytes or 0) / 1024**2:.0f} MB")
    print("Hình và tiếng: có (đã kiểm phần đầu bằng ffprobe). Trên trang thử, link này sẽ được BiliFlow tự tải.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8797, help=f"port of the test Control Center (not {REAL_PORT})")
    parser.add_argument("--probe", metavar="URL", help="only resolve this link, as PROBING does")
    parser.add_argument("--check-input", metavar="ROOT", type=Path, help="list a test root's input and its streams")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):  # Vietnamese text in any terminal, even a redirected one
        stream.reconfigure(encoding="utf-8", errors="replace")
    temp = INSTALL_ROOT / "temp"
    os.environ["TEMP"] = os.environ["TMP"] = str(temp)  # FFmpeg and Python's own temp files stay on drive E
    tempfile.tempdir = str(temp)
    if not HAVE_FFMPEG:
        parser.error(f"the project's FFmpeg is missing ({FFMPEG}); set BILIFLOW_FFMPEG")
    if args.check_input is not None:
        target = args.check_input.resolve()
        if target.parent != temp.resolve() or not target.name.startswith(WORK_PREFIX):
            parser.error(f"--check-input takes a {temp}\\{WORK_PREFIX}* folder")
        report_input(target)
        return 0
    if args.probe:
        return probe_only(args.probe)
    if not HAVE_TLS:
        parser.error(NEED_TLS)
    if args.port == REAL_PORT:
        parser.error(f"{REAL_PORT} is the real Control Center's port; choose another one")
    if not port_is_free(args.port):
        parser.error(f"port {args.port} is in use on 127.0.0.1; choose another one (--port)")
    return serve(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
