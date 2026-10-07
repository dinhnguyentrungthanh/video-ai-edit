"""Native public page adapters. Exact real hosts are configured locally, never in this module.

No page JavaScript is executed. Only known player data and literal URL transforms are read. All derived
requests use SafeHttp. Expiring links remain in memory; public identities contain hashes and selections.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import struct
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit

from biliflow.download_hls import resolve_hls
from biliflow.download_media_file import content_range, resolve_file
from biliflow.download_source_types import ResolveContext, ResolvedSource, SourceChanged, SourceError, stable_url
from biliflow.download_transfer import probe_streams, require_audio_video, stream_summary

MAX_PAGE = 2 * 1024 * 1024
MAX_INDEX = 8 * 1024 * 1024


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bad() -> SourceError:
    return SourceError("PLAYER_CHANGED", "Không đọc được nguồn phim theo cấu trúc trình phát đã hỗ trợ.")


class Page(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.title = ""
        self._title = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "title":
            self._title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False

    def handle_data(self, data):
        if self._title:
            self.title += data


def fetch_text(url: str, ctx: ResolveContext, referer: str) -> tuple[str, str]:
    body, response = ctx.http.fetch(url, ctx.control, limit=MAX_PAGE, headers={"Referer": referer})
    try:
        return body.decode("utf-8-sig"), response.url
    except UnicodeDecodeError:
        raise bad() from None


class SourceNeedsChoice(Exception):
    def __init__(self, choices: list[dict[str, str]]):
        self.choices = choices
        self.message = "Có nhiều bản phim; chọn bản muốn tải."
        super().__init__(self.message)


class PlayerHlsProvider:
    id = "player-hls"
    label = "Nguồn tập trong trình phát"

    def __init__(self, hosts):
        self.hosts = hosts

    def claims(self, url: str) -> bool:
        return self.hosts.matches(url) and urlsplit(url).path.startswith("/phim/")

    def resolve(self, url: str, ctx: ResolveContext) -> ResolvedSource:
        page, final = fetch_text(url, ctx, url)
        frames = [attrs.get("src") for tag, attrs in Page(page).tags
                  if tag == "iframe" and attrs.get("id") == "embed-player"]
        if len(frames) != 1 or not frames[0]:
            raise bad()
        embed, embed_url = fetch_text(urljoin(final, frames[0]), ctx, final)
        match = re.search(r"\b(?:var|let|const)\s+episode\s*=\s*", embed)
        if not match:
            raise bad()
        try:
            episode, _ = json.JSONDecoder().raw_decode(embed[match.end():].lstrip())
            if not isinstance(episode, dict) or not isinstance(episode.get("id"), (str, int)):
                raise ValueError()
            encoded = episode["encrypted_url"]
            if not isinstance(encoded, str) or len(encoded) > 16_384:
                raise ValueError()
            source_bytes = bytes.fromhex(encoded)
        except (ValueError, KeyError, TypeError, RecursionError):
            raise bad() from None
        scripts = [attrs.get("src") for tag, attrs in Page(embed).tags if tag == "script"]
        scripts = [src for src in scripts if src and urlsplit(src).path.endswith("/media_player.js")]
        if len(scripts) != 1:
            raise bad()
        player, _ = fetch_text(urljoin(embed_url, scripts[0]), ctx, embed_url)
        key_match = re.search(r"hexXorDecrypt\(\s*episode\.encrypted_url\s*,\s*(['\"])([^'\"]{1,256})\1\s*\)", player)
        if not key_match:
            raise bad()
        try:
            key = key_match.group(2).encode("ascii")
            media = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(source_bytes)).decode("utf-8")
        except (UnicodeError, ValueError):
            raise bad() from None
        ctx.http.check(media)
        referer = episode.get("http_referer") or embed_url
        if not isinstance(referer, str):
            raise bad()
        ctx.http.check(referer)
        source = resolve_hls(media, ctx, provider=self.id, label=self.label,
                             headers={"Referer": referer}, strip_png=True)
        episode_identity = {name: str(episode.get(name, ""))[:200]
                            for name in ("id", "movieId", "season_number", "server")}
        title = " · ".join(item for item in (Page(embed).title.strip(), str(episode.get("name") or ""),
                                             str(episode.get("server") or "")) if item)[:300]
        return replace(source, title=title or "Tập phim", identity={**source.identity,
                       "page": fingerprint(stable_url(url)), "episode": episode_identity})


def direct_sources(version: dict) -> list[str]:
    sources: list[str] = []
    for field in ("link", "link2", "link3"):
        value = version.get(field)
        if not isinstance(value, str) or not value:
            continue
        try:
            parts = urlsplit(value)
            candidate = value if parts.path.lower().endswith(".mp4") else parse_qs(parts.query).get("s", [""])[0]
            parts = urlsplit(candidate)
        except ValueError:
            raise bad() from None
        if parts.scheme in ("http", "https") and parts.hostname and parts.path.lower().endswith(".mp4"):
            if candidate not in sources:
                sources.append(candidate)
    return sources


def _range(ctx: ResolveContext, url: str, referer: str, offset: int, count: int) -> bytes:
    with ctx.http.open(url, ctx.control, headers={"Referer": referer,
                       "Range": f"bytes={offset}-{offset + count - 1}"}) as response:
        ranged = content_range(response.header("Content-Range"))
        if offset and (response.status != 206 or not ranged or ranged[0] != offset):
            raise SourceError("METADATA_UNAVAILABLE", "Máy chủ không cho đọc chỉ mục MP4 theo đoạn byte.")
        return response.read_some(count)


def mp4_summary(source: ResolvedSource, ctx: ResolveContext, referer: str) -> dict:
    """Read only MP4 box headers and the bounded moov index, even when it is after a large mdat.

    ffprobe receives a LOCAL metadata sample, never a URL (so it cannot bypass network checks). The
    download worker still decodes and validates the actual completed media before publishing it.
    """
    total = source.estimated_bytes
    if not total or ctx.ffprobe is None:
        raise SourceError("METADATA_UNAVAILABLE", "Chưa xác minh được thời lượng nguồn phim.")
    offset, ftyp, moov = 0, b"", None
    for _ in range(128):
        head = _range(ctx, source.media_url, referer, offset, 16)
        if len(head) < 8:
            raise bad()
        length, kind = struct.unpack(">I4s", head[:8])
        header_size = 8
        if length == 1:
            if len(head) < 16:
                raise bad()
            length, header_size = struct.unpack(">Q", head[8:16])[0], 16
        if length == 0:
            length = total - offset
        if length < header_size or offset + length > total:
            raise bad()
        if kind in (b"ftyp", b"moov"):
            if length > MAX_INDEX:
                raise SourceError("METADATA_TOO_LARGE", "Chỉ mục MP4 lớn quá giới hạn đọc trước.")
            body = _range(ctx, source.media_url, referer, offset, length)
            if len(body) != length:
                raise bad()
            if kind == b"ftyp":
                ftyp = body
            else:
                moov = body
                break
        offset += length
        if offset >= total:
            break
    if not moov:
        raise SourceError("METADATA_UNAVAILABLE", "Không tìm thấy chỉ mục thời lượng MP4.")
    sample = ctx.task_dir / "movie-index.mp4"
    sample.write_bytes(ftyp + moov)
    try:
        probe = probe_streams(ctx.ffprobe, sample, input_format="mov")
    finally:
        sample.unlink(missing_ok=True)
    if not probe:
        raise bad()
    result = stream_summary(probe)
    require_audio_video(result, "Nguồn phim")
    return result


class ArticleMp4Provider:
    id = "article-mp4"
    label = "Nguồn phim qua API"
    minimum_seconds = 600

    def __init__(self, hosts):
        self.hosts = hosts

    def claims(self, url: str) -> bool:
        return self.hosts.matches(url) and urlsplit(url).path.rstrip("/") == "/videoinfo"

    def resolve(self, url: str, ctx: ResolveContext) -> ResolvedSource:
        parts = urlsplit(url)
        codes = parse_qs(parts.query).get("id", [])
        if len(codes) != 1 or not re.fullmatch(r"[\w-]{1,200}", codes[0]):
            raise bad()
        code = codes[0].split("_")[0]
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        payload = json.dumps({"article_code": code, "filter_type": "extra",
                              "actionType": "getArticleDetail"}).encode("utf-8")
        body, _ = ctx.http.fetch(origin + "/webapi/index", ctx.control, limit=MAX_PAGE,
                                headers={"Referer": url, "Origin": origin, "Content-Type": "application/json"},
                                method="POST", body=payload)
        try:
            data = json.loads(body)
            films = [item for item in data if isinstance(item, dict) and item.get("article_code") == code]
            if not isinstance(data, list) or len(films) != 1:
                raise ValueError()
            film = films[0]
            versions = film.get("extra_info", [])
            if isinstance(versions, str):
                versions = json.loads(unquote(versions))
            if not isinstance(versions, list) or len(versions) > 100 or not all(isinstance(v, dict) for v in versions):
                raise ValueError()
        except (ValueError, TypeError, RecursionError):
            raise bad() from None
        options = []
        for version in versions:
            sources = direct_sources(version)
            if sources:
                name = str(version.get("name") or "Bản phim")[:200]
                selection = fingerprint(f"{len(options)}:{name}")
                options.append((selection, name, sources))
        if not options:
            raise SourceError("NO_MOVIE_SOURCE", "Không có nguồn phim MP4 trong dữ liệu bản phim.")
        chosen = (ctx.previous or {}).get("selection")
        if chosen is None and len(options) > 1:
            raise SourceNeedsChoice([{"selection": selection, "title": name} for selection, name, _ in options])
        option = next((item for item in options if item[0] == chosen), None) if chosen else options[0]
        if option is None:
            raise SourceChanged("bản phim đã chọn không còn")
        selection, name, candidates = option
        source = resolve_file(candidates[0], ctx, provider=self.id, label=self.label, headers={"Referer": url})
        if ctx.ffprobe is not None:
            metadata_fields = ("duration_seconds", "video_codec", "audio_codec", "width", "height")
            summary = mp4_summary(source, ctx, url) if source.duration_seconds is None else {
                **{field: getattr(source, field) for field in metadata_fields},
                "has_video": bool(source.video_codec), "has_audio": bool(source.audio_codec)}
            require_audio_video(summary, "Nguồn phim")
            if not summary.get("duration_seconds") or summary["duration_seconds"] < self.minimum_seconds:
                raise SourceError("SOURCE_TOO_SHORT", "Nguồn ngắn hơn 10 phút; có thể là quảng cáo hoặc trailer.")
            source = replace(source, **{field: summary.get(field) for field in metadata_fields})
        media = urlsplit(source.media_url)
        identity = {"kind": "file", "page": fingerprint(stable_url(url)), "selection": selection,
                    "media_path": fingerprint(urlunsplit((media.scheme, media.netloc, media.path, "", ""))),
                    "size": source.estimated_bytes}
        return replace(source, title=(str(film.get("article_title") or "Phim") + " · " + name)[:300], identity=identity)
