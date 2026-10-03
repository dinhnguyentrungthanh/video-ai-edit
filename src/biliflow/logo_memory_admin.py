"""Bộ nhớ logo (batch 4a, 2026-10-03): view, re-class and delete remembered logos.

A page and a small JSON API over ``state/studio-logo-memory.json`` for the
Control Center: studio-logo KEEP records ("giữ & nhớ") and platform-logo BLUR
records ("làm mờ & nhớ", conversions, seeds). Reads never write. Reads and
writes hold ``export_guards.REVIEW_QUEUE_IO`` (so they serialise with review
decisions, which remember and forget records; on Windows an open reader makes
a writer's replace or rename fail); a listing takes it once per record. Every
write refuses with 409 when the memory's sha256 is not the one the page showed,
backs the memory up to ``state/backups/`` first and never deletes a frame:
deleting a record writes the memory, then moves its frames folder in one rename
to ``state/backups/studio-logo-frames-<ts>/`` (a folder in use stays where it
was, and the answer says so). Review queues are not touched; a card that
remembered a deleted record keeps its "đã nhớ" note until its decision changes.

The Control Center routes requests here through :func:`handle_get` and
:func:`handle_post` (snippets in temp/ui-plan/batch4/hooks-4a.md); this module
starts no server.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from biliflow.brand_memory import (
    STUDIO_LOGO_FRAMES_PATH,
    _parse_studio_logo_memory,
    _write_studio_logo_memory,
)
from biliflow.export_guards import REVIEW_QUEUE_IO
from biliflow.platform_logos import PLATFORM_MEMORY_CLASS
from biliflow.platform_memory import (
    MEMORY_CHANGED_MESSAGE,
    STUDIO_MEMORY_CLASS,
    backup_memory,
    convert_logo_memory_class,
    convert_plan,
    memory_snapshot,
    memory_unchanged,
    move_frames_to_backups,
)
from biliflow.platform_names import PLATFORMS

PAGE_PATH = "/logo-memory"
API_LIST = "/api/logo-memory"
API_FRAME = "/api/logo-memory/frame"
API_CLASS = "/api/logo-memory/class"
API_DELETE = "/api/logo-memory/delete"
FRAME_URLS_SHOWN = 6
BACKUPS_SHOWN = 10
PAGE_ACTOR = "logo-memory-page"
JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"
MEMORY_CLASSES = (STUDIO_MEMORY_CLASS, PLATFORM_MEMORY_CLASS)
CONTROL_CENTER_DATABASE = Path("state") / "control-center.sqlite3"
REFUSALS = {
    "no_logo_frames": "Không thấy khung nào có logo trên nền tối — đây không phải logo nền tảng "
                      "(ví dụ thẻ giấy phép, cảnh phim).",
    "box_too_large": "Vùng logo phủ hơn 1/4 khung hình — đây là cả một hình, không phải logo nền tảng.",
    "no_stored_frames": "Bản ghi cũ chưa lưu ảnh khung hình; hãy nhớ lại logo này từ trang duyệt.",
    "no_frames": "Bản ghi chưa lưu ảnh khung hình nào.",
    "not_a_studio_logo_record": "Chỉ đổi được bản ghi logo hãng phim thành logo nền tảng.",
    "not_a_platform_logo_record": "Chỉ đổi được bản ghi logo nền tảng thành logo hãng phim.",
}
FRAMES_LEFT_WARNING = (
    "Chưa chuyển được thư mục ảnh khung hình {folder} vào state/backups ({error}) — bản ghi đã xóa khỏi "
    "bộ nhớ; ảnh vẫn ở chỗ cũ, có thể chuyển tay sau."
)


class StaleMemory(Exception):
    """The memory changed since the page read it (HTTP 409)."""


class FrameOutsideMemory(Exception):
    """A stored frame path points outside state/studio-logo-frames or is not a JPEG (HTTP 403)."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative(root: Path, path: Path | None) -> str | None:
    return path.resolve().relative_to(root).as_posix() if path is not None else None


# --------------------------------------------------------------------- reads


def _episodes(root: Path, shas: set[str]) -> dict[str, dict[str, Any]]:
    """Job id and file name of each source sha (Control Center database, read-only)."""
    database = root / CONTROL_CENTER_DATABASE
    if not shas or not database.is_file():
        return {}
    try:
        connection = sqlite3.connect(
            f"file:{urllib.parse.quote(database.as_posix())}?mode=ro", uri=True, timeout=5,
        )
        try:
            rows = connection.execute("select id, source_path, source_sha256 from jobs").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    return {
        str(sha): {"job_id": int(job_id), "name": str(path).replace("\\", "/").rsplit("/", 1)[-1]}
        for job_id, path, sha in rows if str(sha) in shas
    }


def _backups(root: Path) -> list[dict[str, Any]]:
    """The newest memory backups and moved frame folders under state/backups."""
    folder = root / "state" / "backups"
    if not folder.is_dir():
        return []
    entries = [
        path for path in folder.iterdir()
        if (path.is_file() and path.name.startswith("studio-logo-memory-") and path.suffix == ".json")
        or (path.is_dir() and path.name.startswith("studio-logo-frames-"))
    ]
    entries.sort(key=lambda path: path.name, reverse=True)
    return [{
        "path": _relative(root, path), "kind": "frames" if path.is_dir() else "memory",
        "bytes": path.stat().st_size if path.is_file() else None,
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
    } for path in entries[:BACKUPS_SHOWN]]


def _shown_frames(stored: list[dict[str, Any]], logo_times: list[Any]) -> list[int]:
    """Up to six evenly spaced stored frames, the logo frames first when there are any."""
    wanted = {round(float(value), 3) for value in logo_times if isinstance(value, (int, float))}
    indexes = [
        index for index, entry in enumerate(stored)
        if isinstance(entry.get("t"), (int, float)) and round(float(entry["t"]), 3) in wanted
    ] or list(range(len(stored)))
    if len(indexes) <= FRAME_URLS_SHOWN:
        return indexes
    last = len(indexes) - 1
    return [indexes[round(step * last / (FRAME_URLS_SHOWN - 1))] for step in range(FRAME_URLS_SHOWN)]


def _record_window(record: dict[str, Any]) -> list[float] | None:
    window = record.get("window")
    if isinstance(window, list) and len(window) == 2:
        return window
    start, end = record.get("start_seconds"), record.get("end_seconds")
    return [start, end] if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None


def _summary(root: Path, record: dict[str, Any], episodes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    key = str(record.get("key") or "")
    memory_class = record.get("memory_class") or STUDIO_MEMORY_CLASS
    platform = memory_class == PLATFORM_MEMORY_CLASS
    stored = [entry for entry in record.get("stored_frames") or [] if isinstance(entry, dict)]
    plan = convert_plan(root, record, STUDIO_MEMORY_CLASS if platform else PLATFORM_MEMORY_CLASS)
    derived = plan.get("derived") or {}
    logo_times = list(record.get("logo_frame_times") or []) if platform else list(
        derived.get("logo_frame_times") or [])
    quoted = urllib.parse.quote(key, safe="")
    refusal = plan["reason"] if plan["status"] == "refused" else None
    return {
        "key": key,
        "memory_class": memory_class,
        "decision": record.get("decision"),
        "platform": record.get("platform") if platform else None,
        "labels": [str(label) for label in (record.get("labels") or [])[:3]],
        "episode": episodes.get(str(record.get("source_sha256") or "")),
        "seeded_from": record.get("seeded_from"),
        "converted_from": record.get("converted_from"),
        "window": _record_window(record),
        "created_at": record.get("created_at"),
        "frames": len(stored),
        "frames_source": record.get("frames_source"),
        "logo_frames": len(logo_times),
        "blur_region": record.get("blur_region") if platform else None,
        "proposed_blur_region": None if platform else derived.get("blur_region"),
        "ignored_regions": len(record.get("ignored_regions") or []),
        "frame_urls": [f"{API_FRAME}?key={quoted}&i={index}" for index in _shown_frames(stored, logo_times)],
        "convertible": plan["status"] == "convertible",
        "refusal": refusal,
        "refusal_text": REFUSALS.get(refusal) if refusal else None,
    }


def list_logo_memory(root: Path) -> dict[str, Any]:
    """Every remembered logo with its frames, episode and possible conversion (read-only)."""
    root = root.resolve(strict=True)
    with REVIEW_QUEUE_IO:
        _, snapshot = memory_snapshot(root)
    if snapshot is None:
        return {"memory_sha256": None, "records": [], "backups": _backups(root)}
    records = [record for record in _parse_studio_logo_memory(snapshot)["records"] if isinstance(record, dict)]
    episodes = _episodes(root, {str(record.get("source_sha256") or "") for record in records})
    summaries = []
    for record in records:
        with REVIEW_QUEUE_IO:  # one record's frame reads at a time; a decision waits at most that long
            summaries.append(_summary(root, record, episodes))
    return {"memory_sha256": _sha256(snapshot), "records": summaries, "backups": _backups(root)}


def _find(snapshot: bytes | None, key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if snapshot is None:
        raise LookupError("Chưa có bộ nhớ logo nào")
    payload = _parse_studio_logo_memory(snapshot)
    record = next((
        value for value in payload["records"] if isinstance(value, dict) and value.get("key") == key
    ), None)
    if record is None:
        raise LookupError("Không có logo này trong bộ nhớ (có thể vừa bị xóa hoặc đổi)")
    return payload, record


def logo_memory_frame(root: Path, key: str, index: int) -> bytes:
    """JPEG bytes of one stored frame of a record; only files inside state/studio-logo-frames."""
    root = root.resolve(strict=True)
    with REVIEW_QUEUE_IO:
        _, record = _find(memory_snapshot(root)[1], key)
        stored = [entry for entry in record.get("stored_frames") or [] if isinstance(entry, dict)]
        if not 0 <= index < len(stored):
            raise LookupError("Không có khung hình này")
        base = (root / STUDIO_LOGO_FRAMES_PATH).resolve()
        path = (root / str(stored[index].get("image") or "")).resolve()
        if base not in path.parents or path.suffix.lower() not in {".jpg", ".jpeg"}:
            raise FrameOutsideMemory("Ảnh nằm ngoài thư mục khung hình của bộ nhớ logo")
        if not path.is_file():
            raise LookupError("Ảnh khung hình không còn trên đĩa")
        return path.read_bytes()


# -------------------------------------------------------------------- writes


def set_logo_memory_class(
    root: Path, key: str, memory_class: str, *, platform: str | None = None, expected_sha256: str,
    actor: str = PAGE_ACTOR,
) -> dict[str, Any]:
    """Turn one record into the other class (backup first); raises StaleMemory/LookupError/ValueError."""
    if memory_class not in MEMORY_CLASSES:
        raise ValueError("Loại bộ nhớ phải là studio_logo hoặc platform_logo")
    root = root.resolve(strict=True)
    with REVIEW_QUEUE_IO:
        report = convert_logo_memory_class(
            root, [key], to=memory_class, platform=platform if memory_class == PLATFORM_MEMORY_CLASS else None,
            apply=True, actor=actor, expected_sha256=expected_sha256,
        )
        if report.get("error") == "memory_missing":
            raise LookupError("Chưa có bộ nhớ logo nào")
        if report.get("aborted"):
            raise StaleMemory(MEMORY_CHANGED_MESSAGE)
        [entry] = report["records"]
        if entry["status"] == "missing":
            raise LookupError("Không có logo này trong bộ nhớ (có thể vừa bị xóa hoặc đổi)")
        if entry["status"] == "refused":
            raise ValueError(REFUSALS.get(entry["reason"], str(entry["reason"])))
        snapshot = memory_snapshot(root)[1]
        _, record = _find(snapshot, key)
        summary = _summary(root, record, _episodes(root, {str(record.get("source_sha256") or "")}))
    return {"key": key, "status": entry["status"], "backup": report["backup"],
            "memory_sha256": _sha256(snapshot or b""), "record": summary}


def delete_logo_memory(
    root: Path, key: str, *, expected_sha256: str, actor: str = PAGE_ACTOR,
) -> dict[str, Any]:
    """Remove one record: memory backup, memory write, then its frames moved to state/backups.

    Raises StaleMemory/LookupError before writing anything. The frames move
    last, so a failed write never separates a record from its frames; a frames
    folder that cannot move stays where it was and ``warning`` says so.
    """
    root = root.resolve(strict=True)
    with REVIEW_QUEUE_IO:
        path, snapshot = memory_snapshot(root)
        if snapshot is not None and _sha256(snapshot) != expected_sha256:
            raise StaleMemory(MEMORY_CHANGED_MESSAGE)
        payload, record = _find(snapshot, key)
        backup = backup_memory(root, snapshot)
        payload["records"] = [
            value for value in payload["records"] if not (isinstance(value, dict) and value.get("key") == key)
        ]
        if not memory_unchanged(path, snapshot):
            raise StaleMemory(MEMORY_CHANGED_MESSAGE)
        _write_studio_logo_memory(root, payload)
        written = path.read_bytes()
        moved, warning = None, None
        try:
            moved = move_frames_to_backups(root, record.get("frames_folder"))
        except OSError as error:
            warning = FRAMES_LEFT_WARNING.format(folder=record.get("frames_folder"),
                                                 error=error.strerror or type(error).__name__)
    return {"deleted": key, "memory_class": record.get("memory_class") or STUDIO_MEMORY_CLASS,
            "backup": _relative(root, backup), "frames_backup": _relative(root, moved),
            "frames_left": record.get("frames_folder") if warning else None, "warning": warning,
            "memory_sha256": _sha256(written), "deleted_by": actor}


# ------------------------------------------------------------------ dispatch


def _json(status: int, payload: Any) -> tuple[int, bytes, str]:
    return status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), JSON_TYPE


def handle_get(root: Path, path: str, query: str = "") -> tuple[int, bytes, str] | None:
    """``(status, body, content type)`` for the page, the listing and a frame; ``None`` for other paths."""
    if path == PAGE_PATH:
        return 200, logo_memory_page().encode("utf-8"), HTML_TYPE
    if path not in (API_LIST, API_FRAME):
        return None
    try:
        if path == API_LIST:
            return _json(200, list_logo_memory(root))
        params = urllib.parse.parse_qs(query or "", keep_blank_values=True)
        key = (params.get("key") or [""])[0]
        try:
            index = int((params.get("i") or [""])[0])
        except ValueError:
            return _json(400, {"error": "Cần key và số thứ tự khung i"})
        if not key:
            return _json(400, {"error": "Cần key và số thứ tự khung i"})
        return 200, logo_memory_frame(root, key, index), "image/jpeg"
    except FrameOutsideMemory as error:
        return _json(403, {"error": str(error)})
    except LookupError as error:
        return _json(404, {"error": str(error)})
    except (OSError, ValueError) as error:
        return _json(500, {"error": f"Không đọc được bộ nhớ logo: {error}"})
    except Exception as error:  # noqa: BLE001 - a malformed record must not break the page
        return _json(500, {"error": f"Lỗi khi đọc bộ nhớ logo: {type(error).__name__}: {error}"})


def handle_post(root: Path, path: str, body: Any) -> tuple[int, dict[str, Any]] | None:
    """``(status, payload)`` for a class change or a delete; ``None`` for other paths.

    The Control Center checks the host and the session token before calling this.
    """
    if path not in (API_CLASS, API_DELETE):
        return None
    if not isinstance(body, dict):
        return 400, {"error": "Cần một đối tượng JSON"}
    key, expected = body.get("key"), body.get("expected_sha256")
    if not isinstance(key, str) or not key or not isinstance(expected, str) or not expected:
        return 400, {"error": "Cần key và expected_sha256 (tải lại trang rồi làm lại)"}
    try:
        if path == API_CLASS:
            platform = body.get("platform")
            return 200, set_logo_memory_class(
                root, key, str(body.get("memory_class") or ""),
                platform=str(platform) if platform is not None else None, expected_sha256=expected,
            )
        return 200, delete_logo_memory(root, key, expected_sha256=expected)
    except StaleMemory as error:
        return 409, {"error": str(error), "code": "memory_changed"}
    except LookupError as error:
        return 404, {"error": str(error)}
    except ValueError as error:
        return 400, {"error": str(error)}
    except OSError as error:
        return 500, {"error": f"Không ghi được bộ nhớ logo: {error}"}
    except Exception as error:  # noqa: BLE001 - always answered as JSON (the page shows the message)
        return 500, {"error": f"Lỗi khi ghi bộ nhớ logo: {type(error).__name__}: {error}"}


# ---------------------------------------------------------------------- page


def logo_memory_page() -> str:
    """The "Bộ nhớ logo" page (plain template; the platform list comes from platform_names)."""
    names = json.dumps({key: entry["name"] for key, entry in PLATFORMS.items()}, ensure_ascii=False,
                       separators=(",", ":"))
    return _PAGE.replace("__PLATFORM_NAMES__", names.replace("</", "<\\/"))


_PAGE = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bộ nhớ logo · BiliFlow</title><link rel="icon" href="data:,">
<style>
:root{color-scheme:dark;--bg:#0d1117;--card:#161b22;--line:#30363d;--text:#e6edf3;--muted:#8b949e;--keep:#2fbf71;--blur:#f2a93b;--accent:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:2;display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:12px 20px;background:#0d1117f0;border-bottom:1px solid var(--line)}
header h1{font-size:18px;margin:0}header a{color:var(--accent);margin-left:auto}.pill{padding:2px 10px;border:1px solid var(--line);border-radius:99px;color:var(--muted)}
main{max-width:1180px;margin:0 auto;padding:16px 20px 40px;display:grid;gap:14px}.intro{color:var(--muted);margin:0}
.notice{padding:10px 12px;border-radius:10px;background:#16301f;border:1px solid #2f6f3f}.notice.error{background:#2d1517;border-color:#8c3947}
#records{display:grid;gap:12px}.rec{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;display:grid;gap:8px}
.rec h2{font-size:16px;margin:0;display:flex;gap:8px;flex-wrap:wrap;align-items:center}.meta{color:var(--muted);font-size:13px;overflow-wrap:anywhere}
.tag{font-size:12px;padding:1px 8px;border-radius:99px}.tag.studio_logo{background:rgba(47,191,113,.15);color:#7ee2a8}.tag.platform_logo{background:rgba(242,169,59,.15);color:#ffd38a}
.frames{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:6px}.frames img{width:100%;border-radius:6px;background:#000;display:block}
.actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}button,select{font:inherit;border-radius:8px;border:1px solid var(--line);background:#21262d;color:var(--text);padding:7px 12px}
button:hover{border-color:var(--muted)}button.danger{border-color:#8c3947;color:#ffb3bd;margin-left:auto}button:disabled,select:disabled{opacity:.5;cursor:not-allowed}
.refusal{color:#ffd38a;font-size:13px}#backups h3{font-size:14px;margin:8px 0 4px}#backups ul{margin:0;padding-left:18px;color:var(--muted);font-size:13px}
@media (max-width:720px){.frames{grid-template-columns:repeat(3,minmax(0,1fr))}main{padding:12px}header{padding:10px 12px}button.danger{margin-left:0}}
</style></head><body>
<header><h1>Bộ nhớ logo</h1><span class="pill" id="count">Đang tải…</span><a href="/">← Control Center</a></header>
<main><p class="intro">Các logo bạn đã bấm “giữ &amp; nhớ” (logo hãng phim) hoặc “làm mờ &amp; nhớ” (logo nền tảng). Logo hãng phim: lần quét sau, thẻ có hình trùng khớp chuyển xuống Ứng viên phụ với đề xuất Giữ nguyên. Logo nền tảng: đầu hoặc cuối tập có hình trùng khớp thành thẻ Làm mờ vùng logo, vẫn chờ bạn duyệt. Đổi loại hay xóa ở đây chỉ sửa bộ nhớ (sao lưu trước vào state/backups), không sửa quyết định đã duyệt; áp dụng từ lần dựng hàng đợi duyệt sau.</p>
<div class="notice" id="notice" hidden></div><section id="records"></section><section id="backups"></section></main>
<script>
const PLATFORM_NAMES=__PLATFORM_NAMES__;
let token=null,memorySha=null,busy=false;
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function className(memoryClass){return memoryClass==='platform_logo'?'logo nền tảng (làm mờ)':'logo hãng phim (giữ)';}
function clock(s){const v=Math.max(0,Math.floor(Number(s)||0));return `${Math.floor(v/60)}:${String(v%60).padStart(2,'0')}`;}
function baseName(path){return String(path||'').split(/[\\/]/).pop();}
function boxText(region){const b=region&&Array.isArray(region.box)?region.box:null;if(!b)return '';const p=v=>Math.round(Number(v)*100);return ` · vùng làm mờ x ${p(b[0])}%, y ${p(b[1])}%, rộng ${p(b[2])}%, cao ${p(b[3])}% khung hình`;}
function platformOptions(key){return Object.entries(PLATFORM_NAMES).map(([value,name])=>`<option value="${esc(value)}"${value==='iqiyi'?' selected':''}>${esc(name)}</option>`).join('');}
function recordHtml(r){const platform=r.memory_class==='platform_logo',name=platform&&r.platform&&r.platform.key!=='unknown'&&r.platform.name?` ${r.platform.name}`:'',title=platform?`Logo nền tảng${name}`:'Logo hãng phim',where=r.episode?`${r.episode.name} (job ${r.episode.job_id})`:r.seeded_from?`nhớ từ ${baseName(r.seeded_from.path)} ${clock(r.seeded_from.start)}–${clock(r.seeded_from.end)}`:'không còn job nào của video này trong Control Center',span=Array.isArray(r.window)?` · đoạn ${clock(r.window[0])}–${clock(r.window[1])}`:'',frames=` · ${Number(r.frames)||0} khung đã lưu · ${Number(r.logo_frames)||0} khung có logo trên nền tối${r.ignored_regions?` · bỏ qua ${r.ignored_regions} vùng watermark`:''}`,converted=r.converted_from?` · đổi từ ${className(r.converted_from.memory_class)}${r.converted_from.by?` bởi ${r.converted_from.by}`:''}`:'',thumbs=(r.frame_urls||[]).map(u=>`<img src="${esc(u)}" alt="" loading="lazy">`).join(''),key=esc(r.key);let action;if(platform)action=`<button type="button" data-act="studio" data-key="${key}">Đổi thành logo hãng phim (giữ)</button>`;else if(r.convertible)action=`<select data-platform-for="${key}" aria-label="Nền tảng">${platformOptions(r.key)}</select><button type="button" data-act="platform" data-key="${key}">Đổi thành logo nền tảng (làm mờ)</button>`;else action=`<span class="refusal">Không đổi được thành logo nền tảng: ${esc(r.refusal_text||r.refusal||'')}</span>`;return `<article class="rec"><h2><span class="tag ${platform?'platform_logo':'studio_logo'}">${platform?'LÀM MỜ':'GIỮ'}</span>${esc(title)}</h2><div class="meta">${esc(where)}${esc(span)}${esc(frames)}${esc(boxText(platform?r.blur_region:r.proposed_blur_region))}${esc(converted)}</div><div class="meta">Khóa: ${key}</div>${thumbs?`<div class="frames">${thumbs}</div>`:''}<div class="actions">${action}<button type="button" class="danger" data-act="delete" data-key="${key}">Xóa khỏi bộ nhớ</button></div></article>`;}
function backupsHtml(list){if(!list||!list.length)return '<h3>Bản sao lưu</h3><p class="meta">Chưa có bản sao lưu nào.</p>';return `<h3>Bản sao lưu gần đây (state/backups)</h3><ul>${list.map(b=>`<li>${esc(b.path)}${b.kind==='frames'?' · ảnh khung hình đã chuyển ra khi xóa':''} · ${esc(b.modified_at||'')}</li>`).join('')}</ul>`;}
function notice(text,error){const el=$('#notice');el.textContent=text||'';el.className=`notice${error?' error':''}`;el.hidden=!text;}
async function readJson(response){let data=null;try{data=await response.json();}catch(_error){}if(!response.ok){const error=new Error(data?.error||`Máy chủ trả về lỗi ${response.status}`);error.status=response.status;throw error;}return data;}
async function session(){token=(await readJson(await fetch('/api/session',{cache:'no-store'}))).token;}
async function load(){try{const data=await readJson(await fetch('/api/logo-memory',{cache:'no-store'}));memorySha=data.memory_sha256;const records=data.records||[];$('#count').textContent=`${records.length} logo · ${records.filter(r=>r.memory_class==='platform_logo').length} logo nền tảng`;$('#records').innerHTML=records.length?records.map(recordHtml).join(''):'<p class="meta">Chưa có logo nào được nhớ.</p>';$('#backups').innerHTML=backupsHtml(data.backups);}catch(error){notice(`Không tải được bộ nhớ logo: ${error.message}`,true);}}
async function post(path,body,retried){if(!token)await session();const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-BiliFlow-Token':token},body:JSON.stringify(body)});if(response.status===403&&!retried){await session();return post(path,body,true);}return readJson(response);}
async function act(button){const key=button.dataset.key,act=button.dataset.act,record=key&&button.closest('.rec')?.querySelector('h2')?.textContent;if(busy||!key)return;let path,body,question;if(act==='delete'){path='/api/logo-memory/delete';body={key,expected_sha256:memorySha};question=`Xóa “${record}” khỏi bộ nhớ logo? Bộ nhớ và ảnh khung hình được chuyển vào state/backups (không xóa hẳn). Thẻ đã duyệt không đổi.`;}else{const platform=act==='platform'?document.querySelector(`select[data-platform-for="${CSS.escape(key)}"]`)?.value:null;path='/api/logo-memory/class';body={key,memory_class:act==='platform'?'platform_logo':'studio_logo',platform,expected_sha256:memorySha};question=act==='platform'?`Đổi “${record}” thành logo nền tảng ${PLATFORM_NAMES[platform]||''}? Lần quét sau, đầu/cuối tập có hình trùng khớp sẽ thành thẻ Làm mờ vùng logo (chờ bạn duyệt) thay vì xuống Ứng viên phụ với Giữ nguyên. Bộ nhớ được sao lưu trước khi ghi.`:`Đổi “${record}” thành logo hãng phim (giữ)? Lần quét sau, thẻ trùng khớp chuyển xuống Ứng viên phụ với đề xuất Giữ nguyên. Bộ nhớ được sao lưu trước khi ghi.`;}if(!confirm(question))return;busy=true;document.querySelectorAll('button,select').forEach(el=>el.disabled=true);try{const result=await post(path,body);notice(act==='delete'?`Đã xóa khỏi bộ nhớ. Sao lưu: ${result.backup}${result.frames_backup?` · ảnh khung hình: ${result.frames_backup}`:''}${result.warning?` · ${result.warning}`:''}`:result.status==='already'?'Bản ghi đã ở loại này; không đổi gì.':`Đã đổi thành ${className(body.memory_class)}. Sao lưu: ${result.backup}`,act==='delete'&&!!result.warning);}catch(error){notice(error.status===409?'Bộ nhớ logo vừa thay đổi (có thể bạn vừa duyệt một thẻ) — đã tải lại danh sách, hãy làm lại.':`Không lưu được: ${error.message}`,true);}finally{busy=false;await load();}}
document.addEventListener('click',e=>{const button=e.target.closest&&e.target.closest('button[data-act]');if(button)act(button);});
load();
</script></body></html>"""
