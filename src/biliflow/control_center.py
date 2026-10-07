from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psutil

from biliflow import (
    __version__, job_delete, job_purge, logo_memory_admin, recycle_bin, source_archive, source_archive_restore,
    source_cleanup,
)
from biliflow import download_api
from biliflow.codex_supervisor import (
    codex_connection_status,
    collect_visual_evidence,
    load_ai_config,
    run_ai_audit,
    run_local_queue_audit,
    save_ai_config,
    start_codex_login,
    stored_coverage_block_is_outdated,
)
from biliflow.export_dialog import (
    EXPORT_CUSTOM_GB_ATTRIBUTES,
    EXPORT_DIALOG_JS,
)
# Shared with the standalone review UI and source cleanup (batch 3, step B5):
# _REVIEW_QUEUE_IO is the same lock object as export_guards.REVIEW_QUEUE_IO and
# the old names stay importable from this module.
from biliflow.export_guards import (
    DECISION_LABELS,
    EXPORT_IN_FLIGHT_MESSAGE,
    EXPORT_PATH_TAKEN_MESSAGE,
    QUEUE_NOT_READY_MESSAGE,
    REVIEW_EDIT_IN_FLIGHT_MESSAGE,
    REVIEW_QUEUE_IO as _REVIEW_QUEUE_IO,
    SOURCE_ARCHIVED_MEDIA_MESSAGE,
    SOURCE_ARCHIVED_MESSAGE,
    SOURCE_ARCHIVED_REVIEW_REFUSAL,
    SOURCE_CLEANED_MEDIA_MESSAGE,
    SOURCE_CLEANED_MESSAGE,
    SOURCE_CLEANED_REVIEW_REFUSAL,
    SOURCE_MISSING_MESSAGE,
    ActionConflict,
    export_source_refusal,
    export_state_refusal,
    render_in_flight,
    review_summary,
    skip_refusal,
)
from biliflow.http_guards import (
    REQUEST_TIMEOUT_MESSAGE,
    REQUEST_TIMEOUT_SECONDS,
    content_length,
    loopback_bind_address,
    require_loopback_host,
)
from biliflow.job_import import import_existing_project
from biliflow.job_pipeline import DETECTOR_GROUPS
from biliflow.job_store import IN_PROCESS_STATES, SOURCE_ARCHIVED_STATES, JobStore, now_iso
from biliflow.final_renderer import (
    expected_output_duration,
    normalize_output_size_policy,
    read_render_progress,
    render_progress_path,
)
from biliflow.review_workflow import (
    _interactive_html,
    apply_visual_ai_assessments,
    authorize_final_from_resolved_review,
    build_edit_plan,
    bulk_accept_suggested_decisions,
    bulk_keep_review_items,
    clear_review_decision,
    existing_review_export,
    record_review_decision,
    review_resource_status,
    review_export_paths,
)
from biliflow.review_evidence import (
    VIDEO_MIME_TYPES,
    ReviewFrameCache,
    ReviewMediaError,
    item_evidence,
    read_json_cached,
    stream_file,
    strip_time,
)
from biliflow.scheduler import SKIPPED_REFUSAL, InputWatcher, JobScheduler
from biliflow.storage import storage_status
from biliflow import phone_access, tailscale_manager


UNCONFIGURED_RECYCLE_BIN_MESSAGE = "Chưa cấu hình Thùng rác cho Control Center này."
UNCONFIGURED_DELETE_MESSAGE = "Control Center này chưa được phép xóa video gốc."
# "Xóa video gốc" and "Xóa video" delete for good since 2026-10-05: a POST must
# say so, so a page loaded before (which promised the Recycle Bin) cannot delete.
CONFIRM_PERMANENT_MESSAGE = (
    "Thiếu xác nhận xóa vĩnh viễn (trang này có thể đã cũ). Tải lại trang, mở lại hộp thoại, "
    "đánh dấu “Tôi hiểu” rồi xóa."
)
# How long serve() keeps the process alive for an /api/shutdown stop() after
# serve_forever returned: stop() waits up to 90 s for a running cleanup.
STOP_WAIT_SECONDS = 120.0

# Dashboard V2 (route /dashboard-v2/) is the dashboard since 2026-10-06, at the user's request:
# "/" sends the browser there. The classic dashboard (_dashboard_html) is off for now but kept:
# CLASSIC_DASHBOARD = True serves it at "/" again, byte for byte as before (restart to apply).
CLASSIC_DASHBOARD = False
# Only these files of <code>/dashboard_v2 are served: never the demo page, fixtures, scripts,
# a directory listing or anything outside the folder.
DASHBOARD_V2_DIR = Path(__file__).resolve().parents[2] / "dashboard_v2"
DASHBOARD_V2_PAGE = "live.html"
DASHBOARD_V2_FILES = frozenset({
    "styles.css", "theme.css", "contracts.js", "adapter.js", "download-demo.js", "app.js",
    "download-core.js", "download-view.js", "download-live.js",
    "review.css", "review-core.js", "review-detail.js", "review-media.js", "review-cards.js", "review.js",
    "assets/mark.svg", "assets/poster-amber.svg", "assets/poster-blue.svg",
    "assets/poster-rose.svg", "assets/poster-sage.svg", "assets/poster-violet.svg",
})
DASHBOARD_V2_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
}
# connect-src 'self' replaces the demo's 'none' on this route only; framing stays same-origin.
DASHBOARD_V2_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'self'"
)
# Phone mode (batch 2, plan §12.3): the access-code page of the phone listener.
PHONE_LOGIN_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; "
    "frame-ancestors 'none'"
)
_PHONE_ACCESS_LOCK = threading.Lock()
_TAILSCALE_LOCK = threading.Lock()
# The classic review page as served by the phone listener (user decision, plan §8 question 10):
# added after its own styles, so 127.0.0.1:8765 serves the page byte for byte as before.
# (a) an arrow at the right edge of the filter chips, (b) no "phím N" hints on touch screens,
# (c) the four decision buttons stay at the bottom of the screen, (d) no text under 12 px.
REVIEW_PHONE_STYLE = (
    "<style id=\"phone-review\">"
    "@media (hover:none) and (pointer:coarse){.decide button:not(.sel) small{display:none}}"
    "@media (max-width:820px){"
    ".chips-wrap{position:relative}"
    ".chips-wrap .chips{padding-right:44px}"
    ".chips-more{position:absolute;right:0;top:10px;bottom:2px;width:42px;border:0;border-radius:0 999px 999px 0;"
    "background:linear-gradient(to right,transparent,var(--bg,#0d1117) 45%);color:var(--text,#e6edf3);"
    "font-size:24px;font-weight:700;line-height:1;padding:0 4px 0 14px;text-align:right}"
    ".chips-more[hidden]{display:none}"
    # Fixed, not sticky: the buttons sit in a column below the video inside an overflow:hidden card.
    ".decide{position:fixed;left:0;right:0;bottom:0;margin:0;z-index:30;gap:8px;background:var(--card,#161b22);"
    "padding:8px 12px calc(8px + env(safe-area-inset-bottom,0px));box-shadow:0 -6px 18px rgba(0,0,0,.55)}"
    ".decide button{padding:10px 6px;font-size:15px}"
    ".thumb i,.thumb span,.thumb.peak::after{font-size:12px;line-height:15px}"
    ".mchip b,.mchip span,details.export>summary .chev{font-size:12px}"
    "}</style>"
)
REVIEW_PHONE_SCRIPT = (
    "<script id=\"phone-review-js\">(function(){"
    "function setup(){var chips=document.getElementById('chips');if(!chips||chips.parentNode.classList.contains('chips-wrap'))return;"
    "var wrap=document.createElement('div');wrap.className='chips-wrap';chips.parentNode.insertBefore(wrap,chips);wrap.appendChild(chips);"
    "var more=document.createElement('button');more.type='button';more.className='chips-more';more.textContent='\\u203a';"
    "more.setAttribute('aria-label','Xem thêm bộ lọc');wrap.appendChild(more);"
    "function sync(){more.hidden=chips.scrollLeft+chips.clientWidth>=chips.scrollWidth-4}"
    "more.addEventListener('click',function(){chips.scrollBy({left:Math.max(80,chips.clientWidth*0.7),behavior:'smooth'})});"
    "chips.addEventListener('scroll',sync,{passive:true});window.addEventListener('resize',sync);"
    "if(window.ResizeObserver)new ResizeObserver(sync).observe(chips);sync()}"
    # The page re-renders .decide; keep room at the end of the page for the fixed buttons.
    "var queued=false;function pad(){queued=false;var d=document.querySelector('.decide');"
    "var fixed=d&&getComputedStyle(d).position==='fixed';document.body.style.paddingBottom=fixed?(d.offsetHeight+12)+'px':''}"
    "function later(){if(!queued){queued=true;requestAnimationFrame(pad)}}"
    "function start(){setup();pad();new MutationObserver(later).observe(document.body,{childList:true,subtree:true});"
    "window.addEventListener('resize',later)}"
    "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();"
    "})();</script>"
)


def _with_phone_notice(html: str, center: Any) -> str:
    """The classic dashboard with one line while the phone mode is on (H3); unchanged otherwise."""
    phone = getattr(center, "phone", None)
    if phone is None or not phone.enabled or "<body>" not in html:
        return html
    import html as _html
    url = phone.status().get("url") or ""
    notice = (
        "<div id=\"phone-mode-notice\" role=\"status\" style=\"background:#3b2f12;color:#f5d58a;"
        "border-bottom:1px solid #6b5420;padding:8px 16px;font:14px system-ui,sans-serif\">"
        f"Đang mở cho điện thoại: {_html.escape(url)} "
        "(tắt trong <a href=\"/dashboard-v2/#settings\" style=\"color:#ffe7a8\">Dashboard V2 → Cài đặt</a>)</div>"
    )
    return html.replace("<body>", "<body>" + notice, 1)


DASHBOARD_V2_VIEWS = ("overview", "downloads", "videos", "queue", "logos", "settings")
REVIEW_BACK_BUTTON = "onclick=\"location.href='/'\""


def _review_back_to_v2(html: str, query: str) -> str:
    """M4: with ?from=v2, the classic review page's back button opens /dashboard-v2/#<view>.

    Only a view from DASHBOARD_V2_VIEWS is used (anything else → overview); nothing else from the
    URL reaches the page. Without from=v2 the page is returned unchanged.
    """
    params = urllib.parse.parse_qs(query or "")
    if params.get("from") != ["v2"]:
        return html
    view = (params.get("view") or [""])[0]
    if view not in DASHBOARD_V2_VIEWS:
        view = "overview"
    return html.replace(REVIEW_BACK_BUTTON, f"onclick=\"location.href='/dashboard-v2/#{view}'\"", 1)


def _review_page_for_phone(html: str) -> str:
    """The classic review page plus REVIEW_PHONE_STYLE/SCRIPT, for the phone listener only."""
    if "</head>" not in html or "</body>" not in html:
        return html
    head, rest = html.split("</head>", 1)
    body, tail = rest.rsplit("</body>", 1)
    return head + REVIEW_PHONE_STYLE + "</head>" + body + REVIEW_PHONE_SCRIPT + "</body>" + tail


def _download_answer(center: Any, method: str, path: str, value: Any) -> tuple[int, Any] | None:
    """The "Tải video" and "Dung lượng" routes, or None for every other path.

    ``value`` is the query string (GET) or the JSON body (POST). The handler has
    already checked Host, the token (POST) and, on the phone, PHONE_ALLOWED_POSTS.
    """
    if not download_api.owns(path):
        return None
    service = getattr(center, "downloads", None)
    if service is None:
        error = getattr(center, "downloads_error", None) or "chưa khởi tạo"
        return 503, {"error": download_api.UNAVAILABLE_MESSAGE.format(error=error)}
    if method == "GET":
        return service.handle_get(path, value)
    return service.handle_post(path, value)


def _phone_access(center: Any) -> phone_access.PhoneAccess:
    """The center's phone listener state, created on first use (stub centers in tests too)."""
    with _PHONE_ACCESS_LOCK:
        value = getattr(center, "phone", None)
        if value is None:
            store = getattr(center, "store", None)

            def store_event(event_type: str, message: str, payload: dict[str, Any]) -> None:
                if store is not None:
                    level = "WARN" if event_type in ("PHONE_CODE_LOCKED", "PHONE_UNLOCK_LOCKED") else "INFO"
                    store.add_event(None, event_type, message, level=level, payload=payload or None)

            # The Tailscale address comes from the Tailscale BiliFlow manages (runtime\tailscale first).
            # Over Tailscale a device of the PC's own Tailscale account needs no code (the user's choice,
            # 2026-10-07); the manager asks Tailscale who a device is.
            value = phone_access.PhoneAccess(
                on_event=store_event, tailscale=lambda: _tailscale(center).address(),
                tailscale_device=lambda ip: _tailscale(center).same_account_device(ip))
            if store is not None:
                # Question 14: the panel shows the latest phone events and the last state after a restart.
                with contextlib.suppress(Exception):
                    value.restore_history(store.events(None, limit=500))
            center.phone = value
        return value


def _tailscale(center: Any) -> tailscale_manager.TailscaleManager:
    """The center's Tailscale manager (Dashboard V2 → Cài đặt → Tailscale), created on first use."""
    with _TAILSCALE_LOCK:
        value = getattr(center, "tailscale", None)
        if value is None:
            store = getattr(center, "store", None)

            def store_event(event_type: str, message: str, payload: dict[str, Any]) -> None:
                if store is not None:
                    store.add_event(None, event_type, message, level="INFO" if payload.get("ok") else "WARN",
                                    payload=payload or None)

            def phone_enable() -> dict[str, Any]:
                # "Mở cho điện thoại ngoài nhà": the phone mode over Tailscale, as the PC panel's button does.
                return _phone_access(center).enable(lambda access: _phone_handler_class(center, access),
                                                    network="tailscale")

            value = tailscale_manager.TailscaleManager(center.root, phone_enable=phone_enable, on_event=store_event)
            center.tailscale = value
        return value


PHONE_PAGE_WARNINGS = {
    "wifi": "Chỉ dùng trong Wi-Fi nhà: kết nối này là HTTP, không mã hóa.",
    "tailscale": "Qua Tailscale: chỉ thiết bị đã đăng nhập Tailscale của bạn mở được trang này; "
                 "Tailscale mã hóa đường truyền. Thiết bị cùng tài khoản Tailscale với PC vào thẳng, không "
                 "cần mã; trang này hiện khi thiết bị chưa được nhận ra (tài khoản khác, thiết bị được chia "
                 "sẻ, hoặc Tailscale trên PC chưa trả lời): nhập mã trên PC.",
}


def _phone_page(title: str, message: str, *, form: bool, attempts_left: int | None = None,
                redirect: str | None = None, unlock: bool = False, network: str | None = None) -> bytes:
    """Small page of the phone listener: the code form, a refusal, or the hop to V2."""
    import html as _html
    note = (f"<p class=\"left\">Còn {attempts_left} lần nhập.</p>" if attempts_left is not None else "")
    body = (
        "<form method=\"post\" action=\"/phone-login\" autocomplete=\"off\">"
        f"<label>{'Khóa mở đặc biệt' if unlock else 'Mã truy cập (8 ký tự, hiện trên PC)'}"
        "<input name=\"code\" inputmode=\"text\" "
        "autocapitalize=\"none\" autocomplete=\"one-time-code\" maxlength=\"32\" required autofocus></label>"
        "<button type=\"submit\">Vào BiliFlow</button></form>" if form else ""
    )
    # A same-site refresh after the form, so the new SameSite=Strict cookie is sent with V2.
    refresh = f"<meta http-equiv=\"refresh\" content=\"0;url={_html.escape(redirect)}\">" if redirect else ""
    hop = f"<p><a href=\"{_html.escape(redirect)}\">Mở BiliFlow</a></p>" if redirect else ""
    return (
        "<!doctype html><html lang=\"vi\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<meta name=\"referrer\" content=\"same-origin\">{refresh}<title>BiliFlow · điện thoại</title>"
        "<style>body{font:16px system-ui,sans-serif;margin:0;padding:24px 16px;background:#f5f7fb;color:#18202b}"
        "main{max-width:420px;margin:auto;background:#fff;border:1px solid #d8dee8;border-radius:14px;padding:20px}"
        "h1{font-size:20px;margin:0 0 12px}label{display:block;font-weight:600;margin:14px 0 6px}"
        "input{display:block;width:100%;box-sizing:border-box;font-size:22px;letter-spacing:3px;padding:10px;"
        "margin-top:6px;border:1px solid #9aa6b8;border-radius:10px}button{margin-top:14px;width:100%;"
        "font-size:17px;padding:12px;border:0;border-radius:10px;background:#245ebc;color:#fff}"
        ".msg{color:#b42338;font-weight:600}.left,.warn{color:#5b6677;font-size:14px}</style></head><body><main>"
        f"<h1>{_html.escape(title)}</h1><p class=\"msg\">{_html.escape(message)}</p>{note}{body}{hop}"
        f"<p class=\"warn\">{_html.escape(PHONE_PAGE_WARNINGS.get(network or '', PHONE_PAGE_WARNINGS['wifi']))}</p>"
        "</main></body></html>"
    ).encode("utf-8")


def _unconfigured_recycler(*_args: Any, **_kwargs: Any) -> Any:
    """ControlCenter.export_recycler until __init__ binds the real one (stubs and tests never reach the shell)."""
    raise RuntimeError(UNCONFIGURED_RECYCLE_BIN_MESSAGE)


def _unconfigured_deleter(*_args: Any, **_kwargs: Any) -> Any:
    """ControlCenter.source_deleter until __init__ binds the real one (stubs never delete a file)."""
    raise job_purge.DeleteRefused(UNCONFIGURED_DELETE_MESSAGE)


def _unconfigured_bin_info(path: Any) -> Any:
    """ControlCenter.bin_info until __init__ binds the real one; a preview then shows it as blocked."""
    raise recycle_bin.RecycleRefused(UNCONFIGURED_RECYCLE_BIN_MESSAGE)


def _unconfigured_finder(*_args: Any, **_kwargs: Any) -> Any:
    """ControlCenter.record_finder until __init__ binds the real one ("Kiểm tra lại Thùng rác")."""
    raise RuntimeError(UNCONFIGURED_RECYCLE_BIN_MESSAGE)


# "Ẩn khỏi danh sách" / "Hiện lại" (batch 4): display only, for cancelled jobs.
# Format with job_id=...
HIDE_NOT_CANCELLED_MESSAGE = "Video #{job_id} chưa bị hủy; chỉ ẩn được video đã hủy."
HIDE_ALREADY_MESSAGE = "Video #{job_id} đã được ẩn khỏi danh sách."
UNHIDE_NOT_HIDDEN_MESSAGE = "Video #{job_id} không bị ẩn."
JOB_HIDDEN_MESSAGE = "Ẩn khỏi danh sách; report, quyết định duyệt và video gốc giữ nguyên"
JOB_UNHIDDEN_MESSAGE = "Hiện lại trong danh sách"


def _merge_visual_audit_batches(
    batches: list[dict[str, Any]], *, expected_assessment_count: int,
) -> dict[str, Any]:
    if not batches:
        raise ValueError("Visual AI Audit produced no batches")
    severity = {"PASS": 0, "WARN": 1, "BLOCK": 2}
    merged = dict(batches[0])
    merged["result"] = max(
        (str(batch.get("result") or "WARN") for batch in batches),
        key=lambda value: severity.get(value, 1),
    )
    merged["findings"] = list(dict.fromkeys(
        str(value)
        for batch in batches
        for value in list(batch.get("findings") or [])
    ))
    merged["recommended_actions"] = list(dict.fromkeys(
        str(value)
        for batch in batches
        for value in list(batch.get("recommended_actions") or [])
    ))
    assessments: dict[str, dict[str, Any]] = {}
    for batch in batches:
        for item in list(batch.get("visual_assessments") or []):
            item_id = str(item.get("item_id") or "")
            if not item_id:
                continue
            current = assessments.get(item_id)
            if current is None:
                assessments[item_id] = item
                continue
            current_uncertain = current.get("classification") == "uncertain"
            item_uncertain = item.get("classification") == "uncertain"
            if (
                (current_uncertain and not item_uncertain)
                or (
                    current_uncertain == item_uncertain
                    and float(item.get("confidence") or 0)
                    > float(current.get("confidence") or 0)
                )
            ):
                assessments[item_id] = item
    merged["visual_assessments"] = list(assessments.values())
    image_count = sum(
        int((batch.get("visual_audit") or {}).get("image_count") or 0)
        for batch in batches
    )
    merged_visual = dict(merged.get("visual_audit") or {})
    merged_visual.update({
        "image_count": image_count,
        "batch_count": len(batches),
        "batch_size": max(
            int((batch.get("visual_audit") or {}).get("batch_limit") or 0)
            for batch in batches
        ),
        "expected_assessment_count": expected_assessment_count,
        "completed_assessment_count": len(assessments),
    })
    merged["visual_audit"] = merged_visual
    if len(assessments) < expected_assessment_count:
        missing = expected_assessment_count - len(assessments)
        merged["result"] = "WARN" if merged["result"] == "PASS" else merged["result"]
        merged["findings"] = list(dict.fromkeys([
            f"Visual AI omitted {missing} attached review item(s).",
            *merged["findings"],
        ]))
    merged["summary"] = (
        f"Visual AI analyzed {image_count} thumbnail(s) in {len(batches)} batch(es) "
        f"and returned {len(assessments)}/{expected_assessment_count} structured assessment(s)."
    )
    merged["_thread_id"] = batches[-1].get("_thread_id")
    return merged


class SingleInstanceLock:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+b")
        self.handle.seek(0)
        self.handle.write(b"0")
        self.handle.flush()
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            raise RuntimeError("BiliFlow Control Center is already running") from error

    def close(self) -> None:
        if self.handle.closed:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# Jobs whose card shows a review summary (the only queues status() reads).
REVIEW_SUMMARY_STATES = frozenset({"WAITING_REVIEW", "READY_TO_EXPORT", "SKIPPED"})
# A review decision never moves these jobs: an export waiting or running keeps
# the plan fixed at finalize, and a skip ends only through unskip or rerun.
SYNC_KEEP_STATES = frozenset({"QUEUED", "SKIPPED"}) | IN_PROCESS_STATES


# Per-process cache of review summaries, keyed by (path, mtime_ns, size). It
# keeps only the counts, so the 3 s dashboard poll never parses an unchanged
# queue again and never evicts the review page's evidence cache.
_SUMMARY_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_SUMMARY_CACHE_LOCK = threading.Lock()
_SUMMARY_CACHE_LIMIT = 256


def _cached_review_summary(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    key = os.path.normcase(str(path))
    with _SUMMARY_CACHE_LOCK:
        hit = _SUMMARY_CACHE.get(key)
    if hit is not None and hit[0] == stat.st_mtime_ns and hit[1] == stat.st_size:
        return dict(hit[2])
    try:
        with _REVIEW_QUEUE_IO:
            summary = review_summary(_read_json(path))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    with _SUMMARY_CACHE_LOCK:
        if len(_SUMMARY_CACHE) >= _SUMMARY_CACHE_LIMIT:
            _SUMMARY_CACHE.clear()
        _SUMMARY_CACHE[key] = (stat.st_mtime_ns, stat.st_size, summary)
    return dict(summary)


def _inside(root: Path, target: Path) -> Path:
    root = root.resolve(strict=True)
    target = target.resolve(strict=True)
    if target != root and root not in target.parents:
        raise ValueError("Path is outside the allowed BiliFlow directory")
    return target


_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})


def _host_allowed(header: str | None, configured_host: str | None) -> bool:
    """Accept only loopback names (or the bound host), with or without a port.

    Rejecting other Host headers blocks DNS rebinding: a foreign page whose
    name resolves to 127.0.0.1 still sends its own name and cannot read the
    session token, the review page or 18+ thumbnails.
    """
    value = (header or "").strip().casefold()
    if not value:
        return False
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            return False
        host, rest = value[:closing + 1], value[closing + 1:]
    else:
        host, separator, port = value.partition(":")
        rest = f":{port}" if separator else ""
    if rest and not re.fullmatch(r":\d{1,5}", rest):
        return False
    allowed = set(_LOCAL_HOSTS)
    configured = (configured_host or "").strip().casefold()
    if configured:
        bare = configured.strip("[]")
        allowed.add(f"[{bare}]" if ":" in bare else bare)
    return host in allowed


def _resources(root: Path) -> dict[str, Any]:
    disk = psutil.disk_usage(str(root))
    memory = psutil.virtual_memory()
    result: dict[str, Any] = {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory": {"percent": memory.percent, "used_bytes": memory.used, "total_bytes": memory.total},
        "disk": {"percent": disk.percent, "free_bytes": disk.free, "total_bytes": disk.total},
    }
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
        result["gpu"] = {
            "memory_used_bytes": info.used, "memory_total_bytes": info.total,
            "utilization_percent": utilization.gpu,
            "temperature_c": pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU),
        }
        pynvml.nvmlShutdown()
    except Exception:
        result["gpu"] = None
    return result


def _dashboard_html() -> str:
    page = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BiliFlow Control Center</title>
<style>
:root{color-scheme:dark;font:15px system-ui,-apple-system,"Segoe UI",sans-serif;background:#090b10;color:#e8ecf4;--panel:#141923;--panel-2:#1a202c;--line:#2b3546;--muted:#9aa7ba;--blue:#55b8f5;--green:#61d39b;--amber:#e3ad4d;--red:#ff7f8e}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 15% -10%,#18263a 0,transparent 34rem),#090b10}header{padding:16px 24px;background:rgba(15,19,27,.94);border-bottom:1px solid #222b39;display:flex;gap:10px;align-items:center;position:sticky;top:0;z-index:4;backdrop-filter:blur(14px)}h1{font-size:21px;margin:0 auto 0 0;letter-spacing:-.02em}.pill{padding:6px 10px;border:1px solid #303a4b;border-radius:99px;background:#202633;color:#cbd5e4;font-size:13px}main{padding:22px;max-width:1500px;margin:auto}.bar,.job,.workspace{background:rgba(20,25,35,.96);border:1px solid var(--line);border-radius:16px;box-shadow:0 14px 36px rgba(0,0,0,.16)}.bar{padding:14px;margin-bottom:14px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}.ai{display:grid;grid-template-columns:1fr auto;gap:16px}.ai-controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.workspace{overflow:clip}.workspace-head{padding:18px 20px 0}.workspace-title{font-size:19px;font-weight:750;margin:0}.workspace-subtitle{color:var(--muted);font-size:13px;margin-top:4px}.job-tabs{display:flex;gap:8px;padding:16px 20px 0;border-bottom:1px solid var(--line);overflow-x:auto;position:sticky;top:var(--header-h,64px);z-index:3;background:rgba(20,25,35,.98);backdrop-filter:blur(14px)}.job-tab{display:flex;align-items:center;gap:8px;padding:11px 14px;border:1px solid transparent;border-radius:10px 10px 0 0;background:transparent;color:#aab6c8;white-space:nowrap}.job-tab:hover{background:#1c2330;color:#fff}.job-tab.active{background:#202837;border-color:#354258;border-bottom-color:#202837;color:#fff}.tab-count{min-width:24px;padding:2px 7px;border-radius:99px;background:#2d3748;font-size:12px;text-align:center}.job-tab.active .tab-count{background:#286b96;color:#eaf7ff}.job-list{padding:16px}.job{padding:0;margin-bottom:14px;overflow:hidden}.job:last-child{margin-bottom:0}.job[data-bucket="completed"]{border-color:#285744}.job[data-bucket="scanning"],.job[data-bucket="export"]{border-color:#2e5872}.job[data-bucket="review"]{border-color:#4a4227}.job-head{display:flex;gap:14px;align-items:flex-start;padding:17px 18px;border-bottom:1px solid #273142}.job-identity{min-width:0;flex:1}.job-title{font-size:17px;font-weight:750;word-break:break-word}.job-key{color:#738197;font-size:12px;margin-top:3px;word-break:break-all}.job-path{color:var(--muted);font-size:12px;margin-top:7px;word-break:break-all}.state-badge{padding:7px 10px;border:1px solid #3b4658;border-radius:99px;background:#252d3a;font-weight:700;font-size:12px;white-space:nowrap}.tone-running{color:#8fd8ff;border-color:#2e6f96;background:#153348}.tone-complete{color:#88e7b8;border-color:#297252;background:#143c2e}.tone-waiting{color:#f0c66f;border-color:#74591f;background:#3d3014}.tone-error{color:#ff9aa5;border-color:#7e3540;background:#401d24}.job-badges{display:flex;flex-direction:column;align-items:flex-end;gap:6px}.queue-badge{padding:5px 9px;border:1px dashed #5b6b84;border-radius:99px;color:#c9d6e8;font-size:12px;font-weight:700;white-space:nowrap}.job-body{padding:17px 18px}.progress-row{display:flex;align-items:center;gap:12px}.progress-label{min-width:145px;color:#c8d2e1;font-size:13px}.progress{height:8px;background:#252d39;border-radius:99px;overflow:hidden;flex:1}.progress i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#368fd2,#5dc6f5)}.progress-value{min-width:40px;text-align:right;color:#c9d8e9;font-variant-numeric:tabular-nums}.status-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:15px}.status-box{min-height:106px;padding:12px;border:1px solid #2d3748;border-radius:12px;background:#10151e}.status-box.tone-complete{border-color:#285b46;background:#10261f}.status-box.tone-running{border-color:#285e7c;background:#102431}.status-box.tone-waiting{border-color:#655020;background:#2a2415}.status-box.tone-error{border-color:#70313b;background:#2c171c}.status-kicker{color:#8f9db0;font-size:11px;font-weight:750;text-transform:uppercase;letter-spacing:.06em}.status-value{font-weight:750;margin-top:7px}.status-detail{color:#9eacbf;font-size:12px;line-height:1.4;margin-top:5px}.mini-progress{height:6px;margin-top:9px;border-radius:99px;background:#273242;overflow:hidden}.mini-progress i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#2e8dca,#67d1ff)}.phase-group+.phase-group{margin-top:20px}.phase-heading{display:flex;align-items:center;gap:9px;margin:1px 2px 11px;color:#d8e3f1;font-size:13px;font-weight:750}.phase-heading span{padding:2px 7px;border-radius:99px;background:#293444;color:#aebdd0;font-size:11px}.job-error{margin-top:12px;padding:10px 12px;border:1px solid #793641;border-radius:10px;background:#341a20;color:#ff9ba6}.job-footer{padding:14px 18px 17px;border-top:1px solid #273142;background:#11161f}.scope-line{color:#9ba9bc;font-size:12px;margin-bottom:12px}.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.actions select{max-width:220px}button,select{border:1px solid transparent;border-radius:9px;padding:9px 12px;background:#2d70b7;color:#fff;cursor:pointer}button:hover{filter:brightness(1.08)}button.warn{background:#8b611d}button.danger{background:#963845}button.green{background:#247554}button:disabled{opacity:.45;cursor:not-allowed}.detectors{width:100%;border:1px solid #39445a;border-radius:10px;padding:9px 11px;text-align:left}.detectors legend{color:#9ca8bb;padding:0 5px}.detectors label{display:inline-flex;gap:5px;align-items:center;margin:3px 10px 3px 0}.rerun-panel{width:100%;border:1px solid #354055;border-radius:11px;background:#171d28}.rerun-panel summary{cursor:pointer;padding:10px 12px;color:#c5d0df;font-weight:650}.rerun-body{display:flex;gap:8px;flex-wrap:wrap;padding:0 10px 10px}.export-panel{width:100%;border:1px solid #2f5a45;border-radius:11px;background:#131f1a}.export-panel summary{cursor:pointer;padding:10px 12px;color:#bfe9d2;font-weight:650}.export-body{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:0 10px 10px}.export-body label{display:inline-flex;gap:6px;align-items:center;color:#c8d2e1}.export-body input{width:96px;border:1px solid #39445a;border-radius:9px;padding:8px;background:#0f141c;color:#e8ecf4}.export-reason{color:#f0c66f;font-size:12px;flex-basis:100%}.export-error{color:#ff9aa5;font-size:12px;flex-basis:100%}[hidden]{display:none!important}.empty{text-align:center;padding:52px 20px;color:var(--muted)}.detail{font-size:13px}.muted{color:var(--muted)}.state{font-weight:700;color:var(--blue)}.ok{color:var(--green)}.error{color:var(--red)}.notice{display:none;position:fixed;left:16px;right:16px;bottom:16px;z-index:30;max-width:640px;margin:0 auto;border-radius:10px;padding:12px 14px;background:#173c30;border:1px solid #2f8b69;box-shadow:0 10px 30px rgba(0,0,0,.45);cursor:pointer}.notice.error{display:block;background:#4a2027;border-color:#a43d4a}.notice.show{display:block}.cleanup-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 14px;padding:12px 14px;border:1px solid #2f5a45;border-radius:12px;background:#131f1a}.cleanup-summary{color:#d8e3f1;font-weight:650}.cleanup-toolbar .cleanup-summary{flex:1 1 280px}.cleanup-pick{display:inline-flex;gap:6px;align-items:center;color:#c8d2e1;padding:6px 2px;cursor:pointer}.cleanup-pick input{width:17px;height:17px;margin:0}.source-line{margin:0 0 12px;padding:8px 10px;border:1px solid #2d3748;border-radius:9px;background:#10151e;color:#c8d2e1;font-size:12px;line-height:1.45;word-break:break-word}.source-line.tone-complete{color:#88e7b8;border-color:#285b46;background:#10261f}.source-line.tone-running{color:#8fd8ff;border-color:#285e7c;background:#102431}.source-line.tone-waiting{color:#f0c66f;border-color:#655020;background:#2a2415}.source-line.tone-error{color:#ff9aa5;border-color:#70313b;background:#2c171c}.cleanup-note{color:var(--muted);font-size:12px;line-height:1.4}.actions .cleanup-note{flex-basis:100%}#cleanup-dialog{width:min(900px,calc(100vw - 24px));max-width:none;max-height:calc(100vh - 24px);padding:0;border:1px solid #354258;border-radius:14px;background:#141923;color:#e8ecf4;box-shadow:0 24px 70px rgba(0,0,0,.6)}#cleanup-dialog::backdrop{background:rgba(4,6,10,.74)}.cleanup-form{display:flex;flex-direction:column;max-height:calc(100vh - 26px);margin:0}.cleanup-form h2{margin:0;padding:16px 18px;font-size:18px;border-bottom:1px solid var(--line)}.cleanup-scroll{flex:1 1 auto;min-height:0;overflow:auto;padding:14px 18px}.cleanup-scroll p{margin:0 0 10px;line-height:1.45}.cleanup-scroll ul{margin:4px 0 10px;padding-left:20px;color:#c8d2e1;font-size:13px;line-height:1.5}.cleanup-bin{color:#c8d2e1;font-size:13px}.cleanup-table-wrap{overflow-x:auto;margin:0 0 10px;border:1px solid #273142;border-radius:10px}.cleanup-table{width:100%;border-collapse:collapse;font-size:13px}.cleanup-table th,.cleanup-table td{padding:8px 10px;border-bottom:1px solid #273142;text-align:left;vertical-align:top;word-break:break-word}.cleanup-table th{color:#8f9db0;font-size:11px;font-weight:750;text-transform:uppercase;letter-spacing:.05em;background:#10151e}.cleanup-table tbody tr:last-child td{border-bottom:0}.cleanup-alert{padding:9px 11px;border:1px solid #74591f;border-radius:9px;background:#3d3014;color:#f0c66f}.cleanup-block{padding:9px 11px;border:1px solid #7e3540;border-radius:9px;background:#401d24;color:#ff9aa5}.cleanup-actions{position:sticky;bottom:0;display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap;padding:12px 18px;border-top:1px solid var(--line);background:#141923}@media(max-width:1050px){.status-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.ai{grid-template-columns:1fr}}@media(max-width:680px){header{align-items:flex-start;flex-wrap:wrap}h1{width:100%}main{padding:12px}.status-grid{grid-template-columns:1fr}.job-head{flex-direction:column}.job-badges{flex-direction:row;align-items:center;flex-wrap:wrap}.progress-row{align-items:flex-start;flex-wrap:wrap}.progress-label{width:100%}.progress{min-width:180px}.job-tabs{padding-left:12px}.job-list{padding:10px}.actions{align-items:stretch}.actions button,.actions select{flex:1 1 auto}.export-body label{flex:1 1 100%;flex-wrap:wrap}.actions .export-body select{flex:1 1 100%;max-width:none}.export-body input{flex:1}.cleanup-toolbar button{flex:1 1 100%}.cleanup-table-wrap{border:0}.cleanup-table thead{display:none}.cleanup-table tr,.cleanup-table td{display:block}.cleanup-table tr{padding:8px 0;border-bottom:1px solid #273142}.cleanup-table td{padding:3px 0;border:0}.cleanup-table td::before{content:attr(data-label) ": ";color:#8f9db0;font-weight:650}.cleanup-scroll{padding:12px}.cleanup-actions{padding:10px 12px}.cleanup-actions button{flex:1}}
#archive-dialog{width:min(900px,calc(100vw - 24px));max-width:none;max-height:calc(100vh - 24px);padding:0;border:1px solid #354258;border-radius:14px;background:#141923;color:#e8ecf4;box-shadow:0 24px 70px rgba(0,0,0,.6)}#archive-dialog::backdrop{background:rgba(4,6,10,.74)}.archive-badge{padding:5px 9px;border:1px solid #3f5f86;border-radius:99px;background:#16263a;color:#a9cdf5;font-size:12px;font-weight:700;white-space:nowrap}.archive-note{color:var(--muted);font-size:12px;line-height:1.4}.phase-fold>summary{cursor:pointer;list-style:none}.phase-fold>summary::-webkit-details-marker{display:none}.phase-fold>summary::before{content:"▸";color:#8f9db0;font-size:12px}.phase-fold[open]>summary::before{content:"▾"}.hidden-list{display:flex;flex-direction:column;gap:6px}.hidden-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:8px 12px;border:1px solid #273142;border-radius:10px;background:#10151e;color:#c8d2e1;font-size:13px}.hidden-row .hidden-name{flex:1 1 240px;font-weight:650;word-break:break-word}.hidden-row .hidden-when{color:var(--muted);font-size:12px}@media(max-width:680px){.hidden-row button{flex:1 1 100%}}
#delete-dialog{width:min(900px,calc(100vw - 24px));max-width:none;max-height:calc(100vh - 24px);padding:0;border:1px solid #354258;border-radius:14px;background:#141923;color:#e8ecf4;box-shadow:0 24px 70px rgba(0,0,0,.6)}#delete-dialog::backdrop{background:rgba(4,6,10,.74)}.cleanup-ack{display:flex;gap:9px;align-items:flex-start;margin:4px 0 10px;padding:10px 12px;border:1px solid #7e3540;border-radius:9px;background:#2c171c;color:#ffd5da;line-height:1.45;cursor:pointer}.cleanup-ack input{flex:none;width:17px;height:17px;margin:2px 0 0}.lost-notice{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 14px;padding:12px 14px;border:1px solid #74591f;border-radius:12px;background:#2a2415}.lost-summary{flex:1 1 280px;color:#f0c66f;font-weight:650}.lost-notice .cleanup-note{flex-basis:100%}@media(max-width:680px){.lost-notice button{flex:1 1 100%}}
</style></head><body><header><h1>BiliFlow Control Center</h1><span class="pill" id="worker">Đang tải</span><span class="pill" id="resource"></span><a class="pill" href="/logo-memory" title="Logo hãng phim (giữ) và logo nền tảng (làm mờ) đã nhớ">Bộ nhớ logo</a></header>
<main><div id="notice" class="notice" role="status" title="Bấm để ẩn" onclick="this.className='notice'"></div><div class="bar"><button class="green" onclick="scheduler(false)">Chạy hàng đợi</button><button class="warn" onclick="scheduler(true)">Tạm dừng scheduler</button><button onclick="location.reload()">Làm mới</button><button class="danger" onclick="shutdown('after_stage')">Tắt sau bước hiện tại</button><button class="danger" onclick="shutdown('immediate')">Dừng ngay và tắt</button><span class="muted">Đóng tab không làm dừng xử lý. Muốn tắt hẳn, dùng nút Tắt hoặc Stop-BiliFlow.cmd.</span></div><section class="bar ai"><div><div class="job-title">AI Supervisor</div><div id="ai-message" class="muted">Đang kiểm tra Codex…</div><div class="detail muted">AI JSON không gửi media. Visual AI chỉ gửi tối đa 36 thumbnail sau khi bạn xác nhận cho từng video; không gửi video/âm thanh. Mọi lượt audit dùng chung một session AI Supervisor và được chạy tuần tự. Dùng hạn mức ChatGPT, API key và model GPT-6 bị chặn.</div></div><div class="ai-controls"><label><input type="checkbox" id="ai-enabled"> Bật</label><select id="ai-model"><option value="gpt-5.6-luna">GPT-5.6 Luna — mặc định</option><option value="gpt-5.6-terra">GPT-5.6 Terra — cân bằng</option><option value="gpt-5.6-sol">GPT-5.6 Sol — mạnh hơn</option></select><select id="ai-effort"><option value="low">Low — tiết kiệm</option><option value="medium">Medium — mặc định</option><option value="high">High — tối đa</option></select><button onclick="saveAI()">Lưu cấu hình</button><button onclick="checkAI()">Kiểm tra kết nối</button><button class="green" onclick="loginAI()">Đăng nhập Codex</button><a href="https://learn.chatgpt.com/docs/auth" target="_blank" rel="noopener" class="muted">Hướng dẫn chính thức</a></div></section><section class="workspace"><div class="workspace-head"><div class="workspace-title">Video</div><div class="workspace-subtitle">Theo dõi video theo từng giai đoạn: chờ xử lý, chờ chạy cảnh, đang chạy cảnh, chờ duyệt, xuất video và hoàn tất.</div></div><nav id="job-tabs" class="job-tabs" aria-label="Trạng thái video"></nav><section id="jobs" class="job-list"></section></section></main><dialog id="cleanup-dialog" aria-labelledby="cleanup-title"><form class="cleanup-form" method="dialog" onsubmit="return false"><h2 id="cleanup-title">Xóa vĩnh viễn video gốc</h2><div id="cleanup-dialog-body" class="cleanup-scroll"></div><div class="cleanup-actions"><button type="button" id="cleanup-cancel" onclick="closeCleanupDialog()">Hủy</button><button type="button" class="danger" id="cleanup-confirm" onclick="confirmCleanup()" disabled>Xóa vĩnh viễn</button></div></form></dialog>
<dialog id="archive-dialog" aria-labelledby="archive-title"><form class="cleanup-form" method="dialog" onsubmit="return false"><h2 id="archive-title">Lưu trữ video gốc</h2><div id="archive-dialog-body" class="cleanup-scroll"></div><div class="cleanup-actions"><button type="button" id="archive-cancel" onclick="closeArchiveDialog()">Hủy</button><button type="button" class="warn" id="archive-confirm" onclick="confirmArchive()" disabled>Lưu trữ</button></div></form></dialog>
<dialog id="delete-dialog" aria-labelledby="delete-title"><form class="cleanup-form" method="dialog" onsubmit="return false"><h2 id="delete-title">Xóa video khỏi BiliFlow</h2><div id="delete-dialog-body" class="cleanup-scroll"></div><div class="cleanup-actions"><button type="button" id="delete-cancel" onclick="closeDeleteDialog()">Hủy</button><button type="button" class="danger" id="delete-confirm" onclick="confirmDelete()" disabled>Xóa vĩnh viễn</button></div></form></dialog>
<script>
__EXPORT_DIALOG_JS__const SOURCE_MISSING_MESSAGE='__SOURCE_MISSING_MESSAGE__';const SOURCE_CLEANED_MESSAGE='__SOURCE_CLEANED_MESSAGE__';const SOURCE_ARCHIVED_MESSAGE='__SOURCE_ARCHIVED_MESSAGE__';
let token='';let status={};let aiState={ready:false,config:{},message:'Đang kiểm tra Codex…'};let noticeTimer=null;let activeJobTab=null;const DRAFT_PREFIX='biliflow.jobDraft.';const LAST_SCOPE_KEY='biliflow.lastDetectorScope';const SENSITIVE_DETECTORS=['adult','gore','violence'];const detectorDrafts={};const metadataDrafts={};const ocrDrafts={};const speedDrafts={};const draftJobKeys={};const rerunPanelDrafts={};const exportPanelDrafts={};const startingJobs=new Set();const skippingJobs=new Set();const unskippingJobs=new Set();const exportingJobs=new Set();let loadSeq=0,appliedSeq=0,jobsPointerDown=false,lastJobsInteraction=0,jobsRenderTimer=null;const cleanupSelection=new Set();let cleanupPreview=null,cleanupOpening=false,cleanupPosting=false;
const cancellingJobs=new Set();const hidingJobs=new Set();const recheckingRows=new Set();const foldOpen={cancelled:false,hidden:false,archived:false};
const ARCHIVE_RUNNING_TITLE='Đang dọn, lưu trữ hoặc khôi phục video gốc; chờ lượt hiện tại xong.';let archivePreview=null,archiveOpening=false,archivePosting=false;const restoringJobs=new Set();
let cleanupAck=false;let deletePreview=null,deleteOpening=false,deletePosting=false,deleteAck=false;
function storageGet(key){try{const raw=window.localStorage.getItem(key);return raw==null?null:JSON.parse(raw)}catch(e){return null}}
function storageSet(key,value){try{if(value==null)window.localStorage.removeItem(key);else window.localStorage.setItem(key,JSON.stringify(value))}catch(e){}}
function forgetDraftMemory(id){delete detectorDrafts[id];delete metadataDrafts[id];delete ocrDrafts[id];delete speedDrafts[id];delete draftJobKeys[id]}
function applyStoredDraft(id,draft){forgetDraftMemory(id);if(!draft||typeof draft!=='object')return;if(Array.isArray(draft.detectors))detectorDrafts[id]=draft.detectors.map(String);if(draft.metadata&&typeof draft.metadata==='object')metadataDrafts[id]={content_style:String(draft.metadata.content_style||''),profile:String(draft.metadata.profile||'')};if(draft.ocr!=null&&Number.isFinite(Number(draft.ocr)))ocrDrafts[id]=Number(draft.ocr);if(typeof draft.speed==='boolean')speedDrafts[id]=draft.speed;if(draft.job_key)draftJobKeys[id]=String(draft.job_key)}
function draftId(key){if(!key||!key.startsWith(DRAFT_PREFIX))return null;const id=Number(key.slice(DRAFT_PREFIX.length));return Number.isInteger(id)?id:null}
function loadStoredDrafts(){try{const storage=window.localStorage,keys=[];for(let i=0;i<storage.length;i++)keys.push(storage.key(i));keys.forEach(key=>{const id=draftId(key);if(id!==null)applyStoredDraft(id,storageGet(key))})}catch(e){}}
function saveDraft(id){const job=(status.jobs||[]).find(x=>x.id===id);if(job?.job_key)draftJobKeys[id]=job.job_key;const draft={job_key:draftJobKeys[id]||null,detectors:detectorDrafts[id],metadata:metadataDrafts[id],ocr:ocrDrafts[id],speed:speedDrafts[id]};storageSet(DRAFT_PREFIX+id,[draft.detectors,draft.metadata,draft.ocr,draft.speed].some(x=>x!==undefined)?draft:null)}
function clearDraft(id){forgetDraftMemory(id);storageSet(DRAFT_PREFIX+id,null)}
function dropForeignDrafts(){(status.jobs||[]).forEach(j=>{if(draftJobKeys[j.id]&&j.job_key&&draftJobKeys[j.id]!==j.job_key)forgetDraftMemory(j.id)})}
loadStoredDrafts();try{window.addEventListener('storage',e=>{const id=draftId(e.key);if(id===null)return;let draft=null;try{draft=e.newValue?JSON.parse(e.newValue):null}catch(_){}applyStoredDraft(id,draft)})}catch(e){}
async function refreshToken(){const r=await fetch('/api/session',{cache:'no-store'});if(!r.ok)throw Error('Không lấy được phiên Control Center');token=(await r.json()).token;return token}
async function json(url,opt={},retry=true){opt.headers={...(opt.headers||{}),'X-BiliFlow-Token':token,'Content-Type':'application/json'};const r=await fetch(url,opt);if(r.status===403&&retry){await refreshToken();return json(url,opt,false)}const v=await r.json();if(!r.ok){const e=Error(v.error||r.statusText);e.status=r.status;e.code=v.code||null;e.body=v;throw e}return v}
const post=(url,body={})=>json(url,{method:'POST',body:JSON.stringify(body)});
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function notify(message,isError=false){const e=document.getElementById('notice');if(!e)return;e.textContent=message;e.className=`notice show${isError?' error':''}`;if(noticeTimer)clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>{e.className='notice'},6000)}
function detectorPicker(j){const opts=status.detector_options||[],selected=new Set(detectorDrafts[j.id]||j.detector_groups||opts.map(x=>x.id)),all=opts.length>0&&opts.every(x=>selected.has(x.id));return `<fieldset class="detectors"><legend>Nhóm cần kiểm tra</legend><label title="Chạy mọi nhóm"><input type="checkbox" id="det-all-${j.id}" ${all?'checked':''} onchange="toggleAllDetectors(${j.id},this.checked)">Tất cả</label>${opts.map(x=>`<label title="${esc(x.description)}"><input type="checkbox" data-detector-job="${j.id}" value="${esc(x.id)}" ${selected.has(x.id)?'checked':''} onchange="captureDetectorDraft(${j.id})">${esc(x.label)}</label>`).join('')}</fieldset>`}
function captureDetectorDraft(id){const boxes=[...document.querySelectorAll(`[data-detector-job="${id}"]`)];detectorDrafts[id]=boxes.filter(x=>x.checked).map(x=>x.value);saveDraft(id);const all=document.getElementById(`det-all-${id}`);if(all)all.checked=boxes.length>0&&boxes.every(x=>x.checked)}
function toggleAllDetectors(id,checked){document.querySelectorAll(`[data-detector-job="${id}"]`).forEach(x=>x.checked=checked);captureDetectorDraft(id)}
function selectedDetectors(id){captureDetectorDraft(id);const values=detectorDrafts[id]||[];if(!values.length){notify('Hãy chọn ít nhất một nhóm cần kiểm tra.',true);return null}return values}
function metadataSelection(j){const saved=metadataDrafts[j.id]||{},styles=['animation','live_action','mixed'],profiles=['careful','fast'];return{content_style:saved.content_style||(styles.includes(j.content_style)&&j.content_style!=='unknown'?j.content_style:'animation'),profile:saved.profile||(profiles.includes(j.profile)?j.profile:'careful')}}
function captureMetadataDraft(id){const style=document.getElementById(`style-${id}`),profile=document.getElementById(`profile-${id}`);if(style&&profile){metadataDrafts[id]={content_style:style.value,profile:profile.value};saveDraft(id)}}
function captureRerunPanelDrafts(){document.querySelectorAll('.rerun-panel[data-job-id]').forEach(panel=>{rerunPanelDrafts[panel.dataset.jobId]=panel.open})}
function captureExportPanelDrafts(){document.querySelectorAll('.export-panel[data-job-id]').forEach(panel=>{const id=panel.dataset.jobId,mode=document.getElementById(`size-${id}`),gb=document.getElementById(`size-gb-${id}`),draft={...(exportPanelDrafts[id]||{}),open:!!panel.open};if(mode)draft.mode=mode.value;if(gb)draft.gb=gb.value;exportPanelDrafts[id]=draft})}
function setExportPanelOpen(id,open){exportPanelDrafts[id]={...(exportPanelDrafts[id]||{}),open};try{const panel=document.querySelector(`.export-panel[data-job-id="${id}"]`);if(panel)panel.open=open}catch(e){}}
function exportSizeChanged(id){captureExportPanelDrafts();const mode=document.getElementById(`size-${id}`),wrap=document.getElementById(`size-gb-wrap-${id}`);if(mode&&wrap)wrap.hidden=mode.value!=='custom'}
function exportBlockReason(j){const s=j.review_summary;if(!s)return 'Chưa đọc được danh sách duyệt của video này.';if(s.status!=='READY_FOR_EDIT_PLAN')return EXPORT_GATE_MESSAGE;if(j.source_present===false)return SOURCE_MISSING_MESSAGE;return ''}
function exportPanel(j){const id=j.id,draft=exportPanelDrafts[id]||{},choice=exportPolicyChoice(j.review_summary?.export_size_policy),mode=EXPORT_SIZE_MODES.includes(draft.mode)?draft.mode:choice.mode,gb=draft.gb!=null&&draft.gb!==''?draft.gb:choice.gb,reason=exportBlockReason(j),busy=exportingJobs.has(id);return `<details class="export-panel" data-job-id="${id}" ${draft.open?'open':''}><summary>Xuất video</summary><div class="export-body"><label>Dung lượng video<select id="size-${id}" onchange="exportSizeChanged(${id})">${exportSizeOptionsHtml(mode)}</select></label><label id="size-gb-wrap-${id}" ${mode==='custom'?'':'hidden'}>Tối đa<input id="size-gb-${id}" __EXPORT_CUSTOM_GB__ value="${esc(gb)}" oninput="exportSizeChanged(${id})">GB</label><button class="green" onclick="exportVideo(${id},this)" ${reason||busy?'disabled':''} title="${esc(reason)}">${busy?'Đang gửi lệnh xuất…':'Hoàn tất duyệt và xuất video'}</button>${reason?`<span class="export-reason">${esc(reason)}</span>`:''}${draft.error&&!busy?`<span class="export-error" role="alert">${esc(draft.error)}</span>`:''}</div></details>`}
function skipReasonText(j){const s=j.review_summary||{};return s.main_items?`Cả ${s.main_items} cảnh chính đều được chọn Giữ nguyên.`:'Video không có cảnh chính nào cần duyệt.'}
function selectedOcrBatch(id){return Number(document.getElementById(`ocr-${id}`)?.value||1)}
function selectedFastScan(id){return document.getElementById(`fast-${id}`)?.checked===true}
function speedPicker(j){const value=speedDrafts[j.id]??j.fast_scan??true;return `<label title="Ghép OCR xuyên nhiều frame, detect chữ FP16, tính routing logo song song trong lúc OCR, model an toàn hoạt hình và model bạo lực phim người đóng FP16. Không giảm mật độ quét; mục review đã kiểm chứng giống chế độ thường trên cả phim (vài track chữ credits hoặc ảnh xem trước có thể lệch nhẹ). Khác profile Nhanh (giảm mật độ quét)."><input type="checkbox" id="fast-${j.id}" ${value?'checked':''} onchange="speedDrafts[${j.id}]=this.checked;saveDraft(${j.id})"> Tăng tốc xử lý</label>`}
function ocrPicker(j){const value=ocrDrafts[j.id]??j.ocr_recognition_batch_size??1;return `<label title="Chỉ áp dụng OCR trong nhóm Quảng cáo/logo; không đổi mật độ quét.">OCR <select id="ocr-${j.id}" onchange="ocrDrafts[${j.id}]=Number(this.value);saveDraft(${j.id})"><option value="1" ${value===1?'selected':''}>Chuẩn</option><option value="8" ${value===8?'selected':''}>Tăng tốc (thử nghiệm)</option></select></label>`}
function controls(j){const id=j.id;let a=[];if(j.state==='NEEDS_METADATA'||j.state==='DISCOVERED'){const metadata=metadataSelection(j);a.push(`${detectorPicker(j)}${ocrPicker(j)}${speedPicker(j)}<select id="style-${id}" onchange="captureMetadataDraft(${id})"><option value="animation" ${metadata.content_style==='animation'?'selected':''}>Hoạt hình</option><option value="live_action" ${metadata.content_style==='live_action'?'selected':''}>Phim thực tế</option><option value="mixed" ${metadata.content_style==='mixed'?'selected':''}>Hỗn hợp</option></select><select id="profile-${id}" onchange="captureMetadataDraft(${id})"><option value="careful" ${metadata.profile==='careful'?'selected':''}>Tỉ mỉ</option><option value="fast" ${metadata.profile==='fast'?'selected':''}>Nhanh</option></select><button class="green" onclick="start(${id},this)" ${startingJobs.has(id)?'disabled':''}>${startingJobs.has(id)?'Đang bắt đầu…':'Bắt đầu'}</button>`)}if(['PAUSED','FAILED','INTERRUPTED_RECOVERABLE'].includes(j.state))a.push(`<button class="green" onclick="act(${id},'resume')">Tiếp tục</button>`);if(['QUEUED','PREFLIGHT','SCANNING_SAFETY','SCANNING_TEXT','SCANNING_LOGO','LOCALIZING_REGIONS','BUILDING_REVIEW','RENDERING'].includes(j.state)){a.push(`<button class="warn" onclick="act(${id},'stop-after-stage')">Dừng sau bước</button><button class="warn" onclick="act(${id},'pause')">Dừng ngay</button>`)}if(j.active_queue_path){a.push(`<button onclick="location.href='/review/${id}'">Duyệt cảnh</button>${j.ai_audit?.state==='RUNNING'?'<button disabled>Visual AI đang kiểm tra…</button>':aiState.ready?`<button class="green" onclick="audit(${id},true)">Visual AI Audit</button>`:`<button disabled title="${esc(aiState.message||'AI Supervisor chưa sẵn sàng')}">AI chưa sẵn sàng</button>`}`)}else{a.push('<button disabled title="Video cần quét xong và có review queue trước">AI: chờ queue</button>')}if(j.state==='FAILED')a.push(`<button onclick="act(${id},'retry')">Thử lại bước lỗi</button>`);if(j.state==='READY_TO_EXPORT'&&j.review_summary?.skip_eligible)a.push(`<button class="warn" onclick="skipJob(${id},this)" ${skippingJobs.has(id)?'disabled':''} title="${esc(skipReasonText(j))} Đánh dấu xong mà không tạo bản xuất; report và video gốc được giữ nguyên.">Bỏ qua (không xuất)</button>`);if(j.state==='SKIPPED')a.push(isSourceCleaned(j)||isArchived(j)?`<button class="green" disabled title="${esc(isArchived(j)?SOURCE_ARCHIVED_MESSAGE:SOURCE_CLEANED_MESSAGE)}">Mở lại để xuất</button>`:`<button class="green" onclick="unskipJob(${id},this)" ${unskippingJobs.has(id)?'disabled':''}>Mở lại để xuất</button>`);if(!['COMPLETED','SKIPPED','CANCELLED'].includes(j.state))a.push(`<button class="danger" onclick="cancelJob(${id},this)" ${cancellingJobs.has(id)?'disabled':''}>${cancellingJobs.has(id)?'Đang hủy…':'Hủy'}</button>`);if(j.state==='READY_TO_EXPORT')a.push(exportPanel(j));if(cleanupEligible(j)||archiveEligible(j))a.push(pickControl(j));if(cleanupReady(j))a.push(cleanupControls(j));if(archiveEligible(j))a.push(archiveControls(j));if(isArchived(j))a.push(archivedControls(j));if(cleanupRecheckable(j))a.push(recheckButton('source_cleanup',j.source_cleanup.id,id));if(j.state==='CANCELLED')a.push(hideButton(j));if(j.delete)a.push(deleteControls(j));if(j.protected&&(cleanupReady(j)||j.delete))a.push(protectedNote(j));if(['WAITING_REVIEW','READY_TO_EXPORT','COMPLETED','SKIPPED','CANCELLED','FAILED','PAUSED','INTERRUPTED_RECOVERABLE'].includes(j.state))a.push(isArchived(j)?lockedRerun(j):isSourceCleaned(j)?cleanedRerun(j):`<details class="rerun-panel" data-job-id="${id}" ${rerunPanelDrafts[id]?'open':''}><summary>Chạy lại kiểm tra</summary><div class="rerun-body">${detectorPicker(j)}${ocrPicker(j)}${speedPicker(j)}<button class="warn" onclick="rerun(${id})">Chạy lại với phạm vi đã chọn</button></div></details>`);return a.join('')}
function auditText(j){const a=j.ai_audit;if(!a)return 'Visual AI: chưa chạy';if(a.state==='COMPLETED')return `Visual AI: ${a.result||'DONE'} — ${a.summary||''}`;return `Visual AI: ${a.message||a.state}`}
function structureText(j){const a=j.structure_audit;if(!a)return 'Kiểm tra cấu trúc: chờ tạo queue';return `Kiểm tra cấu trúc cục bộ: ${a.result||'DONE'} — ${a.summary||''} · không dùng quota`}
const JOB_TABS=[['waiting','Đang chờ xử lý'],['scan_queue','Đang chờ chạy cảnh để duyệt'],['scanning','Đang chạy cảnh'],['review','Đang chờ duyệt'],['export','Đang chạy xuất video'],['completed','Hoàn tất']];
const DEFAULT_TAB_ORDER=['scanning','export','scan_queue','review','waiting','completed'];
const SCAN_STATES=new Set(['PREFLIGHT','SCANNING_SAFETY','SCANNING_TEXT','SCANNING_LOGO','LOCALIZING_REGIONS','BUILDING_REVIEW','AI_AUDITING']);
function jobTab(j){const s=String(j&&j.state||'');if(s==='COMPLETED'||s==='SKIPPED')return 'completed';if(s==='RENDERING'||s==='VERIFYING')return 'export';if(s==='QUEUED'){const kind=queueKind(j);return kind==='export'?'export':kind==='scan'?'scan_queue':'waiting'}if(s==='WAITING_REVIEW'||s==='READY_TO_EXPORT')return 'review';if(SCAN_STATES.has(s)||s.startsWith('SCANNING_'))return 'scanning';return 'waiting'}
function defaultJobTab(groups){return DEFAULT_TAB_ORDER.find(key=>groups[key]&&groups[key].length)||'waiting'}
function syncHeaderHeight(){try{const h=document.querySelector('header'),r=document.documentElement;if(h&&r&&r.style)r.style.setProperty('--header-h',`${h.offsetHeight}px`)}catch(e){}}
function watchHeaderHeight(){syncHeaderHeight();try{window.addEventListener('resize',syncHeaderHeight);const h=document.querySelector('header');if(h&&window.ResizeObserver)new ResizeObserver(syncHeaderHeight).observe(h)}catch(e){}}
function scrollToJobList(){try{const bar=document.getElementById('job-tabs'),list=document.getElementById('jobs'),h=document.querySelector('header');if(!bar||!list||typeof list.getBoundingClientRect!=='function'||typeof window.scrollTo!=='function')return;const now=window.scrollY||window.pageYOffset||0,target=Math.max(0,Math.round(now+list.getBoundingClientRect().top-(h?h.offsetHeight:0)-(bar.offsetHeight||0)));if(Math.abs(target-now)>1)window.scrollTo({top:target,behavior:'auto'})}catch(e){}}
function revealActiveTab(){try{const bar=document.getElementById('job-tabs'),tab=bar&&typeof bar.querySelector==='function'?bar.querySelector('.job-tab.active'):null;if(!tab)return;const l=tab.offsetLeft,r=l+tab.offsetWidth;if(l<bar.scrollLeft)bar.scrollLeft=l;else if(r>bar.scrollLeft+bar.clientWidth)bar.scrollLeft=r-bar.clientWidth}catch(e){}}
function selectJobTab(tab){activeJobTab=tab;renderJobs(true);scrollToJobList();revealActiveTab()}
function needsSetup(j){return j.state==='NEEDS_METADATA'||j.state==='DISCOVERED'}
function markJobsInteraction(){lastJobsInteraction=Date.now()}
function jobsInteracting(){const box=document.getElementById('jobs'),active=document.activeElement,since=Date.now()-lastJobsInteraction;if(jobsPointerDown||since<400)return true;return !!(box&&active&&(active.tagName==='SELECT'||(active.tagName==='INPUT'&&active.type!=='checkbox'))&&box.contains(active)&&since<15000)}
function deferJobsRender(){if(jobsRenderTimer)return;jobsRenderTimer=setTimeout(()=>{jobsRenderTimer=null;renderJobs()},450)}
function watchJobsInteraction(){const box=document.getElementById('jobs');if(!box)return;box.addEventListener('pointerdown',()=>{jobsPointerDown=true;markJobsInteraction()});['focusin','change','input','keydown'].forEach(name=>box.addEventListener(name,markJobsInteraction));const release=()=>{if(jobsPointerDown){jobsPointerDown=false;markJobsInteraction()}};document.addEventListener('pointerup',release,true);document.addEventListener('pointercancel',release,true)}
function queueKind(j){return j.state==='QUEUED'&&(j.queue_kind==='scan'||j.queue_kind==='export')?j.queue_kind:null}
function queueLine(j){const n=status.queue?.length||0;if(!j.queue_position)return '';return n>1?`Thứ tự chờ: #${j.queue_position} trong ${n} việc`:`Thứ tự chờ: #${j.queue_position}`}
function queuePausedNote(){return status.queue?.paused||status.scheduler_paused?'Hàng đợi đang tạm dừng — bấm “Chạy hàng đợi” để tiếp tục (vẫn giữ thứ tự).':''}
function statePresentation(j){if(j.state==='RENDERING'&&j.render_progress?.state==='VERIFYING')return{label:'Đang kiểm tra output',tone:'running'};if(j.state==='QUEUED'&&queueKind(j)==='export')return{label:'Chờ xuất video',tone:'waiting'};if(j.state==='QUEUED'&&queueKind(j)==='scan')return{label:'Chờ chạy cảnh',tone:'waiting'};const values={DISCOVERED:['Chưa thiết lập','waiting'],NEEDS_METADATA:['Chờ thiết lập','waiting'],QUEUED:['Đang xếp hàng','waiting'],PREFLIGHT:['Đang chuẩn bị','running'],SCANNING_SAFETY:['Đang quét an toàn','running'],SCANNING_TEXT:['Đang quét chữ','running'],SCANNING_LOGO:['Đang quét logo','running'],LOCALIZING_REGIONS:['Đang khoanh vùng','running'],BUILDING_REVIEW:['Đang tạo danh sách duyệt','running'],WAITING_REVIEW:['Chờ duyệt cảnh','waiting'],READY_TO_EXPORT:['Sẵn sàng xuất','waiting'],RENDERING:['Đang xuất video','running'],VERIFYING:['Đang kiểm tra output','running'],PAUSED:['Đã tạm dừng','waiting'],FAILED:['Có lỗi cần xử lý','error'],INTERRUPTED_RECOVERABLE:['Có thể tiếp tục','waiting'],CANCELLED:['Đã hủy','waiting'],COMPLETED:['Đã hoàn tất','complete'],SKIPPED:['Đã bỏ qua — không xuất','complete']};const value=values[j.state]||[j.state||'Chưa rõ','waiting'];return{label:value[0],tone:value[1]}}
function scanStatus(j){const percent=Math.max(0,Math.min(100,Math.round(100*(Number(j.progress)||0))));if(jobTab(j)==='scanning')return{value:`${percent}% · đang chạy`,detail:`Bước hiện tại: ${j.current_stage||'đang chuẩn bị'}`,tone:'running'};if(percent>=100)return{value:'100% · đã quét',detail:'Kết quả phân tích đã được lưu.',tone:'complete'};if(j.state==='QUEUED')return{value:`${percent}% · chờ lượt`,detail:queuePausedNote()||(j.queue_position?`${queueLine(j)}; quét cảnh và xuất video chạy lần lượt theo thứ tự bấm.`:'Video đang nằm trong hàng đợi.'),tone:'waiting'};return{value:`${percent}% · chưa hoàn tất`,detail:'Tiến độ quét và tạo cảnh duyệt.',tone:'waiting'}}
function structureStatus(j){const a=j.structure_audit;if(!a)return{value:'Chưa kiểm tra',detail:'Sẽ có sau khi tạo danh sách duyệt.',tone:'waiting'};const result=String(a.result||'DONE').toUpperCase();return{value:result==='PASS'?'Đạt':a.outdated_rule?`${result} (quy tắc cũ)`:result,detail:(result!=='PASS'&&a.first_finding)||a.summary||'Kiểm tra cấu trúc cục bộ đã hoàn tất.',tone:result==='PASS'?'complete':result==='BLOCK'?'error':'waiting'}}
function visualStatus(j){const a=j.ai_audit;if(!a)return{value:'Chưa chạy',detail:'Visual AI Audit là bước kiểm tra tùy chọn.',tone:'waiting'};if(a.state==='RUNNING'||a.state==='QUEUED')return{value:a.state==='RUNNING'?'Đang kiểm tra':'Đang chờ',detail:a.message||'AI Supervisor đang xử lý.',tone:'running'};if(a.state==='FAILED')return{value:'Lỗi kiểm tra',detail:a.message||'Có thể chạy lại Visual AI Audit.',tone:'error'};const result=String(a.result||'DONE').toUpperCase();return{value:`Đã kiểm tra · ${result}`,detail:a.summary||'Visual AI Audit đã hoàn tất.',tone:result==='BLOCK'?'error':result==='WARN'?'waiting':'complete'}}
function shortDuration(seconds){const value=Number(seconds);if(!Number.isFinite(value)||value<0)return 'đang tính';const rounded=Math.ceil(value/60);if(rounded<60)return `khoảng ${Math.max(1,rounded)} phút`;const hours=Math.floor(rounded/60),minutes=rounded%60;return `khoảng ${hours} giờ${minutes?` ${minutes} phút`:''}`}
function formatStamp(value){if(!value)return '';const date=new Date(value);if(Number.isNaN(date.getTime()))return '';return new Intl.DateTimeFormat('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit',day:'2-digit',month:'2-digit',year:'numeric'}).format(date)}
function completedAt(value){const stamp=formatStamp(value);return stamp?`Hoàn thành lúc ${stamp}`:'Đã hoàn tất và qua bước kiểm tra cuối.'}
function exportStatus(j){if(j.state==='COMPLETED')return{value:'100% · Đã xuất video',detail:completedAt(j.updated_at),tone:'complete',percent:100};if(j.state==='SKIPPED'){const stamp=formatStamp(j.skip?.skipped_at||j.updated_at);return{value:'Không xuất video',detail:isSourceCleaned(j)?`Đã bỏ qua${stamp?` lúc ${stamp}`:''}; report và quyết định duyệt được giữ nguyên; video gốc đã được dọn vào Thùng rác.`:`Đã bỏ qua${stamp?` lúc ${stamp}`:''}; video gốc, report và quyết định duyệt được giữ nguyên.`,tone:'complete'}}if(j.state==='RENDERING'){const p=j.render_progress||{};if(p.state==='VERIFYING')return{value:'100% · đang kiểm tra',detail:'Đã render xong; đang xác nhận hình, tiếng và thời lượng.',tone:'running',percent:100};const percent=Math.max(0,Math.min(99.9,Number(p.percent)||0)),speed=p.speed_text?`Tốc độ ${p.speed_text}`:'Đang khởi tạo FFmpeg',eta=p.eta_seconds!=null?` · còn ${shortDuration(p.eta_seconds)}`:'';return{value:`${percent.toFixed(1)}% · đang xuất`,detail:`${speed}${eta}`,tone:'running',percent}}if(j.state==='VERIFYING')return{value:'100% · đang kiểm tra',detail:'Đang xác nhận hình, tiếng và thời lượng.',tone:'running',percent:100};if(queueKind(j)==='export')return{value:'Chờ xuất video',detail:queuePausedNote()||(j.queue_position?`${queueLine(j)}; xuất video và quét cảnh chạy lần lượt theo thứ tự bấm.`:'Lệnh xuất đã nằm trong hàng đợi.'),tone:'waiting',percent:0};if(j.current_stage==='render'&&['PAUSED','INTERRUPTED_RECOVERABLE'].includes(j.state))return{value:'Xuất video tạm dừng',detail:'Bấm “Tiếp tục” để xuất tiếp; lệnh xuất giữ vị trí cũ trong hàng đợi.',tone:'waiting'};if(j.current_stage==='render'&&j.state==='CANCELLED')return{value:'Đã hủy xuất video',detail:'Mở “Duyệt cảnh” rồi bấm “Hoàn tất duyệt và xuất video” để xếp lệnh xuất mới.',tone:'waiting'};if(j.current_stage==='render'&&j.state==='FAILED')return{value:'Xuất video bị lỗi',detail:'Xem lỗi bên dưới rồi chọn thử lại.',tone:'error'};if(j.state==='READY_TO_EXPORT')return j.source_present===false?{value:'Thiếu video gốc',detail:SOURCE_MISSING_MESSAGE,tone:'error'}:{value:'Sẵn sàng xuất',detail:'Duyệt xong; bấm “Xuất video” trên thẻ hoặc trong trang duyệt cảnh.',tone:'waiting'};if(j.state==='WAITING_REVIEW')return{value:'Chưa xuất',detail:'Cần hoàn tất duyệt cảnh trước.',tone:'waiting'};return{value:'Chưa tới bước xuất',detail:'Output chỉ được tạo sau khi duyệt.',tone:'waiting'}}
function statusBox(title,value){const progress=Number.isFinite(Number(value.percent))?`<div class="mini-progress" aria-label="${esc(title)} ${Math.round(Number(value.percent))}%"><i style="width:${Math.max(0,Math.min(100,Number(value.percent)))}%"></i></div>`:'';return `<div class="status-box tone-${value.tone}"><div class="status-kicker">${esc(title)}</div><div class="status-value">${esc(value.value)}</div><div class="status-detail">${esc(value.detail)}</div>${progress}</div>`}
function videoName(j){const parts=String(j.source_path||j.job_key||'Video').split(/[\\\\/]/);return parts[parts.length-1]||j.job_key||'Video'}
const CLEANUP_LIMIT=50;const CLEANUP_RUNNING_TITLE='Đang xóa, lưu trữ hoặc khôi phục video gốc; chờ lượt hiện tại xong.';const CLEANUP_BUTTON_TITLE='Xóa vĩnh viễn video gốc trong input, manifest của bản xuất và dữ liệu của video trong BiliFlow; file .mp4 trong output được giữ.';const CLEANUP_ACK_TEXT='Tôi hiểu: video gốc và dữ liệu của các video này bị xóa vĩnh viễn, không khôi phục được; các video này không duyệt hay xuất lại được nữa.';
const DELETE_KINDS={CANCELLED:['Đã hủy','Video gốc + dữ liệu BiliFlow','Xóa vĩnh viễn video gốc trong input và xóa video khỏi BiliFlow (quyết định duyệt, báo cáo, log); không đụng tới output.'],LOST:['Mất video gốc','Dữ liệu BiliFlow','Video gốc đã không còn: chỉ xóa video khỏi BiliFlow (quyết định duyệt, báo cáo, log); không đụng tới output.']};const DELETE_ACK_CANCELLED='Tôi hiểu: video gốc của video đã hủy bị xóa vĩnh viễn (không qua Thùng rác, không khôi phục được) và các video này bị xóa khỏi BiliFlow.';const DELETE_ACK_LOST='Tôi hiểu: các video này bị xóa khỏi BiliFlow vĩnh viễn, cùng quyết định duyệt, báo cáo và log của chúng; không khôi phục được.';
function formatBytes(n){const v=Number(n);if(n==null||n===''||!Number.isFinite(v)||v<0)return '';if(v<1073741824)return `${Math.round(v/1048576).toLocaleString('vi-VN')} MB`;return `${(v/1073741824).toLocaleString('vi-VN',{minimumFractionDigits:1,maximumFractionDigits:1})} GB`}
function isSourceCleaned(j){return !!j&&(j.source_cleaned===true||['PENDING','RECYCLED'].includes(j.source_cleanup?.state))}
function cleanupReady(j){return !!j&&['COMPLETED','SKIPPED'].includes(j.state)&&j.cleanup?.eligible===true&&!isSourceCleaned(j)&&!isArchived(j)}
function cleanupEligible(j){return cleanupReady(j)&&!j.protected}
function cleanupSize(j){const v=Number(j.cleanup?.size_bytes??j.source_size_bytes);return Number.isFinite(v)&&v>0?v:0}
function isArchived(j){return !!j&&(j.source_archived===true||['PENDING','ARCHIVED','RESTORING'].includes(j.source_archive?.state))}
function archiveEligible(j){return !!j&&['COMPLETED','SKIPPED'].includes(j.state)&&j.archive?.eligible===true&&!isArchived(j)&&!isSourceCleaned(j)}
function archiveSize(j){const v=Number(j.archive?.size_bytes??j.source_size_bytes);return Number.isFinite(v)&&v>0?v:0}
function archiveBadge(j){if(!isArchived(j))return '';const s=j.source_archive?.state;return `<span class="archive-badge" title="Video gốc nằm trong kho lưu trữ (thư mục archive); bấm “Khôi phục bản xuất” để đưa về input.">${s==='PENDING'?'Đang lưu trữ':s==='RESTORING'?'Đang khôi phục':'Đã lưu trữ'}</span>`}
function archiveRecheckable(j){const a=j&&j.source_archive;return !!a&&a.state==='ARCHIVED'&&a.kind==='EXPORTED'&&a.export_recycled===true&&a.export_verified===false&&Number.isInteger(a.id)}
function archiveRecheckNote(a){if(!a)return '';if(a.export_verified_later_at){const s=formatStamp(a.export_verified_later_at);return ` · Đã thấy bản xuất trong Thùng rác khi kiểm tra lại${s?` lúc ${s}`:''}.`}if(a.export_rechecked_at&&a.export_verified===false){const s=formatStamp(a.export_rechecked_at);return ` Lần kiểm tra lại gần nhất${s?` (${s})`:''} vẫn chưa thấy.`}return ''}
function archiveLineInfo(j){const a=j.source_archive;if(!a)return null;if(a.state==='ARCHIVED'){const stamp=formatStamp(a.archived_at),exported=a.kind==='EXPORTED',unverified=exported&&a.export_verified===false;return[`Đã lưu trữ · video gốc ${a.file_name||videoName(j)} (${formatBytes(a.size_bytes)}) trong kho lưu trữ${exported?` · bản xuất ${a.output_name||''} đã vào Thùng rác`:''}${stamp?` lúc ${stamp}`:''}${unverified?' · Windows chưa xác nhận bản ghi của bản xuất trong Thùng rác; hãy kiểm tra Thùng rác.':''}${archiveRecheckNote(a)}${a.warning?` · ${a.warning}`:''}`,unverified||a.warning?'waiting':'complete']}if(a.state==='PENDING')return['Đang lưu trữ video gốc…','running'];if(a.state==='RESTORING')return['Đang đưa video gốc từ kho lưu trữ về input…','running'];if(a.state==='RESTORED'&&j.source_present!==false){const stamp=formatStamp(a.restored_at);return[`Đã đưa video gốc từ kho lưu trữ về input (SHA-256 khớp)${stamp?` lúc ${stamp}`:''}`,'complete']}if(a.state==='FAILED'&&j.source_present!==false)return[`Lần lưu trữ trước không thành công: ${a.error||''}`,'error'];return null}
function sourceLineInfo(j){const c=j.source_cleanup,a=j.source_archive;if(a&&(isArchived(j)||!c||String(a.created_at||'')>String(c.created_at||''))){const line=archiveLineInfo(j);if(line)return line}if(c?.state==='RECYCLED'){const stamp=formatStamp(c.finished_at);return[`Đã dọn video gốc · ${formatBytes(c.size_bytes)}${stamp?` · lúc ${stamp}`:''} (đang ở Thùng rác)${c.verified===false?' · Windows chưa xác nhận bản ghi trong Thùng rác; hãy kiểm tra Thùng rác.':''}${recheckNote(c)}`,c.verified===false?'waiting':'complete']}if(c?.state==='PENDING')return['Đang dọn video gốc…','running'];if(c?.state==='RESTORED'){const stamp=formatStamp(c.restored_at);return[`Đã khôi phục video gốc (SHA-256 khớp)${stamp?` lúc ${stamp}`:''}`,'complete']}if(c?.state==='FAILED'&&j.source_present!==false)return[`Lần dọn trước không thành công: ${c.error||''}`,'error'];if(j.source_present===false)return['Không còn video gốc trong input','error'];if(['COMPLETED','SKIPPED'].includes(j.state)&&j.cleanup&&!j.cleanup.eligible&&j.cleanup.reason)return[`Chưa dọn được: ${j.cleanup.reason}`,'waiting'];if(['COMPLETED','SKIPPED'].includes(j.state)&&j.archive&&!j.archive.eligible&&j.archive.reason&&!isArchived(j))return[`Chưa lưu trữ được: ${j.archive.reason}`,'waiting'];return null}
function sourceLine(j){const line=sourceLineInfo(j);return line?`<div class="source-line tone-${line[1]}">${esc(line[0])}</div>`:''}
function recheckNote(c){if(!c)return '';if(c.verified_later_at){const s=formatStamp(c.verified_later_at);return ` · Đã thấy trong Thùng rác khi kiểm tra lại${s?` lúc ${s}`:''}.`}if(c.rechecked_at&&c.verified===false){const s=formatStamp(c.rechecked_at);return ` Lần kiểm tra lại gần nhất${s?` (${s})`:''} vẫn chưa thấy.`}return ''}
function cleanupRecheckable(j){const c=j&&j.source_cleanup;return !!c&&c.state==='RECYCLED'&&c.verified===false&&Number.isInteger(c.id)}
function recheckButton(kind,rowId,jobId){const busy=recheckingRows.has(`${kind}:${rowId}`);return `<button onclick="recheckBin('${kind}',${rowId},${jobId},this)" ${busy?'disabled':''} title="Chỉ đọc Thùng rác của Windows để tìm bản ghi; không chuyển hay xóa file nào.">${busy?'Đang kiểm tra…':'Kiểm tra lại Thùng rác'}</button>`}
function isHidden(j){return !!j&&j.state==='CANCELLED'&&!!j.hidden_at}
function hideButton(j){const busy=hidingJobs.has(j.id);return `<button onclick="hideJob(${j.id},this)" ${busy?'disabled':''} title="Chỉ ẩn khỏi danh sách; không xóa report, quyết định duyệt hay video gốc.">${busy?'Đang ẩn…':'Ẩn khỏi danh sách'}</button>`}
function hiddenRow(j){const id=j.id,busy=hidingJobs.has(id),stamp=formatStamp(j.hidden_at);return `<div class="hidden-row" data-job-id="${id}"><span class="hidden-name">#${id} · ${esc(videoName(j))}</span><span class="hidden-when">${esc(`Đã hủy${stamp?` · ẩn lúc ${stamp}`:''}`)}</span><button onclick="unhideJob(${id},this)" ${busy?'disabled':''}>${busy?'Đang hiện lại…':'Hiện lại'}</button></div>`}
function foldSection(key,label,items,body){if(!items.length)return '';return `<details class="phase-group phase-fold" data-fold="${key}" ${foldOpen[key]?'open':''} ontoggle="foldOpen['${key}']=this.open"><summary class="phase-heading">${esc(label)} <span>${items.length}</span></summary>${body}</details>`}
function captureFoldState(){document.querySelectorAll('.phase-fold[data-fold]').forEach(x=>{const key=x.dataset&&x.dataset.fold;if(key&&Object.prototype.hasOwnProperty.call(foldOpen,key))foldOpen[key]=!!x.open})}
function pickControl(j){const id=j.id;return `<label class="cleanup-pick"><input type="checkbox" data-cleanup-job="${id}" ${cleanupSelection.has(id)?'checked':''} onchange="toggleCleanup(${id},this.checked)"> Chọn</label>`}
function cleanupControls(j){const id=j.id,running=!!status.source_cleanup_running,why=j.protected||(running?CLEANUP_RUNNING_TITLE:'');return `<button class="danger" onclick="openCleanup([${id}],this)" ${why?`disabled title="${esc(why)}"`:`title="${esc(CLEANUP_BUTTON_TITLE)}"`}>Xóa video gốc</button>`}
function deleteControls(j){const d=j.delete||{},id=j.id,running=!!status.source_cleanup_running,why=j.protected||(d.eligible!==true?d.reason||'Không xóa được video này.':running?CLEANUP_RUNNING_TITLE:'');return `<button class="danger" onclick="openDelete([${id}],this)" ${why?`disabled title="${esc(why)}"`:`title="${esc((DELETE_KINDS[d.kind]||[])[2]||'')}"`}>Xóa video</button>`}
function protectedNote(j){return `<span class="cleanup-note protected-note">${esc(j.protected)}</span>`}
function archiveControls(j){const id=j.id,running=!!status.source_cleanup_running;return `<button onclick="openArchive([${id}],this)" ${running?`disabled title="${esc(ARCHIVE_RUNNING_TITLE)}"`:'title="Chuyển video gốc vào kho lưu trữ (thư mục archive) và bản xuất vào Thùng rác; bấm “Khôi phục bản xuất” để xuất lại."'}>Lưu trữ</button>`}
function archivedControls(j){const a=j.source_archive||{},id=j.id;if(a.state==='PENDING')return '<button disabled>Đang lưu trữ…</button>';if(a.state==='RESTORING')return '<button disabled>Đang khôi phục…</button>';const busy=restoringJobs.has(id),running=!!status.source_cleanup_running;return `<button class="green" onclick="restoreArchive(${id},this)" ${busy||running?'disabled':''} title="${esc(running&&!busy?ARCHIVE_RUNNING_TITLE:'Đưa video gốc từ kho lưu trữ về input (kiểm tra SHA-256).')}">${busy?'Đang khôi phục…':'Khôi phục bản xuất'}</button>${archiveRecheckable(j)?recheckButton('archive_export',a.id,id):''}`}
function lockedRerun(j){return `<button disabled title="${esc(SOURCE_ARCHIVED_MESSAGE)}">Chạy lại kiểm tra</button><span class="cleanup-note">Video gốc đang ở kho lưu trữ; bấm “Khôi phục bản xuất” để đưa về input trước khi chạy lại.</span>`}
function cleanedRerun(j){return `<button disabled title="${esc(SOURCE_CLEANED_MESSAGE)}">Chạy lại kiểm tra</button><span class="cleanup-note">Chép lại video gốc vào input để chạy lại (đúng tên: ${esc(j.source_cleanup?.file_name||videoName(j))})</span>`}
function cleanupGroup(){return (status.jobs||[]).filter(j=>jobTab(j)==='completed')}
function cleanupCounts(items){const eligible=items.filter(cleanupEligible),chosen=eligible.filter(j=>cleanupSelection.has(j.id));return{eligible,chosen,bytes:chosen.reduce((sum,j)=>sum+cleanupSize(j),0)}}
function archiveCounts(items){const eligible=items.filter(archiveEligible),chosen=eligible.filter(j=>cleanupSelection.has(j.id));return{eligible,chosen,bytes:chosen.reduce((sum,j)=>sum+archiveSize(j),0)}}
function archiveSummaryText(c){return `Lưu trữ: ${c.eligible.length} video lưu trữ được · đã chọn ${c.chosen.length} (${formatBytes(c.bytes)}). Video gốc vào kho lưu trữ (thư mục archive), bản xuất vào Thùng rác.`}
function cleanupSummaryText(c){return `Xóa video gốc: ${c.eligible.length} video xóa được · đã chọn ${c.chosen.length} (${formatBytes(c.bytes)})`}
function cleanupToolbar(items){const c=cleanupCounts(items),a=archiveCounts(items),running=!!status.source_cleanup_running;return `<div class="cleanup-toolbar" role="group" aria-label="Xóa video gốc"><span class="cleanup-summary" id="cleanup-summary">${esc(cleanupSummaryText(c))}</span><button id="cleanup-all" onclick="selectAllCleanup()" ${c.eligible.length?'':'disabled'}>Chọn tất cả video xóa được</button><button id="cleanup-none" onclick="clearCleanupSelection()" ${c.chosen.length?'':'disabled'}>Bỏ chọn</button><button id="cleanup-run" class="danger" onclick="openSelectedCleanup(this)" ${!c.chosen.length||running?'disabled':''} title="${running?esc(CLEANUP_RUNNING_TITLE):''}">Xóa video gốc đã chọn (${c.chosen.length})</button><span class="cleanup-note">Xóa vĩnh viễn, không qua Thùng rác; file .mp4 trong output được giữ.</span><button id="archive-run" onclick="openSelectedArchive(this)" ${!a.chosen.length||running?'disabled':''} title="${running?esc(ARCHIVE_RUNNING_TITLE):''}">Lưu trữ đã chọn (${a.chosen.length})</button><span class="archive-note" id="archive-summary">${esc(archiveSummaryText(a))}</span></div>`}
function updateCleanupToolbar(){const c=cleanupCounts(cleanupGroup()),running=!!status.source_cleanup_running,summary=document.getElementById('cleanup-summary'),run=document.getElementById('cleanup-run'),none=document.getElementById('cleanup-none');if(summary)summary.textContent=cleanupSummaryText(c);if(run){run.textContent=`Xóa video gốc đã chọn (${c.chosen.length})`;run.disabled=!c.chosen.length||running}if(none)none.disabled=!c.chosen.length;const a=archiveCounts(cleanupGroup()),archiveRun=document.getElementById('archive-run'),archiveSummary=document.getElementById('archive-summary');if(archiveSummary)archiveSummary.textContent=archiveSummaryText(a);if(archiveRun){archiveRun.textContent=`Lưu trữ đã chọn (${a.chosen.length})`;archiveRun.disabled=!a.chosen.length||running}}
function pruneCleanupSelection(jobs){cleanupSelection.forEach(id=>{const j=jobs.find(x=>x.id===id);if(!cleanupEligible(j)&&!archiveEligible(j))cleanupSelection.delete(id)})}
function toggleCleanup(id,checked){if(checked)cleanupSelection.add(id);else cleanupSelection.delete(id);updateCleanupToolbar()}
function selectAllCleanup(){const eligible=cleanupGroup().filter(cleanupEligible).sort(byId);cleanupSelection.clear();eligible.slice(0,CLEANUP_LIMIT).forEach(j=>cleanupSelection.add(j.id));if(eligible.length>CLEANUP_LIMIT)notify('Mỗi lần xóa tối đa 50 video; đã chọn 50 video đầu tiên.');renderJobs(true)}
function clearCleanupSelection(){cleanupSelection.clear();renderJobs(true)}
function lostJobs(){return (status.jobs||[]).filter(j=>j.delete&&j.delete.kind==='LOST'&&j.delete.eligible===true&&!j.protected).sort(byId)}
function lostNotice(){const lost=lostJobs(),running=!!status.source_cleanup_running;if(!lost.length)return '';return `<div class="lost-notice" role="group" aria-label="Video mất gốc"><span class="lost-summary" id="lost-summary">${esc(`Có ${lost.length} video không còn video gốc`)}</span><button id="lost-run" class="danger" onclick="openLostCleanup(this)" ${running?`disabled title="${esc(CLEANUP_RUNNING_TITLE)}"`:''}>Dọn video mất gốc</button><span class="cleanup-note">${esc(`Chỉ xóa dữ liệu của các video này trong BiliFlow (quyết định duyệt, báo cáo, log); thư mục output giữ nguyên.${lost.length>CLEANUP_LIMIT?` Mỗi lần dọn tối đa ${CLEANUP_LIMIT} video.`:''}`)}</span></div>`}
function openLostCleanup(button){const ids=lostJobs().map(j=>j.id);return openDelete(ids.slice(0,CLEANUP_LIMIT),button,ids.length>CLEANUP_LIMIT?`Có ${ids.length} video mất gốc; mỗi lần dọn tối đa ${CLEANUP_LIMIT} video nên danh sách này chỉ có ${CLEANUP_LIMIT} video đầu tiên. Xóa xong, bấm “Dọn video mất gốc” lần nữa để dọn tiếp.`:'')}
function jobCard(j,labels){const stateInfo=statePresentation(j),bucket=jobTab(j),percent=Math.max(0,Math.min(100,Math.round(100*(Number(j.progress)||0)))),groups=(j.detector_groups||[]).map(x=>labels[x]||x).join(', ')||'Chưa chọn';return `<article class="job" data-bucket="${bucket}"><div class="job-head"><div class="job-identity"><div class="job-title">#${j.id} · ${esc(videoName(j))}</div><div class="job-key">${esc(j.job_key)}</div><div class="job-path">${esc(j.source_path)}</div></div><div class="job-badges"><span class="state-badge tone-${stateInfo.tone}">${esc(stateInfo.label)}</span>${archiveBadge(j)}${j.queue_position?`<span class="queue-badge" title="Quét cảnh và xuất video dùng chung một hàng đợi, chạy lần lượt theo thứ tự bấm.">Thứ tự chờ: #${j.queue_position}</span>`:''}</div></div><div class="job-body"><div class="progress-row"><div class="progress-label">Tiến trình phân tích</div><div class="progress" aria-label="Tiến trình ${percent}%"><i style="width:${percent}%"></i></div><div class="progress-value">${percent}%</div></div><div class="status-grid">${statusBox('Phân tích cảnh',scanStatus(j))}${statusBox('Cấu trúc cục bộ',structureStatus(j))}${statusBox('Visual AI Audit',visualStatus(j))}${statusBox('Xuất video',exportStatus(j))}</div>${j.error?`<div class="job-error">${esc(j.error)}</div>`:''}</div><div class="job-footer"><div class="scope-line"><strong>Phạm vi kiểm tra:</strong> ${esc(groups)} · <strong>Chế độ:</strong> ${esc(j.profile)} · ${esc(j.content_style)} · OCR: ${j.ocr_recognition_batch_size===8?'Tăng tốc (thử nghiệm)':'Chuẩn'}${j.fast_scan?' · Tăng tốc xử lý':''}</div>${sourceLine(j)}<div class="actions">${controls(j)}</div></div></article>`}
function phaseSection(label,items,labels){if(!items.length)return '';return `<section class="phase-group"><div class="phase-heading">${esc(label)} <span>${items.length}</span></div>${items.map(j=>jobCard(j,labels)).join('')}</section>`}
function byQueue(a,b){return (a.queue_position||1e9)-(b.queue_position||1e9)||a.id-b.id}
function byId(a,b){return a.id-b.id}
function byRecent(a,b){return String(b.updated_at||'').localeCompare(String(a.updated_at||''))||b.id-a.id}
function byHidden(a,b){return String(b.hidden_at||'').localeCompare(String(a.hidden_at||''))||b.id-a.id}
function byArchived(a,b){const t=j=>String(j.source_archive?.archived_at||j.source_archive?.created_at||'');return t(b).localeCompare(t(a))||b.id-a.id}
function tabSections(tab,items,hidden=[]){if(tab==='waiting')return[['Cần thiết lập',items.filter(needsSetup).sort(byId)],['Tạm dừng / lỗi / có thể tiếp tục',items.filter(j=>!needsSetup(j)&&j.state!=='CANCELLED')],['Đã hủy',items.filter(j=>j.state==='CANCELLED').sort(byRecent),'cancelled'],['Đã ẩn',hidden.slice().sort(byHidden),'hidden']];if(tab==='review')return[['Cần duyệt cảnh',items.filter(j=>j.state==='WAITING_REVIEW').sort(byId)],['Đã duyệt xong — chờ xuất hoặc bỏ qua',items.filter(j=>j.state==='READY_TO_EXPORT').sort(byId)]];if(tab==='export')return[['Đang xuất',items.filter(j=>j.state!=='QUEUED')],['Chờ xuất',items.filter(j=>j.state==='QUEUED').sort(byQueue)]];if(tab==='completed')return[['Đã xuất video',items.filter(j=>j.state==='COMPLETED'&&!isArchived(j)).sort(byRecent)],['Đã bỏ qua (không xuất)',items.filter(j=>j.state==='SKIPPED'&&!isArchived(j)).sort(byRecent)],['Đã lưu trữ',items.filter(isArchived).sort(byArchived),'archived']];if(tab==='scan_queue')return[[null,items.slice().sort(byQueue)]];return[[null,items]]}
function renderJobs(force=false){if(!force&&jobsInteracting()){deferJobsRender();return}dropForeignDrafts();captureFoldState();captureRerunPanelDrafts();captureExportPanelDrafts();const jobs=status.jobs||[];pruneCleanupSelection(jobs);startingJobs.forEach(id=>{const j=jobs.find(x=>x.id===id);if(!j||!needsSetup(j))startingJobs.delete(id)});Object.keys(exportPanelDrafts).forEach(id=>{const j=jobs.find(x=>String(x.id)===String(id));if(!j||j.state!=='READY_TO_EXPORT')delete exportPanelDrafts[id]});const labels=Object.fromEntries((status.detector_options||[]).map(x=>[x.id,x.label])),groups=Object.fromEntries(JOB_TABS.map(([key])=>[key,[]])),hidden=jobs.filter(isHidden);jobs.forEach(j=>{if(!isHidden(j))groups[jobTab(j)].push(j)});const known=JOB_TABS.some(([key])=>key===activeJobTab),tab=known?activeJobTab:defaultJobTab(groups);if(!known&&Array.isArray(status.jobs))activeJobTab=tab;document.getElementById('job-tabs').innerHTML=JOB_TABS.map(([key,label])=>`<button class="job-tab ${tab===key?'active':''}" data-tab="${key}" aria-pressed="${tab===key}" onclick="selectJobTab('${key}')"><span>${label}</span><span class="tab-count">${groups[key].length}</span></button>`).join('');const content=(tab==='completed'&&groups.completed.length?cleanupToolbar(groups.completed):'')+tabSections(tab,groups[tab],hidden).map(([label,items,fold])=>fold?foldSection(fold,label,items,fold==='hidden'?`<div class="hidden-list">${items.map(hiddenRow).join('')}</div>`:items.map(j=>jobCard(j,labels)).join('')):label?phaseSection(label,items,labels):items.map(j=>jobCard(j,labels)).join('')).join('');document.getElementById('jobs').innerHTML=lostNotice()+(content||`<div class="empty">${jobs.length?'Không có video trong mục này.':'Chưa có video trong input.'}</div>`)}
function render(){const waiting=status.queue?.length||0;document.getElementById('worker').textContent=status.active?`Đang chạy #${status.active.job_id}: ${status.active.stage}${waiting?` · ${waiting} việc đang chờ`:''}`:(status.scheduler_paused?`Scheduler tạm dừng${waiting?` · ${waiting} việc giữ nguyên thứ tự`:''}`:waiting?`Đang chờ · ${waiting} việc trong hàng đợi`:'Đang chờ');const r=status.resources||{};document.getElementById('resource').textContent=`CPU ${Math.round(r.cpu_percent||0)}% · RAM ${Math.round(r.memory?.percent||0)}% · GPU ${r.gpu?Math.round(r.gpu.utilization_percent)+'%':'N/A'}`;syncHeaderHeight();renderJobs()}
function discardPendingLoads(){appliedSeq=Math.max(appliedSeq,loadSeq)}
async function load(){const seq=++loadSeq;const next=await json('/api/status');if(seq<=appliedSeq)return false;appliedSeq=seq;status=next;render();return true}
function renderAI(){const c=aiState.config||{},s=aiState.session||{};document.getElementById('ai-enabled').checked=!!c.enabled;document.getElementById('ai-model').value=c.model||'gpt-5.6-luna';document.getElementById('ai-effort').value=c.reasoning_effort||'medium';const e=document.getElementById('ai-message');e.textContent=`${aiState.message||'Chưa kiểm tra'} · ${c.model||''} / ${c.reasoning_effort||''} · session dùng chung: ${s.active?'đã khởi tạo':'sẽ tạo ở lượt audit đầu'}${aiState.login_running?' · đang chờ đăng nhập':''}`;e.className=aiState.ready?'ok':'error';render()}
async function loadAI(){aiState=await json('/api/ai');renderAI()}
async function saveAI(){try{aiState=await post('/api/ai/config',{enabled:document.getElementById('ai-enabled').checked,model:document.getElementById('ai-model').value,reasoning_effort:document.getElementById('ai-effort').value});renderAI();notify(`Đã lưu cấu hình AI: ${aiState.config.model} / ${aiState.config.reasoning_effort}`)}catch(e){notify(`Không lưu được cấu hình AI: ${e.message}`,true)}}
async function checkAI(){try{aiState=await post('/api/ai/check');renderAI();notify(aiState.ready?'AI Supervisor đã kết nối và sẵn sàng.':aiState.message,!aiState.ready)}catch(e){notify(`Không kiểm tra được AI: ${e.message}`,true)}}
async function loginAI(){try{aiState=await post('/api/ai/login');renderAI();alert('Codex đang mở luồng đăng nhập ChatGPT trong trình duyệt. Hoàn tất đăng nhập rồi bấm Kiểm tra kết nối.')}catch(e){alert(e.message)}}
function sameDetectorScope(a,b){const x=[...new Set(a||[])].sort(),y=[...new Set(b||[])].sort();return x.length===y.length&&x.every((v,i)=>v===y[i])}
function confirmStartScope(id,detectors){const previous=storageGet(LAST_SCOPE_KEY);const ask=Array.isArray(previous)?!sameDetectorScope(previous,detectors):detectors.some(x=>SENSITIVE_DETECTORS.includes(x));if(!ask)return true;const labels=Object.fromEntries((status.detector_options||[]).map(x=>[x.id,x.label])),job=(status.jobs||[]).find(x=>x.id===id);return confirm(`Bắt đầu #${id} ${job?videoName(job):''} với các nhóm: ${detectors.map(x=>labels[x]||x).join(', ')}?`)}
async function start(id,button){if(startingJobs.has(id))return;startingJobs.add(id);const release=()=>{startingJobs.delete(id);if(button&&button.isConnected){button.disabled=false;button.textContent='Bắt đầu'}};if(button){button.disabled=true;button.textContent='Đang bắt đầu…'}captureMetadataDraft(id);const metadata=metadataDrafts[id],detectors=selectedDetectors(id);if(!detectors||!metadata||!confirmStartScope(id,detectors)){release();return}try{await post(`/api/jobs/${id}/start`,{content_style:metadata.content_style,profile:metadata.profile,detectors,ocr_recognition_batch_size:selectedOcrBatch(id),fast_scan:selectedFastScan(id)})}catch(e){release();await load().catch(()=>{});const job=(status.jobs||[]).find(x=>x.id===id);if(job&&!needsSetup(job)){clearDraft(id);notify(`Video #${id} không còn chờ thiết lập (${statePresentation(job).label}); không cần bắt đầu lại.`)}else notify(`Không thể bắt đầu #${id}: ${e.message}`,true);return}discardPendingLoads();clearDraft(id);storageSet(LAST_SCOPE_KEY,detectors);await load();const job=(status.jobs||[]).find(x=>x.id===id),n=status.queue?.length||0;notify(`Đã xếp #${id} ${job?videoName(job):''} vào hàng đợi quét cảnh${job&&job.queue_position?` (lượt ${job.queue_position}/${n})`:''}.`)}
async function act(id,name){try{await post(`/api/jobs/${id}/${name}`);discardPendingLoads();await load()}catch(e){alert(e.message)}}
function cancelConfirmText(j){return `Hủy video #${j.id} ${videoName(j)}?\\n“Hủy” dừng hẳn việc quét hoặc lệnh xuất đang chờ/đang chạy của video này. Video chuyển vào nhóm “Đã hủy” ở cuối mục “Đang chờ xử lý” (có thể “Ẩn khỏi danh sách”); report, quyết định duyệt và video gốc giữ nguyên. Muốn làm lại: bấm “Chạy lại kiểm tra”, hoặc mở “Duyệt cảnh” nếu video đã có danh sách duyệt.\\n• “Dừng sau bước”/“Dừng ngay” chỉ tạm dừng; “Tiếp tục” chạy tiếp từ chỗ cũ.\\n• “Bỏ qua (không xuất)” dành cho video đã duyệt xong: đánh dấu xong mà không xuất, chuyển sang “Hoàn tất”.`}
async function cancelJob(id,button){if(cancellingJobs.has(id))return;const j=(status.jobs||[]).find(x=>x.id===id);if(!j)return;cancellingJobs.add(id);const reset=()=>{if(button&&button.isConnected){button.disabled=false;button.textContent='Hủy'}};if(button)button.disabled=true;if(!confirm(cancelConfirmText(j))){cancellingJobs.delete(id);reset();return}if(button)button.textContent='Đang hủy…';let err=null;try{await post(`/api/jobs/${id}/cancel`)}catch(e){err=e}cancellingJobs.delete(id);if(!err||err.status===409){discardPendingLoads();await load().catch(()=>{})}reset();if(!err)notify(`Đã hủy #${id}. Video nằm trong nhóm “Đã hủy” ở cuối mục “Đang chờ xử lý”.`);else if(err.code==='already_cancelled')notify(err.message);else notify(`Không thể hủy #${id}: ${err.message}`,true)}
async function setHidden(id,hide,button){if(hidingJobs.has(id))return;hidingJobs.add(id);if(button){button.disabled=true;button.textContent=hide?'Đang ẩn…':'Đang hiện lại…'}let err=null;try{await post(`/api/jobs/${id}/${hide?'hide':'unhide'}`)}catch(e){err=e}hidingJobs.delete(id);if(!err||err.status===409){discardPendingLoads();await load().catch(()=>{})}if(button&&button.isConnected){button.disabled=false;button.textContent=hide?'Ẩn khỏi danh sách':'Hiện lại'}if(!err)notify(hide?`Đã ẩn #${id} khỏi danh sách. Mở “Đã ẩn” ở cuối mục “Đang chờ xử lý” để hiện lại.`:`Đã hiện lại #${id} trong nhóm “Đã hủy”.`);else if(err.status===409)notify(err.message);else notify(`Không thể ${hide?'ẩn':'hiện lại'} #${id}: ${err.message}`,true)}
function hideJob(id,button){return setHidden(id,true,button)}
function unhideJob(id,button){return setHidden(id,false,button)}
async function recheckBin(kind,rowId,jobId,button){const key=`${kind}:${rowId}`;if(recheckingRows.has(key))return;recheckingRows.add(key);if(button){button.disabled=true;button.textContent='Đang kiểm tra…'}let r=null,err=null;try{r=await post('/api/source-recycle-check',{kind,id:rowId})}catch(e){err=e}recheckingRows.delete(key);if(!err||err.status===409){discardPendingLoads();await load().catch(()=>{})}if(button&&button.isConnected){button.disabled=false;button.textContent='Kiểm tra lại Thùng rác'}if(err)notify(`Không kiểm tra lại được Thùng rác cho #${jobId}: ${err.message}`,true);else notify((r&&r.message)||'')}
async function skipJob(id,button){if(skippingJobs.has(id))return;const j=(status.jobs||[]).find(x=>x.id===id);if(!j)return;skippingJobs.add(id);if(button)button.disabled=true;try{if(!confirm(`Đánh dấu #${id} ${videoName(j)} là xong mà không xuất video?\\n${skipReasonText(j)} Không tạo bản xuất; video gốc, report và quyết định duyệt được giữ nguyên. Video sẽ chuyển sang mục “Hoàn tất”; có thể bấm “Mở lại để xuất” sau.`))return;await post(`/api/jobs/${id}/skip`);discardPendingLoads();await load().catch(()=>{});notify(`Đã đánh dấu #${id} xong (không xuất video). Video đã chuyển sang mục “Hoàn tất”.`)}catch(e){notify(`Không thể bỏ qua #${id}: ${e.message}`,true)}finally{skippingJobs.delete(id);if(button&&button.isConnected)button.disabled=false}}
async function unskipJob(id,button){if(unskippingJobs.has(id))return;const j=(status.jobs||[]).find(x=>x.id===id);if(!j)return;unskippingJobs.add(id);if(button)button.disabled=true;try{if(!confirm(`Mở lại #${id} ${videoName(j)} để xuất video?\\nVideo sẽ quay về mục “Đang chờ duyệt” (Đã duyệt xong — chờ xuất hoặc bỏ qua).`))return;await post(`/api/jobs/${id}/unskip`);discardPendingLoads();await load().catch(()=>{});notify(`Đã mở lại #${id}; video nằm ở mục “Đang chờ duyệt”.`)}catch(e){notify(`Không thể mở lại #${id}: ${e.message}`,true)}finally{unskippingJobs.delete(id);if(button&&button.isConnected)button.disabled=false}}
async function exportVideo(id,button){if(exportingJobs.has(id))return;const j=(status.jobs||[]).find(x=>x.id===id);if(!j)return;exportingJobs.add(id);const release=()=>{exportingJobs.delete(id);if(button&&button.isConnected){button.disabled=false;button.textContent='Hoàn tất duyệt và xuất video'}};if(button){button.disabled=true;button.textContent='Đang gửi lệnh xuất…'}const fail=message=>{exportPanelDrafts[id]={...(exportPanelDrafts[id]||{}),error:message,open:true};release();setExportPanelOpen(id,true);renderJobs();notify(message,true)};if(exportPanelDrafts[id])delete exportPanelDrafts[id].error;if(!j.review_summary||j.review_summary.status!=='READY_FOR_EDIT_PLAN'){release();alert(EXPORT_GATE_MESSAGE);return}if(j.source_present===false){fail(SOURCE_MISSING_MESSAGE);return}captureExportPanelDrafts();const draft=exportPanelDrafts[id]||{},choice=exportPolicyChoice(j.review_summary.export_size_policy);let selection;try{selection=exportSizeSelection(draft.mode||choice.mode,draft.gb!=null?draft.gb:choice.gb)}catch(e){fail(e.message);return}if(!confirm(exportConfirmText(selection))){release();return}setExportPanelOpen(id,false);let result;try{result=await post(`/api/jobs/${id}/review/finalize`,selection)}catch(e){fail(`Không gửi được lệnh xuất: ${e.message}`);return}exportingJobs.delete(id);delete exportPanelDrafts[id];discardPendingLoads();await load().catch(()=>{});if(result&&result.status==='COMPLETED'){notify(`Video #${id} đã có bản xuất: ${result.output||''}.`);return}const job=(status.jobs||[]).find(x=>x.id===id),n=status.queue?.length||0;notify(`Đã xếp #${id} vào hàng đợi xuất video${job&&job.queue_position?` (lượt ${job.queue_position}/${n})`:''}. Theo dõi ở mục “Đang chạy xuất video”.`)}
async function rerun(id){const detectors=selectedDetectors(id);if(!detectors)return;if(!confirm('Chạy lại video với các nhóm kiểm tra đang chọn? Report và quyết định cũ vẫn được giữ; lượt mới dùng thư mục revision riêng.'))return;try{await post(`/api/jobs/${id}/rerun`,{detectors,ocr_recognition_batch_size:selectedOcrBatch(id),fast_scan:selectedFastScan(id)});discardPendingLoads();clearDraft(id);delete rerunPanelDrafts[id];await load();const job=(status.jobs||[]).find(x=>x.id===id),n=status.queue?.length||0;notify(`Đã xếp #${id} chạy lại theo phạm vi mới trong một revision riêng${job&&job.queue_position?` (lượt ${job.queue_position}/${n})`:''}.`)}catch(e){notify(`Không thể chạy lại: ${e.message}`,true)}}
function openSelectedCleanup(button){return openCleanup([...cleanupSelection].filter(id=>cleanupEligible((status.jobs||[]).find(x=>x.id===id))),button)}
function showCleanupDialog(){const d=document.getElementById('cleanup-dialog');if(!d)return;if(!d.open){if(typeof d.showModal==='function')d.showModal();else d.setAttribute('open','')}const cancel=document.getElementById('cleanup-cancel');if(cancel&&cancel.focus)cancel.focus()}
async function openCleanup(ids,button){if(cleanupOpening||cleanupPosting)return;const list=[...new Set((ids||[]).map(Number))].filter(x=>Number.isInteger(x)&&x>0).sort((a,b)=>a-b);if(!list.length)return;cleanupOpening=true;if(button)button.disabled=true;try{const p=await json(`/api/source-cleanup/preview?ids=${list.join(',')}`);renderCleanupDialog(p,'');showCleanupDialog()}catch(e){notify(`Không lấy được danh sách xóa video gốc: ${e.message}`,true)}finally{cleanupOpening=false;if(button&&button.isConnected)button.disabled=!!status.source_cleanup_running}}
function cleanupRow(x){const skipped=x.kind==='SKIPPED',output=skipped?'Đã bỏ qua (không xuất)':`${x.output_name||''}${x.output_bytes!=null?` (${formatBytes(x.output_bytes)})`:''}`,when=skipped?`Bỏ qua lúc ${formatStamp(x.skipped_at)}`:formatStamp(x.exported_at);return `<tr><td data-label="Video gốc">${esc(`#${x.job_id} ${x.file_name||x.name||''}`)}</td><td data-label="Dung lượng">${esc(formatBytes(x.size_bytes))}</td><td data-label="Báo cáo, log">${esc(formatBytes(x.reports_bytes))}</td><td data-label="Bản xuất (giữ .mp4)">${esc(output)}</td><td data-label="Xuất lúc">${esc(when)}</td></tr>`}
function cleanupConfirmable(){const p=cleanupPreview;return !!(cleanupAck&&p&&p.preview_id&&(p.eligible||[]).length)}
function setCleanupAck(checked){cleanupAck=!!checked;const b=document.getElementById('cleanup-confirm');if(b&&!cleanupPosting)b.disabled=!cleanupConfirmable()}
function renderCleanupDialog(p,message){if(!p||!cleanupPreview||p.preview_id!==cleanupPreview.preview_id)cleanupAck=false;cleanupPreview=p||null;const eligible=(p&&p.eligible)||[],ineligible=(p&&p.ineligible)||[],n=eligible.length,parts=['<p>Các video dưới đây sẽ bị xóa vĩnh viễn: không qua Thùng rác, không khôi phục được.</p><ul><li>Video gốc trong thư mục input.</li><li>Manifest của bản xuất (…-reviewed.mp4.manifest.json).</li><li>Thư mục báo cáo và log của video.</li><li>Video trong BiliFlow, cùng quyết định duyệt của nó.</li></ul><p>Giữ nguyên: file .mp4 đã xuất trong output (không còn gắn với video nào trong BiliFlow), bộ nhớ logo/studio và các báo cáo benchmark. Sau khi xóa, video không duyệt hay xuất lại được nữa.</p>'];if(message)parts.push(`<p class="cleanup-alert" role="alert">${esc(message)}</p>`);if(n){parts.push(`<div class="cleanup-table-wrap"><table class="cleanup-table"><thead><tr><th>Video gốc</th><th>Dung lượng</th><th>Báo cáo, log</th><th>Bản xuất (giữ .mp4)</th><th>Xuất lúc</th></tr></thead><tbody>${eligible.map(cleanupRow).join('')}</tbody></table></div>`);parts.push(`<p class="cleanup-summary">${esc(`Tổng cộng: ${n} video · ${formatBytes(p.total_bytes)} video gốc · ${formatBytes(p.reports_bytes)} báo cáo và log sẽ bị xóa vĩnh viễn.`)}</p>`)}else parts.push('<p class="cleanup-alert">Không có video nào xóa được trong lựa chọn này.</p>');if(ineligible.length)parts.push(`<div class="cleanup-ineligible"><p>Không thể xóa:</p><ul>${ineligible.map(x=>`<li>${esc(`#${x.job_id} ${x.name||''} — ${x.reason||''}`)}</li>`).join('')}</ul></div>`);if(n)parts.push(`<label class="cleanup-ack"><input type="checkbox" id="cleanup-ack" ${cleanupAck?'checked':''} onchange="setCleanupAck(this.checked)"> <span>${esc(CLEANUP_ACK_TEXT)}</span></label>`);parts.push(`<p class="cleanup-note" id="cleanup-wait" ${cleanupPosting?'':'hidden'}>Có thể mất vài phút với nhiều video; đừng tắt BiliFlow.</p>`);const body=document.getElementById('cleanup-dialog-body'),confirmButton=document.getElementById('cleanup-confirm'),cancel=document.getElementById('cleanup-cancel');if(body)body.innerHTML=parts.join('');if(confirmButton){confirmButton.textContent=`Xóa vĩnh viễn ${n} video gốc`;confirmButton.disabled=!cleanupConfirmable()}if(cancel)cancel.disabled=false}
function closeCleanupDialog(){if(cleanupPosting)return;const d=document.getElementById('cleanup-dialog');if(d){if(typeof d.close==='function'){if(d.open)d.close()}else d.removeAttribute('open')}cleanupPreview=null;cleanupAck=false}
function watchCleanupDialog(){try{const d=document.getElementById('cleanup-dialog');if(!d||!d.addEventListener)return;d.addEventListener('cancel',e=>{if(cleanupPosting)e.preventDefault()});d.addEventListener('close',()=>{if(cleanupPosting){showCleanupDialog();return}cleanupPreview=null;cleanupAck=false})}catch(e){}}
function deleteDetails(results){return results.map(x=>{const m=x.message||'';if(x.status==='PARTIAL')return ` #${x.job_id}: ${m}`;if(['FAILED','NOT_RUN'].includes(x.status))return ` Không xóa được #${x.job_id}: ${m}`;return x.status==='DELETED'&&m.includes('Lưu ý')?` #${x.job_id}: ${m}`:''}).join('')}
function cleanupResultText(result){const results=(result&&result.results)||[],n=Number(result&&result.deleted_count)||0,partial=Number(result&&result.partial_count)||0,freed=Number(result&&result.deleted_bytes)||0;return `${n?`Đã xóa vĩnh viễn ${n} video gốc và xóa ${n} video khỏi BiliFlow.`:partial?'':'Không xóa được video gốc nào.'}${freed?` Dung lượng video gốc đã xóa: ${formatBytes(freed)}.`:''}${deleteDetails(results)}`.trim()}
async function confirmCleanup(){if(cleanupPosting)return;const p=cleanupPreview,ids=((p&&p.eligible)||[]).map(x=>x.job_id);if(!p||!p.preview_id||!ids.length||!cleanupAck)return;cleanupPosting=true;const confirmButton=document.getElementById('cleanup-confirm'),cancel=document.getElementById('cleanup-cancel'),wait=document.getElementById('cleanup-wait'),ack=document.getElementById('cleanup-ack');if(confirmButton){confirmButton.disabled=true;confirmButton.textContent='Đang kiểm tra SHA-256 và xóa…'}if(cancel)cancel.disabled=true;if(ack)ack.disabled=true;if(wait)wait.hidden=false;let result;try{result=await post('/api/source-cleanup',{job_ids:ids,preview_id:p.preview_id,confirm_permanent:true})}catch(e){cleanupPosting=false;const fresh=e.body&&e.body.preview;if(e.code==='preview_changed'&&fresh)renderCleanupDialog(fresh,'Danh sách đã thay đổi, hãy xem lại.');else if(e.code==='busy')renderCleanupDialog(fresh||p,e.message);else renderCleanupDialog(p,`Không xóa được: ${e.message}`);showCleanupDialog();return}cleanupPosting=false;closeCleanupDialog();const results=(result&&result.results)||[];results.forEach(x=>{if(['DELETED','PARTIAL'].includes(x.status))cleanupSelection.delete(x.job_id)});discardPendingLoads();await load().catch(()=>{});notify(cleanupResultText(result),!results.length||results.some(x=>x.status!=='DELETED'))}
function showDeleteDialog(){const d=document.getElementById('delete-dialog');if(!d)return;if(!d.open){if(typeof d.showModal==='function')d.showModal();else d.setAttribute('open','')}const cancel=document.getElementById('delete-cancel');if(cancel&&cancel.focus)cancel.focus()}
async function openDelete(ids,button,message=''){if(deleteOpening||deletePosting)return;const list=[...new Set((ids||[]).map(Number))].filter(x=>Number.isInteger(x)&&x>0).sort((a,b)=>a-b);if(!list.length)return;deleteOpening=true;if(button)button.disabled=true;try{const p=await json(`/api/job-delete/preview?ids=${list.join(',')}`);renderDeleteDialog(p,message);showDeleteDialog()}catch(e){notify(`Không lấy được danh sách xóa video: ${e.message}`,true)}finally{deleteOpening=false;if(button&&button.isConnected)button.disabled=!!status.source_cleanup_running}}
function deleteRow(x){const k=DELETE_KINDS[x.kind]||[x.kind||'',''];return `<tr><td data-label="Video">${esc(`#${x.job_id} ${x.file_name||x.name||''}`)}</td><td data-label="Loại">${esc(k[0])}</td><td data-label="Sẽ xóa">${esc(k[1])}</td><td data-label="Video gốc">${esc(x.kind==='LOST'?'Đã mất':formatBytes(x.size_bytes))}</td><td data-label="Báo cáo, log">${esc(formatBytes(x.reports_bytes))}</td></tr>`}
function deleteConfirmable(){const p=deletePreview;return !!(deleteAck&&p&&p.preview_id&&(p.eligible||[]).length)}
function setDeleteAck(checked){deleteAck=!!checked;const b=document.getElementById('delete-confirm');if(b&&!deletePosting)b.disabled=!deleteConfirmable()}
function renderDeleteDialog(p,message){if(!p||!deletePreview||p.preview_id!==deletePreview.preview_id)deleteAck=false;deletePreview=p||null;const eligible=(p&&p.eligible)||[],ineligible=(p&&p.ineligible)||[],n=eligible.length,cancelled=eligible.some(x=>x.kind==='CANCELLED'),parts=['<p>Các video dưới đây sẽ bị xóa khỏi BiliFlow vĩnh viễn: không qua Thùng rác, không khôi phục được.</p><ul><li>Video đã hủy: xóa video gốc trong thư mục input, rồi xóa dữ liệu của video trong BiliFlow.</li><li>Video mất video gốc: chỉ xóa dữ liệu của video trong BiliFlow.</li></ul><p>Dữ liệu của video trong BiliFlow gồm video trong danh sách, quyết định duyệt, thư mục báo cáo và log. Giữ nguyên: mọi file trong output (bản xuất và manifest), bộ nhớ logo/studio và các báo cáo benchmark.</p>'];if(message)parts.push(`<p class="cleanup-alert" role="alert">${esc(message)}</p>`);if(n){parts.push(`<div class="cleanup-table-wrap"><table class="cleanup-table"><thead><tr><th>Video</th><th>Loại</th><th>Sẽ xóa</th><th>Video gốc</th><th>Báo cáo, log</th></tr></thead><tbody>${eligible.map(deleteRow).join('')}</tbody></table></div>`);parts.push(`<p class="cleanup-summary">${esc(`Tổng cộng: ${n} video${cancelled?` · ${formatBytes(p.total_bytes)} video gốc`:''} · ${formatBytes(p.reports_bytes)} báo cáo và log sẽ bị xóa vĩnh viễn.`)}</p>`)}else parts.push('<p class="cleanup-alert">Không có video nào xóa được trong lựa chọn này.</p>');if(ineligible.length)parts.push(`<div class="cleanup-ineligible"><p>Không thể xóa:</p><ul>${ineligible.map(x=>`<li>${esc(`#${x.job_id} ${x.name||''} — ${x.reason||''}`)}</li>`).join('')}</ul></div>`);if(n)parts.push(`<label class="cleanup-ack"><input type="checkbox" id="delete-ack" ${deleteAck?'checked':''} onchange="setDeleteAck(this.checked)"> <span>${esc(cancelled?DELETE_ACK_CANCELLED:DELETE_ACK_LOST)}</span></label>`);parts.push(`<p class="cleanup-note" id="delete-wait" ${deletePosting?'':'hidden'}>Có thể mất vài phút với nhiều video; đừng tắt BiliFlow.</p>`);const body=document.getElementById('delete-dialog-body'),confirmButton=document.getElementById('delete-confirm'),cancel=document.getElementById('delete-cancel');if(body)body.innerHTML=parts.join('');if(confirmButton){confirmButton.textContent=`Xóa vĩnh viễn ${n} video`;confirmButton.disabled=!deleteConfirmable()}if(cancel)cancel.disabled=false}
function closeDeleteDialog(){if(deletePosting)return;const d=document.getElementById('delete-dialog');if(d){if(typeof d.close==='function'){if(d.open)d.close()}else d.removeAttribute('open')}deletePreview=null;deleteAck=false}
function watchDeleteDialog(){try{const d=document.getElementById('delete-dialog');if(!d||!d.addEventListener)return;d.addEventListener('cancel',e=>{if(deletePosting)e.preventDefault()});d.addEventListener('close',()=>{if(deletePosting){showDeleteDialog();return}deletePreview=null;deleteAck=false})}catch(e){}}
function deleteResultText(result){const results=(result&&result.results)||[],n=Number(result&&result.deleted_count)||0,partial=Number(result&&result.partial_count)||0,freed=Number(result&&result.deleted_bytes)||0;return `${n?`Đã xóa ${n} video khỏi BiliFlow.`:partial?'':'Không xóa được video nào.'}${freed?` Dung lượng video gốc đã xóa: ${formatBytes(freed)}.`:''}${deleteDetails(results)}`.trim()}
async function confirmDelete(){if(deletePosting)return;const p=deletePreview,ids=((p&&p.eligible)||[]).map(x=>x.job_id);if(!p||!p.preview_id||!ids.length||!deleteAck)return;deletePosting=true;const confirmButton=document.getElementById('delete-confirm'),cancel=document.getElementById('delete-cancel'),wait=document.getElementById('delete-wait'),ack=document.getElementById('delete-ack');if(confirmButton){confirmButton.disabled=true;confirmButton.textContent=p.eligible.some(x=>x.kind==='CANCELLED')?'Đang kiểm tra SHA-256 và xóa…':'Đang xóa…'}if(cancel)cancel.disabled=true;if(ack)ack.disabled=true;if(wait)wait.hidden=false;let result;try{result=await post('/api/job-delete',{job_ids:ids,preview_id:p.preview_id,confirm_permanent:true})}catch(e){deletePosting=false;const fresh=e.body&&e.body.preview;if(e.code==='preview_changed'&&fresh)renderDeleteDialog(fresh,'Danh sách đã thay đổi, hãy xem lại.');else if(e.code==='busy')renderDeleteDialog(fresh||p,e.message);else renderDeleteDialog(p,`Không xóa được: ${e.message}`);showDeleteDialog();return}deletePosting=false;closeDeleteDialog();const results=(result&&result.results)||[];results.forEach(x=>{if(['DELETED','PARTIAL'].includes(x.status))cleanupSelection.delete(x.job_id)});discardPendingLoads();await load().catch(()=>{});notify(deleteResultText(result),!results.length||results.some(x=>x.status!=='DELETED'))}
function openSelectedArchive(button){return openArchive([...cleanupSelection].filter(id=>archiveEligible((status.jobs||[]).find(x=>x.id===id))),button)}
function showArchiveDialog(){const d=document.getElementById('archive-dialog');if(!d)return;if(!d.open){if(typeof d.showModal==='function')d.showModal();else d.setAttribute('open','')}const cancel=document.getElementById('archive-cancel');if(cancel&&cancel.focus)cancel.focus()}
async function openArchive(ids,button){if(archiveOpening||archivePosting)return;const list=[...new Set((ids||[]).map(Number))].filter(x=>Number.isInteger(x)&&x>0).sort((a,b)=>a-b);if(!list.length)return;archiveOpening=true;if(button)button.disabled=true;try{const p=await json(`/api/source-archive/preview?ids=${list.join(',')}`);renderArchiveDialog(p,'');showArchiveDialog()}catch(e){notify(`Không lấy được danh sách lưu trữ: ${e.message}`,true)}finally{archiveOpening=false;if(button&&button.isConnected)button.disabled=!!status.source_cleanup_running}}
function archiveRow(x){const skipped=x.kind==='SKIPPED',output=skipped?'Đã bỏ qua (không xuất)':`${x.output_name||''}${x.output_bytes!=null?` (${formatBytes(x.output_bytes)})`:''}`,when=skipped?`Bỏ qua lúc ${formatStamp(x.skipped_at)}`:formatStamp(x.exported_at);return `<tr><td data-label="Video gốc">${esc(`#${x.job_id} ${x.file_name||x.name||''}`)}</td><td data-label="Dung lượng">${esc(formatBytes(x.size_bytes))}</td><td data-label="Lưu vào">${esc(x.archive_path||'')}</td><td data-label="Bản xuất vào Thùng rác">${esc(output)}</td><td data-label="Xuất lúc">${esc(when)}</td></tr>`}
function renderArchiveDialog(p,message){archivePreview=p||null;const eligible=(p&&p.eligible)||[],ineligible=(p&&p.ineligible)||[],rb=p&&p.recycle_bin,blocked=p&&p.blocked,n=eligible.length,parts=['<p>Video gốc dưới đây được chuyển vào kho lưu trữ của BiliFlow (thư mục “archive”, cùng ổ đĩa với input; không xóa, không nén) và được kiểm tra SHA-256 sau khi chuyển. Với video đã xuất, bản xuất và manifest của nó được chuyển vào Thùng rác của Windows (không xóa vĩnh viễn); video đã bỏ qua chỉ chuyển video gốc. Report, quyết định duyệt và bộ nhớ logo/studio được giữ nguyên; cạnh mỗi video gốc có một archive-manifest.json ghi lại quyết định duyệt và bản xuất. Muốn xuất lại: bấm “Khôi phục bản xuất”.</p>'];if(message)parts.push(`<p class="cleanup-alert" role="alert">${esc(message)}</p>`);if(n){parts.push(`<div class="cleanup-table-wrap"><table class="cleanup-table"><thead><tr><th>Video gốc</th><th>Dung lượng</th><th>Lưu vào</th><th>Bản xuất vào Thùng rác</th><th>Xuất lúc</th></tr></thead><tbody>${eligible.map(archiveRow).join('')}</tbody></table></div>`);parts.push(`<p class="cleanup-summary">${esc(`Tổng cộng: ${n} video · ${formatBytes(p.archive_bytes)} video gốc vào kho lưu trữ${p.freed_bytes?` · ${formatBytes(p.freed_bytes)} bản xuất vào Thùng rác (giải phóng khi bạn dọn sạch Thùng rác)`:''}.`)}</p>`)}else parts.push('<p class="cleanup-alert">Không có video nào lưu trữ được trong lựa chọn này.</p>');if(rb)parts.push(`<p class="cleanup-bin">${esc(`Thùng rác của ổ ${rb.volume} đang chứa ${formatBytes(rb.used_bytes)} / giới hạn ${formatBytes(rb.max_bytes)}; sau khi chuyển bản xuất: ${formatBytes(rb.after_bytes)}.`)}</p>`);if(blocked)parts.push(`<p class="cleanup-block" role="alert">${esc(blocked)}</p>`);if(ineligible.length)parts.push(`<div class="cleanup-ineligible"><p>Không thể lưu trữ:</p><ul>${ineligible.map(x=>`<li>${esc(`#${x.job_id} ${x.name||''} — ${x.reason||''}`)}</li>`).join('')}</ul></div>`);parts.push(`<p class="cleanup-note" id="archive-wait" ${archivePosting?'':'hidden'}>Có thể mất vài phút với nhiều video (kiểm tra SHA-256 từng video); đừng tắt BiliFlow.</p>`);const body=document.getElementById('archive-dialog-body'),confirmButton=document.getElementById('archive-confirm'),cancel=document.getElementById('archive-cancel');if(body)body.innerHTML=parts.join('');if(confirmButton){confirmButton.textContent=`Lưu trữ ${n} video`;confirmButton.disabled=!!blocked||!n||!(p&&p.preview_id)}if(cancel)cancel.disabled=false}
function closeArchiveDialog(){if(archivePosting)return;const d=document.getElementById('archive-dialog');if(d){if(typeof d.close==='function'){if(d.open)d.close()}else d.removeAttribute('open')}archivePreview=null}
function watchArchiveDialog(){try{const d=document.getElementById('archive-dialog');if(!d||!d.addEventListener)return;d.addEventListener('cancel',e=>{if(archivePosting)e.preventDefault()});d.addEventListener('close',()=>{if(archivePosting){showArchiveDialog();return}archivePreview=null})}catch(e){}}
function archiveResultText(result){const results=(result&&result.results)||[],n=Number(result&&result.archived_count)||0,freed=Number(result&&result.freed_bytes)||0;let text=n?`Đã lưu trữ ${n} video gốc${freed?`; bản xuất (${formatBytes(freed)}) đã vào Thùng rác, dung lượng được giải phóng khi bạn dọn sạch Thùng rác`:''}.`:'Không lưu trữ được video gốc nào.';results.forEach(x=>{if(['FAILED','NOT_RUN'].includes(x.status))text+=` Không lưu trữ được #${x.job_id}: ${x.message||''}`;else if(x.status==='PENDING')text+=` #${x.job_id} chưa xong: ${x.message||''}`;else if(x.status==='UNVERIFIED')text+=` #${x.job_id}: bản xuất đã rời thư mục output nhưng Windows chưa xác nhận bản ghi trong Thùng rác; hãy kiểm tra Thùng rác.`});return text}
async function confirmArchive(){if(archivePosting)return;const p=archivePreview,ids=((p&&p.eligible)||[]).map(x=>x.job_id);if(!p||!p.preview_id||p.blocked||!ids.length)return;archivePosting=true;const confirmButton=document.getElementById('archive-confirm'),cancel=document.getElementById('archive-cancel'),wait=document.getElementById('archive-wait');if(confirmButton){confirmButton.disabled=true;confirmButton.textContent='Đang kiểm tra SHA-256 và lưu trữ…'}if(cancel)cancel.disabled=true;if(wait)wait.hidden=false;let result;try{result=await post('/api/source-archive',{job_ids:ids,preview_id:p.preview_id})}catch(e){archivePosting=false;const fresh=e.body&&e.body.preview;if(e.code==='preview_changed'&&fresh)renderArchiveDialog(fresh,'Danh sách đã thay đổi, hãy xem lại.');else if(['bin_capacity','bin_unavailable','busy'].includes(e.code))renderArchiveDialog(fresh||p,e.message);else renderArchiveDialog(p,`Không lưu trữ được: ${e.message}`);showArchiveDialog();return}archivePosting=false;closeArchiveDialog();const results=(result&&result.results)||[];results.forEach(x=>{if(['ARCHIVED','UNVERIFIED'].includes(x.status))cleanupSelection.delete(x.job_id)});discardPendingLoads();await load().catch(()=>{});notify(archiveResultText(result),!results.length||results.some(x=>x.status!=='ARCHIVED'))}
function restoreConfirmText(j){const exported=j.source_archive?.kind!=='SKIPPED';return `Khôi phục bản xuất cho #${j.id} ${videoName(j)}?\\nVideo gốc được đưa từ kho lưu trữ về input (kiểm tra SHA-256); ${exported?'video về mục “Đang chờ duyệt” để xuất lại; bản xuất cũ vẫn ở Thùng rác tới khi bạn dọn sạch.':'video vẫn ở mục “Hoàn tất” (đã bỏ qua).'}`}
async function restoreArchive(id,button){if(restoringJobs.has(id))return;const j=(status.jobs||[]).find(x=>x.id===id);if(!j)return;restoringJobs.add(id);const reset=()=>{if(button&&button.isConnected){button.disabled=false;button.textContent='Khôi phục bản xuất'}};if(button)button.disabled=true;if(!confirm(restoreConfirmText(j))){restoringJobs.delete(id);reset();return}if(button)button.textContent='Đang khôi phục…';let r=null,err=null;try{r=await post('/api/source-archive/restore',{job_id:id})}catch(e){err=e}restoringJobs.delete(id);if(!err||err.status===409){discardPendingLoads();await load().catch(()=>{})}reset();if(err)notify(`Không khôi phục được #${id}: ${err.message}`,true);else notify((r&&r.message)||`Đã khôi phục #${id}.`)}
async function audit(id,visual=false){if(visual&&!confirm('Visual AI Audit sẽ gửi tối đa 36 ảnh thumbnail của riêng video này cho Codex bằng tài khoản ChatGPT. Video và âm thanh gốc không được gửi. Tiếp tục?'))return;try{await post(`/api/jobs/${id}/ai-audit`,{visual});alert(visual?'Visual AI Audit đã được xếp chạy. AI chỉ đưa đề xuất; bạn vẫn duyệt mọi thay đổi.':'AI JSON audit đã được xếp chạy.')}catch(e){alert(e.message)}}
async function scheduler(paused){await post('/api/scheduler',{paused});discardPendingLoads();await load()}
async function shutdown(mode){if(!confirm(mode==='immediate'?'Dừng bước hiện tại và tắt Control Center?':'Tắt sau khi bước hiện tại hoàn tất?'))return;document.body.innerHTML='<main><div class="empty"><div class="name" id="shutdown-state">Đang gửi lệnh tắt BiliFlow…</div><p class="muted">Tab sẽ được giữ lại để hiển thị kết quả.</p></div></main>';try{await post('/api/shutdown',{mode});document.getElementById('shutdown-state').textContent=mode==='immediate'?'Đang dừng job và tắt backend…':'Đang chờ bước hiện tại hoàn tất rồi tắt…';for(let i=0;i<120;i++){await new Promise(r=>setTimeout(r,500));try{await fetch('/healthz',{cache:'no-store'})}catch(_){document.getElementById('shutdown-state').textContent='BiliFlow đã tắt hoàn toàn. Bạn có thể đóng tab.';return}}document.getElementById('shutdown-state').textContent='Lệnh tắt đã được nhận nhưng backend vẫn đang hoàn tất bước hiện tại.'}catch(e){document.getElementById('shutdown-state').textContent=`Không xác nhận được trạng thái tắt: ${e.message}`}}
watchDeleteDialog();watchArchiveDialog();watchCleanupDialog();(async()=>{watchJobsInteraction();watchHeaderHeight();await refreshToken();await Promise.all([load(),loadAI()]);setInterval(()=>load().catch(e=>notify(`Mất kết nối dashboard: ${e.message}`,true)),3000);setInterval(()=>{if(aiState.login_running)loadAI().catch(()=>{})},3000)})().catch(e=>notify(e.message,true));
</script></body></html>"""
    return (
        page.replace("__EXPORT_DIALOG_JS__", EXPORT_DIALOG_JS)
        .replace("__EXPORT_CUSTOM_GB__", EXPORT_CUSTOM_GB_ATTRIBUTES)
        .replace("__SOURCE_MISSING_MESSAGE__", SOURCE_MISSING_MESSAGE)
        .replace("__SOURCE_CLEANED_MESSAGE__", SOURCE_CLEANED_MESSAGE)
        .replace("__SOURCE_ARCHIVED_MESSAGE__", SOURCE_ARCHIVED_MESSAGE)
    )


class ControlCenter:
    # "Xóa video gốc" and "Xóa video": no real default. A stub built with __new__
    # (tests) gets this, which refuses; only __init__ binds the permanent delete.
    source_deleter = staticmethod(_unconfigured_deleter)
    # Batch 4 ("Lưu trữ" and "Kiểm tra lại Thùng rác"): the same rule for the Recycle Bin.
    bin_info = staticmethod(_unconfigured_bin_info)
    export_recycler = staticmethod(_unconfigured_recycler)
    record_finder = staticmethod(_unconfigured_finder)

    def __init__(self, root: Path, *, host: str = "127.0.0.1", port: int = 8765,
                 stable_seconds: float = 60.0, import_existing: bool = True):
        self.root = root.resolve(strict=True)
        self.host = host
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self.lock = SingleInstanceLock(self.root / "state" / "control-center.lock")
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.recovered = self.store.recover_interrupted()
        # A cleanup interrupted by a crash or a closed window: settle its PENDING
        # rows on every start, whether or not existing reports are imported.
        self.cleanup_reconciled = source_cleanup.reconcile_pending_cleanups(
            self.root, self.store, finder=recycle_bin.find_recycle_record,
        )
        # The same for an interrupted "Lưu trữ" or "Khôi phục bản xuất" (never the recycler).
        self.archive_reconciled = source_archive_restore.reconcile_pending_archives(
            self.root, self.store, finder=recycle_bin.find_recycle_record,
        )
        # The only place that binds the real permanent delete and Recycle Bin functions.
        self.source_deleter = job_purge.delete_input_file
        self.export_recycler = recycle_bin.send_export_to_recycle_bin
        self.bin_info = recycle_bin.volume_bin_info
        self.record_finder = recycle_bin.find_recycle_record
        if import_existing:
            import_existing_project(self.root, self.store)
        self.scheduler = JobScheduler(self.root, self.store)
        # A crash after the last stage finished but before its result was
        # recorded: record it now, as the worker would have done.
        self.scheduler.recover_finished_stages()
        self.watcher = InputWatcher(self.root, self.store, stable_seconds=stable_seconds)
        self.server: ThreadingHTTPServer | None = None
        self._stopping = threading.Event()
        # Set when stop() has finished (store and lock closed); serve() waits
        # for it so the process outlives an /api/shutdown stop thread.
        self._stopped = threading.Event()
        # Scope audits to the active queue revision because reruns keep the job id.
        self._audit_jobs: dict[int, str] = {}
        self._audit_lock = threading.Lock()
        # Codex accepts sequential turns on one persistent thread.  Serializing
        # audits prevents two video workers from racing on that shared session.
        self._ai_session_lock = threading.Lock()
        self._audit_cancel = threading.Event()
        self._audit_threads: set[threading.Thread] = set()
        self._login_process = None
        self._login_log_handle = None
        # Review evidence frames: CPU FFmpeg, read-only source, bounded cache.
        self.frame_cache = ReviewFrameCache(
            self.root, self.root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
        )
        # "Tải video": its own database (state/downloads.sqlite3) and worker, started in serve().
        # A broken download database answers 503 on its routes; scans and review keep running.
        self.downloads: download_api.DownloadService | None = None
        self.downloads_error: str | None = None
        try:
            self.downloads = download_api.DownloadService(
                self.root, cleanable=self.cleanable_sources, bin_reader=recycle_bin.volume_bin_info,
            )
        except Exception as error:  # noqa: BLE001
            self.downloads_error = f"{type(error).__name__}: {error}"

    def cleanable_sources(self) -> tuple[int, int]:
        """"Dung lượng": the jobs whose source "Dọn video gốc" could clean now, and their bytes.

        The same read-only assessment as the card hints: no hash, no Recycle Bin query.
        """
        cleanups = self.store.latest_source_cleanups()
        archives = self.store.latest_source_archives()
        count = total = 0
        for job in self.store.list_jobs():
            if job.get("state") not in source_cleanup.ELIGIBLE_STATES:
                continue
            hint, _ = self._source_hints(job, cleanups.get(int(job["id"])), archives.get(int(job["id"])))
            if hint and hint["eligible"]:
                count += 1
                total += int(hint["size_bytes"] or 0)
        return count, total

    def stop_downloads(self) -> None:
        """Stop the download worker (its yt-dlp trees die) and close its database."""
        service = getattr(self, "downloads", None)
        if service is None:
            return
        try:
            service.stop()
        except Exception as error:  # noqa: BLE001 - the scheduler and the store must still stop
            self.store.add_event(None, "DOWNLOADS_STOP_FAILED", f"Dừng tải video lỗi: {error}", level="ERROR")

    def status(self) -> dict[str, Any]:
        jobs = self.store.list_jobs()
        # The same order the worker uses, so "Thứ tự chờ" is the real run order.
        order = {item["job_id"]: item for item in self.scheduler.queue_order()}
        # One read of every job's latest cleanup row; the hints never hash and
        # never query the Recycle Bin (they re-read a queue only when it changed).
        context = {
            "cleanups": self.store.latest_source_cleanups(),
            "cleanup_checks": self.store.recycle_check_summary("SOURCE_CLEANUP"),
            "archives": self.store.latest_source_archives(),
            "archive_checks": self.store.recycle_check_summary("ARCHIVE_EXPORT"),
            # One read of the golden sets per call ("protected" and the "Xóa video" hints).
            "golden": job_purge.golden_index(self.root),
        }
        shown: list[dict[str, Any]] = []
        for job in jobs:
            try:
                self._fill_card(job, order.get(int(job["id"])), context)
            except KeyError:
                # Removed ("Xóa video gốc", "Xóa video") after list_jobs(): no longer listed.
                if self._job_exists(int(job["id"])):
                    raise
                continue
            shown.append(job)
        return {
            "version": __version__, "started": True, "recovered_jobs": self.recovered,
            "scheduler_paused": self.store.setting("scheduler_paused", False),
            "queue": {
                "length": len(order),
                "paused": bool(self.store.setting("scheduler_paused", False)),
            },
            "active": self.scheduler.active, "resources": _resources(self.root),
            "jobs": shown, "storage": storage_status(self.root).as_dict(),
            "detector_options": [
                {"id": key, **value} for key, value in DETECTOR_GROUPS.items()
            ],
            "source_cleanup_running": source_cleanup.cleanup_running(),
        }

    def _fill_card(self, job: dict[str, Any], place: dict[str, Any] | None, context: dict[str, Any]) -> None:
        """The status() fields of one job card (KeyError once the job is gone)."""
        job["queue_position"] = place["position"] if place else None
        job["queue_kind"] = place["kind"] if place else None
        job["ai_audit"] = self.ai_audit_summary(int(job["id"]))
        job["structure_audit"] = self.structure_audit_summary(int(job["id"]))
        job["render_progress"] = self.render_progress_summary(job)
        # Dashboard V2: an unfinished export request (waiting, running or failed
        # and retryable) locks export, rerun and decisions; the card no longer guesses it.
        job["render_request"] = self.store.render_request(int(job["id"])) is not None
        job["ocr_recognition_batch_size"] = self.scheduler.ocr_batch_size(int(job["id"]))
        job["fast_scan"] = self.scheduler.fast_scan(int(job["id"]))
        job["detector_groups"] = list(
            self.scheduler.detector_groups(int(job["id"]))
        )
        job["review_summary"] = self.review_summary_for(job)
        job["source_present"] = Path(str(job["source_path"])).is_file()
        job["skip"] = (
            self.store.setting(f"skip:{int(job['id'])}") if job["state"] == "SKIPPED" else None
        )
        row = context["cleanups"].get(int(job["id"]))
        check = context["cleanup_checks"].get(int(row["id"])) if row else None
        job["source_cleanup"] = source_cleanup.cleanup_row_summary(row, check)
        job["source_cleaned"] = bool(row and row["state"] in ("PENDING", "RECYCLED"))
        archive = context["archives"].get(int(job["id"]))
        job["source_archive"] = source_archive.archive_row_summary(
            archive, context["archive_checks"].get(int(archive["id"])) if archive else None,
        )
        job["source_archived"] = bool(archive and archive["state"] in SOURCE_ARCHIVED_STATES)
        job["cleanup"], job["archive"] = self._source_hints(job, row, archive)
        # A golden-set job: every delete button stays off, with this reason.
        job["protected"] = job_purge.protected_reason(self.root, job, index=context["golden"])
        job["delete"] = self._delete_hint(job, row, archive, check, context["golden"])

    def _delete_hint(self, job: dict[str, Any], row: dict[str, Any] | None, archive: dict[str, Any] | None,
                     check: dict[str, Any] | None, golden: job_purge.GoldenIndex) -> dict[str, Any] | None:
        """The "Xóa video" hint (a cancelled job, or one whose source is gone); an error marks only that card."""
        try:
            return job_delete.delete_hint(
                self.root, self.store, self.scheduler, job, latest_row=row, archive_row=archive, check=check,
                audit_running=self.audit_running, index=golden,
            )
        except Exception as error:  # noqa: BLE001 - one unreadable job must not break the Dashboard
            return {"eligible": False, "kind": None, "reason": f"Không kiểm tra được: {error}", "size_bytes": 0}

    def _job_exists(self, job_id: int) -> bool:
        try:
            self.store.get_job(job_id)
        except KeyError:
            return False
        return True

    def _source_hints(
        self, job: dict[str, Any], row: dict[str, Any] | None, archive: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """The "Xóa video gốc" and "Lưu trữ" hints of a card, from one shared assessment.

        Never hashes and never queries the Recycle Bin; an error only marks
        that card, never status().
        """
        if job.get("state") not in source_cleanup.ELIGIBLE_STATES:
            return None, None
        try:
            base = source_cleanup.assess_job(
                self.root, self.store, self.scheduler, job, latest_row=row, archive_row=archive,
            )
            cleanup = source_cleanup.cleanup_hint(
                self.root, self.store, self.scheduler, job, latest_row=row, assessment=base,
            )
        except Exception as error:  # noqa: BLE001 - one unreadable job must not break the Dashboard
            failed = {
                "eligible": False, "kind": None, "reason": f"Không kiểm tra được: {error}",
                "size_bytes": None, "output_name": None, "output_bytes": None,
                "exported_at": None, "skipped_at": None,
            }
            return failed, {**failed, "manifest_bytes": None}
        try:
            hint = source_archive.archive_hint(
                self.root, self.store, self.scheduler, job, cleanup_row=row, archive_row=archive, base=base,
            )
        except Exception as error:  # noqa: BLE001
            hint = {
                "eligible": False, "kind": None, "reason": f"Không kiểm tra được: {error}",
                "size_bytes": None, "output_name": None, "output_bytes": None, "manifest_bytes": None,
                "exported_at": None, "skipped_at": None,
            }
        return cleanup, hint

    def audit_running(self, job_id: int) -> bool:
        """An AI audit of this job is queued or running (it writes beside the review queue)."""
        lock = getattr(self, "_audit_lock", None)
        if lock is None:
            return False
        with lock:
            return int(job_id) in getattr(self, "_audit_jobs", {})

    def source_cleanup_preview(self, job_ids: Any) -> dict[str, Any]:
        """GET /api/source-cleanup/preview ("Xóa video gốc"): read-only (no hash, no write, no deleter)."""
        return source_cleanup.preview_cleanup(
            self.root, self.store, self.scheduler, source_cleanup.parse_job_ids(job_ids),
            audit_running=self.audit_running,
        )

    def source_cleanup_run(self, job_ids: Any, preview_id: Any, confirm_permanent: Any = None) -> dict[str, Any]:
        """POST /api/source-cleanup ("Xóa video gốc"): delete the confirmed sources for good.

        Each video's export manifest goes too (the .mp4 stays), then its job.
        Raises ValueError for a bad request (400; also without
        ``confirm_permanent: true``) and CleanupConflict when nothing may start
        (409). Stops between videos once BiliFlow shuts down.
        """
        if confirm_permanent is not True:
            raise ValueError(CONFIRM_PERMANENT_MESSAGE)
        if not isinstance(job_ids, list):
            raise ValueError(source_cleanup.JOB_IDS_MESSAGE)
        return source_cleanup.execute_cleanup(
            self.root, self.store, self.scheduler, job_ids, preview_id,
            deleter=self.source_deleter, audit_running=self.audit_running,
            should_stop=getattr(self, "_stopping", threading.Event()).is_set,
        )

    def job_delete_preview(self, job_ids: Any) -> dict[str, Any]:
        """GET /api/job-delete/preview ("Xóa video"): read-only; reads the Recycle Bin only for a
        source the old cleanup moved there without a known record."""
        return job_delete.preview_delete(
            self.root, self.store, self.scheduler, source_cleanup.parse_job_ids(job_ids),
            finder=self.record_finder, audit_running=self.audit_running,
        )

    def job_delete_run(self, job_ids: Any, preview_id: Any, confirm_permanent: Any = None) -> dict[str, Any]:
        """POST /api/job-delete ("Xóa video", "Dọn video mất gốc"): remove cancelled or lost videos.

        A cancelled video's source in input goes for good; output is never touched.
        ValueError (400) for a bad request or without ``confirm_permanent: true``,
        CleanupConflict (409) when nothing may start.
        """
        if confirm_permanent is not True:
            raise ValueError(CONFIRM_PERMANENT_MESSAGE)
        if not isinstance(job_ids, list):
            raise ValueError(source_cleanup.JOB_IDS_MESSAGE)
        return job_delete.execute_delete(
            self.root, self.store, self.scheduler, job_ids, preview_id,
            deleter=self.source_deleter, finder=self.record_finder, audit_running=self.audit_running,
            should_stop=getattr(self, "_stopping", threading.Event()).is_set,
        )

    def source_recycle_check(self, kind: Any, subject_id: Any) -> dict[str, Any]:
        """POST /api/source-recycle-check: read the bin again; only appends a recycle_checks row.

        ValueError (400) for a bad body, ActionConflict (409) when nothing was read or written.
        """
        return source_cleanup.recheck_recycle_record(
            self.root, self.store, kind, subject_id, finder=self.record_finder,
        )

    def source_archive_preview(self, job_ids: Any) -> dict[str, Any]:
        """GET /api/source-archive/preview: read-only (no hash, no write, no recycler)."""
        return source_archive.preview_archive(
            self.root, self.store, self.scheduler, source_archive.parse_job_ids(job_ids),
            bin_info=self.bin_info,
        )

    def source_archive_run(self, job_ids: Any, preview_id: Any) -> dict[str, Any]:
        """POST /api/source-archive: archive the confirmed sources and recycle their exports.

        ValueError (400) for a bad request, ActionConflict (409) when nothing may
        start. Stops between videos once BiliFlow shuts down.
        """
        if not isinstance(job_ids, list):
            raise ValueError(source_archive.JOB_IDS_MESSAGE)
        return source_archive.execute_archive(
            self.root, self.store, self.scheduler, job_ids, preview_id,
            recycler=self.export_recycler, bin_info=self.bin_info,
            should_stop=getattr(self, "_stopping", threading.Event()).is_set,
        )

    def restore_archived_source(self, job_id: Any) -> dict[str, Any]:
        """POST /api/source-archive/restore: rename the archived source back to input (409 when it cannot)."""
        return source_archive_restore.restore_archive(self.root, self.store, self.scheduler, job_id)

    def set_job_hidden(self, job_id: int, hidden: bool) -> dict[str, Any]:
        """POST /api/jobs/{id}/hide|unhide: list display only, for a cancelled job.

        Never touches reports, review decisions, settings or files. ActionConflict
        (409) 'not_cancelled', 'already_hidden' or 'not_hidden' writes nothing.
        """
        with self.scheduler.job_action_lock:
            self._refuse_hidden_change(self.store.get_job(job_id), hidden)
            value = self.store.set_job_hidden(job_id, hidden)
            if value is None:
                # The worker changed the job between the read and the conditional write.
                self._refuse_hidden_change(self.store.get_job(job_id), hidden)
                code, message = (
                    ("not_cancelled", HIDE_NOT_CANCELLED_MESSAGE) if hidden
                    else ("not_hidden", UNHIDE_NOT_HIDDEN_MESSAGE)
                )
                raise ActionConflict(code, message.format(job_id=job_id))
            self.store.add_event(
                job_id, "JOB_HIDDEN" if hidden else "JOB_UNHIDDEN",
                JOB_HIDDEN_MESSAGE if hidden else JOB_UNHIDDEN_MESSAGE,
            )
        return value

    @staticmethod
    def _refuse_hidden_change(job: dict[str, Any], hidden: bool) -> None:
        job_id = job["id"]
        if hidden and job["state"] != "CANCELLED":
            raise ActionConflict("not_cancelled", HIDE_NOT_CANCELLED_MESSAGE.format(job_id=job_id))
        if hidden and job.get("hidden_at"):
            raise ActionConflict("already_hidden", HIDE_ALREADY_MESSAGE.format(job_id=job_id))
        if not hidden and not job.get("hidden_at"):
            raise ActionConflict("not_hidden", UNHIDE_NOT_HIDDEN_MESSAGE.format(job_id=job_id))

    def review_summary_for(self, job: dict[str, Any]) -> dict[str, Any] | None:
        """Counts of the active review queue, only for jobs whose card needs them."""
        if job.get("state") not in REVIEW_SUMMARY_STATES or not job.get("active_queue_path"):
            return None
        try:
            path = _inside(self.root / "reports", self.root / str(job["active_queue_path"]))
        except (OSError, ValueError):
            return None
        summary = _cached_review_summary(path)
        if summary is not None and summary.get("skip_eligible") and not self._skip_state_allowed(job):
            # The card offers "Bỏ qua" only when skip_export would accept it.
            summary["skip_eligible"] = False
        return summary

    def _skip_state_allowed(self, job: dict[str, Any]) -> bool:
        """The job-state half of skip_export's guards (the queue half is skip_refusal)."""
        return (
            job.get("state") == "READY_TO_EXPORT"
            and not self._export_in_flight(job)
            and not self.scheduler.is_busy(int(job["id"]))
        )

    def render_progress_summary(self, job: dict[str, Any]) -> dict[str, Any] | None:
        if job.get("state") != "RENDERING":
            return None
        render = self.store.setting(f"render:{int(job['id'])}")
        if not isinstance(render, dict) or not render.get("output"):
            return {"state": "STARTING", "percent": 0.0}
        expected = float(job.get("duration_seconds") or 0.0)
        plan_value = render.get("plan")
        if plan_value:
            try:
                plan = _read_json(self.root / str(plan_value))
                expected = expected_output_duration(
                    operations=list(plan.get("approved_operations") or []),
                    duration=float(plan.get("source", {}).get("duration_seconds") or expected),
                )
            except (OSError, TypeError, ValueError):
                pass
        output = self.root / str(render["output"])
        return read_render_progress(
            render_progress_path(self.root, output),
            expected_duration_seconds=max(0.001, expected),
        ) or {"state": "STARTING", "percent": 0.0}

    def structure_audit_summary(self, job_id: int) -> dict[str, Any] | None:
        job = self.store.get_job(job_id)
        active_queue = str(job.get("active_queue_path") or "")
        if not active_queue:
            return None
        active_parent = Path(active_queue).parent.as_posix().casefold()
        artifacts = [
            item for item in self.store.artifacts(job_id)
            if item["kind"] == "structure_audit" and item["status"] == "VALID"
            and Path(str(item["path"])).parent.as_posix().casefold()
            == active_parent
        ]
        if not artifacts:
            return None
        try:
            payload = _read_json(self.root / artifacts[-1]["path"])
        except (OSError, ValueError):
            return None
        outdated = stored_coverage_block_is_outdated(payload)
        first_finding = next(
            (str(value) for value in payload.get("findings") or [] if value), None,
        )
        if outdated:
            first_finding = (
                "Kết quả này lưu theo quy tắc cũ: queue không thiếu ứng viên, chỉ detector "
                "chưa xét hết. Chạy lại kiểm tra cấu trúc cục bộ để cập nhật (thường thành WARN)."
            )
        return {
            "state": "COMPLETED",
            "result": payload.get("result"),
            "summary": payload.get("summary"),
            "first_finding": first_finding,
            "outdated_rule": outdated,
            "updated_at": payload.get("created_at"),
            "uses_chatgpt_quota": False,
        }

    def ai_audit_summary(self, job_id: int) -> dict[str, Any] | None:
        job = self.store.get_job(job_id)
        active_queue = str(job.get("active_queue_path") or "")
        if not active_queue:
            return None
        active_queue_key = Path(active_queue).as_posix().casefold()
        with self._audit_lock:
            if self._audit_jobs.get(job_id, "").casefold() == active_queue_key:
                return {"state": "RUNNING", "message": "AI Supervisor đang kiểm tra"}
        active_revision = next(
            (
                revision for revision in self.store.revisions(job_id)
                if int(revision["revision"]) == int(job.get("active_revision") or 0)
            ),
            None,
        )

        def event_matches_active_queue(event: dict[str, Any]) -> bool:
            payload = event.get("payload") or {}
            scoped_queue = str(payload.get("queue_path") or "")
            if scoped_queue:
                return Path(scoped_queue).as_posix().casefold() == active_queue_key
            # Backward compatibility for events created before queue scoping.
            return bool(
                active_revision
                and str(event.get("created_at") or "")
                >= str(active_revision.get("created_at") or "")
            )

        latest_event = next(
            (
                event for event in self.store.events(job_id, limit=50)
                if str(event.get("event_type", "")).startswith("AI_AUDIT_")
                and event_matches_active_queue(event)
            ),
            None,
        )
        if latest_event and latest_event["event_type"] == "AI_AUDIT_FAILED":
            return {
                "state": "FAILED", "message": latest_event["message"],
                "updated_at": latest_event["created_at"],
            }
        active_parent = Path(active_queue).parent.as_posix().casefold()
        artifacts = [
            item for item in self.store.artifacts(job_id)
            if item["kind"] == "ai_audit" and item["status"] == "VALID"
            and Path(str(item["path"])).parent.as_posix().casefold() == active_parent
        ]
        if artifacts:
            path = self.root / artifacts[-1]["path"]
            try:
                payload = _read_json(path)
                return {
                    "state": "COMPLETED", "result": payload.get("result"),
                    "summary": payload.get("summary"),
                    "updated_at": payload.get("created_at"),
                }
            except (OSError, ValueError):
                pass
        if latest_event and latest_event["event_type"] == "AI_AUDIT_QUEUED":
            return {
                "state": "QUEUED", "message": "AI Supervisor đang chờ chạy",
                "updated_at": latest_event["created_at"],
            }
        return None

    def queue_path(self, job_id: int) -> Path:
        job = self.store.get_job(job_id)
        value = job.get("active_queue_path")
        if not value:
            raise ValueError("Job has no review queue")
        return _inside(self.root / "reports", self.root / value)

    def media_key(self, job_id: int) -> str:
        """Per-job key for review media URLs (<img>/<video> cannot send headers)."""
        return hmac.new(
            self.token.encode("utf-8"), f"review-media:{int(job_id)}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def media_key_valid(self, job_id: int, key: str | None) -> bool:
        if not key:
            return False
        return hmac.compare_digest(
            str(key).encode("utf-8"), self.media_key(job_id).encode("utf-8"),
        )

    def review_queue(self, job_id: int) -> dict[str, Any]:
        """Active queue of a job, read-only (callers must not mutate it)."""
        try:
            with _REVIEW_QUEUE_IO:
                return read_json_cached(self.queue_path(job_id))
        except KeyError as error:
            raise ReviewMediaError(404, f"Unknown job: {job_id}") from error
        except (OSError, ValueError) as error:
            raise ReviewMediaError(404, "Job has no readable review queue") from error

    def review_source(
        self, job_id: int, queue: dict[str, Any],
    ) -> tuple[dict[str, Any], Path]:
        """The job's own source video, verified against the queue and its recorded stat.

        The path never comes from the request: it is the job row's path, and it
        must match the queue source and the size/mtime recorded at import.
        A source cleaned into the Recycle Bin (or on its way there) answers 410,
        so no new stream or frame opens it while the shell moves it. An
        archived source (batch 4) answers 404: the review page then shows the
        source as missing and uses the report previews.
        """
        job = self.store.get_job(job_id)
        lock = self.store.source_lock(job_id)
        if lock == "cleaned":
            raise ReviewMediaError(410, SOURCE_CLEANED_MEDIA_MESSAGE)
        if lock == "archived":
            raise ReviewMediaError(404, SOURCE_ARCHIVED_MEDIA_MESSAGE)
        recorded = Path(str(job["source_path"]))
        source = queue.get("source") or {}

        def normal(value: str) -> str:
            return os.path.normcase(os.path.abspath(value))

        if (
            not source.get("path")
            or normal(str(source["path"])) != normal(str(recorded))
            or str(source.get("sha256") or "").casefold()
            != str(job["source_sha256"]).casefold()
        ):
            raise ReviewMediaError(409, "Review queue does not match this job's source video")
        try:
            stat = recorded.stat()
        except OSError as error:
            raise ReviewMediaError(404, "Source video is missing") from error
        if not recorded.is_file():
            raise ReviewMediaError(404, "Source video is missing")
        if (
            stat.st_size != int(job["source_size_bytes"])
            or stat.st_mtime_ns != int(job["source_mtime_ns"])
        ):
            raise ReviewMediaError(409, "Source video changed since it was scanned")
        return job, recorded

    def review_evidence(self, job_id: int, item_id: str) -> dict[str, Any]:
        queue = self.review_queue(job_id)
        try:
            evidence = item_evidence(self.root, queue, item_id)
        except KeyError as error:
            raise ReviewMediaError(404, f"Unknown review item: {item_id}") from error
        try:
            _job, source = self.review_source(job_id, queue)
            mime = VIDEO_MIME_TYPES.get(source.suffix.casefold())
            evidence["video"] = (
                {"available": True, "mime": mime, "reason": None} if mime
                else {"available": False, "mime": None, "reason": "unsupported_container"}
            )
        except (KeyError, ReviewMediaError) as error:
            status = getattr(error, "status", 404)
            evidence["video"] = {
                "available": False, "mime": None,
                "reason": (
                    "source_cleaned" if status == 410
                    else "source_changed" if status == 409 else "source_missing"
                ),
            }
        return evidence

    def review_frame(self, job_id: int, item_id: str, seconds: Any) -> Path:
        queue = self.review_queue(job_id)
        try:
            evidence = item_evidence(self.root, queue, item_id)
        except KeyError as error:
            raise ReviewMediaError(404, f"Unknown review item: {item_id}") from error
        try:
            timestamp = strip_time(evidence, seconds)
        except ValueError as error:
            raise ReviewMediaError(400, str(error)) from error
        job, source = self.review_source(job_id, queue)
        return self.frame_cache.frame(source, str(job["source_sha256"]), timestamp)

    def review_video(self, job_id: int) -> tuple[Path, str]:
        queue = self.review_queue(job_id)
        _job, source = self.review_source(job_id, queue)
        mime = VIDEO_MIME_TYPES.get(source.suffix.casefold())
        if mime is None:
            raise ReviewMediaError(
                415, "The browser cannot play this container; use the frame strip",
            )
        return source, mime

    def sync_queue_state(self, job_id: int, queue: dict[str, Any]) -> None:
        """Follow a review decision: READY_TO_EXPORT when fully decided, else WAITING_REVIEW.

        An export waiting or running and any in-process job keep their state.
        A skipped job stays skipped while its review still allows a skip;
        otherwise it goes back to the review flow and the skip record ends.
        A job that moves retires an old export request (a paused, failed or
        interrupted render stage) so Tiếp tục can never render the old plan.
        """
        state = "READY_TO_EXPORT" if queue.get("status") == "READY_FOR_EDIT_PLAN" else "WAITING_REVIEW"
        job = self.store.get_job(job_id)
        if job["state"] == "SKIPPED":
            reason = skip_refusal(review_summary(queue))
            if reason is None:
                return
            if self.store.update_job_if(job_id, states={"SKIPPED"}, state=state, error=None) is None:
                return
            record = self.store.setting(f"skip:{job_id}")
            self.store.set_setting(f"skip:{job_id}", None)
            self.store.add_event(
                job_id, "JOB_SKIP_SUPERSEDED",
                f"Bỏ đánh dấu bỏ qua vì quyết định duyệt đã đổi: {reason}",
                payload={"state": state, "queue_status": queue.get("status"), "skip": record},
            )
            return
        value = self.store.update_job_if(job_id, exclude=SYNC_KEEP_STATES, state=state, error=None)
        if value is not None:
            # Reentrant: the review routes already hold job_action_lock.
            self.scheduler.retire_render_request(job_id, "review_changed", clear_current_stage=True)

    def _export_in_flight(self, job: dict[str, Any]) -> bool:
        """True while the job's export waits in the queue or runs (export_guards.render_in_flight)."""
        job_id = int(job["id"])
        pending = (
            (self.store.next_pending_stage(job_id) or {}).get("name")
            if job["state"] == "QUEUED" else None
        )
        return render_in_flight(job, pending, worker_busy=self.scheduler.is_busy(job_id))

    def ensure_review_editable(self, job_id: int) -> None:
        """Refuse a decision edit while an export waits or runs, or once the source is cleaned.

        The render uses the plan fixed at finalize, so an edit then would not
        reach the output. A video whose source is in the Recycle Bin (or on
        its way there) is read-only until the watcher sees the same file back
        in input (product default, batch 3); an archived one until "Khôi phục
        bản xuất" (batch 4). Call it inside _REVIEW_QUEUE_IO and the
        scheduler's job_action_lock, before the queue is written.
        """
        if self._export_in_flight(self.store.get_job(job_id)):
            raise ValueError(REVIEW_EDIT_IN_FLIGHT_MESSAGE)
        lock = self.store.source_lock(job_id)
        if lock == "cleaned":
            raise ValueError(SOURCE_CLEANED_REVIEW_REFUSAL)
        if lock == "archived":
            raise ValueError(SOURCE_ARCHIVED_REVIEW_REFUSAL)

    def finalize(
        self, job_id: int, *, size_mode: str = "default",
        max_output_gb: object = None,
    ) -> dict[str, Any]:
        # Every guard runs before anything is written (queue policy, plan, render).
        with _REVIEW_QUEUE_IO, self.scheduler.job_action_lock:
            job = self.store.get_job(job_id)
            # Skipped, export in flight, then queued or busy.
            refusal = export_state_refusal(
                job, in_flight=self._export_in_flight(job),
                worker_busy=self.scheduler.is_busy(job_id),
            )
            if refusal:
                raise ValueError(refusal)
            queue_path = self.queue_path(job_id)
            queue = _read_json(queue_path)
            if queue.get("status") != "READY_FOR_EDIT_PLAN":
                raise ValueError(QUEUE_NOT_READY_MESSAGE)
            # Source cleaned into the Recycle Bin or archived, then source missing.
            lock = self.store.source_lock(job_id)
            refusal = export_source_refusal(
                Path(str(job["source_path"])), cleaned=lock == "cleaned", archived=lock == "archived",
            )
            if refusal:
                raise ValueError(refusal)
            policy = normalize_output_size_policy(size_mode, max_output_gb)
            planned = {**queue, "export_size_policy": policy}
            plan_path, output_path, _ = review_export_paths(self.root, planned)
            existing = existing_review_export(self.root, planned)
            if existing is None and os.path.lexists(output_path):
                # A file (or a link, even a broken one) at the path this render
                # would write that no manifest proves: neither the export nor overwritten.
                raise ValueError(EXPORT_PATH_TAKEN_MESSAGE.format(name=output_path.name))
            queue["export_size_policy"] = policy
            _write_json(queue_path, queue)
            if existing is not None:
                # The export of this review already exists (proven by its
                # manifest): no render, and an old request left by a paused or
                # failed export is retired.
                self.store.update_job(
                    job_id, state="COMPLETED", progress=1.0, current_stage=None, stop_mode=None,
                )
                self.scheduler.retire_render_request(
                    job_id, "output_exists", clear_current_stage=True,
                )
                return {
                    "status": "COMPLETED",
                    "output": existing[0].relative_to(self.root).as_posix(),
                    "export_size_policy": policy,
                }
            plan = build_edit_plan(project_root=self.root, queue_path=queue_path, plan_path=plan_path)
            if plan.get("status") == "READY_FOR_PREVIEW":
                authorize_final_from_resolved_review(
                    project_root=self.root, plan_path=plan_path, actor="control_center_user"
                )
            self.scheduler.queue_render(
                job_id, plan_path=plan_path, output_path=output_path,
                max_output_bytes=policy["maximum_output_bytes"],
                target_output_bytes=policy["target_output_bytes"],
            )
            return {
                "status": "QUEUED", "plan": plan_path.relative_to(self.root).as_posix(),
                "output": output_path.relative_to(self.root).as_posix(),
                "export_size_policy": policy,
            }

    def _refuse_source_lock(self, job_id: int) -> None:
        """ValueError when the job's source is cleaned into the Recycle Bin or archived."""
        lock = self.store.source_lock(job_id)
        if lock == "cleaned":
            raise ValueError(SOURCE_CLEANED_MESSAGE)
        if lock == "archived":
            raise ValueError(SOURCE_ARCHIVED_MESSAGE)

    def skip_export(self, job_id: int) -> dict[str, Any]:
        """Bỏ qua (không xuất): mark a reviewed video done without an export.

        Writes only the job state, the skip:{id} setting, its events and the
        retirement of an old render stage; the queue, decisions, reports,
        source and outputs are never touched.
        """
        with _REVIEW_QUEUE_IO, self.scheduler.job_action_lock:
            # A cleaned or archived video stays in "Hoàn tất" as it is.
            self._refuse_source_lock(job_id)
            job = self.store.get_job(job_id)
            if job["state"] == "SKIPPED":
                raise ValueError(f"Video #{job_id} đã được đánh dấu bỏ qua.")
            # An old export request (a render stage a paused, failed or
            # interrupted export left behind) is not in flight: only a QUEUED or
            # RENDERING job exports. The skip retires that request below.
            if self._export_in_flight(job):
                raise ValueError(f"Video #{job_id} đang chờ xuất hoặc đang xuất; không thể bỏ qua.")
            if (
                job["state"] == "QUEUED" or job["state"] in IN_PROCESS_STATES
                or self.scheduler.is_busy(job_id)
            ):
                raise ValueError(f"Video #{job_id} đang trong hàng đợi hoặc đang được xử lý; không thể bỏ qua.")
            if job["state"] != "READY_TO_EXPORT":
                raise ValueError(
                    "Chỉ bỏ qua được video đã duyệt xong (Sẵn sàng xuất) mà không có cảnh chính "
                    "nào cần xử lý hoặc mọi cảnh chính đều Giữ nguyên."
                )
            try:
                queue = _read_json(self.queue_path(job_id))
            except (OSError, ValueError) as error:
                raise ValueError("Video chưa có danh sách duyệt đọc được; không thể bỏ qua.") from error
            summary = review_summary(queue)
            reason = skip_refusal(summary)
            if reason:
                raise ValueError(reason)
            record = {
                "queue_path": job.get("active_queue_path"),
                "revision": job.get("active_revision"),
                "main_items": summary["main_items"],
                "advisory_items": summary["advisory_items"],
                "decisions": summary["decisions"],
                "skipped_at": now_iso(),
                "actor": "control_center_user",
            }
            value = self.store.update_job_if(
                job_id, states={"READY_TO_EXPORT"}, state="SKIPPED", progress=1.0,
                current_stage=None, stop_mode=None, error=None,
            )
            if value is None:
                raise ValueError(f"Video #{job_id} vừa đổi trạng thái; tải lại Dashboard rồi thử lại.")
            self.store.set_setting(f"skip:{job_id}", record)
            self.store.add_event(
                job_id, "JOB_SKIPPED",
                "Đánh dấu xong mà không xuất video; video gốc, report và quyết định duyệt giữ nguyên",
                payload=record,
            )
            self.scheduler.retire_render_request(job_id, "skipped", clear_current_stage=True)
            return value

    def unskip_export(self, job_id: int) -> dict[str, Any]:
        """Mở lại để xuất: undo a skip; the job returns to its review state."""
        with _REVIEW_QUEUE_IO, self.scheduler.job_action_lock:
            # A cleaned or archived video stays in "Hoàn tất" as it is.
            self._refuse_source_lock(job_id)
            job = self.store.get_job(job_id)
            if job["state"] != "SKIPPED":
                raise ValueError(f"Video #{job_id} không ở trạng thái Đã bỏ qua.")
            try:
                ready = _read_json(self.queue_path(job_id)).get("status") == "READY_FOR_EDIT_PLAN"
            except (OSError, ValueError):
                ready = False
            state = "READY_TO_EXPORT" if ready else "WAITING_REVIEW"
            value = self.store.update_job_if(job_id, states={"SKIPPED"}, state=state, error=None)
            if value is None:
                raise ValueError(f"Video #{job_id} vừa đổi trạng thái; tải lại Dashboard rồi thử lại.")
            record = self.store.setting(f"skip:{job_id}")
            self.store.set_setting(f"skip:{job_id}", None)
            self.store.add_event(
                job_id, "JOB_UNSKIPPED", "Mở lại để xuất video",
                payload={"state": state, "skip": record},
            )
            return value

    def start_ai_audit(self, job_id: int, *, visual_opt_in: bool = False) -> None:
        if visual_opt_in:
            connection = self.ai_status()
            if not connection["ready"]:
                raise ValueError(connection["message"])
        # Under job_action_lock like "Xóa video gốc"/"Xóa video": a removal either
        # sees this audit in its locked re-check, or has finished and get_job fails.
        with self.scheduler.job_action_lock:
            job = self.store.get_job(job_id)
            queue_path = self.queue_path(job_id)
            queue_relative = queue_path.relative_to(self.root).as_posix()
            with self._audit_lock:
                if job_id in self._audit_jobs:
                    raise ValueError("AI Supervisor is already auditing this job")
                self._audit_jobs[job_id] = queue_relative
        audit_label = "Visual AI Audit" if visual_opt_in else "Local structure audit"
        event_scope = {"visual_opt_in": visual_opt_in, "queue_path": queue_relative}
        self.store.add_event(
            job_id, "AI_AUDIT_QUEUED", f"{audit_label} requested",
            payload=event_scope,
        )

        def worker() -> None:
            try:
                if visual_opt_in:
                    with self._ai_session_lock:
                        shared_thread_id = self.store.setting(
                            "ai_supervisor_thread_id"
                        )
                        config = load_ai_config(self.root)
                        with _REVIEW_QUEUE_IO:
                            queue_payload = _read_json(queue_path)
                        evidence = collect_visual_evidence(
                            self.root, queue_payload,
                            max_images=int(config["max_visual_images"]),
                            max_images_per_item=int(
                                config["max_visual_images_per_item"]
                            ),
                        )
                        batch_size = int(config["visual_batch_size"])
                        batches = []
                        for offset in range(0, len(evidence), batch_size):
                            batch = run_ai_audit(
                                root=self.root, job=job,
                                queue_path=queue_path,
                                thread_id=shared_thread_id,
                                cancel_event=self._audit_cancel,
                                visual_opt_in=True,
                                visual_batch_offset=offset,
                                visual_batch_limit=batch_size,
                            )
                            batches.append(batch)
                            shared_thread_id = (
                                batch.get("_thread_id") or shared_thread_id
                            )
                            if shared_thread_id:
                                self.store.set_setting(
                                    "ai_supervisor_thread_id",
                                    shared_thread_id,
                                )
                        payload = _merge_visual_audit_batches(
                            batches,
                            expected_assessment_count=len({
                                item["item_id"] for item in evidence
                            }),
                        )
                else:
                    payload = run_local_queue_audit(
                        root=self.root, job=job, queue_path=queue_path,
                    )
                thread_id = payload.pop("_thread_id", None)
                if thread_id:
                    self.store.set_setting("ai_supervisor_thread_id", thread_id)
                path = queue_path.parent / (
                    "ai-audit.json" if visual_opt_in
                    else "structure-audit.json"
                )
                payload["created_at"] = now_iso()
                _write_json(path, payload)
                if visual_opt_in:
                    with _REVIEW_QUEUE_IO:
                        apply_visual_ai_assessments(
                            project_root=self.root,
                            queue_path=queue_path,
                            audit_payload=payload,
                        )
                self.store.add_artifact(
                    job_id,
                    stage_name="ai_audit" if visual_opt_in else "build_review",
                    kind="ai_audit" if visual_opt_in else "structure_audit",
                    path=path.relative_to(self.root).as_posix(),
                    bytes_count=path.stat().st_size,
                )
                self.store.add_event(
                    job_id,
                    "AI_AUDIT_COMPLETED" if visual_opt_in
                    else "STRUCTURE_AUDIT_COMPLETED",
                    (
                        "AI Supervisor" if visual_opt_in
                        else "Local structure audit"
                    ) + f": {payload['result']} — {payload['summary']}",
                    payload=event_scope,
                )
            except Exception as error:
                self.store.add_event(
                    job_id, "AI_AUDIT_FAILED", str(error), level="ERROR",
                    payload=event_scope,
                )
            finally:
                with self._audit_lock:
                    self._audit_jobs.pop(job_id, None)
                    self._audit_threads.discard(threading.current_thread())

        thread = threading.Thread(
            target=worker, name=f"biliflow-ai-audit-{job_id}", daemon=False,
        )
        with self._audit_lock:
            self._audit_threads.add(thread)
        thread.start()

    def ai_status(self) -> dict[str, Any]:
        login_running = bool(
            self._login_process is not None and self._login_process.poll() is None
        )
        if self._login_process is not None and not login_running:
            if self._login_log_handle and not self._login_log_handle.closed:
                self._login_log_handle.close()
            self._login_process = None
            self._login_log_handle = None
        value = codex_connection_status(self.root)
        value["login_running"] = login_running
        value["session"] = {
            "mode": "shared_ai_supervisor_thread",
            "active": bool(self.store.setting("ai_supervisor_thread_id")),
        }
        return value

    def update_ai_config(self, body: dict[str, Any]) -> dict[str, Any]:
        old = load_ai_config(self.root)
        config = save_ai_config(self.root, {
            "enabled": bool(body.get("enabled", old["enabled"])),
            "model": str(body.get("model", old["model"])),
            "reasoning_effort": str(body.get("reasoning_effort", old["reasoning_effort"])),
        })
        self.store.add_event(
            None, "AI_CONFIG_UPDATED",
            f"AI Supervisor pinned to {config['model']} / {config['reasoning_effort']}",
        )
        return self.ai_status()

    def start_ai_login(self) -> dict[str, Any]:
        if self._login_process is not None and self._login_process.poll() is None:
            raise ValueError("Codex login is already running")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = self.root / "logs" / "control-center" / f"codex-login-{stamp}.log"
        self._login_process, self._login_log_handle = start_codex_login(
            self.root, log_path
        )
        self.store.add_event(
            None, "AI_LOGIN_STARTED", "Opened Codex ChatGPT login flow",
            payload={"log": log_path.relative_to(self.root).as_posix()},
        )
        value = codex_connection_status(self.root)
        value["login_running"] = True
        return value

    def stop_ai_login(self) -> None:
        process = self._login_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()
        if self._login_log_handle and not self._login_log_handle.closed:
            self._login_log_handle.close()
        self._login_process = None
        self._login_log_handle = None

    def stop_ai_audits(self) -> None:
        self._audit_cancel.set()
        with self._audit_lock:
            threads = list(self._audit_threads)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(12)

    def serve(self) -> None:
        require_loopback_host(self.host)
        try:
            self.server = ThreadingHTTPServer((loopback_bind_address(self.host), self.port), _handler_class(self))
            actual_port = self.server.server_port
            state = {"schema_version": 1, "pid": os.getpid(), "host": self.host,
                     "port": actual_port, "url": f"http://{self.host}:{actual_port}/",
                     "token": self.token, "started_at": now_iso()}
            _write_json(self.root / "state" / "control-center.json", state)
            self.scheduler.start()
            self.watcher.start()
            if getattr(self, "downloads", None) is not None:
                self.downloads.start()  # recovery, then the worker; an error shows on #downloads
            print(f"BiliFlow Control Center: {state['url']}", flush=True)
            self.server.serve_forever(poll_interval=0.5)
        finally:
            # A normal API shutdown already performs cleanup on its helper
            # thread. This branch handles Ctrl+C/startup failures without
            # calling HTTPServer.shutdown from the serve_forever thread.
            if not self._stopping.is_set():
                self._stopping.set()
                if getattr(self, "phone", None) is not None:
                    self.phone.disable("stopped")
                if getattr(self, "tailscale", None) is not None:
                    self.tailscale.close()  # a waiting `tailscale login` ends with the Control Center
                self.stop_ai_audits()
                self.stop_ai_login()
                self.watcher.shutdown()
                self.stop_downloads()
                self.scheduler.shutdown(immediate=True)
                if self.server:
                    self.server.server_close()
                state_path = self.root / "state" / "control-center.json"
                if state_path.exists():
                    try:
                        state_path.unlink()
                    except OSError:
                        pass
                # A running cleanup stops between videos (_stopping); let the
                # current one settle its row before the store closes.
                source_cleanup.wait_idle(timeout=90.0)
                self.store.close()
                self.lock.close()
            else:
                # /api/shutdown runs stop() on a daemon thread, and its
                # server.shutdown() is what ended serve_forever above. Keep
                # this (main) thread alive until stop() has waited for a
                # running cleanup and closed the store; otherwise the process
                # exits and kills that thread, and the recycle thread with it.
                stopped = getattr(self, "_stopped", None)
                if stopped is not None:
                    stopped.wait(timeout=STOP_WAIT_SECONDS)

    def stop(self, *, immediate: bool = False) -> None:
        if self._stopping.is_set():
            return
        self._stopping.set()
        if getattr(self, "phone", None) is not None:
            self.phone.disable("stopped")  # the phone code dies with the Control Center
        if getattr(self, "tailscale", None) is not None:
            self.tailscale.close()  # Tailscale itself keeps running; only BiliFlow's helper stops
        try:
            self.stop_ai_audits()
            self.stop_ai_login()
            self.watcher.shutdown()
            self.stop_downloads()
            self.scheduler.shutdown(immediate=immediate)
            if self.server:
                self.server.shutdown()
                self.server.server_close()
        finally:
            try:
                state_path = self.root / "state" / "control-center.json"
                if state_path.exists():
                    try:
                        state_path.unlink()
                    except OSError:
                        pass
                # A running cleanup stops between videos (_stopping); let the
                # current one settle its row before the store closes.
                source_cleanup.wait_idle(timeout=90.0)
                self.store.close()
                self.lock.close()
            finally:
                stopped = getattr(self, "_stopped", None)
                if stopped is not None:
                    stopped.set()


def _handler_class(center: ControlCenter) -> type[BaseHTTPRequestHandler]:
    """HTTP handler bound to one Control Center; module level so tests can bind port 0."""

    class Handler(BaseHTTPRequestHandler):
        # A client that stops sending its request (headers or body) cannot hold
        # a thread: StreamRequestHandler sets this on the socket. stream_video
        # lifts it while a video streams.
        timeout = REQUEST_TIMEOUT_SECONDS

        def log_message(self, format: str, *args) -> None:
            return

        def end_headers(self) -> None:
            # Every response (pages, JSON, streamed media, refusals, send_error): no other site
            # may frame this Control Center; one of its own pages still may (SAMEORIGIN).
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
            super().end_headers()

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

        def redirect(self, location: str, status: int = 303) -> None:
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def body(self) -> dict[str, Any]:
            length = content_length(self.headers)
            if length > 65536:
                raise ValueError("Request is too large")
            value = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value

        def authorized(self) -> bool:
            return self.headers.get("X-BiliFlow-Token") == center.token

        def drain_body(self) -> None:
            # A refused POST still reads its small body: closing a socket with
            # unread bytes makes Windows reset it before the client reads the 403.
            try:
                length = content_length(self.headers)
            except ValueError:
                return
            if 0 < length <= 65536:
                self.rfile.read(length)

        def stream_video(self, source: Path, mime: str) -> None:
            """Stream without the request timeout: a paused player stops reading for minutes."""
            self.connection.settimeout(None)
            try:
                stream_file(self, source, mime, getattr(center, "_stopping", None))
            finally:
                with contextlib.suppress(OSError):
                    self.connection.settimeout(self.timeout)

        def request_timed_out(self) -> None:
            """The request body stopped arriving: answer 408 if the client still reads, then close."""
            self.close_connection = True
            with contextlib.suppress(OSError):
                self.send_json(408, {"error": REQUEST_TIMEOUT_MESSAGE})

        def host_allowed(self, *, drain: bool = False) -> bool:
            if _host_allowed(self.headers.get("Host"), getattr(center, "host", None)):
                return True
            if drain:
                self.drain_body()
            self.send_json(403, {"error": "Địa chỉ truy cập không hợp lệ"})
            return False

        def review_media(self, job_id: int, kind: str, query: str) -> None:
            params = urllib.parse.parse_qs(query, keep_blank_values=True)

            def param(name: str) -> str:
                return (params.get(name) or [""])[0]

            try:
                if kind == "evidence":
                    if not param("item"):
                        raise ReviewMediaError(400, "item is required")
                    self.send_json(200, center.review_evidence(job_id, param("item")))
                    return
                if not center.media_key_valid(job_id, param("k")):
                    raise ReviewMediaError(403, "Khóa xem media không hợp lệ")
                if kind == "frame":
                    if not param("item") or not param("t"):
                        raise ReviewMediaError(400, "item and t are required")
                    target = center.review_frame(job_id, param("item"), param("t"))
                    self.send_bytes(200, target.read_bytes(), "image/jpeg")
                else:
                    source, mime = center.review_video(job_id)
                    self.stream_video(source, mime)
            except ReviewMediaError as error:
                self.send_json(error.status, {"error": str(error)})
            except KeyError as error:
                self.send_json(404, {"error": str(error)})
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            except Exception as error:
                self.send_json(500, {"error": str(error)})

        def parsed_path(self, *, drain: bool = False) -> Any:
            """urlparse of the request path; a malformed one (e.g. ``http://[x/``) answers 400 (H1)."""
            try:
                return urllib.parse.urlparse(self.path)
            except ValueError:
                if drain:
                    self.drain_body()
                self.close_connection = True
                self.send_json(400, {"error": "Đường dẫn không hợp lệ"})
                return None

        def loopback_client(self) -> bool:
            """The request came over the 127.0.0.1 listener (never true on the phone listener)."""
            return not getattr(self, "phone_listener", False) and self.client_address[0] == "127.0.0.1"

        def dashboard_v2(self, path: str) -> None:
            """GET /dashboard-v2/ (live.html) and its whitelisted assets; read-only."""
            if path == "/dashboard-v2":
                # Relative asset URLs need the trailing slash (base-uri 'none' forbids <base>).
                self.redirect("/dashboard-v2/", 301)
                return
            name = path.removeprefix("/dashboard-v2/") or DASHBOARD_V2_PAGE
            if name != DASHBOARD_V2_PAGE and name not in DASHBOARD_V2_FILES:
                self.send_json(404, {"error": "Không tìm thấy"})
                return
            target = DASHBOARD_V2_DIR / name
            if not target.is_file():
                self.send_json(404, {"error": "Không tìm thấy"})
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", DASHBOARD_V2_TYPES[target.suffix])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if name == DASHBOARD_V2_PAGE:
                self.send_header("Content-Security-Policy", DASHBOARD_V2_CSP)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if not self.host_allowed():
                return
            parsed = self.parsed_path()
            if parsed is None:
                return
            path = parsed.path
            try:
                if match := re.fullmatch(r"/api/jobs/(\d+)/review/(evidence|frame|video)", path):
                    self.review_media(int(match.group(1)), match.group(2), parsed.query)
                elif path == "/" and not CLASSIC_DASHBOARD:
                    self.redirect("/dashboard-v2/")
                elif path == "/":
                    # Byte for byte the classic page, plus one notice line while the phone mode is on (H3).
                    self.send_bytes(200, _with_phone_notice(_dashboard_html(), center).encode(),
                                    "text/html; charset=utf-8")
                elif path == "/dashboard-v2" or path.startswith("/dashboard-v2/"):
                    self.dashboard_v2(path)
                elif path == "/api/phone-mode":
                    # PC listener only (the phone listener answers this path itself).
                    if not self.loopback_client():
                        self.send_json(403, {"error": phone_access.PC_ONLY_POSTS[path], "code": "pc_only"})
                    else:
                        self.send_json(200, {"remote": False, **_phone_access(center).status(include_secret=True)})
                elif path == "/api/tailscale":
                    # Dashboard V2 → Cài đặt → Tailscale; PC only (the phone listener refuses it first).
                    if not self.loopback_client():
                        self.send_json(403, {"error": phone_access.PC_ONLY_TAILSCALE, "code": "pc_only"})
                    else:
                        self.send_json(200, _tailscale(center).status())
                elif path == "/healthz":
                    self.send_json(200, {"status": "ok", "version": __version__})
                elif path == "/api/session":
                    self.send_json(200, {"token": center.token})
                elif path == "/api/status":
                    self.send_json(200, center.status())
                elif path == "/api/ai":
                    self.send_json(200, center.ai_status())
                elif path == "/api/jobs":
                    self.send_json(200, center.store.list_jobs())
                elif match := re.fullmatch(r"/api/jobs/(\d+)", path):
                    job_id = int(match.group(1))
                    self.send_json(200, {"job": center.store.get_job(job_id),
                                         "stages": center.store.stages(job_id),
                                         "revisions": center.store.revisions(job_id),
                                         "artifacts": center.store.artifacts(job_id),
                                         "events": center.store.events(job_id)})
                elif match := re.fullmatch(r"/review/(\d+)", path):
                    job_id = int(match.group(1))
                    prefix = f"/api/jobs/{job_id}/review"
                    html = _interactive_html(center.token).replace("'/api/", f"'{prefix}/")
                    # M4: opened from V2 → its back button returns to V2; otherwise byte for byte as before.
                    html = _review_back_to_v2(html, parsed.query)
                    self.send_bytes(200, html.encode(), "text/html; charset=utf-8")
                elif match := re.fullmatch(r"/api/jobs/(\d+)/review/(queue|session|resources|export)", path):
                    job_id, kind = int(match.group(1)), match.group(2)
                    if kind == "queue":
                        with _REVIEW_QUEUE_IO:
                            value = _read_json(center.queue_path(job_id))
                    elif kind == "session":
                        value = {"token": center.token, "media_key": center.media_key(job_id)}
                    elif kind == "resources":
                        with _REVIEW_QUEUE_IO:
                            value = review_resource_status(
                                project_root=center.root, queue_path=center.queue_path(job_id)
                            )
                    else:
                        job = center.store.get_job(job_id)
                        outputs = [x for x in center.store.artifacts(job_id) if x["kind"] == "final_output"]
                        value = {
                            "status": job["state"],
                            "output": outputs[-1]["path"] if outputs else None,
                            "error": job["error"] if job["state"] == "FAILED" else None,
                            "render_progress": center.render_progress_summary(job),
                            # The review page is read-only once the source is cleaned.
                            "source_cleaned": center.store.source_cleaned(job_id),
                            "source_name": Path(str(job["source_path"])).name,
                            "source_cleanup": source_cleanup.cleanup_row_summary(
                                center.store.latest_source_cleanup(job_id)
                            ),
                            # Batch 4: an archived source also makes the page read-only.
                            "source_archived": center.store.source_archived(job_id),
                            "source_archive": source_archive.archive_row_summary(
                                center.store.latest_source_archive(job_id)
                            ),
                        }
                    self.send_json(200, value)
                elif path.startswith("/media/"):
                    relative = urllib.parse.unquote(path.removeprefix("/media/"))
                    target = _inside(center.root / "reports", center.root / relative)
                    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                    self.send_bytes(200, target.read_bytes(), content_type)
                elif path == "/api/source-cleanup/preview":
                    # Read-only: no hash, no write, never the recycler. A bad id
                    # list is the client's error (400), not a missing page (404).
                    ids = urllib.parse.parse_qs(parsed.query).get("ids", [""])[0]
                    try:
                        value = center.source_cleanup_preview(ids)
                    except ValueError as error:
                        self.send_json(400, {"error": str(error)})
                    else:
                        self.send_json(200, value)
                elif path == "/api/source-archive/preview":
                    # Read-only like the cleanup preview: no hash, no write, never the recycler.
                    ids = urllib.parse.parse_qs(parsed.query).get("ids", [""])[0]
                    try:
                        value = center.source_archive_preview(ids)
                    except ValueError as error:
                        self.send_json(400, {"error": str(error)})
                    else:
                        self.send_json(200, value)
                elif path == "/api/job-delete/preview":
                    # "Xóa video": read-only like the cleanup preview (no hash, no write, no deleter).
                    ids = urllib.parse.parse_qs(parsed.query).get("ids", [""])[0]
                    try:
                        value = center.job_delete_preview(ids)
                    except ValueError as error:
                        self.send_json(400, {"error": str(error)})
                    else:
                        self.send_json(200, value)
                elif (download := _download_answer(center, "GET", path, parsed.query)) is not None:
                    # "Tải video" snapshot and task detail, "Dung lượng" (read-only).
                    self.send_json(*download)
                elif (logo_memory := logo_memory_admin.handle_get(center.root, path, parsed.query)) is not None:
                    # "Bộ nhớ logo" (batch 4a): GET /logo-memory, /api/logo-memory,
                    # /api/logo-memory/frame?key=&i= — read-only; errors come back as JSON.
                    self.send_bytes(*logo_memory)
                else:
                    self.send_json(404, {"error": "Không tìm thấy"})
            except (KeyError, ValueError, FileNotFoundError) as error:
                self.send_json(404, {"error": str(error)})
            except Exception as error:
                self.send_json(500, {"error": str(error)})

        def do_POST(self) -> None:
            if not self.host_allowed(drain=True):
                return
            parsed = self.parsed_path(drain=True)
            if parsed is None:
                return
            path = parsed.path
            if not self.authorized():
                self.drain_body()
                self.send_json(403, {"error": "Phiên Control Center không hợp lệ"})
                return
            try:
                body = self.body()
            except TimeoutError:
                self.request_timed_out()
                return
            except (ValueError, RecursionError) as error:
                # RecursionError: JSON nested too deep for json.loads.
                self.send_json(400, {"error": str(error)})
                return
            try:
                if path == "/api/scheduler":
                    center.store.set_setting("scheduler_paused", bool(body.get("paused")))
                    center.scheduler._wake.set()
                    result: Any = {"paused": bool(body.get("paused"))}
                elif path == "/api/ai/config":
                    result = center.update_ai_config(body)
                elif path == "/api/ai/login":
                    result = center.start_ai_login()
                elif path == "/api/ai/check":
                    result = center.ai_status()
                elif path == "/api/shutdown":
                    mode = body.get("mode", "after_stage")
                    result = {"status": "STOPPING", "mode": mode}
                    self.send_json(202, result)
                    threading.Thread(target=center.stop,
                                     kwargs={"immediate": mode == "immediate"}, daemon=True).start()
                    return
                elif path == "/api/source-cleanup":
                    # Host and token were checked above; the deleter is reached
                    # only through execute_cleanup's own checks.
                    result = center.source_cleanup_run(
                        body.get("job_ids"), body.get("preview_id"), body.get("confirm_permanent"),
                    )
                elif path == "/api/job-delete":
                    # "Xóa video": the deleter is reached only through execute_delete's own checks.
                    result = center.job_delete_run(
                        body.get("job_ids"), body.get("preview_id"), body.get("confirm_permanent"),
                    )
                elif path == "/api/source-recycle-check":
                    # Reads the Recycle Bin only; appends one recycle_checks row.
                    result = center.source_recycle_check(body.get("kind"), body.get("id"))
                elif path == "/api/source-archive":
                    # "Lưu trữ": the export recycler is reached only through execute_archive's checks.
                    result = center.source_archive_run(body.get("job_ids"), body.get("preview_id"))
                elif path == "/api/source-archive/restore":
                    result = center.restore_archived_source(body.get("job_id"))
                elif path == "/api/phone-mode":
                    # Turn the phone listener on or off without restarting; 127.0.0.1 + token only.
                    if not self.loopback_client():
                        self.send_json(403, {"error": phone_access.PC_ONLY_POSTS[path], "code": "pc_only"})
                        return
                    phone = _phone_access(center)
                    if "extend" in body:
                        # Question 15: "Gia hạn thêm 8 giờ" on the PC panel; same code, new deadline.
                        if body.get("extend") is not True:
                            raise ValueError("extend phải là true")
                        result = {"remote": False, **phone.extend()}
                        self.send_json(200, result)
                        return
                    enabled = body.get("enabled")
                    if not isinstance(enabled, bool):
                        raise ValueError("enabled phải là true hoặc false")
                    if enabled:
                        port = body.get("port", phone_access.DEFAULT_PORT)
                        # "wifi" (home Wi-Fi) or "tailscale" (outside home; the user's choice, 2026-10-06).
                        network = body.get("network", phone_access.DEFAULT_NETWORK)
                        result = phone.enable(lambda access: _phone_handler_class(center, access), port=port,
                                              network=network)
                    else:
                        result = phone.disable("user")
                    result = {"remote": False, **result}
                elif path == "/api/phone-mode/extend":
                    # "Gia hạn thêm 8 giờ" from the phone, only while it is open over Tailscale (the user's
                    # choice, 2026-10-06). The phone learns the new time, never the code.
                    phone = _phone_access(center)
                    if not self.loopback_client():
                        if phone.network != "tailscale":
                            self.send_json(403, {"error": phone_access.EXTEND_TAILSCALE_ONLY, "code": "pc_only"})
                            return
                        status = phone.extend(by=self.client_address[0])
                        result = {"remote": True, "enabled": status["enabled"], "network": status["network"],
                                  "expires_at": status["expires_at"], "added_seconds": status["added_seconds"]}
                    else:
                        result = {"remote": False, **phone.extend()}
                elif match := re.fullmatch(r"/api/tailscale/(install|start-service|firewall|login|up|down|logout|remote-on)", path):
                    # Install, sign in and drive Tailscale: 127.0.0.1 + token only; one task at a time.
                    if not self.loopback_client():
                        self.send_json(403, {"error": phone_access.PC_ONLY_TAILSCALE, "code": "pc_only"})
                        return
                    result = _tailscale(center).start(match.group(1))
                elif match := re.fullmatch(r"/api/jobs/(\d+)/(hide|unhide)", path):
                    result = center.set_job_hidden(int(match.group(1)), match.group(2) == "hide")
                elif match := re.fullmatch(r"/api/jobs/(\d+)/(start|resume|pause|stop-after-stage|cancel|retry|rerun|skip|unskip|ai-audit)", path):
                    job_id, action = int(match.group(1)), match.group(2)
                    if action == "start":
                        detectors = body.get("detectors")
                        if not isinstance(detectors, list):
                            raise ValueError("Hãy chọn ít nhất một nhóm cần kiểm tra")
                        result = center.scheduler.start_job(
                            job_id, content_style=str(body["content_style"]),
                            profile=str(body.get("profile", "careful")),
                            detector_groups=[str(value) for value in detectors],
                            ocr_recognition_batch_size=body.get("ocr_recognition_batch_size"),
                            fast_scan=body.get("fast_scan"),
                        )
                    elif action == "resume": result = center.scheduler.resume(job_id)
                    elif action == "pause": result = center.scheduler.pause_now(job_id)
                    elif action == "stop-after-stage": result = center.scheduler.stop_after_stage(job_id)
                    elif action == "cancel": result = center.scheduler.cancel(job_id)
                    elif action == "retry": result = center.scheduler.retry(job_id)
                    elif action == "skip": result = center.skip_export(job_id)
                    elif action == "unskip": result = center.unskip_export(job_id)
                    elif action == "rerun":
                        detectors = body.get("detectors")
                        result = center.scheduler.rerun(
                            job_id,
                            ocr_recognition_batch_size=body.get("ocr_recognition_batch_size"),
                            fast_scan=body.get("fast_scan"),
                            detector_groups=(
                                [str(value) for value in detectors]
                                if isinstance(detectors, list) else None
                            ),
                        )
                    else:
                        visual_opt_in = bool(body.get("visual", False))
                        center.start_ai_audit(
                            job_id, visual_opt_in=visual_opt_in
                        )
                        result = {
                            "status": "QUEUED",
                            "visual_opt_in": visual_opt_in,
                        }
                elif match := re.fullmatch(r"/api/jobs/(\d+)/review/(decision|clear|bulk-keep|bulk-accept|finalize)", path):
                    job_id, action = int(match.group(1)), match.group(2)
                    queue_path = center.queue_path(job_id)
                    with _REVIEW_QUEUE_IO, center.scheduler.job_action_lock:
                        if action != "finalize":
                            # Checked before any write: the queue stays byte-identical.
                            center.ensure_review_editable(job_id)
                        if action == "decision":
                            result = record_review_decision(
                                project_root=center.root, queue_path=queue_path,
                                item_id=str(body["id"]), decision=str(body["decision"]),
                                note=body.get("note"), full_frame=bool(body.get("full_frame", False)),
                                actor="control_center_user", transport="control_center",
                                remember_studio_logo=body.get("remember_studio_logo") is True,
                                remember_platform_logo=body.get("remember_platform_logo") is True)
                            center.sync_queue_state(job_id, result)
                        elif action == "clear":
                            result = clear_review_decision(
                                project_root=center.root, queue_path=queue_path,
                                item_id=str(body["id"]), actor="control_center_user",
                                transport="control_center")
                            center.sync_queue_state(job_id, result)
                        elif action == "bulk-keep":
                            result = bulk_keep_review_items(
                                project_root=center.root, queue_path=queue_path,
                                review_filter=str(body["filter"]), actor="control_center_user",
                                transport="control_center")
                            center.sync_queue_state(job_id, result)
                        elif action == "bulk-accept":
                            result = bulk_accept_suggested_decisions(
                                project_root=center.root, queue_path=queue_path,
                                review_filter=str(body["filter"]), actor="control_center_user",
                                transport="control_center")
                            center.sync_queue_state(job_id, result)
                        else:
                            result = center.finalize(
                                job_id,
                                size_mode=str(body.get("size_mode") or "default"),
                                max_output_gb=body.get("max_output_gb"),
                            )
                elif (download := _download_answer(center, "POST", path, body)) is not None:
                    # "Tải video" (download_api.POST_ROUTES). Host and token were checked above;
                    # the worker never touches input files other than its own new ones.
                    status, result = download
                    if status != 200:
                        self.send_json(status, result); return
                elif (logo_memory := logo_memory_admin.handle_post(center.root, path, body)) is not None:
                    # "Bộ nhớ logo" (batch 4a): POST /api/logo-memory/class {key, memory_class,
                    # platform?, expected_sha256} and /api/logo-memory/delete {key, expected_sha256}.
                    # Host and token were checked above; 409 = memory changed since the page loaded.
                    status, result = logo_memory
                    if status != 200:
                        self.send_json(status, result); return
                else:
                    self.send_json(404, {"error": "Không tìm thấy"}); return
                self.send_json(200, result)
            except ActionConflict as error:
                # Before the ValueError branch (ActionConflict, CleanupConflict
                # included, is not one, but the order keeps a 409 a 409): nothing
                # started, the dialog or the card shows why.
                self.send_json(409, {
                    "error": str(error), "code": error.code,
                    **({"preview": error.preview} if error.preview else {}),
                })
            except (KeyError, TypeError, ValueError) as error:
                self.send_json(400, {"error": str(error)})
            except Exception as error:
                self.send_json(500, {"error": str(error)})

    return Handler


def _phone_handler_class(center: ControlCenter, phone: phone_access.PhoneAccess) -> type[BaseHTTPRequestHandler]:
    """Handler of the phone listener: the Control Center handler behind the access-code gate.

    Every request needs the Host <ip>:<port> of the listener and the access-code cookie,
    except the code page itself (the code is typed, never taken from a link).
    Writes also need the session token (inherited do_POST),
    a same-origin Origin when one is sent, and are refused for the PC-only actions.
    """
    base = _handler_class(center)
    ENTRY_PATHS = ("/", "/dashboard-v2", "/dashboard-v2/", "/phone-login")
    V2 = "/dashboard-v2/"

    def page(*args: Any, **kwargs: Any) -> bytes:
        """The code page with the warning of the network this listener is on."""
        return _phone_page(*args, network=phone.network, **kwargs)

    class PhoneHandler(base):
        phone_listener = True
        # H1: a connection that has not shown the cookie is held at most this long.
        timeout = phone_access.GATE_TIMEOUT_SECONDS
        _cached_body: dict[str, Any] | None = None

        _gate: threading.Timer | None = None

        def setup(self) -> None:
            super().setup()
            # L1: a real deadline counted from the accept, not per read: a client that trickles one
            # byte every few seconds is still cut off GATE_TIMEOUT_SECONDS after connecting.
            request = self.request

            def cut() -> None:
                with contextlib.suppress(OSError):
                    request.shutdown(socket.SHUT_RDWR)

            self._gate = threading.Timer(phone_access.GATE_TIMEOUT_SECONDS, cut)
            self._gate.daemon = True
            self._gate.start()

        def finish(self) -> None:
            if self._gate is not None:
                self._gate.cancel()
            super().finish()

        def opened(self) -> None:
            """The cookie is valid: no deadline, the usual request timeout (video streams lift it)."""
            if self._gate is not None:
                self._gate.cancel()
            promote = getattr(self.server, "promote", None)
            if promote is not None:
                promote(self.request)  # M1: no longer counted against this device's cookieless limit
            self.timeout = REQUEST_TIMEOUT_SECONDS
            with contextlib.suppress(OSError):
                self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

        def refuse_first(self, status: int, payload: dict[str, Any]) -> None:
            """Answer before reading the body (H2); then drop what is left for at most 1 s and close."""
            self.close_connection = True
            self.send_json(status, payload)
            with contextlib.suppress(OSError, ValueError):
                self.wfile.flush()
                self.connection.settimeout(1.0)
                self.drain_body()

        def stream_video(self, source: Path, mime: str) -> None:
            """M1: a phone stream gets a finite write timeout (the PC stream keeps none); the player
            asks again with Range when it resumes."""
            self.connection.settimeout(phone_access.STREAM_WRITE_TIMEOUT_SECONDS)
            try:
                stream_file(self, source, mime, getattr(center, "_stopping", None))
            except OSError:
                self.close_connection = True  # a stalled or vanished reader: the connection is dropped
            finally:
                with contextlib.suppress(OSError):
                    self.connection.settimeout(self.timeout)

        def checked_body(self) -> dict[str, Any]:
            """M3: like Handler.body(), but a body shorter than Content-Length is a 400, never `{}`."""
            length = content_length(self.headers)
            if length > 65536:
                raise ValueError("Request is too large")
            data = self.rfile.read(length) if length else b""
            if len(data) != length:
                self.close_connection = True
                raise ValueError(phone_access.BODY_CUT_MESSAGE)
            value = json.loads(data or b"{}")
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value

        def body(self) -> dict[str, Any]:
            # ai-audit: the phone handler already read the body to check `visual`.
            if self._cached_body is not None:
                return self._cached_body
            return self.checked_body()

        def host_allowed(self, *, drain: bool = False) -> bool:
            if phone.host_ok(self.headers.get("Host")):
                return True
            if drain:
                self.drain_body()
            self.send_json(403, {"error": "Địa chỉ truy cập không hợp lệ"})
            return False

        def has_access(self) -> bool:
            return phone.cookie_ok(self.headers.get("Cookie"))

        def send_page(self, status: int, page: bytes, *, cookie: str | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # same-origin, not no-referrer: no-referrer makes browsers send "Origin: null" on the code
            # form, which the Origin check refuses; nothing leaves the listener either way.
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", PHONE_LOGIN_CSP)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(page)

        def answer_code(self, code: str) -> None:
            """Check one code attempt and answer with the hop to V2, the form again, or the lock."""
            outcome, cookie = phone.try_code(code, ip=self.client_address[0])
            if outcome == "ok" and cookie:
                self.send_page(200, page("Đã xác nhận mã", "Đang mở BiliFlow…", form=False, redirect=V2),
                               cookie=cookie)
            elif outcome == "locked":
                # The form stays: the special key can lift the lock (plan §8, question 9).
                self.send_page(403, page("Đã khóa nhập mã", phone_access.LOCKED_MESSAGE, form=True, unlock=True))
            elif outcome == "wrong_unlock":
                self.send_page(403, page("Đã khóa nhập mã", phone_access.WRONG_UNLOCK_MESSAGE, form=True,
                                                attempts_left=phone.unlock_attempts_left(), unlock=True))
            elif outcome == "unlock_locked":
                self.send_page(403, page("Đã khóa nhập mã", phone_access.UNLOCK_LOCKED_MESSAGE, form=False))
            elif outcome == "unlocked":
                self.send_page(401, page("Đã gỡ khóa", phone_access.UNLOCKED_MESSAGE, form=True,
                                                attempts_left=phone.attempts_left()))
            else:
                self.send_page(401, page("Mã không đúng", "Mã không đúng. Xem lại mã trên PC.", form=True,
                                                attempts_left=phone.attempts_left()))

        def refuse_without_access(self, path: str) -> None:
            state = phone.status()
            if state["locked"] and path in ENTRY_PATHS:
                message = phone_access.UNLOCK_LOCKED_MESSAGE if state["unlock_locked"] else phone_access.LOCKED_MESSAGE
                self.send_page(403, page("Đã khóa nhập mã", message, form=not state["unlock_locked"], unlock=True))
            elif path in ENTRY_PATHS:
                self.send_page(401, page("BiliFlow trên điện thoại", "Nhập mã truy cập hiện trên PC.",
                                                form=True))
            else:
                self.send_json(401, {"error": "Cần mã truy cập của chế độ điện thoại"})

        def do_GET(self) -> None:
            if not self.host_allowed():
                return
            parsed = self.parsed_path()
            if parsed is None:
                return
            if not self.has_access():
                # Over Tailscale a device of the PC's own account opens an entry page without the code
                # (the user's choice, 2026-10-07); API calls never ask Tailscale.
                cookie = phone.tailscale_cookie(self.client_address[0]) if parsed.path in ENTRY_PATHS else None
                if cookie:
                    self.send_page(200, page("Đã nhận ra thiết bị", "Thiết bị này cùng tài khoản Tailscale với PC. "
                                             "Đang mở BiliFlow…", form=False, redirect=V2), cookie=cookie)
                    return
                # The code is only typed into the form (question 12): a ?code= link is ignored.
                self.refuse_without_access(parsed.path)
                return
            self.opened()
            if parsed.path in ("/", "/phone-login"):
                # V2 is the phone page; the classic dashboard (when CLASSIC_DASHBOARD is on) is PC only.
                self.redirect(V2)
                return
            if match := re.fullmatch(r"/review/(\d+)", parsed.path):
                # Same page as the PC (same token, same API prefix), plus the phone layout fixes.
                job_id = int(match.group(1))
                prefix = f"/api/jobs/{job_id}/review"
                html = _interactive_html(center.token).replace("'/api/", f"'{prefix}/")
                html = _review_back_to_v2(html, parsed.query)
                self.send_bytes(200, _review_page_for_phone(html).encode(), "text/html; charset=utf-8")
                return
            if parsed.path == "/api/phone-mode":
                # No code, link or counters here: the phone only learns that it is the phone (and how),
                # and over Tailscale when the mode turns off ("Gia hạn thêm 8 giờ" on the phone).
                self.send_json(200, {"remote": True, "enabled": True, "network": phone.network,
                                     "expires_at": phone.status()["expires_at"],
                                     "pc_only": sorted(set(phone_access.PC_ONLY_POSTS.values())
                                                       | {phone_access.PC_ONLY_VISUAL_AUDIT})})
                return
            super().do_GET()

        def do_POST(self) -> None:
            if not self.host_allowed(drain=True):
                return
            parsed = self.parsed_path(drain=True)
            if parsed is None:
                return
            path = parsed.path
            origin = self.headers.get("Origin")
            if origin is not None and origin != phone.origin:
                self.drain_body()
                self.send_json(403, {"error": "Nguồn yêu cầu không hợp lệ"})
                return
            if path == "/phone-login":
                try:
                    length = content_length(self.headers)
                    if length > 1024:
                        raise ValueError("Request is too large")
                    raw = self.rfile.read(length).decode("utf-8", "replace")
                except TimeoutError:
                    self.request_timed_out()
                    return
                except ValueError as error:
                    self.close_connection = True  # the unread body is not drained
                    self.send_json(400, {"error": str(error)})
                    return
                self.answer_code(urllib.parse.parse_qs(raw).get("code", [""])[0])
                return
            if not self.has_access():
                self.refuse_first(401, {"error": "Cần mã truy cập của chế độ điện thoại"})
                return
            self.opened()
            # H2: only PHONE_ALLOWED_POSTS; anything else is refused before the body is used.
            reason = phone_access.pc_only_reason(path)
            if reason:
                self.refuse_first(403, {"error": reason, "code": "pc_only"})
                return
            if phone_access.AI_AUDIT_ROUTE.fullmatch(path):
                # Visual AI Audit sends thumbnails out of the PC: PC only (plan §12.7). JSON audit stays.
                if not self.authorized():
                    self.drain_body()
                    self.send_json(403, {"error": "Phiên Control Center không hợp lệ"})
                    return
                try:
                    body = self.checked_body()
                except TimeoutError:
                    self.request_timed_out()
                    return
                except (ValueError, RecursionError) as error:
                    self.send_json(400, {"error": str(error)})
                    return
                if bool(body.get("visual", False)):
                    self.send_json(403, {"error": phone_access.PC_ONLY_VISUAL_AUDIT, "code": "pc_only"})
                    return
                self._cached_body = body
            super().do_POST()

    return PhoneHandler


def serve_control_center(*, project_root: Path, host: str = "127.0.0.1", port: int = 8765,
                         stable_seconds: float = 60.0, import_existing: bool = True) -> None:
    # Before the database is opened or the project imported.
    require_loopback_host(host)
    ControlCenter(project_root, host=host, port=port, stable_seconds=stable_seconds,
                  import_existing=import_existing).serve()
