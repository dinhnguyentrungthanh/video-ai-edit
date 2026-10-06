'use strict';
/* "Tải video" gate: download-core.js, download-view.js and the adapter's download calls against a
 * fake transport (no network, no server, no DOM). Run: node dashboard_v2/verify-download.cjs
 * tests/test_dashboard_v2_downloads.py runs it and checks the state sets against the backend. */
const assert = require('node:assert/strict');
const C = require('./contracts.js');
const A = require('./adapter.js');
const K = require('./download-core.js');
const V = require('./download-view.js');

const TOKEN = 'tok-dl';
const task = (id, state, extra = {}) => ({id, state, url: 'https://clips.example/v/' + id, title: 'Video ' + id,
  attempt: 1, downloaded_bytes: 0, total_bytes: null, speed: null, eta: null, error_message: null, error_code: null,
  output_name: null, entries: null, name_locked: false, media: {}, ...extra});
const snapshot = (tasks, extra = {}) => ({tasks, counts: {}, settings: {slots: 2, max_slots: 3},
  space: {free_bytes: 500e9, reserve_bytes: 100e9}, temp: {tasks: 0, bytes: 0}, running: [], worker_error: null, worker_error_at: null, ...extra});
const ctx = (extra = {}) => ({icon: () => '', offline: false, remote: false, error: '', storageError: '', ...extra});

function fake(script = {}) {
  const calls = [];
  const transport = async (method, path, options) => {
    calls.push({method, path, headers: {...options.headers}, body: options.body === undefined ? undefined : JSON.parse(options.body)});
    const key = method + ' ' + path;
    if (key === 'GET /api/session') return {status: 200, body: {token: TOKEN}};
    let answer = script[key];
    if (Array.isArray(answer)) answer = answer.length > 1 ? answer.shift() : answer[0];
    if (typeof answer === 'function') answer = await answer(options);
    return answer || {status: 200, body: {}};
  };
  return {calls, transport, posts: () => calls.filter(c => c.method === 'POST')};
}

let passed = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('every state has a label, a tone and exactly one filter group', () => {
  assert.equal(new Set(K.STATES).size, 14);
  const groups = K.FILTERS.map(f => f[0]).filter(f => f !== 'all');
  for (const s of K.STATES) {
    assert.ok(K.LABELS[s], 'label ' + s);
    assert.ok(s in K.TONES, 'tone ' + s);
    assert.ok(groups.includes(K.group({state: s})), 'group ' + s);
    assert.equal(groups.filter(g => K.matches({state: s}, g)).length, 1, s);
  }
  const counted = K.counts(K.STATES.map((s, i) => ({id: i, state: s})));
  assert.equal(counted.all, 14);
  assert.equal(groups.reduce((sum, g) => sum + counted[g], 0), 14);
});

test('buttons follow the backend sets; offline locks every write', () => {
  for (const s of K.STATES) {
    const ids = K.actions(task(1, s), {}).map(a => a.id);
    assert.equal(ids.includes('stop'), K.STOPPABLE.includes(s), 'stop ' + s);
    assert.equal(ids.includes('resume'), K.RESUMABLE.includes(s), 'resume ' + s);
    assert.equal(ids.includes('retry'), K.RETRYABLE.includes(s), 'retry ' + s);
    assert.equal(ids.includes('cancel'), K.CANCELLABLE.includes(s) && s !== 'CANCELLING', 'cancel ' + s);
    assert.equal(ids.includes('remove'), K.FINAL.includes(s), 'remove ' + s);
    for (const a of K.actions(task(1, s), {offline: true})) {
      assert.equal(a.enabled, false);
      assert.match(a.reason, /Mất kết nối/);
    }
  }
  assert.equal(K.canRename(task(1, 'QUEUED')), true);
  assert.equal(K.canRename(task(1, 'QUEUED', {name_locked: true})), false);
  assert.equal(K.canRename(task(1, 'PUBLISHING')), false);
  assert.equal(K.canRename(task(1, 'COMPLETED')), false);
});

test('cancel always asks; retry and remove ask when they drop the downloaded part', () => {
  const confirmOf = (s, id) => (K.actions(task(1, s), {}).find(a => a.id === id) || {}).confirm;
  for (const s of K.STATES.filter(x => K.CANCELLABLE.includes(x) && x !== 'CANCELLING')) assert.ok(confirmOf(s, 'cancel'), s);
  for (const s of ['FAILED', 'STOPPED', 'INTERRUPTED']) {
    assert.match(confirmOf(s, 'retry'), /tải lại từ đầu/);
    assert.match(confirmOf(s, 'remove'), /file tạm/);
  }
  assert.equal(confirmOf('CANCELLED', 'retry'), null);
  assert.equal(confirmOf('EXPIRED', 'retry'), null);
  assert.equal(confirmOf('COMPLETED', 'remove'), null);
});

test('progress never invents a percent; 100 % only once the file is in input', () => {
  let p = K.progress(task(1, 'DOWNLOADING', {downloaded_bytes: 5e6}));
  assert.equal(p.percent, null); assert.equal(p.indeterminate, true); assert.match(p.sizeText, /đã tải/);
  p = K.progress(task(1, 'DOWNLOADING', {downloaded_bytes: 999.9, total_bytes: 1000, speed: 2048, eta: 75}));
  assert.equal(p.percent, 99); assert.equal(p.speed, '2 KB/s'); assert.equal(p.eta, 'còn 1:15');
  assert.equal(K.progress(task(1, 'DOWNLOADING', {downloaded_bytes: 5000, total_bytes: 1000})).percent, 99);
  assert.equal(K.progress(task(1, 'VERIFYING', {downloaded_bytes: 1000, total_bytes: 1000})).percent, null);
  assert.equal(K.progress(task(1, 'VERIFYING')).indeterminate, true);
  assert.equal(K.progress(task(1, 'COMPLETED')).percent, 100);
  p = K.progress(task(1, 'QUEUED'));
  assert.equal(p.percent, null); assert.equal(p.indeterminate, false); assert.equal(p.speed, ''); assert.equal(p.eta, '');
  assert.equal(K.progress(task(1, 'STOPPED', {downloaded_bytes: 250, total_bytes: 1000, speed: 99, eta: 9})).speed, '');
});

test('an HLS link counts segments; bytes stay bytes; joining has no percent; 100 % only once in input', () => {
  const hls = extra => task(1, 'DOWNLOADING', {progress_basis: 'fragments', fragments_total: 40, transfer_stage: 'downloading', ...extra});
  let p = K.progress(hls({fragments_done: 10, downloaded_bytes: 50e6, speed: 1048576, eta: 30}));
  assert.equal(p.percent, 25); assert.equal(p.sizeText, '10/40 đoạn · 48 MB'); assert.equal(p.speed, '1 MB/s'); assert.equal(p.stage, '');
  assert.equal(K.progress(hls({fragments_done: 40, downloaded_bytes: 200e6})).percent, 99, 'every segment is not the file in input');
  assert.equal(K.progress(hls({fragments_done: 99})).sizeText, '40/40 đoạn', 'never more than the total');
  p = K.progress(hls({fragments_done: 40, transfer_stage: 'remuxing', speed: 5, eta: 5}));
  assert.equal(p.percent, null); assert.equal(p.indeterminate, true); assert.equal(p.stage, 'Đang ghép các đoạn thành MP4');
  assert.equal(p.speed, ''); assert.equal(p.eta, '');
  assert.equal(K.progress({...hls({fragments_done: 12}), state: 'STOPPED'}).percent, 30);
  assert.equal(K.progress({...hls({fragments_done: 40}), state: 'COMPLETED'}).percent, 100);
  assert.equal(K.progress(hls({fragments_total: 0, downloaded_bytes: 5e6})).sizeText, '5 MB đã tải', 'no total: bytes only');
  const joining = V.row(hls({fragments_done: 40, transfer_stage: 'remuxing'}), V.createUi(), ctx());
  assert.match(joining, /Đang ghép các đoạn thành MP4/);
  assert.ok(!joining.includes('aria-valuenow'));
  const labelled = V.row(hls({fragments_done: 3, media: {source_label: 'Link HLS trực tiếp <b>'}}), V.createUi(), ctx());
  assert.match(labelled, /Nguồn: Link HLS trực tiếp &lt;b&gt;/);
  assert.ok(!V.row(task(2, 'DOWNLOADING'), V.createUi(), ctx()).includes('Nguồn:'), 'a yt-dlp link has no source line');
});

test('a batch needs links, at most 20, and the rights box; blank lines are dropped', () => {
  assert.throws(() => K.checkBatch(' \n\n', true), /ít nhất một link/);
  assert.throws(() => K.checkBatch(Array.from({length: 21}, (_, i) => 'https://clips.example/' + i).join('\n'), true), /tối đa 20/);
  assert.throws(() => K.checkBatch('https://clips.example/1', false), /quyền/);
  assert.deepEqual(K.checkBatch(' https://clips.example/1 \r\n\r\nhttps://clips.example/2\n', true),
    ['https://clips.example/1', 'https://clips.example/2']);
  assert.deepEqual(K.batchErrors({errors: [{line: 2, code: 'USERINFO', message: 'Link không được chứa tên đăng nhập'}, {code: 'X'}]}),
    ['Dòng 2: Link không được chứa tên đăng nhập', 'X']);
  assert.deepEqual(K.batchErrors(new Error('x')), []);
});

test('a page yt-dlp cannot read says "Chưa hỗ trợ" with the reason; other failures stay "Lỗi"', () => {
  const reason = 'Trang này chưa được hỗ trợ: yt-dlp không tìm thấy video nào đọc được trong trang.';
  for (const code of K.UNSUPPORTED) {
    const html = V.row(task(8, 'FAILED', {error_code: code, error_message: reason}), V.createUi(), ctx());
    assert.match(html, /<span class="badge red">Chưa hỗ trợ<\/span>/, code);
    assert.ok(html.includes(reason), code);
  }
  assert.deepEqual(K.UNSUPPORTED, ['UNSUPPORTED', 'NO_ENTRIES']);
  assert.equal(K.label(task(8, 'FAILED', {error_code: 'DRM'})), 'Lỗi');
  assert.equal(K.label(task(8, 'STOPPED', {error_code: 'UNSUPPORTED'})), 'Đã dừng');
  assert.match(V.row(task(9, 'QUEUED', {url: 'https://phim.example/phim/1'}), V.createUi(), ctx()),
    /<p class="download-source">https:\/\/phim\.example\/phim\/1<\/p>/);
});

test('sizes and clocks', () => {
  assert.equal(K.formatBytes(null), ''); assert.equal(K.formatBytes(-1), '');
  assert.equal(K.formatBytes(512 * 1024), '512 KB');
  assert.equal(K.formatBytes(3 * 1048576), '3 MB');
  assert.equal(K.formatBytes(1.5 * 1073741824), '1,5 GB');
  assert.equal(K.clock(59), '0:59'); assert.equal(K.clock(3725), '1:02:05'); assert.equal(K.clock(null), '');
});

test('text from the sites is written as text everywhere (title, link, entries, errors, log, notices)', () => {
  const evil = '<x-evil onclick="1">\'"&';
  const ui = V.createUi();
  const rows = [
    task(1, 'FAILED', {title: evil, url: 'https://clips.example/"><x-evil>', error_message: evil, error_code: '<x-evil>'}),
    task(2, 'NEEDS_CHOICE', {title: evil, entries: [{index: 1, title: evil, duration_seconds: 60}, {index: 2, title: 'b'}]}),
    task(3, 'COMPLETED', {title: evil, output_name: evil + '.mp4', media: {height: 1080, video_codec: '<x-evil>', audio_codec: 'aac'}}),
  ];
  ui.open.add(1);
  ui.details.set(1, {events: [{kind: 'NOTE', message: evil}], log: ['[download] ' + evil]});
  ui.renames.set(2, evil);
  const html = V.page(snapshot(rows, {worker_error: evil, worker_error_at: '2026-10-05T10:00:00+00:00'}),
    {computing: false, summary: null, error: evil}, ui, ctx({error: evil}));
  assert.ok(!html.includes('<x-evil'), 'no raw tag');
  assert.ok(html.includes('&lt;x-evil onclick=&quot;1&quot;&gt;&#39;&quot;&amp;'));
  assert.ok(!/onclick="1"/.test(html));
});

test('a finished task shows only the file name in input; a waiting one can be renamed', () => {
  const done = V.row(task(4, 'COMPLETED', {output_name: 'Phim.mp4', media: {height: 720, video_codec: 'h264', audio_codec: 'aac'}}), V.createUi(), ctx());
  assert.match(done, /Đã vào input: <strong>Phim\.mp4<\/strong>/);
  assert.match(done, /720p · h264 \+ aac/);
  assert.ok(!done.includes('data-rename'));
  assert.ok(!/E:\\|input\\/.test(done), 'no local path');
  const queued = V.row(task(5, 'QUEUED'), V.createUi(), ctx());
  assert.match(queued, /data-rename="5" value="Video 5"/);
  assert.match(V.row(task(5, 'QUEUED'), V.createUi(), ctx({offline: true})), /data-rename="5"[^>]* disabled/);
});

test('the list has three parts; the tools bar (its select) is its own part', () => {
  const html = V.list(snapshot([task(1, 'QUEUED')]), V.createUi(), ctx());
  const at = id => html.indexOf('id="' + id + '"');
  assert.ok(at('dl-counts') >= 0 && at('dl-counts') < at('dl-tools') && at('dl-tools') < at('dl-items'));
  assert.ok(V.tools(snapshot([]), V.createUi(), ctx()).includes('id="dl-slots"'));
  assert.ok(!V.items(snapshot([task(1, 'QUEUED')]), V.createUi(), ctx()).includes('dl-slots'));
  assert.equal(V.tools(snapshot([]), V.createUi(), ctx()), V.tools(snapshot([]), V.createUi(), ctx()), 'same data, same HTML (no redraw)');
});

test('the meter has aria-valuenow only with a known percent', () => {
  const unknown = V.row(task(6, 'DOWNLOADING', {downloaded_bytes: 10}), V.createUi(), ctx());
  assert.match(unknown, /class="meter download-meter indeterminate"/);
  assert.ok(!unknown.includes('aria-valuenow'));
  const known = V.row(task(6, 'DOWNLOADING', {downloaded_bytes: 500, total_bytes: 1000}), V.createUi(), ctx());
  assert.match(known, /aria-valuenow="50"/);
  assert.match(known, /<strong>50%<\/strong>/);
  assert.ok(!V.row(task(6, 'QUEUED'), V.createUi(), ctx()).includes('role="progressbar"'), 'no empty bar while waiting');
  const waiting = V.row(task(6, 'WAITING_SPACE', {error_message: 'Chờ chỗ trống: cần 7 GB.'}), V.createUi(), ctx());
  assert.equal(waiting.split('Chờ chỗ trống').length - 1, 2, 'the badge and the message, not a third time');
  assert.ok(!V.row(task(6, 'CANCELLING'), V.createUi(), ctx()).includes('data-rename'), 'no rename while cancelling');
  const done = V.row(task(6, 'COMPLETED', {output_name: 'a.mp4'}), V.createUi(), ctx());
  assert.equal(done.split('Đã vào input').length - 1, 2, 'the badge and the file line only');
});

test('NEEDS_CHOICE: "Tải video đã chọn" waits for a pick', () => {
  const ui = V.createUi(), t = task(7, 'NEEDS_CHOICE', {entries: [{index: 1, title: 'a'}, {index: 2, title: 'b'}]});
  assert.match(V.row(t, ui, ctx()), /data-action="dl-choose" data-id="7" disabled/);
  ui.choices.set(7, 2);
  const picked = V.row(t, ui, ctx());
  assert.match(picked, /value="2" checked/);
  assert.ok(!/data-action="dl-choose" data-id="7" disabled/.test(picked));
});

test('"Dung lượng": computing, one error per part, phone wording, nothing to clean on the page', () => {
  assert.match(V.storage({computing: true, summary: null}, ctx()), /Đang tính…/);
  const summary = {computed_at: '2026-10-05T10:00:00+00:00', drive: {free_bytes: 5e11, total_bytes: 1e12, reserve_bytes: 1e11},
    folders: {input: 1e9, output: null, reports: 0, cache: 2e9, temp: 3e6}, cleanable: {jobs: 2, bytes: 4e9},
    recycle_bin: {error: 'Không đọc được Thùng rác: x'}};
  const pc = V.storage({computing: false, summary}, ctx());
  assert.match(pc, /2 video đã xuất xong/); assert.match(pc, /Xóa video gốc \/ Lưu trữ/);
  assert.match(pc, /Không đọc được Thùng rác: x/); assert.match(pc, /<span>—<\/span>/);
  assert.ok(!/data-action="(dl-cleanup|cleanup|archive)"/.test(pc), 'the panel only links to the existing actions');
  assert.match(V.storage({computing: false, summary}, ctx({remote: true})), /Xóa video gốc \(Lưu trữ chỉ làm trên PC\)/);
  assert.match(V.storage(null, ctx({storageError: 'HTTP 503'})), /HTTP 503/);
});

test('notices: the worker error with its time, a failed list', () => {
  const html = V.notices(snapshot([], {worker_error: 'OSError: <BiliFlow>\\state', worker_error_at: '2026-10-05T10:00:00+00:00'}), ctx({error: 'HTTP 500'}));
  assert.match(html, /Hàng tải video báo lỗi lúc .+: OSError: &lt;BiliFlow&gt;\\state/);
  assert.match(html, /Không tải được danh sách tải video: HTTP 500/);
  assert.equal(V.notices(snapshot([]), ctx()), '');
});

test('adapter: an older /api/downloads answer that arrives late is dropped', async () => {
  let release;
  const slow = new Promise(resolve => { release = resolve; });
  const f = fake({'GET /api/downloads': [async () => { await slow; return {status: 200, body: {tasks: ['old']}}; }, {status: 200, body: {tasks: ['new']}}]});
  const a = A.create({contracts: C, transport: f.transport});
  const first = a.loadDownloads(), second = await a.loadDownloads();
  release();
  assert.deepEqual(second.tasks, ['new']);
  assert.equal(await first, null);
  await a.loadStorage(true);
  assert.equal(f.calls.at(-1).path, '/api/storage-summary?refresh=1');
  await a.loadDownload(12);
  assert.equal(f.calls.at(-1).path, '/api/downloads/12');
});

test('adapter: download writes are BFContracts POSTs with the token; a refused batch keeps one error per line', async () => {
  const errors = [{line: 1, code: 'BAD_PORT', message: 'Link không được dùng cổng riêng.'}];
  const f = fake({'POST /api/downloads': {status: 400, body: {error: 'Lô bị từ chối', code: 'BATCH_REJECTED', errors}}});
  const a = A.create({contracts: C, transport: f.transport});
  await assert.rejects(() => a.dispatch('downloadAdd', null, {urls: ['https://x.example/1'], rights_confirmed: true}),
    error => error.status === 400 && error.errors.length === 1 && error.errors[0].line === 1);
  await a.dispatch('downloadStop', {id: 5}, {});
  await a.dispatch('downloadSettings', null, {slots: 3});
  const posts = f.posts();
  assert.deepEqual(posts.map(p => p.path), ['/api/downloads', '/api/downloads/5/stop', '/api/downloads/settings']);
  assert.ok(posts.every(p => p.headers['X-BiliFlow-Token'] === TOKEN));
  assert.deepEqual(posts[0].body, {urls: ['https://x.example/1'], rights_confirmed: true});
  await assert.rejects(() => a.dispatch('downloadStop', null, {}), /id/);
  for (const op of ['downloadRename', 'downloadChoose', 'downloadResume', 'downloadCancel', 'downloadRetry', 'downloadRemove']) {
    assert.equal(C.request(op, {id: 9}).method, 'POST');
    assert.match(C.request(op, {id: 9}).path, /^\/api\/downloads\/9\/(rename|choose|resume|cancel|retry|remove)$/);
  }
});

test('live store: a download write refreshes only the download list, also after a refusal', async () => {
  const f = fake({'POST /api/downloads/3/cancel': {status: 409, body: {error: 'Lượt đã xong.'}},
    'GET /api/downloads': {status: 200, body: snapshot([task(3, 'COMPLETED')])}});
  const store = A.createLiveStore(A.create({contracts: C, transport: f.transport}));
  await assert.rejects(() => store.downloadAction('downloadCancel', 3, {}), /Lượt đã xong/);
  assert.equal(store.snapshot().downloads.tasks[0].state, 'COMPLETED');
  assert.equal(f.calls.filter(c => c.path === '/api/status').length, 0);
  const g = fake({'GET /api/downloads': {status: 503, body: {error: 'Tính năng tải video chưa sẵn sàng: x'}}});
  const broken = A.createLiveStore(A.create({contracts: C, transport: g.transport}));
  await broken.loadDownloads();
  assert.match(broken.snapshot().downloads_error, /chưa sẵn sàng/);
  store.stop(); broken.stop();
});

test('live store: a slow "Dung lượng" answer never brings back an older snapshot (phone mode stays on)', async () => {
  let release;
  const slow = new Promise(resolve => { release = resolve; });
  const f = fake({'GET /api/storage-summary': async () => { await slow; return {status: 200, body: {computing: false, summary: null}}; },
    'GET /api/phone-mode': {status: 200, body: {remote: true}}});
  const store = A.createLiveStore(A.create({contracts: C, transport: f.transport}));
  const storage = store.loadStorage(false);
  await store.loadPhone();
  assert.equal(store.snapshot().remote, true);
  release();
  await storage;
  assert.equal(store.snapshot().remote, true, 'the storage answer kept the phone flag');
  assert.deepEqual(store.snapshot().storage_summary, {computing: false, summary: null});
  store.stop();
});

test('a /api/status refresh keeps the download list, "Dung lượng" and their errors; status `storage` stays apart', () => {
  const previous = {downloads: {tasks: [1]}, downloads_error: 'x', storage_summary: {computing: true}, storage_error: 'y', remote: true};
  const next = A.normalizeSnapshot({version: 't', jobs: [], storage: {state: 'OK'}}, null, null, previous);
  assert.deepEqual([next.downloads, next.downloads_error, next.storage_summary, next.storage_error, next.storage, next.remote],
    [{tasks: [1]}, 'x', {computing: true}, 'y', {state: 'OK'}, true]);
});

test('live store: one poll timer, one request at a time, none while hidden, cleared when the page closes', async () => {
  const timers = new Map(), realSet = global.setInterval, realClear = global.clearInterval;
  let next = 1, release;
  global.setInterval = (fn, ms) => { const id = next++; timers.set(id, {fn, ms}); return id; };
  global.clearInterval = id => { timers.delete(id); };
  try {
    const slow = new Promise(resolve => { release = resolve; });
    const f = fake({'GET /api/downloads': async () => { await slow; return {status: 200, body: snapshot([])}; },
      'GET /api/storage-summary': {status: 200, body: {computing: false, summary: {}}}});
    const store = A.createLiveStore(A.create({contracts: C, transport: f.transport}));
    store.watchDownloads(true, 2000);
    store.watchDownloads(true, 2000);
    assert.equal(timers.size, 1, 'opening twice keeps one timer');
    const [timer] = timers.values();
    assert.equal(timer.ms, 2000);
    timer.fn(); timer.fn(); // the first answer has not arrived
    await new Promise(resolve => setImmediate(resolve));
    const lists = () => f.calls.filter(c => c.path === '/api/downloads').length;
    assert.equal(lists(), 2, 'one per opening, none while one is pending');
    release();
    await new Promise(resolve => setImmediate(resolve));
    global.document = {hidden: true};
    timer.fn();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(lists(), 2, 'no poll while the tab is hidden');
    delete global.document;
    store.watchDownloads(false);
    assert.equal(timers.size, 0, 'closing the page clears it');
    store.watchDownloads(true, 2000);
    store.stop();
    assert.equal(timers.size, 0, 'stop() clears it');
  } finally {
    global.setInterval = realSet; global.clearInterval = realClear; delete global.document;
  }
});

test('the form: no source to pick, any site; the box is read-only while a batch is sent', () => {
  const evil = '<x-evil onclick="1">';
  const ui = V.createUi();
  ui.text = evil;
  const idle = V.form(snapshot([]), ui, ctx());
  assert.ok(!idle.includes('<x-evil'));
  assert.ok(!/<select/.test(idle), 'no source select');
  assert.match(idle, /từ trang nào cũng được/);
  assert.match(idle, /"Chưa hỗ trợ"/);
  assert.match(idle, /<textarea[^>]* aria-describedby="dl-hint dl-error"/);
  assert.match(idle, /data-action="dl-add"(?![^>]* disabled)/);
  assert.match(V.form(snapshot([]), V.createUi(), ctx({offline: true})), /data-action="dl-add"[^>]* disabled/);
  ui.busy.add('add');
  const html = V.form(snapshot([]), ui, ctx());
  assert.match(html, /<textarea[^>]* readonly>/);
  assert.match(html, /Đang kiểm tra link…/);
  const tools = V.tools(snapshot([]), {...V.createUi(), busy: new Set(['slots'])}, ctx());
  assert.match(tools, /id="dl-slots"[^>]* disabled/);
});

(async () => {
  for (const [name, fn] of tests) {
    try { await fn(); } catch (error) { error.message = name + ': ' + error.message; throw error; }
    passed++;
    process.stdout.write('OK ' + name + '\n');
  }
  process.stdout.write(JSON.stringify({passed, failed: 0}) + '\n');
})().catch(error => {
  process.stderr.write(String(error && error.stack || error) + '\n', () => process.exit(1));
});
