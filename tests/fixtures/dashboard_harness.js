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
  previewCalls: [], cleanupPosts: [], cleanupRefuse: null, cleanupResults: null, blocked: null, cleanupRunning: false, cleanupRow: 0};
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
// The server side of "Dọn video gốc" (contract sections 11.2 and 11.3), as far as the dashboard sees it.
const fileName = job => job.source_path.split('/').pop();
server.preview = function (idsText) {
  const eligible = [], ineligible = [];
  for (const id of idsText.split(',').map(Number).sort((a, b) => a - b)) {
    const job = server.jobs.find(j => j.id === id), c = job && job.cleanup;
    if (job && c && c.eligible && !job.source_cleaned) eligible.push({job_id: id, name: fileName(job), file_name: fileName(job), source_path: job.source_path, size_bytes: c.size_bytes, kind: c.kind, output_path: c.output_name ? `output/${c.output_name}` : null, output_name: c.output_name, output_bytes: c.output_bytes, exported_at: c.exported_at, skipped_at: c.skipped_at});
    else ineligible.push({job_id: id, name: job ? fileName(job) : '', reason: job ? ((c && c.reason) || 'Chỉ dọn được video đã xuất hoặc đã bỏ qua (mục “Hoàn tất”)') : `Không tìm thấy video #${id}`});
  }
  const total = eligible.reduce((sum, x) => sum + x.size_bytes, 0), used = 11823971925;
  return {preview_id: server.previewId || 'a'.repeat(64), eligible, ineligible, count: eligible.length, total_bytes: total,
    recycle_bin: {volume: 'E:', used_bytes: used, items: 7, max_bytes: 52157218816, after_bytes: used + total}, blocked: server.blocked || null};
};
function cleanupRow(job, state, extra = {}) {
  server.cleanupRow += 1;
  return {id: server.cleanupRow, state, kind: job.cleanup.kind, size_bytes: job.cleanup.size_bytes, file_name: fileName(job), source_path: job.source_path, created_at: '2026-10-03T09:00:00+07:00', finished_at: '2026-10-03T09:00:05+07:00', restored_at: null, verified: false, error: null, ...extra};
}
async function cleanupPost(body) {
  server.cleanupPosts.push(body);
  if (server.postDelay) await sleep(server.postDelay);
  if (server.cleanupRefuse) { const r = server.cleanupRefuse; server.cleanupRefuse = null; return response(r.status, {error: r.error, code: r.code, ...(r.preview ? {preview: r.preview} : {})}); }
  const planned = server.cleanupResults || body.job_ids.map(id => ({job_id: id, status: 'RECYCLED'}));
  server.cleanupResults = null;
  const results = planned.map(r => {
    const job = server.jobs.find(j => j.id === r.job_id), size = job.cleanup.size_bytes;
    if (r.status === 'RECYCLED' || r.status === 'UNVERIFIED') {
      Object.assign(job, {source_cleanup: cleanupRow(job, 'RECYCLED', {verified: r.status === 'RECYCLED'}), source_present: false, source_cleaned: true,
        cleanup: {...job.cleanup, eligible: false, reason: 'Video gốc đã được dọn trước đó'}});
    } else if (r.status === 'FAILED') job.source_cleanup = cleanupRow(job, 'FAILED', {error: r.message});
    return {job_id: r.job_id, name: fileName(job), status: r.status, message: r.message || 'Đã chuyển video gốc vào Thùng rác', size_bytes: size};
  });
  const moved = results.filter(r => r.status === 'RECYCLED' || r.status === 'UNVERIFIED');
  return response(200, {results, recycled_count: moved.length, recycled_bytes: moved.reduce((sum, r) => sum + r.size_bytes, 0), failed_count: results.filter(r => r.status === 'FAILED').length, pending: results.filter(r => r.status === 'PENDING').length});
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
  return [...html.matchAll(/<details class="(export-panel|rerun-panel)" data-job-id="(\d+)"\s*(open)?>/g)].map(m => new FakeElement('DETAILS', {className: m[1], dataset: {jobId: m[2]}, open: !!m[3]}));
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
  statics['cleanup-confirm'] = new FakeElement('BUTTON', {id: 'cleanup-confirm', disabled: true, textContent: 'Chuyển vào Thùng rác'});
  statics['cleanup-cancel'] = new FakeElement('BUTTON', {id: 'cleanup-cancel', disabled: false, textContent: 'Hủy', focus() { this.focused = (this.focused || 0) + 1; }});
  const sandbox = {
    document, fetch: fakeFetch, localStorage: makeStorage(storageMap), console,
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval: () => {},
    confirm: message => { log.confirms.push(message); return log.confirmAnswer; },
    alert: message => log.alerts.push(message),
    listeners: {}, addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
  };
  sandbox.window = sandbox;
  const context = vm.createContext(sandbox);
  vm.runInContext(script, context);
  const run = code => vm.runInContext(code, context);
  return {run, document, jobs: statics.jobs, dialog, notice: () => document.getElementById('notice').textContent, noticeIsError: () => document.getElementById('notice').className.includes('error')};
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
    const cleanupButton = new RegExp(`<button class="warn" onclick="openCleanup\\(\\[${id}\\],this\\)"([^>]*)>`).exec(chunk);
    const sourceLine = /<div class="source-line tone-([a-z]+)">([^<]*)</.exec(chunk);
    return {id, badge, queue, scan: values[0], scanDetail: details[0], exportValue: values[3], exportDetail: details[3], hasStart: chunk.includes(`start(${id},`), startDisabled: chunk.includes(`start(${id},this)" disabled>Đang bắt đầu…`), title: /class="job-title">([^<]*)</.exec(chunk)[1], detectors, ocr: ocr ? Number(ocr[1]) : null,
      bucket: attr(chunk, 'data-bucket'), hasSkip: chunk.includes(`onclick="skipJob(${id},`), hasUnskip: chunk.includes(`unskipJob(${id},`), hasCancel: chunk.includes(`act(${id},'cancel')`), hasRerun: chunk.includes('class="rerun-panel"'),
      hasExport: chunk.includes('class="export-panel"'), exportDisabled: exportButton ? /disabled/.test(exportButton[1]) : null, exportTitle: exportButton ? attr(exportButton[1], 'title') : null, exportReason: (/class="export-reason">([^<]*)</.exec(chunk) || [null, null])[1], exportError: (/class="export-error"[^>]*>([^<]*)</.exec(chunk) || [null, null])[1],
      hasCleanupPick: !!pick, cleanupChecked: pick ? /\schecked(\s|$)/.test(pick[1]) : null, hasCleanupButton: chunk.includes(`openCleanup([${id}]`),
      cleanupButtonDisabled: cleanupButton ? /\sdisabled(\s|$)/.test(cleanupButton[1]) : null, cleanupButtonTitle: cleanupButton ? attr(cleanupButton[1], 'title') : null,
      sourceLine: sourceLine ? sourceLine[2] : null, sourceTone: sourceLine ? sourceLine[1] : null, cleanupNote: (/class="cleanup-note">([^<]*)</.exec(chunk) || [null, null])[1],
      rerunDisabled: /<button disabled title="[^"]*">Chạy lại kiểm tra<\/button>/.test(chunk), unskipDisabled: /<button class="green" disabled title="[^"]*">Mở lại để xuất<\/button>/.test(chunk)};
  });
}
// The section headings (with counts) of the visible tab, in order.
function headings(page) {
  return [...page.jobs.innerHTML.matchAll(/class="phase-heading">([^<]*) <span>(\d+)<\/span>/g)].map(m => [m[1], Number(m[2])]);
}
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
  const confirm = page.document.getElementById('cleanup-confirm'), cancel = page.document.getElementById('cleanup-cancel');
  return {open: page.dialog.open, body: page.document.getElementById('cleanup-dialog-body').innerHTML, confirm: confirm.textContent, confirmDisabled: !!confirm.disabled, cancelDisabled: !!cancel.disabled};
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
  const done = {...BASE, state: 'COMPLETED', progress: 1, active_queue_path: 'q', source_present: true, source_cleaned: false, source_cleanup: null};
  const exported = (size, output, extra = {}) => ({eligible: true, kind: 'EXPORTED', reason: null, size_bytes: size, output_name: output, output_bytes: 104857600, exported_at: '2026-10-02T15:00:00+07:00', skipped_at: null, ...extra});
  const skippedHint = size => ({eligible: true, kind: 'SKIPPED', reason: null, size_bytes: size, output_name: null, output_bytes: null, exported_at: null, skipped_at: '2026-10-02T14:00:00+07:00'});
  const row = (id, state, job, size, extra = {}) => ({id, state, kind: 'EXPORTED', size_bytes: size, file_name: job.split('/').pop(), source_path: job, created_at: '2026-10-03T08:00:00+07:00', finished_at: '2026-10-03T08:00:04+07:00', restored_at: null, verified: true, error: null, ...extra});
  server.jobs.push({...done, id: 42, job_key: 'ep-42', source_path: 'input/Tập 12.mp4', source_size_bytes: 252168775, updated_at: '2026-10-02T15:00:00', cleanup: exported(252168775, 'ep-42-reviewed.mp4')});
  server.jobs.push({...done, id: 60, job_key: 'ep-60', source_path: 'input/Tập 30.mp4', state: 'SKIPPED', source_size_bytes: 248000000, updated_at: '2026-10-02T14:00:00', skip: {skipped_at: '2026-10-02T14:00:00+07:00'}, cleanup: skippedHint(248000000)});
  server.jobs.push({...done, id: 45, job_key: 'ep-45', source_path: 'input/Tập 15.mp4', source_size_bytes: 250000000, updated_at: '2026-10-02T13:00:00', source_cleanup: row(1, 'RESTORED', 'input/Tập 15.mp4', 250000000, {restored_at: '2026-10-02T09:00:00+07:00'}), cleanup: exported(250000000, 'ep-45-reviewed.mp4')});
  server.jobs.push({...done, id: 41, job_key: 'ep-41', source_path: 'input/Tập 11.mp4', source_size_bytes: 256115645, updated_at: '2026-10-02T12:00:00', source_present: false, source_cleaned: true, source_cleanup: row(2, 'RECYCLED', 'input/Tập 11.mp4', 256115645), cleanup: exported(256115645, 'ep-41-reviewed.mp4', {eligible: false, reason: 'Video gốc đã được dọn trước đó'})});
  server.jobs.push({...done, id: 70, job_key: 'ep-70', source_path: 'input/Tập 40.mp4', state: 'SKIPPED', source_size_bytes: 240000000, updated_at: '2026-10-02T11:30:00', skip: {skipped_at: '2026-10-02T11:00:00+07:00'}, source_present: false, source_cleaned: true, source_cleanup: row(3, 'RECYCLED', 'input/Tập 40.mp4', 240000000, {kind: 'SKIPPED', verified: false}), cleanup: {...skippedHint(240000000), eligible: false, reason: 'Video gốc đã được dọn trước đó'}});
  server.jobs.push({...done, id: 37, job_key: 'ep-37', source_path: 'input/Tập 7.mp4', source_size_bytes: 230000000, updated_at: '2026-10-02T11:00:00', cleanup: exported(230000000, 'ep-37-reviewed.mp4', {eligible: false, reason: 'Không thấy bản xuất trong thư mục output (đã bị dời hoặc đổi tên?)'})});
  server.jobs.push({...done, id: 3, job_key: 'ep-3', source_path: 'input/Tập 3.mp4', source_size_bytes: 220000000, updated_at: '2026-10-02T10:00:00', source_present: false, cleanup: exported(220000000, 'ep-3-reviewed.mp4', {eligible: false, reason: 'Video gốc không còn trong thư mục input'})});
  server.cleanupRow = 3;
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
  // Hủy posts nothing; confirm without a preview posts nothing either.
  page.run('closeCleanupDialog()');
  await page.run('confirmCleanup()');
  out.cancel = {posts: server.cleanupPosts.length, open: page.dialog.open, preview_cleared: page.run('cleanupPreview===null')};
  // Reopen and confirm twice: one POST, the dialog cannot be closed meanwhile.
  await page.run('openCleanup([42,60])');
  server.postDelay = 80;
  const first = page.run('confirmCleanup()'), second = page.run('confirmCleanup()');
  out.posting = {...dialogState(page), esc_prevented: page.dialog.dispatch('cancel').defaultPrevented, wait_shown: page.document.getElementById('cleanup-wait').hidden === false};
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
  const after = byId();
  out.ok = {posts: server.cleanupPosts.slice(), notice: page.notice(), error: page.noticeIsError(), open: page.dialog.open, selection: selection(page),
    lines: [after[42].sourceLine, after[60].sourceLine], picks: [after[42].hasCleanupPick, after[60].hasCleanupPick], esc_prevented_when_idle: page.dialog.dispatch('cancel').defaultPrevented};
  // A blocked preview (Recycle Bin capacity) cannot be confirmed.
  const capacity = 'Không thể dọn: Thùng rác của ổ E: đang chứa 11,0 GB, giới hạn 48,6 GB; chuyển thêm 40,0 GB sẽ vượt giới hạn và Windows có thể xóa vĩnh viễn các mục cũ nhất. Hãy dọn sạch Thùng rác hoặc chọn ít video hơn.';
  server.blocked = capacity;
  const postsBeforeBlocked = server.cleanupPosts.length;
  await page.run('openCleanup([45])');
  await page.run('confirmCleanup()');
  out.blocked = {...dialogState(page), posts: server.cleanupPosts.length - postsBeforeBlocked};
  server.blocked = null;
  page.run('closeCleanupDialog()');
  // 409 preview_changed shows the new list in the open dialog; the next confirm uses its id.
  await page.run('openCleanup([45])');
  server.cleanupRefuse = {status: 409, error: 'Danh sách đã thay đổi, hãy xem lại.', code: 'preview_changed', preview: {...server.preview('45'), preview_id: 'b'.repeat(64)}};
  await page.run('confirmCleanup()');
  out.changed = {...dialogState(page), preview_id: page.run('cleanupPreview&&cleanupPreview.preview_id')};
  server.cleanupRefuse = {status: 409, error: 'Đang dọn video gốc; chờ lần dọn trước xong rồi thử lại.', code: 'busy'};
  await page.run('confirmCleanup()');
  out.busy = {...dialogState(page), body_sent: server.cleanupPosts[server.cleanupPosts.length - 1]};
  server.cleanupRefuse = {status: 500, error: 'boom'};
  await page.run('confirmCleanup()');
  out.other_error = dialogState(page);
  page.run('closeCleanupDialog()');
  // A partial failure: #45 moved, #60 is still open in another tab.
  Object.assign(job(60), {source_present: true, source_cleaned: false, source_cleanup: row(9, 'RESTORED', 'input/Tập 30.mp4', 248000000, {kind: 'SKIPPED', restored_at: '2026-10-03T10:00:00+07:00'}), cleanup: skippedHint(248000000)});
  await page.run('load()');
  page.run('toggleCleanup(45,true);toggleCleanup(60,true)');
  await page.run('openCleanup([45,60])');
  server.cleanupResults = [{job_id: 45, status: 'RECYCLED'}, {job_id: 60, status: 'FAILED', message: 'File đang được mở (ví dụ đang phát trong trang duyệt). Đóng trang duyệt của video này rồi thử lại.'}];
  await page.run('confirmCleanup()');
  const partial = byId();
  out.partial = {notice: page.notice(), error: page.noticeIsError(), open: page.dialog.open, selection: selection(page), line60: partial[60].sourceLine, pick60: partial[60].hasCleanupPick, line45: partial[45].sourceLine};
  // A selection without any cleanable video.
  await page.run('openCleanup([3])');
  out.nothing = dialogState(page);
  page.run('closeCleanupDialog()');
  // While a cleanup runs (another tab), the card button and the toolbar are disabled.
  server.cleanupRunning = true;
  await page.run('load()');
  const running = byId();
  page.run('updateCleanupToolbar()');
  out.running = {card: running[60].cleanupButtonDisabled, title: running[60].cleanupButtonTitle, toolbar: toolbar(page), static_run_disabled: !!page.document.getElementById('cleanup-run').disabled};
  server.cleanupRunning = false;
  // "Chọn tất cả" stops at 50 videos.
  for (let id = 200; id < 255; id++) server.jobs.push({...done, id, job_key: `ep-${id}`, source_path: `input/${id}.mp4`, source_size_bytes: 1048576, updated_at: '2026-10-01T10:00:00', cleanup: exported(1048576, `ep-${id}-reviewed.mp4`)});
  await page.run('load()');
  page.run('selectAllCleanup()');
  const eligible = cards(page).filter(c => c.hasCleanupPick).map(c => c.id).sort((a, b) => a - b);
  out.limit = {count: selection(page).length, eligible: eligible.length, first50: JSON.stringify(selection(page)) === JSON.stringify(eligible.slice(0, 50)), notice: page.notice(), toolbar: toolbar(page)};
  console.log(JSON.stringify(out));
}
(async () => {
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
