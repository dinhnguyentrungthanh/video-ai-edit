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
const server = {jobs: [], posts: [], delays: [], clock: 0, postDelay: 0, refuseNext: false};
for (const id of [44, 45]) server.jobs.push({id, job_key: `ep-${id}`, source_path: `input/Tập ${id}.mp4`, state: 'QUEUED', updated_at: `2026-10-02T13:0${id - 40}:00`, priority: 100, detector_groups: ['advertising'], content_style: 'live_action', profile: 'careful', progress: 0, ocr_recognition_batch_size: 1, fast_scan: true, active_queue_path: null});
// #48 uses a Windows path: the card and the confirm must show only the file name.
for (const id of [46, 47, 48]) server.jobs.push({id, job_key: `ep-${id}`, source_path: id === 48 ? ['E:', 'DungChung', 'BiliFlow', 'input', `Tập ${id}.mp4`].join(String.fromCharCode(92)) : `input/Tập ${id}.mp4`, state: 'NEEDS_METADATA', updated_at: `2026-10-02T12:${id}:00`, priority: 100, detector_groups: null, content_style: 'unknown', profile: 'careful', progress: 0, ocr_recognition_batch_size: 1, fast_scan: true, active_queue_path: null});
const response = (status, value) => ({ok: status < 400, status, statusText: String(status), json: async () => JSON.parse(JSON.stringify(value))});
async function fakeFetch(url, opt = {}) {
  if (url === '/api/session') return response(200, {token: 't'});
  if (url === '/api/ai') return response(200, {ready: false, config: {}, message: 'mock'});
  if (url === '/api/status') {
    const snapshot = JSON.parse(JSON.stringify(server.jobs));
    const delay = server.delays.shift() || 0;
    if (delay) await sleep(delay);
    snapshot.sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
    return response(200, {jobs: snapshot, detector_options: options, resources: {}, active: null});
  }
  const match = /^\/api\/jobs\/(\d+)\/start$/.exec(url);
  if (match && opt.method === 'POST') {
    const id = Number(match[1]), body = JSON.parse(opt.body), job = server.jobs.find(x => x.id === id);
    server.posts.push({id, detectors: body.detectors});
    if (server.postDelay) await sleep(server.postDelay);
    if (server.refuseNext) { server.refuseNext = false; return response(400, {error: 'Lỗi thử nghiệm'}); }
    if (!['NEEDS_METADATA', 'DISCOVERED'].includes(job.state)) return response(400, {error: `Video #${id} đang ở trạng thái ${job.state}`});
    server.clock += 1;
    Object.assign(job, {state: 'QUEUED', detector_groups: body.detectors, content_style: body.content_style, updated_at: `2026-10-02T14:${String(server.clock).padStart(2, '0')}:00`});
    return response(200, job);
  }
  return response(200, {});
}
class FakeElement {
  constructor(tagName, attrs = {}) { Object.assign(this, {tagName, textContent: '', className: '', value: '', checked: false, listeners: {}}, attrs); this._html = ''; }
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); }
  dispatch(name) { (this.listeners[name] || []).forEach(fn => fn({type: name})); }
  contains(other) { return other === this || (this.controls || []).includes(other); }
  set innerHTML(html) { this._html = html; if (this.id === 'jobs') this.controls = parseControls(html); }
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
function makeStorage(map) {
  return {getItem: k => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k), key: i => [...map.keys()][i] ?? null, get length() { return map.size; }};
}
function boot(storageMap, log) {
  const statics = {};
  const controlId = /^(det-all|style|profile|ocr|fast)-\d+$/;
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
      return [];
    },
    querySelector() { return null; },
    addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
    dispatch(name) { (this.listeners[name] || []).forEach(fn => fn({type: name})); },
  };
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
    return {id, badge, hasStart: chunk.includes(`start(${id},`), startDisabled: chunk.includes(`start(${id},this)" disabled>Đang bắt đầu…`), title: /class="job-title">([^<]*)</.exec(chunk)[1], detectors, ocr: ocr ? Number(ocr[1]) : null};
  });
}
function setDetectors(page, id, values) {
  page.jobs.controls.filter(x => x.detectorJob === String(id)).forEach(x => { x.checked = values.includes(x.value); });
  page.run(`captureDetectorDraft(${id})`);
}
(async () => {
  const out = {};
  const storage = new Map();
  const log = {confirms: [], alerts: [], confirmAnswer: true};
  const page = boot(storage, log);
  await sleep(30);
  page.run("selectJobTab('waiting')");
  out.initial_order = cards(page).map(c => c.id);
  // The user configures #46 as advertising only, live action.
  setDetectors(page, 46, ['advertising']);
  page.jobs.controls.find(x => x.id === 'style-46').value = 'live_action';
  page.run('captureMetadataDraft(46)');
  // A periodic poll is in flight (slow) when Start(46) is clicked.
  server.delays.push(250);
  const stalePoll = page.run('load()');
  await sleep(20);
  await page.run('start(46)');
  out.after_start = cards(page);
  out.stale_applied = await stalePoll;
  out.after_stale_poll = cards(page);
  out.posts = server.posts.slice();
  out.confirms_for_46 = log.confirms.slice();
  // Drafts for #47 survive a periodic re-render and a reload.
  setDetectors(page, 47, ['advertising', 'adult']);
  page.run('ocrDrafts[47]=Number("8");saveDraft(47)');
  await page.run('load()');
  out.rerender_47 = cards(page).find(c => c.id === 47);
  out.storage_keys = [...storage.keys()].sort();
  out.last_scope = JSON.parse(storage.get('biliflow.lastDetectorScope') || 'null');
  const reloaded = boot(storage, log);
  await sleep(30);
  reloaded.run("selectJobTab('waiting')");
  out.reload_47 = cards(reloaded).find(c => c.id === 47);
  out.reload_46 = cards(reloaded).find(c => c.id === 46);
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
  out.title_48 = (cards(reloaded).find(c => c.id === 48) || {}).title;
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
  out.card_47_while_pending = cards(reloaded).find(c => c.id === 47);
  await Promise.all([first, second]);
  server.postDelay = 0;
  out.double_posts = server.posts.length - postsBeforeDouble;
  out.double_confirms = log.confirms.length;
  out.double_notice_is_error = reloaded.document.getElementById('notice').className.includes('error');
  out.starting_after_double = reloaded.run('startingJobs.size');
  out.card_47_after_double = cards(reloaded).find(c => c.id === 47);
  // A pointer held inside #jobs defers the rebuild until shortly after release.
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
