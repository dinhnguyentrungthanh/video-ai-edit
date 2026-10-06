'use strict';
/* Adapter gate: ControlCenterAdapter against a fake transport (no network, no server).
 * Run: node dashboard_v2/verify-adapter.cjs */
const assert = require('node:assert/strict');
const C = require('./contracts.js');
const A = require('./adapter.js');

const TOKEN = 'tok-1';
const job = (id, state, extra = {}) => ({id, state, ...extra});

/* Fake transport: records every call; `script` answers by "METHOD path" (array = one answer per call). */
function fake(script = {}) {
  const calls = [];
  let token = TOKEN;
  const transport = async (method, path, options) => {
    calls.push({method, path, headers: {...options.headers}, body: options.body === undefined ? undefined : JSON.parse(options.body)});
    const key = method + ' ' + path;
    if (key === 'GET /api/session' && !script[key]) return {status: 200, body: {token}};
    let answer = script[key];
    if (Array.isArray(answer)) answer = answer.length > 1 ? answer.shift() : answer[0];
    if (typeof answer === 'function') answer = await answer(options);
    if (answer instanceof Error) throw answer;
    return answer || {status: 200, body: {}};
  };
  return {calls, transport, setToken: t => { token = t; }, posts: () => calls.filter(c => c.method === 'POST'), sessions: () => calls.filter(c => c.path === '/api/session')};
}
const adapterWith = f => A.create({contracts: C, transport: f.transport});

let passed = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('POST gets the token from GET /api/session and sends JSON + X-BiliFlow-Token', async () => {
  const f = fake(), a = adapterWith(f);
  await a.dispatch('scheduler', null, {paused: true});
  assert.equal(f.sessions().length, 1);
  const [post] = f.posts();
  assert.equal(post.path, '/api/scheduler');
  assert.equal(post.headers['Content-Type'], 'application/json');
  assert.equal(post.headers['X-BiliFlow-Token'], TOKEN);
  assert.deepEqual(post.body, {paused: true});
  assert.ok(!f.calls.some(c => c.path.includes(TOKEN)), 'token never in a URL');
  await a.dispatch('scheduler', null, {paused: false});
  assert.equal(f.sessions().length, 1, 'token reused');
});

test('Every operation of guide section 4 uses its real path and body', async () => {
  const f = fake(), a = adapterWith(f);
  const j = job(7, 'READY_TO_EXPORT');
  const cases = [
    ['start', j, {content_style: 'animation', profile: 'careful', detectors: ['advertising'], ocr_recognition_batch_size: 1, fast_scan: false}, '/api/jobs/7/start'],
    ['rerun', j, {detectors: ['gore'], ocr_recognition_batch_size: 8, fast_scan: true}, '/api/jobs/7/rerun'],
    ['resume', j, {}, '/api/jobs/7/resume'], ['pause', j, {}, '/api/jobs/7/pause'],
    ['stopAfter', j, {}, '/api/jobs/7/stop-after-stage'], ['cancel', j, {}, '/api/jobs/7/cancel'],
    ['retry', j, {}, '/api/jobs/7/retry'], ['skip', j, {}, '/api/jobs/7/skip'], ['unskip', j, {}, '/api/jobs/7/unskip'],
    ['hide', j, {}, '/api/jobs/7/hide'], ['unhide', j, {}, '/api/jobs/7/unhide'],
    ['audit', j, {visual: true}, '/api/jobs/7/ai-audit'],
    ['finalize', j, {size_mode: 'default'}, '/api/jobs/7/review/finalize'],
    ['finalize', job(8, 'READY_TO_EXPORT'), {size_mode: 'custom', max_output_gb: 2.5}, '/api/jobs/8/review/finalize'],
    ['finalize', job(9, 'READY_TO_EXPORT'), {size_mode: 'unlimited'}, '/api/jobs/9/review/finalize'],
    ['scheduler', null, {paused: true}, '/api/scheduler'],
    ['shutdown', null, {mode: 'after_stage'}, '/api/shutdown'],
    ['aiConfig', null, {enabled: true, model: 'gpt-5.6-luna', reasoning_effort: 'low'}, '/api/ai/config'],
    ['aiCheck', null, {}, '/api/ai/check'], ['aiLogin', null, {}, '/api/ai/login'],
    ['cleanup', null, {job_ids: [1, 2], preview_id: 'p1', confirm_permanent: true}, '/api/source-cleanup'],
    ['delete', null, {job_ids: [5], preview_id: 'p3', confirm_permanent: true}, '/api/job-delete'],
    ['archive', null, {job_ids: [3], preview_id: 'p2'}, '/api/source-archive'],
    ['restore', null, {job_id: 4}, '/api/source-archive/restore'],
    ['recheck', null, {kind: 'source_cleanup', id: 901}, '/api/source-recycle-check'],
    ['logoClass', null, {key: 'k', memory_class: 'platform_logo', platform: 'iqiyi', expected_sha256: 'a'.repeat(64)}, '/api/logo-memory/class'],
    ['logoDelete', null, {key: 'k', expected_sha256: 'a'.repeat(64)}, '/api/logo-memory/delete'],
    ['phoneMode', null, {enabled: true}, '/api/phone-mode'],
    ['phoneMode', null, {enabled: false}, '/api/phone-mode'],
    ['phoneMode', null, {extend: true}, '/api/phone-mode'],
  ];
  for (const [op, target, body] of cases) await a.dispatch(op, target, body);
  const posts = f.posts();
  assert.equal(posts.length, cases.length);
  cases.forEach(([op, , body, path], i) => {
    assert.equal(posts[i].path, path, op);
    assert.deepEqual(posts[i].body, body, op);
  });
});

test('Invalid job id or unknown operation never reaches the transport', async () => {
  const f = fake(), a = adapterWith(f);
  await assert.rejects(() => a.dispatch('start', job(0, 'DISCOVERED'), {}));
  await assert.rejects(() => a.dispatch('start', null, {}));
  await assert.rejects(() => a.dispatch('nope', job(1, 'X'), {}));
  await assert.rejects(() => a.dispatch('status', null, {}), /POST/);
  assert.equal(f.posts().length, 0);
});

test('403 refreshes the token once and resends exactly once', async () => {
  const f = fake({'POST /api/jobs/7/cancel': [{status: 403, body: {error: 'Phiên Control Center không hợp lệ'}}, {status: 200, body: {state: 'CANCELLED'}}]});
  const a = adapterWith(f);
  await a.refreshToken();
  f.setToken('tok-2');
  const result = await a.dispatch('cancel', job(7, 'QUEUED'), {});
  assert.equal(result.status, 200);
  const posts = f.posts();
  assert.equal(posts.length, 2);
  assert.equal(posts[0].headers['X-BiliFlow-Token'], TOKEN);
  assert.equal(posts[1].headers['X-BiliFlow-Token'], 'tok-2');
  assert.equal(f.sessions().length, 2);
});

test('A second 403 is shown, never a third POST', async () => {
  const f = fake({'POST /api/jobs/7/cancel': {status: 403, body: {error: 'Phiên Control Center không hợp lệ'}}});
  const a = adapterWith(f);
  await assert.rejects(() => a.dispatch('cancel', job(7, 'QUEUED'), {}), e => e.status === 403 && /không hợp lệ/.test(e.message));
  assert.equal(f.posts().length, 2);
  assert.equal(f.sessions().length, 2);
});

test('409 keeps code (top level) and preview, and is never replayed', async () => {
  const preview = {preview_id: 'p2', eligible: [], ineligible: [{job_id: 1, name: 'a', reason: 'r'}], recycle_bin: null, blocked: 'x'};
  const f = fake({'POST /api/source-cleanup': {status: 409, body: {error: 'Danh sách đã thay đổi', code: 'preview_changed', preview}}});
  const a = adapterWith(f);
  await assert.rejects(() => a.dispatch('cleanup', null, {job_ids: [1], preview_id: 'p1'}), e => {
    assert.equal(e.status, 409); assert.equal(e.code, 'preview_changed'); assert.deepEqual(e.preview, preview);
    assert.match(e.message, /Danh sách đã thay đổi/); return true;
  });
  assert.equal(f.posts().length, 1);
});

test('400 (finalize refusing a file without a manifest) shows the reason verbatim, no retry', async () => {
  const reason = 'Thư mục output đã có file “Tập 1-reviewed.mp4” nhưng không có manifest chứng minh; BiliFlow không ghi đè';
  const f = fake({'POST /api/jobs/7/review/finalize': {status: 400, body: {error: reason}}});
  const a = adapterWith(f);
  await assert.rejects(() => a.dispatch('finalize', job(7, 'READY_TO_EXPORT'), {size_mode: 'default'}), e => e.status === 400 && e.message === reason);
  assert.equal(f.posts().length, 1);
});

test('408, 500 and a dropped connection are shown and never resent', async () => {
  for (const [answer, status] of [[{status: 408, body: {error: 'Request timed out'}}, 408], [{status: 500, body: {error: 'boom'}}, 500], [new TypeError('Failed to fetch'), 0], [{status: 408, body: null}, 408]]) {
    const f = fake({'POST /api/jobs/7/retry': answer});
    const a = adapterWith(f);
    await assert.rejects(() => a.dispatch('retry', job(7, 'FAILED'), {}), e => {
      assert.equal(e.status, status); assert.ok(e.message.length > 0); return true;
    });
    assert.equal(f.posts().length, 1, 'status ' + status);
  }
});

test('A GET answered 403 does not refresh the token and is not retried', async () => {
  const f = fake({'GET /api/status': {status: 403, body: {error: 'Địa chỉ truy cập không hợp lệ'}}});
  const a = adapterWith(f);
  await assert.rejects(() => a.loadStatus(), e => e.status === 403);
  assert.equal(f.sessions().length, 0);
  assert.equal(f.calls.length, 1);
});

test('An older /api/status answer never replaces a newer one', async () => {
  let release;
  const slow = new Promise(r => { release = r; });
  const f = fake({'GET /api/status': [() => slow.then(() => ({status: 200, body: {n: 1}})), {status: 200, body: {n: 2}}]});
  const a = adapterWith(f);
  const first = a.loadStatus(), second = a.loadStatus();
  assert.deepEqual(await second, {n: 2});
  release();
  assert.equal(await first, null);
});

test('A double click sends one POST; a later click sends again', async () => {
  let release;
  const gate = new Promise(r => { release = r; });
  const f = fake({'POST /api/jobs/7/review/finalize': () => gate.then(() => ({status: 200, body: {status: 'QUEUED'}}))});
  const a = adapterWith(f);
  const j = job(7, 'READY_TO_EXPORT');
  const p1 = a.dispatch('finalize', j, {size_mode: 'default'}), p2 = a.dispatch('finalize', j, {size_mode: 'default'});
  release();
  await Promise.all([p1, p2]);
  assert.equal(f.posts().length, 1);
  await a.dispatch('finalize', j, {size_mode: 'default'});
  assert.equal(f.posts().length, 2);
});

test('Previews are GET only, with encoded ids and the 1-50 limit', async () => {
  const f = fake({'GET /api/source-archive/preview?ids=3%2C4': {status: 200, body: {preview_id: 'x', eligible: []}},
    'GET /api/job-delete/preview?ids=111%2C113': {status: 200, body: {preview_id: 'd', eligible: []}}});
  const a = adapterWith(f);
  assert.equal((await a.preview('archive', [3, 4])).preview_id, 'x');
  assert.equal((await a.preview('delete', [111, 113])).preview_id, 'd');
  await assert.rejects(() => a.preview('purge', [1]), e => e.status === 400);
  await assert.rejects(() => a.preview('cleanup', []));
  await assert.rejects(() => a.preview('cleanup', Array.from({length: 51}, (_, i) => i + 1)));
  await assert.rejects(() => a.preview('cleanup', [1, -2]));
  await assert.rejects(() => a.preview('cleanup', ['1;DROP']));
  assert.equal(f.posts().length, 0);
});

test('Snapshot normalization keeps backend values and derives only display fields', () => {
  const status = {version: '0.7.24', jobs: [
    {id: 5, state: 'RENDERING', source_path: 'E:\\DungChung\\BiliFlow\\input\\Tập 5.mp4', duration_seconds: 3725, detector_groups: ['adult'], active_revision: 2, current_stage: 'render', render_progress: {state: 'VERIFYING', percent: 100}},
    {id: 6, state: 'PAUSED', source_path: '/x/y.mkv', current_stage: 'render', render_request: true},
    {id: 8, state: 'PAUSED', source_path: '/x/z.mkv', current_stage: 'render'},
    {id: 7, state: 'COMPLETED', source_path: 'a.mp4', cleanup: {eligible: true, output_name: 'a-reviewed.mp4'}},
  ], active: {job_id: 5, stage: 'render', pid: 1}, queue: {length: 0, paused: false},
  resources: {cpu_percent: 3, memory: {percent: 4, used_bytes: 1, total_bytes: 2}, disk: {percent: 5, free_bytes: 6, total_bytes: 7}, gpu: null}, source_cleanup_running: false};
  const s = A.normalizeSnapshot(status, null, {memory_sha256: 'b'.repeat(64), records: [{key: 'k1', labels: ['iQIYI'], memory_class: 'platform_logo', frames: 2, frame_urls: ['/api/logo-memory/frame?key=k1&i=0']}]});
  const [a, b, d, c] = s.jobs;
  assert.equal(a.name, 'Tập 5'); assert.equal(a.duration, '1:02:05'); assert.equal(a.render_progress.state, 'VERIFYING');
  assert.equal(C.tab(a), 'export'); assert.equal(a.output_path, null);
  assert.equal(b.name, 'y'); assert.equal(b.render_request, true); assert.deepEqual(b.detector_groups, []);
  assert.equal(d.render_request, false, 'render_request comes from the backend only, never from current_stage');
  assert.equal(c.output_path, 'a-reviewed.mp4');
  assert.equal(s.resources.gpu, null); assert.equal(s.logos[0].name, 'iQIYI'); assert.equal(s.memory_sha256, 'b'.repeat(64));
  assert.equal(s.ai.ready, false, 'AI is not ready until /api/ai says so');
  assert.equal(s.mode, 'live'); assert.deepEqual(s.requests, []);
});

test('Live store: an action reloads /api/status; a failure leaves offline state with the last data', async () => {
  const f = fake({'GET /api/status': [{status: 200, body: {jobs: [{id: 1, state: 'QUEUED', source_path: 'a.mp4'}]}}, {status: 200, body: {jobs: [{id: 1, state: 'PAUSED', source_path: 'a.mp4'}]}}, new TypeError('down')],
    'GET /api/ai': {status: 200, body: {ready: true, config: {enabled: true}, message: 'ok'}}});
  const store = A.createLiveStore(adapterWith(f));
  const seen = [];
  store.subscribe(s => seen.push(s.jobs.map(j => j.state).join()));
  await store.refresh();
  await store.dispatch('pause', {id: 1}, {});
  assert.deepEqual(seen.slice(-2), ['QUEUED', 'PAUSED']);
  await store.refresh();
  assert.equal(store.snapshot().offline, true);
  assert.equal(store.snapshot().jobs[0].state, 'PAUSED');
  assert.equal(f.posts().length, 1);
});

test('Live store: a refused file action is not followed by any other write', async () => {
  const f = fake({'POST /api/source-archive': {status: 409, body: {error: 'Bận', code: 'busy'}}, 'GET /api/status': {status: 200, body: {jobs: []}}});
  const store = A.createLiveStore(adapterWith(f));
  await assert.rejects(() => store.fileAction('archive', [3], 'p'), e => e.status === 409 && e.code === 'busy');
  assert.equal(f.posts().length, 1);
});

test('Live store: "Xóa video gốc" and "Xóa video" send confirm_permanent, "Lưu trữ" does not', async () => {
  const ok = {status: 200, body: {results: []}};
  const f = fake({'POST /api/source-cleanup': ok, 'POST /api/job-delete': ok, 'POST /api/source-archive': ok, 'GET /api/status': {status: 200, body: {jobs: []}}});
  const store = A.createLiveStore(adapterWith(f));
  await store.fileAction('cleanup', [1], 'p1');
  await store.fileAction('delete', [2, 3], 'p2');
  await store.fileAction('archive', [4], 'p3');
  assert.deepEqual(f.posts().map(p => [p.path, p.body]), [
    ['/api/source-cleanup', {job_ids: [1], preview_id: 'p1', confirm_permanent: true}],
    ['/api/job-delete', {job_ids: [2, 3], preview_id: 'p2', confirm_permanent: true}],
    ['/api/source-archive', {job_ids: [4], preview_id: 'p3'}],
  ]);
});

test('Phone mode: the PC store keeps the status with its code; the phone store only learns it is remote', async () => {
  const pcStatus = {remote: false, enabled: true, url: 'http://192.168.1.5:8767/', code: 'abcd2345', locked: false};
  const f = fake({'GET /api/phone-mode': {status: 200, body: pcStatus}, 'GET /api/status': {status: 200, body: {jobs: []}},
    'POST /api/phone-mode': {status: 200, body: {remote: false, enabled: false, code: null}}});
  const store = A.createLiveStore(adapterWith(f));
  await store.loadPhone();
  assert.deepEqual(store.snapshot().phone, pcStatus); assert.equal(store.snapshot().remote, false);
  await store.refresh();
  assert.equal(store.snapshot().phone.code, 'abcd2345', 'a status poll keeps the phone panel');
  await store.dispatch('phoneMode', null, {enabled: false});
  assert.equal(store.snapshot().phone.enabled, false);
  const posts = f.posts(); assert.equal(posts.length, 1); assert.deepEqual(posts[0].body, {enabled: false});
  const g = fake({'GET /api/phone-mode': {status: 200, body: {remote: true, enabled: true}}});
  const remote = A.createLiveStore(adapterWith(g));
  await remote.loadPhone();
  assert.equal(remote.snapshot().remote, true);
  const old = fake({'GET /api/phone-mode': {status: 404, body: {error: 'Không tìm thấy'}}});
  const legacy = A.createLiveStore(adapterWith(old));
  await legacy.loadPhone();
  assert.deepEqual(legacy.snapshot().phone, {unavailable: true});
});

test('Phone mode: a 403 pc_only refusal is shown, without token refresh or resend', async () => {
  const f = fake({'POST /api/source-archive': {status: 403, body: {error: 'Chỉ làm trên PC: lưu trữ…', code: 'pc_only'}}});
  const a = adapterWith(f);
  await assert.rejects(() => a.dispatch('archive', null, {job_ids: [1], preview_id: 'p'}),
    e => e.status === 403 && /Chỉ làm trên PC/.test(e.message));
  assert.equal(f.posts().length, 1, 'pc_only is not a token problem: one POST, no token refresh');
  assert.equal(f.sessions().length, 1);
});

test('Review (R0): GETs of one job, encoded query strings, URL builders only, no write', async () => {
  const f = fake({
    'GET /api/jobs/12/review/session': {status: 200, body: {token: 'tok-review', media_key: 'k/1+2=='}},
    'GET /api/jobs/12/review/queue': {status: 200, body: {items: [], status: 'READY_FOR_EDIT_PLAN'}},
    'GET /api/jobs/12/review/evidence?item=visual_logo%3A1%20%26%20x': {status: 200, body: {frames: []}},
  });
  const a = adapterWith(f), r = a.review(12);
  assert.equal(r.jobId, 12);
  assert.deepEqual(await r.queue(), {items: [], status: 'READY_FOR_EDIT_PLAN'});
  await Promise.all([r.session(), r.session()]);
  assert.equal(f.calls.filter(c => c.path === '/api/jobs/12/review/session').length, 1, 'session is single-flight');
  assert.equal(r.mediaKey(), 'k/1+2==');
  assert.deepEqual(await r.session(), {media_key: 'k/1+2=='}, 'the token never leaves the adapter');
  await r.resources(); await r.exportState();
  await r.evidence('visual_logo:1 & x');
  assert.deepEqual(f.calls.map(c => c.method + ' ' + c.path), ['GET /api/jobs/12/review/queue', 'GET /api/jobs/12/review/session',
    'GET /api/jobs/12/review/session', 'GET /api/jobs/12/review/resources', 'GET /api/jobs/12/review/export',
    'GET /api/jobs/12/review/evidence?item=visual_logo%3A1%20%26%20x']);
  assert.equal(r.frameUrl('a/b?c#d', 12.5, 'k/1+2=='), '/api/jobs/12/review/frame?item=a%2Fb%3Fc%23d&t=12.5&k=k%2F1%2B2%3D%3D');
  assert.equal(r.videoUrl('k/1+2=='), '/api/jobs/12/review/video?k=k%2F1%2B2%3D%3D');
  assert.equal(r.mediaUrl('job/rev 1/ảnh#1.jpg'), '/media/' + encodeURIComponent('job/rev 1/ảnh#1.jpg'));
  assert.equal(f.posts().length, 0, 'R0 is read-only');
  await a.dispatch('scheduler', null, {paused: true});
  assert.equal(f.posts()[0].headers['X-BiliFlow-Token'], 'tok-review', 'the review session refreshes the same token');
  for (const bad of [0, -1, 1.5, '7x', '1e3', '', null, 'abc', 1e10]) assert.throws(() => a.review(bad), e => e.status === 400, String(bad));
});

test('Review (R0): probeVideo sends Range bytes=0-0 in raw mode and returns only the status', async () => {
  let seen = null;
  const f = fake({'GET /api/jobs/3/review/video?k=key': opts => { seen = opts; return {status: 410, body: null}; }});
  const status = await adapterWith(f).review(3).probeVideo('key');
  assert.equal(status, 410);
  assert.equal(seen.headers.Range, 'bytes=0-0');
  assert.equal(seen.raw, true, 'the transport is asked not to parse the body');
  let parsed = false;
  const raw = A.fetchTransport(async () => ({status: 206, body: {cancel: async () => {}}, text: async () => { parsed = true; return 'x'; }}));
  assert.deepEqual(await raw('GET', '/v', {headers: {}, raw: true}), {status: 206, body: null});
  assert.equal(parsed, false, 'a raw answer is never read');
});

test('Review (R0): contracts.request appends an encoded query and keeps the endpoint list', async () => {
  assert.equal(C.request('evidence', {id: 4}, null, {item: 'x y', skip: null}).path, '/api/jobs/4/review/evidence?item=x%20y');
  assert.equal(C.request('queue', {id: 4}).path, '/api/jobs/4/review/queue');
  assert.equal(C.request('status', null, null, {}).path, '/api/status');
  assert.throws(() => C.request('queue', {id: '4'}), /job id/);
});

test('Live store: pause() stops /api/status polling while the review dialog is open; resume() refreshes at once', async () => {
  const f = fake({'GET /api/status': {status: 200, body: {version: 't', jobs: []}}});
  const store = A.createLiveStore(adapterWith(f));
  const statuses = () => f.calls.filter(c => c.path === '/api/status').length;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  try {
    store.start(20);
    await sleep(70);
    store.pause();
    await sleep(10);
    const before = statuses();
    await sleep(90); // several 20 ms ticks (Windows timers are ~15 ms coarse): none may poll
    assert.equal(statuses(), before, 'no poll while paused');
    // R0-T1: stop the interval first, so the count below only sees what resume() does.
    store.stop();
    const stopped = statuses();
    store.resume();
    await sleep(5);
    assert.equal(statuses(), stopped + 1, 'one refresh at once on resume');
    store.resume();
    await sleep(5);
    assert.equal(statuses(), stopped + 1, 'resume() without pause() does nothing');
    assert.equal(typeof store.review(5).queue, 'function');
  } finally {
    store.stop(); // a failed assert must not leave the interval keeping node alive
  }
});

/* R2: review writes of one job: a serial chain (never once()), retries 300/900 ms on a network error or >= 500. */
function writeAdapter(script) {
  const f = fake(script), sleeps = [];
  const a = A.create({contracts: C, transport: f.transport, sleep: ms => { sleeps.push(ms); return Promise.resolve(); }});
  return {f, a, sleeps, posts: () => f.posts().filter(c => c.path.includes('/review/'))};
}
const QUEUE = n => ({status: 'REVIEW_REQUIRED', items: [], counts: {total: n, pending: n}});
test('Review writes (R2): two quick decisions on two cards are two POSTs, in order, one after the other', async () => {
  let release;
  const gate = new Promise(r => { release = r; });
  const answers = [() => gate.then(() => ({status: 200, body: QUEUE(1)})), {status: 200, body: QUEUE(2)}];
  const {f, a, posts} = writeAdapter({'POST /api/jobs/12/review/decision': answers});
  const r = a.review(12);
  const b1 = {id: 'gore-12-0001', decision: 'CUT', full_frame: false, note: null};
  const b2 = {id: 'logo-12-0002', decision: 'BLUR', full_frame: false, note: null, remember_platform_logo: true};
  const p1 = r.write('decision', b1), p2 = r.write('decision', b2);
  assert.equal(r.pendingWrites(), 2);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(posts().length, 1, 'the second POST waits for the first');
  release();
  const [x1, x2] = await Promise.all([p1, p2]);
  assert.deepEqual(posts().map(c => c.body), [b1, b2], 'bodies sent exactly as given, in order');
  assert.equal(x1.last, false); assert.equal(x2.last, true, 'only the last pending write applies its queue');
  assert.deepEqual(x2.body, QUEUE(2));
  assert.equal(r.pendingWrites(), 0);
  assert.equal(f.calls.filter(c => c.path === '/api/status').length, 0, 'no /api/status after a decision');
  assert.ok(posts().every(c => c.headers['X-BiliFlow-Token'] === TOKEN && c.headers['Content-Type'] === 'application/json'));
});
test('Review writes (R2): a network error or >= 500 is sent again after 300 ms then 900 ms; then it fails with the attempts', async () => {
  let w = writeAdapter({'POST /api/jobs/12/review/decision': [{status: 500, body: {error: 'bận'}}, new Error('reset'), {status: 200, body: QUEUE(0)}]});
  const ok = await w.a.review(12).write('decision', {id: 'a', decision: 'KEEP', full_frame: false, note: null});
  assert.equal(w.posts().length, 3); assert.deepEqual(w.sleeps, [300, 900]); assert.equal(ok.last, true);
  w = writeAdapter({'POST /api/jobs/12/review/clear': [{status: 503, body: {error: 'Hàng đợi đang bị khóa'}}]});
  await assert.rejects(() => w.a.review(12).write('clear', {id: 'a'}), e => e.status === 503 && e.attempts === 3 && e.message === 'Hàng đợi đang bị khóa');
  assert.equal(w.posts().length, 3); assert.deepEqual(w.sleeps, [300, 900], 'two retries only');
});
test('Review writes (R2): with the real timers the resends come about 300 ms and 900 ms apart', async () => {
  const times = [];
  const f = fake({'POST /api/jobs/12/review/decision': () => { times.push(Date.now()); return times.length < 3 ? {status: 502, body: {}} : {status: 200, body: QUEUE(0)}; }});
  await A.create({contracts: C, transport: f.transport}).review(12).write('decision', {id: 'a', decision: 'KEEP', full_frame: false, note: null});
  const gaps = [times[1] - times[0], times[2] - times[1]];
  assert.ok(gaps[0] >= 280 && gaps[0] < 600 && gaps[1] >= 870 && gaps[1] < 1400, JSON.stringify(gaps));
});
test('Review writes (R2): 400 is not sent again and keeps the server text; the next write still goes', async () => {
  const text = 'Chỉ có thể ghi nhớ logo hãng phim khi chọn Giữ nguyên';
  const {a, posts, sleeps} = writeAdapter({'POST /api/jobs/12/review/decision': [{status: 400, body: {error: text}}, {status: 200, body: QUEUE(0)}]});
  const r = a.review(12);
  const p1 = r.write('decision', {id: 'a', decision: 'CUT', full_frame: false, note: null, remember_studio_logo: true});
  const p2 = r.write('decision', {id: 'b', decision: 'KEEP', full_frame: false, note: null});
  await assert.rejects(() => p1, e => e.status === 400 && e.message === text && e.attempts === 1);
  assert.equal((await p2).last, true);
  assert.equal(posts().length, 2); assert.deepEqual(sleeps, [], '400 never waits or retries');
});
test('Review writes (R2): 403 gets a new session and sends once more; a second 403 is not retried', async () => {
  let w = writeAdapter({'POST /api/jobs/12/review/decision': [{status: 403, body: {error: 'token'}}, {status: 200, body: QUEUE(0)}]});
  await w.a.review(12).write('decision', {id: 'a', decision: 'KEEP', full_frame: false, note: null});
  assert.equal(w.posts().length, 2); assert.equal(w.f.sessions().length, 2, 'token, then one refresh'); assert.deepEqual(w.sleeps, []);
  w = writeAdapter({'POST /api/jobs/12/review/decision': [{status: 403, body: {error: 'Phiên không hợp lệ'}}]});
  await assert.rejects(() => w.a.review(12).write('decision', {id: 'a', decision: 'KEEP', full_frame: false, note: null}), e => e.status === 403);
  assert.equal(w.posts().length, 2, 'one resend after the refresh, no retry loop'); assert.deepEqual(w.sleeps, []);
});
test('Review writes (R2): the chain belongs to the job, not to one dialog: a new review(id) sees the pending writes and waits for them', async () => {
  let release;
  const gate = new Promise(r => { release = r; });
  const {a, posts} = writeAdapter({'POST /api/jobs/12/review/decision': () => gate.then(() => ({status: 500, body: {error: 'WinError 32'}}))});
  const first = a.review(12), failed = first.write('decision', {id: 'a', decision: 'KEEP', full_frame: false, note: null}).catch(e => e);
  const again = a.review(12), other = a.review(13);
  assert.equal(again.pendingWrites(), 1, 'a reopened dialog sees the write'); assert.equal(other.pendingWrites(), 0);
  let idle = false;
  const waiting = again.idle().then(() => { idle = true; });
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(idle, false);
  release();
  const error = await failed; await waiting;
  assert.equal(error.attempts, 3, 'it still finishes (with its retries) after the dialog closed');
  assert.equal(idle, true); assert.equal(again.pendingWrites(), 0); assert.equal(posts().length, 3);
  await assert.rejects(() => again.write('finalize', {size_mode: 'default'}), e => e.status === 400);
  assert.equal(posts().length, 3, 'only decision, clear and bulk go through the chain');
});

test('R3: a bulk write queued after pending decisions waits for them; it sends {filter} only; Quảng cáo is two writes, in order', async () => {
  let release;
  const gate = new Promise(r => { release = r; });
  const {f, a, posts} = writeAdapter({'POST /api/jobs/12/review/decision': () => gate.then(() => ({status: 200, body: QUEUE(1)})), 'POST /api/jobs/12/review/bulk-keep': {status: 200, body: QUEUE(0)}});
  const r = a.review(12);
  const d = r.write('decision', {id: 'a', decision: 'KEEP', full_frame: false, note: null});
  const b1 = r.write('bulkKeep', {filter: 'visual_logo'}), b2 = r.write('bulkKeep', {filter: 'text'});
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.deepEqual(posts().map(c => c.path), ['/api/jobs/12/review/decision'], 'the bulk waits for the decision');
  release();
  await Promise.all([d, b1, b2]);
  assert.deepEqual(posts().map(c => [c.path, c.body]), [['/api/jobs/12/review/decision', {id: 'a', decision: 'KEEP', full_frame: false, note: null}],
    ['/api/jobs/12/review/bulk-keep', {filter: 'visual_logo'}], ['/api/jobs/12/review/bulk-keep', {filter: 'text'}]]);
  assert.equal(f.calls.filter(c => c.path === '/api/status').length, 0);
});
test('R3: finalize is sent once, never retried (500, network error, 409), and only through dispatch', async () => {
  for (const answer of [{status: 500, body: {error: 'lỗi'}}, new Error('reset'), {status: 409, body: {error: 'đổi', code: 'state_changed'}}]) {
    const sleeps = [], f = fake({'POST /api/jobs/7/review/finalize': answer});
    const a = A.create({contracts: C, transport: f.transport, sleep: ms => { sleeps.push(ms); return Promise.resolve(); }});
    await assert.rejects(() => a.dispatch('finalize', job(7, 'READY_TO_EXPORT'), {size_mode: 'custom', max_output_gb: 2.5}));
    assert.equal(f.posts().length, 1, 'one POST'); assert.deepEqual(sleeps, [], 'no retry wait');
    assert.deepEqual(f.posts()[0].body, {size_mode: 'custom', max_output_gb: 2.5}, 'no extra field');
    await assert.rejects(() => a.review(7).write('finalize', {size_mode: 'default'}), e => e.status === 400);
    assert.equal(f.posts().length, 1, 'the review write chain refuses finalize');
  }
});

(async () => {
  for (const [name, fn] of tests) {
    try { await fn(); } catch (error) { error.message = name + ': ' + error.message; throw error; }
    passed++;
    process.stdout.write('OK ' + name + '\n');
  }
  process.stdout.write(JSON.stringify({passed, failed: 0}) + '\n');
})().catch(error => {
  // R0-T1: exit even if a failed test left a timer running (a store that was never stopped).
  process.stderr.write(String(error && error.stack || error) + '\n', () => process.exit(1));
});
