"""An optional anonymous browser adapter whose page requests all go through SafeHttp.

Uses the installed Edge and Playwright's Python API directly. It never calls a separate extraction script
or opens a user's browser profile. Playwright is imported only when this provider is configured.
"""
from __future__ import annotations

import time
import os
import tempfile
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from biliflow.download_hls import resolve_hls
from biliflow.download_http import Cancelled, HttpError
from biliflow.download_media_file import PlaylistLink, resolve_file
from biliflow.download_page_sources import fingerprint, mp4_summary
from biliflow.download_source_types import ResolveContext, ResolvedSource, SourceError, stable_url
from biliflow.download_transfer import require_audio_video

MAX_BROWSER_BODY = 2 * 1024 * 1024
REQUEST_HEADERS = {"referer", "origin", "accept", "accept-language", "content-type", "range", "if-range"}


def is_descendant(frame, roots) -> bool:
    while frame is not None:
        if frame in roots:
            return True
        frame = frame.parent_frame
    return False


class CheckedBrowserRequests:
    """Browser network adapter. No route.continue_, browser fetch, or unvalidated redirect is used."""
    def __init__(self, ctx: ResolveContext):
        self.ctx = ctx
        self.media = []
        self.failure = None
        self.page = None

    def handle(self, route):
        request = route.request
        try:
            if self.ctx.control.requested:
                raise Cancelled()
            frame = request.frame
            if urlsplit(request.url).scheme not in ("http", "https"):
                route.abort()
                return
            if self.page is not None and frame.page != self.page:
                route.abort()
                return
            self.ctx.http.check(request.url)
            suffix = urlsplit(request.url).path.lower()
            if request.resource_type == "media" or suffix.endswith((".mp4", ".m3u8")):
                self.media.append((frame, request.url))
                route.abort()  # metadata and video are fetched later, through the normal source transfer
                return
            if request.resource_type not in ("document", "script", "xhr", "fetch", "stylesheet"):
                route.abort()
                return
            method = request.method
            if method not in ("GET", "POST"):
                route.abort()
                return
            headers = {key: value for key, value in request.headers.items() if key.lower() in REQUEST_HEADERS}
            body, response = self.ctx.http.fetch(request.url, self.ctx.control, limit=MAX_BROWSER_BODY,
                                                  headers=headers, method=method, body=request.post_data_buffer)
            # SafeHttp followed redirects itself. Make the browser navigate to the validated final document
            # so its relative script/iframe URLs use the correct base, without doing unchecked networking.
            if response.url != request.url and request.resource_type == "document":
                route.fulfill(status=302, headers={"Location": response.url}, body=b"")
                return
            response_headers = {"Content-Type": response.header("Content-Type") or "application/octet-stream"}
            for key in ("Content-Range", "Accept-Ranges"):
                if response.header(key):
                    response_headers[key] = response.header(key)
            route.fulfill(status=response.status, headers=response_headers, body=body)
        except (HttpError, ValueError) as error:
            self.failure = error if isinstance(error, HttpError) else SourceError("PLAYER_CHANGED", "Yêu cầu trình phát không hợp lệ.")
            route.abort()


def browser_source(url: str, ctx: ResolveContext) -> tuple[str, str, str]:
    try:
        from playwright.sync_api import Error as BrowserError, sync_playwright
    except ImportError:
        raise SourceError("BROWSER_RUNTIME_MISSING", "Bộ đọc trang này cần Playwright trong môi trường BiliFlow.") from None
    bridge = CheckedBrowserRequests(ctx)
    browser = None
    with tempfile.TemporaryDirectory(prefix="browser-", dir=ctx.task_dir) as profile, sync_playwright() as runtime:
        try:
            context = runtime.chromium.launch_persistent_context(
                profile, channel="msedge", headless=True, service_workers="block", accept_downloads=False,
                downloads_path=profile, artifacts_dir=profile,
                env={**os.environ, "TEMP": str(ctx.task_dir), "TMP": str(ctx.task_dir)})
            browser = context.browser
            context.route("**/*", bridge.handle)
            context.route_web_socket("**/*", lambda socket: socket.close())
            page = context.pages[0] if context.pages else context.new_page()
            bridge.page = page
            page.on("popup", lambda popup: popup.close())
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if ctx.control.requested:
                    raise Cancelled()
                if bridge.failure and bridge.failure.code not in ("NETWORK", "UNAVAILABLE", "TOO_LARGE_RESPONSE"):
                    raise bridge.failure
                roots = [element.content_frame() for element in page.locator("#playerled iframe").element_handles()]
                roots = [frame for frame in roots if frame is not None]
                if page.locator("#playerled video").count():
                    roots.append(page.main_frame)
                candidates = []
                for frame in page.frames:
                    if is_descendant(frame, roots):
                        selector = "#playerled video" if frame == page.main_frame else "video"
                        try:
                            videos = frame.locator(selector).evaluate_all("nodes => nodes.map(v => v.currentSrc || v.src)")
                        except BrowserError:
                            continue  # an iframe may navigate while its player initializes
                        for media in videos:
                            if media.startswith(("http://", "https://")):
                                candidates.append((media, frame.url))
                # Blob-based HLS: accept only playlists observed in the identified player subtree.
                candidates.extend((media, frame.url) for frame, media in bridge.media
                                  if is_descendant(frame, roots) and urlsplit(media).path.lower().endswith(".m3u8"))
                unique = list(dict.fromkeys(candidates))
                if len(unique) == 1:
                    media, referer = unique[0]
                    ctx.http.check(media)
                    title = page.locator("h1").first.inner_text() if page.locator("h1").count() else page.title()
                    return media, referer, title[:300]
                if len(unique) > 1:
                    raise SourceError("AMBIGUOUS_PLAYER", "Trình phát có nhiều nguồn phim; chưa chọn được một nguồn duy nhất.")
                page.wait_for_timeout(200)
            if bridge.failure:
                raise bridge.failure
            raise SourceError("PLAYER_CHANGED", "Chưa đọc được nguồn video. Kiểm tra lại link tập phim hoặc bấm Thử lại.")
        except BrowserError:
            if ctx.control.requested:
                raise Cancelled() from None
            if bridge.failure:
                raise bridge.failure
            raise SourceError("BROWSER_FAILED", "Không mở hoặc đọc được trình phát bằng Microsoft Edge.") from None
        finally:
            if browser is not None:
                context.unroute_all(behavior="ignoreErrors")
                browser.close()


class EmbeddedMediaProvider:
    id = "embedded-media"
    label = "Nguồn trong trình phát trang"

    def __init__(self, hosts):
        self.hosts = hosts

    def claims(self, url: str) -> bool:
        return self.hosts.matches(url) and urlsplit(url).path.startswith("/xem/")

    def resolve(self, url: str, ctx: ResolveContext) -> ResolvedSource:
        media, referer, title = browser_source(url, ctx)
        if urlsplit(media).path.lower().endswith(".m3u8"):
            source = resolve_hls(media, ctx, provider=self.id, label=self.label, headers={"Referer": referer})
        else:
            try:
                source = resolve_file(media, ctx, provider=self.id, label=self.label, headers={"Referer": referer})
            except PlaylistLink:
                source = resolve_hls(media, ctx, provider=self.id, label=self.label, headers={"Referer": referer})
            if source.transport == "http_file" and ctx.ffprobe is not None and source.duration_seconds is None:
                summary = mp4_summary(source, ctx, referer)
                require_audio_video(summary, "Nguồn tập phim")
                source = replace(source, **{key: summary.get(key) for key in
                                 ("duration_seconds", "video_codec", "audio_codec", "width", "height")})
        identity = dict(source.identity)
        if source.transport == "http_file":
            parts = urlsplit(media)
            identity = {"kind": "file", "size": source.estimated_bytes,
                        "media_path": fingerprint(urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")))}
        identity["page"] = fingerprint(stable_url(url))
        return replace(source, title=title, identity=identity)
