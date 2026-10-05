// Runs the dashboard page script in node with a tiny fake DOM, fetch and
// localStorage. Prints one JSON object with the observations.
const vm = require('vm');
const fs = require('fs');
const script = fs.readFileSync(process.argv[2], 'utf8');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const options = [
  {id: 'advertising', label: 'Quảng cáo / logo', description: ''},
  {id: 'adult', label: '18+', description: ''},
  {id: 'gore', label: 'Máu me', description: ''},
  {id: 'violence', label: 'Bạo lực', description: ''},
];
const server = {jobs: [], posts: [], actions: [], delays: [], clock: 0, postDelay: 0, refuseNext: false, refuseAction: null, seq: 2, paused: false,
  previewCalls: [], cleanupPosts: [], cleanupRefuse: null, cleanupResults: null, cleanupRunning: false};
// Batch 4: Hủy / Ẩn / Hiện lại, "Kiểm tra lại Thùng rác", "Lưu trữ" and "Khôi phục bản xuất".
Object.assign(server, {flagPosts: [], recheckPosts: [], recheckFound: false, archivePreviewCalls: [], archivePosts: [], archiveRefuse: null, archiveResults: null, restorePosts: [], restoreRefuse: null, archiveRow: 0});
// The permanent delete flow (D1, D2): "Xóa video gốc" and "Xóa video" refuse a POST without confirm_permanent: true.
Object.assign(server, {deletePreviewCalls: [], deletePosts: [], deleteRefuse: null, deleteResults: null, deletePreviewError: null});
const CONFIRM_PERMANENT_MESSAGE = 'Thiếu xác nhận xóa vĩnh viễn (trang này có thể đã cũ). Tải lại trang, mở lại hộp thoại, đánh dấu “Tôi hiểu” rồi xóa.';
const TAB_KEYS = ['waiting', 'scan_queue', 'scanning', 'review', 'export', 'completed'];
// #44 was clicked before #45 (queue_seq), although #45 was touched later.
for (const id of [44, 45]) server.jobs.push({id, job_key: `ep-${id}`, source_path: `input/Tập ${id}.mp4`, state: 'QUEUED', updated_at: `2026-10-02T13:0${id - 40}:00`, priority: 100, queue_seq: id - 43, pending_stage: 'preflight', detector_groups: ['advertising'], content_style: 'live_action', profile: 'careful', progress: 0, ocr_recognition_batch_size: 1, fast_scan: true, active_queue_path: null});
// #48 uses a Windows path: the card and the confirm must show only the file name.
for (const id of [46, 47, 48]) server.jobs.push({id, job_key: `ep-${id}`, source_path: id === 48 ? ['E:', 'DungChung', 'BiliFlow', 'input', `Tập ${id}.mp4`].join(String.fromCharCode(92)) : `input/Tập ${id}.mp4`, state: 'NEEDS_METADATA', updated_at: `2026-10-02T12:${id}:00`, priority: 100, detector_groups: null, content_style: 'unknown', profile: 'careful', progress: 0, ocr_recognition_batch_size: 1, fast_scan: true, active_queue_path: null});
// Mirrors JobScheduler.queue_order(): waiting jobs by priority, queue_seq (NULL last), id.
function withQueue(jobs) {
  const waiting = jobs.filter(j => j.state === 'QUEUED' && !j.stop_mode && j.pending_stage !== null).sort((a, b) => (a.priority - b.priority) || ((a.queue_seq == null) - (b.queue_seq == null)) || ((a.queue_seq || 0) - (b.queue_seq || 0)) || (a.id - b.id));
  jobs.forEach(j => { const index = waiting.indexOf(j); j.queue_position = index < 0 ? null : index + 1; j.queue_kind = index < 0 ? null : (j.pending_stage === 'render' ? 'export' : 'scan'); });
  return {length: waiting.length, paused: server.paused};
}
const response = (status, value) => ({ok: status < 400, status, statusText: String(status), json: async () => JSON.parse(JSON.stringify(value))});
async function fakeFetch(url, opt = {}) {
  if (url === '/api/session') return response(200, {token: 't'});
  if (url === '/api/ai') return response(200, {ready: false, config: {}, message: 'mock'});
  if (url === '/api/status') {
    const snapshot = JSON.parse(JSON.stringify(server.jobs));
    const delay = server.delays.shift() || 0;
    if (delay) await sleep(delay);
    snapshot.sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
    const queue = withQueue(snapshot);
    return response(200, {jobs: snapshot, queue, scheduler_paused: server.paused, detector_options: options, resources: {}, active: null, source_cleanup_running: server.cleanupRunning});
  }
  if (url.startsWith('/api/source-cleanup/preview?ids=')) {
    const ids = url.slice('/api/source-cleanup/preview?ids='.length);
    server.previewCalls.push(ids);
    if (server.previewError) { const error = server.previewError; server.previewError = null; return response(400, {error}); }
    return response(200, server.preview(ids));
  }
  if (url === '/api/source-cleanup' && opt.method === 'POST') return cleanupPost(JSON.parse(opt.body || '{}'));
  if (url.startsWith('/api/job-delete/preview?ids=')) {
    const ids = url.slice('/api/job-delete/preview?ids='.length);
    server.deletePreviewCalls.push(ids);
    if (server.deletePreviewError) { const error = server.deletePreviewError; server.deletePreviewError = null; return response(400, {error}); }
    return response(200, server.deletePreview(ids));
  }
  if (url === '/api/job-delete' && opt.method === 'POST') return deletePost(JSON.parse(opt.body || '{}'));
  const flag =/^\/api\/jobs\/(\d+)\/(cancel|hide|unhide)$/.exec(url);
  if (flag && opt.method === 'POST') return flagAction(Number(flag[1]), flag[2]);
  if (url === '/api/source-recycle-check' && opt.method === 'POST') return recheckPost(JSON.parse(opt.body || '{}'));
  if (url.startsWith('/api/source-archive/preview?ids=')) {
    const ids = url.slice('/api/source-archive/preview?ids='.length);
    server.archivePreviewCalls.push(ids);
    return response(200, server.archivePreview(ids));
  }
  if (url === '/api/source-archive' && opt.method === 'POST') return archivePost(JSON.parse(opt.body || '{}'));
  if (url === '/api/source-archive/restore' && opt.method === 'POST') return restorePost(JSON.parse(opt.body || '{}'));
  const action = /^\/api\/jobs\/(\d+)\/(skip|unskip|review\/finalize)$/.exec(url);
  if (action && opt.method === 'POST') return jobAction(Number(action[1]), action[2], JSON.parse(opt.body || '{}'));
  const rerunMatch = /^\/api\/jobs\/(\d+)\/rerun$/.exec(url);
  if (rerunMatch && opt.method === 'POST') {
    const id = Number(rerunMatch[1]), body = JSON.parse(opt.body), job = server.jobs.find(x => x.id === id);
    server.posts.push({id, rerun: true, detectors: body.detectors});
    server.clock += 1;
    server.seq += 1;
    Object.assign(job, {state: 'QUEUED', queue_seq: server.seq, pending_stage: 'preflight', current_stage: null, detector_groups: body.detectors, updated_at: `2026-10-02T16:${String(server.clock).padStart(2, '0')}:00`});
    return response(200, job);
  }
  const match = /^\/api\/jobs\/(\d+)\/start$/.exec(url);
  if (match && opt.method === 'POST') {
    const id = Number(match[1]), body = JSON.parse(opt.body), job = server.jobs.find(x => x.id === id);
    server.posts.push({id, detectors: body.detectors});
    if (server.postDelay) await sleep(server.postDelay);
    if (server.refuseNext) { server.refuseNext = false; return response(400, {error: 'Lỗi thử nghiệm'}); }
    if (!['NEEDS_METADATA', 'DISCOVERED'].includes(job.state)) return response(400, {error: `Video #${id} đang ở trạng thái ${job.state}`});
    server.clock += 1;
    server.seq += 1;
    Object.assign(job, {state: 'QUEUED', queue_seq: server.seq, pending_stage: 'preflight', detector_groups: body.detectors, content_style: body.content_style, updated_at: `2026-10-02T14:${String(server.clock).padStart(2, '0')}:00`});
    return response(200, job);
  }
  return response(200, {});
}
// The server side of skip, unskip and finalize, as far as the dashboard sees it.
async function jobAction(id, name, body) {
  server.actions.push({id, name, body});
  // Whether the card's export panel was still open when the request left.
  const sent = server.page && (server.page.jobs.panels || []).find(x => x.className === 'export-panel' && x.dataset.jobId === String(id));
  if (server.openAtPost) server.openAtPost.push(sent ? sent.open : null);
  if (server.postDelay) await sleep(server.postDelay);
  if (server.refuseAction) { const error = server.refuseAction; server.refuseAction = null; return response(400, {error}); }
  const job = server.jobs.find(x => x.id === id);
  server.clock += 1;
  job.updated_at = `2026-10-02T15:${String(server.clock).padStart(2, '0')}:00`;
  if (name === 'skip') Object.assign(job, {state: 'SKIPPED', skip: {skipped_at: '2026-10-02T15:00:00+07:00'}});
  else if (name === 'unskip') Object.assign(job, {state: 'READY_TO_EXPORT', skip: null});
  else { server.seq += 1; Object.assign(job, {state: 'QUEUED', current_stage: 'render', pending_stage: 'render', queue_seq: server.seq}); return response(200, {status: 'QUEUED', output: `output/${id}.mp4`, export_size_policy: {mode: body.size_mode}}); }
  return response(200, job);
}
// The server side of "Xóa video gốc" (docs/DELETE_FLOW_PLAN.md section 5), as far as the dashboard sees it.
const fileName = job => job.source_path.split('/').pop();
const LOST_HINT = {eligible: true, kind: 'LOST', reason: null, size_bytes: 0};
server.preview = function (idsText) {
  const eligible = [], ineligible = [];
  for (const id of idsText.split(',').map(Number).sort((a, b) => a - b)) {
    const job = server.jobs.find(j => j.id === id), c = job && job.cleanup;
    if (job && c && c.eligible && !job.source_cleaned && !job.protected) eligible.push({job_id: id, name: fileName(job), file_name: fileName(job), source_path: job.source_path, size_bytes: c.size_bytes, kind: c.kind, output_path: c.output_name ? `output/${c.output_name}` : null, output_name: c.output_name, output_bytes: c.output_bytes, exported_at: c.exported_at, skipped_at: c.skipped_at, reports_bytes: job.reports_bytes || 0});
    else ineligible.push({job_id: id, name: job ? fileName(job) : '', reason: job ? ((c && !c.eligible && c.reason) || job.protected || 'Chỉ dọn được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)') : `Không tìm thấy video #${id}`});
  }
  return {preview_id: server.previewId || 'a'.repeat(64), eligible, ineligible, count: eligible.length,
    total_bytes: eligible.reduce((sum, x) => sum + x.size_bytes, 0), reports_bytes: eligible.reduce((sum, x) => sum + x.reports_bytes, 0)};
};
// The answer of both delete POSTs: DELETED removes the job, PARTIAL leaves it without its source.
function deleteAnswer(results) {
  const gone = results.filter(r => r.status === 'DELETED' || r.status === 'PARTIAL'), count = status => results.filter(r => r.status === status).length;
  return response(200, {results, deleted_count: count('DELETED'), deleted_bytes: gone.reduce((sum, r) => sum + r.size_bytes, 0), failed_count: count('FAILED'), partial_count: count('PARTIAL')});
}
function refusal(r) { return response(r.status, {error: r.error, code: r.code, ...(r.preview ? {preview: r.preview} : {})}); }
async function cleanupPost(body) {
  server.cleanupPosts.push(body);
  if (server.postDelay) await sleep(server.postDelay);
  if (body.confirm_permanent !== true) return response(400, {error: CONFIRM_PERMANENT_MESSAGE});
  if (server.cleanupRefuse) { const r = server.cleanupRefuse; server.cleanupRefuse = null; return refusal(r); }
  const planned = server.cleanupResults || body.job_ids.map(id => ({job_id: id, status: 'DELETED'}));
  server.cleanupResults = null;
  return deleteAnswer(planned.map(r => {
    const job = server.jobs.find(j => j.id === r.job_id), size = job.cleanup.size_bytes;
    if (r.status === 'DELETED') server.jobs.splice(server.jobs.indexOf(job), 1);
    else if (r.status === 'PARTIAL') Object.assign(job, {source_present: false, cleanup: {...job.cleanup, eligible: false, reason: 'Video gốc không còn trong thư mục input'}, delete: {...LOST_HINT}});
    return {job_id: r.job_id, name: fileName(job), status: r.status, message: r.message || 'Đã xóa vĩnh viễn video gốc và xóa video khỏi BiliFlow', size_bytes: size};
  }));
}
// The server side of "Xóa video" and "Dọn video mất gốc" (D2).
server.deletePreview = function (idsText) {
  const eligible = [], ineligible = [];
  for (const id of idsText.split(',').map(Number).sort((a, b) => a - b)) {
    const job = server.jobs.find(j => j.id === id), d = job && job.delete;
    if (job && d && d.eligible && !job.protected) eligible.push({job_id: id, name: fileName(job), file_name: fileName(job), source_path: job.source_path, kind: d.kind, state: job.state, size_bytes: d.size_bytes, reports_bytes: job.reports_bytes || 0});
    else ineligible.push({job_id: id, name: job ? fileName(job) : '', reason: job ? (job.protected || (d && d.reason) || 'Chỉ xóa được video đã hủy hoặc video không còn video gốc') : `Không tìm thấy video #${id}`});
  }
  return {preview_id: server.deletePreviewId || 'd'.repeat(64), eligible, ineligible, count: eligible.length,
    total_bytes: eligible.reduce((sum, x) => sum + x.size_bytes, 0), reports_bytes: eligible.reduce((sum, x) => sum + x.reports_bytes, 0)};
};
async function deletePost(body) {
  server.deletePosts.push(body);
  if (server.postDelay) await sleep(server.postDelay);
  if (body.confirm_permanent !== true) return response(400, {error: CONFIRM_PERMANENT_MESSAGE});
  if (server.deleteRefuse) { const r = server.deleteRefuse; server.deleteRefuse = null; return refusal(r); }
  const planned = server.deleteResults || body.job_ids.map(id => ({job_id: id, status: 'DELETED'}));
  server.deleteResults = null;
  return deleteAnswer(planned.map(r => {
    const job = server.jobs.find(j => j.id === r.job_id), lost = job.delete.kind === 'LOST', size = job.delete.size_bytes;
    if (r.status === 'DELETED') server.jobs.splice(server.jobs.indexOf(job), 1);
    else if (r.status === 'PARTIAL') Object.assign(job, {source_present: false, delete: {...LOST_HINT}});
    return {job_id: r.job_id, name: fileName(job), status: r.status, message: r.message || (lost ? 'Đã xóa video khỏi BiliFlow' : 'Đã xóa vĩnh viễn video gốc và xóa video khỏi BiliFlow'), size_bytes: size};
  }));
}
// Hủy, Ẩn khỏi danh sách and Hiện lại (batch 4), with the server's 409 codes.
async function flagAction(id, name) {
  server.flagPosts.push({id, name});
  if (server.postDelay) await sleep(server.postDelay);
  const job = server.jobs.find(x => x.id === id);
  server.clock += 1;
  if (name === 'cancel') {
    if (job.state === 'CANCELLED') return response(409, {error: `Video #${id} đã được hủy trước đó; không hủy thêm lần nữa.`, code: 'already_cancelled'});
    if (job.state === 'COMPLETED') return response(409, {error: `Video #${id} đã hoàn tất; không có gì để hủy.`, code: 'not_cancellable'});
    Object.assign(job, {state: 'CANCELLED', stop_mode: 'CANCELLED', pending_stage: null, queue_seq: null, hidden_at: null, updated_at: `2026-10-03T13:${String(server.clock).padStart(2, '0')}:00`});
    return response(200, job);
  }
  if (name === 'hide') {
    if (job.state !== 'CANCELLED') return response(409, {error: `Video #${id} chưa bị hủy; chỉ ẩn được video đã hủy.`, code: 'not_cancelled'});
    if (job.hidden_at) return response(409, {error: `Video #${id} đã được ẩn khỏi danh sách.`, code: 'already_hidden'});
    job.hidden_at = '2026-10-03T12:30:00+07:00';
    return response(200, job);
  }
  if (!job.hidden_at) return response(409, {error: `Video #${id} không bị ẩn.`, code: 'not_hidden'});
  job.hidden_at = null;
  return response(200, job);
}
// "Kiểm tra lại Thùng rác": only the summary fields of the row change, as on the server.
async function recheckPost(body) {
  server.recheckPosts.push(body);
  const cleanup = body.kind === 'source_cleanup';
  const job = server.jobs.find(j => (cleanup ? j.source_cleanup : j.source_archive) && (cleanup ? j.source_cleanup : j.source_archive).id === body.id);
  const found = !!server.recheckFound, stamp = '2026-10-03T13:30:00+07:00', what = cleanup ? 'video gốc' : 'bản xuất';
  if (cleanup) Object.assign(job.source_cleanup, found ? {verified: true, verified_later_at: stamp, rechecked_at: stamp} : {rechecked_at: stamp});
  else Object.assign(job.source_archive, found ? {export_verified: true, export_verified_later_at: stamp, export_rechecked_at: stamp} : {export_rechecked_at: stamp});
  return response(200, {kind: body.kind, id: body.id, job_id: job.id, found, record: found ? 'E:\\$Recycle.Bin\\S-1\\$I4RHHWK.mp4' : null, checked_at: stamp,
    message: found ? `Đã thấy ${what} trong Thùng rác của Windows.` : `Vẫn chưa thấy ${what} trong Thùng rác của Windows; hãy mở Thùng rác để kiểm tra. BiliFlow không thay đổi gì.`});
}
// The server side of "Lưu trữ" and "Khôi phục bản xuất" (batch 4), as far as the dashboard sees it.
server.archivePreview = function (idsText) {
  const eligible = [], ineligible = [];
  for (const id of idsText.split(',').map(Number).sort((a, b) => a - b)) {
    const job = server.jobs.find(j => j.id === id), a = job && job.archive;
    if (job && a && a.eligible) eligible.push({job_id: id, name: fileName(job), file_name: fileName(job), kind: a.kind, size_bytes: a.size_bytes, archive_path: `archive/sources/${job.job_key}/${fileName(job)}`, output_name: a.output_name, output_bytes: a.output_bytes, manifest_bytes: a.manifest_bytes, exported_at: a.exported_at, skipped_at: a.skipped_at});
    else ineligible.push({job_id: id, name: job ? fileName(job) : '', reason: job ? ((a && a.reason) || 'Chỉ lưu trữ được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)') : `Không tìm thấy video #${id}`});
  }
  const exported = eligible.filter(x => x.kind === 'EXPORTED'), freed = exported.reduce((sum, x) => sum + x.output_bytes + (x.manifest_bytes || 0), 0), used = 11823971925;
  return {preview_id: server.archivePreviewId || 'c'.repeat(64), eligible, ineligible, count: eligible.length, archive_bytes: eligible.reduce((sum, x) => sum + x.size_bytes, 0), freed_bytes: freed,
    recycle_bin: exported.length ? {volume: 'E:', used_bytes: used, items: 7, max_bytes: 52157218816, after_bytes: used + freed} : null, blocked: server.archiveBlocked || null};
};
function archiveRow(job, state, extra = {}) {
  server.archiveRow += 1;
  const a = job.archive, exported = a.kind === 'EXPORTED';
  return {id: server.archiveRow, state, kind: a.kind, size_bytes: a.size_bytes, file_name: fileName(job), source_path: job.source_path, archive_path: `archive/sources/${job.job_key}/${fileName(job)}`,
    output_name: a.output_name, output_bytes: a.output_bytes, created_at: '2026-10-03T13:00:00+07:00', archived_at: '2026-10-03T13:00:09+07:00', restored_at: null,
    export_recycled: exported, export_verified: exported, export_verified_at_archive: exported, export_verified_later_at: null, export_rechecked_at: null, warning: null, error: null, ...extra};
}
async function archivePost(body) {
  server.archivePosts.push(body);
  if (server.postDelay) await sleep(server.postDelay);
  if (server.archiveRefuse) { const r = server.archiveRefuse; server.archiveRefuse = null; return response(r.status, {error: r.error, code: r.code, ...(r.preview ? {preview: r.preview} : {})}); }
  const planned = server.archiveResults || body.job_ids.map(id => ({job_id: id, status: 'ARCHIVED'}));
  server.archiveResults = null;
  const results = planned.map(r => {
    const job = server.jobs.find(j => j.id === r.job_id), a = job.archive, exported = a.kind === 'EXPORTED';
    if (r.status === 'ARCHIVED' || r.status === 'UNVERIFIED') {
      const verified = r.status === 'ARCHIVED' && exported;
      Object.assign(job, {source_archive: archiveRow(job, 'ARCHIVED', {export_verified: verified, export_verified_at_archive: verified}), source_archived: true, source_present: false,
        archive: {...a, eligible: false, reason: 'Video gốc đã được lưu trữ'}, cleanup: {...(job.cleanup || {}), eligible: false, reason: 'Video gốc đang ở kho lưu trữ'}});
    } else if (r.status === 'FAILED') job.source_archive = archiveRow(job, 'FAILED', {error: r.message, archived_at: null});
    return {job_id: r.job_id, name: fileName(job), status: r.status, message: r.message || 'Đã lưu trữ video gốc', size_bytes: a.size_bytes, output_bytes: exported ? a.output_bytes : 0};
  });
  const moved = results.filter(r => r.status === 'ARCHIVED' || r.status === 'UNVERIFIED');
  return response(200, {results, archived_count: moved.length, freed_bytes: moved.reduce((sum, r) => sum + r.output_bytes, 0), failed_count: results.filter(r => r.status === 'FAILED').length, pending: results.filter(r => r.status === 'PENDING').length});
}
async function restorePost(body) {
  server.restorePosts.push(body);
  if (server.postDelay) await sleep(server.postDelay);
  if (server.restoreRefuse) { const r = server.restoreRefuse; server.restoreRefuse = null; return response(r.status, {error: r.error, code: r.code}); }
  const job = server.jobs.find(j => j.id === body.job_id), skipped = job.source_archive.kind === 'SKIPPED';
  server.clock += 1;
  Object.assign(job, {state: skipped ? 'SKIPPED' : 'READY_TO_EXPORT', source_archived: false, source_present: true, source_archive: {...job.source_archive, state: 'RESTORED', restored_at: '2026-10-03T14:00:00+07:00'},
    archive: {...job.archive, eligible: skipped, reason: skipped ? null : 'Chỉ lưu trữ được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)'}, updated_at: `2026-10-03T14:${String(server.clock).padStart(2, '0')}:00`});
  return response(200, {job_id: job.id, status: 'RESTORED', state: job.state, message: `Đã đưa video gốc của #${job.id} về input (SHA-256 khớp).`});
}
class FakeElement {
  constructor(tagName, attrs = {}) { Object.assign(this, {tagName, textContent: '', className: '', value: '', checked: false, listeners: {}}, attrs); this._html = ''; }
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
  dispatch(name) { const event = {type: name, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }}; (this.listeners[name] || []).forEach(fn => fn(event)); return event; }
  contains(other) { return other === this || (this.controls || []).includes(other); }
  set innerHTML(html) { this._html = html; if (this.id === 'jobs') { this.controls = parseControls(html); this.panels = parsePanels(html); } }
  get innerHTML() { return this._html; }
}
function attr(text, name) { const m = new RegExp(`\\b${name}="([^"]*)"`).exec(text); return m ? m[1] : null; }
function parseControls(html) {
  const els = [];
  for (const m of html.matchAll(/<input\b([^>]*)>/g)) els.push(new FakeElement('INPUT', {id: attr(m[1], 'id'), type: attr(m[1], 'type'), value: attr(m[1], 'value') || '', checked: /\schecked(\s|$)/.test(m[1]), detectorJob: attr(m[1], 'data-detector-job')}));
  for (const m of html.matchAll(/<select\b([^>]*)>([\s\S]*?)<\/select>/g)) {
    const opts = [...m[2].matchAll(/<option value="([^"]*)"\s*([^>]*)>/g)];
    const chosen = opts.find(o => /selected/.test(o[2])) || opts[0];
    els.push(new FakeElement('SELECT', {id: attr(m[1], 'id'), value: chosen ? chosen[1] : ''}));
  }
  return els;
}
// <details class="export-panel|rerun-panel" data-job-id="…" [open]> as fake elements.
function parsePanels(html) {
  const panels = [...html.matchAll(/<details class="(export-panel|rerun-panel)" data-job-id="(\d+)"\s*(open)?>/g)].map(m => new FakeElement('DETAILS', {className: m[1], dataset: {jobId: m[2]}, open: !!m[3]}));
  // Batch 4: the closed-by-default folds "Đã hủy", "Đã ẩn" and "Đã lưu trữ".
  const folds = [...html.matchAll(/<details class="phase-group phase-fold" data-fold="([a-z]+)"\s*(open)?/g)].map(m => new FakeElement('DETAILS', {className: 'phase-fold', dataset: {fold: m[1]}, open: !!m[2]}));
  return [...panels, ...folds];
}
function makeStorage(map) {
  return {getItem: k => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k), key: i => [...map.keys()][i] ?? null, get length() { return map.size; }};
}
function boot(storageMap, log, extra = {}) {
  const statics = {};
  const controlId = /^(det-all|style|profile|ocr|fast|size|size-gb|size-gb-wrap)-\d+$/;
  const document = {
    activeElement: null, listeners: {}, body: new FakeElement('BODY'),
    getElementById(id) {
      const jobs = statics.jobs;
      const control = (jobs && jobs.controls || []).find(x => x.id === id);
      if (control) return control;
      if (controlId.test(id)) return null;
      return statics[id] || (statics[id] = new FakeElement('DIV', {id}));
    },
    querySelectorAll(selector) {
      const m = /^\[data-detector-job="(\d+)"\]$/.exec(selector);
      if (m) return (statics.jobs.controls || []).filter(x => x.detectorJob === m[1]);
      const panels = /^\.(export-panel|rerun-panel)\[data-job-id\]$/.exec(selector);
      if (panels) return (statics.jobs.panels || []).filter(x => x.className === panels[1]);
      if (selector === '.phase-fold[data-fold]') return (statics.jobs.panels || []).filter(x => x.className === 'phase-fold');
      return [];
    },
    querySelector(selector) {
      if (selector === 'header') return extra.header || null;
      const panel = /^\.(export-panel|rerun-panel)\[data-job-id="(\d+)"\]$/.exec(selector);
      if (panel) return (statics.jobs.panels || []).find(x => x.className === panel[1] && x.dataset.jobId === panel[2]) || null;
      return null;
    },
    addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
    dispatch(name) { const event = {type: name, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }}; (this.listeners[name] || []).forEach(fn => fn(event)); return event; },
  };
  if (extra.documentElement) document.documentElement = extra.documentElement;
  statics.jobs = new FakeElement('DIV', {id: 'jobs'});
  // The <dialog> and its two buttons live outside #jobs (static markup).
  const dialog = new FakeElement('DIALOG', {id: 'cleanup-dialog', open: false});
  dialog.showModal = function () { this.open = true; this.shown = (this.shown || 0) + 1; };
  dialog.close = function () { this.open = false; this.dispatch('close'); };
  statics['cleanup-dialog'] = dialog;
  statics['cleanup-confirm'] = new FakeElement('BUTTON', {id: 'cleanup-confirm', disabled: true, textContent: 'Xóa vĩnh viễn'});
  statics['cleanup-cancel'] = new FakeElement('BUTTON', {id: 'cleanup-cancel', disabled: false, textContent: 'Hủy', focus() { this.focused = (this.focused || 0) + 1; }});
  // Batch 4: the archive <dialog> (static markup like the cleanup one).
  const archiveDialog = new FakeElement('DIALOG', {id: 'archive-dialog', open: false});
  archiveDialog.showModal = function () { this.open = true; this.shown = (this.shown || 0) + 1; };
  archiveDialog.close = function () { this.open = false; this.dispatch('close'); };
  statics['archive-dialog'] = archiveDialog;
  statics['archive-confirm'] = new FakeElement('BUTTON', {id: 'archive-confirm', disabled: true, textContent: 'Lưu trữ'});
  statics['archive-cancel'] = new FakeElement('BUTTON', {id: 'archive-cancel', disabled: false, textContent: 'Hủy', focus() { this.focused = (this.focused || 0) + 1; }});
  // D4: the "Xóa video" <dialog> (static markup like the other two).
  const deleteDialog = new FakeElement('DIALOG', {id: 'delete-dialog', open: false});
  deleteDialog.showModal = function () { this.open = true; this.shown = (this.shown || 0) + 1; };
  deleteDialog.close = function () { this.open = false; this.dispatch('close'); };
  statics['delete-dialog'] = deleteDialog;
  statics['delete-confirm'] = new FakeElement('BUTTON', {id: 'delete-confirm', disabled: true, textContent: 'Xóa vĩnh viễn'});
  statics['delete-cancel'] = new FakeElement('BUTTON', {id: 'delete-cancel', disabled: false, textContent: 'Hủy', focus() { this.focused = (this.focused || 0) + 1; }});
  const sandbox = {
    document, fetch: fakeFetch, localStorage: makeStorage(storageMap), console,
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
    confirm: message => { log.confirms.push(message); return log.confirmAnswer; },
    alert: message => log.alerts.push(message),
    listeners: {}, addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
  };
  sandbox.window = sandbox;
  if (extra.scroll) {
    // A page whose job list starts listOffset px below the top of the document.
    const scroll = extra.scroll;
    sandbox.scrollTo = options => { scroll.calls.push(options); scroll.y = options.top; };
    Object.defineProperty(sandbox, 'scrollY', {get: () => scroll.y});
    statics.jobs.getBoundingClientRect = () => ({top: scroll.listOffset - scroll.y});
    statics['job-tabs'] = new FakeElement('NAV', {id: 'job-tabs', offsetHeight: scroll.barHeight, scrollLeft: 0, clientWidth: 600,
      querySelector(selector) { return selector === '.job-tab.active' ? scroll.activeTab : null; }});
  }
  const context = vm.createContext(sandbox);
  vm.runInContext(script, context);
  const run = code => vm.runInContext(code, context);
  return {run, document, jobs: statics.jobs, dialog, archiveDialog, deleteDialog, notice:() => document.getElementById('notice').textContent, noticeIsError: () => document.getElementById('notice').className.includes('error')};
}
function cards(page) {
  return page.jobs.innerHTML.split('<article class="job"').slice(1).map(chunk => {
    const id = Number(/class="job-title">#(\d+)/.exec(chunk)[1]);
    const badge = /class="state-badge[^"]*">([^<]*)</.exec(chunk)[1];
    const detectors = [...chunk.matchAll(/<input\b([^>]*)>/g)].map(m => m[1]).filter(a => /data-detector-job=/.test(a) && /\schecked(\s|$)/.test(a)).map(a => attr(a, 'value'));
    const ocr = (/<select id="ocr-\d+"[\s\S]*?<\/select>/.exec(chunk) || [''])[0].match(/<option value="(\d+)" selected>/);
    const queue = (/class="queue-badge"[^>]*>([^<]*)</.exec(chunk) || [null, null])[1];
    const values = [...chunk.matchAll(/class="status-value">([^<]*)</g)].map(m => m[1]);
    const details = [...chunk.matchAll(/class="status-detail">([^<]*)</g)].map(m => m[1]);
    const exportButton = /<button class="green" onclick="exportVideo\(\d+,this\)" ([^>]*)>([^<]*)</.exec(chunk);
    const pick = new RegExp(`<input type="checkbox" data-cleanup-job="${id}"([^>]*)>`).exec(chunk);
    const cleanupButton = new RegExp(`<button class="danger" onclick="openCleanup\\(\\[${id}\\],this\\)"([^>]*)>`).exec(chunk);
    const deleteButton = new RegExp(`<button class="danger" onclick="openDelete\\(\\[${id}\\],this\\)"([^>]*)>([^<]*)<`).exec(chunk);
    const sourceLine = /<div class="source-line tone-([a-z]+)">([^<]*)</.exec(chunk);
    return {id, badge, queue, scan: values[0], scanDetail: details[0], exportValue: values[3], exportDetail: details[3], hasStart: chunk.includes(`start(${id},`), startDisabled: chunk.includes(`start(${id},this)" disabled>Đang bắt đầu…`), title: /class="job-title">([^<]*)</.exec(chunk)[1], detectors, ocr: ocr ? Number(ocr[1]) : null,
      bucket: attr(chunk, 'data-bucket'), hasSkip: chunk.includes(`onclick="skipJob(${id},`), hasUnskip: chunk.includes(`unskipJob(${id},`), hasCancel: chunk.includes(`cancelJob(${id},`), hasRerun: chunk.includes('class="rerun-panel"'),
      cancelButton: (m => m ? {disabled: /\sdisabled(\s|$)/.test(m[1]), text: m[2]} : null)(new RegExp(`<button class="danger" onclick="cancelJob\\(${id},this\\)"([^>]*)>([^<]*)<`).exec(chunk)),
      hasHide: chunk.includes(`hideJob(${id},`), hideTitle: (m => m ? m[1] : null)(new RegExp(`onclick="hideJob\\(${id},this\\)"[^>]*title="([^"]*)"`).exec(chunk)),
      recheck: (m => m ? {kind: m[1], row: Number(m[2]), disabled: /\sdisabled(\s|$)/.test(m[3]), text: m[4]} : null)(new RegExp(`onclick="recheckBin\\('([a-z_]+)',(\\d+),${id},this\\)"([^>]*)>([^<]*)<`).exec(chunk)),
      archiveBadge: (/class="archive-badge"[^>]*>([^<]*)</.exec(chunk) || [null, null])[1], hasArchiveButton: chunk.includes(`openArchive([${id}]`),
      restore: (m => m ? {disabled: /\sdisabled(\s|$)/.test(m[1]), text: m[2]} : null)(new RegExp(`onclick="restoreArchive\\(${id},this\\)"([^>]*)>([^<]*)<`).exec(chunk)),
      pickLabel: (new RegExp(`data-cleanup-job="${id}"[^>]*>([^<]*)</label>`).exec(chunk) || [null, null])[1],
      hasExport: chunk.includes('class="export-panel"'), exportDisabled: exportButton ? /disabled/.test(exportButton[1]) : null, exportTitle: exportButton ? attr(exportButton[1], 'title') : null, exportReason: (/class="export-reason">([^<]*)</.exec(chunk) || [null, null])[1], exportError: (/class="export-error"[^>]*>([^<]*)</.exec(chunk) || [null, null])[1],
      hasCleanupPick: !!pick, cleanupChecked: pick ? /\schecked(\s|$)/.test(pick[1]) : null, hasCleanupButton: chunk.includes(`openCleanup([${id}]`),
      cleanupButtonDisabled: cleanupButton ? /\sdisabled(\s|$)/.test(cleanupButton[1]) : null, cleanupButtonTitle: cleanupButton ? attr(cleanupButton[1], 'title') : null,
      deleteButton: deleteButton ? {text: deleteButton[2], disabled: /\sdisabled(\s|$)/.test(deleteButton[1]), title: attr(deleteButton[1], 'title')} : null,
      protectedNote: (/class="cleanup-note protected-note">([^<]*)</.exec(chunk) || [null, null])[1],
      sourceLine: sourceLine ? sourceLine[2] : null, sourceTone: sourceLine ? sourceLine[1] : null, cleanupNote: (/class="cleanup-note">([^<]*)</.exec(chunk) || [null, null])[1],
      rerunDisabled: /<button disabled title="[^"]*">Chạy lại kiểm tra<\/button>/.test(chunk), unskipDisabled: /<button class="green" disabled title="[^"]*">Mở lại để xuất<\/button>/.test(chunk)};
  });
}
// The section headings (with counts) of the visible tab, in order.
function headings(page) {
  return [...page.jobs.innerHTML.matchAll(/class="phase-heading">([^<]*) <span>(\d+)<\/span>/g)].map(m => [m[1], Number(m[2])]);
}
// Batch 4: the folds of the visible tab ({key, open, label, count, ids}) and the compact hidden rows.
function folds(page) {
  // Folds end their tab; a fold's cards contain <details> of their own, so a fold runs to the next fold.
  const html = page.jobs.innerHTML;
  const starts = [...html.matchAll(/<details class="phase-group phase-fold" data-fold="([a-z]+)"\s*(open)?\s*ontoggle="[^"]*"><summary class="phase-heading">([^<]*) <span>(\d+)<\/span><\/summary>/g)];
  return starts.map((m, i) => {
    const body = html.slice(m.index + m[0].length, i + 1 < starts.length ? starts[i + 1].index : html.length);
    return {key: m[1], open: !!m[2], label: m[3], count: Number(m[4]), ids: [...body.matchAll(/class="job-title">#(\d+)|<div class="hidden-row" data-job-id="(\d+)"/g)].map(x => Number(x[1] || x[2]))};
  });
}
function hiddenRows(page) {
  return [...page.jobs.innerHTML.matchAll(/<div class="hidden-row" data-job-id="(\d+)"><span class="hidden-name">([^<]*)<\/span><span class="hidden-when">([^<]*)<\/span><button onclick="unhideJob\((\d+),this\)"([^>]*)>([^<]*)<\/button>/g)]
    .map(m => ({id: Number(m[1]), name: m[2], when: m[3], disabled: /\sdisabled(\s|$)/.test(m[5]), text: m[6]}));
}
function foldElement(page, key) { return (page.jobs.panels || []).find(x => x.className === 'phase-fold' && x.dataset.fold === key); }
// The tab bar: [key, label, count] in order, plus the active key.
function tabBar(page) {
  const html = page.document.getElementById('job-tabs').innerHTML;
  const tabs = [...html.matchAll(/<button class="job-tab ?(active)?" data-tab="([^"]*)"[^>]*><span>([^<]*)<\/span><span class="tab-count">(\d+)<\/span>/g)].map(m => [m[2], m[3], Number(m[4])]);
  const active = (/class="job-tab active" data-tab="([^"]*)"/.exec(html) || [null, null])[1];
  return {tabs, active};
}
// Every card of every tab (tagged with its tab); the user's tab is restored.
function allCards(page) {
  const previous = page.run('activeJobTab');
  const out = [];
  for (const key of TAB_KEYS) {
    page.run(`activeJobTab=${JSON.stringify(key)};renderJobs(true)`);
    for (const card of cards(page)) out.push({...card, tab: key});
  }
  page.run(`activeJobTab=${JSON.stringify(previous)};renderJobs(true)`);
  return out;
}
// The cleanup toolbar of the 'Hoàn tất' tab, as rendered into #jobs.
function toolbar(page) {
  const html = page.jobs.innerHTML;
  if (!html.includes('class="cleanup-toolbar"')) return null;
  const button = id => { const m = new RegExp(`<button id="${id}"([^>]*)>([^<]*)<`).exec(html); return m ? {text: m[2], disabled: /\sdisabled(\s|$)/.test(m[1]), title: attr(m[1], 'title')} : null; };
  return {summary: (/id="cleanup-summary">([^<]*)</.exec(html) || [null, null])[1], all: button('cleanup-all'), none: button('cleanup-none'), run: button('cleanup-run'),
    note: (/class="cleanup-toolbar"[\s\S]*?<span class="cleanup-note">([^<]*)</.exec(html) || [null, null])[1], first: html.indexOf('class="cleanup-toolbar"') < html.indexOf('<article')};
}
function selection(page) { return JSON.parse(page.run('JSON.stringify([...cleanupSelection].sort((a,b)=>a-b))')); }
function dialogState(page) {
  const confirm = page.document.getElementById('cleanup-confirm'), cancel = page.document.getElementById('cleanup-cancel'), body = page.document.getElementById('cleanup-dialog-body').innerHTML;
  return {open: page.dialog.open, body, confirm: confirm.textContent, confirmDisabled: !!confirm.disabled, cancelDisabled: !!cancel.disabled,
    ack: page.run('cleanupAck'), ackShown: body.includes('id="cleanup-ack"'), ackChecked: /id="cleanup-ack" checked/.test(body)};
}
// D4: the "Dọn video mất gốc" notice that heads every tab, and the "Xóa video" dialog.
function lostNoticeState(page) {
  const html = page.jobs.innerHTML, m = /<div class="lost-notice" role="group" aria-label="Video mất gốc"><span class="lost-summary" id="lost-summary">([^<]*)<\/span><button id="lost-run" class="danger" onclick="openLostCleanup\(this\)"([^>]*)>([^<]*)<\/button><span class="cleanup-note">([^<]*)<\/span><\/div>/.exec(html);
  return m ? {summary: m[1], button: m[3], disabled: /\sdisabled(\s|$)/.test(m[2]), title: attr(m[2], 'title'), note: m[4], first: m.index === 0} : null;
}
function deleteDialogState(page) {
  const confirm = page.document.getElementById('delete-confirm'), cancel = page.document.getElementById('delete-cancel'), body = page.document.getElementById('delete-dialog-body').innerHTML;
  const refused = (/<div class="cleanup-ineligible"><p>Không thể xóa:<\/p><ul>([\s\S]*?)<\/ul><\/div>/.exec(body) || [null, ''])[1];
  return {open: page.deleteDialog.open, confirm: confirm.textContent, confirmDisabled: !!confirm.disabled, cancelDisabled: !!cancel.disabled, ack: page.run('deleteAck'),
    ackShown: body.includes('id="delete-ack"'), ackText: (/id="delete-ack"[^>]*> <span>([^<]*)<\/span>/.exec(body) || [null, null])[1],
    rows: [...body.matchAll(/<tr><td data-label="Video">([^<]*)<\/td><td data-label="Loại">([^<]*)<\/td><td data-label="Sẽ xóa">([^<]*)<\/td><td data-label="Video gốc">([^<]*)<\/td><td data-label="Báo cáo, log">([^<]*)<\/td><\/tr>/g)].map(m => m.slice(1, 6)),
    summary: (/class="cleanup-summary">([^<]*)</.exec(body) || [null, null])[1], alert: (/class="cleanup-alert" role="alert">([^<]*)</.exec(body) || [null, null])[1],
    ineligible: [...refused.matchAll(/<li>([^<]*)<\/li>/g)].map(m => m[1]), body};
}
function tabOf(page, id) { const card = allCards(page).find(c => c.id === id); return card ? card.tab : null; }
function setDetectors(page, id, values) {
  page.jobs.controls.filter(x => x.detectorJob === String(id)).forEach(x => { x.checked = values.includes(x.value); });
  page.run(`captureDetectorDraft(${id})`);
}
async function queueScenario() {
  // Shared FIFO: an export and two scans wait; #64 was paused out of the queue.
  server.jobs.length = 0;
  const base = {priority: 100, detector_groups: ['advertising'], content_style: 'animation', profile: 'careful', ocr_recognition_batch_size: 1, fast_scan: true, active_queue_path: null};
  server.jobs.push({...base, id: 60, job_key: 'ep-60', source_path: 'input/Tập 60.mp4', state: 'QUEUED', current_stage: 'render', pending_stage: 'render', queue_seq: 1, progress: 1, updated_at: '2026-10-02T10:00:00', active_queue_path: 'reports/jobs/ep-60/review-queue.json'});
  server.jobs.push({...base, id: 61, job_key: 'ep-61', source_path: 'input/Tập 61.mp4', state: 'QUEUED', pending_stage: 'preflight', queue_seq: 3, progress: 0, updated_at: '2026-10-02T09:00:00'});
  server.jobs.push({...base, id: 62, job_key: 'ep-62', source_path: 'input/Tập 62.mp4', state: 'QUEUED', pending_stage: 'text', queue_seq: 2, progress: 0.4, updated_at: '2026-10-02T11:00:00'});
  server.jobs.push({...base, id: 63, job_key: 'ep-63', source_path: 'input/Tập 63.mp4', state: 'READY_TO_EXPORT', progress: 1, updated_at: '2026-10-02T12:00:00', active_queue_path: 'reports/jobs/ep-63/review-queue.json'});
  server.jobs.push({...base, id: 64, job_key: 'ep-64', source_path: 'input/Tập 64.mp4', state: 'PAUSED', stop_mode: 'PAUSED', queue_seq: 4, progress: 0.2, updated_at: '2026-10-02T12:30:00'});
  const out = {};
  const style = {props: {}, setProperty(name, value) { this.props[name] = value; }};
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(new Map(), log, {documentElement: {style}, header: {offsetHeight: 72}});
  await sleep(30);
  out.header_h = style.props['--header-h'] || null;
  out.resize_listener = (page.run('window.listeners.resize') || []).length;
  out.default_tab = page.run('activeJobTab');
  out.cards = allCards(page);
  page.run("selectJobTab('export')");
  out.export_headings = headings(page);
  out.worker = page.document.getElementById('worker').textContent;
  page.run("selectJobTab('scanning')");
  out.scanning_tab = page.jobs.innerHTML;
  server.paused = true;
  await page.run('load()');
  out.paused = allCards(page);
  out.paused_worker = page.document.getElementById('worker').textContent;
  // "Chạy lại kiểm tra" on the reviewed #63 says where the new run waits.
  page.run("selectJobTab('review')");
  await page.run('rerun(63)');
  out.rerun_notice = page.notice();
  out.rerun_notice_is_error = page.document.getElementById('notice').className.includes('error');
  out.rerun_posts = server.posts.filter(x => x.rerun);
  console.log(JSON.stringify(out));
}
const STATE_FIXTURE = [
  ['NEEDS_METADATA', 'waiting'], ['DISCOVERED', 'waiting'], ['QUEUED', 'scan_queue', {pending_stage: 'preflight'}],
  ['QUEUED', 'export', {pending_stage: 'render', current_stage: 'render'}], ['QUEUED', 'waiting', {stop_mode: 'AFTER_STAGE', pending_stage: 'text'}],
  ['QUEUED', 'waiting', {pending_stage: null}], ['PREFLIGHT', 'scanning'], ['SCANNING_SAFETY', 'scanning'], ['SCANNING_TEXT', 'scanning'],
  ['SCANNING_LOGO', 'scanning'], ['LOCALIZING_REGIONS', 'scanning'], ['BUILDING_REVIEW', 'scanning'], ['AI_AUDITING', 'scanning'],
  ['WAITING_REVIEW', 'review'], ['READY_TO_EXPORT', 'review'], ['RENDERING', 'export'], ['VERIFYING', 'export'], ['COMPLETED', 'completed'],
  ['SKIPPED', 'completed'], ['CANCELLED', 'waiting'], ['FAILED', 'waiting'], ['FAILED', 'waiting', {current_stage: 'render'}],
  ['PAUSED', 'waiting'], ['INTERRUPTED_RECOVERABLE', 'waiting'], ['WEIRD', 'waiting'],
];
const BASE = {priority: 100, detector_groups: ['advertising'], content_style: 'animation', profile: 'careful', ocr_recognition_batch_size: 1, fast_scan: true, active_queue_path: null, progress: 0};
async function tabsScenario() {
  server.jobs.length = 0;
  STATE_FIXTURE.forEach(([state, expected, extra], index) => {
    const id = 100 + index;
    server.jobs.push({...BASE, id, job_key: `ep-${id}`, source_path: `input/${id}.mp4`, state, expected, queue_seq: state === 'QUEUED' ? 50 - index : null, updated_at: `2026-10-02T10:${String(index).padStart(2, '0')}:00`, ...(extra || {})});
  });
  // A second scan click, clicked before #102: the scan tab follows the queue.
  server.jobs.push({...BASE, id: 130, job_key: 'ep-130', source_path: 'input/130.mp4', state: 'QUEUED', expected: 'scan_queue', pending_stage: 'preflight', queue_seq: 1, updated_at: '2026-10-02T11:00:00'});
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(new Map(), log);
  await sleep(30);
  const out = {default_tab: page.run('activeJobTab'), expected: Object.fromEntries(server.jobs.map(j => [j.id, j.expected]))};
  out.cards = allCards(page).map(c => [c.id, c.tab, c.bucket]);
  out.bar = tabBar(page);
  out.headings = {};
  for (const key of TAB_KEYS) { page.run(`selectJobTab('${key}')`); out.headings[key] = headings(page); if (key === 'scan_queue') out.scan_order = cards(page).map(c => c.id); }
  // The user's tab is kept across polls even when another tab gets work.
  page.run("selectJobTab('completed')");
  server.jobs.find(j => j.id === 102).state = 'PREFLIGHT';
  await page.run('load()');
  out.tab_after_poll = page.run('activeJobTab');
  console.log(JSON.stringify(out));
}
async function skipScenario() {
  server.jobs.length = 0;
  const summary = (main, decisions, eligible) => ({status: 'READY_FOR_EDIT_PLAN', main_items: main, advisory_items: 193, pending: 0, decisions, export_size_policy: null, skip_eligible: eligible});
  server.jobs.push({...BASE, id: 60, job_key: 'ep-60', source_path: 'input/Tập 30.mp4', state: 'READY_TO_EXPORT', progress: 1, active_queue_path: 'reports/jobs/ep-60/review-queue.json', updated_at: '2026-10-02T10:00:00', source_present: true, review_summary: summary(0, {}, true)});
  server.jobs.push({...BASE, id: 44, job_key: 'ep-44', source_path: 'input/Tập 14.mp4', state: 'READY_TO_EXPORT', progress: 1, active_queue_path: 'reports/jobs/ep-44/review-queue.json', updated_at: '2026-10-02T10:01:00', source_present: true, review_summary: summary(2, {KEEP: 2}, true)});
  server.jobs.push({...BASE, id: 41, job_key: 'ep-41', source_path: 'input/Tập 11.mp4', state: 'READY_TO_EXPORT', progress: 1, active_queue_path: 'reports/jobs/ep-41/review-queue.json', updated_at: '2026-10-02T10:02:00', source_present: true, review_summary: summary(3, {BLUR: 1, KEEP: 2}, false)});
  server.jobs.push({...BASE, id: 70, job_key: 'ep-70', source_path: 'input/Tập 40.mp4', state: 'SKIPPED', progress: 1, active_queue_path: 'reports/jobs/ep-70/review-queue.json', updated_at: '2026-10-02T09:00:00', source_present: true, review_summary: summary(0, {}, true), skip: {skipped_at: '2026-10-02T09:00:00+07:00'}});
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(new Map(), log);
  await sleep(30);
  const out = {default_tab: page.run('activeJobTab')};
  out.before = allCards(page);
  page.run("selectJobTab('completed')");
  out.completed_headings = headings(page);
  page.run("selectJobTab('review')");
  // Cancel posts nothing.
  log.confirmAnswer = false;
  await page.run('skipJob(60)');
  out.cancel_actions = server.actions.length;
  out.confirm_text = log.confirms[0];
  // A double click posts once and asks once.
  log.confirms.length = 0; log.confirmAnswer = true; server.postDelay = 80;
  await Promise.all([page.run('skipJob(60)'), page.run('skipJob(60)')]);
  server.postDelay = 0;
  out.skip_actions = server.actions.slice();
  out.skip_confirms = log.confirms.length;
  out.skip_notice = page.notice();
  out.tab_after_skip = page.run('activeJobTab');
  out.after_skip = allCards(page).find(c => c.id === 60);
  page.run("selectJobTab('completed')");
  out.completed_after_skip = headings(page);
  // Mở lại để xuất returns #70 to the review tab.
  server.actions.length = 0; log.confirms.length = 0;
  await page.run('unskipJob(70)');
  out.unskip_actions = server.actions.slice();
  out.unskip_confirm = log.confirms[0];
  out.unskip_notice = page.notice();
  out.after_unskip = allCards(page).find(c => c.id === 70);
  // A refusal is shown as an error and the card stays where it was.
  server.refuseAction = 'Video có cảnh chính không phải Giữ nguyên';
  await page.run('skipJob(44)');
  out.refused_notice = page.notice();
  out.refused_is_error = page.document.getElementById('notice').className.includes('error');
  out.refused_tab = tabOf(page, 44);
  console.log(JSON.stringify(out));
}
async function exportScenario() {
  server.jobs.length = 0;
  const ready = {status: 'READY_FOR_EDIT_PLAN', main_items: 2, advisory_items: 0, pending: 0, decisions: {BLUR: 1, KEEP: 1}, export_size_policy: null, skip_eligible: false};
  server.jobs.push({...BASE, id: 60, job_key: 'ep-60', source_path: 'input/Tập 30.mp4', state: 'READY_TO_EXPORT', progress: 1, active_queue_path: 'q', updated_at: '2026-10-02T10:00:00', source_present: true, review_summary: ready});
  server.jobs.push({...BASE, id: 61, job_key: 'ep-61', source_path: 'input/Tập 31.mp4', state: 'READY_TO_EXPORT', progress: 1, active_queue_path: 'q', updated_at: '2026-10-02T10:01:00', source_present: true, review_summary: {...ready, status: 'REVIEW_REQUIRED', pending: 1}});
  server.jobs.push({...BASE, id: 1, job_key: 'ep-1', source_path: 'input/Tập 1.mp4', state: 'READY_TO_EXPORT', progress: 1, active_queue_path: 'q', updated_at: '2026-10-02T10:02:00', source_present: false, review_summary: ready});
  server.jobs.push({...BASE, id: 62, job_key: 'ep-62', source_path: 'input/Tập 32.mp4', state: 'READY_TO_EXPORT', progress: 1, active_queue_path: 'q', updated_at: '2026-10-02T10:03:00', source_present: true, review_summary: {...ready, export_size_policy: {mode: 'custom', maximum_output_gb: 2}}});
  server.jobs.push({...BASE, id: 63, job_key: 'ep-63', source_path: 'input/Tập 33.mp4', state: 'QUEUED', current_stage: 'render', pending_stage: 'render', queue_seq: 1, progress: 1, active_queue_path: 'q', updated_at: '2026-10-02T10:04:00', source_present: true});
  server.jobs.push({...BASE, id: 64, job_key: 'ep-64', source_path: 'input/Tập 34.mp4', state: 'RENDERING', current_stage: 'render', progress: 1, active_queue_path: 'q', updated_at: '2026-10-02T10:05:00', source_present: true, render_progress: {percent: 12.5}});
  // A paused export and a cancelled export (the render stage was retired) wait in "Đang chờ xử lý".
  server.jobs.push({...BASE, id: 65, job_key: 'ep-65', source_path: 'input/Tập 35.mp4', state: 'PAUSED', stop_mode: 'PAUSED', current_stage: 'render', queue_seq: 2, progress: 1, active_queue_path: 'q', updated_at: '2026-10-02T10:06:00', source_present: true});
  server.jobs.push({...BASE, id: 66, job_key: 'ep-66', source_path: 'input/Tập 36.mp4', state: 'CANCELLED', current_stage: 'render', progress: 1, active_queue_path: 'q', updated_at: '2026-10-02T10:07:00', source_present: true});
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(new Map(), log);
  await sleep(30);
  const out = {};
  out.before = allCards(page);
  page.run("selectJobTab('review')");
  out.review_headings = headings(page);
  const panel = id => page.jobs.panels.find(x => x.className === 'export-panel' && x.dataset.jobId === String(id));
  const control = id => page.jobs.controls.find(x => x.id === id);
  // #62 preselects its saved custom policy, as on the review page.
  out.preset_62 = [control('size-62').value, control('size-gb-62').value];
  // Open #60's panel, choose a custom 2.5 GB maximum; the choice survives a poll.
  panel(60).open = true;
  control('size-60').value = 'custom';
  control('size-gb-60').value = '2.5';
  page.run('exportSizeChanged(60)');
  await page.run('load()');
  out.kept_after_load = {open: panel(60).open, mode: control('size-60').value, gb: control('size-gb-60').value};
  // Typing in the custom size field (focused INPUT type=number) defers the rebuild;
  // it happens shortly after the field loses focus.
  const typingBefore = page.jobs.innerHTML;
  page.document.activeElement = control('size-gb-60');
  out.focused_type = page.document.activeElement.type;
  page.run('markJobsInteraction()');
  // Past the 400 ms click window, so only the focused field holds the rebuild back.
  await sleep(500);
  server.jobs.find(x => x.id === 62).progress = 0.5;
  await page.run('load()');
  out.deferred_while_typing = page.jobs.innerHTML === typingBefore;
  await sleep(600);
  out.deferred_while_typing = out.deferred_while_typing && page.jobs.innerHTML === typingBefore;
  page.document.activeElement = null;
  await sleep(1000);
  out.rendered_after_blur = page.jobs.innerHTML !== typingBefore;
  out.kept_after_blur = {open: panel(60).open, mode: control('size-60').value, gb: control('size-gb-60').value};
  server.jobs.find(x => x.id === 62).progress = 1;
  await page.run('load()');
  // Cancel posts nothing and leaves the panel open.
  log.confirmAnswer = false;
  await page.run('exportVideo(60)');
  out.cancel = {actions: server.actions.length, open: panel(60).open, confirm: log.confirms[0]};
  // An invalid custom size never reaches confirm or the server.
  log.confirms.length = 0; log.confirmAnswer = true;
  control('size-gb-60').value = '0.01';
  await page.run('exportVideo(60)');
  out.invalid = {actions: server.actions.length, confirms: log.confirms.length, notice: page.notice(), error: page.document.getElementById('notice').className.includes('error')};
  // A server refusal reopens the panel with the error.
  control('size-gb-60').value = '2.5';
  server.refuseAction = 'Video gốc không còn trong input; không thể xuất.';
  server.page = page; server.openAtPost = [];
  await page.run('exportVideo(60)');
  out.refused = {actions: server.actions.length, open: panel(60).open, notice: page.notice(), error: page.document.getElementById('notice').className.includes('error'), openAtPost: server.openAtPost.slice()};
  // The error also shows inside the reopened panel (after the deferred re-render), as on the review page.
  await sleep(600);
  const refusedCard = allCards(page).find(c => c.id === 60);
  out.refused.panelError = refusedCard.exportError;
  out.refused.openAfterRender = panel(60).open;
  // OK: one confirm, one POST with the review page's body, the panel closes.
  server.actions.length = 0; log.confirms.length = 0; server.openAtPost = []; server.postDelay = 80;
  await Promise.all([page.run('exportVideo(60)'), page.run('exportVideo(60)')]);
  server.postDelay = 0;
  out.ok = {actions: server.actions.slice(), confirms: log.confirms.slice(), notice: page.notice(), openAtPost: server.openAtPost.slice(), tab: tabOf(page, 60), card: allCards(page).find(c => c.id === 60), noticeClass: page.document.getElementById('notice').className};
  // A job whose review is not finished alerts the gate and posts nothing.
  server.actions.length = 0; log.alerts.length = 0;
  await page.run('exportVideo(61)');
  out.gate = {actions: server.actions.length, alerts: log.alerts.slice()};
  console.log(JSON.stringify(out));
}
async function cleanupScenario() {
  server.jobs.length = 0;
  const done = {...BASE, state: 'COMPLETED', progress: 1, active_queue_path: 'q', source_present: true, source_cleaned: false, source_cleanup: null, protected: null, delete: null};
  const exported = (size, output, extra = {}) => ({eligible: true, kind: 'EXPORTED', reason: null, size_bytes: size, output_name: output, output_bytes: 104857600, exported_at: '2026-10-02T15:00:00+07:00', skipped_at: null, ...extra});
  const skippedHint = size => ({eligible: true, kind: 'SKIPPED', reason: null, size_bytes: size, output_name: null, output_bytes: null, exported_at: null, skipped_at: '2026-10-02T14:00:00+07:00'});
  const row = (id, state, job, size, extra = {}) => ({id, state, kind: 'EXPORTED', size_bytes: size, file_name: job.split('/').pop(), source_path: job, created_at: '2026-10-03T08:00:00+07:00', finished_at: '2026-10-03T08:00:04+07:00', restored_at: null, verified: true, error: null, ...extra});
  server.jobs.push({...done, id: 42, job_key: 'ep-42', source_path: 'input/Tập 12.mp4', source_size_bytes: 252168775, updated_at: '2026-10-02T15:00:00', reports_bytes: 52428800, cleanup: exported(252168775, 'ep-42-reviewed.mp4')});
  server.jobs.push({...done, id: 60, job_key: 'ep-60', source_path: 'input/Tập 30.mp4', state: 'SKIPPED', source_size_bytes: 248000000, updated_at: '2026-10-02T14:00:00', reports_bytes: 20971520, skip: {skipped_at: '2026-10-02T14:00:00+07:00'}, cleanup: skippedHint(248000000)});
  server.jobs.push({...done, id: 45, job_key: 'ep-45', source_path: 'input/Tập 15.mp4', source_size_bytes: 250000000, updated_at: '2026-10-02T13:00:00', reports_bytes: 10485760, source_cleanup: row(1, 'RESTORED', 'input/Tập 15.mp4', 250000000, {restored_at: '2026-10-02T09:00:00+07:00'}), cleanup: exported(250000000, 'ep-45-reviewed.mp4')});
  server.jobs.push({...done, id: 41, job_key: 'ep-41', source_path: 'input/Tập 11.mp4', source_size_bytes: 256115645, updated_at: '2026-10-02T12:00:00', source_present: false, source_cleaned: true, source_cleanup: row(2, 'RECYCLED', 'input/Tập 11.mp4', 256115645), cleanup: exported(256115645, 'ep-41-reviewed.mp4', {eligible: false, reason: 'Video gốc đã được dọn trước đó'})});
  server.jobs.push({...done, id: 70, job_key: 'ep-70', source_path: 'input/Tập 40.mp4', state: 'SKIPPED', source_size_bytes: 240000000, updated_at: '2026-10-02T11:30:00', skip: {skipped_at: '2026-10-02T11:00:00+07:00'}, source_present: false, source_cleaned: true, source_cleanup: row(3, 'RECYCLED', 'input/Tập 40.mp4', 240000000, {kind: 'SKIPPED', verified: false}), cleanup: {...skippedHint(240000000), eligible: false, reason: 'Video gốc đã được dọn trước đó'}});
  server.jobs.push({...done, id: 37, job_key: 'ep-37', source_path: 'input/Tập 7.mp4', source_size_bytes: 230000000, updated_at: '2026-10-02T11:00:00', cleanup: exported(230000000, 'ep-37-reviewed.mp4', {eligible: false, reason: 'Không thấy bản xuất trong thư mục output (đã bị dời hoặc đổi tên?)'})});
  server.jobs.push({...done, id: 3, job_key: 'ep-3', source_path: 'input/Tập 3.mp4', source_size_bytes: 220000000, updated_at: '2026-10-02T10:00:00', source_present: false, cleanup: exported(220000000, 'ep-3-reviewed.mp4', {eligible: false, reason: 'Video gốc không còn trong thư mục input'})});
  const job = id => server.jobs.find(j => j.id === id);
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(new Map(), log);
  await sleep(30);
  const out = {};
  const byId = () => Object.fromEntries(cards(page).map(c => [c.id, c]));
  page.run("selectJobTab('completed')");
  out.cards = byId();
  out.headings = headings(page);
  out.toolbar0 = toolbar(page);
  out.selection0 = selection(page);
  // A focused checkbox (unlike a text field) never holds the 3 s rebuild back.
  const htmlBeforeFocus = page.jobs.innerHTML;
  page.document.activeElement = page.jobs.controls.find(x => x.type === 'checkbox');
  out.focused_checkbox = !!page.document.activeElement;
  job(37).progress = 0.9;
  await page.run('load()');
  out.checkbox_focus_rendered = page.jobs.innerHTML !== htmlBeforeFocus;
  page.document.activeElement = null;
  job(37).progress = 1;
  await page.run('load()');
  // "Chọn tất cả", then untick #45: the toolbar is updated in place, #jobs is not rebuilt.
  page.run('selectAllCleanup()');
  out.after_all = {selection: selection(page), toolbar: toolbar(page), checked: Object.fromEntries(cards(page).filter(c => c.hasCleanupPick).map(c => [c.id, c.cleanupChecked]))};
  const htmlBeforeToggle = page.jobs.innerHTML;
  page.run('toggleCleanup(45,false)');
  const run = page.document.getElementById('cleanup-run');
  out.after_toggle = {selection: selection(page), html_unchanged: page.jobs.innerHTML === htmlBeforeToggle, run_text: run.textContent, run_disabled: !!run.disabled,
    summary: page.document.getElementById('cleanup-summary').textContent, none_disabled: !!page.document.getElementById('cleanup-none').disabled};
  await page.run('load()');
  out.after_load = {selection: selection(page), checked: Object.fromEntries(cards(page).filter(c => c.hasCleanupPick).map(c => [c.id, c.cleanupChecked])), toolbar: toolbar(page)};
  // A selected video that leaves "Hoàn tất" is dropped from the selection.
  Object.assign(job(60), {state: 'READY_TO_EXPORT', skip: null});
  await page.run('load()');
  out.after_ready = selection(page);
  Object.assign(job(60), {state: 'SKIPPED', skip: {skipped_at: '2026-10-02T14:00:00+07:00'}});
  await page.run('load()');
  out.after_back = selection(page);
  // A refused preview is an error notice; the dialog stays closed.
  server.previewError = 'Chọn từ 1 đến 50 video mỗi lần dọn.';
  await page.run('openCleanup([42])');
  out.preview_error = {notice: page.notice(), error: page.noticeIsError(), open: page.dialog.open};
  // The preview of #42 and #60.
  server.previewCalls.length = 0;
  await page.run('openCleanup([60,42,42])');
  out.preview = {calls: server.previewCalls.slice(), shown: page.dialog.shown, focused: page.document.getElementById('cleanup-cancel').focused || 0, ...dialogState(page)};
  // The confirm waits for the "Tôi hiểu" box: unticked, a click posts nothing.
  const confirmDisabled = () => !!page.document.getElementById('cleanup-confirm').disabled;
  await page.run('confirmCleanup()');
  out.unticked = {posts: server.cleanupPosts.length, confirmDisabled: confirmDisabled()};
  page.run('setCleanupAck(true)');
  out.ticked = {ack: page.run('cleanupAck'), confirmDisabled: confirmDisabled()};
  page.run('setCleanupAck(false)');
  out.unticked_again = {ack: page.run('cleanupAck'), confirmDisabled: confirmDisabled()};
  // Hủy posts nothing and forgets the tick; confirm without a preview posts nothing either.
  page.run('setCleanupAck(true)');
  page.run('closeCleanupDialog()');
  await page.run('confirmCleanup()');
  out.cancel = {posts: server.cleanupPosts.length, open: page.dialog.open, preview_cleared: page.run('cleanupPreview===null'), ack_cleared: page.run('cleanupAck===false')};
  // Reopened, the box starts unticked; ticked, two clicks on confirm make one POST and the dialog cannot be closed meanwhile.
  await page.run('openCleanup([42,60])');
  out.reopened = dialogState(page);
  page.run('setCleanupAck(true)');
  server.postDelay = 80;
  const first = page.run('confirmCleanup()'), second = page.run('confirmCleanup()');
  out.posting = {...dialogState(page), esc_prevented: page.dialog.dispatch('cancel').defaultPrevented, wait_shown: page.document.getElementById('cleanup-wait').hidden === false, ack_locked: !!page.document.getElementById('cleanup-ack').disabled};
  page.run('closeCleanupDialog()');
  out.posting.open_after_close_click = page.dialog.open;
  // A second Esc without new user activation is not cancelable in Chromium: the
  // dialog closes anyway (native close, then a 'close' event). It must reopen.
  const shownBefore = page.dialog.shown;
  page.dialog.close();
  out.posting.open_after_forced_close = page.dialog.open;
  out.posting.reshown = page.dialog.shown - shownBefore;
  out.posting.preview_kept = page.run('cleanupPreview!==null');
  await Promise.all([first, second]);
  server.postDelay = 0;
  // Deleted videos leave the list (the job is gone from BiliFlow).
  const after = byId();
  out.ok = {posts: server.cleanupPosts.slice(), notice: page.notice(), error: page.noticeIsError(), open: page.dialog.open, selection: selection(page),
    ids: Object.keys(after).map(Number).sort((a, b) => a - b), headings: headings(page), ack: page.run('cleanupAck'), esc_prevented_when_idle: page.dialog.dispatch('cancel').defaultPrevented};
  // 409 preview_changed shows the new list in the open dialog and clears the tick; the next confirm uses its id.
  await page.run('openCleanup([45])');
  page.run('setCleanupAck(true)');
  server.cleanupRefuse = {status: 409, error: 'Danh sách đã thay đổi, hãy xem lại.', code: 'preview_changed', preview: {...server.preview('45'), preview_id: 'b'.repeat(64)}};
  await page.run('confirmCleanup()');
  out.changed = {...dialogState(page), preview_id: page.run('cleanupPreview&&cleanupPreview.preview_id')};
  // Busy: the same list keeps its tick and can be sent again.
  page.run('setCleanupAck(true)');
  server.cleanupRefuse = {status: 409, error: 'Đang xóa video gốc; chờ lần xóa trước xong rồi thử lại.', code: 'busy'};
  await page.run('confirmCleanup()');
  out.busy = {...dialogState(page), body_sent: server.cleanupPosts[server.cleanupPosts.length - 1]};
  // Any other refusal (here the answer a page without confirm_permanent gets) is shown in the dialog.
  server.cleanupRefuse = {status: 400, error: CONFIRM_PERMANENT_MESSAGE};
  await page.run('confirmCleanup()');
  out.other_error = dialogState(page);
  page.run('closeCleanupDialog()');
  // #45 is deleted, #61 loses its source but a file stays (PARTIAL), #62 is open elsewhere (FAILED).
  for (const id of [61, 62]) server.jobs.push({...done, id, job_key: `ep-${id}`, source_path: `input/Tập ${id}.mp4`, source_size_bytes: 200000000 + id, updated_at: `2026-10-02T09:${id - 30}:00`, reports_bytes: 1048576, cleanup: exported(200000000 + id, `ep-${id}-reviewed.mp4`)});
  await page.run('load()');
  page.run('toggleCleanup(45,true);toggleCleanup(61,true);toggleCleanup(62,true)');
  await page.run('openSelectedCleanup(null)');
  out.partial_preview = {call: server.previewCalls[server.previewCalls.length - 1], ...dialogState(page)};
  page.run('setCleanupAck(true)');
  server.cleanupResults = [{job_id: 45, status: 'DELETED'},
    {job_id: 61, status: 'PARTIAL', message: 'Đã xóa video gốc nhưng còn dữ liệu chưa xóa được (reports/jobs/ep-61: đang được mở). Video vẫn có trong danh sách và không còn video gốc; bấm “Dọn video mất gốc” để xóa nốt.'},
    {job_id: 62, status: 'FAILED', message: 'Video gốc đang được mở (ví dụ trong trang duyệt hoặc một trình xem video). Đóng nó rồi thử lại; video gốc vẫn còn.'}];
  await page.run('confirmCleanup()');
  const partial = byId();
  out.partial = {post: server.cleanupPosts[server.cleanupPosts.length - 1], notice: page.notice(), error: page.noticeIsError(), open: page.dialog.open, selection: selection(page), gone45: !partial[45],
    card61: {line: partial[61].sourceLine, tone: partial[61].sourceTone, pick: partial[61].hasCleanupPick, cleanup: partial[61].hasCleanupButton, del: partial[61].deleteButton}, pick62: partial[62].cleanupChecked, lost: lostNoticeState(page)};
  // A selection without any video that can go.
  await page.run('openCleanup([3])');
  out.nothing = dialogState(page);
  page.run('closeCleanupDialog()');
  // While a source-file action runs (another tab), the card buttons, the toolbar and the notice wait.
  server.cleanupRunning = true;
  await page.run('load()');
  const running = byId();
  page.run('updateCleanupToolbar()');
  out.running = {card: running[62].cleanupButtonDisabled, title: running[62].cleanupButtonTitle, toolbar: toolbar(page), static_run_disabled: !!page.document.getElementById('cleanup-run').disabled,
    del61: running[61].deleteButton, lost: lostNoticeState(page)};
  server.cleanupRunning = false;
  // "Chọn tất cả" stops at 50 videos.
  for (let id = 200; id < 255; id++) server.jobs.push({...done, id, job_key: `ep-${id}`, source_path: `input/${id}.mp4`, source_size_bytes: 1048576, updated_at: '2026-10-01T10:00:00', cleanup: exported(1048576, `ep-${id}-reviewed.mp4`)});
  await page.run('load()');
  page.run('selectAllCleanup()');
  const eligible = cards(page).filter(c => c.hasCleanupPick).map(c => c.id).sort((a, b) => a - b);
  out.limit = {count: selection(page).length, eligible: eligible.length, first50: JSON.stringify(selection(page)) === JSON.stringify(eligible.slice(0, 50)), notice: page.notice(), toolbar: toolbar(page)};
  console.log(JSON.stringify(out));
}
async function scrollScenario() {
  // "Đang chờ xử lý" has 3 cards, "Hoàn tất" a long list, "Đang chạy xuất video" none.
  server.jobs.length = 0;
  for (let i = 0; i < 3; i++) server.jobs.push({...BASE, id: 10 + i, job_key: `ep-${10 + i}`, source_path: `input/${10 + i}.mp4`, state: 'NEEDS_METADATA', updated_at: `2026-10-03T10:0${i}:00`});
  for (let i = 0; i < 30; i++) server.jobs.push({...BASE, id: 100 + i, job_key: `ep-${100 + i}`, source_path: `input/${100 + i}.mp4`, state: 'COMPLETED', progress: 1, updated_at: `2026-10-03T09:${String(i).padStart(2, '0')}:00`});
  // The list starts 600 px down the page, under a 72 px header and a 56 px sticky tab bar: its top is at 472.
  const scroll = {y: 0, calls: [], listOffset: 600, barHeight: 56, activeTab: {offsetLeft: 900, offsetWidth: 160}};
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(new Map(), log, {header: {offsetHeight: 72}, scroll});
  await sleep(30);
  const out = {boot_calls: scroll.calls.length};
  const step = (tab, y) => { scroll.y = y; scroll.calls.length = 0; page.run(`selectJobTab('${tab}')`); return {calls: scroll.calls.map(c => [c.top, c.behavior]), y: scroll.y}; };
  // At the top of the page the list is below: scroll down to it.
  out.below = step('completed', 0);
  // Deep in the long list, another tab: scroll back up to the start of the list.
  out.above = step('waiting', 2400);
  // Already at the start of the list: nothing moves.
  out.aligned = step('completed', 472);
  out.near = step('waiting', 473);
  // An empty tab, then a full one: the position is computed after the render.
  out.empty = step('export', 1200);
  out.full = step('completed', scroll.y);
  // The tab bar scrolls sideways to show the active tab (it does not move the page).
  const bar = page.document.getElementById('job-tabs');
  out.bar_right = bar.scrollLeft;
  bar.scrollLeft = 700; scroll.activeTab = {offsetLeft: 100, offsetWidth: 160};
  step('waiting', 0);
  out.bar_left = bar.scrollLeft;
  // The 3 s poll never scrolls.
  scroll.calls.length = 0; scroll.y = 2000;
  await page.run('load()');
  out.poll_calls = scroll.calls.length;
  console.log(JSON.stringify(out));
}
async function cancelledScenario() {
  server.jobs.length = 0;
  const base = id => ({...BASE, id, job_key: `ep-${id}`, source_path: `input/Tập ${id}.mp4`, hidden_at: null});
  server.jobs.push({...base(5), state: 'QUEUED', pending_stage: 'preflight', queue_seq: 1, updated_at: '2026-10-03T12:05:00'});
  server.jobs.push({...base(6), state: 'PAUSED', stop_mode: 'PAUSED', updated_at: '2026-10-03T12:06:00'});
  // Job 2 was cancelled last (newest first), job 3 earlier; job 4 is cancelled and hidden.
  server.jobs.push({...base(2), state: 'CANCELLED', stop_mode: 'CANCELLED', updated_at: '2026-10-03T12:17:00'});
  server.jobs.push({...base(3), state: 'CANCELLED', stop_mode: 'CANCELLED', updated_at: '2026-10-03T11:00:00', active_queue_path: 'reports/jobs/ep-3/review-queue.json'});
  server.jobs.push({...base(4), state: 'CANCELLED', stop_mode: 'CANCELLED', updated_at: '2026-10-03T10:00:00', hidden_at: '2026-10-03T12:20:00+07:00'});
  // A flag older code left on a job that is no longer cancelled: it is not hidden.
  server.jobs.push({...base(8), state: 'FAILED', updated_at: '2026-10-03T09:30:00', hidden_at: '2026-10-03T08:00:00+07:00'});
  server.jobs.push({...base(7), state: 'COMPLETED', progress: 1, updated_at: '2026-10-03T09:00:00'});
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(new Map(), log);
  await sleep(30);
  const out = {};
  const card = id => allCards(page).find(c => c.id === id);
  out.bar = tabBar(page).tabs;
  page.run("selectJobTab('waiting')");
  out.headings = headings(page);
  out.folds = folds(page);
  out.hidden_rows = hiddenRows(page);
  out.card2 = card(2); out.card6 = card(6); out.card8 = card(8);
  // Hủy asks first; declining posts nothing.
  log.confirmAnswer = false;
  await page.run('cancelJob(6)');
  out.declined = {posts: server.flagPosts.length, confirm: log.confirms[0], card: card(6).cancelButton};
  // A double click asks once and posts once; the button says "Đang hủy…" meanwhile.
  log.confirms.length = 0; log.confirmAnswer = true; server.postDelay = 80;
  const button = {disabled: false, textContent: 'Hủy', isConnected: true};
  const first = page.run('cancelJob')(6, button), second = page.run('cancelJob')(6, button);
  await sleep(10);
  out.busy = {disabled: button.disabled, text: button.textContent};
  page.run('renderJobs(true)');
  out.busy_card = (allCards(page).find(c => c.id === 6) || {}).cancelButton;
  await Promise.all([first, second]);
  server.postDelay = 0;
  out.cancelled = {posts: server.flagPosts.slice(), confirms: log.confirms.length, notice: page.notice(), error: page.noticeIsError(), button: {disabled: button.disabled, text: button.textContent}, folds: folds(page)};
  // Another tab cancelled #5 already: the stale card's Hủy gets 409, reloads and says so without an error.
  Object.assign(server.jobs.find(j => j.id === 5), {state: 'CANCELLED', stop_mode: 'CANCELLED', pending_stage: null, queue_seq: null, updated_at: '2026-10-03T12:40:00'});
  server.flagPosts.length = 0; log.confirms.length = 0;
  await page.run('cancelJob(5)');
  out.repeat = {posts: server.flagPosts.slice(), notice: page.notice(), error: page.noticeIsError(), folds: folds(page)};
  // Ẩn khỏi danh sách: #3 leaves "Đã hủy" for "Đã ẩn"; the tab count drops.
  server.flagPosts.length = 0;
  await page.run('hideJob(3)');
  out.hidden = {posts: server.flagPosts.slice(), notice: page.notice(), error: page.noticeIsError(), folds: folds(page), rows: hiddenRows(page), count: tabBar(page).tabs.find(t => t[0] === 'waiting')[2]};
  // An opened fold stays open across polls; a closed one stays closed.
  foldElement(page, 'hidden').open = true;
  await page.run('load()');
  out.kept_open = folds(page).map(f => [f.key, f.open]);
  // Hiện lại: #4 goes back to "Đã hủy" (it keeps its place by its own time).
  server.flagPosts.length = 0;
  await page.run('unhideJob(4)');
  out.unhidden = {posts: server.flagPosts.slice(), notice: page.notice(), folds: folds(page)};
  // A refusal (another tab already showed it) is a notice, not an error, and reloads.
  server.jobs.find(j => j.id === 4).hidden_at = null;
  await page.run('unhideJob(2)');
  out.refused = {notice: page.notice(), error: page.noticeIsError()};
  // A tab holding only hidden jobs is not chosen by default and counts 0.
  server.jobs.length = 0;
  server.jobs.push({...base(20), state: 'CANCELLED', updated_at: '2026-10-03T10:00:00', hidden_at: '2026-10-03T12:20:00+07:00'});
  server.jobs.push({...base(21), state: 'COMPLETED', progress: 1, updated_at: '2026-10-03T09:00:00'});
  const fresh = boot(new Map(), log);
  await sleep(30);
  out.only_hidden = {default_tab: fresh.run('activeJobTab'), bar: tabBar(fresh).tabs.map(t => [t[0], t[2]])};
  fresh.run("selectJobTab('waiting')");
  out.only_hidden.folds = folds(fresh);
  out.only_hidden.empty = fresh.jobs.innerHTML.includes('class="empty"');
  console.log(JSON.stringify(out));
}
// Batch 4d: the archive toolbar, dialog, badge, fold, restore and bin re-check of "Hoàn tất".
function archiveToolbar(page) {
  const html = page.jobs.innerHTML, m = /<button id="archive-run"([^>]*)>([^<]*)</.exec(html);
  return m ? {text: m[2], disabled: /\sdisabled(\s|$)/.test(m[1]), summary: (/id="archive-summary">([^<]*)</.exec(html) || [null, null])[1]} : null;
}
function archiveDialogState(page) {
  const confirm = page.document.getElementById('archive-confirm'), cancel = page.document.getElementById('archive-cancel'), body = page.document.getElementById('archive-dialog-body').innerHTML;
  return {open: page.archiveDialog.open, confirm: confirm.textContent, confirmDisabled: !!confirm.disabled, cancelDisabled: !!cancel.disabled,
    rows: [...body.matchAll(/<td data-label="Lưu vào">([^<]*)</g)].map(m => m[1]), summary: (/class="cleanup-summary">([^<]*)</.exec(body) || [null, null])[1],
    bin: (/class="cleanup-bin">([^<]*)</.exec(body) || [null, null])[1], alert: (/class="cleanup-alert" role="alert">([^<]*)</.exec(body) || [null, null])[1],
    ineligible: [...body.matchAll(/<li>([^<]*)<\/li>/g)].map(m => m[1])};
}
async function archiveScenario() {
  server.jobs.length = 0;
  const done = {...BASE, state: 'COMPLETED', progress: 1, active_queue_path: 'q', source_present: true, source_cleaned: false, source_cleanup: null, source_archived: false, source_archive: null};
  const cleanable = (size, output) => ({eligible: true, kind: output ? 'EXPORTED' : 'SKIPPED', reason: null, size_bytes: size, output_name: output, output_bytes: output ? 104857600 : null, exported_at: output ? '2026-10-02T15:00:00+07:00' : null, skipped_at: output ? null : '2026-10-02T14:00:00+07:00'});
  const hint = (size, output, extra = {}) => ({...cleanable(size, output), manifest_bytes: output ? 2048 : null, ...extra});
  const add = (id, extra) => server.jobs.push({...done, id, job_key: `ep-${id}`, source_path: `input/Tập ${id}.mp4`, source_size_bytes: 300000000 + id, updated_at: `2026-10-02T15:${id}:00`, ...extra});
  const archived = (id, output) => ({source_present: false, source_archived: true, cleanup: {...cleanable(300000000 + id, output), eligible: false, reason: 'Video gốc đang ở kho lưu trữ'}, archive: hint(300000000 + id, output, {eligible: false, reason: 'Video gốc đã được lưu trữ'})});
  add(70, {cleanup: cleanable(300000070, 'ep-70-reviewed.mp4'), archive: hint(300000070, 'ep-70-reviewed.mp4')});
  add(71, {state: 'SKIPPED', skip: {skipped_at: '2026-10-02T14:00:00+07:00'}, cleanup: cleanable(300000071, null), archive: hint(300000071, null)});
  add(72, {cleanup: cleanable(300000072, 'ep-72-reviewed.mp4'), archive: hint(300000072, 'ep-72-reviewed.mp4', {eligible: false, reason: 'Kho lưu trữ đã có file “Tập 72.mp4” của video này; BiliFlow không ghi đè'})});
  // Archived: #73 exported (Windows did not confirm the export's bin record), #74 skipped, #75 still moving.
  add(73, archived(73, 'ep-73-reviewed.mp4'));
  add(74, {state: 'SKIPPED', skip: {skipped_at: '2026-10-02T13:00:00+07:00'}, ...archived(74, null)});
  add(75, archived(75, 'ep-75-reviewed.mp4'));
  server.archiveRow = 0;
  for (const [id, state, extra] of [[73, 'ARCHIVED', {export_verified: false, export_verified_at_archive: false, archived_at: '2026-10-03T11:00:00+07:00'}],
    [74, 'ARCHIVED', {archived_at: '2026-10-03T12:00:00+07:00'}], [75, 'PENDING', {archived_at: null, created_at: '2026-10-03T12:30:00+07:00'}]]) {
    const job = server.jobs.find(j => j.id === id);
    job.source_archive = archiveRow(job, state, extra);
  }
  const job = id => server.jobs.find(j => j.id === id);
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(new Map(), log);
  await sleep(30);
  const out = {};
  const view = c => ({pick: c.pickLabel, cleanup: c.hasCleanupButton, archive: c.hasArchiveButton, badge: c.archiveBadge, restore: c.restore, recheck: c.recheck, line: c.sourceLine, tone: c.sourceTone, rerun_disabled: c.rerunDisabled, unskip_disabled: c.unskipDisabled});
  const byId = () => Object.fromEntries(cards(page).map(c => [c.id, view(c)]));
  page.run("selectJobTab('completed')");
  out.headings = headings(page);
  out.folds = folds(page);
  out.completed_count = tabBar(page).tabs.find(t => t[0] === 'completed')[2];
  page.run('foldOpen.archived=true;renderJobs(true)');
  out.cards = byId();
  out.toolbar0 = archiveToolbar(page);
  // One "Chọn" box feeds both buttons; #72 can be cleaned but not archived.
  page.run('toggleCleanup(70,true);toggleCleanup(71,true);toggleCleanup(72,true);renderJobs(true)');
  out.toolbar1 = {archive: archiveToolbar(page), cleanup: toolbar(page).run.text, selection: selection(page)};
  await page.run('openSelectedArchive(null)');
  out.preview = {calls: server.archivePreviewCalls.slice(), ...archiveDialogState(page)};
  // A busy refusal keeps the dialog open with the server's text.
  server.archiveRefuse = {status: 409, code: 'busy', error: 'Đang dọn, lưu trữ hoặc khôi phục video gốc; chờ lượt trước xong rồi thử lại.'};
  await page.run('confirmArchive()');
  out.busy = archiveDialogState(page);
  // The list changed: the new preview (only #70) is shown.
  server.archiveRefuse = {status: 409, code: 'preview_changed', error: 'Danh sách đã thay đổi, hãy xem lại.', preview: server.archivePreview('70')};
  await page.run('confirmArchive()');
  out.changed = archiveDialogState(page);
  // Confirm: one POST; the dialog cannot be closed meanwhile.
  server.postDelay = 60;
  const posting = page.run('confirmArchive()');
  out.posting = {...archiveDialogState(page), esc_prevented: page.archiveDialog.dispatch('cancel').defaultPrevented, wait_shown: page.document.getElementById('archive-wait').hidden === false};
  page.run('closeArchiveDialog()');
  out.posting.open_after_close_click = page.archiveDialog.open;
  await posting;
  server.postDelay = 0;
  out.archived = {post: server.archivePosts[server.archivePosts.length - 1], posts: server.archivePosts.length, open: page.archiveDialog.open, selection: selection(page), notice: page.notice(), error: page.noticeIsError(), folds: folds(page).map(f => [f.key, f.ids]), card: byId()[70]};
  // The card button of a skipped video: no export, nothing to the bin.
  await page.run('openArchive([71],null)');
  out.skipped_preview = archiveDialogState(page);
  await page.run('confirmArchive()');
  out.skipped = {notice: page.notice(), error: page.noticeIsError(), card: byId()[71]};
  // "Kiểm tra lại Thùng rác" for the export of #73.
  server.recheckFound = true;
  await page.run(`recheckBin('archive_export',${job(73).source_archive.id},73,null)`);
  out.recheck = {posts: server.recheckPosts.slice(), notice: page.notice(), error: page.noticeIsError(), card: byId()[73]};
  // Restore #73: declined first, then confirmed; it leaves "Hoàn tất" for "Đang chờ duyệt".
  log.confirmAnswer = false;
  await page.run('restoreArchive(73,null)');
  out.declined = {posts: server.restorePosts.length, confirm: log.confirms[log.confirms.length - 1]};
  log.confirmAnswer = true;
  await page.run('restoreArchive(73,null)');
  out.restored = {posts: server.restorePosts.slice(), notice: page.notice(), error: page.noticeIsError(), tab: tabOf(page, 73), folds: folds(page).map(f => [f.key, f.ids])};
  // A refused restore (a file already at the input path) is an error notice; #74 stays archived.
  server.restoreRefuse = {status: 409, code: 'target_exists', error: 'Trong input đã có file “Tập 74.mp4”. BiliFlow không ghi đè: dời file đó ra khỏi input rồi bấm “Khôi phục bản xuất” lại.'};
  await page.run('restoreArchive(74,null)');
  out.refused = {notice: page.notice(), error: page.noticeIsError(), confirm: log.confirms[log.confirms.length - 1], card: byId()[74]};
  // While a cleanup, archive, restore or re-check runs, the buttons wait.
  server.cleanupRunning = true;
  await page.run('load()');
  out.running = {restore: byId()[74].restore, toolbar: archiveToolbar(page)};
  server.cleanupRunning = false;
  console.log(JSON.stringify(out));
}
// D4: "Xóa video" (cancelled and lost videos), "Dọn video mất gốc" and the golden-set lock on the classic page.
async function deleteScenario() {
  server.jobs.length = 0;
  const GOLDEN = 'Video thuộc bộ nhãn vàng dùng để chấm detector (annotations/golden); BiliFlow không xóa video này';
  const card = (id, extra) => ({...BASE, id, job_key: `ep-${id}`, source_path: `input/Tập ${id}.mp4`, source_present: true, source_cleaned: false, source_cleanup: null, source_archived: false, source_archive: null,
    protected: null, delete: null, hidden_at: null, reports_bytes: 1048576 * id, updated_at: `2026-10-03T12:${String(id % 60).padStart(2, '0')}:00`, ...extra});
  const lost = {...LOST_HINT}, cancelled = {state: 'CANCELLED', stop_mode: 'CANCELLED'};
  const exported = size => ({eligible: true, kind: 'EXPORTED', reason: null, size_bytes: size, output_name: 'x-reviewed.mp4', output_bytes: 104857600, exported_at: '2026-10-02T15:00:00+07:00', skipped_at: null});
  const ready = {status: 'READY_FOR_EDIT_PLAN', main_items: 1, advisory_items: 0, pending: 0, decisions: {KEEP: 1}, export_size_policy: null, skip_eligible: false};
  // "Đang chờ xử lý": a cancelled video still in input, a cancelled golden one, a lost one that cannot go yet, a hidden lost one.
  server.jobs.push(card(2, {...cancelled, source_size_bytes: 300000000, delete: {eligible: true, kind: 'CANCELLED', reason: null, size_bytes: 300000000}}));
  server.jobs.push(card(39, {...cancelled, protected: GOLDEN, delete: {eligible: false, kind: 'CANCELLED', reason: GOLDEN, size_bytes: 0}}));
  server.jobs.push(card(7, {state: 'FAILED', source_present: false, delete: {eligible: false, kind: 'LOST', reason: 'Còn lệnh xuất video chưa xong', size_bytes: 0}}));
  server.jobs.push(card(3, {...cancelled, hidden_at: '2026-10-03T12:30:00+07:00', source_present: false, delete: lost}));
  // Lost videos in "Đang chờ duyệt" and "Hoàn tất", a golden exported video and an ordinary one.
  server.jobs.push(card(6, {state: 'READY_TO_EXPORT', progress: 1, active_queue_path: 'q', source_present: false, delete: lost, review_summary: ready}));
  server.jobs.push(card(5, {state: 'COMPLETED', progress: 1, active_queue_path: 'q', source_present: false, delete: lost, cleanup: {...exported(250000000), eligible: false, reason: 'Video gốc không còn trong thư mục input'}}));
  server.jobs.push(card(37, {state: 'COMPLETED', progress: 1, active_queue_path: 'q', protected: GOLDEN, source_size_bytes: 230000000, cleanup: exported(230000000)}));
  server.jobs.push(card(8, {state: 'COMPLETED', progress: 1, active_queue_path: 'q', source_size_bytes: 210000000, cleanup: exported(210000000)}));
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(new Map(), log);
  await sleep(30);
  const out = {};
  const all = () => Object.fromEntries(allCards(page).map(c => [c.id, c]));
  const view = c => ({tab: c.tab, del: c.deleteButton, cleanup: c.hasCleanupButton ? {disabled: c.cleanupButtonDisabled, title: c.cleanupButtonTitle} : null, pick: c.hasCleanupPick, note: c.protectedNote});
  out.cards = Object.fromEntries(Object.entries(all()).map(([id, c]) => [id, view(c)]));
  // The notice heads every tab: #3 (hidden), #5 and #6; not #7 (refused) nor the golden #39.
  out.notice = Object.fromEntries(TAB_KEYS.map(key => { page.run(`selectJobTab('${key}')`); return [key, lostNoticeState(page)]; }));
  // "Hoàn tất": the golden #37 is neither counted nor selected for "Xóa video gốc".
  page.run("selectJobTab('completed')");
  out.toolbar = toolbar(page);
  page.run('selectAllCleanup()');
  out.selected = selection(page);
  page.run('clearCleanupSelection()');
  // "Xóa video" on the cancelled #2: unticked, confirm does nothing; ticked, two clicks make one POST.
  page.run("selectJobTab('waiting')");
  await page.run('openDelete([2],null)');
  out.cancelled_preview = {calls: server.deletePreviewCalls.slice(), shown: page.deleteDialog.shown, focused: page.document.getElementById('delete-cancel').focused || 0, ...deleteDialogState(page)};
  await page.run('confirmDelete()');
  out.cancelled_unticked_posts = server.deletePosts.length;
  page.run('setDeleteAck(true)');
  out.cancelled_ticked = {ack: page.run('deleteAck'), confirmDisabled: !!page.document.getElementById('delete-confirm').disabled};
  server.postDelay = 60;
  const first = page.run('confirmDelete()'), second = page.run('confirmDelete()');
  out.cancelled_posting = {...deleteDialogState(page), esc_prevented: page.deleteDialog.dispatch('cancel').defaultPrevented, wait_shown: page.document.getElementById('delete-wait').hidden === false};
  page.run('closeDeleteDialog()');
  out.cancelled_posting.open_after_close_click = page.deleteDialog.open;
  await Promise.all([first, second]);
  server.postDelay = 0;
  out.cancelled_done = {posts: server.deletePosts.slice(), notice: page.notice(), error: page.noticeIsError(), open: page.deleteDialog.open, ack: page.run('deleteAck'), gone: !all()[2], folds: folds(page).map(f => [f.key, f.ids])};
  // "Dọn video mất gốc" lists #3, #5 and #6 (lowest ids first).
  await page.run('openLostCleanup(null)');
  out.lost_preview = {call: server.deletePreviewCalls[server.deletePreviewCalls.length - 1], ...deleteDialogState(page)};
  // The list changed meanwhile: the new one is shown and the tick is cleared.
  page.run('setDeleteAck(true)');
  server.deleteRefuse = {status: 409, code: 'preview_changed', error: 'Danh sách đã thay đổi, hãy xem lại.', preview: {...server.deletePreview('3,5'), preview_id: 'e'.repeat(64)}};
  await page.run('confirmDelete()');
  out.lost_changed = deleteDialogState(page);
  // Busy keeps the tick of the same list.
  page.run('setDeleteAck(true)');
  server.deleteRefuse = {status: 409, code: 'busy', error: 'Đang xóa, lưu trữ hoặc khôi phục video; chờ lượt trước xong rồi thử lại.'};
  await page.run('confirmDelete()');
  out.lost_busy = {...deleteDialogState(page), sent: server.deletePosts[server.deletePosts.length - 1]};
  // #3 goes; a log of #5 is still open (PARTIAL), so #5 stays in the list and in the notice.
  server.deleteResults = [{job_id: 3, status: 'DELETED'}, {job_id: 5, status: 'PARTIAL', message: 'Còn dữ liệu của video chưa xóa được (logs/control-center/job-5-x.log: đang được mở). Video vẫn có trong danh sách; đóng file đang mở rồi bấm “Xóa video” lại.'}];
  await page.run('confirmDelete()');
  out.lost_done = {post: server.deletePosts[server.deletePosts.length - 1], posts: server.deletePosts.length, notice: page.notice(), error: page.noticeIsError(), open: page.deleteDialog.open,
    lost: lostNoticeState(page), ids: allCards(page).map(c => c.id).sort((a, b) => a - b), hidden: hiddenRows(page).map(r => r.id)};
  // A refused preview is an error notice; the dialog stays closed.
  server.deletePreviewError = 'Chọn từ 1 đến 50 video mỗi lần dọn.';
  await page.run('openDelete([5],null)');
  out.preview_error = {notice: page.notice(), error: page.noticeIsError(), open: page.deleteDialog.open};
  // While a source-file action runs (another tab), every delete button and the notice wait; the golden reason stays.
  server.cleanupRunning = true;
  await page.run('load()');
  const running = all();
  out.running = {lost: lostNoticeState(page), del5: running[5].deleteButton, del39: running[39].deleteButton, cleanup8: {disabled: running[8].cleanupButtonDisabled, title: running[8].cleanupButtonTitle}, cleanup37: running[37].cleanupButtonTitle};
  server.cleanupRunning = false;
  // More than 50 lost videos: the notice says so and the dialog lists the first 50 (lowest ids).
  for (let id = 100; id < 160; id++) server.jobs.push(card(id, {state: 'COMPLETED', progress: 1, updated_at: '2026-10-01T10:00:00', source_present: false, delete: lost}));
  await page.run('load()');
  out.many_notice = lostNoticeState(page);
  await page.run('openLostCleanup(null)');
  const sent = server.deletePreviewCalls[server.deletePreviewCalls.length - 1].split(',').map(Number);
  out.many = {count: sent.length, first: sent[0], last: sent[sent.length - 1], ...deleteDialogState(page)};
  out.many.rows = out.many.rows.length;
  delete out.many.body;
  page.run('closeDeleteDialog()');
  console.log(JSON.stringify(out));
}
(async () => {
  if (process.argv[3] === 'delete') return deleteScenario();
  if (process.argv[3] === 'archive') return archiveScenario();
  if (process.argv[3] === 'scroll') return scrollScenario();
  if (process.argv[3] === 'cancelled') return cancelledScenario();
  if (process.argv[3] === 'cleanup') return cleanupScenario();
  if (process.argv[3] === 'queue') return queueScenario();
  if (process.argv[3] === 'tabs') return tabsScenario();
  if (process.argv[3] === 'skip') return skipScenario();
  if (process.argv[3] === 'export') return exportScenario();
  const out = {};
  const storage = new Map();
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(storage, log);
  await sleep(30);
  out.initial_tab = page.run('activeJobTab');
  page.run("selectJobTab('waiting')");
  out.initial_order = cards(page).map(c => c.id);
  page.run("selectJobTab('scan_queue')");
  out.initial_scan_queue = cards(page).map(c => c.id);
  page.run("selectJobTab('waiting')");
  // The user configures #46 as advertising only, live action.
  setDetectors(page, 46, ['advertising']);
  page.jobs.controls.find(x => x.id === 'style-46').value = 'live_action';
  page.run('captureMetadataDraft(46)');
  // A periodic poll is in flight (slow) when Start(46) is clicked.
  server.delays.push(250);
  const stalePoll = page.run('load()');
  await sleep(20);
  await page.run('start(46)');
  out.start_notice = page.notice();
  out.start_notice_is_error = page.document.getElementById('notice').className.includes('error');
  out.after_start = allCards(page);
  out.tab_after_start = page.run('activeJobTab');
  out.stale_applied = await stalePoll;
  out.after_stale_poll = allCards(page);
  out.posts = server.posts.slice();
  out.confirms_for_46 = log.confirms.slice();
  // Drafts for #47 survive a periodic re-render and a reload.
  setDetectors(page, 47, ['advertising', 'adult']);
  page.run('ocrDrafts[47]=Number("8");saveDraft(47)');
  await page.run('load()');
  out.rerender_47 = allCards(page).find(c => c.id === 47);
  out.storage_keys = [...storage.keys()].sort();
  out.last_scope = JSON.parse(storage.get('biliflow.lastDetectorScope') || 'null');
  const reloaded = boot(storage, log);
  await sleep(30);
  reloaded.run("selectJobTab('waiting')");
  out.reload_47 = allCards(reloaded).find(c => c.id === 47);
  out.reload_46 = allCards(reloaded).find(c => c.id === 46);
  // A scope that differs from the previous start asks first; cancelling posts nothing.
  log.confirms.length = 0; log.confirmAnswer = false;
  const postsBefore = server.posts.length;
  setDetectors(reloaded, 48, ['advertising', 'adult', 'gore', 'violence']);
  await reloaded.run('start(48)');
  out.confirm_48 = log.confirms.slice();
  out.posts_after_cancel = server.posts.length - postsBefore;
  out.draft_48_kept_after_cancel = storage.has('biliflow.jobDraft.48');
  // Another tab started #48 meanwhile; this tab's stale card is refused by the server.
  log.confirmAnswer = true;
  server.jobs.find(x => x.id === 48).state = 'QUEUED';
  await reloaded.run('start(48)');
  out.refused_notice = reloaded.notice();
  out.refused_notice_is_error = reloaded.document.getElementById('notice').className.includes('error');
  out.draft_48_kept_after_refusal = storage.has('biliflow.jobDraft.48');
  out.title_48 = (allCards(reloaded).find(c => c.id === 48) || {}).title;
  // A refusal while #47 still waits for setup keeps its draft and shows an error.
  log.confirms.length = 0;
  server.refuseNext = true;
  const button47 = {disabled: false, textContent: 'Bắt đầu', isConnected: true};
  await reloaded.run('start')(47, button47);
  out.error_notice_47 = reloaded.notice();
  out.error_notice_47_is_error = reloaded.document.getElementById('notice').className.includes('error');
  out.draft_47_kept_after_error = storage.has('biliflow.jobDraft.47');
  out.button_47_restored = !button47.disabled && button47.textContent === 'Bắt đầu';
  // A double click on Start(47) posts once and asks at most once.
  log.confirms.length = 0;
  reloaded.document.getElementById('notice').className = 'notice';
  const postsBeforeDouble = server.posts.length;
  server.postDelay = 150;
  const first = reloaded.run('start')(47, button47);
  const second = reloaded.run('start')(47, button47);
  out.button_47_busy = button47.disabled && button47.textContent === 'Đang bắt đầu…';
  reloaded.run('renderJobs(true)');
  out.card_47_while_pending = allCards(reloaded).find(c => c.id === 47);
  await Promise.all([first, second]);
  server.postDelay = 0;
  out.double_posts = server.posts.length - postsBeforeDouble;
  out.double_confirms = log.confirms.length;
  out.double_notice_is_error = reloaded.document.getElementById('notice').className.includes('error');
  out.starting_after_double = reloaded.run('startingJobs.size');
  out.card_47_after_double = allCards(reloaded).find(c => c.id === 47);
  // A pointer held inside #jobs defers the rebuild until shortly after release.
  reloaded.run("selectJobTab('scan_queue')");
  const before = reloaded.jobs.innerHTML;
  server.jobs.find(x => x.id === 47).progress = 0.5;
  server.jobs.find(x => x.id === 47).state = 'QUEUED';
  reloaded.jobs.dispatch('pointerdown');
  await reloaded.run('load()');
  out.render_deferred_while_pressed = reloaded.jobs.innerHTML === before;
  reloaded.document.dispatch('pointerup');
  await sleep(1000);
  out.rendered_after_release = reloaded.jobs.innerHTML !== before;
  out.notices = reloaded.notice();
  console.log(JSON.stringify(out));
})().catch(error => { console.error(error && error.stack || error); process.exit(1); });
