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
const server = {jobs: [], posts: [], actions: [], delays: [], clock: 0, postDelay: 0, refuseNext: false, refuseAction: null, seq: 2, paused: false};
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
    return response(200, {jobs: snapshot, queue, scheduler_paused: server.paused, detector_options: options, resources: {}, active: null});
  }
  const action = /^\/api\/jobs\/(\d+)\/(skip|unskip|review\/finalize)$/.exec(url);
  if (action && opt.method === 'POST') return jobAction(Number(action[1]), action[2], JSON.parse(opt.body || '{}'));
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
class FakeElement {
  constructor(tagName, attrs = {}) { Object.assign(this, {tagName, textContent: '', className: '', value: '', checked: false, listeners: {}}, attrs); this._html = ''; }
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
  dispatch(name) { (this.listeners[name] || []).forEach(fn => fn({type: name})); }
  contains(other) { return other === this || (this.controls || []).includes(other); }
  set innerHTML(html) { this._html = html; if (this.id === 'jobs') { this.controls = parseControls(html); this.panels = parsePanels(html); } }
  get innerHTML() { return this._html; }
}
function attr(text, name) { const m = new RegExp(`\\b${name}="([^"]*)"`).exec(text); return m ? m[1] : null; }
function parseControls(html) {
  const els = [];
  for (const m of html.matchAll(/<input\b([^>]*)>/g)) els.push(new FakeElement('INPUT', {id: attr(m[1], 'id'), value: attr(m[1], 'value') || '', checked: /\schecked(\s|$)/.test(m[1]), detectorJob: attr(m[1], 'data-detector-job')}));
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
    dispatch(name) { (this.listeners[name] || []).forEach(fn => fn({type: name})); },
  };
  if (extra.documentElement) document.documentElement = extra.documentElement;
  statics.jobs = new FakeElement('DIV', {id: 'jobs'});
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
  return {run, document, jobs: statics.jobs, notice: () => document.getElementById('notice').textContent};
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
    return {id, badge, queue, scan: values[0], scanDetail: details[0], exportValue: values[3], exportDetail: details[3], hasStart: chunk.includes(`start(${id},`), startDisabled: chunk.includes(`start(${id},this)" disabled>Đang bắt đầu…`), title: /class="job-title">([^<]*)</.exec(chunk)[1], detectors, ocr: ocr ? Number(ocr[1]) : null,
      bucket: attr(chunk, 'data-bucket'), hasSkip: chunk.includes(`onclick="skipJob(${id},`), hasUnskip: chunk.includes(`unskipJob(${id},`), hasCancel: chunk.includes(`act(${id},'cancel')`), hasRerun: chunk.includes('class="rerun-panel"'),
      hasExport: chunk.includes('class="export-panel"'), exportDisabled: exportButton ? /disabled/.test(exportButton[1]) : null, exportTitle: exportButton ? attr(exportButton[1], 'title') : null, exportReason: (/class="export-reason">([^<]*)</.exec(chunk) || [null, null])[1], exportError: (/class="export-error"[^>]*>([^<]*)</.exec(chunk) || [null, null])[1]};
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
(async () => {
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
