from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import psutil

from biliflow import __version__
from biliflow.codex_supervisor import (
    codex_connection_status,
    collect_visual_evidence,
    load_ai_config,
    run_ai_audit,
    run_local_queue_audit,
    save_ai_config,
    start_codex_login,
)
from biliflow.job_import import import_existing_project
from biliflow.job_pipeline import DETECTOR_GROUPS
from biliflow.job_store import JobStore, now_iso
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
    record_review_decision,
    review_resource_status,
    review_export_paths,
)
from biliflow.scheduler import InputWatcher, JobScheduler
from biliflow.storage import storage_status


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


def _inside(root: Path, target: Path) -> Path:
    root = root.resolve(strict=True)
    target = target.resolve(strict=True)
    if target != root and root not in target.parents:
        raise ValueError("Path is outside the allowed BiliFlow directory")
    return target


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
    return """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BiliFlow Control Center</title>
<style>
:root{color-scheme:dark;font:15px system-ui,-apple-system,"Segoe UI",sans-serif;background:#090b10;color:#e8ecf4;--panel:#141923;--panel-2:#1a202c;--line:#2b3546;--muted:#9aa7ba;--blue:#55b8f5;--green:#61d39b;--amber:#e3ad4d;--red:#ff7f8e}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 15% -10%,#18263a 0,transparent 34rem),#090b10}header{padding:16px 24px;background:rgba(15,19,27,.94);border-bottom:1px solid #222b39;display:flex;gap:10px;align-items:center;position:sticky;top:0;z-index:4;backdrop-filter:blur(14px)}h1{font-size:21px;margin:0 auto 0 0;letter-spacing:-.02em}.pill{padding:6px 10px;border:1px solid #303a4b;border-radius:99px;background:#202633;color:#cbd5e4;font-size:13px}main{padding:22px;max-width:1500px;margin:auto}.bar,.job,.workspace{background:rgba(20,25,35,.96);border:1px solid var(--line);border-radius:16px;box-shadow:0 14px 36px rgba(0,0,0,.16)}.bar{padding:14px;margin-bottom:14px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}.ai{display:grid;grid-template-columns:1fr auto;gap:16px}.ai-controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.workspace{overflow:hidden}.workspace-head{padding:18px 20px 0}.workspace-title{font-size:19px;font-weight:750;margin:0}.workspace-subtitle{color:var(--muted);font-size:13px;margin-top:4px}.job-tabs{display:flex;gap:8px;padding:16px 20px 0;border-bottom:1px solid var(--line);overflow-x:auto}.job-tab{display:flex;align-items:center;gap:8px;padding:11px 14px;border:1px solid transparent;border-radius:10px 10px 0 0;background:transparent;color:#aab6c8;white-space:nowrap}.job-tab:hover{background:#1c2330;color:#fff}.job-tab.active{background:#202837;border-color:#354258;border-bottom-color:#202837;color:#fff}.tab-count{min-width:24px;padding:2px 7px;border-radius:99px;background:#2d3748;font-size:12px;text-align:center}.job-tab.active .tab-count{background:#286b96;color:#eaf7ff}.job-list{padding:16px}.job{padding:0;margin-bottom:14px;overflow:hidden}.job:last-child{margin-bottom:0}.job[data-bucket="completed"]{border-color:#285744}.job[data-bucket="running"]{border-color:#2e5872}.job-head{display:flex;gap:14px;align-items:flex-start;padding:17px 18px;border-bottom:1px solid #273142}.job-identity{min-width:0;flex:1}.job-title{font-size:17px;font-weight:750;word-break:break-word}.job-key{color:#738197;font-size:12px;margin-top:3px;word-break:break-all}.job-path{color:var(--muted);font-size:12px;margin-top:7px;word-break:break-all}.state-badge{padding:7px 10px;border:1px solid #3b4658;border-radius:99px;background:#252d3a;font-weight:700;font-size:12px;white-space:nowrap}.tone-running{color:#8fd8ff;border-color:#2e6f96;background:#153348}.tone-complete{color:#88e7b8;border-color:#297252;background:#143c2e}.tone-waiting{color:#f0c66f;border-color:#74591f;background:#3d3014}.tone-error{color:#ff9aa5;border-color:#7e3540;background:#401d24}.job-body{padding:17px 18px}.progress-row{display:flex;align-items:center;gap:12px}.progress-label{min-width:145px;color:#c8d2e1;font-size:13px}.progress{height:8px;background:#252d39;border-radius:99px;overflow:hidden;flex:1}.progress i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#368fd2,#5dc6f5)}.progress-value{min-width:40px;text-align:right;color:#c9d8e9;font-variant-numeric:tabular-nums}.status-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:15px}.status-box{min-height:106px;padding:12px;border:1px solid #2d3748;border-radius:12px;background:#10151e}.status-box.tone-complete{border-color:#285b46;background:#10261f}.status-box.tone-running{border-color:#285e7c;background:#102431}.status-box.tone-waiting{border-color:#655020;background:#2a2415}.status-box.tone-error{border-color:#70313b;background:#2c171c}.status-kicker{color:#8f9db0;font-size:11px;font-weight:750;text-transform:uppercase;letter-spacing:.06em}.status-value{font-weight:750;margin-top:7px}.status-detail{color:#9eacbf;font-size:12px;line-height:1.4;margin-top:5px}.mini-progress{height:6px;margin-top:9px;border-radius:99px;background:#273242;overflow:hidden}.mini-progress i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#2e8dca,#67d1ff)}.phase-group+.phase-group{margin-top:20px}.phase-heading{display:flex;align-items:center;gap:9px;margin:1px 2px 11px;color:#d8e3f1;font-size:13px;font-weight:750}.phase-heading span{padding:2px 7px;border-radius:99px;background:#293444;color:#aebdd0;font-size:11px}.job-error{margin-top:12px;padding:10px 12px;border:1px solid #793641;border-radius:10px;background:#341a20;color:#ff9ba6}.job-footer{padding:14px 18px 17px;border-top:1px solid #273142;background:#11161f}.scope-line{color:#9ba9bc;font-size:12px;margin-bottom:12px}.actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.actions select{max-width:220px}button,select{border:1px solid transparent;border-radius:9px;padding:9px 12px;background:#2d70b7;color:#fff;cursor:pointer}button:hover{filter:brightness(1.08)}button.warn{background:#8b611d}button.danger{background:#963845}button.green{background:#247554}button:disabled{opacity:.45;cursor:not-allowed}.detectors{width:100%;border:1px solid #39445a;border-radius:10px;padding:9px 11px;text-align:left}.detectors legend{color:#9ca8bb;padding:0 5px}.detectors label{display:inline-flex;gap:5px;align-items:center;margin:3px 10px 3px 0}.rerun-panel{width:100%;border:1px solid #354055;border-radius:11px;background:#171d28}.rerun-panel summary{cursor:pointer;padding:10px 12px;color:#c5d0df;font-weight:650}.rerun-body{display:flex;gap:8px;flex-wrap:wrap;padding:0 10px 10px}.empty{text-align:center;padding:52px 20px;color:var(--muted)}.detail{font-size:13px}.muted{color:var(--muted)}.state{font-weight:700;color:var(--blue)}.ok{color:var(--green)}.error{color:var(--red)}.notice{display:none;border-radius:10px;padding:12px 14px;margin-bottom:12px;background:#173c30;border:1px solid #2f8b69}.notice.error{display:block;background:#4a2027;border-color:#a43d4a}.notice.show{display:block}@media(max-width:1050px){.status-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.ai{grid-template-columns:1fr}}@media(max-width:680px){header{align-items:flex-start;flex-wrap:wrap}h1{width:100%}main{padding:12px}.status-grid{grid-template-columns:1fr}.job-head{flex-direction:column}.progress-row{align-items:flex-start;flex-wrap:wrap}.progress-label{width:100%}.progress{min-width:180px}.job-tabs{padding-left:12px}.job-list{padding:10px}.actions{align-items:stretch}.actions button,.actions select{flex:1 1 auto}}
</style></head><body><header><h1>BiliFlow Control Center</h1><span class="pill" id="worker">Đang tải</span><span class="pill" id="resource"></span></header>
<main><div id="notice" class="notice" role="status"></div><div class="bar"><button class="green" onclick="scheduler(false)">Chạy hàng đợi</button><button class="warn" onclick="scheduler(true)">Tạm dừng scheduler</button><button onclick="location.reload()">Làm mới</button><button class="danger" onclick="shutdown('after_stage')">Tắt sau bước hiện tại</button><button class="danger" onclick="shutdown('immediate')">Dừng ngay và tắt</button><span class="muted">Đóng tab không làm dừng xử lý. Muốn tắt hẳn, dùng nút Tắt hoặc Stop-BiliFlow.cmd.</span></div><section class="bar ai"><div><div class="job-title">AI Supervisor</div><div id="ai-message" class="muted">Đang kiểm tra Codex…</div><div class="detail muted">AI JSON không gửi media. Visual AI chỉ gửi tối đa 36 thumbnail sau khi bạn xác nhận cho từng video; không gửi video/âm thanh. Mọi lượt audit dùng chung một session AI Supervisor và được chạy tuần tự. Dùng hạn mức ChatGPT, API key và model GPT-6 bị chặn.</div></div><div class="ai-controls"><label><input type="checkbox" id="ai-enabled"> Bật</label><select id="ai-model"><option value="gpt-5.6-luna">GPT-5.6 Luna — mặc định</option><option value="gpt-5.6-terra">GPT-5.6 Terra — cân bằng</option><option value="gpt-5.6-sol">GPT-5.6 Sol — mạnh hơn</option></select><select id="ai-effort"><option value="low">Low — tiết kiệm</option><option value="medium">Medium — mặc định</option><option value="high">High — tối đa</option></select><button onclick="saveAI()">Lưu cấu hình</button><button onclick="checkAI()">Kiểm tra kết nối</button><button class="green" onclick="loginAI()">Đăng nhập Codex</button><a href="https://learn.chatgpt.com/docs/auth" target="_blank" rel="noopener" class="muted">Hướng dẫn chính thức</a></div></section><section class="workspace"><div class="workspace-head"><div class="workspace-title">Video</div><div class="workspace-subtitle">Theo dõi riêng các video đang chờ, đang xử lý và đã xuất hoàn tất.</div></div><nav id="job-tabs" class="job-tabs" aria-label="Trạng thái video"></nav><section id="jobs" class="job-list"></section></section></main>
<script>
let token='';let status={};let aiState={ready:false,config:{},message:'Đang kiểm tra Codex…'};let noticeTimer=null;let activeJobTab=null;const detectorDrafts={};const metadataDrafts={};const rerunPanelDrafts={};
async function refreshToken(){const r=await fetch('/api/session',{cache:'no-store'});if(!r.ok)throw Error('Không lấy được phiên Control Center');token=(await r.json()).token;return token}
async function json(url,opt={},retry=true){opt.headers={...(opt.headers||{}),'X-BiliFlow-Token':token,'Content-Type':'application/json'};const r=await fetch(url,opt);if(r.status===403&&retry){await refreshToken();return json(url,opt,false)}const v=await r.json();if(!r.ok)throw Error(v.error||r.statusText);return v}
const post=(url,body={})=>json(url,{method:'POST',body:JSON.stringify(body)});
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function notify(message,isError=false){const e=document.getElementById('notice');if(!e)return;e.textContent=message;e.className=`notice show${isError?' error':''}`;if(noticeTimer)clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>{e.className='notice'},6000)}
function detectorPicker(j){const opts=status.detector_options||[],selected=new Set(detectorDrafts[j.id]||j.detector_groups||opts.map(x=>x.id)),all=opts.length>0&&opts.every(x=>selected.has(x.id));return `<fieldset class="detectors"><legend>Nhóm cần kiểm tra</legend><label title="Chạy mọi nhóm"><input type="checkbox" id="det-all-${j.id}" ${all?'checked':''} onchange="toggleAllDetectors(${j.id},this.checked)">Tất cả</label>${opts.map(x=>`<label title="${esc(x.description)}"><input type="checkbox" data-detector-job="${j.id}" value="${esc(x.id)}" ${selected.has(x.id)?'checked':''} onchange="captureDetectorDraft(${j.id})">${esc(x.label)}</label>`).join('')}</fieldset>`}
function captureDetectorDraft(id){const boxes=[...document.querySelectorAll(`[data-detector-job="${id}"]`)];detectorDrafts[id]=boxes.filter(x=>x.checked).map(x=>x.value);const all=document.getElementById(`det-all-${id}`);if(all)all.checked=boxes.length>0&&boxes.every(x=>x.checked)}
function toggleAllDetectors(id,checked){document.querySelectorAll(`[data-detector-job="${id}"]`).forEach(x=>x.checked=checked);captureDetectorDraft(id)}
function selectedDetectors(id){captureDetectorDraft(id);const values=detectorDrafts[id]||[];if(!values.length){notify('Hãy chọn ít nhất một nhóm cần kiểm tra.',true);return null}return values}
function metadataSelection(j){const saved=metadataDrafts[j.id]||{},styles=['animation','live_action','mixed'],profiles=['careful','fast'];return{content_style:saved.content_style||(styles.includes(j.content_style)&&j.content_style!=='unknown'?j.content_style:'animation'),profile:saved.profile||(profiles.includes(j.profile)?j.profile:'careful')}}
function captureMetadataDraft(id){const style=document.getElementById(`style-${id}`),profile=document.getElementById(`profile-${id}`);if(style&&profile)metadataDrafts[id]={content_style:style.value,profile:profile.value}}
function captureRerunPanelDrafts(){document.querySelectorAll('.rerun-panel[data-job-id]').forEach(panel=>{rerunPanelDrafts[panel.dataset.jobId]=panel.open})}
const ocrDrafts={};const speedDrafts={};
function selectedOcrBatch(id){return Number(document.getElementById(`ocr-${id}`)?.value||1)}
function selectedFastScan(id){return document.getElementById(`fast-${id}`)?.checked===true}
function speedPicker(j){const value=speedDrafts[j.id]??j.fast_scan??true;return `<label title="Ghép OCR xuyên nhiều frame, detect chữ FP16, tính routing logo song song trong lúc OCR. Không giảm mật độ quét; mục review đã kiểm chứng giống chế độ thường trên cả phim (vài track chữ credits có thể khác nhẹ). Khác profile Nhanh (giảm mật độ quét)."><input type="checkbox" id="fast-${j.id}" ${value?'checked':''} onchange="speedDrafts[${j.id}]=this.checked"> Tăng tốc xử lý</label>`}
function ocrPicker(j){const value=ocrDrafts[j.id]??j.ocr_recognition_batch_size??1;return `<label title="Chỉ áp dụng OCR trong nhóm Quảng cáo/logo; không đổi mật độ quét.">OCR <select id="ocr-${j.id}" onchange="ocrDrafts[${j.id}]=Number(this.value)"><option value="1" ${value===1?'selected':''}>Chuẩn</option><option value="8" ${value===8?'selected':''}>Tăng tốc (thử nghiệm)</option></select></label>`}
function controls(j){const id=j.id;let a=[];if(j.state==='NEEDS_METADATA'||j.state==='DISCOVERED'){const metadata=metadataSelection(j);a.push(`${detectorPicker(j)}${ocrPicker(j)}${speedPicker(j)}<select id="style-${id}" onchange="captureMetadataDraft(${id})"><option value="animation" ${metadata.content_style==='animation'?'selected':''}>Hoạt hình</option><option value="live_action" ${metadata.content_style==='live_action'?'selected':''}>Phim thực tế</option><option value="mixed" ${metadata.content_style==='mixed'?'selected':''}>Hỗn hợp</option></select><select id="profile-${id}" onchange="captureMetadataDraft(${id})"><option value="careful" ${metadata.profile==='careful'?'selected':''}>Tỉ mỉ</option><option value="fast" ${metadata.profile==='fast'?'selected':''}>Nhanh</option></select><button class="green" onclick="start(${id})">Bắt đầu</button>`)}if(['PAUSED','FAILED','INTERRUPTED_RECOVERABLE'].includes(j.state))a.push(`<button class="green" onclick="act(${id},'resume')">Tiếp tục</button>`);if(['QUEUED','PREFLIGHT','SCANNING_SAFETY','SCANNING_TEXT','SCANNING_LOGO','LOCALIZING_REGIONS','BUILDING_REVIEW','RENDERING'].includes(j.state)){a.push(`<button class="warn" onclick="act(${id},'stop-after-stage')">Dừng sau bước</button><button class="warn" onclick="act(${id},'pause')">Dừng ngay</button>`)}if(j.active_queue_path){a.push(`<button onclick="location.href='/review/${id}'">Duyệt cảnh</button>${j.ai_audit?.state==='RUNNING'?'<button disabled>Visual AI đang kiểm tra…</button>':aiState.ready?`<button class="green" onclick="audit(${id},true)">Visual AI Audit</button>`:`<button disabled title="${esc(aiState.message||'AI Supervisor chưa sẵn sàng')}">AI chưa sẵn sàng</button>`}`)}else{a.push('<button disabled title="Video cần quét xong và có review queue trước">AI: chờ queue</button>')}if(j.state==='FAILED')a.push(`<button onclick="act(${id},'retry')">Thử lại bước lỗi</button>`);if(['WAITING_REVIEW','READY_TO_EXPORT','COMPLETED','CANCELLED','FAILED','PAUSED','INTERRUPTED_RECOVERABLE'].includes(j.state))a.push(`<details class="rerun-panel" data-job-id="${id}" ${rerunPanelDrafts[id]?'open':''}><summary>Chạy lại kiểm tra</summary><div class="rerun-body">${detectorPicker(j)}${ocrPicker(j)}${speedPicker(j)}<button class="warn" onclick="rerun(${id})">Chạy lại với phạm vi đã chọn</button></div></details>`);if(!['COMPLETED','CANCELLED'].includes(j.state))a.push(`<button class="danger" onclick="act(${id},'cancel')">Hủy</button>`);return a.join('')}
function auditText(j){const a=j.ai_audit;if(!a)return 'Visual AI: chưa chạy';if(a.state==='COMPLETED')return `Visual AI: ${a.result||'DONE'} — ${a.summary||''}`;return `Visual AI: ${a.message||a.state}`}
function structureText(j){const a=j.structure_audit;if(!a)return 'Kiểm tra cấu trúc: chờ tạo queue';return `Kiểm tra cấu trúc cục bộ: ${a.result||'DONE'} — ${a.summary||''} · không dùng quota`}
const runningStates=new Set(['PREFLIGHT','SCANNING_SAFETY','SCANNING_TEXT','SCANNING_LOGO','LOCALIZING_REGIONS','BUILDING_REVIEW','RENDERING','VERIFYING']);
function jobBucket(j){if(j.state==='COMPLETED')return 'completed';if(runningStates.has(j.state))return 'running';return 'waiting'}
function selectJobTab(tab){activeJobTab=tab;renderJobs()}
function statePresentation(j){if(j.state==='RENDERING'&&j.render_progress?.state==='VERIFYING')return{label:'Đang kiểm tra output',tone:'running'};const values={DISCOVERED:['Chưa thiết lập','waiting'],NEEDS_METADATA:['Chờ thiết lập','waiting'],QUEUED:['Đang xếp hàng','waiting'],PREFLIGHT:['Đang chuẩn bị','running'],SCANNING_SAFETY:['Đang quét an toàn','running'],SCANNING_TEXT:['Đang quét chữ','running'],SCANNING_LOGO:['Đang quét logo','running'],LOCALIZING_REGIONS:['Đang khoanh vùng','running'],BUILDING_REVIEW:['Đang tạo danh sách duyệt','running'],WAITING_REVIEW:['Chờ duyệt cảnh','waiting'],READY_TO_EXPORT:['Sẵn sàng xuất','waiting'],RENDERING:['Đang xuất video','running'],VERIFYING:['Đang kiểm tra output','running'],PAUSED:['Đã tạm dừng','waiting'],FAILED:['Có lỗi cần xử lý','error'],INTERRUPTED_RECOVERABLE:['Có thể tiếp tục','waiting'],CANCELLED:['Đã hủy','waiting'],COMPLETED:['Đã hoàn tất','complete']};const value=values[j.state]||[j.state||'Chưa rõ','waiting'];return{label:value[0],tone:value[1]}}
function scanStatus(j){const percent=Math.max(0,Math.min(100,Math.round(100*(Number(j.progress)||0))));if(runningStates.has(j.state)&&j.state!=='RENDERING'&&j.state!=='VERIFYING')return{value:`${percent}% · đang chạy`,detail:`Bước hiện tại: ${j.current_stage||'đang chuẩn bị'}`,tone:'running'};if(percent>=100)return{value:'100% · đã quét',detail:'Kết quả phân tích đã được lưu.',tone:'complete'};if(j.state==='QUEUED')return{value:`${percent}% · chờ lượt`,detail:'Video đang nằm trong hàng đợi.',tone:'waiting'};return{value:`${percent}% · chưa hoàn tất`,detail:'Tiến độ quét và tạo cảnh duyệt.',tone:'waiting'}}
function structureStatus(j){const a=j.structure_audit;if(!a)return{value:'Chưa kiểm tra',detail:'Sẽ có sau khi tạo danh sách duyệt.',tone:'waiting'};const result=String(a.result||'DONE').toUpperCase();return{value:result==='PASS'?'Đạt':result,detail:a.summary||'Kiểm tra cấu trúc cục bộ đã hoàn tất.',tone:result==='PASS'?'complete':result==='BLOCK'?'error':'waiting'}}
function visualStatus(j){const a=j.ai_audit;if(!a)return{value:'Chưa chạy',detail:'Visual AI Audit là bước kiểm tra tùy chọn.',tone:'waiting'};if(a.state==='RUNNING'||a.state==='QUEUED')return{value:a.state==='RUNNING'?'Đang kiểm tra':'Đang chờ',detail:a.message||'AI Supervisor đang xử lý.',tone:'running'};if(a.state==='FAILED')return{value:'Lỗi kiểm tra',detail:a.message||'Có thể chạy lại Visual AI Audit.',tone:'error'};const result=String(a.result||'DONE').toUpperCase();return{value:`Đã kiểm tra · ${result}`,detail:a.summary||'Visual AI Audit đã hoàn tất.',tone:result==='BLOCK'?'error':result==='WARN'?'waiting':'complete'}}
function shortDuration(seconds){const value=Number(seconds);if(!Number.isFinite(value)||value<0)return 'đang tính';const rounded=Math.ceil(value/60);if(rounded<60)return `khoảng ${Math.max(1,rounded)} phút`;const hours=Math.floor(rounded/60),minutes=rounded%60;return `khoảng ${hours} giờ${minutes?` ${minutes} phút`:''}`}
function completedAt(value){if(!value)return 'Đã hoàn tất và qua bước kiểm tra cuối.';const date=new Date(value);if(Number.isNaN(date.getTime()))return 'Đã hoàn tất và qua bước kiểm tra cuối.';return `Hoàn thành lúc ${new Intl.DateTimeFormat('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit',day:'2-digit',month:'2-digit',year:'numeric'}).format(date)}`}
function exportStatus(j){if(j.state==='COMPLETED')return{value:'100% · Đã xuất video',detail:completedAt(j.updated_at),tone:'complete',percent:100};if(j.state==='RENDERING'){const p=j.render_progress||{};if(p.state==='VERIFYING')return{value:'100% · đang kiểm tra',detail:'Đã render xong; đang xác nhận hình, tiếng và thời lượng.',tone:'running',percent:100};const percent=Math.max(0,Math.min(99.9,Number(p.percent)||0)),speed=p.speed_text?`Tốc độ ${p.speed_text}`:'Đang khởi tạo FFmpeg',eta=p.eta_seconds!=null?` · còn ${shortDuration(p.eta_seconds)}`:'';return{value:`${percent.toFixed(1)}% · đang xuất`,detail:`${speed}${eta}`,tone:'running',percent}}if(j.state==='VERIFYING')return{value:'100% · đang kiểm tra',detail:'Đang xác nhận hình, tiếng và thời lượng.',tone:'running',percent:100};if(j.current_stage==='render'&&j.state==='QUEUED')return{value:'Chờ xuất video',detail:'Lệnh xuất đã nằm trong hàng đợi.',tone:'waiting',percent:0};if(j.current_stage==='render'&&j.state==='FAILED')return{value:'Xuất video bị lỗi',detail:'Xem lỗi bên dưới rồi chọn thử lại.',tone:'error'};if(j.state==='READY_TO_EXPORT')return{value:'Sẵn sàng xuất',detail:'Duyệt xong; có thể tạo output.',tone:'waiting'};if(j.state==='WAITING_REVIEW')return{value:'Chưa xuất',detail:'Cần hoàn tất duyệt cảnh trước.',tone:'waiting'};return{value:'Chưa tới bước xuất',detail:'Output chỉ được tạo sau khi duyệt.',tone:'waiting'}}
function statusBox(title,value){const progress=Number.isFinite(Number(value.percent))?`<div class="mini-progress" aria-label="${esc(title)} ${Math.round(Number(value.percent))}%"><i style="width:${Math.max(0,Math.min(100,Number(value.percent)))}%"></i></div>`:'';return `<div class="status-box tone-${value.tone}"><div class="status-kicker">${esc(title)}</div><div class="status-value">${esc(value.value)}</div><div class="status-detail">${esc(value.detail)}</div>${progress}</div>`}
function videoName(j){const parts=String(j.source_path||j.job_key||'Video').split(/[\\/]/);return parts[parts.length-1]||j.job_key||'Video'}
function jobCard(j,labels){const stateInfo=statePresentation(j),bucket=jobBucket(j),percent=Math.max(0,Math.min(100,Math.round(100*(Number(j.progress)||0)))),groups=(j.detector_groups||[]).map(x=>labels[x]||x).join(', ')||'Chưa chọn';return `<article class="job" data-bucket="${bucket}"><div class="job-head"><div class="job-identity"><div class="job-title">#${j.id} · ${esc(videoName(j))}</div><div class="job-key">${esc(j.job_key)}</div><div class="job-path">${esc(j.source_path)}</div></div><span class="state-badge tone-${stateInfo.tone}">${esc(stateInfo.label)}</span></div><div class="job-body"><div class="progress-row"><div class="progress-label">Tiến trình phân tích</div><div class="progress" aria-label="Tiến trình ${percent}%"><i style="width:${percent}%"></i></div><div class="progress-value">${percent}%</div></div><div class="status-grid">${statusBox('Phân tích cảnh',scanStatus(j))}${statusBox('Cấu trúc cục bộ',structureStatus(j))}${statusBox('Visual AI Audit',visualStatus(j))}${statusBox('Xuất video',exportStatus(j))}</div>${j.error?`<div class="job-error">${esc(j.error)}</div>`:''}</div><div class="job-footer"><div class="scope-line"><strong>Phạm vi kiểm tra:</strong> ${esc(groups)} · <strong>Chế độ:</strong> ${esc(j.profile)} · ${esc(j.content_style)} · OCR: ${j.ocr_recognition_batch_size===8?'Tăng tốc (thử nghiệm)':'Chuẩn'}${j.fast_scan?' · Tăng tốc xử lý':''}</div><div class="actions">${controls(j)}</div></div></article>`}
function runPhase(j){return j.state==='RENDERING'||j.state==='VERIFYING'||j.current_stage==='render'?'export':'analysis'}
function phaseSection(label,items,labels){if(!items.length)return '';return `<section class="phase-group"><div class="phase-heading">${esc(label)} <span>${items.length}</span></div>${items.map(j=>jobCard(j,labels)).join('')}</section>`}
function renderJobs(){captureRerunPanelDrafts();const jobs=status.jobs||[],labels=Object.fromEntries((status.detector_options||[]).map(x=>[x.id,x.label])),groups={waiting:[],running:[],completed:[]};jobs.forEach(j=>groups[jobBucket(j)].push(j));if(!activeJobTab){activeJobTab=groups.running.length?'running':groups.waiting.length?'waiting':'completed'}const tabs=[['waiting','Đang chờ xử lý'],['running','Đang chạy'],['completed','Hoàn tất']];document.getElementById('job-tabs').innerHTML=tabs.map(([key,label])=>`<button class="job-tab ${activeJobTab===key?'active':''}" onclick="selectJobTab('${key}')"><span>${label}</span><span class="tab-count">${groups[key].length}</span></button>`).join('');const visible=groups[activeJobTab]||[];let content='';if(activeJobTab==='running'){content=phaseSection('Đang phân tích để duyệt',visible.filter(j=>runPhase(j)==='analysis'),labels)+phaseSection('Đang xuất video',visible.filter(j=>runPhase(j)==='export'),labels)}else{content=visible.map(j=>jobCard(j,labels)).join('')}document.getElementById('jobs').innerHTML=content||`<div class="empty">${jobs.length?'Không có video trong mục này.':'Chưa có video trong input.'}</div>`}
function render(){document.getElementById('worker').textContent=status.active?`Đang chạy #${status.active.job_id}: ${status.active.stage}`:(status.scheduler_paused?'Scheduler tạm dừng':'Đang chờ');const r=status.resources||{};document.getElementById('resource').textContent=`CPU ${Math.round(r.cpu_percent||0)}% · RAM ${Math.round(r.memory?.percent||0)}% · GPU ${r.gpu?Math.round(r.gpu.utilization_percent)+'%':'N/A'}`;renderJobs()}
async function load(){status=await json('/api/status');render()}
function renderAI(){const c=aiState.config||{},s=aiState.session||{};document.getElementById('ai-enabled').checked=!!c.enabled;document.getElementById('ai-model').value=c.model||'gpt-5.6-luna';document.getElementById('ai-effort').value=c.reasoning_effort||'medium';const e=document.getElementById('ai-message');e.textContent=`${aiState.message||'Chưa kiểm tra'} · ${c.model||''} / ${c.reasoning_effort||''} · session dùng chung: ${s.active?'đã khởi tạo':'sẽ tạo ở lượt audit đầu'}${aiState.login_running?' · đang chờ đăng nhập':''}`;e.className=aiState.ready?'ok':'error';render()}
async function loadAI(){aiState=await json('/api/ai');renderAI()}
async function saveAI(){try{aiState=await post('/api/ai/config',{enabled:document.getElementById('ai-enabled').checked,model:document.getElementById('ai-model').value,reasoning_effort:document.getElementById('ai-effort').value});renderAI();notify(`Đã lưu cấu hình AI: ${aiState.config.model} / ${aiState.config.reasoning_effort}`)}catch(e){notify(`Không lưu được cấu hình AI: ${e.message}`,true)}}
async function checkAI(){try{aiState=await post('/api/ai/check');renderAI();notify(aiState.ready?'AI Supervisor đã kết nối và sẵn sàng.':aiState.message,!aiState.ready)}catch(e){notify(`Không kiểm tra được AI: ${e.message}`,true)}}
async function loginAI(){try{aiState=await post('/api/ai/login');renderAI();alert('Codex đang mở luồng đăng nhập ChatGPT trong trình duyệt. Hoàn tất đăng nhập rồi bấm Kiểm tra kết nối.')}catch(e){alert(e.message)}}
async function start(id){captureMetadataDraft(id);const metadata=metadataDrafts[id],detectors=selectedDetectors(id);if(!detectors||!metadata)return;await post(`/api/jobs/${id}/start`,{content_style:metadata.content_style,profile:metadata.profile,detectors,ocr_recognition_batch_size:selectedOcrBatch(id),fast_scan:selectedFastScan(id)});delete ocrDrafts[id];delete speedDrafts[id];delete detectorDrafts[id];delete metadataDrafts[id];await load()}
async function act(id,name){try{await post(`/api/jobs/${id}/${name}`);await load()}catch(e){alert(e.message)}}
async function rerun(id){const detectors=selectedDetectors(id);if(!detectors)return;if(!confirm('Chạy lại video với các nhóm kiểm tra đang chọn? Report và quyết định cũ vẫn được giữ; lượt mới dùng thư mục revision riêng.'))return;try{await post(`/api/jobs/${id}/rerun`,{detectors,ocr_recognition_batch_size:selectedOcrBatch(id),fast_scan:selectedFastScan(id)});delete ocrDrafts[id];delete speedDrafts[id];delete detectorDrafts[id];delete rerunPanelDrafts[id];await load();notify('Đã xếp video chạy lại theo phạm vi mới trong một revision riêng.')}catch(e){notify(`Không thể chạy lại: ${e.message}`,true)}}
async function audit(id,visual=false){if(visual&&!confirm('Visual AI Audit sẽ gửi tối đa 36 ảnh thumbnail của riêng video này cho Codex bằng tài khoản ChatGPT. Video và âm thanh gốc không được gửi. Tiếp tục?'))return;try{await post(`/api/jobs/${id}/ai-audit`,{visual});alert(visual?'Visual AI Audit đã được xếp chạy. AI chỉ đưa đề xuất; bạn vẫn duyệt mọi thay đổi.':'AI JSON audit đã được xếp chạy.')}catch(e){alert(e.message)}}
async function scheduler(paused){await post('/api/scheduler',{paused});await load()}
async function shutdown(mode){if(!confirm(mode==='immediate'?'Dừng bước hiện tại và tắt Control Center?':'Tắt sau khi bước hiện tại hoàn tất?'))return;document.body.innerHTML='<main><div class="empty"><div class="name" id="shutdown-state">Đang gửi lệnh tắt BiliFlow…</div><p class="muted">Tab sẽ được giữ lại để hiển thị kết quả.</p></div></main>';try{await post('/api/shutdown',{mode});document.getElementById('shutdown-state').textContent=mode==='immediate'?'Đang dừng job và tắt backend…':'Đang chờ bước hiện tại hoàn tất rồi tắt…';for(let i=0;i<120;i++){await new Promise(r=>setTimeout(r,500));try{await fetch('/healthz',{cache:'no-store'})}catch(_){document.getElementById('shutdown-state').textContent='BiliFlow đã tắt hoàn toàn. Bạn có thể đóng tab.';return}}document.getElementById('shutdown-state').textContent='Lệnh tắt đã được nhận nhưng backend vẫn đang hoàn tất bước hiện tại.'}catch(e){document.getElementById('shutdown-state').textContent=`Không xác nhận được trạng thái tắt: ${e.message}`}}
(async()=>{await refreshToken();await Promise.all([load(),loadAI()]);setInterval(()=>load().catch(e=>notify(`Mất kết nối dashboard: ${e.message}`,true)),3000);setInterval(()=>{if(aiState.login_running)loadAI().catch(()=>{})},3000)})().catch(e=>notify(e.message,true));
</script></body></html>"""


class ControlCenter:
    def __init__(self, root: Path, *, host: str = "127.0.0.1", port: int = 8765,
                 stable_seconds: float = 60.0, import_existing: bool = True):
        self.root = root.resolve(strict=True)
        self.host = host
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self.lock = SingleInstanceLock(self.root / "state" / "control-center.lock")
        self.store = JobStore(self.root / "state" / "control-center.sqlite3")
        self.recovered = self.store.recover_interrupted()
        if import_existing:
            import_existing_project(self.root, self.store)
        self.scheduler = JobScheduler(self.root, self.store)
        self.watcher = InputWatcher(self.root, self.store, stable_seconds=stable_seconds)
        self.server: ThreadingHTTPServer | None = None
        self._stopping = threading.Event()
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

    def status(self) -> dict[str, Any]:
        jobs = self.store.list_jobs()
        for job in jobs:
            job["ai_audit"] = self.ai_audit_summary(int(job["id"]))
            job["structure_audit"] = self.structure_audit_summary(int(job["id"]))
            job["render_progress"] = self.render_progress_summary(job)
            job["ocr_recognition_batch_size"] = self.scheduler.ocr_batch_size(int(job["id"]))
            job["fast_scan"] = self.scheduler.fast_scan(int(job["id"]))
            job["detector_groups"] = list(
                self.scheduler.detector_groups(int(job["id"]))
            )
        return {
            "version": __version__, "started": True, "recovered_jobs": self.recovered,
            "scheduler_paused": self.store.setting("scheduler_paused", False),
            "active": self.scheduler.active, "resources": _resources(self.root),
            "jobs": jobs, "storage": storage_status(self.root).as_dict(),
            "detector_options": [
                {"id": key, **value} for key, value in DETECTOR_GROUPS.items()
            ],
        }

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
        return {
            "state": "COMPLETED",
            "result": payload.get("result"),
            "summary": payload.get("summary"),
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

    def sync_queue_state(self, job_id: int, queue: dict[str, Any]) -> None:
        state = "READY_TO_EXPORT" if queue.get("status") == "READY_FOR_EDIT_PLAN" else "WAITING_REVIEW"
        self.store.update_job(job_id, state=state, error=None)

    def finalize(
        self, job_id: int, *, size_mode: str = "default",
        max_output_gb: object = None,
    ) -> dict[str, Any]:
        queue_path = self.queue_path(job_id)
        queue = _read_json(queue_path)
        if queue.get("status") != "READY_FOR_EDIT_PLAN":
            raise ValueError("Vẫn còn mục chưa có quyết định cuối cùng")
        policy = normalize_output_size_policy(size_mode, max_output_gb)
        queue["export_size_policy"] = policy
        _write_json(queue_path, queue)
        plan_path, output_path, _ = review_export_paths(self.root, queue)
        if output_path.exists():
            self.store.update_job(job_id, state="COMPLETED", progress=1.0)
            return {
                "status": "COMPLETED",
                "output": output_path.relative_to(self.root).as_posix(),
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

    def start_ai_audit(self, job_id: int, *, visual_opt_in: bool = False) -> None:
        if visual_opt_in:
            connection = self.ai_status()
            if not connection["ready"]:
                raise ValueError(connection["message"])
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
        center = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args) -> None:
                return

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

            def body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 65536:
                    raise ValueError("Request is too large")
                value = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(value, dict):
                    raise ValueError("JSON object required")
                return value

            def authorized(self) -> bool:
                return self.headers.get("X-BiliFlow-Token") == center.token

            def do_GET(self) -> None:
                path = urllib.parse.urlparse(self.path).path
                try:
                    if path == "/":
                        self.send_bytes(200, _dashboard_html().encode(), "text/html; charset=utf-8")
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
                        self.send_bytes(200, html.encode(), "text/html; charset=utf-8")
                    elif match := re.fullmatch(r"/api/jobs/(\d+)/review/(queue|session|resources|export)", path):
                        job_id, kind = int(match.group(1)), match.group(2)
                        if kind == "queue": value = _read_json(center.queue_path(job_id))
                        elif kind == "session": value = {"token": center.token}
                        elif kind == "resources":
                            value = review_resource_status(
                                project_root=center.root, queue_path=center.queue_path(job_id)
                            )
                        else:
                            job = center.store.get_job(job_id)
                            outputs = [x for x in center.store.artifacts(job_id) if x["kind"] == "final_output"]
                            value = {"status": job["state"], "output": outputs[-1]["path"] if outputs else None}
                        self.send_json(200, value)
                    elif path.startswith("/media/"):
                        relative = urllib.parse.unquote(path.removeprefix("/media/"))
                        target = _inside(center.root / "reports", center.root / relative)
                        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                        self.send_bytes(200, target.read_bytes(), content_type)
                    else:
                        self.send_json(404, {"error": "Không tìm thấy"})
                except (KeyError, ValueError, FileNotFoundError) as error:
                    self.send_json(404, {"error": str(error)})
                except Exception as error:
                    self.send_json(500, {"error": str(error)})

            def do_POST(self) -> None:
                path = urllib.parse.urlparse(self.path).path
                if not self.authorized():
                    self.send_json(403, {"error": "Phiên Control Center không hợp lệ"})
                    return
                try:
                    body = self.body()
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
                    elif match := re.fullmatch(r"/api/jobs/(\d+)/(start|resume|pause|stop-after-stage|cancel|retry|rerun|ai-audit)", path):
                        job_id, action = int(match.group(1)), match.group(2)
                        if action == "start":
                            detectors = body.get("detectors")
                            if not isinstance(detectors, list):
                                raise ValueError("Hãy chọn ít nhất một nhóm cần kiểm tra")
                            result = center.scheduler.configure_and_queue(
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
                        if action == "decision":
                            result = record_review_decision(
                                project_root=center.root, queue_path=queue_path,
                                item_id=str(body["id"]), decision=str(body["decision"]),
                                note=body.get("note"), full_frame=bool(body.get("full_frame", False)),
                                actor="control_center_user", transport="control_center")
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
                    else:
                        self.send_json(404, {"error": "Không tìm thấy"}); return
                    self.send_json(200, result)
                except (KeyError, TypeError, ValueError) as error:
                    self.send_json(400, {"error": str(error)})
                except Exception as error:
                    self.send_json(500, {"error": str(error)})

        try:
            self.server = ThreadingHTTPServer((self.host, self.port), Handler)
            actual_port = self.server.server_port
            state = {"schema_version": 1, "pid": os.getpid(), "host": self.host,
                     "port": actual_port, "url": f"http://{self.host}:{actual_port}/",
                     "token": self.token, "started_at": now_iso()}
            _write_json(self.root / "state" / "control-center.json", state)
            self.scheduler.start()
            self.watcher.start()
            print(f"BiliFlow Control Center: {state['url']}", flush=True)
            self.server.serve_forever(poll_interval=0.5)
        finally:
            # A normal API shutdown already performs cleanup on its helper
            # thread. This branch handles Ctrl+C/startup failures without
            # calling HTTPServer.shutdown from the serve_forever thread.
            if not self._stopping.is_set():
                self._stopping.set()
                self.stop_ai_audits()
                self.stop_ai_login()
                self.watcher.shutdown()
                self.scheduler.shutdown(immediate=True)
                if self.server:
                    self.server.server_close()
                state_path = self.root / "state" / "control-center.json"
                if state_path.exists():
                    try:
                        state_path.unlink()
                    except OSError:
                        pass
                self.store.close()
                self.lock.close()

    def stop(self, *, immediate: bool = False) -> None:
        if self._stopping.is_set():
            return
        self._stopping.set()
        try:
            self.stop_ai_audits()
            self.stop_ai_login()
            self.watcher.shutdown()
            self.scheduler.shutdown(immediate=immediate)
            if self.server:
                self.server.shutdown()
                self.server.server_close()
        finally:
            state_path = self.root / "state" / "control-center.json"
            if state_path.exists():
                try:
                    state_path.unlink()
                except OSError:
                    pass
            self.store.close()
            self.lock.close()


def serve_control_center(*, project_root: Path, host: str = "127.0.0.1", port: int = 8765,
                         stable_seconds: float = 60.0, import_existing: bool = True) -> None:
    ControlCenter(project_root, host=host, port=port, stable_seconds=stable_seconds,
                  import_existing=import_existing).serve()
