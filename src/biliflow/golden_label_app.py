"""Local Golden Set labeling page (docs/QUALITY_PLAN.md §12.5).

Serves 127.0.0.1 by default. Phone mode binds the home Wi-Fi address and then
requires a random access code (kept in an HttpOnly cookie) for every request,
pages, video and frames included. Source videos from the manifest are streamed
read-only with HTTP Range; frames are extracted with FFmpeg into reports/benchmarks.
Every write goes through LabelStore and needs the session token.
"""
from __future__ import annotations

import hmac
import html
import http.cookies
import json
import re
import secrets
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from biliflow.golden_set import (
    LabelStore, StaleRevision, canonical_sha256, easy_cards, read_json, source_file, watermark_candidates,
)

CHUNK_BYTES = 1024 * 1024
RANGE = re.compile(r"bytes=(\d*)-(\d*)")
SET_PLACEHOLDER = "__GOLDEN_SET__"  # replaced by the manifest's set name when a page is served


def code_matches(given: str, expected: str) -> bool:
    """Constant-time access-code check that tolerates any text a phone keyboard sends."""
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Inclusive byte range for a single-range request; None means the whole file. Raises on 416."""
    if not header:
        return None
    match = RANGE.fullmatch(header.strip())
    if not match or match.group(1) == match.group(2) == "":
        raise ValueError("unsupported range")
    if match.group(1) == "":
        start, end = max(0, size - int(match.group(2))), size - 1
    else:
        start = int(match.group(1))
        end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
    if start >= size or start > end:
        raise ValueError("unsatisfiable range")
    return start, end


class GoldenLabelApp:
    def __init__(self, root: Path, *, labels_dir: Path, suggestions_path: Path, frames_dir: Path,
                 ffmpeg: Path, host: str = "127.0.0.1", port: int = 8766, access_code: str | None = None):
        self.root = root.resolve()
        self.manifest = read_json(labels_dir / "segments.json")
        suggestions: list[dict[str, Any]] = []
        if suggestions_path.exists():
            payload = read_json(suggestions_path)
            if payload.get("manifest_sha256") != canonical_sha256(self.manifest):
                raise ValueError("suggestions.json được tạo cho manifest khác; chạy lại golden_prefill.py collect")
            suggestions = payload["suggestions"]
        self.suggestions = sorted(suggestions, key=lambda s: (s["segment_id"], s["start_seconds"], s["id"]))
        for suggestion in self.suggestions:  # dense logo windows only carry a coarse routing box
            if suggestion.get("tier") == "dense" and suggestion.get("candidate_type") == "dense_logo_window":
                suggestion["region_approximate"] = True
        self.store = LabelStore(labels_dir, self.manifest, self.suggestions)
        self.set_name = str(self.manifest["golden_set"])  # validated by LabelStore: one of KNOWN_SETS
        self.watermarks = watermark_candidates(self.manifest, self.suggestions)
        self.sources = {key: source_file(self.root, self.manifest, key) for key in self.manifest["sources"]}
        self.frames_dir = frames_dir
        self.ffmpeg = ffmpeg
        self.host, self.port = host, port
        if host not in ("127.0.0.1", "localhost") and not access_code:
            raise ValueError("Opening the labeling page beyond this PC requires an access code")
        self.access_code = access_code
        self.allowed_hosts = {"127.0.0.1", "localhost", host}
        self.token = secrets.token_urlsafe(32)
        self.frame_lock = threading.Lock()
        self.frame_locks: dict[str, threading.Lock] = {}
        self.server: ThreadingHTTPServer | None = None

    def state(self) -> dict[str, Any]:
        labels = self.store.state()
        unresolved = {}
        for segment in self.manifest["segments"]:
            unresolved[segment["id"]] = sum(1 for s in self.suggestions if s["segment_id"] == segment["id"]
                                            and s["id"] not in labels["suggestion_resolutions"])
        sources = {key: {k: v for k, v in source.items() if k != "path"} | {"name": Path(source["path"]).name}
                   for key, source in self.manifest["sources"].items()}
        return {"sources": sources, "segments": self.manifest["segments"], "labels": labels,
                "suggestions": self.suggestions, "unresolved": unresolved,
                "easy": {"watermarks": self.watermarks,
                         "cards": easy_cards(self.suggestions, labels, self.watermarks)}}

    def page(self, easy: bool) -> str:
        """The labeling page, titled with the manifest's Golden Set name."""
        return (EASY_PAGE if easy else PAGE).replace(SET_PLACEHOLDER, html.escape(self.set_name))

    def frame(self, key: str, seconds: float) -> Path:
        source = self.manifest["sources"][key]
        if not 0 <= seconds <= source["duration_seconds"]:
            raise ValueError("Thời điểm nằm ngoài video")
        target = self.frames_dir / f"{key}-{int(round(seconds * 1000)):010d}.jpg"
        with self.frame_lock:  # one lock per frame file: different frames extract in parallel
            lock = self.frame_locks.setdefault(target.name, threading.Lock())
        with lock:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(".tmp.jpg")
                subprocess.run([str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{seconds:.3f}",
                                "-i", str(self.sources[key]), "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "4",
                                str(temporary)], check=True, capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                temporary.replace(target)
        return target

    def mutate(self, path: str, body: dict[str, Any]) -> Any:
        revision = int(body.get("revision", -1))
        parts = path.strip("/").split("/")
        if parts == ["api", "events"]:
            return self.store.upsert_event(body.get("event") or {}, revision)
        if len(parts) == 4 and parts[:2] == ["api", "events"] and parts[3] == "delete":
            self.store.delete_event(parts[2], revision)
            return {"deleted": parts[2]}
        if len(parts) == 4 and parts[:2] == ["api", "suggestions"] and parts[3] == "resolve":
            self.store.resolve_suggestion(parts[2], body.get("resolution"), revision)
            return {"resolved": parts[2]}
        if len(parts) == 4 and parts[:2] == ["api", "suggestions"] and parts[3] == "cover":
            # easy mode "đã có khung xanh": the logo is already labelled, so no duplicate box is drawn
            self.store.cover_suggestion(parts[2], str(body.get("event_id") or ""), revision)
            return {"covered": parts[2]}
        if len(parts) == 4 and parts[:2] == ["api", "segments"] and parts[3] == "status":
            self.store.set_segment_status(parts[2], str(body.get("status")), revision)
            return {"segment": parts[2]}
        if parts == ["api", "film-logos"]:
            created = self.store.add_film_logo(str(body.get("source")), body.get("region_source_pixels"),
                                               float(body.get("start_seconds", 0)), float(body.get("end_seconds", 0)),
                                               revision, category=str(body.get("category") or "visual_logo"),
                                               replace=bool(body.get("replace")))
            return {"events": [e["id"] for e in created]}
        if len(parts) == 4 and parts[:2] == ["api", "watermarks"] and parts[3] in ("confirm", "decline"):
            candidate = next((c for c in self.watermarks if c["id"] == parts[2]), None)
            if candidate is None:
                raise KeyError(parts[2])
            if parts[3] == "confirm":
                return {"events": [e["id"] for e in self.store.confirm_watermark(candidate, revision)]}
            self.store.decline_watermark(candidate["id"], revision)
            return {"declined": candidate["id"]}
        if len(parts) == 4 and parts[:2] == ["api", "segments"] and parts[3] == "reset":
            return {"removed": self.store.reset_segment(parts[2], revision)}
        if len(parts) == 4 and parts[:2] == ["api", "segments"] and parts[3] == "reject-remaining":
            return {"rejected": self.store.reject_remaining(parts[2], bool(body.get("advisory_only", True)), revision)}
        raise KeyError(path)

    def handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def send_bytes(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def send_json(self, status: int, payload: Any) -> None:
                self.send_bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                "application/json; charset=utf-8")

            def local_host(self) -> bool:
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
                return host in app.allowed_hosts

            def has_access(self) -> bool:
                if app.access_code is None:
                    return True
                try:
                    cookie = http.cookies.SimpleCookie(self.headers.get("Cookie") or "").get("golden_access")
                except http.cookies.CookieError:
                    return False
                return cookie is not None and code_matches(cookie.value, app.access_code)

            def gate(self, url) -> bool:
                """Phone mode: a link with ?code=... sets the cookie; anything else needs the cookie."""
                if app.access_code is None or self.has_access():
                    return True
                code = urllib.parse.parse_qs(url.query).get("code", [""])[0].strip().lower()
                if code and code_matches(code, app.access_code):
                    self.send_response(303)
                    self.send_header("Location", url.path or "/")
                    self.send_header("Set-Cookie", f"golden_access={app.access_code}; HttpOnly; SameSite=Strict; Path=/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return False
                if url.path in ("/", "/full"):
                    self.send_bytes(401, LOGIN_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                else:
                    self.send_json(401, {"error": "Cần mã truy cập"})
                return False

            def send_file(self, path: Path, content_type: str) -> None:
                size = path.stat().st_size
                try:
                    wanted = parse_range(self.headers.get("Range"), size)
                except ValueError:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end = wanted or (0, size - 1)
                self.send_response(206 if wanted else 200)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if wanted:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                try:
                    with path.open("rb") as handle:
                        handle.seek(start)
                        remaining = end - start + 1
                        while remaining > 0:
                            chunk = handle.read(min(CHUNK_BYTES, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass  # the browser cancels range requests while seeking

            def do_GET(self) -> None:
                if not self.local_host():
                    self.send_json(403, {"error": "Địa chỉ không hợp lệ"}); return
                url = urllib.parse.urlsplit(self.path)
                if not self.gate(url):
                    return
                try:
                    if url.path in ("/", "/full"):
                        self.send_bytes(200, app.page(url.path == "/").encode("utf-8"), "text/html; charset=utf-8")
                    elif url.path == "/api/session":
                        self.send_json(200, {"token": app.token})
                    elif url.path == "/api/state":
                        self.send_json(200, app.state())
                    elif url.path.startswith("/video/"):
                        key = url.path.split("/", 2)[2]
                        if key not in app.sources:
                            self.send_json(404, {"error": "Không có nguồn này"}); return
                        self.send_file(app.sources[key], "video/mp4")
                    elif url.path.startswith("/frame/"):
                        key = url.path.split("/", 2)[2]
                        if key not in app.sources:
                            self.send_json(404, {"error": "Không có nguồn này"}); return
                        seconds = float(urllib.parse.parse_qs(url.query).get("t", ["nan"])[0])
                        self.send_file(app.frame(key, seconds), "image/jpeg")
                    else:
                        self.send_json(404, {"error": "Không tìm thấy"})
                except ValueError as error:
                    self.send_json(400, {"error": str(error)})
                except Exception as error:  # surfaced to the page, never hides a write
                    self.send_json(500, {"error": str(error)})

            def do_POST(self) -> None:
                if not self.local_host() or not self.has_access() or self.headers.get("X-Golden-Token") != app.token:
                    self.send_json(403, {"error": "Phiên gán nhãn không hợp lệ; tải lại trang"}); return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    result = app.mutate(urllib.parse.urlsplit(self.path).path, body)
                    self.send_json(200, {"result": result, "state": app.state()})
                except StaleRevision as error:
                    self.send_json(409, {"error": str(error)})
                except KeyError:
                    self.send_json(404, {"error": "Không tìm thấy"})
                except ValueError as error:
                    self.send_json(400, {"error": str(error), "code": getattr(error, "code", None)})
                except Exception as error:
                    self.send_json(500, {"error": str(error)})

        return Handler

    def serve(self) -> None:
        self.server = _ExclusiveServer((self.host, self.port), self.handler())
        try:
            self.server.serve_forever()
        finally:
            self.server.server_close()
            self.store.close()

    def start_background(self) -> threading.Thread:
        self.server = _ExclusiveServer((self.host, self.port), self.handler())
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        self.store.close()


class _ExclusiveServer(ThreadingHTTPServer):
    # Without SO_REUSEADDR a second labeling server on Windows fails to bind instead of
    # sharing the port; the LabelStore lock file also refuses a second writer.
    allow_reuse_address = False
    daemon_threads = True


LOGIN_PAGE = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Mã truy cập</title>
<style>body{font:18px system-ui,sans-serif;margin:0;padding:24px;background:#f5f6f8;color:#1d2430}
form{max-width:420px;margin:10vh auto;background:#fff;border:1px solid #d9dee6;border-radius:12px;padding:20px}
input,button{font:inherit;width:100%;padding:12px;margin-top:10px;border-radius:10px;border:1px solid #d9dee6}
button{background:#2457c5;color:#fff;border-color:#2457c5}</style></head><body>
<form method="get" action="/"><b>Gán nhãn BiliFlow</b><p>Nhập mã truy cập hiện trên cửa sổ đen ở máy tính.</p>
<input name="code" autocomplete="off" autocapitalize="none" placeholder="Mã truy cập"><button>Mở trang</button></form></body></html>"""


PAGE = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Gán nhãn Golden Set __GOLDEN_SET__</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--text:#1d2430;--muted:#5d6878;--line:#d9dee6;--accent:#2457c5;--ok:#1f7a45;--bad:#b3261e;--warn:#9a6700;
--logo:#d97706;--text-c:#7c3aed;--adult:#db2777;--gore:#b91c1c;--violence:#475569}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--panel:#1d2128;--text:#e6e9ee;--muted:#9aa4b2;--line:#323843;--accent:#7aa2ff;--ok:#4cc27f;--bad:#ff8a80;--warn:#e3b341}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif}
header{display:flex;gap:16px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:5}
header h1{font-size:16px;margin:0}#progress{color:var(--muted)}#notice{margin-left:auto;font-weight:600}
.layout{display:grid;grid-template-columns:260px 1fr;gap:12px;padding:12px}
nav{display:flex;flex-direction:column;gap:6px;max-height:calc(100vh - 70px);overflow:auto}
nav button{text-align:left;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text);cursor:pointer}
nav button.active{outline:2px solid var(--accent)}nav small{display:block;color:var(--muted)}
.badge{display:inline-block;padding:1px 6px;border-radius:10px;font-size:12px;border:1px solid var(--line)}
.complete{color:var(--ok);border-color:var(--ok)}.in_progress{color:var(--warn);border-color:var(--warn)}
main{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(320px,1fr);gap:12px;align-items:start}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
h2{font-size:15px;margin:0 0 6px}h3{font-size:14px;margin:12px 0 6px}
.stage{position:relative;width:100%;background:#000}.stage video{display:block;width:100%;height:auto}
#overlay{position:absolute;inset:0;pointer-events:none}#overlay.drawing{pointer-events:auto;cursor:crosshair}
.box{position:absolute;border:2px solid;pointer-events:none}.box span{position:absolute;top:-18px;left:-2px;font-size:11px;padding:0 3px;color:#fff}
.controls{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:8px 0}
button.b{padding:5px 9px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--text);cursor:pointer}
button.b:hover{border-color:var(--accent)}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
button.danger{color:var(--bad)}button.on{outline:2px solid var(--accent)}button:disabled{opacity:.5;cursor:not-allowed}
#time{font-variant-numeric:tabular-nums;min-width:210px}
#timeline{position:relative;height:46px;border:1px solid var(--line);border-radius:6px;background:var(--bg);cursor:pointer;margin:6px 0}
#timeline i{position:absolute;height:12px;border-radius:3px;opacity:.85}#timeline b{position:absolute;top:0;bottom:0;width:2px;background:var(--accent)}
form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}form label{display:flex;flex-direction:column;gap:2px;font-size:12px;color:var(--muted)}
form .wide{grid-column:1/-1}input,select,textarea{font:inherit;padding:4px;border:1px solid var(--line);border-radius:5px;background:var(--bg);color:var(--text)}
.list{display:flex;flex-direction:column;gap:6px;max-height:520px;overflow:auto}
.card{border:1px solid var(--line);border-radius:8px;padding:6px;display:grid;grid-template-columns:150px 1fr;gap:8px}
.card.resolved{opacity:.55}.thumb{position:relative;width:150px}.thumb img{width:150px;display:block;border-radius:4px}
.meta{font-size:12px;color:var(--muted);word-break:break-word}.row{display:flex;gap:4px;flex-wrap:wrap;margin-top:4px}
.help{font-size:12px;color:var(--muted);line-height:1.5}
@media (max-width:1100px){.layout{grid-template-columns:1fr}main{grid-template-columns:1fr}nav{max-height:none}}
</style></head><body>
<header><h1>BiliFlow · Gán nhãn Golden Set __GOLDEN_SET__</h1><span id="progress"></span><a href="/">Chế độ dễ</a><span id="notice"></span></header>
<div class="layout"><nav id="segments"></nav><main>
<section><h2 id="seg-title">Chọn một đoạn</h2><div class="help" id="seg-purpose"></div>
<div class="stage" id="stage"><video id="video" controls preload="metadata"></video><div id="overlay"></div></div>
<div class="controls"><span id="time">–</span>
<button class="b" onclick="seekSeg(0)">⏮ Đầu đoạn</button><button class="b" onclick="step(-5)">−5s</button><button class="b" onclick="step(-1)">−1s</button>
<button class="b" onclick="step(1)">+1s</button><button class="b" onclick="step(5)">+5s</button>
<select id="rate" onchange="video.playbackRate=+this.value"><option value="1">1×</option><option value="1.5">1,5×</option><option value="2">2×</option></select>
<button class="b" onclick="setEdge('start')">[ Đặt đầu</button><button class="b" onclick="setEdge('end')">] Đặt cuối</button>
<button class="b" id="draw" onclick="toggleDraw()">✎ Vẽ vùng (R)</button></div>
<div id="timeline" onclick="timelineSeek(event)"></div>
<div class="help">Phím tắt: Space phát/dừng · ←/→ 1 s · Shift+←/→ 5 s · , . từng khung · [ ] đặt đầu/cuối · R vẽ vùng. Vẽ vùng <b>sát</b> logo/chữ cần che. Mọi nhóm đều gán; phần không có nhãn trong đoạn đã xem hết được hiểu là KEEP. 18+/máu me/bạo lực có thật mà bạn vẫn giữ: chọn <b>KEEP · có thật</b> (máy báo là đúng, không phải bẫy).</div>
<h3>Nhãn của đoạn</h3><div class="list" id="labels"></div>
<div class="controls"><button class="b primary" id="complete" onclick="complete()">✔ Đã xem hết đoạn</button><span class="help" id="complete-help"></span></div>
</section>
<section><h2 id="form-title">Nhãn mới</h2>
<form id="form" onsubmit="saveEvent(event)">
<label>Nhóm<select name="category"><option value="visual_logo">Logo / watermark</option><option value="text">Chữ quảng cáo</option><option value="adult">18+</option><option value="gore">Máu me</option><option value="violence">Bạo lực</option></select></label>
<label>Hành động<select name="expected_action" onchange="if(this.value==='KEEP_PRESENT')this.form.severity.value='should_catch'"><option>BLUR</option><option>CUT</option><option value="KEEP">KEEP (bẫy đã biết)</option><option value="KEEP_PRESENT">KEEP · có thật, vẫn giữ (18+/máu me/bạo lực)</option></select></label>
<label>Bắt đầu (giây)<input name="start_seconds" type="number" step="0.001" required></label>
<label>Kết thúc (giây)<input name="end_seconds" type="number" step="0.001" required></label>
<label>Mức độ<select name="severity"><option value="must_catch">must_catch — lọt là lỗi nghiêm trọng</option><option value="should_catch">should_catch</option><option value="nice_to_have">nice_to_have</option></select></label>
<label>Vùng (pixel gốc)<input name="region" readonly placeholder="toàn khung"></label>
<label class="wide"><span><input type="checkbox" name="ambiguous"> Mơ hồ (chính tôi cũng không chắc)</span></label>
<label class="wide">Ghi chú<textarea name="notes" rows="2"></textarea></label>
<div class="row wide"><button class="b primary">Lưu nhãn</button><button class="b" type="button" onclick="clearRegion()">Xóa vùng</button><button class="b" type="button" onclick="resetForm()">Hủy</button></div>
</form>
<h3>Gợi ý <label class="help"><input type="checkbox" id="show-resolved" onchange="render()"> hiện cả gợi ý đã xử lý</label></h3>
<div class="row"><button class="b" onclick="rejectRemaining(true)">Đánh dấu Sai mọi advisory còn lại</button><button class="b" onclick="rejectRemaining(false)">Đánh dấu Sai mọi gợi ý còn lại</button></div>
<div class="list" id="suggestions"></div></section></main></div>
<script>
const COLORS={visual_logo:'var(--logo)',text:'var(--text-c)',adult:'var(--adult)',gore:'var(--gore)',violence:'var(--violence)'};
const NAMES={visual_logo:'Logo',text:'Chữ',adult:'18+',gore:'Máu me',violence:'Bạo lực'};
let token='',S=null,seg=null,editing=null,fromSuggestion=null,region=null,drawing=false,drag=null;
const video=document.getElementById('video'),overlay=document.getElementById('overlay');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=t=>{t=Math.max(0,t);const h=Math.floor(t/3600),m=Math.floor(t%3600/60),s=(t%60).toFixed(1).padStart(4,'0');return (h?h+':':'')+String(m).padStart(h?2:1,'0')+':'+s};
function notice(text,bad){const n=document.getElementById('notice');n.textContent=text;n.style.color=bad?'var(--bad)':'var(--ok)';clearTimeout(n.t);n.t=setTimeout(()=>n.textContent='',4000)}
async function load(){if(!token)token=(await (await fetch('/api/session')).json()).token;S=await (await fetch('/api/state',{cache:'no-store'})).json();if(!seg)seg=S.segments[0].id;render()}
async function post(url,body){body.revision=S.labels.revision;const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Golden-Token':token},body:JSON.stringify(body)});const j=await r.json();if(!r.ok){if(r.status===409)await load();throw Error(j.error||r.status)}S=j.state;render();return j.result}
const segment=()=>S.segments.find(s=>s.id===seg),source=()=>S.sources[segment().source];
const events=()=>S.labels.events.filter(e=>e.segment_id===seg).sort((a,b)=>a.start_seconds-b.start_seconds);
const suggestions=()=>S.suggestions.filter(s=>s.segment_id===seg);
function render(){const done=S.segments.filter(s=>S.labels.segments[s.id].status==='complete').length;document.getElementById('progress').textContent=`${done}/${S.segments.length} đoạn đã xem hết · ${S.labels.events.length} nhãn · revision ${S.labels.revision}`;
document.getElementById('segments').innerHTML=S.segments.map(s=>{const st=S.labels.segments[s.id].status,n=S.labels.events.filter(e=>e.segment_id===s.id).length;return `<button class="${s.id===seg?'active':''}" onclick="selectSeg('${s.id}')"><b>${s.id}</b> · ${esc(S.sources[s.source].name.slice(0,28))}<small>${fmt(s.start_seconds)}–${fmt(s.end_seconds)} · ${s.split} · <span class="badge ${st}">${{unlabeled:'chưa gán',in_progress:'đang gán',complete:'đã xem hết'}[st]}</span> · ${n} nhãn · ${S.unresolved[s.id]} gợi ý chờ</small></button>`}).join('');
const g=segment();document.getElementById('seg-title').textContent=`${g.id} · ${S.sources[g.source].name}`;document.getElementById('seg-purpose').textContent=`${fmt(g.start_seconds)}–${fmt(g.end_seconds)} (${g.split}) · ${g.purpose}`;
const want='/video/'+g.source;if(!video.src.endsWith(want)){video.src=want;video.onloadedmetadata=()=>{video.currentTime=segment().start_seconds;video.onloadedmetadata=null}}
const locked=S.labels.segments[g.id].status==='complete';
document.getElementById('labels').innerHTML=events().map(e=>`<div class="card"><div class="thumb">${thumb(g.source,(e.start_seconds+e.end_seconds)/2,e.region_source_pixels,COLORS[e.category])}</div><div><b>${NAMES[e.category]} · ${e.content_present?'KEEP · có thật · giữ':e.expected_action}${e.ambiguous?' · mơ hồ':''}</b> <span class="badge">${e.severity||''}</span><div class="meta">${fmt(e.start_seconds)}–${fmt(e.end_seconds)} · ${e.id}${e.from_suggestion?' · từ gợi ý':''}<br>${esc(e.notes)}</div><div class="row"><button class="b" onclick="seek(${e.start_seconds})">Xem</button><button class="b" ${locked?'disabled':''} onclick="edit('${e.id}')">Sửa</button><button class="b danger" ${locked?'disabled':''} onclick="removeEvent('${e.id}')">Xóa</button></div></div></div>`).join('')||'<div class="help">Chưa có nhãn.</div>';
const showAll=document.getElementById('show-resolved').checked,res=S.labels.suggestion_resolutions;
document.getElementById('suggestions').innerHTML=suggestions().filter(s=>showAll||!res[s.id]).map(s=>{const r=res[s.id];return `<div class="card ${r?'resolved':''}"><div class="thumb">${thumb(g.source,(s.start_seconds+s.end_seconds)/2,s.region_source_pixels,COLORS[s.category])}</div><div><b>${NAMES[s.category]}</b> ${s.advisory?'<span class="badge">advisory</span>':''} ${s.tier==='dense'?'<span class="badge">quét dày</span>':''} ${s.prior_decision?`<span class="badge">trước đây: ${s.prior_decision}</span>`:''}<div class="meta">${fmt(s.start_seconds)}–${fmt(s.end_seconds)} · đề xuất ${s.suggested_action||'—'} · ${s.merged_count} mục · ${esc(s.labels.join('; ').slice(0,110))}</div>${r?`<div class="meta"><b>${{accepted:'Đã thành nhãn',ambiguous:'Mơ hồ',rejected:'Sai'}[r.resolution]}</b></div>`:''}<div class="row"><button class="b" onclick="seek(${s.start_seconds})">Xem</button>${r||locked?(r&&r.resolution==='rejected'&&!locked?`<button class="b" onclick="unreject('${s.id}')">Bỏ đánh dấu</button>`:''):`<button class="b primary" onclick="accept('${s.id}')">Đúng…</button><button class="b" onclick="reject('${s.id}')">Sai</button><button class="b" onclick="ambiguous('${s.id}')">Mơ hồ</button>`}</div></div></div>`}).join('')||'<div class="help">Không còn gợi ý chờ xử lý.</div>';
const pending=S.unresolved[g.id],btn=document.getElementById('complete');btn.textContent=locked?'↺ Mở lại đoạn để sửa':'✔ Đã xem hết đoạn';btn.disabled=!locked&&pending>0;document.getElementById('complete-help').textContent=locked?'Đoạn đã khóa; chỉ đoạn đã xem hết mới được chấm điểm.':pending?`Còn ${pending} gợi ý chưa xử lý.`:'Chỉ bấm khi đã xem trọn đoạn và gán mọi thứ cần BLUR/CUT.';
drawTimeline();drawOverlay()}
function thumb(key,t,r,color){const src=S.sources[key];let box='';if(r)box=`<div class="box" style="border-color:${color};left:${100*r.x/src.width}%;top:${100*r.y/src.height}%;width:${100*r.width/src.width}%;height:${100*r.height/src.height}%"></div>`;return `<img loading="lazy" src="/frame/${key}?t=${t.toFixed(3)}">${box}`}
function drawTimeline(){const g=segment(),len=g.end_seconds-g.start_seconds,pos=t=>100*(Math.max(g.start_seconds,Math.min(g.end_seconds,t))-g.start_seconds)/len;let html='';
suggestions().forEach(s=>{if(!S.labels.suggestion_resolutions[s.id])html+=`<i title="gợi ý" style="top:4px;left:${pos(s.start_seconds)}%;width:max(3px,${pos(s.end_seconds)-pos(s.start_seconds)}%);background:${COLORS[s.category]};opacity:.35"></i>`});
events().forEach(e=>html+=`<i title="${e.id}" style="top:24px;left:${pos(e.start_seconds)}%;width:max(3px,${pos(e.end_seconds)-pos(e.start_seconds)}%);background:${COLORS[e.category]}"></i>`);
html+=`<b style="left:${pos(video.currentTime||g.start_seconds)}%"></b>`;document.getElementById('timeline').innerHTML=html}
function drawOverlay(){if(!S)return;const src=source(),t=video.currentTime,boxes=[];const pct=(r,color,label,dash)=>`<div class="box" style="border-color:${color};border-style:${dash?'dashed':'solid'};left:${100*r.x/src.width}%;top:${100*r.y/src.height}%;width:${100*r.width/src.width}%;height:${100*r.height/src.height}%"><span style="background:${color}">${label}</span></div>`;
events().forEach(e=>{if(e.region_source_pixels&&e.start_seconds<=t&&t<=e.end_seconds&&(!editing||editing!==e.id))boxes.push(pct(e.region_source_pixels,COLORS[e.category],NAMES[e.category]+' '+e.expected_action))});
suggestions().forEach(s=>{if(s.region_source_pixels&&!S.labels.suggestion_resolutions[s.id]&&s.start_seconds<=t&&t<=s.end_seconds)boxes.push(pct(s.region_source_pixels,COLORS[s.category],'gợi ý',true))});
if(region)boxes.push(pct(region,'#facc15','vùng đang sửa'));if(drag&&drag.box)boxes.push(pct(drag.box,'#facc15','',true));overlay.innerHTML=boxes.join('');
const g=segment();document.getElementById('time').textContent=`${fmt(t)} (đoạn +${fmt(t-g.start_seconds)})`;const b=document.querySelector('#timeline b');if(b)b.style.left=100*(Math.max(g.start_seconds,Math.min(g.end_seconds,t))-g.start_seconds)/(g.end_seconds-g.start_seconds)+'%'}
video.addEventListener('timeupdate',()=>{const g=segment();if(g&&!video.paused&&video.currentTime>=g.end_seconds){video.pause();notice('Đã tới cuối đoạn')}drawOverlay()});video.addEventListener('seeked',drawOverlay);
function selectSeg(id){const same=seg&&segment().source===S.segments.find(s=>s.id===id).source;seg=id;resetForm();render();if(same&&video.readyState>0)seek(segment().start_seconds)}
function seek(t){video.pause();video.currentTime=t;}
function seekSeg(offset){seek(segment().start_seconds+offset)}
function step(d){seek(Math.max(0,video.currentTime+d))}
function timelineSeek(ev){const r=ev.currentTarget.getBoundingClientRect(),g=segment();seek(g.start_seconds+(ev.clientX-r.left)/r.width*(g.end_seconds-g.start_seconds))}
const F=()=>document.getElementById('form');
function setEdge(which){F()[which==='start'?'start_seconds':'end_seconds'].value=video.currentTime.toFixed(3)}
function showRegion(){F().region.value=region?`${region.x},${region.y} ${region.width}×${region.height}`:'';drawOverlay()}
function clearRegion(){region=null;showRegion()}
function toggleDraw(){drawing=!drawing;overlay.classList.toggle('drawing',drawing);document.getElementById('draw').classList.toggle('on',drawing);if(drawing)video.pause()}
function point(ev){const r=overlay.getBoundingClientRect(),src=source();return {x:Math.max(0,Math.min(src.width,(ev.clientX-r.left)/r.width*src.width)),y:Math.max(0,Math.min(src.height,(ev.clientY-r.top)/r.height*src.height))}}
overlay.addEventListener('pointerdown',ev=>{if(!drawing)return;overlay.setPointerCapture(ev.pointerId);drag={a:point(ev)}});
overlay.addEventListener('pointermove',ev=>{if(!drag)return;const b=point(ev);drag.box={x:Math.round(Math.min(drag.a.x,b.x)),y:Math.round(Math.min(drag.a.y,b.y)),width:Math.round(Math.abs(b.x-drag.a.x)),height:Math.round(Math.abs(b.y-drag.a.y))};drawOverlay()});
overlay.addEventListener('pointerup',()=>{if(drag&&drag.box&&drag.box.width>=4&&drag.box.height>=4){region=drag.box;showRegion()}drag=null;toggleDraw()});
function resetForm(){editing=null;fromSuggestion=null;region=null;const f=F();f.reset();document.getElementById('form-title').textContent='Nhãn mới';showRegion()}
function fill(v){const f=F();f.category.value=v.category;f.expected_action.value=v.content_present&&v.expected_action==='KEEP'?'KEEP_PRESENT':v.expected_action;f.start_seconds.value=v.start_seconds;f.end_seconds.value=v.end_seconds;f.severity.value=v.severity||'must_catch';f.ambiguous.checked=!!v.ambiguous;f.notes.value=v.notes||'';region=v.region_source_pixels?{...v.region_source_pixels}:null;showRegion()}
function edit(id){const e=S.labels.events.find(x=>x.id===id);resetForm();editing=id;fromSuggestion=e.from_suggestion;fill(e);document.getElementById('form-title').textContent='Sửa '+id;seek(e.start_seconds)}
function fromSug(s,ambig){const act=['BLUR','CUT'].includes(s.prior_decision)?s.prior_decision:['BLUR','CUT'].includes(s.suggested_action)?s.suggested_action:(s.group==='advertising'?'BLUR':'CUT');return {category:s.category,expected_action:act,start_seconds:s.start_seconds,end_seconds:s.end_seconds,severity:s.category==='text'?'should_catch':'must_catch',ambiguous:ambig,notes:'',region_source_pixels:s.region_source_pixels}}
function accept(id){const s=S.suggestions.find(x=>x.id===id);resetForm();fromSuggestion=id;fill(fromSug(s,false));document.getElementById('form-title').textContent='Xác nhận gợi ý — kiểm tra thời gian, vùng, mức độ rồi Lưu';seek(s.start_seconds)}
async function ambiguous(id){const s=S.suggestions.find(x=>x.id===id);if(fromSuggestion===id)resetForm();try{await post('/api/events',{event:{...fromSug(s,true),segment_id:seg,from_suggestion:id}});notice('Đã lưu nhãn mơ hồ')}catch(e){notice(e.message,true)}}
async function reject(id){if(fromSuggestion===id)resetForm();try{await post(`/api/suggestions/${id}/resolve`,{resolution:'rejected'});notice('Đã đánh dấu Sai')}catch(e){notice(e.message,true)}}
async function unreject(id){try{await post(`/api/suggestions/${id}/resolve`,{resolution:null});notice('Đã bỏ đánh dấu')}catch(e){notice(e.message,true)}}
async function saveEvent(ev){ev.preventDefault();const f=F(),act=f.expected_action.value,present=act==='KEEP_PRESENT';const payload={id:editing,segment_id:seg,category:f.category.value,expected_action:present?'KEEP':act,content_present:present,start_seconds:+f.start_seconds.value,end_seconds:+f.end_seconds.value,severity:act==='KEEP'?null:f.severity.value,ambiguous:f.ambiguous.checked,notes:f.notes.value,region_source_pixels:region,from_suggestion:fromSuggestion};try{const saved=await post('/api/events',{event:payload});notice('Đã lưu '+saved.id);resetForm()}catch(e){notice(e.message,true)}}
async function removeEvent(id){if(!confirm('Xóa nhãn '+id+'? Lịch sử vẫn giữ bản cũ.'))return;try{await post(`/api/events/${id}/delete`,{});notice('Đã xóa '+id)}catch(e){notice(e.message,true)}}
async function rejectRemaining(advisoryOnly){if(!confirm(advisoryOnly?'Đánh dấu Sai mọi gợi ý advisory chưa xử lý của đoạn này?':'Đánh dấu Sai MỌI gợi ý chưa xử lý của đoạn này? Chỉ làm khi đã xem hết đoạn.'))return;try{const r=await post(`/api/segments/${seg}/reject-remaining`,{advisory_only:advisoryOnly});notice(`Đã đánh dấu Sai ${r.rejected} gợi ý`)}catch(e){notice(e.message,true)}}
async function complete(){const locked=S.labels.segments[seg].status==='complete';if(!locked&&!confirm('Xác nhận đã xem trọn đoạn và gán mọi thứ cần BLUR/CUT?'))return;try{await post(`/api/segments/${seg}/status`,{status:locked?'in_progress':'complete'});notice(locked?'Đã mở lại đoạn':'Đoạn đã xem hết')}catch(e){notice(e.message,true)}}
document.addEventListener('keydown',ev=>{if(['INPUT','TEXTAREA','SELECT'].includes(ev.target.tagName))return;const k=ev.key;const fps=S?source().fps:25;
if(k===' '){ev.preventDefault();video.paused?video.play():video.pause()}else if(k==='ArrowLeft'){ev.preventDefault();step(ev.shiftKey?-5:-1)}else if(k==='ArrowRight'){ev.preventDefault();step(ev.shiftKey?5:1)}
else if(k===',')step(-1/fps);else if(k==='.')step(1/fps);else if(k==='[')setEdge('start');else if(k===']')setEdge('end');else if(k==='r'||k==='R')toggleDraw()});
load().catch(e=>notice(e.message,true));
</script></body></html>"""


# Default page: guided questions with the same KEEP/BLUR/CUT choices as the Dashboard review.
EASY_PAGE = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Gán nhãn Golden Set __GOLDEN_SET__ — chế độ dễ</title>
<style>
:root{--bg:#f5f6f8;--panel:#fff;--text:#1d2430;--muted:#5d6878;--line:#d9dee6;--accent:#2457c5;--ok:#1f7a45;--bad:#b3261e;--warn:#9a6700;--present:#0f766e}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--panel:#1d2128;--text:#e6e9ee;--muted:#9aa4b2;--line:#323843;--accent:#7aa2ff;--ok:#4cc27f;--bad:#ff8a80;--warn:#e3b341;--present:#5eead4}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px system-ui,sans-serif}
header{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:5}
header h1{font-size:18px;margin:0}header a{color:var(--accent)}#progress{color:var(--muted)}#notice{margin-left:auto;font-weight:600}
main{max-width:1180px;margin:0 auto;padding:14px 16px;display:flex;flex-direction:column;gap:14px}
section{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
h2{font-size:18px;margin:0 0 8px}.muted{color:var(--muted)}.small{font-size:13px}
.guide ol{margin:6px 0 0 18px;padding:0;line-height:1.6}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{font:inherit;cursor:pointer;border-radius:10px;border:1px solid var(--line);background:var(--panel);color:var(--text);padding:8px 12px}
button.big{font-size:17px;padding:12px 18px;min-width:150px;font-weight:600}
button.keep{border-color:var(--ok);color:var(--ok)}button.blur{border-color:var(--accent);color:var(--accent)}
button.cut{border-color:var(--bad);color:var(--bad)}button.unsure{border-color:var(--warn);color:var(--warn)}
button.present{border-color:var(--present);color:var(--present)}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button:disabled{opacity:.45;cursor:not-allowed}
.thumbs{display:flex;gap:8px;flex-wrap:wrap}.thumb{position:relative;width:min(480px,100%)}.thumb img{width:100%;display:block;border-radius:8px}
.box{position:absolute;border:3px solid #ef4444;pointer-events:none;border-radius:2px}
.segs{display:flex;gap:6px;flex-wrap:wrap}.segs button{font-size:14px}.segs button.active{outline:3px solid var(--accent)}
.done{color:var(--ok);font-weight:600}
.layout{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:14px;align-items:start}
.stage{position:relative;background:#000;border-radius:8px;overflow:hidden}.stage video{display:block;width:100%;height:auto}
#overlay{position:absolute;inset:0;pointer-events:none}#overlay.drawing{pointer-events:auto;cursor:crosshair;touch-action:none}
.stage.drawing-now{outline:5px solid #f59e0b;outline-offset:2px}.stage .drawtip{position:absolute;left:50%;top:10px;transform:translateX(-50%);background:#f59e0b;color:#111;font-weight:700;padding:6px 12px;border-radius:8px;z-index:3;pointer-events:none}
.prior{font-weight:700;padding:4px 8px;border-radius:6px;background:#fef3c7;color:#7c2d12;display:inline-block}
.card{border:2px solid var(--line);border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:10px}
.question{font-size:18px;font-weight:600}.hint{font-size:13px;color:var(--muted);line-height:1.5}
.labels li{margin:3px 0}.flag{font-size:18px;padding:14px 20px}
@media (max-width:900px){.layout{grid-template-columns:1fr}button.big{min-width:44%}}
</style></head><body>
<header><h1>BiliFlow · Gán nhãn Golden Set __GOLDEN_SET__ (chế độ dễ)</h1><span id="progress"></span><a href="/full">Chế độ đầy đủ</a><span id="notice"></span></header>
<main>
<section class="guide"><h2>Bạn cần làm gì</h2>
<ol><li><b>Bước 1:</b> trả lời mỗi phim một câu về watermark (logo dính suốt phim).</li>
<li><b>Bước 2:</b> chọn một đoạn, trả lời từng ảnh: <b>khi xuất video thật, bạn sẽ làm gì với chỗ đó?</b> — giống lúc duyệt trên Dashboard. Với 18+/máu me/bạo lực, trước hết cho biết <b>có thật không</b>: có thật mà bạn vẫn giữ thì chọn <b>Có thật — vẫn giữ nguyên</b>, đừng chọn "Máy sai".</li>
<li><b>Bước 3:</b> xem nhanh cả đoạn; thấy chỗ cần làm mờ/cắt mà chưa được hỏi thì bấm <b>⚑</b>. Xong bấm <b>✔ Xong đoạn này</b>.</li></ol>
<div class="small">Khi xem video, <b style="color:#16a34a">khung xanh</b> là chỗ đã được đánh dấu (ví dụ watermark đã xác nhận). Thấy logo nằm suốt phim mà <b>không có khung xanh</b>: bấm <b>🖼 Thêm logo suốt phim</b> dưới video và khoanh vùng một lần.</div>
<div class="small muted">Không chắc thì chọn <b>Không chắc</b> — không sao cả. Mọi câu trả lời được lưu ngay; tắt cửa sổ đen là dừng, lần sau mở lại làm tiếp.</div></section>
<section id="step1"></section>
<section id="step2"></section>
</main>
<script>
const NAMES={visual_logo:'logo / watermark',text:'chữ quảng cáo',adult:'cảnh 18+',gore:'máu me',violence:'bạo lực'};
const SAFETY=['adult','gore','violence'];
// [answer kind, button class, label]; the number key of a button is its position
const LOGO_CHOICES=[['keep','keep','1 · ✘ Sai — để nguyên'],['blur','blur','2 · ✔ Đúng — làm mờ'],['cut','cut','3 · ✔ Đúng — cắt bỏ'],['unsure','unsure','4 · Không chắc']];
const SAFETY_CHOICES=[['keep','keep','1 · ✘ Máy sai — cảnh bình thường'],['present','present','2 · ✔ Có thật — vẫn giữ nguyên'],['blur','blur','3 · ✔ Có thật — làm mờ'],['cut','cut','4 · ✔ Có thật — cắt bỏ'],['unsure','unsure','5 · Không chắc']];
const choices=s=>SAFETY.includes(s.category)?SAFETY_CHOICES:LOGO_CHOICES;
let token='',S=null,seg=null,history=[],flag=null,drawing=false,drag=null,region=null,pendingSave=null,stopAt=null,filmLogo=null;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=t=>{t=Math.max(0,t);const h=Math.floor(t/3600),m=Math.floor(t%3600/60),s=Math.floor(t%60);return (h?h+':':'')+String(m).padStart(h?2:1,'0')+':'+String(s).padStart(2,'0')};
function notice(text,bad){const n=$('notice');n.textContent=text;n.style.color=bad?'var(--bad)':'var(--ok)';clearTimeout(n.t);n.t=setTimeout(()=>n.textContent='',4000)}
async function load(){if(!token)token=(await (await fetch('/api/session')).json()).token;S=await (await fetch('/api/state',{cache:'no-store'})).json();render()}
async function post(url,body){body=body||{};body.revision=S.labels.revision;const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Golden-Token':token},body:JSON.stringify(body)});const j=await r.json();if(!r.ok){if(r.status===409)await load();const err=Error(j.error||r.status);err.code=j.code;throw err}S=j.state;render();return j.result}
const segment=()=>S.segments.find(s=>s.id===seg),source=k=>S.sources[k];
const sug=id=>S.suggestions.find(s=>s.id===id);
function boxHtml(r,src){if(!r)return '';return `<div class="box" style="left:${100*r.x/src.width}%;top:${100*r.y/src.height}%;width:${100*r.width/src.width}%;height:${100*r.height/src.height}%"></div>`}
function marked(segId,t){return S.labels.events.filter(e=>e.segment_id===segId&&e.region_source_pixels&&!e.ambiguous&&e.expected_action!=='KEEP'&&e.start_seconds<=t&&t<=e.end_seconds)}
function greenBox(r,src){return `<div class="box" style="border-color:#16a34a;left:${100*r.x/src.width}%;top:${100*r.y/src.height}%;width:${100*r.width/src.width}%;height:${100*r.height/src.height}%"></div>`}
function thumb(key,t,r,segId){const src=source(key),green=segId?marked(segId,t).map(e=>greenBox(e.region_source_pixels,src)).join(''):'';return `<div class="thumb"><img loading="lazy" src="/frame/${key}?t=${t.toFixed(3)}">${green}${boxHtml(r,src)}</div>`}
function pendingCards(id){const res=S.labels.suggestion_resolutions;return (S.easy.cards[id]||[]).filter(k=>!res[k])}
function render(){
 const done=S.segments.filter(s=>S.labels.segments[s.id].status==='complete').length;$('progress').textContent=`${done}/${S.segments.length} đoạn đã xong · ${S.labels.events.length} nhãn`;
 const dec=S.labels.watermark_decisions||{},open=S.easy.watermarks.filter(w=>!dec[w.id]);
 $('step1').innerHTML=`<h2>Bước 1 — Watermark ${open.length?'':'<span class="done">✔ xong</span>'}</h2>`+(open.length?open.map(w=>{const src=source(w.source),t1=Math.min(w.start_seconds+30,w.end_seconds-1),t2=(w.start_seconds+w.end_seconds)/2;return `<div class="card"><div class="question">Phim <b>${esc(src.name.slice(0,60))}</b>: khung đỏ có phải <b>logo/watermark</b> cần làm mờ suốt phim không?</div><div class="hint">Xuất hiện ${fmt(w.start_seconds)}–${fmt(w.end_seconds)}${w.prior_blur?' · trước đây bạn đã chọn làm mờ khung này':''}${w.labels.length?' · chữ đọc được: '+esc(w.labels.join(', ')):''}</div><div class="thumbs">${thumb(w.source,t1,w.region_source_pixels)}${thumb(w.source,t2,w.region_source_pixels)}</div><div class="row"><button class="big blur" onclick="wm('${w.id}',true)">✔ Đúng, làm mờ suốt phim</button><button class="big keep" onclick="wm('${w.id}',false)">✘ Không phải</button></div><div class="hint">Nếu khung đỏ nằm trên người/vật trong phim (không phải logo) thì chọn <b>Không phải</b>.</div></div>`}).join(''):'<div class="muted">Đã trả lời hết. Chuyển sang bước 2 bên dưới.</div>');
 renderStep2()}
async function wm(id,yes){try{await post(`/api/watermarks/${id}/${yes?'confirm':'decline'}`);notice(yes?'Đã gán watermark cho các đoạn của phim':'Đã ghi: không phải watermark')}catch(e){notice(e.message,true)}}
let built=null;
function renderStep2(){
 const st=S.labels.segments,segButtons=S.segments.map(s=>{const n=pendingCards(s.id).length,c=st[s.id].status==='complete';return `<button class="${s.id===seg?'active':''}" onclick="pick('${s.id}')"><b>${s.id}</b> ${esc(source(s.source).name.slice(0,14))} ${fmt(s.start_seconds)} · ${c?'<span class="done">✔ xong</span>':n?n+' câu':'xem nhanh'}</button>`}).join('');
 if(!seg){built=null;$('step2').innerHTML=`<h2>Bước 2 và 3 — Từng đoạn</h2><div class="segs">${segButtons}</div><p class="muted">Chọn một đoạn để bắt đầu (nên bắt đầu với đoạn ngắn như C20A).</p>`;return}
 const g=segment();
 if(built!==seg){  // the player is built once per segment so answers never reload the video
  $('step2').innerHTML=`<h2>Bước 2 và 3 — Từng đoạn</h2><div class="segs" id="segs"></div><div class="layout" style="margin-top:12px"><div><div class="stage" id="stage"><video id="video" controls preload="metadata"></video><div id="overlay"></div></div><div class="row small" style="margin-top:6px"><span id="time"></span><select id="rate" onchange="video.playbackRate=+this.value"><option value="1">1×</option><option value="1.5">1,5×</option><option value="2" selected>2×</option></select><span class="muted">Đoạn ${g.id}: ${fmt(g.start_seconds)}–${fmt(g.end_seconds)}</span></div><div class="row" style="margin-top:6px"><button onclick="startFilmLogo()">🖼 Thêm logo suốt phim (máy chưa tìm thấy)</button></div><div id="labels" class="small" style="margin-top:8px"></div></div><div id="work"></div></div>`;
  built=seg;setupVideo(g)}
 $('segs').innerHTML=segButtons;
 const locked=st[g.id].status==='complete',cards=pendingCards(g.id),total=(S.easy.cards[g.id]||[]).length;
 renderLabels(g,locked);
 const work=$('work');if(!pendingSave&&!(filmLogo&&!filmLogo.box)){drawing=false;stopDrawUi()}if(!pendingSave&&!drawing&&!filmLogo)drawLabelBoxes();
 if(locked){work.innerHTML=`<div class="card"><div class="question done">✔ Đoạn này đã xong</div><div class="row"><button onclick="reopen()">↺ Mở lại để sửa</button><button onclick="redo()">↺ Làm lại đoạn này từ đầu</button></div><div class="hint">Làm lại: xóa các câu trả lời của đoạn (giữ nhãn watermark/logo suốt phim) rồi hỏi lại từ câu đầu. Bản cũ vẫn lưu trong lịch sử.</div></div>`;return}
 if(filmLogo){work.innerHTML=filmLogoCard();if(!filmLogo.box)startDraw();return}
 if(pendingSave){const greens=pendingSave.from_suggestion?coverCandidates(pendingSave):[];work.innerHTML=`<div class="card" style="border-color:#f59e0b"><div class="question">👈 Kéo chuột trên VIDEO (viền cam) để khoanh vùng cần làm mờ</div><div class="hint">Khoanh sát logo/chữ. Video đã dừng ở đúng chỗ; có thể tua bằng thanh video trước khi khoanh. Logo/watermark đã có khung xanh thì không cần khoanh lại.</div><div class="row"><button class="big blur" onclick="fullFrame()">Làm mờ cả khung hình</button>${greens.length?`<button class="big keep" onclick="coverPending('${greens[0].id}')">✔ Đã có khung xanh — không cần khoanh</button>`:''}<button onclick="startDraw()">✎ Khoanh lại</button><button onclick="cancelPending()">Hủy</button></div></div>`;startDraw();return}
 if(flag&&flag.end!=null){work.innerHTML=flagForm();return}
 if(cards.length){const s=sug(cards[0]),mid=(s.start_seconds+s.end_seconds)/2,safety=SAFETY.includes(s.category);
  const approx=s.region_approximate,box=approx?null:s.region_source_pixels;
  const question=safety?`Máy báo có <b>${NAMES[s.category]}</b> ở đoạn này. Có thật không, và bạn sẽ làm gì khi xuất video?`
   :box?'Thứ nằm TRONG khung đỏ có phải logo/chữ quảng cáo không?'
   :`Trong cảnh này máy thấy logo/chữ quảng cáo — đúng không?${approx?' <span class="small muted">(máy chỉ biết vị trí đại khái)</span>':''}`;
  const hint=safety?"Chọn 'Máy sai' chỉ khi đoạn này KHÔNG có nội dung đó. Có thật mà bạn muốn giữ thì chọn 'Có thật — vẫn giữ nguyên'."
   :`Logo/watermark đã có khung xanh thì không cần khoanh lại. Chọn <b>Sai</b> khi đó là nội dung bình thường của phim (chữ credits, logo hãng phim, chữ/hình trong cảnh).${box?'':' Nếu Đúng — làm mờ, trang sẽ bảo bạn khoanh sát logo/chữ trên video.'}`;
  const prior=s.prior_decision?`<div><span class="prior">Trước đây trên Dashboard bạn đã chọn: ${{KEEP:'GIỮ NGUYÊN',BLUR:'LÀM MỜ',CUT:'CẮT BỎ',NEEDS_MORE_CONTEXT:'CẦN XEM THÊM'}[s.prior_decision]||s.prior_decision}${safety&&s.prior_decision==='KEEP'?' (giữ nguyên không có nghĩa là máy sai)':''}</span></div>`:'';
  const buttons=choices(s).map(([kind,cls,text])=>`<button class="big ${cls}" onclick="answer('${kind}')">${text}</button>`).join('');
  work.innerHTML=`<div class="card"><div class="small muted">Bước 2 · câu ${total-cards.length+1}/${total} · <span style="color:#ef4444">khung đỏ</span> = chỗ đang hỏi, <span style="color:#16a34a">khung xanh</span> = đã đánh dấu</div>${thumb(g.source,mid,box,g.id)}<div class="row"><button onclick="playRange(${s.start_seconds},${s.end_seconds})">▶ Xem đoạn này (${fmt(s.start_seconds)}–${fmt(s.end_seconds)})</button></div><div class="question">${question}</div>${prior}<div class="row">${buttons}</div><div class="hint">${hint}</div>${history.length?'<button onclick="undo()">↶ Hoàn tác câu vừa rồi</button>':''}</div>`;return}
 work.innerHTML=`<div class="card"><div class="small muted">Bước 3 · xem nhanh cả đoạn</div><div class="question">Xem nhanh đoạn này, bấm ⚑ khi thấy chỗ cần làm mờ/cắt mà chưa được hỏi</div><div class="row"><button class="primary" onclick="playSegment()">▶ Phát cả đoạn (2×)</button><button class="flag ${flag?'cut':'blur'}" id="flagbtn" onclick="toggleFlag()">${flag?'■ Hết chỗ có vấn đề (F)':'⚑ Chỗ này có vấn đề (F)'}</button></div><div class="hint">Logo/watermark đã xác nhận ở bước 1 không cần đánh dấu lại. Máu me/bạo lực <b>có thật</b> (chém, đâm thấy rõ, máu) mà máy chưa hỏi: bấm ⚑ rồi chọn <b>có thật · vẫn giữ</b> nếu bạn giữ khi xuất. Đánh nhau hoạt hình không máu thì không cần đánh dấu.</div><div class="row"><button class="primary big" onclick="finish()">✔ Xong đoạn này</button>${history.length?'<button onclick="undo()">↶ Hoàn tác</button>':''}</div></div>`}
function renderLabels(g,locked){const evs=S.labels.events.filter(e=>e.segment_id===g.id).sort((a,b)=>a.start_seconds-b.start_seconds);$('labels').innerHTML=evs.length?`<b>Nhãn của đoạn:</b><ul class="labels">${evs.map(e=>`<li>${fmt(e.start_seconds)}–${fmt(e.end_seconds)} · ${NAMES[e.category]} · ${e.ambiguous?'không chắc':e.expected_action==='CUT'?'cắt':e.expected_action==='BLUR'?'làm mờ':e.content_present?'có thật · giữ':'giữ'}${locked?'':` <button class="small" onclick="removeEvent('${e.id}')">xóa</button>`}</li>`).join('')}</ul>`:'<span class="muted">Đoạn này chưa có nhãn.</span>'}
let video=null;
function setupVideo(g){video=$('video');video.src='/video/'+g.source;video.onloadedmetadata=()=>{video.currentTime=segment().start_seconds;video.playbackRate=+$('rate').value;video.onloadedmetadata=null};video.onratechange=null
 video.ontimeupdate=()=>{const s=segment();if(!s)return;if(!drawing&&!pendingSave&&!filmLogo)drawLabelBoxes();$('time').textContent=`${fmt(video.currentTime)} (đoạn +${fmt(video.currentTime-s.start_seconds)})`;if(stopAt!=null&&video.currentTime>=stopAt){video.pause();stopAt=null}if(!video.paused&&video.currentTime>=s.end_seconds){video.pause();notice('Đã tới cuối đoạn')}};
 const overlay=$('overlay');overlay.onpointerdown=ev=>{if(!drawing)return;overlay.setPointerCapture(ev.pointerId);drag={a:pt(ev)}};overlay.onpointermove=ev=>{if(!drag)return;const b=pt(ev);drag.box={x:Math.round(Math.min(drag.a.x,b.x)),y:Math.round(Math.min(drag.a.y,b.y)),width:Math.round(Math.abs(b.x-drag.a.x)),height:Math.round(Math.abs(b.y-drag.a.y))};overlay.innerHTML=boxHtml(drag.box,source(segment().source))};overlay.onpointerup=()=>{const box=drag&&drag.box;drag=null;if(box&&box.width>=4&&box.height>=4){drawing=false;stopDrawUi();finishDraw(box)}}}
function pt(ev){const r=$('overlay').getBoundingClientRect(),src=source(segment().source);return {x:Math.max(0,Math.min(src.width,(ev.clientX-r.left)/r.width*src.width)),y:Math.max(0,Math.min(src.height,(ev.clientY-r.top)/r.height*src.height))}}
function pick(id){seg=id;flag=null;pendingSave=null;history=[];render()}
function playRange(a,b){video.currentTime=Math.max(segment().start_seconds,a-1);stopAt=b+1;video.play()}
function playSegment(){if(video.currentTime<segment().start_seconds||video.currentTime>=segment().end_seconds-0.5)video.currentTime=segment().start_seconds;stopAt=null;video.play()}
function eventFrom(s,kind){const logo=['visual_logo','text'].includes(s.category),box=s.region_approximate?null:s.region_source_pixels;const base={segment_id:seg,category:s.category,start_seconds:s.start_seconds,end_seconds:s.end_seconds,severity:'must_catch',from_suggestion:s.id,notes:'chế độ dễ'};
 if(kind==='present')return {...base,expected_action:'KEEP',content_present:true,severity:'should_catch',region_source_pixels:null};
 if(kind==='cut')return {...base,expected_action:'CUT',region_source_pixels:null};
 if(kind==='unsure')return {...base,expected_action:logo?'BLUR':'CUT',region_source_pixels:box,ambiguous:true};
 return {...base,expected_action:'BLUR',region_source_pixels:logo?box:null}}
async function answer(kind){const id=pendingCards(seg)[0];if(!id)return;const s=sug(id);try{if(kind==='keep'){await post(`/api/suggestions/${id}/resolve`,{resolution:'rejected'});history.push({type:'reject',id});notice(SAFETY.includes(s.category)?'Đã ghi: máy sai':'Đã ghi: để nguyên');return}
 const ev=eventFrom(s,kind);if(kind==='blur'&&['visual_logo','text'].includes(s.category)&&!ev.region_source_pixels){pendingSave=ev;video.pause();video.currentTime=(s.start_seconds+s.end_seconds)/2;render();return}
 const saved=await post('/api/events',{event:ev});history.push({type:'event',id:saved.id});notice(kind==='present'?'Đã lưu: có thật · vẫn giữ':'Đã lưu')}catch(e){notice(e.message,true)}}
function coverCandidates(ev){const g=greenLabels(ev),q=sug(ev.from_suggestion),r=q&&q.region_source_pixels;if(!r)return g.length===1?g:[];return g.filter(e=>{const b=e.region_source_pixels,ix=Math.max(0,Math.min(b.x+b.width,r.x+r.width)-Math.max(b.x,r.x)),iy=Math.max(0,Math.min(b.y+b.height,r.y+r.height)-Math.max(b.y,r.y));return ix*iy>=0.3*b.width*b.height})}
function greenLabels(ev){return S.labels.events.filter(e=>e.segment_id===ev.segment_id&&e.region_source_pixels&&!e.ambiguous&&['BLUR','CUT'].includes(e.expected_action)&&['visual_logo','text'].includes(e.category)&&e.start_seconds<ev.end_seconds&&ev.start_seconds<e.end_seconds)}
async function coverPending(eventId){const waiting=pendingSave,id=waiting&&waiting.from_suggestion;if(!id)return;pendingSave=null;drawing=false;stopDrawUi();try{await post(`/api/suggestions/${id}/cover`,{event_id:eventId});history.push({type:'cover',id});notice('Đã ghi: logo đã có khung xanh')}catch(e){pendingSave=waiting;notice(e.message,true);render()}}
function startDraw(){drawing=true;const o=$('overlay'),st=$('stage');if(o){o.classList.add('drawing');o.innerHTML='<div class="drawtip">Kéo chuột ở đây để khoanh vùng</div>'}if(st){st.classList.add('drawing-now');st.scrollIntoView({behavior:'smooth',block:'center'})}video&&video.pause();notice('Hãy kéo chuột trên video để khoanh vùng')}
function stopDrawUi(){const st=$('stage'),o=$('overlay');if(st)st.classList.remove('drawing-now');if(o)o.classList.remove('drawing')}
function fullFrame(){const src=source(segment().source);drawing=false;stopDrawUi();finishDraw({x:0,y:0,width:src.width,height:src.height})}
async function finishDraw(box){if(filmLogo){filmLogo.box=box;filmLogo.at=video.currentTime;render();$('overlay').innerHTML=boxHtml(box,source(segment().source));return}if(!pendingSave)return;const waiting=pendingSave,ev={...waiting,region_source_pixels:box};pendingSave=null;flag=null;drawing=false;stopDrawUi();try{const saved=await post('/api/events',{event:ev});history.push({type:'event',id:saved.id});notice('Đã lưu vùng làm mờ')}catch(e){pendingSave=waiting;notice(e.message,true);render()}}
function cancelPending(){pendingSave=null;drawing=false;flag=null;filmLogo=null;stopDrawUi();render()}
function drawLabelBoxes(){const o=$('overlay');if(!o||!video||!seg)return;const src=source(segment().source),t=video.currentTime;o.innerHTML=S.labels.events.filter(e=>e.segment_id===seg&&e.region_source_pixels&&!e.ambiguous&&e.expected_action!=='KEEP'&&e.start_seconds<=t&&t<=e.end_seconds).map(e=>{const r=e.region_source_pixels;return `<div class="box" style="border-color:#16a34a;left:${100*r.x/src.width}%;top:${100*r.y/src.height}%;width:${100*r.width/src.width}%;height:${100*r.height/src.height}%"><span style="position:absolute;top:-20px;left:-3px;background:#16a34a;color:#fff;font-size:11px;padding:0 4px;white-space:nowrap">đã đánh dấu</span></div>`}).join('')}
function startFilmLogo(){if(!video)return;video.pause();pendingSave=null;flag=null;filmLogo={box:null,category:'visual_logo'};render()}
function filmLogoCard(){const g=segment(),dur=source(g.source).duration_seconds;if(!filmLogo.box)return `<div class="card"><div class="question">Kéo chuột trên video để khoanh logo nằm suốt phim</div><div class="hint">Tua tới chỗ thấy rõ logo rồi khoanh sát logo. Chỉ cần khoanh một lần; trang sẽ gán cho mọi đoạn của phim trong khoảng bạn chọn ở bước sau.</div><div class="row"><button onclick="cancelPending()">Hủy</button></div></div>`;
 const at=filmLogo.at,c=filmLogo.category;return `<div class="card"><div class="question">Vùng này là gì, và xuất hiện trong khoảng nào?</div><div class="row"><button class="${c==='visual_logo'?'primary':''}" onclick="filmLogo.category='visual_logo';render()">Logo / watermark</button><button class="${c==='text'?'primary':''}" onclick="filmLogo.category='text';render()">Chữ quảng cáo</button></div><div class="hint">Khung đã khoanh hiện trên video. Nếu chưa đúng, bấm Khoanh lại. Khung xanh có sẵn nghĩa là chỗ đó đã được đánh dấu.</div><div class="row"><button class="big blur" onclick="saveFilmLogo(0,${dur})">Suốt cả phim</button><button class="big blur" onclick="saveFilmLogo(${at},${dur})">Từ ${fmt(at)} tới hết phim</button><button class="big blur" onclick="saveFilmLogo(0,${at})">Từ đầu phim tới ${fmt(at)}</button><button class="big blur" onclick="saveFilmLogo(${g.start_seconds},${g.end_seconds},true)">Chỉ trong đoạn này</button></div><div class="row"><button onclick="filmLogo.box=null;render()">✎ Khoanh lại</button><button onclick="cancelPending()">Hủy</button></div></div>`}
async function saveFilmLogo(start,end,onlyHere,replace){const box=filmLogo.box,g=segment(),category=filmLogo.category;try{if(onlyHere){const saved=await post('/api/events',{event:{segment_id:seg,category,start_seconds:start,end_seconds:end,severity:'must_catch',expected_action:'BLUR',region_source_pixels:box,notes:'chế độ dễ · trong đoạn'}});history.push({type:'event',id:saved.id})}else{const r=await post('/api/film-logos',{source:g.source,region_source_pixels:box,start_seconds:start,end_seconds:end,category,replace:!!replace});notice(`Đã lưu ${r.events.length} nhãn cho các đoạn của phim`)}filmLogo=null;drawing=false;render()}catch(e){if(e.code==='enlarge'&&confirm(e.message+'.\n\nThay khung cũ bằng khung vừa khoanh cho cả khoảng này?'))return saveFilmLogo(start,end,onlyHere,true);notice(e.message,true)}}
function toggleFlag(){const s=segment();if(!flag){flag={start:Math.max(s.start_seconds,video.currentTime-1),end:null};notice('Đã đánh dấu điểm đầu — bấm lại khi hết');render()}else{flag.end=Math.min(s.end_seconds,Math.max(flag.start+1,video.currentTime+1));video.pause();render()}}
function flagForm(){return `<div class="card"><div class="question">Chỗ ${fmt(flag.start)}–${fmt(flag.end)} là gì, và bạn sẽ làm gì?</div><div class="hint">Chọn một nút. Logo/chữ cần làm mờ sẽ hỏi bạn khoanh vùng trên video.</div><div class="row"><button class="big blur" onclick="flagged('visual_logo','blur')">Logo / watermark · làm mờ</button><button class="big blur" onclick="flagged('text','blur')">Chữ quảng cáo · làm mờ</button><button class="big cut" onclick="flagged('visual_logo','cut')">Quảng cáo cả cảnh · cắt</button><button class="big blur" onclick="flagged('adult','blur')">Cảnh 18+ · làm mờ</button><button class="big cut" onclick="flagged('adult','cut')">Cảnh 18+ · cắt</button><button class="big present" onclick="flagged('gore','present')">Máu me · Có thật — vẫn giữ nguyên</button><button class="big cut" onclick="flagged('gore','cut')">Máu me · cắt</button><button class="big present" onclick="flagged('violence','present')">Bạo lực · Có thật — vẫn giữ nguyên</button><button class="big cut" onclick="flagged('violence','cut')">Bạo lực · cắt</button><button class="big unsure" onclick="flagged('unsure','cut')">Không chắc là gì</button></div><div class="hint">Máu me/bạo lực có thật mà bạn giữ khi xuất: chọn <b>Có thật — vẫn giữ nguyên</b> (máy vẫn phải báo để bạn duyệt).</div><button onclick="flag=null;render()">Hủy</button></div>`}
async function flagged(category,kind){const unsure=category==='unsure',present=kind==='present';const ev={segment_id:seg,category:unsure?'visual_logo':category,start_seconds:+flag.start.toFixed(3),end_seconds:+flag.end.toFixed(3),severity:present?'should_catch':'must_catch',expected_action:present?'KEEP':kind==='cut'?'CUT':'BLUR',region_source_pixels:null,ambiguous:unsure,notes:'chế độ dễ · bị sót'};if(present)ev.content_present=true;
 if(!unsure&&kind==='blur'&&['visual_logo','text'].includes(category)){pendingSave=ev;video.currentTime=(flag.start+flag.end)/2;flag=null;render();return}
 try{const saved=await post('/api/events',{event:ev});history.push({type:'event',id:saved.id});flag=null;notice('Đã lưu chỗ bị sót');render()}catch(e){notice(e.message,true)}}
async function undo(){const last=history.pop();if(!last)return;try{if(last.type==='event')await post(`/api/events/${last.id}/delete`);else await post(`/api/suggestions/${last.id}/resolve`,{resolution:null});notice('Đã hoàn tác')}catch(e){notice(e.message,true)}}
async function removeEvent(id){if(!confirm('Xóa nhãn này?'))return;try{await post(`/api/events/${id}/delete`);notice('Đã xóa')}catch(e){notice(e.message,true)}}
async function finish(){const left=pendingCards(seg).length;if(left&&!confirm(`Còn ${left} câu chưa trả lời — coi như "Để nguyên"?`))return;if(!confirm('Bạn đã xem hết đoạn này và đánh dấu mọi chỗ cần làm mờ/cắt?'))return;try{await post(`/api/segments/${seg}/reject-remaining`,{advisory_only:false});await post(`/api/segments/${seg}/status`,{status:'complete'});history=[];notice('Đoạn đã xong');const next=S.segments.find(s=>S.labels.segments[s.id].status!=='complete');if(next)pick(next.id)}catch(e){notice(e.message,true)}}
async function redo(){if(!confirm('Làm lại đoạn '+seg+' từ đầu? Các câu trả lời của đoạn sẽ bị xóa (nhãn watermark/logo suốt phim vẫn giữ).'))return;try{const r=await post(`/api/segments/${seg}/reset`);history=[];notice(`Đã xóa ${r.removed} nhãn; trả lời lại từ câu đầu`)}catch(e){notice(e.message,true)}}
async function reopen(){try{await post(`/api/segments/${seg}/status`,{status:'in_progress'});notice('Đã mở lại đoạn')}catch(e){notice(e.message,true)}}
document.addEventListener('keydown',ev=>{if(['INPUT','TEXTAREA','SELECT'].includes(ev.target.tagName)||!seg||!video)return;const k=ev.key;
 if(k===' '){ev.preventDefault();video.paused?video.play():video.pause()}else if(k==='ArrowLeft'){ev.preventDefault();video.currentTime-=ev.shiftKey?5:1}else if(k==='ArrowRight'){ev.preventDefault();video.currentTime+=ev.shiftKey?5:1}
 else if(pendingCards(seg).length&&!pendingSave&&!filmLogo&&/^[1-9]$/.test(k)){const opts=choices(sug(pendingCards(seg)[0]));if(+k<=opts.length)answer(opts[+k-1][0])}else if((k==='f'||k==='F')&&!pendingCards(seg).length)toggleFlag()});
load().catch(e=>notice(e.message,true));
</script></body></html>"""
