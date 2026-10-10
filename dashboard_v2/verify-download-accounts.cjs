'use strict';
/* "Tài khoản nguồn phim", episode dialog and groups gate (M5, docs/SOURCE_ACCOUNTS_PLAN.md 9.14): contracts, the
 * adapter's account/episode/group calls, download-core/-view/-episodes against fakes (no network, no server, no real
 * DOM; the dialog controller runs on a tiny fake document). Run: node dashboard_v2/verify-download-accounts.cjs
 * tests/test_dashboard_v2_downloads.py runs it. Labels carry markup and secret-looking text on purpose. */
const assert = require('node:assert/strict');
const C = require('./contracts.js');
const A = require('./adapter.js');
const K = require('./download-core.js');
const V = require('./download-view.js');
const E = require('./download-episodes.js');

const TOKEN = 'tok-acc';
const BAIT = '<img src=x onerror="window.__x=1">';
const SECRET = 'ticket=SECRET-TICKET; storage_state=C:\\BiliFlow\\state\\vault\\phim-a.bin';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const tick = () => new Promise(resolve => setImmediate(resolve));
/* Waits until `check()` holds (or `ms` passed); the caller asserts afterwards, so a slow machine only waits longer. */
const until = async (check, ms = 3000) => { const end = Date.now() + ms; while (!check() && Date.now() < end) await sleep(5); };
const task = (id, state, extra = {}) => ({id, state, url: 'https://phim-a.example/p/' + id, title: 'Lượt ' + id, attempt: 1,
  downloaded_bytes: 0, total_bytes: null, speed: null, eta: null, error_message: null, error_code: null, output_name: null,
  entries: null, name_locked: false, media: {}, ...extra});
const counts = extra => Object.fromEntries(['pending', 'held', 'queued', 'waiting_login', 'running', 'completed', 'failed', 'stopped',
  'interrupted', 'cancelled', 'expired', 'removed', 'attention'].map(k => [k, extra[k] || 0]));
const group = (id, extra = {}) => ({id, parent_task_id: 20, source_id: 'phim-a', source_label: 'Nguồn A', title: 'Phim ' + id, state: 'ACTIVE',
  mode: 'all', complete: true, note: null, reasons: [], total: 4, done: 1, counts: counts({completed: 1, running: 1, queued: 2}), percent: null,
  finished: false, existing_count: 0, existing: null, created_at: '2026-10-08T10:00:00+00:00', ...extra});
const source = (id, extra = {}) => ({id, label: 'Nguồn ' + id, state: 'NOT_CONNECTED', session_check: 'ttl', authenticated_at: null,
  recheck_at: null, checked_at: null, error_code: null, message: null, login_supported: true, reader_supported: true, login_running: false,
  last_login: null, warnings: [], waiting_tasks: 0, ...extra});
const snapshot = (tasks, extra = {}) => ({tasks, counts: {}, settings: {slots: 2, max_slots: 3}, space: {}, temp: {tasks: 0, bytes: 0},
  running: [], worker_error: null, worker_error_at: null, groups: [], accounts: {sources: [], problems: [], problem_text: '', error: null}, ...extra});
const ctx = (extra = {}) => ({icon: () => '', offline: false, remote: false, device: 'pc', error: '', storageError: '', now: Date.parse('2026-10-08T10:00:00Z'), ...extra});

function fake(script = {}) {
  const calls = [];
  const transport = async (method, path, options) => {
    calls.push({method, path, headers: {...options.headers}, body: options.body === undefined ? undefined : JSON.parse(options.body)});
    const key = method + ' ' + path;
    if (key === 'GET /api/session') return {status: 200, body: {token: TOKEN}};
    let answer = script[key];
    if (Array.isArray(answer)) answer = answer.length > 1 ? answer.shift() : answer[0];
    if (typeof answer === 'function') answer = await answer(options);
    if (answer === 'DROP') throw new Error('connection reset');
    return answer || {status: 200, body: {}};
  };
  return {calls, transport, posts: () => calls.filter(c => c.method === 'POST'), lists: () => calls.filter(c => c.path === '/api/downloads').length};
}

let passed = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/* ----- contracts and adapter ----- */
test('contracts: {source} takes only a configured id; every account and group route is a POST with its id', () => {
  for (const [op, tail] of [['accountLogin', 'login'], ['accountCancelLogin', 'cancel-login'], ['accountDisconnect', 'disconnect']]) {
    assert.deepEqual([C.request(op, {source: 'phim-a'}).method, C.request(op, {source: 'phim-a'}).path], ['POST', '/api/download-accounts/phim-a/' + tail]);
    for (const bad of ['', 'A', '-a', '../x', 'a/b', 'a b', 'a'.repeat(41), undefined, 7]) assert.throws(() => C.request(op, {source: bad}), /mã nguồn/);
  }
  assert.deepEqual(C.accountOps, {login: 'accountLogin', 'cancel-login': 'accountCancelLogin', disconnect: 'accountDisconnect'});
  for (const op of ['Stop', 'Resume', 'Cancel', 'Retry', 'Remove']) {
    assert.equal(C.request('downloadGroup' + op, {id: 4}).path, '/api/downloads/groups/4/' + op.toLowerCase());
    assert.equal(C.request('downloadGroup' + op, {id: 4}).method, 'POST');
  }
  assert.equal(C.request('downloadEpisodes', {id: 18}).method, 'GET');
  assert.equal(C.request('downloadEpisodesDraft', {id: 18}).path, '/api/downloads/18/episodes/draft');
  assert.equal(C.request('downloadEpisodesConfirm', {id: 18}).path, '/api/downloads/18/episodes/confirm');
  assert.equal(C.request('downloadGroup', {id: 4}).path, '/api/downloads/groups/4');
  assert.match(C.PC_ONLY_ACCOUNT_REASON, /Chỉ làm trên PC/);
});

test('adapter: a refusal keeps only the listed detail fields (never a vault path, cookie or ticket)', async () => {
  const f = fake({'POST /api/downloads/18/episodes/confirm': {status: 409, body: {error: 'Lượt này không còn chờ chọn tập.', code: 'NOT_WAITING',
    group_id: 5, state: 'EXPANDED', cookie: SECRET, path: 'C:\\vault', ticket: SECRET}}});
  const a = A.create({contracts: C, transport: f.transport});
  const error = await a.dispatch('downloadEpisodesConfirm', {id: 18}, {selection: {mode: 'all'}}).catch(e => e);
  assert.equal(error.status, 409); assert.equal(error.code, 'NOT_WAITING');
  assert.deepEqual(error.detail, {group_id: 5, state: 'EXPANDED'});
  assert.ok(!JSON.stringify(error.detail).includes('SECRET'));
  assert.match(error.message, /\(NOT_WAITING\)$/);
});

test('live store: a draft never refreshes the list; Tải N tập and the account buttons do, also after a refusal; POSTs carry the token', async () => {
  const f = fake({'POST /api/downloads/18/episodes/draft': {status: 200, body: {revision: 1}},
    'POST /api/downloads/18/episodes/confirm': {status: 400, body: {error: 'x', code: 'SCOPE_NOT_CONFIRMED', count: 3, confirm_label: 'Tải 3 tập đã thấy'}},
    'POST /api/download-accounts/phim-a/login': {status: 202, body: {login: 'STARTED'}},
    'POST /api/download-accounts/phim-b/disconnect': {status: 403, body: {error: C.PC_ONLY_ACCOUNT_REASON, code: 'pc_only'}},
    'GET /api/downloads': {status: 200, body: snapshot([])}, 'GET /api/phone-mode': {status: 200, body: {remote: false, enabled: false}}});
  const store = A.createLiveStore(A.create({contracts: C, transport: f.transport}));
  await store.episodeDraft(18, {selection: {mode: 'all'}, fingerprint: 'f', revision: 0});
  assert.equal(f.lists(), 0, 'no list refresh after a draft');
  const scope = await store.episodeConfirm(18, {selection: {mode: 'all'}}).catch(e => e);
  assert.equal(scope.code, 'SCOPE_NOT_CONFIRMED'); assert.deepEqual(scope.detail, {count: 3, confirm_label: 'Tải 3 tập đã thấy'});
  assert.equal(f.lists(), 1);
  await assert.rejects(() => store.accountAction('accountLogin', 'phim-a'), /Chưa xác định được trang này mở trên PC/);
  assert.equal(f.posts().filter(p => p.path.startsWith('/api/download-accounts/')).length, 0, 'nothing sent before the PC is confirmed');
  assert.equal(f.lists(), 1);
  await store.loadPhone();
  assert.equal(store.snapshot().device, 'pc');
  const started = await store.accountAction('accountLogin', 'phim-a');
  assert.deepEqual(started, {login: 'STARTED'}, 'the 202 is returned as is; the panel follows the list');
  assert.equal(f.lists(), 2);
  const phone = await store.accountAction('accountDisconnect', 'phim-b').catch(e => e);
  assert.equal(phone.status, 403);
  assert.equal(f.posts().filter(p => p.path.endsWith('/disconnect')).length, 1, 'pc_only is never resent with a new session');
  assert.equal(f.lists(), 3);
  assert.ok(f.posts().every(p => p.headers['X-BiliFlow-Token'] === TOKEN));
  assert.deepEqual(f.posts().find(p => p.path.endsWith('/login')).body, {}, 'a sign-in sends no password, cookie or session');
  await assert.rejects(() => store.accountAction('accountLogin', '../phim-a'), /mã nguồn/);
  await store.loadEpisodes(18); await store.loadGroup(4);
  assert.deepEqual(f.calls.slice(-2).map(c => c.path), ['/api/downloads/18/episodes', '/api/downloads/groups/4']);
  store.stop();
});

test('adapter: a double press of Tải N tập is one POST; a lost answer is status 0 and the list still refreshes', async () => {
  let release;
  const slow = new Promise(resolve => { release = resolve; });
  const f = fake({'POST /api/downloads/18/episodes/confirm': [async () => { await slow; return {status: 200, body: {replay: false}}; }, 'DROP'],
    'GET /api/downloads': {status: 200, body: snapshot([])}});
  const store = A.createLiveStore(A.create({contracts: C, transport: f.transport}));
  const body = {selection: {mode: 'all'}, fingerprint: 'f', idempotency_key: 'ep-aaaaaaaaaaaa'};
  const one = store.episodeConfirm(18, body), two = store.episodeConfirm(18, body);
  await tick();
  release();
  await Promise.all([one, two]);
  assert.equal(f.posts().length, 1);
  const lost = await store.episodeConfirm(18, body).catch(e => e);
  assert.equal(lost.status, 0);
  assert.equal(f.posts().length, 2, 'never resent by the adapter: the dialog resends with the same key');
  assert.ok(f.lists() >= 2);
  store.stop();
});

/* ----- core ----- */
test('account state comes from the server only: a closed window, a 202 or an old last_login is never "Đã kết nối"', () => {
  const label = extra => K.accountState(source('a', extra)).label;
  assert.equal(label({state: 'CONNECTED'}), 'Đã kết nối');
  assert.equal(label({state: 'NOT_CONNECTED', last_login: {connected: true, code: 'CONNECTED'}}), 'Chưa đăng nhập');
  assert.equal(label({state: 'NOT_CONNECTED', last_login: {connected: false, code: 'LOGIN_WINDOW_CLOSED'}}), 'Chưa đăng nhập');
  assert.equal(label({state: 'LOGGING_IN'}), 'Đang đăng nhập');
  assert.equal(label({state: 'NOT_CONNECTED', login_running: true}), 'Đang đăng nhập');
  assert.equal(label({state: 'NEEDS_LOGIN', error_code: 'SESSION_EXPIRED'}), 'Hết phiên');
  assert.equal(label({state: 'NEEDS_LOGIN', error_code: 'SESSION_REJECTED'}), 'Cần đăng nhập lại');
  assert.equal(label({state: 'NOT_CONNECTED', login_supported: false}), 'Chưa hỗ trợ');
  assert.equal(label({state: 'NOT_CONNECTED', reader_supported: false}), 'Chưa hỗ trợ', 'no page reader: a session could not be used');
  assert.equal(label({state: 'CONNECTED', reader_supported: false}), 'Đã kết nối', 'a saved session keeps its state');
  assert.equal(label({state: 'CHECK_FAILED', error_code: 'CHECK_UNREACHABLE'}), 'Chưa kiểm tra được kết nối', 'a network error is not "Hết phiên"');
  assert.equal(label({state: 'UNAVAILABLE'}), 'Chưa sẵn sàng');
  assert.deepEqual(K.accountState(source('a', {state: 'constructor'})), {label: 'constructor', tone: 'grey'}, 'own keys only');
  assert.equal(K.label({state: 'constructor'}), 'constructor');
  assert.match(V.row(task(3, 'constructor'), V.createUi(), ctx()), /<span class="badge grey">constructor<\/span>/);
  assert.equal(K.memberState({status: 'CREATED', task_state: 'toString'}).tone, 'grey');
});

test('a session past its 1-hour mark by the page clock is "Hết phiên" before the server answers; never the other way round', () => {
  const at = Date.parse('2026-10-08T10:50:00Z');
  const connected = source('a', {state: 'CONNECTED', authenticated_at: '2026-10-08T09:50:00Z', recheck_at: '2026-10-08T10:50:00Z'});
  assert.equal(K.accountState(connected, at - 1000).label, 'Đã kết nối');
  assert.deepEqual(K.accountState(connected, at), {label: 'Hết phiên', tone: 'amber'});
  assert.equal(K.accountState(connected).label, 'Đã kết nối', 'no clock given: the server state only');
  assert.equal(K.accountState({...connected, session_check: 'live'}, at + 1).label, 'Đã kết nối', 'a live check has no fixed mark');
  assert.equal(K.accountState({...connected, recheck_at: 'x'}, at + 1).label, 'Đã kết nối');
  assert.equal(K.accountState({...connected, state: 'NOT_CONNECTED'}, at + 1).label, 'Chưa đăng nhập');
  const later = V.accountsPanel(snapshot([], {accounts: {sources: [{...connected, last_login: {connected: true, code: 'CONNECTED',
    at: '2026-10-08T09:50:00Z'}}], problems: [], problem_text: '', error: null}}), V.createUi(), ctx({now: at + 60000}));
  assert.match(later, />Hết phiên</); assert.ok(!later.includes('Đã kết nối'), 'no "Đã kết nối" anywhere once the mark passed');
  assert.match(later, /Phiên hết hạn lúc/); assert.ok(!later.includes('Phiên dùng tới') && !later.includes('còn khoảng'));
  assert.match(later, /phiên đó hiện không dùng được/);
  assert.match(later, /data-op="login"[^>]*>Đăng nhập lại</);
  const server = V.accountsPanel(snapshot([], {accounts: {sources: [{...connected, state: 'NEEDS_LOGIN', error_code: 'SESSION_EXPIRED'}],
    problems: [], problem_text: '', error: null}}), V.createUi(), ctx({now: at + 60000}));
  assert.match(server, />Hết phiên</); assert.match(server, /Phiên hết hạn lúc/);
  // Review P3: once this page saw a session past its mark, the same session (source, sign-in time, mark) stays
  // "Hết phiên" when the page clock goes back; a new sign-in is another session; the server's state still decides.
  const seen = new Set();
  assert.equal(K.accountState(connected, at + 1, seen).label, 'Hết phiên');
  assert.equal(K.accountState(connected, at - 1, seen).label, 'Hết phiên', 'the clock set back: still expired');
  assert.equal(K.ttlPassed(connected, at - 1, seen), true);
  assert.equal(K.accountState(connected, at - 1).label, 'Đã kết nối', 'without the page memory: the clock alone');
  assert.equal(K.accountState({...connected, id: 'b'}, at - 1, seen).label, 'Đã kết nối', 'per source');
  const again = {...connected, authenticated_at: '2026-10-08T11:00:00Z', recheck_at: '2026-10-08T12:00:00Z'};
  assert.equal(K.accountState(again, at - 1, seen).label, 'Đã kết nối', 'a new sign-in starts again');
  assert.equal(K.accountState({...connected, state: 'NOT_CONNECTED'}, at - 1, seen).label, 'Chưa đăng nhập', 'the server decides');
  assert.equal(K.accountState({...connected, state: 'LOGGING_IN', login_running: true}, at - 1, seen).label, 'Đang đăng nhập');
  const ui = V.createUi(), panel = now => V.accountsPanel(snapshot([], {accounts: {sources: [connected], problems: [], problem_text: '',
    error: null}}), ui, ctx({now}));
  assert.match(panel(at + 60000), />Hết phiên</);
  const back = panel(at - 60000);
  assert.match(back, />Hết phiên</); assert.match(back, /Phiên hết hạn lúc/);
  assert.ok(!back.includes('Đã kết nối') && !back.includes('Phiên dùng tới'), 'the same page keeps it across renders');
  const renewed = V.accountsPanel(snapshot([], {accounts: {sources: [again], problems: [], problem_text: '', error: null}}), ui,
    ctx({now: at - 60000}));
  assert.match(renewed, />Đã kết nối</); assert.match(renewed, /Phiên dùng tới/);
});

test('account buttons: none on the phone, none for a source the server cannot sign in, Hủy only while signing in', () => {
  const PC = {device: 'pc'};
  const ids = (extra, c = PC) => K.accountActions(source('a', extra), c).map(a => a.id);
  assert.deepEqual(ids({}), ['login']);
  assert.equal(K.accountActions(source('a'), PC).find(a => a.id === 'login').label, 'Đăng nhập');
  assert.deepEqual(ids({state: 'CONNECTED'}), ['login', 'disconnect']);
  assert.equal(K.accountActions(source('a', {state: 'CONNECTED'}), PC)[0].label, 'Đăng nhập lại');
  assert.deepEqual(ids({state: 'NEEDS_LOGIN', error_code: 'SESSION_EXPIRED'}), ['login', 'disconnect']);
  assert.deepEqual(ids({state: 'LOGGING_IN', login_running: true}), ['cancel-login']);
  assert.deepEqual(ids({login_supported: false}), [], 'no reader or verifier: no useless window');
  assert.deepEqual(ids({state: 'CONNECTED', login_supported: false}), ['disconnect']);
  assert.deepEqual(ids({reader_supported: false}), [], 'no page reader: no sign-in window either');
  assert.deepEqual(ids({state: 'NEEDS_LOGIN', reader_supported: false}), ['disconnect']);
  assert.deepEqual(ids({state: 'UNAVAILABLE'}), []);
  assert.deepEqual(ids({state: 'CONNECTED'}, {remote: true, device: 'phone'}), [], 'phone: state only');
  assert.deepEqual(ids({state: 'CONNECTED'}, {remote: true, device: 'pc'}), [], 'a phone flag always wins');
  assert.deepEqual(ids({state: 'LOGGING_IN', login_running: true}, {remote: true, device: 'phone'}), [], 'phone: no Hủy đăng nhập either');
  for (const c of [{}, {device: null}, {device: 'PC'}, {device: true}, {modeKnown: true}, {remote: false}, null, undefined]) {
    for (const extra of [{state: 'CONNECTED'}, {state: 'LOGGING_IN', login_running: true}]) {
      assert.deepEqual(K.accountActions(source('a', extra), c), [], 'only a page confirmed as the PC: ' + JSON.stringify(c) + ' ' + extra.state);
    }
  }
  assert.deepEqual(ids({state: 'CONNECTED'}, {device: 'pc'}), ['login', 'disconnect']);
  for (const a of K.accountActions(source('a', {state: 'CONNECTED'}), {offline: true, device: 'pc'})) { assert.equal(a.enabled, false); assert.match(a.reason, /Mất kết nối/); }
});

test('task buttons: a series page offers Chọn tập; a split page only Xóa cả nhóm once the group is done; a cancelled group\'s episode never resumes', () => {
  const page = K.actions(task(18, 'NEEDS_CHOICE', {choice_kind: 'episodes'}), {});
  assert.equal(page[0].id, 'episodes'); assert.equal(page[0].primary, true); assert.equal(page[0].label, 'Chọn tập');
  assert.ok(!K.actions(task(5, 'NEEDS_CHOICE', {entries: [{index: 1}]}), {}).some(a => a.id === 'episodes'), 'the entries flow stays');
  const split = done => K.actions(task(20, 'EXPANDED'), {group: group(1, {finished: done})});
  assert.deepEqual(split(false).map(a => [a.id, a.label, a.enabled]), [['remove', 'Xóa cả nhóm', false]]);
  assert.match(split(false)[0].reason, /Hủy nhóm trước/);
  assert.equal(split(true)[0].enabled, true);
  assert.match(split(true)[0].confirm, /4 tập/); assert.match(split(true)[0].confirm, /đã vào input không bị đụng tới/);
  assert.equal(K.actions(task(20, 'EXPANDED'), {})[0].enabled, false, 'no group known: no remove');
  const cancelling = K.actions(task(20, 'EXPANDED'), {group: group(1, {state: 'CANCELLED', counts: counts({running: 1, cancelled: 3})})})[0];
  assert.equal(cancelling.enabled, false); assert.match(cancelling.reason, /Nhóm đang hủy/); assert.ok(!cancelling.reason.includes('bấm Hủy nhóm'));
  assert.equal(K.canRename(task(18, 'NEEDS_CHOICE', {choice_kind: 'episodes'})), false, 'the group takes the film title from the list');
  assert.equal(K.canRename(task(5, 'NEEDS_CHOICE', {entries: [{index: 1}]})), true, 'a playlist choice keeps its rename');
  const cancelled = group(2, {state: 'CANCELLED'});
  for (const s of ['STOPPED', 'INTERRUPTED', 'FAILED', 'CANCELLED', 'EXPIRED']) {
    const ids = K.actions(task(36, s, {group_id: 2}), {group: cancelled}).map(a => a.id);
    assert.ok(!ids.includes('resume') && !ids.includes('retry'), s);
  }
  assert.ok(K.actions(task(36, 'STOPPED', {group_id: 2}), {group: group(2)}).some(a => a.id === 'resume'), 'an active group resumes');
});

test('group buttons, badge and confirmations follow the counts; the scope and the kept files are named', () => {
  const ids = g => K.groupActions(g, {}).map(a => a.id);
  assert.deepEqual(ids(group(1)), ['stop', 'cancel']);
  assert.deepEqual(ids(group(1, {counts: counts({held: 2, failed: 1, completed: 1}), finished: false})), ['resume', 'retry', 'cancel']);
  assert.deepEqual(ids(group(1, {counts: counts({completed: 4}), finished: true})), ['remove']);
  assert.deepEqual(ids(group(1, {state: 'CANCELLED', counts: counts({completed: 1, cancelled: 3}), finished: true})), ['remove']);
  assert.deepEqual(K.groupActions(group(1, {state: 'CANCELLED', counts: counts({running: 1, cancelled: 3})}), {}).map(a => a.label), ['Hủy các tập còn lại']);
  const confirm = (id, g) => K.groupActions(g, {}).find(a => a.id === id).confirm;
  const g = group(1, {title: 'Phim <b>x</b>', counts: counts({completed: 2, running: 1, pending: 3, failed: 1})});
  assert.match(confirm('cancel', g), /5 tập chưa xong/); assert.match(confirm('cancel', g), /2 tập đã vào input được giữ nguyên/);
  assert.match(confirm('retry', g), /Thử lại 1 tập/); assert.match(confirm('retry', g), /tập đã vào input không tải lại/);
  assert.equal(K.groupState(g).label, 'Đang tải');
  assert.equal(K.groupState(group(1, {counts: counts({waiting_login: 2})})).label, 'Chờ đăng nhập');
  assert.equal(K.groupState(group(1, {state: 'CANCELLED', counts: counts({cancelled: 4})})).label, 'Đã hủy');
  assert.equal(K.groupState(group(1, {finished: true, counts: counts({completed: 3, failed: 1})})).label, 'Cần xử lý');
  assert.equal(K.groupState(group(1, {finished: true, counts: counts({completed: 4})})).label, 'Đã xong');
  assert.equal(K.groupState(group(1, {finished: true, counts: counts({completed: 3, expired: 1})})).label, 'Cần xử lý', 'a swept file can be retried');
  assert.equal(K.groupState(group(1, {finished: true, counts: counts({completed: 3, removed: 1})})).label, 'Xong một phần');
  assert.match(confirm('cancel', g), /đang chuyển vào input thì vẫn xong/);
  assert.equal(K.memberState({status: 'PENDING'}).label, 'Chờ chỗ trong danh sách');
  assert.equal(K.memberState({status: 'CREATED', task_state: 'WAITING_LOGIN'}).label, 'Chờ đăng nhập');
  assert.equal(K.memberState({status: 'CREATED', task_state: null, last_state: 'COMPLETED'}).label, 'Đã xóa khỏi danh sách (Đã vào input)');
  for (const a of K.groupActions(g, {offline: true})) assert.equal(a.enabled, false);
});

/* ----- view ----- */
test('accounts panel: old backend, nothing configured, a config error; never a fake source', () => {
  const html = accounts => V.accountsPanel(snapshot([], {accounts}), V.createUi(), ctx());
  assert.match(html(undefined), /chưa có phần tài khoản nguồn phim/);
  assert.match(html(null), /chưa có phần tài khoản nguồn phim/);
  const none = html({sources: [], problems: [], problem_text: '', error: null});
  assert.match(none, /Chưa có nguồn phim cần đăng nhập/); assert.ok(!none.includes('<select'));
  assert.match(none, /config\/download_accounts\.local\.json/); assert.match(none, /config\/download_accounts\.example\.json/);
  const broken = html({sources: [], problems: [{source: 'x', reason: 'host'}], problem_text: 'Nguồn "x": ' + BAIT, error: null});
  assert.match(broken, /Cấu hình nguồn có lỗi/); assert.ok(!broken.includes('<img'));
  assert.match(V.accountsPanel(null, V.createUi(), ctx()), /Đang tải/);
});

test('accounts panel: the selected source survives a render; states, TTL, warnings and last sign-in are escaped text; no secret field is shown', () => {
  const ui = V.createUi();
  const list = {sources: [source('phim-a', {state: 'CONNECTED', authenticated_at: '2026-10-08T09:50:00Z', recheck_at: '2026-10-08T10:50:00Z',
      vault_path: SECRET, cookie: SECRET}),
    source('phim-b', {label: 'Nguồn B ' + BAIT, state: 'NEEDS_LOGIN', error_code: 'SESSION_EXPIRED', message: 'Hết hạn ' + BAIT, waiting_tasks: 2,
      warnings: [{code: 'SESSION_SAVE_FAILED', message: 'Không lưu được ' + BAIT, at: '2026-10-08T08:00:00Z'}],
      last_login: {code: 'LOGIN_WINDOW_CLOSED', message: 'Cửa sổ đã đóng ' + BAIT, connected: false, profile_left: true, at: '2026-10-08T08:00:00Z'}}),
    source('phim-c', {login_supported: false, reader_supported: false}),
    source('phim-d', {state: 'CHECK_FAILED', session_check: 'live', checked_at: '2026-10-08T09:59:00Z', error_code: 'CHECK_UNREACHABLE',
      message: 'Không kiểm tra được kết nối (lỗi mạng).'})], problems: [], problem_text: '', error: null};
  const render = () => V.accountsPanel(snapshot([], {accounts: list}), ui, ctx());
  let html = render();
  assert.match(html, /<option value="phim-a" selected>/); assert.match(html, />Đã kết nối</); assert.match(html, /còn khoảng 50 phút/);
  assert.match(html, /tối đa 1 giờ/); assert.match(html, /chỉ để tham khảo/);
  assert.ok(!html.includes('SECRET') && !html.includes('vault'), 'no session, vault path or cookie on the page');
  ui.account = 'phim-b';
  html = render();
  assert.match(html, /<option value="phim-b" selected>/);
  assert.match(html, />Hết phiên</); assert.match(html, /2 lượt chờ đăng nhập/);
  assert.match(html, /data-op="login"[^>]*>Đăng nhập lại</); assert.match(html, /data-op="disconnect"/);
  assert.match(html, /chưa dọn được/);
  assert.ok(!html.includes('<img'), 'markup from the source is text');
  assert.ok(html.includes('&lt;img src=x onerror=&quot;window.__x=1&quot;&gt;'));
  assert.equal(render(), html, 'same data, same HTML (no redraw on the poll)');
  ui.account = 'phim-c';
  html = render();
  assert.match(html, />Chưa hỗ trợ</); assert.ok(!html.includes('data-action="dl-account"'), 'no useless window');
  assert.match(html, /chưa biết cách xác nhận đăng nhập/); assert.match(html, /"Chưa hỗ trợ", kể cả khi đã đăng nhập/);
  ui.account = 'phim-d';
  html = render();
  assert.match(html, /không có nghĩa là hết phiên/); assert.match(html, /Kiểm tra gần nhất/); assert.ok(!html.includes('tối đa 1 giờ'));
  const phone = V.accountsPanel(snapshot([], {accounts: list}), ui, ctx({remote: true}));
  assert.ok(!phone.includes('data-action="dl-account"')); assert.match(phone, /chỉ làm trên PC/);
  ui.account = 'gone';
  assert.match(render(), /<option value="phim-a" selected>/, 'an unknown choice falls back to the first source');
});

test('the form no longer says "Không dùng cookie hay đăng nhập"; groups sit between the tools and the rows', () => {
  const form = V.form(snapshot([]), V.createUi(), ctx());
  assert.ok(!form.includes('Không dùng cookie hay đăng nhập'));
  assert.match(form, /Tài khoản nguồn phim/);
  const list = V.list(snapshot([task(1, 'QUEUED')], {groups: [group(1)]}), V.createUi(), ctx());
  const at = id => list.indexOf('id="' + id + '"');
  assert.ok(at('dl-tools') < at('dl-groups') && at('dl-groups') < at('dl-items'));
  assert.equal(V.list(snapshot([]), V.createUi(), ctx()).includes('Nhóm tập ('), false, 'no groups, no section');
  const page = V.page(snapshot([]), null, V.createUi(), ctx());
  assert.ok(page.indexOf('id="dl-accounts-root"') > page.indexOf('download-form'), 'the panel sits under the form');
});

test('group card: a percent only when the server gives one; chips, the 100-task note and member rows as escaped text', () => {
  const ui = V.createUi();
  const unsized = V.groupCard(group(1, {counts: counts({completed: 1, running: 1, pending: 3})}), ui, ctx());
  assert.ok(!unsized.includes('role="progressbar"') && !unsized.includes('aria-valuenow') && !/\d+%/.test(unsized));
  assert.match(unsized, /3 chờ chỗ trong danh sách/); assert.match(unsized, /tối đa 100/);
  assert.match(unsized, /Đã xong 1\/4 tập/);
  const sized = V.groupCard(group(1, {percent: 42}), ui, ctx());
  assert.match(sized, /aria-valuenow="42"/); assert.match(sized, /<strong>42%<\/strong>/);
  assert.ok(!V.groupCard(group(1, {percent: '42'}), ui, ctx()).includes('aria-valuenow'), 'a string is not a percent');
  const evil = V.groupCard(group(1, {title: BAIT, source_label: BAIT, note: BAIT, complete: false, reasons: [BAIT]}), ui, ctx());
  assert.ok(!evil.includes('<img')); assert.match(evil, /chưa đầy đủ/);
  ui.groupsOpen.add(1);
  ui.members.set(1, {members: [
    {id: 102, ordinal: 2, code: 'S01E02', episode_label: BAIT, variant_label: 'Phụ đề', status: 'CREATED', task_id: 31, task_state: 'FAILED',
      error_message: 'Lỗi ' + BAIT, downloaded_bytes: 0, total_bytes: null},
    {id: 101, ordinal: 1, code: 'S01E01', episode_label: 'Tập 1', status: 'PENDING', task_id: null, task_state: null}]});
  const open = V.groupCard(group(1), ui, ctx());
  assert.ok(open.indexOf('dl-member-102') < open.indexOf('dl-member-101'), 'the server order, never sorted here');
  assert.ok(!open.includes('<img')); assert.match(open, /details[^>]* open/); assert.match(open, /Chờ chỗ trong danh sách/);
  assert.match(open, /data-action="dl-group-op" data-group="1" data-op="stop"/);
  ui.members.set(1, {members: [], error: 'Không tải được danh sách tập: HTTP 503'});
  assert.match(V.groupCard(group(1), ui, ctx()), /HTTP 503/);
});

test('rows: WAITING_LOGIN points to the panel on the PC and to the PC on the phone; OTHER_ACCOUNT explains; an episode shows its place', () => {
  const data = snapshot([], {accounts: {sources: [source('phim-b', {label: 'Nguồn B ' + BAIT})], problems: [], problem_text: '', error: null},
    groups: [group(1, {title: 'Phim mẫu'})]});
  const waiting = task(24, 'WAITING_LOGIN', {login_source: 'phim-b', login_reason: 'SESSION_EXPIRED', error_message: 'Chờ đăng nhập Nguồn B'});
  const pc = V.row(waiting, V.createUi(), ctx({data}));
  assert.match(pc, /data-action="dl-account-show" data-source="phim-b"/);
  assert.ok(!pc.includes('data-action="dl-account"'), 'the row only selects the source: Đăng nhập is pressed in the panel');
  assert.ok(!pc.includes('<img'));
  const phone = V.row(waiting, V.createUi(), ctx({data, remote: true}));
  assert.ok(!phone.includes('dl-account-show')); assert.match(phone, /trên PC/);
  const other = V.row({...waiting, login_reason: 'OTHER_ACCOUNT'}, V.createUi(), ctx({data}));
  assert.match(other, /tài khoản Windows/); assert.ok(!other.includes('dl-account-show'));
  // planned_name as download_groups.names_by_task gives it: the ordinal has at least 3 digits (ordinal_width)
  const child = task(31, 'DOWNLOADING', {group_id: 1, group: {group_id: 1, ordinal: 2, total: 6, code: 'S01E02', planned_name: '002 - Phim mẫu - S01E02'}});
  const row = V.row(child, V.createUi(), ctx({data}));
  assert.match(row, /Nhóm N1 · tập 2\/6 · S01E02 · Tên file dự kiến: 002 - Phim mẫu - S01E02/);
  assert.match(row, /data-action="dl-group-show" data-group="1"/);
  assert.match(row, /data-rename="31" value="Phim mẫu"/, 'rename edits the film part only');
  assert.match(row, /aria-label="Tên phim trong tên file của lượt 31"/);
  const waitingChild = task(32, 'WAITING_LOGIN', {group_id: 1, login_source: 'phim-b', login_reason: 'SESSION_EXPIRED',
    group: {group_id: 1, ordinal: 3, total: 6, code: 'S01E03', planned_name: '003 - Phim mẫu - S01E03'}});
  const childPc = V.row(waitingChild, V.createUi(), ctx({data}));
  assert.match(childPc, /Nhóm N1 · tập 3\/6 · S01E03/); assert.match(childPc, /data-action="dl-account-show" data-source="phim-b"/);
  const childPhone = V.row(waitingChild, V.createUi(), ctx({data, remote: true}));
  assert.match(childPhone, /Nhóm N1 · tập 3\/6/); assert.match(childPhone, /trên PC/); assert.ok(!childPhone.includes('dl-account-show'));
  const childOther = V.row({...waitingChild, login_reason: 'OTHER_ACCOUNT'}, V.createUi(), ctx({data}));
  assert.match(childOther, /tài khoản Windows/); assert.ok(!childOther.includes('dl-account-show'));
  for (const unknown of [ctx({data, device: null}), ctx({data, device: null, deviceChecking: true})]) { // the mode is not known yet
    for (const [name, html] of [['row', V.row(waiting, V.createUi(), unknown)], ['episode', V.row(waitingChild, V.createUi(), unknown)]]) {
      assert.ok(!html.includes('dl-account-show'), name + ': no sign-in button while the mode is unknown');
      assert.match(html, /Lượt này chờ phiên đăng nhập của Nguồn B[^<]*: đăng nhập trên PC \(trang Tải video → Tài khoản nguồn phim\)/, name);
      assert.ok(!html.includes('<img'), name);
    }
  }
  assert.match(V.row(waitingChild, V.createUi(), ctx({data, device: null})), /Nhóm N1 · tập 3\/6 · S01E03/);
  const page = V.row(task(18, 'NEEDS_CHOICE', {choice_kind: 'episodes', entries: [{index: 1, title: 'x'}],
    episodes: {title: BAIT, episode_count: 17, complete: false, message: 'giới hạn 25 trang', has_draft: true}}), V.createUi(), ctx({data}));
  assert.match(page, /data-op="episodes"[^>]*>Chọn tập</); assert.ok(!page.includes('dl-choice'), 'no entries fieldset for a series page');
  assert.match(page, /17 tập · danh sách chưa đầy đủ/); assert.match(page, /đã lưu lựa chọn nháp/); assert.ok(!page.includes('<img'));
  assert.ok(!page.includes('data-rename'), 'a series page has no rename box (the group is named from the list)');
  const split = V.row(task(20, 'EXPANDED'), V.createUi(), ctx({data}));
  assert.match(split, /Đã tách thành nhóm N1 \(4 tập\)/); assert.match(split, /data-op="remove" disabled[^>]*>Xóa cả nhóm</);
  assert.ok(!/data-op="(retry|resume|stop|cancel)"/.test(split) && !split.includes('data-rename'), 'no Retry, rename, stop or cancel');
});

/* ----- episode dialog: pure part ----- */
const episode = (key, label, variants) => ({key, number: null, label, special: false, order: 0, variants});
const v = (id, kind, label) => ({id, kind, label, quality: '', audio: '', size: null});
function answer(extra = {}, listing = {}) {
  return {task_id: 18, fingerprint: 'fp-1', draft: null, revision: 0, plan: null, plan_error: null, max_episodes: 500, ...extra,
    listing: {title: 'Phim ' + BAIT, complete: true, message: null, variant_kinds: [{kind: 'k1', label: 'Lồng tiếng ' + BAIT, episodes: 3},
      {kind: 'k2', label: 'Phụ đề', episodes: 2}],
    groups: [{key: 's1', number: 1, label: 'Mùa 1', episodes: [episode('e2', 'Tập 2', [v('e2a', 'k1', 'LT'), v('e2b', 'k2', 'PĐ')]),
      episode('e10', 'Tập 10 ' + BAIT, [v('e10a', 'k1', 'LT')]), episode('e1', 'Tập 1', [v('e1a', 'k1', 'LT'), v('e1b', 'k2', 'PĐ')])]},
      {key: 'sp', number: null, label: '', special: true, episodes: [episode('x1', '', [v('x1a', 'k3', BAIT)])]}], ...listing}};
}

test('dialog model: the server order (Tập 10 after Tập 2), seasons as sent, fallback labels, variant kinds', () => {
  const m = E.model(answer());
  assert.deepEqual(m.episodes.map(e => e.key), ['e2', 'e10', 'e1', 'x1']);
  assert.deepEqual(m.seasons.map(s => s.label), ['Mùa 1', 'Tập đặc biệt']);
  assert.equal(m.episodes[3].label, 'Tập #?');
  assert.equal(m.single, false); assert.equal(m.count, 4); assert.deepEqual(m.kinds.map(k => k.kind), ['k1', 'k2']);
  assert.equal(E.model({listing: {groups: [{episodes: [episode('a', 'A', [v('a1', 'k', 'x')])]}]}}).single, true);
  assert.deepEqual(E.model({}).episodes, [], 'an empty or broken answer is an empty list');
});

test('dialog model: a variant label that already names its quality and audio shows each part once; a label\'s own words are kept', () => {
  const MB = 1048576;
  const q = (id, label, quality, audio, size) => ({id, kind: 'k-' + id, label, quality, audio, size});
  const listing = {variant_kinds: [], groups: [{key: 's1', number: 1, label: 'Mùa 1', episodes: [
    episode('a', 'Tập 1', [q('a1', '1080p · Vietsub', '1080p', 'Vietsub', MB)]),
    episode('b', 'Tập 2', [q('b1', '1080p · Vietsub', '1080p', 'Vietsub', 3 * MB), q('b2', '720p · Thuyết minh', '720p', 'Thuyết minh', MB)]),
    episode('c', 'Tập 3', [q('c1', 'Bản đẹp', '1080p', 'Vietsub', MB), q('c2', 'Bản đẹp · 1080p', '1080p', 'Vietsub', null),
      q('c3', '', '720p', '', null), q('c4', 'Bản 1080p', '1080p', '', null), q('c5', BAIT + ' · 720p', '720p', 'Vietsub', null)])]}]};
  const m = E.model(answer({}, listing));
  assert.deepEqual(m.episodes.map(e => e.variants.map(x => x.label)), [
    ['1080p · Vietsub · 1 MB'],
    ['1080p · Vietsub · 3 MB', '720p · Thuyết minh · 1 MB'],
    ['Bản đẹp · 1080p · Vietsub · 1 MB', 'Bản đẹp · 1080p · Vietsub', 'Bản · 720p', 'Bản 1080p · 1080p', BAIT + ' · 720p · Vietsub']],
    'the backend names a variant without its own label "quality · audio" (download_account_listing); a label is never cut inside');
  const html = E.html({...E.emptyState(), loading: false, model: m, fingerprint: 'fp-1', revision: 0,
    sel: {...E.emptySelection(m), mode: 'pick', picked: new Set([0, 1, 2]), how: 'each', each: new Map([[1, 0]])}}, false);
  assert.match(html, /<small>1080p · Vietsub · 1 MB<\/small>/, 'a one-variant row');
  assert.match(html, /<option value="0" selected>1080p · Vietsub · 3 MB<\/option><option value="1">720p · Thuyết minh · 1 MB<\/option>/);
  assert.ok(!html.includes('<img') && !html.includes('Vietsub · 1080p · Vietsub'), 'escaped, and no part twice');
});

test('dialog selection: a draft round-trips; unknown ids are dropped; a missing variant is never guessed', () => {
  const m = E.model(answer());
  const all = E.fromDraft({mode: 'all', variant_kind: 'k2'}, m);
  assert.deepEqual(E.selection(all, m), {mode: 'all', variant_kind: 'k2'});
  const pick = E.fromDraft({mode: 'pick', episodes: ['e1', 'gone', 'e10'], variants: {e1: 'e1b', e10: 'e10a', gone: 'x'}}, m);
  assert.deepEqual([...pick.picked], [2, 1]);
  assert.deepEqual(E.selection(pick, m), {mode: 'pick', episodes: ['e10', 'e1'], variants: {e10: 'e10a', e1: 'e1b'}}, 'the server order');
  pick.picked.add(0);
  assert.deepEqual(E.selection(pick, m).variants, {e10: 'e10a', e1: 'e1b'}, 'e2 has two variants and none chosen: left out, the server reports it missing');
  assert.equal(E.selection(E.emptySelection(m), m), null, 'nothing before a mode');
  assert.equal(E.fromDraft({mode: 'all', variant_kind: 'nope'}, m).how, null);
});

test('dialog blocker: what keeps "Tải N tập" off, in order', () => {
  const m = E.model(answer({}, {complete: false, message: 'giới hạn'})), plan = {count: 3, complete: false, missing: [], ambiguous: [], too_large: false, max: 500};
  const st = extra => ({...E.emptyState(), loading: false, model: m, fingerprint: 'fp-1', revision: 1, sel: {...E.emptySelection(m), mode: 'all', how: 'kind', kind: 0},
    plan, ...extra});
  assert.match(E.blocker(st(), true), /Mất kết nối/);
  assert.match(E.blocker(st({closedGroup: {group_id: 5}})), /không còn chờ/);
  assert.match(E.blocker(st({loading: true})), /Đang tải/);
  assert.match(E.blocker(st({remote: {fingerprint: 'fp-2', revision: 1}})), /Danh sách tập vừa đổi/);
  assert.match(E.blocker(st({sel: E.emptySelection(m)})), /Chọn "Tải tất cả/);
  assert.match(E.blocker(st({dirty: true})), /Đang lưu/);
  assert.match(E.blocker(st({planError: {error: 'Chọn một bản ' + BAIT}})), /Chọn một bản/);
  assert.match(E.blocker(st({plan: {...plan, too_large: true, count: 501}})), /tối đa 500 tập; lựa chọn này có 501/);
  assert.match(E.blocker(st({plan: {...plan, missing: [{episode: 'e2'}]}})), /không có bản đã chọn/);
  assert.match(E.blocker(st({plan: {...plan, ambiguous: [{episode: 'e2'}]}})), /hai file cùng bản/);
  assert.match(E.blocker(st()), /ô xác nhận/);
  assert.equal(E.blocker(st({scopeOk: true})), '');
  assert.equal(E.blocker(st({scopeOk: true, plan: {...plan, complete: true}, confirming: true})), 'Đang gửi…');
});

test('dialog keys: the same request keeps its key (a resend replays); another selection, list or skip_existing is another request', () => {
  const body = {selection: {mode: 'all', variant_kind: 'k1'}, fingerprint: 'fp-1'};
  const same = E.requestText(18, {...body, confirm_scope: true, idempotency_key: 'x'});
  assert.equal(E.requestText(18, body), same, 'confirm_scope and the key are not part of the request (download_groups.request_hash)');
  assert.notEqual(E.requestText(18, {...body, skip_existing: true}), same);
  assert.notEqual(E.requestText(18, {...body, fingerprint: 'fp-2'}), same);
  assert.notEqual(E.requestText(18, {...body, selection: {mode: 'all', variant_kind: 'k2'}}), same);
  assert.notEqual(E.requestText(19, body), same);
  const key = E.newKey(n => Uint8Array.from({length: n}, (_, i) => i * 37));
  assert.match(key, E.KEY_PATTERN); assert.equal(key.length, 21);
});

test('dialog HTML: escaped labels everywhere, the server\'s label on the button, the scope box, the existing list, the group link', () => {
  const m = E.model(answer({}, {complete: false, message: 'đọc tới 25 trang ' + BAIT}));
  const st = {...E.emptyState(), loading: false, model: m, fingerprint: 'fp-1', revision: 2, source: BAIT,
    sel: {...E.emptySelection(m), mode: 'pick', picked: new Set([0, 1]), how: 'each', each: new Map([[0, 1]])},
    plan: {count: 2, complete: false, confirm_label: 'Tải 2 tập đã thấy', note: 'Chỉ các tập đã thấy ' + BAIT, missing: [{episode: 'e10', label: BAIT}],
      ambiguous: [], too_large: false, max: 500},
    existing: {existing: [{episode: 'e2', label: BAIT, task_id: 37}], existing_count: 1}, error: 'Lỗi ' + BAIT, errorList: [BAIT]};
  const html = E.html(st, false);
  assert.ok(!html.includes('<img'), 'no raw markup from the source');
  assert.match(html, /Tải 2 tập đã thấy<\/button>/); assert.match(html, /id="dl-ep-confirm"[^>]* disabled/);
  assert.match(html, /id="dl-ep-scope"/); assert.match(html, /chỉ tải các tập đã thấy/);
  assert.match(html, /Tải tất cả 4 tập đã thấy/); assert.match(html, /đây không phải toàn bộ phim/);
  assert.match(html, /Thiếu bản đã chọn/);
  assert.match(html, /data-action="dl-ep-rest" disabled aria-describedby="dl-ep-why">Tải các tập còn lại/, 'off while "Tải N tập" is (a missing variant)');
  assert.match(html, /<option value="1" selected>PĐ<\/option>/);
  assert.equal((html.match(/data-ep-pick="/g) || []).length, 4);
  assert.match(html, /data-ep-pick="0" checked/); assert.ok(!/data-ep-pick="2" checked/.test(html));
  const closed = E.html({...st, closedGroup: {group_id: 5}}, false);
  assert.match(closed, /data-action="dl-ep-group" data-group="5">Xem nhóm tập #5/); assert.ok(!closed.includes('dl-ep-confirm'));
  const broken = E.html({...st, existing: {existing: [null, 'x', {episode: 'e1'}], existing_count: 3}, plan: {...st.plan, missing: [null], ambiguous: null}}, false);
  assert.match(broken, /<li>e1 <small><\/small><\/li>/, 'malformed entries are skipped, never a crash');
  const gone = E.html({...st, closedGroup: {group_id: null, state: 'CANCELLED'}}, false);
  assert.match(gone, /không còn chờ chọn tập \(đã hủy\)/); assert.ok(!gone.includes('dl-ep-group'));
  const big = {groups: [{key: '', number: null, label: '', episodes: Array.from({length: 500}, (_, i) => episode('b' + i, 'Tập ' + (i + 1), [v('b' + i + 'v', 'k', '720p')]))}],
    variant_kinds: [{kind: 'k', label: '720p', episodes: 500}], complete: false};
  const m500 = E.model(answer({}, big)), started = Date.now();
  const html500 = E.html({...E.emptyState(), loading: false, model: m500, fingerprint: 'fp-1', revision: 0,
    sel: {...E.emptySelection(m500), mode: 'pick', picked: new Set(m500.episodes.map(e => e.index))}}, false);
  assert.ok(Date.now() - started < 500, '500 episodes render fast');
  assert.equal((html500.match(/data-ep-pick="\d+" checked/g) || []).length, 500);
  assert.match(html500, /Mỗi tập có đúng một file/);
});

/* ----- episode dialog: controller on a fake document ----- */
function fakeDocument() {
  const listeners = {}, root = {html: ''}, closeButton = {focused: 0, focus() { this.focused++; }};
  const opener = {isConnected: true, focused: 0, focus() { this.focused++; }};
  const dialog = {open: false, attrs: {}, setAttribute(k, value) { this.attrs[k] = value; }, set innerHTML(_) {},
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    querySelector: selector => selector === '#dl-ep-root' ? root : selector.includes('dl-ep-close') ? closeButton : null,
    contains: el => !!el.inDialog, showModal() { this.open = true; }, close() { this.open = false; (listeners.close || []).forEach(fn => fn()); }};
  global.document = {createElement: () => dialog, body: {appendChild() {}}, activeElement: opener};
  return {dialog, root, opener};
}
function dialogFor(script, extra = {}) {
  const doc = fakeDocument(), calls = [], toasts = [], shown = [];
  let pageTask = {id: 18, state: 'NEEDS_CHOICE', episodes: {fingerprint: 'fp-1', revision: 0}}, seed = 1;
  const call = kind => async (id, body) => {
    calls.push({kind, id, body: body === undefined ? undefined : JSON.parse(JSON.stringify(body))});
    return script[kind](body, calls);
  };
  const ep = E.create({store: {loadEpisodes: call('get'), episodeDraft: call('draft'), episodeConfirm: call('confirm')},
    dom: {patch: (el, html) => { el.html = html; }}, toast: (text, error) => toasts.push([text, !!error]), offline: () => false,
    task: () => pageTask, groupOfPage: () => 5, sourceLabel: () => 'Nguồn A', showGroup: id => shown.push(id),
    random: n => Uint8Array.from({length: n}, () => seed++), ...extra});
  return {ep, doc, calls, toasts, shown, kinds: k => calls.filter(c => c.kind === k), setTask: t => { pageTask = t; }};
}
const fail = (status, code, detail = {}, message = 'refused') => new A.AdapterError(status, message, {code, detail});
const single = () => answer({}, {variant_kinds: [{kind: 'k', label: '720p', episodes: 3}], groups: [{key: '', number: 1, label: 'Mùa 1',
  episodes: ['a', 'b', 'c'].map(k => episode(k, 'Tập ' + k, [v(k + 'v', 'k', '720p')]))}]});
const planned = n => ({count: n, complete: true, confirm_label: 'Tải ' + n + ' tập', note: null, missing: [], ambiguous: [], too_large: false, max: 500});
const pickEl = index => ({inDialog: true, dataset: {epPick: String(index)}, checked: true});
const modeEl = value => ({inDialog: true, name: 'dl-ep-mode', value, dataset: {}});

test('dialog: opening only reads; a burst of changes is one draft; drafts never overlap and carry the last revision', async () => {
  let inflight = 0, most = 0, revision = 0;
  const d = dialogFor({get: async () => single(), draft: async body => {
    inflight++; most = Math.max(most, inflight); await sleep(450); inflight--; revision++;
    return {revision, draft: body.selection, plan: planned(body.selection.episodes ? body.selection.episodes.length : 3), plan_error: null};
  }});
  d.ep.open(18);
  await tick(); await tick();
  assert.equal(d.kinds('get').length, 1); assert.equal(d.calls.length, 1, 'no POST on open');
  assert.equal(d.doc.dialog.open, true);
  d.ep.change(modeEl('pick')); d.ep.change(pickEl(0)); d.ep.change(pickEl(2));
  await sleep(E.SAVE_DELAY_MS + 60);
  assert.equal(d.kinds('draft').length, 1, 'one draft for the burst');
  assert.deepEqual(d.kinds('draft')[0].body, {selection: {mode: 'pick', episodes: ['a', 'c']}, fingerprint: 'fp-1', revision: 0});
  d.ep.change(pickEl(1));
  await sleep(E.SAVE_DELAY_MS + 60);
  assert.equal(d.kinds('draft').length, 1, 'the next draft waits for the first answer');
  await sleep(700);
  assert.equal(d.kinds('draft').length, 2); assert.equal(most, 1, 'never two drafts in flight');
  assert.equal(d.kinds('draft')[1].body.revision, 1, 'sent with the revision of the first answer');
  assert.deepEqual(d.kinds('draft')[1].body.selection.episodes, ['a', 'b', 'c']);
  await sleep(500);
  assert.match(d.doc.root.html, /Tải 3 tập<\/button>/);
  assert.equal(d.kinds('confirm').length, 0, 'never confirmed by itself');
});

test('dialog: STALE_DRAFT reloads the stored choice with a notice; another device\'s change is shown, never overwritten', async () => {
  let gets = 0;
  const d = dialogFor({get: async () => { gets++; return gets === 1 ? single() : {...single(), revision: 4, draft: {mode: 'pick', episodes: ['b']}, plan: planned(1)}; },
    draft: async () => { throw fail(409, 'STALE_DRAFT', {revision: 4}); }});
  d.ep.open(18);
  await tick(); await tick();
  d.ep.change(modeEl('all'));
  await sleep(E.SAVE_DELAY_MS + 80);
  assert.equal(gets, 2);
  const st = d.ep.state();
  assert.match(st.notice, /thiết bị khác/); assert.equal(st.revision, 4); assert.deepEqual([...st.sel.picked], [1]); assert.equal(st.sel.mode, 'pick');
  assert.equal(d.kinds('draft').length, 1, 'no second draft over the other device');
  d.setTask({id: 18, state: 'NEEDS_CHOICE', episodes: {fingerprint: 'fp-1', revision: 6}});
  d.ep.sync();
  assert.match(d.doc.root.html, /Mở lựa chọn đã lưu/); assert.deepEqual([...d.ep.state().sel.picked], [1], 'the poll never rewrites the choice');
  d.setTask({id: 18, state: 'NEEDS_CHOICE', episodes: {fingerprint: 'fp-9', revision: 6}});
  d.ep.sync();
  assert.match(d.doc.root.html, /Danh sách tập vừa đổi/); assert.match(d.doc.root.html, /id="dl-ep-confirm"[^>]* disabled/);
  d.setTask({id: 18, state: 'EXPANDED'});
  d.ep.sync();
  assert.match(d.doc.root.html, /Xem nhóm tập #5/);
});

test('dialog: a double press sends one request; a lost answer is resent with the same key and replays; the group is shown', async () => {
  let confirms = 0;
  const d = dialogFor({get: async () => ({...single(), draft: {mode: 'all'}, revision: 1, plan: planned(3)}),
    confirm: async () => { confirms++; await sleep(30); if (confirms === 1) throw fail(0, null, {}, 'lost'); return {group: {id: 9, total: 3}, replay: true, existing: []}; }});
  d.ep.open(18);
  await tick(); await tick();
  d.ep.click({}, 'dl-ep-confirm'); d.ep.click({}, 'dl-ep-confirm');
  await sleep(60);
  assert.equal(d.kinds('confirm').length, 1, 'one request for a double press');
  assert.match(d.ep.state().error, /Mất kết nối khi gửi/);
  const first = d.kinds('confirm')[0].body;
  assert.match(first.idempotency_key, E.KEY_PATTERN); assert.equal(first.skip_existing, undefined);
  d.ep.click({}, 'dl-ep-confirm');
  await sleep(60);
  assert.equal(d.kinds('confirm')[1].body.idempotency_key, first.idempotency_key, 'the resend replays, never a second group');
  assert.equal(d.doc.dialog.open, false); assert.deepEqual(d.shown, [9]);
  assert.match(d.toasts.at(-1)[0], /đã được tạo trước đó: 3 tập/);
  assert.equal(d.doc.opener.focused, 0, 'focus goes to the group card, not back to the old button');
});

test('dialog: ITEMS_EXIST lists them and "Tải các tập còn lại" is another request; NOT_WAITING links to the group; a conflict drops the key', async () => {
  let round = 0;
  const d = dialogFor({get: async () => ({...single(), draft: {mode: 'all'}, revision: 1, plan: planned(3)}), confirm: async () => {
    round++;
    if (round === 1) throw fail(409, 'ITEMS_EXIST', {existing: [{episode: 'b', label: 'Tập b ' + BAIT, task_id: 37}], existing_count: 1});
    if (round === 2) throw fail(409, 'IDEMPOTENCY_CONFLICT');
    if (round === 3) throw fail(409, 'NOT_WAITING', {group_id: 7, state: 'EXPANDED'});
    return {group: {id: 7}, replay: false, existing: []};
  }});
  d.ep.open(18);
  await tick(); await tick();
  d.ep.click({}, 'dl-ep-confirm');
  await sleep(20);
  assert.match(d.doc.root.html, /1 tập đã có trong danh sách tải/); assert.match(d.doc.root.html, /Tải các tập còn lại/);
  assert.ok(!d.doc.root.html.includes('<img'));
  d.ep.click({}, 'dl-ep-rest');
  await sleep(20);
  const [plain, rest] = d.kinds('confirm').map(c => c.body);
  assert.equal(rest.skip_existing, true); assert.notEqual(rest.idempotency_key, plain.idempotency_key);
  assert.match(d.ep.state().error, /refused/);
  d.ep.click({}, 'dl-ep-rest');
  await sleep(20);
  assert.notEqual(d.kinds('confirm')[2].body.idempotency_key, rest.idempotency_key, 'a conflicting key is not used again');
  assert.match(d.doc.root.html, /data-action="dl-ep-group" data-group="7"/);
  d.ep.click({dataset: {group: '7'}}, 'dl-ep-group');
  assert.deepEqual(d.shown, [7]); assert.equal(d.doc.dialog.open, false);
});

test('dialog: without crypto.getRandomValues nothing is sent and the page says why', async () => {
  const d = dialogFor({get: async () => ({...single(), draft: {mode: 'all'}, revision: 1, plan: planned(3)}), confirm: async () => ({group: {id: 1}})},
    {random: () => { throw new TypeError('crypto missing'); }});
  d.ep.open(18);
  await tick(); await tick();
  d.ep.click({}, 'dl-ep-confirm');
  await tick();
  assert.equal(d.kinds('confirm').length, 0);
  assert.match(d.ep.state().error, /không tạo được khóa yêu cầu/); assert.equal(d.ep.state().confirming, false);
});

test('dialog: closing saves a pending choice and returns focus; it never confirms', async () => {
  const d = dialogFor({get: async () => single(), draft: async body => ({revision: 1, draft: body.selection, plan: planned(3), plan_error: null})});
  d.ep.open(18);
  await tick(); await tick();
  d.ep.change(modeEl('all'));
  d.ep.click({}, 'dl-ep-close');
  await tick();
  assert.equal(d.kinds('draft').length, 1, 'the choice is saved now for a refresh or another device');
  assert.equal(d.doc.opener.focused, 1);
  assert.equal(d.kinds('confirm').length, 0);
});

test('accounts panel: no page reader is "Chưa hỗ trợ" without Đăng nhập; an old success under another state is not current', () => {
  const html = (s, c = {}) => V.accountsPanel(snapshot([], {accounts: {sources: [s], problems: [], problem_text: '', error: null}}), V.createUi(), ctx(c));
  const noReader = html(source('a', {reader_supported: false}), {device: 'pc'});
  assert.match(noReader, />Chưa hỗ trợ</); assert.ok(!noReader.includes('data-op="login"'));
  const at = '2026-10-08T09:00:00Z';
  assert.match(html(source('a', {state: 'NEEDS_LOGIN', error_code: 'SESSION_EXPIRED', last_login: {connected: true, at}})), /phiên đó hiện không dùng được/);
  assert.ok(!html(source('a', {state: 'CONNECTED', last_login: {connected: true, at}})).includes('không dùng được'));
  for (const state of ['LOGGING_IN', 'CHECK_FAILED']) {
    assert.ok(!html(source('a', {state, last_login: {connected: true, at}})).includes('không dùng được'), state + ': that session may still work');
  }
  assert.match(html(source('a', {state: 'NOT_CONNECTED', last_login: {connected: true, at}})), /phiên đó hiện không dùng được/, 'disconnected');
});

test('dialog HTML: the existing list names how many it did not show; when every chosen episode exists the rest is not offered', () => {
  const m = E.model(answer());
  const st = {...E.emptyState(), loading: false, model: m, fingerprint: 'fp-1', revision: 1, sel: {...E.emptySelection(m), mode: 'all'},
    plan: {count: 4, complete: true, confirm_label: 'Tải 4 tập', note: null, missing: [], ambiguous: [], too_large: false, max: 500}};
  const many = E.html({...st, existing: {existing: Array.from({length: 50}, (_, i) => ({episode: 'e' + i, task_id: i + 1})), existing_count: 75}}, false);
  assert.match(many, /75 tập đã có trong danh sách tải/); assert.match(many, /… và 25 tập khác/); assert.match(many, /data-action="dl-ep-rest"/);
  const none = E.html({...st, existing: {existing: [{episode: 'e1', task_id: 3}], existing_count: 4, none: true}}, false);
  assert.ok(!none.includes('data-action="dl-ep-rest"')); assert.match(none, /không còn tập nào để tải thêm/); assert.match(none, /Để tôi chọn lại/);
});

test('dialog: reopening while a draft is sent keeps the newest choice: the change made meanwhile goes next, the list is read after it', async () => {
  let stored = null, revision = 0;
  const order = [];
  const d = dialogFor({get: async () => { order.push('get'); return {...single(), draft: stored, revision}; },
    draft: async body => {
      order.push('draft'); await sleep(200); revision++; stored = body.selection;
      return {revision, draft: stored, plan: planned(body.selection.episodes ? body.selection.episodes.length : 3), plan_error: null};
    }});
  d.ep.open(18);
  await tick(); await tick();
  d.ep.change(modeEl('all'));
  await sleep(E.SAVE_DELAY_MS + 40);
  assert.equal(d.kinds('draft').length, 1, 'the first draft is in flight');
  d.ep.change(modeEl('pick')); d.ep.change(pickEl(0));
  d.ep.click({}, 'dl-ep-close');
  d.ep.open(18);
  await sleep(700);
  assert.deepEqual(order, ['get', 'draft', 'draft', 'get'], 'the change made meanwhile is sent, then the list is read');
  assert.deepEqual(stored, {mode: 'pick', episodes: ['a']}); assert.equal(d.kinds('draft')[1].body.revision, 1);
  const st = d.ep.state();
  assert.equal(st.sel.mode, 'pick'); assert.deepEqual([...st.sel.picked], [0]); assert.equal(st.revision, 2);
  assert.match(d.doc.root.html, /Đã đánh dấu 1 tập/);
});

test('dialog: "Tải N tập" comes back when the connection does; a failed reload is said and blocks until the list loads', async () => {
  let off = false, gets = 0;
  const d = dialogFor({get: async () => {
    gets++;
    if (gets === 2) throw fail(0, null, {}, 'offline');
    return {...single(), draft: {mode: 'all'}, revision: 1, plan: planned(3)};
  }, confirm: async () => ({group: {id: 3, total: 3}})}, {offline: () => off});
  d.ep.open(18);
  await tick(); await tick();
  d.ep.sync();
  off = true;
  d.ep.click({}, 'dl-ep-confirm');
  await tick();
  assert.equal(d.kinds('confirm').length, 0); assert.match(d.doc.root.html, /id="dl-ep-confirm"[^>]* disabled/);
  assert.match(d.doc.root.html, /Mất kết nối/);
  off = false;
  d.ep.sync();
  assert.ok(!/id="dl-ep-confirm"[^>]* disabled/.test(d.doc.root.html), 'the first poll after the connection returns turns it on');
  d.ep.click({}, 'dl-ep-reload');
  await sleep(20);
  assert.match(d.doc.root.html, /Không tải lại được danh sách tập: offline/); assert.match(d.doc.root.html, /Thử tải lại danh sách/);
  assert.match(d.doc.root.html, /id="dl-ep-confirm"[^>]* disabled/, 'the old list is not confirmed');
  d.ep.click({}, 'dl-ep-reload');
  await sleep(20);
  assert.ok(!d.doc.root.html.includes('Không tải lại được')); assert.ok(!/id="dl-ep-confirm"[^>]* disabled/.test(d.doc.root.html));
});

test('dialog: an all-existing refusal is not asked again; a failed save can be sent again; an answer after closing is a toast', async () => {
  let drafts = 0;
  const all = [{episode: 'a', task_id: 1}, {episode: 'b', task_id: 2}, {episode: 'c', task_id: 3}];
  const d = dialogFor({get: async () => ({...single(), draft: {mode: 'all'}, revision: 1, plan: planned(3)}),
    confirm: async () => { await sleep(30); throw fail(409, 'ITEMS_EXIST', {existing: all, existing_count: 3}, 'Mọi tập đã chọn đều đã nằm trong danh sách tải.'); },
    draft: async body => {
      drafts++;
      if (drafts === 1) throw fail(500, 'INTERNAL', {}, 'hỏng');
      return {revision: 2, draft: body.selection, plan: planned(1), plan_error: null};
    }});
  d.ep.open(18);
  await tick(); await tick();
  d.ep.click({}, 'dl-ep-confirm');
  await sleep(60);
  assert.match(d.doc.root.html, /3 tập đã có trong danh sách tải/); assert.ok(!d.doc.root.html.includes('data-action="dl-ep-rest"'));
  d.ep.click({}, 'dl-ep-rest');
  await sleep(60);
  assert.equal(d.kinds('confirm').length, 1, 'the same refusal is not asked for again');
  d.ep.change(modeEl('pick')); d.ep.change(pickEl(0));
  await sleep(E.SAVE_DELAY_MS + 40);
  assert.match(d.doc.root.html, /Chưa lưu được lựa chọn: hỏng/); assert.match(d.doc.root.html, /data-action="dl-ep-save"/);
  assert.match(d.doc.root.html, /id="dl-ep-confirm"[^>]* disabled/);
  d.ep.click({}, 'dl-ep-save');
  await sleep(40);
  assert.equal(drafts, 2); assert.ok(!d.doc.root.html.includes('dl-ep-save')); assert.match(d.doc.root.html, /Tải 1 tập<\/button>/);
  d.ep.click({}, 'dl-ep-confirm'); d.ep.click({}, 'dl-ep-close');
  await sleep(60);
  assert.equal(d.kinds('confirm').length, 2);
  assert.deepEqual(d.toasts.at(-1), ['Chưa tạo nhóm tập: Mọi tập đã chọn đều đã nằm trong danh sách tải.', true]);
});

/* M6 (N1, R44): the two STALE_PREVIEW branches (save() and confirm()) as download_groups answers them. */
test('dialog: STALE_PREVIEW on a draft or on Tải N tập reloads the list with its notice, never confirms by itself; the next press is the user\'s and keeps its key', async () => {
  const stale = () => fail(409, 'STALE_PREVIEW', {}, 'Danh sách tập đã đổi; mở lại danh sách rồi chọn lại.');
  const lists = [{...single(), revision: 0}, {...single(), fingerprint: 'fp-2', revision: 1},
    {...single(), fingerprint: 'fp-3', revision: 3, draft: {mode: 'all'}, plan: planned(3)}];
  let drafts = 0, confirms = 0;
  const d = dialogFor({get: async () => (lists.length > 1 ? lists.shift() : lists[0]),
    draft: async body => {
      drafts++;
      if (drafts === 1) throw stale();
      return {revision: body.revision + 1, draft: body.selection, plan: planned(3), plan_error: null};
    },
    confirm: async () => {
      confirms++;
      if (confirms === 1) throw stale();
      if (confirms === 2) throw fail(0, null, {}, 'lost');
      return {group: {id: 8, total: 3}, replay: true, existing: []};
    }});
  d.ep.open(18);
  await tick(); await tick();
  // A draft refused because the list changed: the new list is read with the notice; nothing is resent by itself.
  d.ep.change(modeEl('all'));
  await until(() => d.kinds('get').length === 2 && !d.ep.state().loading);
  let st = d.ep.state();
  assert.match(st.notice, /Danh sách tập đã đổi; đã tải lại danh sách/); assert.match(d.doc.root.html, /Danh sách tập đã đổi; đã tải lại danh sách/);
  assert.equal(st.fingerprint, 'fp-2'); assert.equal(st.revision, 1); assert.equal(st.sel.mode, null, 'the old choice is not carried to the new list');
  await sleep(E.SAVE_DELAY_MS + 100);
  assert.equal(d.kinds('draft').length, 1, 'the refused draft is not sent again by itself'); assert.equal(d.kinds('confirm').length, 0);
  assert.match(d.doc.root.html, /id="dl-ep-confirm"[^>]* disabled/);
  d.ep.change(modeEl('all'));
  await until(() => d.kinds('draft').length === 2 && !d.ep.state().saving && !!d.ep.state().plan);
  assert.deepEqual(d.kinds('draft')[1].body, {selection: {mode: 'all'}, fingerprint: 'fp-2', revision: 1}, 'the user\'s new choice, on the new list');
  // "Tải N tập" refused for the same reason: reloaded with its own notice, never sent again by itself.
  d.ep.click({}, 'dl-ep-confirm');
  await until(() => d.kinds('get').length === 3 && !d.ep.state().loading);
  st = d.ep.state();
  const first = d.kinds('confirm')[0].body;
  assert.equal(first.fingerprint, 'fp-2'); assert.match(first.idempotency_key, E.KEY_PATTERN);
  assert.match(st.notice, /Danh sách tập đã đổi trước khi gửi; đã tải lại/); assert.equal(st.fingerprint, 'fp-3');
  assert.equal(st.error, '', 'a reload, not an error'); assert.equal(d.doc.dialog.open, true);
  await sleep(E.SAVE_DELAY_MS + 100);
  assert.equal(d.kinds('confirm').length, 1, 'never confirmed by itself after the reload');
  assert.ok(!/id="dl-ep-confirm"[^>]* disabled/.test(d.doc.root.html), 'the stored choice on the new list waits for the user');
  // The user's press is a request on the new list with its own key; its lost answer is resent with that same key.
  d.ep.click({}, 'dl-ep-confirm');
  await until(() => d.kinds('confirm').length === 2 && !d.ep.state().confirming);
  const second = d.kinds('confirm')[1].body;
  assert.equal(second.fingerprint, 'fp-3'); assert.notEqual(second.idempotency_key, first.idempotency_key, 'another list is another request');
  assert.match(d.ep.state().error, /Mất kết nối khi gửi/);
  d.ep.click({}, 'dl-ep-confirm');
  await until(() => d.kinds('confirm').length === 3 && !d.doc.dialog.open);
  assert.equal(d.kinds('confirm')[2].body.idempotency_key, second.idempotency_key, 'the next press keeps the key: a replay, never a second group');
  assert.deepEqual(d.shown, [8]);
});

/* M6: "Tải các tập còn lại" (skip_existing) is "Tải N tập" without the episodes already listed. It keeps every other
 * check: the server would refuse them again (SCOPE_NOT_CONFIRMED, STALE_PREVIEW) or send the list the page could not
 * reload. */
const incomplete = () => {
  const a = single();
  return {...a, listing: {...a.listing, complete: false, message: 'Đã đọc tới giới hạn 25 trang.'}, draft: {mode: 'all'}, revision: 1,
    plan: {...planned(3), complete: false, confirm_label: 'Tải 3 tập đã thấy', note: 'Danh sách chưa đầy đủ: chỉ có các tập đã thấy.'}};
};
const restButton = html => (html.match(/<button[^>]*data-action="dl-ep-rest"[^>]*>/) || [''])[0];

test('dialog HTML: "Tải các tập còn lại" is off, with the reason, whenever "Tải N tập" would be', () => {
  const m = E.model(single());
  const base = {...E.emptyState(), loading: false, model: m, fingerprint: 'fp-1', revision: 1, sel: {...E.emptySelection(m), mode: 'all'},
    plan: planned(3), existing: {existing: [{episode: 'b', label: 'Tập b', task_id: 37}], existing_count: 1}};
  const on = restButton(E.html(base, false));
  assert.ok(on && !on.includes('disabled'), 'no check left: it can be pressed');
  const off = [['offline', {}, true], ['a changed list', {remote: {fingerprint: 'fp-2', revision: 1}}],
    ['a failed reload', {loadError: 'Không tải lại được danh sách tập: x'}], ['an unsaved change', {dirty: true}], ['a draft on its way', {saving: true}], ['a refused draft', {saveError: 'Chưa lưu được lựa chọn: x'}],
    ['a plan error', {planError: {error: 'Chọn một bản'}}], ['no plan', {plan: null}], ['a missing variant', {plan: {...planned(3), missing: [{episode: 'a'}]}}],
    ['an ambiguous variant', {plan: {...planned(3), ambiguous: [{episode: 'a'}]}}], ['the scope box', {plan: {...planned(3), complete: false}}],
    ['too many', {plan: {...planned(501), too_large: true}}]];
  for (const [name, extra, offline] of off) {
    const html = E.html({...base, ...extra}, !!offline);
    assert.match(restButton(html), / disabled aria-describedby="dl-ep-why"/, name);
    assert.match(html, /id="dl-ep-why"/, name + ': the reason is on the page');
  }
});

test('dialog: "Tải các tập còn lại" keeps the scope box, a changed list and a failed reload; it sends confirm_scope with skip_existing', async () => {
  let failNext = false;
  const d = dialogFor({get: async () => { if (failNext) { failNext = false; throw fail(0, null, {}, 'offline'); } return incomplete(); },
    confirm: async body => {
      if (!body.skip_existing) throw fail(409, 'ITEMS_EXIST', {existing: [{episode: 'b', label: 'Tập b', task_id: 37, state: 'DOWNLOADING'}], existing_count: 1});
      return {group: {id: 6, total: 2}, replay: false, existing: [{episode: 'b', label: 'Tập b', task_id: 37, state: 'DOWNLOADING'}]};
    }});
  const scope = checked => ({inDialog: true, id: 'dl-ep-scope', checked, dataset: {}});
  const rests = () => d.kinds('confirm').filter(c => c.body.skip_existing).length;
  const refused = async () => {
    d.ep.change(scope(true));
    d.ep.click({}, 'dl-ep-confirm');
    await until(() => /1 tập đã có trong danh sách tải/.test(d.doc.root.html));
  };
  d.ep.open(18);
  await tick(); await tick();
  await refused();
  assert.equal(d.kinds('confirm')[0].body.confirm_scope, true);
  assert.ok(!restButton(d.doc.root.html).includes('disabled'));
  // 1) the scope box unticked after the refusal
  d.ep.change(scope(false));
  assert.match(restButton(d.doc.root.html), / disabled/);
  d.ep.click({}, 'dl-ep-rest');
  await sleep(30);
  assert.equal(rests(), 0, 'an unticked scope box stops it as it stops Tải N tập');
  d.ep.change(scope(true));
  assert.ok(!restButton(d.doc.root.html).includes('disabled'));
  // 2) the poll sees another list
  d.setTask({id: 18, state: 'NEEDS_CHOICE', episodes: {fingerprint: 'fp-9', revision: 1}});
  d.ep.sync();
  assert.match(d.doc.root.html, /Danh sách tập vừa đổi/); assert.match(restButton(d.doc.root.html), / disabled/);
  d.ep.click({}, 'dl-ep-rest');
  await sleep(30);
  assert.equal(rests(), 0, 'a changed list stops it (the server would answer STALE_PREVIEW)');
  d.setTask({id: 18, state: 'NEEDS_CHOICE', episodes: {fingerprint: 'fp-1', revision: 1}});
  d.ep.sync();
  // 3) a reload that failed keeps the old list on the page: it is not sent
  failNext = true;
  d.ep.click({}, 'dl-ep-reload');
  await until(() => /Không tải lại được danh sách tập/.test(d.doc.root.html));
  assert.match(d.doc.root.html, /1 tập đã có trong danh sách tải/); assert.match(restButton(d.doc.root.html), / disabled/);
  d.ep.click({}, 'dl-ep-rest');
  await sleep(30);
  assert.equal(rests(), 0, 'the list the page could not reload is not sent');
  // 4) the list loads again: asked anew, then the rest goes with its scope
  d.ep.click({}, 'dl-ep-reload');
  await until(() => !d.ep.state().loading && !d.ep.state().loadError);
  await refused();
  d.ep.click({}, 'dl-ep-rest');
  await until(() => rests() === 1 && !d.doc.dialog.open);
  const rest = d.kinds('confirm').at(-1).body;
  assert.deepEqual([rest.skip_existing, rest.confirm_scope, rest.fingerprint, rest.selection], [true, true, 'fp-1', {mode: 'all'}]);
  assert.match(rest.idempotency_key, E.KEY_PATTERN);
  assert.equal(d.kinds('confirm').length, 3, 'two plain presses (each refused) and one rest'); assert.deepEqual(d.shown, [6]);
  assert.match(d.toasts.at(-1)[0], /bỏ qua 1 tập đã có/);
});

/* ----- the page's mode (plan 9.18, Codex P2 of M5): the live store and the page controller as live.html runs them ----- */
const L = require('./download-live.js');
const modeData = () => snapshot([task(24, 'WAITING_LOGIN', {login_source: 'phim-a', login_reason: 'SESSION_EXPIRED'})],
  {accounts: {sources: [source('phim-a', {label: 'Nguồn A', state: 'CONNECTED'})], problems: [], problem_text: '', error: null}});
/* adapter → live store → download-live controller (no DOM: the page HTML is read back, clicks go to its handler). */
function livePage(phoneAnswer, transport, extra = {}) {
  const f = fake({'GET /api/downloads': {status: 200, body: extra.data || modeData()}, 'GET /api/phone-mode': phoneAnswer,
    'GET /api/status': extra.status || {status: 200, body: {}}, ...(extra.routes || {}),
    'POST /api/download-accounts/phim-a/login': {status: 202, body: {login: 'STARTED'}},
    'POST /api/download-accounts/phim-a/cancel-login': {status: 202, body: {}},
    'POST /api/download-accounts/phim-a/disconnect': {status: 200, body: {}}});
  const store = A.createLiveStore(A.create({contracts: C, transport: transport || f.transport}));
  const toasts = [], modals = [];
  global.document = {addEventListener() {}, removeEventListener() {}, querySelector: () => null, activeElement: null, body: {}};
  const page = L.create({store, view: V, core: K, episodes: null, contracts: C, $: () => null, icon: () => '',
    toast: (text, error) => toasts.push([text, !!error]), showModal: (title, body, commit, label) => modals.push({title, body, commit, label}),
    openCleanable() {}, dom: {morph() {}, parse: () => ({firstElementChild: null}), patch() {}}});
  return {f, store, page, toasts, modals, press: op => page.click({dataset: {op}}, 'dl-account'),
    check: () => page.click({dataset: {}}, 'dl-mode-check'),
    accountPosts: () => f.posts().filter(p => p.path.startsWith('/api/download-accounts/')),
    phoneGets: () => f.calls.filter(c => c.method === 'GET' && c.path === '/api/phone-mode').length};
}
const hasAccountButton = html => html.includes('data-action="dl-account"');
const PC_CTA = /data-action="dl-account-show" data-source="phim-a">Đăng nhập Nguồn A/;

test('page mode: 503, 404, a dropped connection or an answer without a boolean remote is unknown: no account button, no POST', async () => {
  const unknown = [['503', {status: 503, body: {error: 'fixture mode unavailable'}}, /Control Center trả lỗi 503/],
    ['404', {status: 404, body: {error: 'not found'}}, /không có mục này \(404\)/], ['network', 'DROP', /mất kết nối hoặc quá thời gian chờ/],
    ['no remote', {status: 200, body: {enabled: true}}, /thiếu thông tin này/], ['remote as text', {status: 200, body: {remote: 'false'}}, /thiếu thông tin này/],
    ['empty body', {status: 200, body: null}, /thiếu thông tin này/]];
  for (const [name, answer, why] of unknown) {
    const p = livePage(() => answer);
    await p.store.loadDownloads();
    await p.store.loadPhone();
    const html = p.page.html();
    assert.equal(p.store.snapshot().device, null, name);
    assert.ok(!hasAccountButton(html), name + ': no account button');
    assert.match(html, /Chưa xác định được trang này mở trên PC hay qua điện thoại/, name); assert.match(html, why, name);
    assert.match(html, /data-action="dl-mode-check">Kiểm tra lại/, name);
    assert.ok(!PC_CTA.test(html), name + ': the waiting row does not guide a PC sign-in');
    assert.match(html, /Lượt này chờ phiên đăng nhập của Nguồn A/, name);
    assert.ok(!html.includes('fixture mode unavailable'), name + ': the server text is not shown');
    for (const op of ['login', 'cancel-login', 'disconnect']) p.press(op);
    await sleep(20);
    assert.equal(p.accountPosts().length, 0, name + ': a press sends nothing'); assert.equal(p.modals.length, 0, name + ': no disconnect dialog');
    assert.deepEqual(p.toasts, [], name + ': the page refuses the press itself, before the store would');
    await assert.rejects(() => p.store.accountAction('accountLogin', 'phim-a'), /Chưa xác định/, name + ': the store refuses too');
    assert.equal(p.f.posts().length, 0, name + ': no POST at all');
    p.store.stop();
    await sleep(5);
  }
});

test('page mode: Kiểm tra lại only reads /api/phone-mode; remote=false gives working buttons, remote=true the phone note; an error later keeps the answer', async () => {
  for (const remote of [false, true]) {
    let phone = {status: 503, body: {}};
    const p = livePage(() => phone);
    await p.store.loadDownloads();
    await p.store.loadPhone();
    assert.ok(!hasAccountButton(p.page.html()));
    phone = {status: 200, body: {remote, enabled: remote}};
    p.check();
    await sleep(20);
    assert.equal(p.phoneGets(), 2, 'one GET per check'); assert.equal(p.f.posts().length, 0, 'a check sends no POST');
    let html = p.page.html();
    assert.ok(!html.includes('dl-mode-check'), 'known: no re-check');
    if (!remote) {
      assert.match(html, /data-action="dl-account" data-op="login"[^>]*>Đăng nhập lại/); assert.match(html, /data-op="disconnect"/);
      assert.match(html, PC_CTA, 'the waiting row points to the panel on the PC');
      p.press('login');
      await sleep(20);
      assert.deepEqual(p.accountPosts().map(x => x.path), ['/api/download-accounts/phim-a/login'], 'one POST, only after a press');
      p.press('disconnect');
      assert.equal(p.modals.length, 1, 'disconnect asks first'); assert.equal(p.accountPosts().length, 1);
      await p.modals[0].commit();
      assert.equal(p.accountPosts().at(-1).path, '/api/download-accounts/phim-a/disconnect');
    } else {
      assert.ok(!hasAccountButton(html)); assert.match(html, /chỉ làm trên PC/); assert.match(html, /Đăng nhập Nguồn A trên PC/);
      for (const op of ['login', 'disconnect']) p.press(op);
      await sleep(20);
      assert.equal(p.accountPosts().length, 0, 'the phone never posts'); assert.equal(p.modals.length, 0);
      assert.deepEqual(p.toasts, [], 'the phone page refuses the press itself');
    }
    const posts = p.f.posts().length;
    phone = {status: 503, body: {}};
    await p.store.loadPhone();
    html = p.page.html();
    assert.equal(p.store.snapshot().device, remote ? 'phone' : 'pc', 'an error keeps what the last answer said');
    assert.equal(hasAccountButton(html), !remote, remote ? 'an error never turns the phone into the PC' : 'the confirmed PC keeps its buttons');
    phone = {status: 200, body: {enabled: true}};
    await p.store.loadPhone();
    assert.equal(p.store.snapshot().device, remote ? 'phone' : 'pc', 'an answer without remote changes nothing either');
    assert.equal(p.f.posts().length, posts);
    p.store.stop();
    await sleep(5);
  }
});

test('page mode: while /api/phone-mode has not answered nothing shows or sends; one check at a time', async () => {
  let release;
  const p = livePage(() => new Promise(resolve => { release = resolve; }));
  await p.store.loadDownloads();
  const pending = p.store.loadPhone();
  await tick();
  const html = p.page.html();
  assert.ok(!hasAccountButton(html)); assert.match(html, /Đang kiểm tra trang này mở trên PC hay qua điện thoại/);
  assert.match(html, /data-action="dl-mode-check" disabled/); assert.ok(!PC_CTA.test(html));
  for (const op of ['login', 'cancel-login', 'disconnect']) p.press(op);
  p.check(); p.store.loadPhone();
  await tick();
  assert.equal(p.phoneGets(), 1, 'a check while one is pending shares it'); assert.equal(p.accountPosts().length, 0);
  release({status: 200, body: {remote: false, enabled: false}});
  await pending;
  await sleep(10);
  assert.match(p.page.html(), /data-action="dl-account" data-op="login"/, 'the answer arrives: the PC buttons');
  assert.equal(p.accountPosts().length, 0, 'still nothing sent before a press');
  p.store.stop();
});

test('page mode: a /api/phone-mode GET cut by the timeout is unknown, not the PC', async () => {
  const calls = [];
  const fetchImpl = (path, init) => {
    calls.push({method: init.method, path});
    if (path === '/api/phone-mode') {
      return new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'}))));
    }
    const body = path === '/api/session' ? {token: TOKEN} : path === '/api/downloads' ? modeData() : {};
    return Promise.resolve({status: 200, text: async () => JSON.stringify(body)});
  };
  const p = livePage(null, A.fetchTransport(fetchImpl, 30));
  await p.store.loadDownloads();
  await p.store.loadPhone();
  const html = p.page.html();
  assert.equal(p.store.snapshot().device, null);
  assert.ok(!hasAccountButton(html)); assert.match(html, /\(mất kết nối hoặc quá thời gian chờ\)/);
  p.press('login'); p.press('disconnect');
  await sleep(20);
  assert.equal(calls.filter(c => c.method === 'POST').length, 0, 'no POST after a timeout');
  p.store.stop();
});

test('page mode: the poll asks again at most 5 times while the mode is unknown (GET only) and stops once it is known', async () => {
  const p = livePage(() => ({status: 503, body: {}}));
  p.store.start(10);
  await until(() => p.phoneGets() >= 6);
  await sleep(100);
  assert.equal(p.phoneGets(), 1 + 5, 'the first check and 5 retries');
  assert.equal(p.f.posts().length, 0);
  p.store.stop();
  await sleep(5);
  let n = 0;
  const q = livePage(() => (++n < 3 ? {status: 503, body: {}} : {status: 200, body: {remote: false, enabled: false}}));
  q.store.start(10);
  await until(() => q.store.snapshot().device === 'pc');
  await sleep(100);
  assert.equal(q.phoneGets(), 3, 'no retry once the PC is confirmed'); assert.equal(q.store.snapshot().device, 'pc');
  assert.equal(q.f.posts().length, 0);
  q.store.stop();
  await sleep(5);
});

test('page mode: a GET still on its way when the poll ticks does not use a retry up', async () => {
  let hold, first = true;
  const p = livePage(() => (first ? (first = false, new Promise(resolve => { hold = resolve; })) : {status: 503, body: {}}));
  p.store.start(10);
  await sleep(150);
  assert.equal(p.phoneGets(), 1, 'ticks during a hung request send nothing more');
  hold({status: 503, body: {}});
  await until(() => p.phoneGets() >= 6);
  await sleep(100);
  assert.equal(p.phoneGets(), 1 + 5, 'all 5 retries still follow once it ended'); assert.equal(p.f.posts().length, 0);
  p.store.stop();
  await sleep(5);
});

test('page mode: an answer with enabled but no remote is unknown, also for the poll: still at most 1 + 5 checks', async () => {
  const p = livePage(() => ({status: 200, body: {enabled: true, failed_attempts: 2}}));
  p.store.start(10);
  await until(() => p.phoneGets() >= 6);
  await sleep(100);
  assert.equal(p.phoneGets(), 1 + 5, 'the PC panel reload needs a confirmed PC'); assert.equal(p.store.snapshot().device, null);
  assert.equal(p.f.posts().length, 0);
  p.store.stop();
  await sleep(5);
});

test('page mode: back online after the retries ran out, an unknown mode is asked again (at most 5 more)', async () => {
  let online = false;
  const p = livePage(() => ({status: 503, body: {}}), null, {status: () => (online ? {status: 200, body: {}} : 'DROP')});
  p.store.start(10);
  await until(() => p.phoneGets() >= 6 && p.store.snapshot().offline);
  await sleep(100);
  assert.equal(p.store.snapshot().offline, true); assert.equal(p.phoneGets(), 1 + 5);
  online = true;
  await until(() => p.phoneGets() >= 11);
  await sleep(100);
  assert.equal(p.store.snapshot().offline, false); assert.equal(p.phoneGets(), 1 + 5 + 5, 'five more once the status answers again');
  assert.equal(p.f.posts().length, 0);
  p.store.stop();
  await sleep(5);
  let pc = false;
  const q = livePage(() => ({status: 200, body: {remote: false, enabled: false}}), null, {status: () => (pc ? {status: 200, body: {}} : 'DROP')});
  q.store.start(10);
  await until(() => q.store.snapshot().device === 'pc' && q.store.snapshot().offline);
  const gets = q.phoneGets();
  pc = true;
  await until(() => !q.store.snapshot().offline);
  await sleep(100);
  assert.equal(q.phoneGets(), gets, 'a known mode is not asked again when the status comes back'); assert.equal(q.store.snapshot().device, 'pc');
  q.store.stop();
  await sleep(5);
});

test('page mode: a retry after an error stays quiet; Kiểm tra lại shows its own progress and joins a pending request', async () => {
  let answer = {status: 503, body: {}}, release;
  const p = livePage(() => answer);
  await p.store.loadDownloads();
  await p.store.loadPhone();
  answer = new Promise(resolve => { release = resolve; });
  const retry = p.store.loadPhone();
  await tick();
  let html = p.page.html();
  assert.equal(p.store.snapshot().device_checking, false, 'a retry does not flip back to checking');
  assert.match(html, /Control Center trả lỗi 503/); assert.ok(!html.includes('Đang kiểm tra'));
  assert.match(html, /data-action="dl-mode-check">Kiểm tra lại/, 'the button stays usable');
  p.check();
  await tick();
  html = p.page.html();
  assert.match(html, /Đang kiểm tra trang này mở trên PC hay qua điện thoại/); assert.match(html, /data-action="dl-mode-check" disabled/);
  assert.equal(p.phoneGets(), 2, 'the check joins the retry already on its way');
  release({status: 200, body: {remote: false}});
  await retry;
  await sleep(10);
  html = p.page.html();
  assert.ok(!html.includes('Đang kiểm tra')); assert.match(html, /data-action="dl-account" data-op="login"/);
  assert.equal(p.accountPosts().length, 0);
  p.store.stop();
});

test('page mode: loadPhone({fresh}) asks again after a request already on its way; a plain call shares it', async () => {
  const answers = [];
  const p = livePage(() => new Promise(resolve => answers.push(resolve)));
  const first = p.store.loadPhone(), shared = p.store.loadPhone(), fresh = p.store.loadPhone({fresh: true});
  await tick();
  assert.equal(p.phoneGets(), 1);
  answers[0]({status: 200, body: {remote: false}});
  await first; await shared;
  await sleep(5);
  assert.equal(p.phoneGets(), 2, 'fresh sends its own GET once the earlier one ended');
  answers[1]({status: 200, body: {remote: true}});
  await fresh;
  assert.equal(p.store.snapshot().device, 'phone', 'the newer answer wins'); assert.equal(p.f.posts().length, 0);
  p.store.stop();
});

test('page mode: when a Tailscale task ends, the mode is read again even if a check was already on its way', async () => {
  const answers = [];
  let running = true;
  const p = livePage(() => new Promise(resolve => answers.push(resolve)), null,
    {routes: {'GET /api/tailscale': () => ({status: 200, body: {installed: true, task: {running}}})}});
  await p.store.loadTailscale();
  const earlier = p.store.loadPhone();
  await tick();
  running = false;
  const after = p.store.loadTailscale();
  await sleep(5);
  assert.equal(p.phoneGets(), 1, 'the re-read waits for the request already on its way');
  answers[0]({status: 200, body: {remote: false}});
  await earlier;
  await sleep(5);
  assert.equal(p.phoneGets(), 2, 'then asks again: that request left before the task ended');
  answers[1]({status: 200, body: {remote: false, enabled: true}});
  await after;
  assert.equal(p.store.snapshot().phone.enabled, true); assert.equal(p.f.posts().length, 0);
  p.store.stop();
});

test('page mode: cancel-login posts once on a confirmed PC for a source that is signing in; never on the phone, after an error or while pending', async () => {
  const signingIn = () => snapshot([], {accounts: {sources: [source('phim-a', {label: 'Nguồn A', state: 'LOGGING_IN', login_running: true})],
    problems: [], problem_text: '', error: null}});
  const cases = [['PC', () => ({status: 200, body: {remote: false}}), true], ['phone', () => ({status: 200, body: {remote: true}}), false],
    ['503', () => ({status: 503, body: {}}), false], ['pending', () => new Promise(() => {}), false]];
  for (const [name, phone, pc] of cases) {
    const p = livePage(phone, null, {data: signingIn()});
    await p.store.loadDownloads();
    p.store.loadPhone();
    await sleep(5);
    const html = p.page.html();
    assert.equal(/data-op="cancel-login"/.test(html), pc, name + ': Hủy đăng nhập only on the confirmed PC');
    if (name === 'phone') assert.match(html, /chỉ làm trên PC/);
    p.press('cancel-login');
    await sleep(20);
    assert.deepEqual(p.accountPosts().map(x => x.path), pc ? ['/api/download-accounts/phim-a/cancel-login'] : [], name);
    if (!pc) assert.deepEqual(p.toasts, [], name + ': the page refuses the press itself');
    p.store.stop();
    await sleep(5);
  }
});

test('page mode: a phone-mode POST answer without remote (Bật/Tắt, Gia hạn) keeps the known mode', async () => {
  for (const remote of [true, false]) {
    const p = livePage(() => ({status: 200, body: {remote, enabled: true}}));
    await p.store.loadDownloads();
    await p.store.loadPhone();
    for (const [operation, body] of [['phoneExtend', {}], ['phoneMode', {enabled: false}]]) {
      await p.store.dispatch(operation, null, body); // the fake answers {} (no remote)
      assert.equal(p.store.snapshot().device, remote ? 'phone' : 'pc', operation);
      assert.equal(p.store.snapshot().remote, remote, operation);
      assert.equal(hasAccountButton(p.page.html()), !remote, operation + ': an answer without remote never turns the phone into the PC');
    }
    assert.equal(p.accountPosts().length, 0);
    p.store.stop();
    await sleep(5);
  }
});

/* M6 (N1, R20): the phone drives episodes and groups (download-core ignores the device for them); only the account
 * routes are PC only. adapter → live store → download-live with the real episode dialog on the fake document. */
test('phone (remote): a series page still offers Chọn tập, whose dialog reads, drafts and confirms; an active group keeps Dừng, Tiếp tục, Thử lại and Hủy', async () => {
  const PHONE = ctx({remote: true, device: 'phone'});
  const page = task(18, 'NEEDS_CHOICE', {choice_kind: 'episodes', media: {source_label: 'Nguồn A', provider: 'phim-a'},
    episodes: {title: 'Phim', kind: 'series', episode_count: 3, complete: true, message: null, fingerprint: 'fp-1', revision: 0, has_draft: false}});
  const busy = group(4, {counts: counts({running: 1, held: 1, failed: 1, completed: 1})});
  const chooser = K.actions(page, PHONE).find(a => a.id === 'episodes');
  assert.deepEqual([chooser.label, chooser.enabled, chooser.primary], ['Chọn tập', true, true]);
  assert.deepEqual(K.groupActions(busy, PHONE).map(a => [a.id, a.enabled]), [['stop', true], ['resume', true], ['retry', true], ['cancel', true]]);
  const data = snapshot([page], {groups: [busy], accounts: {sources: [source('phim-a', {label: 'Nguồn A', state: 'CONNECTED'})], problems: [],
    problem_text: '', error: null}});
  const f = fake({'GET /api/downloads': {status: 200, body: data}, 'GET /api/phone-mode': {status: 200, body: {remote: true, enabled: true}},
    'GET /api/downloads/18/episodes': {status: 200, body: single()},
    'POST /api/downloads/18/episodes/draft': options => ({status: 200, body: {task_id: 18, revision: 1, draft: JSON.parse(options.body).selection,
      plan: planned(3), plan_error: null}}),
    'POST /api/downloads/18/episodes/confirm': {status: 200, body: {group: {id: 9, total: 3}, replay: false, existing: []}}});
  const store = A.createLiveStore(A.create({contracts: C, transport: f.transport}));
  const doc = fakeDocument();
  Object.assign(global.document, {addEventListener() {}, removeEventListener() {}, querySelector: () => null});
  global.window = {crypto: globalThis.crypto};
  try {
    const modals = [];
    const live = L.create({store, view: V, core: K, episodes: E, contracts: C, $: () => null, icon: () => '', toast() {},
      showModal: (title, body, commit, label) => modals.push({title, commit, label}), openCleanable() {},
      dom: {morph() {}, parse: () => ({firstElementChild: null}), patch: (el, html) => { el.html = html; }}});
    await store.loadDownloads();
    await store.loadPhone();
    assert.equal(store.snapshot().device, 'phone');
    const html = live.html();
    assert.ok(!html.includes('data-action="dl-account"'), 'no account button on the phone'); assert.match(html, /chỉ làm trên PC/);
    assert.match(html, /data-op="episodes"[^>]*>Chọn tập</);
    for (const op of ['stop', 'resume', 'retry', 'cancel']) {
      assert.match(html, new RegExp('data-action="dl-group-op" data-group="4" data-op="' + op + '"(?![^>]*disabled)'), op);
    }
    live.click({dataset: {id: '18', op: 'episodes'}}, 'dl-op');
    await until(() => doc.dialog.open && /name="dl-ep-mode"/.test(doc.root.html));
    assert.ok(doc.dialog.open); assert.match(doc.root.html, /name="dl-ep-mode"/, 'the list is shown');
    assert.deepEqual(f.calls.filter(c => c.path.startsWith('/api/downloads/18/')).map(c => c.method + ' ' + c.path), ['GET /api/downloads/18/episodes'],
      'opening only reads');
    live.change(modeEl('all'));
    await until(() => /Tải 3 tập<\/button>/.test(doc.root.html));
    assert.match(doc.root.html, /Tải 3 tập<\/button>/, 'the draft answer gives the count');
    live.click({}, 'dl-ep-confirm');
    await until(() => !doc.dialog.open);
    assert.equal(doc.dialog.open, false, 'the group is made: the dialog closes');
    for (const op of ['stop', 'resume']) live.click({dataset: {group: '4', op}}, 'dl-group-op');
    await until(() => f.posts().filter(p => p.path.startsWith('/api/downloads/groups/')).length === 2);
    assert.equal(f.posts().filter(p => p.path.startsWith('/api/downloads/groups/')).length, 2, 'Dừng nhóm and Tiếp tục nhóm post at once');
    for (const op of ['retry', 'cancel']) live.click({dataset: {group: '4', op}}, 'dl-group-op');
    assert.equal(modals.length, 2, 'retry and cancel ask first, as on the PC');
    for (const m of modals) await m.commit();
    await sleep(30); // the focus step after each action runs while the fake document is still there
    assert.deepEqual(f.posts().map(p => p.path), ['/api/downloads/18/episodes/draft', '/api/downloads/18/episodes/confirm',
      '/api/downloads/groups/4/stop', '/api/downloads/groups/4/resume', '/api/downloads/groups/4/retry', '/api/downloads/groups/4/cancel']);
    const sent = f.posts()[1].body;
    assert.deepEqual([sent.selection, sent.fingerprint], [{mode: 'all'}, 'fp-1']); assert.match(sent.idempotency_key, E.KEY_PATTERN);
    assert.ok(f.posts().every(p => p.headers['X-BiliFlow-Token'] === TOKEN));
  } finally {
    store.stop();
    delete global.window;
  }
});

/* M6: download-fake-server.cjs (the M5 browser checks) answers the all-existing case as download_groups._new_items:
 * 409 ITEMS_EXIST, never an empty group. Its names are checked against the backend in test_dashboard_v2_downloads.py. */
test('fake server: ITEMS_EXIST as download_groups._new_items (some, or every one even with skip_existing); never an empty group', () => {
  const F = require('./download-fake-accounts.cjs');
  let rows = [], next = 40;
  const fakeAccounts = F.create({task: (id, state, extra) => ({id, state, ...extra}), tasks: () => rows, push: row => { rows = [...rows, row]; },
    removeTasks: ids => { rows = rows.filter(x => !ids.has(x.id)); }, nextId: () => next++});
  rows = fakeAccounts.seed();
  const groupCount = () => fakeAccounts.fakeControl('/__fake/groups', new URLSearchParams())[1].groups.length;
  const before = groupCount();
  const page = fakeAccounts.handleGet('/api/downloads/22/episodes')[1];
  const send = (key, episodes, skip) => fakeAccounts.handlePost('/api/downloads/22/episodes/confirm', {idempotency_key: key, fingerprint: page.fingerprint,
    selection: {mode: 'pick', episodes}, ...(skip ? {skip_existing: true} : {})});
  const taken = [{episode: 'c-e2', label: 'Tập 2', task_id: 37, state: 'DOWNLOADING'}, {episode: 'c-e3', label: 'Tập 3', group_id: 3, state: 'PENDING'}];
  const some = 'Có tập đã nằm trong danh sách tải; bỏ chọn các tập đó hoặc tải phần còn lại.', all = 'Mọi tập đã chọn đều đã nằm trong danh sách tải.';
  assert.deepEqual(send('fake-some-0001', ['c-e1', 'c-e2']), [409, {error: some, code: 'ITEMS_EXIST', existing: [taken[0]], existing_count: 1}]);
  assert.deepEqual(send('fake-all-00001', ['c-e2', 'c-e3']), [409, {error: some, code: 'ITEMS_EXIST', existing: taken, existing_count: 2}],
    'every one listed, no skip: the same first answer');
  assert.deepEqual(send('fake-all-00002', ['c-e2', 'c-e3'], true), [409, {error: all, code: 'ITEMS_EXIST', existing: taken, existing_count: 2}],
    'skip_existing with nothing new');
  assert.equal(groupCount(), before, 'no empty group'); assert.equal(rows.find(x => x.id === 22).state, 'NEEDS_CHOICE');
  const [status, made] = send('fake-rest-0001', ['c-e1', 'c-e2'], true);
  assert.equal(status, 200); assert.equal(made.group.total, 1); assert.deepEqual(made.existing, [taken[0]]);
  assert.equal(groupCount(), before + 1); assert.equal(rows.find(x => x.id === 22).state, 'EXPANDED');
});

/* M6 (plan P3): "Chọn tất cả" (dl-ep-all) and "Bỏ chọn tất cả" (dl-ep-none), shown in the "Chọn tập" mode. They only
 * change the ticks: the count on "Tải N tập", the 500 limit and a missing variant come from the server's draft answer
 * (download_account_tasks.save_episode_draft), never from the page. */
const BAIT_TEXT = '&lt;img src=x onerror=&quot;window.__x=1&quot;&gt;';
const ticked = html => (html.match(/data-ep-pick="\d+" checked/g) || []).length;
const buttonOf = (html, action) =>
  (html.match(new RegExp('<button[^>]*data-action="' + action + '"[^>]*>')) || [''])[0];
const rowOf = (html, index) => (html.match(new RegExp('<li id="dl-ep-row-' + index + '"[\\s\\S]*?</li>')) || [''])[0];
const confirmOff = html => /id="dl-ep-confirm"[^>]* disabled/.test(html);
const CONFIRM_ON = n => new RegExp('data-action="dl-ep-confirm">Tải ' + n + ' tập</button>');
const NO_EPISODE = {error: 'Chọn ít nhất một tập.', code: 'BAD_SELECTION'}; // plan_selection on an empty pick
/* The draft answer for a list of `total` episodes with one file each; over 500 the plan says too_large. */
function draftAnswer(body, revision, total = 3) {
  const n = body.selection.mode === 'all' ? total : body.selection.episodes.length;
  if (!n) return {revision, draft: body.selection, plan: null, plan_error: NO_EPISODE};
  return {revision, draft: body.selection, plan: {...planned(n), too_large: n > 500}, plan_error: null};
}
/* The dialog of `list`, switched to "Chọn tập" (its first draft answered); settled(n): the n-th draft answered. */
async function pickDialog(list, draft) {
  const d = dialogFor({get: async () => list, draft});
  d.drafts = () => d.kinds('draft').map(c => c.body);
  d.settled = n => until(() => d.drafts().length === n && !d.ep.state().saving && !d.ep.state().dirty);
  d.ep.open(18);
  await tick(); await tick();
  assert.ok(!d.doc.root.html.includes('data-action="dl-ep-all"'), 'only in the Chọn tập mode');
  d.ep.change(modeEl('pick'));
  await d.settled(1);
  return d;
}

test('dialog: Chọn tất cả / Bỏ chọn tất cả tick all or none; the count and Tải N tập follow', async () => {
  let revision = 0, hold = null;
  const d = await pickDialog(single(), async body => { if (hold) await hold; return draftAnswer(body, ++revision); });
  let html = d.doc.root.html;
  assert.ok(buttonOf(html, 'dl-ep-all') && !buttonOf(html, 'dl-ep-all').includes('disabled'));
  assert.match(buttonOf(html, 'dl-ep-none'), / disabled/, 'nothing ticked: nothing to clear');
  assert.match(html, /id="dl-ep-why">Chọn ít nhất một tập/); assert.ok(confirmOff(html));
  // Chọn tất cả: every episode at once; "Tải N tập" waits for the server's count
  d.ep.click({}, 'dl-ep-all');
  html = d.doc.root.html;
  assert.equal(ticked(html), 3); assert.match(html, /Đã đánh dấu 3 tập/); assert.match(html, /3 tập · đã chọn 3/);
  assert.ok(!buttonOf(html, 'dl-ep-none').includes('disabled'));
  assert.ok(confirmOff(html), 'off until the server counted');
  await d.settled(2);
  assert.deepEqual(d.drafts()[1], {selection: {mode: 'pick', episodes: ['a', 'b', 'c']}, fingerprint: 'fp-1',
    revision: 1});
  html = d.doc.root.html;
  assert.match(html, /Đã đánh dấu 3 tập · Sẽ tải 3 tập/); assert.match(html, CONFIRM_ON(3));
  // Bỏ chọn tất cả: none; the server's reason keeps "Tải N tập" off
  d.ep.click({}, 'dl-ep-none');
  html = d.doc.root.html;
  assert.equal(ticked(html), 0); assert.match(html, /Đã đánh dấu 0 tập/); assert.match(html, /3 tập · đã chọn 0/);
  assert.match(buttonOf(html, 'dl-ep-none'), / disabled/);
  await d.settled(3);
  assert.deepEqual(d.drafts()[2], {selection: {mode: 'pick', episodes: []}, fingerprint: 'fp-1', revision: 2});
  assert.match(d.doc.root.html, /id="dl-ep-why">Chọn ít nhất một tập/); assert.ok(confirmOff(d.doc.root.html));
  // a burst (all, none, all) is one draft of the last state
  d.ep.click({}, 'dl-ep-all'); d.ep.click({}, 'dl-ep-none'); d.ep.click({}, 'dl-ep-all');
  await d.settled(4);
  await sleep(E.SAVE_DELAY_MS + 60);
  assert.equal(d.drafts().length, 4, 'one draft for the burst');
  assert.deepEqual(d.drafts()[3], {selection: {mode: 'pick', episodes: ['a', 'b', 'c']}, fingerprint: 'fp-1',
    revision: 3});
  // a press while a draft is on its way goes next, with the revision of that draft's answer
  let release;
  hold = new Promise(resolve => { release = resolve; });
  d.ep.click({}, 'dl-ep-none');
  await until(() => d.drafts().length === 5);
  d.ep.click({}, 'dl-ep-all');
  await sleep(E.SAVE_DELAY_MS + 60);
  assert.equal(d.drafts().length, 5, 'never two drafts at once');
  hold = null;
  release();
  await d.settled(6);
  assert.deepEqual(d.drafts().slice(4).map(b => [b.selection.episodes, b.revision]), [[[], 4], [['a', 'b', 'c'], 5]]);
  assert.ok(d.drafts().every(b => b.fingerprint === 'fp-1'));
  assert.match(d.doc.root.html, CONFIRM_ON(3));
  assert.equal(d.kinds('confirm').length, 0, 'never confirmed by itself'); assert.equal(d.kinds('get').length, 1);
});

test('dialog: Chọn tất cả on 501 episodes sends all 501 (never cut here); too_large keeps it off', async () => {
  const items = Array.from({length: 501}, (_, i) =>
    episode('b' + i, 'Tập ' + (i + 1), [v('b' + i + 'v', 'k', '720p')]));
  const list = answer({}, {variant_kinds: [{kind: 'k', label: '720p', episodes: 501}],
    groups: [{key: '', number: null, label: '', episodes: items}]});
  let revision = 0;
  const d = await pickDialog(list, async body => draftAnswer(body, ++revision, 501));
  d.ep.click({}, 'dl-ep-all');
  assert.equal(ticked(d.doc.root.html), 501); assert.match(d.doc.root.html, /Đã đánh dấu 501 tập/);
  await d.settled(2);
  const sent = d.drafts()[1].selection.episodes;
  assert.equal(sent.length, 501, 'the page never cuts the choice at 500 by itself');
  assert.deepEqual([sent[0], sent[9], sent[10], sent[500]], ['b0', 'b9', 'b10', 'b500'], 'the server order');
  const html = d.doc.root.html;
  assert.ok(confirmOff(html)); assert.match(html, /id="dl-ep-why">Một nhóm tối đa 500 tập; lựa chọn này có 501 tập/);
  assert.match(html, /disabled aria-describedby="dl-ep-why">Tải 501 tập<\/button>/, 'the server\'s label, off');
  d.ep.change({...pickEl(500), checked: false});
  await d.settled(3);
  assert.equal(d.drafts()[2].selection.episodes.length, 500);
  assert.match(d.doc.root.html, CONFIRM_ON(500), 'at the limit the server\'s plan allows it');
  d.ep.click({}, 'dl-ep-none');
  await d.settled(4);
  assert.deepEqual(d.drafts()[3].selection, {mode: 'pick', episodes: []}); assert.equal(ticked(d.doc.root.html), 0);
  assert.ok(confirmOff(d.doc.root.html)); assert.equal(d.kinds('confirm').length, 0);
});

test('dialog: Chọn tất cả never drops or guesses a variant; the server\'s missing list blocks it', async () => {
  // answer(): x1 has no k1 file; e2 and e1 have two files each (as download_account_listing.plan_selection sees it)
  const k1 = new Set(['e2', 'e10', 'e1']), all = ['e2', 'e10', 'e1', 'x1'];
  let revision = 0;
  const d = await pickDialog(answer(), async body => {
    const s = body.selection, n = s.episodes.length;
    revision++;
    if (!n) return {revision, draft: s, plan: null, plan_error: NO_EPISODE};
    const missing = s.episodes.filter(key => (s.variant_kind ? !k1.has(key) : !(s.variants || {})[key]));
    return {revision, draft: s, plan_error: null,
      plan: {...planned(n - missing.length), missing: missing.map(key => ({episode: key, label: key}))}};
  });
  d.ep.change({inDialog: true, name: 'dl-ep-how', value: 'kind', dataset: {}});
  d.ep.change({inDialog: true, id: 'dl-ep-kind', value: '0', dataset: {}});
  d.ep.click({}, 'dl-ep-all');
  await d.settled(2);
  assert.deepEqual(d.drafts()[1].selection, {mode: 'pick', episodes: all, variant_kind: 'k1'},
    'x1 has no such file: still ticked and sent, never left out by the page');
  let html = d.doc.root.html;
  assert.equal(ticked(html), 4); assert.ok(confirmOff(html));
  assert.match(html, /id="dl-ep-why">Có tập không có bản đã chọn/);
  assert.ok(rowOf(html, 3).includes('Thiếu bản đã chọn') && !rowOf(html, 0).includes('Thiếu bản đã chọn'));
  d.ep.change({inDialog: true, name: 'dl-ep-how', value: 'each', dataset: {}});
  await d.settled(3);
  assert.deepEqual(d.drafts()[2].selection, {mode: 'pick', episodes: all, variants: {e10: 'e10a', x1: 'x1a'}},
    'two files and none chosen (e2, e1): left out of variants, never guessed');
  html = d.doc.root.html;
  assert.ok(confirmOff(html)); assert.match(html, /Có tập không có bản đã chọn/);
  assert.deepEqual([0, 1, 2, 3].map(i => rowOf(html, i).includes('Thiếu bản đã chọn')), [true, false, true, false]);
  d.ep.click({}, 'dl-ep-none');
  await d.settled(4);
  assert.deepEqual(d.drafts()[3].selection, {mode: 'pick', episodes: [], variants: {}});
  assert.equal(ticked(d.doc.root.html), 0); assert.match(d.doc.root.html, /id="dl-ep-why">Chọn ít nhất một tập/);
  assert.equal(d.kinds('confirm').length, 0);
});

/* M6 (plan P6): the downloader has no global pause (plan 9.7, 9.10, 9.14: "không thêm pause toàn cục"). The queue-wide
 * tools are the slot count and Dọn file tạm; a task and a group keep their own Dừng / Tiếp tục. Every control of the
 * page is pinned, so a queue-wide control anywhere on it fails whatever its action name or wording. */
const GLOBAL_PAUSE = /data-(?:action|op)="[^"]*(?:pause|tam-dung|resume-all|stop-all|all-stop|all-resume)[^"]*"/i;
const GLOBAL_PAUSE_TEXT =
  /Tạm dừng|Tạm ngừng|pause|(?:Dừng|Ngừng|Tiếp tục)(?: tải)? (?:tất cả|hết|toàn bộ|hàng đợi|mọi)/i;
const actionsIn = html => [...new Set((html.match(/data-action="[^"]+"/g) || []).map(a => a.slice(13, -1)))].sort();
const partOf = (html, from, to) => html.slice(html.indexOf(from), html.indexOf(to, html.indexOf(from)));
/* The page's data-actions besides the account panel's own, and its fields besides each task's rename box. */
const PAGE_ACTIONS = ['dl-add', 'dl-cleanup', 'dl-filter', 'dl-group-op', 'dl-group-show', 'dl-op', 'dl-rename',
  'storage-refresh'];
const PAGE_FIELDS = ['dl-account-source', 'dl-rights', 'dl-slots', 'dl-urls'];
/* The `key` number of every `action` control (undefined when it names none). */
const targetsOf = (html, action, key) => (html.match(new RegExp('<[^>]*data-action="' + action + '"[^>]*>', 'g')) || [])
  .map(b => (b.match(new RegExp(' data-' + key + '="(\\d+)"')) || [])[1]);

test('no global pause: the page, the account panel and the queue tools on the PC, phone and unknown mode', async () => {
  const place = {group_id: 4, ordinal: 1, total: 4, code: 'S01E01', planned_name: '001 - Phim 4 - S01E01'};
  const data = snapshot([task(1, 'DOWNLOADING', {downloaded_bytes: 5}), task(2, 'QUEUED'), task(3, 'STOPPED'),
    task(24, 'WAITING_LOGIN', {login_source: 'phim-a', login_reason: 'SESSION_EXPIRED'}),
    task(31, 'DOWNLOADING', {group_id: 4, group: place})],
  {groups: [group(4, {counts: counts({running: 1, held: 1, failed: 1, completed: 1})})],
    temp: {tasks: 1, bytes: 1048576},
    accounts: {sources: [source('phim-a', {label: 'Nguồn A', state: 'CONNECTED'})], problems: [], problem_text: '',
      error: null}});
  const modes = [['PC', () => ({status: 200, body: {remote: false}}), 'pc', ['dl-account'], ['dl-account-show']],
    ['phone', () => ({status: 200, body: {remote: true}}), 'phone', [], []],
    ['unknown', () => ({status: 503, body: {}}), null, ['dl-mode-check'], []],
    ['checking', () => new Promise(() => {}), null, ['dl-mode-check'], []]];
  for (const [name, phone, device, panel, row] of modes) {
    const p = livePage(phone, null, {data});
    await p.store.loadDownloads();
    p.store.loadPhone();
    await sleep(5);
    assert.equal(p.store.snapshot().device, device, name);
    const html = p.page.html(), tools = partOf(html, 'id="dl-tools"', 'id="dl-groups"');
    assert.ok(!GLOBAL_PAUSE.test(html), name + ': no pause-all control');
    assert.ok(!GLOBAL_PAUSE_TEXT.test(html), name + ': no pause wording');
    assert.deepEqual(actionsIn(tools), ['dl-cleanup'], name + ': the queue tools are Dọn file tạm');
    assert.match(tools, /id="dl-slots"/, name + ': and the slot count');
    assert.deepEqual(actionsIn(partOf(html, 'id="dl-accounts"', '</section>')), panel, name + ': the account panel');
    assert.deepEqual(actionsIn(html), [...PAGE_ACTIONS, ...panel, ...row].sort(), name + ': every action on the page');
    const clickable = html.match(/<(?:button|a)\b[^>]*>/g) || [];
    assert.ok(clickable.length > 20 && clickable.every(b => /data-action="/.test(b)), name + ': every button acts');
    const fields = (html.match(/<(?:input|select|textarea)\b[^>]*>/g) || []).filter(f => !/data-rename="\d+"/.test(f));
    assert.deepEqual(fields.map(f => (f.match(/ id="([^"]+)"/) || [])[1]).sort(), PAGE_FIELDS, name + ': every field');
    assert.deepEqual([...new Set(targetsOf(html, 'dl-op', 'id'))].sort(), ['1', '2', '24', '3', '31'],
      name + ': a task op names its task');
    assert.deepEqual([...new Set(targetsOf(html, 'dl-group-op', 'group'))], ['4'],
      name + ': a group op names its group');
    assert.match(html, /data-action="dl-group-op" data-group="4" data-op="stop"/, name + ': a group keeps its Dừng');
    assert.match(html, /data-action="dl-op" data-id="1" data-op="stop"/, name + ': a task keeps its own Dừng');
    p.store.stop();
    await sleep(5);
  }
});

/* M6 (traceability): markup from a source where the gates had no bait yet is written as text. Already covered: the film
 * title (dialog, series row, group card), a variant's label, member episode labels and errors, the task row
 * (verify-download.cjs) and the dialog's error, scope note, missing and existing lists. */
test('escaped: the variant-kind choice, a plan error, an episode\'s place, member names, the account error', () => {
  const m = E.model(answer());
  const kind = E.html({...E.emptyState(), loading: false, model: m, fingerprint: 'fp-1', revision: 1,
    sel: {...E.emptySelection(m), mode: 'all', how: 'kind', kind: 0}, planError: {error: 'Chọn một bản ' + BAIT}},
  false);
  assert.ok(!kind.includes('<img'));
  assert.ok(kind.includes('<option value="0" selected>Lồng tiếng ' + BAIT_TEXT + ' (3/4 tập có bản này)</option>'));
  assert.ok(kind.includes('id="dl-ep-why">Chọn một bản ' + BAIT_TEXT));
  const data = snapshot([], {groups: [group(1, {title: BAIT})]});
  const child = task(31, 'DOWNLOADING', {group_id: 1,
    group: {group_id: 1, ordinal: 2, total: 6, code: 'S01' + BAIT, planned_name: '002 - ' + BAIT}});
  const row = V.row(child, V.createUi(), ctx({data}));
  assert.ok(!row.includes('<img'));
  assert.ok(row.includes('tập 2/6 · S01' + BAIT_TEXT + ' · Tên file dự kiến: 002 - ' + BAIT_TEXT));
  assert.ok(row.includes('data-rename="31" value="' + BAIT_TEXT + '"'), 'the film part in the rename box');
  const ui = V.createUi();
  ui.groupsOpen.add(1);
  ui.members.set(1, {members: [{id: 103, ordinal: 3, code: null, episode_label: 'Tập 3', season_label: 'Mùa ' + BAIT,
    variant_label: 'Bản ' + BAIT, status: 'CREATED', task_id: 33, task_state: 'QUEUED'}]});
  const card = V.groupCard(group(1), ui, ctx());
  assert.ok(!card.includes('<img')); assert.ok(card.includes('Tập 3 · Mùa ' + BAIT_TEXT + ' · Bản ' + BAIT_TEXT));
  ui.members.set(1, {members: [], error: 'Không tải được danh sách tập: ' + BAIT});
  const failed = V.groupCard(group(1), ui, ctx());
  assert.ok(!failed.includes('<img')); assert.ok(failed.includes('Không tải được danh sách tập: ' + BAIT_TEXT));
  const panel = V.accountsPanel(snapshot([], {accounts: {sources: [source('phim-a')], problems: [], problem_text: '',
    error: 'Không đọc được cấu hình nguồn: ' + BAIT}}), V.createUi(), ctx({device: 'pc'}));
  assert.ok(!panel.includes('<img')); assert.ok(panel.includes('Không đọc được cấu hình nguồn: ' + BAIT_TEXT));
});

test('escaped: a server message in the dialog; the confirm dialogs of a task, a group and Ngắt kết nối', async () => {
  let gets = 0;
  const d = dialogFor({get: async () => {
    gets++;
    if (gets === 1) throw fail(500, 'INTERNAL', {}, 'Lỗi ' + BAIT);
    return single();
  }, draft: async () => { throw fail(500, 'INTERNAL', {}, 'Hỏng ' + BAIT); }});
  const page = () => d.doc.root.html;
  d.ep.open(18);
  await until(() => page().includes('Không tải được danh sách tập'));
  assert.ok(!page().includes('<img')); assert.ok(page().includes('Không tải được danh sách tập: Lỗi ' + BAIT_TEXT));
  d.ep.click({}, 'dl-ep-reload');
  await until(() => !!d.ep.state().model && !d.ep.state().loading);
  d.ep.change(modeEl('all'));
  await until(() => page().includes('Chưa lưu được lựa chọn'));
  assert.ok(!page().includes('<img')); assert.ok(page().includes('Chưa lưu được lựa chọn: Hỏng ' + BAIT_TEXT));
  const data = snapshot([task(7, 'DOWNLOADING', {title: 'Phim ' + BAIT, downloaded_bytes: 5})],
    {groups: [group(4, {title: 'Phim ' + BAIT})], accounts: {sources: [source('phim-a', {label: 'Nguồn ' + BAIT,
      state: 'CONNECTED', waiting_tasks: 1})], problems: [], problem_text: '', error: null}});
  const p = livePage(() => ({status: 200, body: {remote: false}}), null, {data});
  await p.store.loadDownloads();
  await p.store.loadPhone();
  p.page.click({dataset: {id: '7', op: 'cancel'}}, 'dl-op');
  p.page.click({dataset: {group: '4', op: 'cancel'}}, 'dl-group-op');
  p.press('disconnect');
  assert.deepEqual(p.modals.map(x => x.label), ['Hủy lượt tải', 'Hủy nhóm', 'Ngắt kết nối']);
  for (const x of p.modals) {
    assert.ok(!x.body.includes('<img'), x.label); assert.ok(x.body.includes(BAIT_TEXT), x.label);
  }
  assert.equal(p.f.posts().length, 0, 'asking sends nothing');
  p.store.stop();
});

const TEST_LIMIT_MS = 10000;
function limited(fn) {
  let timer;
  const limit = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('did not finish within ' + TEST_LIMIT_MS + ' ms')), TEST_LIMIT_MS); });
  return Promise.race([Promise.resolve().then(fn), limit]).finally(() => clearTimeout(timer));
}
process.exitCode = 1; // a run that ends without its summary line (a promise nothing settles) fails
(async () => {
  for (const [name, fn] of tests) {
    try { await limited(fn); } catch (error) { error.message = name + ': ' + error.message; throw error; } finally { delete global.document; }
    passed++;
    process.stdout.write('OK ' + name + '\n');
  }
  process.stdout.write(JSON.stringify({passed, failed: 0}) + '\n');
  process.exitCode = 0;
})().catch(error => {
  process.stderr.write(String(error && error.stack || error) + '\n', () => process.exit(1));
});
