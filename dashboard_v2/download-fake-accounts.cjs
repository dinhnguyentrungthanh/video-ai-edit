'use strict';
/* Source-account part of download-fake-server.cjs (M5, docs/SOURCE_ACCOUNTS_PLAN.md 9.14): sources in several states,
 * series pages with their stored episode lists, groups of episodes and their routes, in memory. Never a browser, a
 * site or a ticket: "Đăng nhập" only flips a state, and only a /__fake/ call ends it. Hosts are .example; some labels
 * carry markup on purpose (it must show as text). The /__fake/ routes exist only here, for the browser check: another
 * device editing the draft, the list changing, a confirm whose answer is lost, a sign-in that ends. The answers keep the
 * backend's shapes (M6: episode names, ITEMS_EXIST); where this file is simpler (when groups fill, which tasks a sign-in
 * wakes) a comment says so. What it shows proves the page only, never the backend. */
const crypto = require('crypto');

const BAIT = '<img src=x onerror="window.__dlXss=1">';
const MB = 1048576;
const MAX_GROUP = 500, MAX_UNFINISHED = 100;
const CLOSED = ['COMPLETED', 'CANCELLED', 'EXPIRED', 'EXPANDED'];
const RUNNING = ['PROBING', 'WAITING_SPACE', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING', 'CANCELLING'];
const STOPPABLE = ['QUEUED', 'PROBING', 'WAITING_SPACE', 'DOWNLOADING', 'WAITING_LOGIN'];
const CANCELLABLE = ['QUEUED', 'PROBING', 'NEEDS_CHOICE', 'WAITING_SPACE', 'DOWNLOADING', 'VERIFYING', 'STOPPED', 'FAILED', 'INTERRUPTED',
  'WAITING_LOGIN'];
const BUCKETS = ['pending', 'held', 'queued', 'waiting_login', 'running', 'completed', 'failed', 'stopped', 'interrupted', 'cancelled',
  'expired', 'removed', 'attention'];
const UNFINISHED = ['pending', 'held', 'queued', 'waiting_login', 'running', 'attention'];
const hash = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');

/* Episode names as the backend gives them (download_episode_names, download_files.sanitize_name): "NNN - <film> - <code>",
 * the ordinal at least 3 digits wide for the whole group, forbidden characters as spaces, only the film part cut; a
 * finished episode keeps its MKV. The reserved device names (CON, NUL…) are left out: no fixture title is one.
 * test_dashboard_v2_downloads.py compares these names with the backend's. */
const NAME_LIMIT = 150, TAKEN_RESERVE = ' (9999)'.length, SEPARATOR = ' - ', MIN_WIDTH = 3, MAX_CODE = 40;
const trimEnd = text => text.replace(/[. ]+$/, '');
function namePart(value, limit) {
  const text = trimEnd(String(value || '').normalize('NFC').replace(/[\\/:*?"<>|\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim());
  return trimEnd(trimEnd(trimEnd(text.replace(/\.mp4$/i, '')).slice(0, NAME_LIMIT - '.mp4'.length)).slice(0, limit));
}
const ordinalWidth = total => Math.max(MIN_WIDTH, String(Math.max(Number(total) || 0, 1)).length);
function plannedName(ordinal, total, film, code) { // planned_stem: the name a free group_target gives, without its extension
  const prefix = String(ordinal).padStart(ordinalWidth(total), '0'), codePart = namePart(code, MAX_CODE) || '?';
  const room = NAME_LIMIT - '.mp4'.length - TAKEN_RESERVE - prefix.length - codePart.length - 2 * SEPARATOR.length;
  const filmPart = room > 0 ? namePart(film, room) : '';
  return trimEnd((filmPart ? [prefix, filmPart, codePart] : [prefix, codePart]).join(SEPARATOR).replace(/\s+/g, ' '));
}
function episodeCode(seasonNumber, number, special, label) { // episode_code: the source's numbers, never a guess
  const two = n => String(n).padStart(2, '0');
  if (Number.isInteger(number)) return (Number.isInteger(seasonNumber) ? 'S' + two(seasonNumber) : '') + (special ? 'SP' : 'E') + two(number);
  const text = namePart(label, MAX_CODE) || '?';
  return special ? 'SP ' + text : text;
}

const V1080 = {kind: 'k1080-vi', label: 'Lồng tiếng', quality: '1080p', audio: 'Tiếng Việt', size: 900 * MB};
const V720 = {kind: 'k720-sub', label: 'Phụ đề', quality: '720p', audio: 'Gốc', size: 450 * MB};
function episode(key, number, label, variants) {
  return {key, number, label, special: false, variants: variants.map((v, i) => ({id: key + '-v' + (i + 1), ...v}))};
}
/* The public listing (download_account_listing.Listing.public): seasons in order, episodes with their order. */
function listing(source, film, title, seasons, extra = {}) {
  let order = 0;
  const groups = seasons.map(s => ({key: s.key, number: s.number, label: s.label, special: !!s.special,
    episodes: s.episodes.map(e => ({...e, special: !!s.special, order: ++order}))}));
  const kinds = {};
  groups.forEach(g => g.episodes.forEach(e => new Map(e.variants.map(v => [v.kind, v])).forEach(v => { // episodes that have the kind
    const k = kinds[v.kind] || (kinds[v.kind] = {kind: v.kind, label: [v.label, v.quality, v.audio].filter(Boolean).join(' · '), episodes: 0});
    k.episodes += 1;
  })));
  const body = {source, film, title, kind: 'series', groups};
  const complete = extra.complete !== false;
  return {...body, complete, reasons: complete ? [] : ['PAGE_LIMIT'], message: complete ? null : extra.message || null, episode_count: order,
    file_count: groups.reduce((n, g) => n + g.episodes.reduce((m, e) => m + e.variants.length, 0), 0), variant_kinds: Object.values(kinds),
    skipped: {}, pages: 1, fingerprint: hash(body)};
}
const episodes = l => l.groups.flatMap(g => g.episodes);

function seriesA() { // two seasons, specials, per-episode variants, one missing and one ambiguous file
  const s1 = Array.from({length: 12}, (_, i) => episode('a-s1e' + (i + 1), i + 1, i === 6 ? 'Tập 7 ' + BAIT : 'Tập ' + (i + 1), [V1080, V720]));
  const s2 = [episode('a-s2e1', 1, 'Tập 1', [V1080, V720]), episode('a-s2e2', 2, 'Tập 2', [V1080, V720]), episode('a-s2e3', 3, 'Tập 3', [V1080])];
  const sp = [episode('a-sp1', 1, 'Đặc biệt 1', [V1080, {...V1080, size: 950 * MB}, V720]), episode('a-sp2', 2, 'Đặc biệt 2', [V1080, V720])];
  return listing('phim-a', 'film-a', '<b>Phim mẫu</b> nhiều tập ' + BAIT, [{key: 's1', number: 1, label: 'Mùa 1', episodes: s1},
    {key: 's2', number: 2, label: 'Mùa 2', episodes: s2}, {key: 'sp', number: null, label: 'Tập đặc biệt', special: true, episodes: sp}]);
}
function seriesB() { // 500 episodes, one file each, the list not complete
  const all = Array.from({length: 500}, (_, i) => episode('b-e' + (i + 1), i + 1, 'Tập ' + (i + 1),
    [{kind: 'k720', label: '720p', quality: '720p', audio: '', size: null}]));
  return listing('phim-a', 'film-b', 'Phim mẫu 500 tập', [{key: '', number: null, label: '', episodes: all}],
    {complete: false, message: 'Đã đọc tới giới hạn 25 trang; có thể còn tập chưa đọc.'});
}
function seriesC() { // 12 episodes, one file each; two of them are already in the list
  const all = Array.from({length: 12}, (_, i) => episode('c-e' + (i + 1), i + 1, 'Tập ' + (i + 1), [V720]));
  return listing('phim-a', 'film-c', 'Phim mẫu 12 tập', [{key: '1', number: 1, label: 'Mùa 1', episodes: all}]);
}

/* plan_selection: what "Tải N tập" would start (count, missing/ambiguous, label, scope); throws {status, body} like GroupError. */
function plan(l, sel) {
  const all = episodes(l), byKey = new Map(all.map(e => [e.key, e]));
  const unknown = (sel.episodes || []).filter(k => !byKey.has(k));
  if (sel.variants) Object.entries(sel.variants).forEach(([k, v]) => { if (!byKey.has(k) || !byKey.get(k).variants.some(x => x.id === v)) unknown.push(k); });
  if (sel.variant_kind && !l.variant_kinds.some(k => k.kind === sel.variant_kind)) unknown.push(sel.variant_kind);
  if (unknown.length) throw {status: 400, body: {error: 'Có mã tập hoặc mã bản không thuộc danh sách đã lưu.', code: 'BAD_SELECTION', unknown: unknown.slice(0, 20)}};
  const chosen = sel.mode === 'all' ? all : all.filter(e => sel.episodes.includes(e.key));
  if (!chosen.length) throw {status: 400, body: {error: 'Chọn ít nhất một tập.', code: 'BAD_SELECTION'}};
  if (!sel.variant_kind && !sel.variants && chosen.some(e => e.variants.length !== 1)) {
    throw {status: 400, body: {error: 'Chọn một bản (chất lượng, âm thanh) cho các tập.', code: 'BAD_SELECTION'}};
  }
  const missing = [], ambiguous = [], files = new Map(); // files: episode key → its one chosen variant
  chosen.forEach(e => {
    const hits = sel.variant_kind ? e.variants.filter(v => v.kind === sel.variant_kind)
      : sel.variants ? e.variants.filter(v => v.id === sel.variants[e.key]) : e.variants;
    if (!hits.length) missing.push({episode: e.key, label: e.label});
    else if (hits.length > 1) ambiguous.push({episode: e.key, label: e.label});
    else files.set(e.key, hits[0]);
  });
  const count = chosen.length;
  return {mode: sel.mode, count, complete: l.complete, confirm_label: l.complete ? 'Tải ' + count + ' tập' : 'Tải ' + count + ' tập đã thấy',
    note: l.complete ? null : 'Danh sách chưa đầy đủ: chỉ có các tập đã thấy.', max: MAX_GROUP, too_large: count > MAX_GROUP, missing, ambiguous,
    keys: chosen.map(e => e.key), files};
}
const publicPlan = p => { const {keys, files, ...rest} = p; return rest; };
function parseSelection(value) {
  const bad = why => ({status: 400, body: {error: 'Lựa chọn tập không hợp lệ: ' + why + '.', code: 'BAD_SELECTION'}});
  if (!value || typeof value !== 'object' || Object.keys(value).some(k => !['mode', 'episodes', 'variant_kind', 'variants'].includes(k))) throw bad('cần mode');
  if (!['all', 'pick'].includes(value.mode)) throw bad('mode');
  const list = value.episodes === undefined || value.episodes === null ? [] : value.episodes;
  if (!Array.isArray(list) || new Set(list).size !== list.length || list.some(k => typeof k !== 'string')) throw bad('episodes');
  if (value.mode === 'all' && list.length) throw bad('mode "all" không kèm danh sách tập');
  if (value.variant_kind != null && value.variants != null) throw bad('chỉ chọn variant_kind hoặc variants');
  return {mode: value.mode, episodes: list, variant_kind: value.variant_kind ?? null, variants: value.variants ?? null};
}

/* io: task(id, state, extra) builds a row, tasks() the current list, push(row), removeTasks(Set of ids), nextId(),
 * phone (the phone listener), accounts: undefined | 'none' (no source configured) | 'old' (a backend without M4) | 'problem'. */
function create(io) {
  const now = Date.now(), iso = ms => new Date(ms).toISOString();
  const sources = io.accounts === 'none' ? [] : [
    {id: 'phim-a', label: 'Nguồn phim A', state: 'CONNECTED', session_check: 'ttl', authenticated_at: iso(now - 10 * 60000),
      recheck_at: iso(now + 50 * 60000), checked_at: null, error_code: null, message: null, login_supported: true, reader_supported: true,
      login_running: false, last_login: {code: 'CONNECTED', message: 'Đã kết nối.', connected: true, profile_left: false, failure: null, hang: null,
        at: iso(now - 10 * 60000)}, warnings: []},
    {id: 'phim-b', label: 'Nguồn phim B ' + BAIT, state: 'NEEDS_LOGIN', session_check: 'ttl', authenticated_at: iso(now - 130 * 60000),
      recheck_at: null, checked_at: null, error_code: 'SESSION_EXPIRED', message: 'Đã quá 1 giờ từ lần đăng nhập; cần đăng nhập lại.',
      login_supported: true, reader_supported: true, login_running: false,
      last_login: {code: 'LOGIN_WINDOW_CLOSED', message: 'Cửa sổ đăng nhập đã đóng trước khi đăng nhập xong.', connected: false, profile_left: true,
        failure: null, hang: null, at: iso(now - 125 * 60000)},
      warnings: [{code: 'SESSION_SAVE_FAILED', message: 'Không lưu được phiên mới lần trước ' + BAIT, at: iso(now - 126 * 60000)}]},
    {id: 'phim-c', label: 'Nguồn phim C', state: 'NOT_CONNECTED', session_check: 'ttl', authenticated_at: null, recheck_at: null, checked_at: null,
      error_code: null, message: null, login_supported: false, reader_supported: false, login_running: false, last_login: null, warnings: []},
    {id: 'phim-d', label: 'Nguồn phim D', state: 'CHECK_FAILED', session_check: 'live', authenticated_at: iso(now - 30 * 60000), recheck_at: null,
      checked_at: iso(now - 60000), error_code: 'CHECK_UNREACHABLE', message: 'Không kiểm tra được kết nối (lỗi mạng); phiên chưa bị coi là hết hạn.',
      login_supported: true, reader_supported: true, login_running: false, last_login: null, warnings: []},
  ];
  const previews = new Map(), groups = new Map(), requests = new Map();
  const taken = new Map([['c-e2', {task_id: 37, state: 'DOWNLOADING'}], ['c-e3', {group_id: 3, state: 'PENDING'}]]);
  const control = {dropNextConfirm: null}; // 'cut' (the answer stops after its headers) or 'reset' (no answer at all)
  const t = (id, state, extra) => io.task(id, state, extra);
  const taskById = id => io.tasks().find(x => x.id === Number(id));

  function addGroup(id, parent, title, state, members) {
    groups.set(id, {id, parent_task_id: parent, source_id: 'phim-a', source_label: 'Nguồn phim A', title, state, mode: 'all', complete: true,
      note: null, reasons: [], total: members.length, existing: [], created_at: iso(now - 3600000),
      members: members.map((m, i) => ({id: id * 100 + i + 1, ordinal: i + 1, code: episodeCode(1, i + 1, false, ''),
        episode_label: 'Tập ' + (i + 1), season_label: 'Mùa 1', episode_number: i + 1, season_number: 1, special: false,
        variant_label: V720.label, status: m.status || 'CREATED', task_id: m.task || null, last_state: null}))});
  }
  /* Seed: three series pages waiting for a choice, three groups (one cancelled), tasks waiting for a sign-in. */
  function seed() {
    previews.set(18, {listing: seriesA(), draft: null, revision: 0});
    previews.set(19, {listing: seriesB(), draft: null, revision: 0});
    previews.set(22, {listing: seriesC(), draft: null, revision: 0});
    const media = {source_label: 'Nguồn phim A', provider: 'phim-a'};
    const page = (id, title, url) => t(id, 'NEEDS_CHOICE', {url, title: null, original_title: title, choice_kind: 'episodes', media});
    addGroup(1, 20, 'Phim mẫu đã tách <i>nhóm</i>', 'ACTIVE', [{task: 30}, {task: 31}, {task: 32}, {task: 33}, {task: 34}, {status: 'PENDING'}]);
    addGroup(2, 21, 'Phim mẫu đã hủy', 'CANCELLED', [{task: 35}, {task: 36}, {status: 'CANCELLED'}]);
    addGroup(4, 23, 'Phim mẫu 3 tập', 'ACTIVE', [{task: 37}, {task: 38}, {task: 39}]);
    const done = (g, ordinal) => { // the name the worker gives a finished episode (group_target), MKV kept
      const own = groups.get(g), m = own.members[ordinal - 1];
      return {downloaded_bytes: 450 * MB, total_bytes: 450 * MB, output_size: 450 * MB, name_locked: true,
        output_name: plannedName(ordinal, own.total, own.title, m.code) + '.mkv'};
    };
    const child = (id, g, ordinal, state, extra) => t(id, state, {url: 'https://phim-a.example/phim/nhom-' + g, title: null, desired_name: null,
      original_title: groups.get(g).title + ' · Tập ' + ordinal, group_id: g, media, ...extra});
    const parent = (id, title, url) => t(id, 'EXPANDED', {url, original_title: title, title: null, choice_kind: 'episodes', media});
    return [
      page(18, previews.get(18).listing.title, 'https://phim-a.example/phim/nhieu-tap'),
      page(19, 'Phim mẫu 500 tập', 'https://phim-a.example/phim/500-tap'),
      parent(20, 'Phim mẫu đã tách', 'https://phim-a.example/phim/da-tach'),
      parent(21, 'Phim mẫu đã hủy', 'https://phim-a.example/phim/da-huy'),
      page(22, 'Phim mẫu 12 tập', 'https://phim-a.example/phim/12-tap'),
      parent(23, 'Phim mẫu 3 tập', 'https://phim-a.example/phim/3-tap'),
      t(24, 'WAITING_LOGIN', {url: 'https://phim-b.example/xem/1', original_title: 'Phim lẻ nguồn B', login_source: 'phim-b',
        login_reason: 'SESSION_EXPIRED', error_code: 'SOURCE_LOGIN_REQUIRED', media: {source_label: 'Nguồn phim B', provider: 'phim-b'},
        error_message: 'Chờ đăng nhập Nguồn phim B: Đã quá 1 giờ từ lần đăng nhập; cần đăng nhập lại. Bấm Đăng nhập ở trang Tải video trên PC.'}),
      t(25, 'WAITING_LOGIN', {url: 'https://phim-a.example/xem/2', original_title: 'Phim lẻ của tài khoản Windows khác', login_source: 'phim-a',
        login_reason: 'OTHER_ACCOUNT', error_code: 'SOURCE_LOGIN_REQUIRED', media, error_message: 'Chờ tài khoản Windows đã thêm lượt này ' +
        '(Nguồn phim A): lượt này được thêm khi BiliFlow chạy bằng tài khoản Windows khác; chỉ tài khoản đó dùng được phiên đăng nhập của nó.'}),
      child(30, 1, 1, 'COMPLETED', done(1, 1)),
      child(31, 1, 2, 'DOWNLOADING', {downloaded_bytes: 300 * MB, total_bytes: 450 * MB, speed: 4 * MB, eta: 40}),
      child(32, 1, 3, 'FAILED', {estimated_bytes: null, error_code: 'SOURCE_CHANGED', error_message: 'Nguồn đã đổi file của tập này; dán lại trang phim. ' + BAIT}),
      child(33, 1, 4, 'COMPLETED', done(1, 4)),
      child(34, 1, 5, 'QUEUED', {estimated_bytes: null}), // sizes not known yet: no percent for group 1
      child(35, 2, 1, 'COMPLETED', done(2, 1)),
      child(36, 2, 2, 'CANCELLED'),
      child(37, 4, 1, 'COMPLETED', done(4, 1)),
      child(38, 4, 2, 'DOWNLOADING', {downloaded_bytes: 225 * MB, total_bytes: 450 * MB, speed: 3 * MB, eta: 75}),
      child(39, 4, 3, 'WAITING_LOGIN', {estimated_bytes: 450 * MB, login_source: 'phim-b', login_reason: 'SESSION_EXPIRED',
        error_code: 'SOURCE_LOGIN_REQUIRED', error_message: 'Chờ đăng nhập Nguồn phim B: Đã quá 1 giờ từ lần đăng nhập; cần đăng nhập lại.'}),
    ];
  }

  function bucket(member) {
    if (member.status === 'PENDING') return 'pending';
    if (member.status === 'HELD') return 'held';
    if (member.status === 'CANCELLED') return 'cancelled';
    const task = member.task_id ? taskById(member.task_id) : null, state = task ? task.state : member.last_state;
    if (!state) return 'removed';
    if (RUNNING.includes(state)) return task ? 'running' : 'removed';
    return {QUEUED: 'queued', WAITING_LOGIN: 'waiting_login', COMPLETED: 'completed', FAILED: 'failed', STOPPED: 'stopped',
      INTERRUPTED: 'interrupted', CANCELLED: 'cancelled', EXPIRED: 'expired'}[state] || (task ? 'attention' : 'removed');
  }
  /* download_groups.summarize: a percent only when every member still in the group has a known size. */
  function summary(g, withExisting) {
    const counts = Object.fromEntries(BUCKETS.map(k => [k, 0]));
    let sized = true, size = 0, done = 0;
    g.members.forEach(m => {
      const b = bucket(m);
      counts[b] += 1;
      if (['cancelled', 'expired', 'removed'].includes(b)) return;
      const task = m.task_id ? taskById(m.task_id) : null;
      const s = task ? (task.state === 'COMPLETED' ? task.output_size : task.total_bytes || task.estimated_bytes) : null;
      if (!s) { sized = false; return; }
      size += s;
      done += task.state === 'COMPLETED' ? s : Math.min(task.downloaded_bytes || 0, s);
    });
    const live = g.total - counts.cancelled - counts.expired - counts.removed;
    const percent = sized && live > 0 && size > 0 ? (counts.completed === live ? 100 : Math.min(99, Math.floor(100 * done / size))) : null;
    const {members, existing, ...rest} = g;
    return {...rest, done: counts.completed, counts, percent, finished: !UNFINISHED.some(k => counts[k]),
      existing_count: existing.length, existing: withExisting ? existing : null};
  }
  /* The filler: members waiting for room become tasks, in order, while fewer than 100 tasks are unfinished. The backend
   * runs it in each dispatch pass of the worker (download_account_tasks._account_upkeep), with or without a page open;
   * here it runs on GET /api/downloads and after a confirm or a group action. The page only ever sees it through the
   * list it polls, so only the timing differs: no state or count is shown that the backend would not give. */
  function fill() {
    const unfinished = () => io.tasks().filter(x => !CLOSED.includes(x.state)).length;
    for (const g of groups.values()) {
      if (g.state !== 'ACTIVE') continue;
      for (const m of g.members) {
        if (m.status !== 'PENDING' || unfinished() >= MAX_UNFINISHED) continue;
        const id = io.nextId();
        io.push(t(id, 'QUEUED', {url: 'https://phim-a.example/phim/' + g.parent_task_id, title: null, original_title: g.title + ' · ' + m.episode_label,
          estimated_bytes: null, group_id: g.id, media: {source_label: g.source_label, provider: g.source_id}}));
        Object.assign(m, {status: 'CREATED', task_id: id});
      }
    }
  }
  function decorate(task) {
    if (io.accounts === 'old') return task;
    const out = {...task};
    const p = task.state === 'NEEDS_CHOICE' && task.choice_kind === 'episodes' ? previews.get(task.id) : null;
    out.episodes = p ? {title: p.listing.title, kind: p.listing.kind, episode_count: p.listing.episode_count, complete: p.listing.complete,
      message: p.listing.message, fingerprint: p.listing.fingerprint, revision: p.revision, has_draft: !!p.draft} : null;
    out.choice_kind = task.state === 'NEEDS_CHOICE' ? task.choice_kind || 'entries' : null;
    const g = task.group_id ? groups.get(task.group_id) : null, m = g && g.members.find(x => x.task_id === task.id);
    out.group = m ? {group_id: g.id, ordinal: m.ordinal, total: g.total, code: m.code, // download_groups.names_by_task
      planned_name: plannedName(m.ordinal, g.total, task.desired_name || g.title, m.code)} : null;
    out.login_source = task.login_source || null;
    out.login_reason = task.login_reason || null;
    return out;
  }
  function snapshotExtras() {
    if (io.accounts === 'old') return {};
    fill();
    const waiting = {};
    io.tasks().filter(x => x.state === 'WAITING_LOGIN').forEach(x => { waiting[x.login_source] = (waiting[x.login_source] || 0) + 1; });
    return {groups: [...groups.values()].map(g => summary(g, false)),
      accounts: {sources: sources.map(s => { const {before, ...rest} = s; return {...rest, waiting_tasks: waiting[s.id] || 0}; }),
        problems: io.accounts === 'problem' ? [{source: 'phim-x', reason: 'host'}] : [],
        problem_text: io.accounts === 'problem' ? 'Nguồn "phim-x": host phải là tên miền đầy đủ ' + BAIT : '', error: null}};
  }

  function waitingPage(id) {
    const task = taskById(id), p = previews.get(Number(id));
    if (task && task.state === 'NEEDS_CHOICE' && p) return p;
    const g = [...groups.values()].find(x => x.parent_task_id === Number(id));
    throw {status: task ? 409 : 404, body: {error: task ? 'Lượt này không còn chờ chọn tập.' : 'Không thấy lượt tải.', code: task ? 'NOT_WAITING' : 'NOT_FOUND',
      group_id: g ? g.id : null, state: task ? task.state : null}};
  }
  function planOrError(l, selection) {
    if (!selection) return [null, null];
    try { return [publicPlan(plan(l, selection)), null]; } catch (e) { return [null, e.body]; }
  }
  function memberRow(g, x) {
    const task = x.task_id ? taskById(x.task_id) : null, pick = name => task ? task[name] ?? null : null;
    return {...x, group_id: g.id, task_state: pick('state'), downloaded_bytes: pick('downloaded_bytes'), total_bytes: pick('total_bytes'),
      estimated_bytes: pick('estimated_bytes'), output_size: pick('output_size'), desired_name: pick('desired_name'), error_code: pick('error_code'),
      error_message: pick('error_message'), login_source: pick('login_source'), login_reason: pick('login_reason')};
  }
  /* GET routes: [status, body] or null (not ours). */
  function handleGet(path) {
    if (io.accounts === 'old') return null;
    let m = path.match(/^\/api\/downloads\/(\d+)\/episodes$/);
    if (m) {
      try {
        const p = waitingPage(m[1]), [planned, problem] = planOrError(p.listing, p.draft);
        return [200, {task_id: Number(m[1]), listing: p.listing, fingerprint: p.listing.fingerprint, draft: p.draft, revision: p.revision,
          plan: planned, plan_error: problem, max_episodes: MAX_GROUP}];
      } catch (e) { if (e && e.status) return [e.status, e.body]; throw e; }
    }
    m = path.match(/^\/api\/downloads\/groups\/(\d+)$/);
    if (!m) return null;
    const g = groups.get(Number(m[1]));
    return g ? [200, {group: summary(g, true), members: g.members.map(x => memberRow(g, x))}] : [404, {error: 'Không thấy nhóm tập.', code: 'NOT_FOUND'}];
  }

  function confirm(id, body) {
    const key = body.idempotency_key;
    if (typeof key !== 'string' || !/^[A-Za-z0-9_-]{8,64}$/.test(key)) throw {status: 400, body: {error: 'Thiếu khóa yêu cầu.', code: 'BAD_REQUEST_KEY'}};
    const selection = parseSelection(body.selection);
    const digest = hash([Number(id), selection, body.fingerprint, !!body.skip_existing]); // download_groups.request_hash: not confirm_scope
    if (requests.has(key)) {
      const r = requests.get(key);
      if (r.digest !== digest) throw {status: 409, body: {error: 'Khóa yêu cầu này đã dùng cho một lựa chọn khác.', code: 'IDEMPOTENCY_CONFLICT'}};
      return {group: summary(groups.get(r.group), true), replay: true, existing: groups.get(r.group).existing};
    }
    const p = waitingPage(id), l = p.listing;
    if (body.fingerprint !== l.fingerprint) throw {status: 409, body: {error: 'Danh sách tập đã đổi; mở lại danh sách rồi chọn lại.', code: 'STALE_PREVIEW'}};
    const planned = plan(l, selection);
    const list = x => ({episodes: x.slice(0, 50), episode_count: x.length});
    if (planned.missing.length) throw {status: 400, body: {error: 'Có tập không có bản đã chọn.', code: 'VARIANT_MISSING', ...list(planned.missing)}};
    if (planned.ambiguous.length) throw {status: 400, body: {error: 'Có tập có hai file cùng bản.', code: 'VARIANT_AMBIGUOUS', ...list(planned.ambiguous)}};
    if (planned.count > MAX_GROUP) throw {status: 400, body: {error: 'Một nhóm tối đa 500 tập; đang chọn ' + planned.count + '.', code: 'GROUP_TOO_LARGE',
      count: planned.count}};
    if (!l.complete && body.confirm_scope !== true) throw {status: 400, body: {error: 'Danh sách chưa đầy đủ; xác nhận tải các tập đã thấy.',
      code: 'SCOPE_NOT_CONFIRMED', count: planned.count, confirm_label: planned.confirm_label}};
    const byKey = new Map(episodes(l).map(e => [e.key, e])), seasonOf = new Map(l.groups.flatMap(s => s.episodes.map(e => [e.key, s])));
    // download_groups._new_items: episodes already listed are refused unless skip_existing, and with it too when none is
    // left (never an empty group); the same code both times, only the message and the count differ.
    const existing = planned.keys.filter(k => taken.has(k)).map(k => ({episode: k, label: byKey.get(k).label, ...taken.get(k)}));
    const listed = error => ({status: 409, body: {error, code: 'ITEMS_EXIST', existing: existing.slice(0, 50), existing_count: existing.length}});
    if (existing.length && body.skip_existing !== true) throw listed('Có tập đã nằm trong danh sách tải; bỏ chọn các tập đó hoặc tải phần còn lại.');
    const keys = planned.keys.filter(k => !taken.has(k)), gid = Math.max(0, ...groups.keys()) + 1;
    if (!keys.length) throw listed('Mọi tập đã chọn đều đã nằm trong danh sách tải.');
    const member = (k, i) => { // download_groups._write_group
      const e = byKey.get(k), s = seasonOf.get(k), season = s && !s.special ? s.number : null;
      return {id: gid * 1000 + i + 1, ordinal: i + 1, code: episodeCode(season, e.number, e.special, e.label), episode_label: e.label,
        season_label: s ? s.label : null, episode_number: e.number, season_number: season, special: !!e.special,
        variant_label: planned.files.get(k).label, status: 'PENDING', task_id: null, last_state: null};
    };
    groups.set(gid, {id: gid, parent_task_id: Number(id), source_id: l.source, source_label: 'Nguồn phim A', title: l.title, state: 'ACTIVE',
      mode: selection.mode, complete: l.complete, note: planned.note, reasons: l.complete ? [] : [l.message], total: keys.length, existing,
      created_at: new Date().toISOString(), members: keys.map(member)});
    requests.set(key, {digest, group: gid});
    Object.assign(taskById(id), {state: 'EXPANDED'});
    previews.delete(Number(id));
    fill();
    return {group: summary(groups.get(gid), true), replay: false, existing};
  }
  function draft(id, body) {
    const p = waitingPage(id), selection = parseSelection(body.selection);
    try { plan(p.listing, selection); } catch (e) { if (e.body && e.body.unknown) throw e; }
    if (body.fingerprint !== p.listing.fingerprint) throw {status: 409, body: {error: 'Danh sách tập đã đổi; mở lại danh sách rồi chọn lại.', code: 'STALE_PREVIEW'}};
    if (body.revision !== p.revision) throw {status: 409, body: {error: 'Lựa chọn vừa được sửa ở nơi khác; tải lại danh sách.', code: 'STALE_DRAFT',
      revision: p.revision}};
    p.draft = selection;
    p.revision += 1;
    const [planned, problem] = planOrError(p.listing, selection);
    return {task_id: Number(id), revision: p.revision, draft: selection, plan: planned, plan_error: problem};
  }
  function groupAction(g, op) {
    if (op === 'remove') {
      if (!summary(g, false).finished) throw {status: 409, body: {error: 'Nhóm còn tập chưa tải xong hoặc đang chờ; bấm Hủy nhóm trước ' +
        '(tập đã tải xong vẫn ở trong input).', code: 'GROUP_ACTIVE'}};
      io.removeTasks(new Set(g.members.map(m => m.task_id).filter(Boolean).concat([g.parent_task_id])));
      groups.delete(g.id);
      return {group_id: g.id, removed: true, freed_bytes: 0};
    }
    if (g.state === 'CANCELLED') throw {status: 409, body: {error: 'Nhóm này đã hủy; dán lại trang phim để chọn tập.', code: 'GROUP_CANCELLED'}};
    const tasks = g.members.map(m => m.task_id && taskById(m.task_id)).filter(Boolean);
    const set = (task, state) => Object.assign(task, {state, speed: null, eta: null});
    const members = (from, to) => g.members.forEach(m => { if (from.includes(m.status)) m.status = to; });
    if (op === 'stop') { members(['PENDING'], 'HELD'); tasks.filter(x => STOPPABLE.includes(x.state)).forEach(x => set(x, 'STOPPED')); }
    if (op === 'resume') { members(['HELD'], 'PENDING'); tasks.filter(x => ['STOPPED', 'INTERRUPTED'].includes(x.state)).forEach(x => set(x, 'QUEUED')); }
    if (op === 'retry') tasks.filter(x => ['FAILED', 'EXPIRED'].includes(x.state)).forEach(x => Object.assign(set(x, 'QUEUED'), {error_code: null, error_message: null}));
    if (op === 'cancel') {
      g.state = 'CANCELLED';
      members(['PENDING', 'HELD'], 'CANCELLED');
      tasks.filter(x => CANCELLABLE.includes(x.state)).forEach(x => set(x, 'CANCELLED'));
    }
    fill();
    return {group: summary(g, true)};
  }
  function account(id, op) {
    if (io.phone) return [403, {error: 'Chỉ làm trên PC: đăng nhập, hủy đăng nhập và ngắt kết nối tài khoản nguồn phim chỉ làm trên PC.', code: 'pc_only'}];
    const s = sources.find(x => x.id === id);
    if (!s) return [404, {error: 'Nguồn này chưa được cấu hình.', code: 'ACCOUNT_UNKNOWN'}];
    const view = () => { const {before, ...rest} = s; return rest; };
    if (op === 'login') {
      if (!s.login_supported) return [409, {error: 'BiliFlow chưa biết cách xác nhận đăng nhập của nguồn này; chưa mở cửa sổ.', code: 'LOGIN_UNSUPPORTED'}];
      if (s.login_running) return [409, {error: 'Cửa sổ đăng nhập của nguồn này đang mở.', code: 'LOGIN_BUSY'}];
      Object.assign(s, {before: s.state, login_running: true, state: 'LOGGING_IN'});
      return [202, {source: view(), login: 'STARTED'}];
    }
    if (op === 'cancel-login') {
      const was = s.login_running;
      if (was) Object.assign(s, {login_running: false, state: s.before, last_login: {code: 'LOGIN_CANCELLED', message: 'Đã hủy đăng nhập; phiên cũ (nếu có) giữ nguyên.',
        connected: false, profile_left: false, failure: null, hang: null, at: new Date().toISOString()}});
      return [200, {cancelled: was, source: view()}];
    }
    Object.assign(s, {login_running: false, state: 'NOT_CONNECTED', authenticated_at: null, recheck_at: null, checked_at: null, error_code: null, message: null});
    return [200, {source: view()}];
  }
  /* The end of a sign-in (/__fake/login-result). Simpler than the backend, which wakes only the tasks a newer session of
   * the same source and Windows account can use (generation and SID): here every waiting task of the source except
   * OTHER_ACCOUNT goes back to the queue. The page reads only the states. */
  function finishLogin(id, ok) {
    const s = sources.find(x => x.id === id);
    if (!s || !s.login_running) return [409, {error: 'no sign-in'}];
    const at = new Date().toISOString();
    if (ok) {
      Object.assign(s, {login_running: false, state: 'CONNECTED', authenticated_at: at, recheck_at: new Date(Date.now() + 3600000).toISOString(),
        error_code: null, message: null, last_login: {code: 'CONNECTED', message: 'Đã kết nối.', connected: true, profile_left: false, failure: null, hang: null, at}});
      io.tasks().filter(x => x.state === 'WAITING_LOGIN' && x.login_source === id && x.login_reason !== 'OTHER_ACCOUNT')
        .forEach(x => Object.assign(x, {state: 'QUEUED', error_code: null, error_message: null, login_source: null, login_reason: null}));
    } else {
      Object.assign(s, {login_running: false, state: s.before, last_login: {code: 'LOGIN_WINDOW_CLOSED',
        message: 'Cửa sổ đăng nhập đã đóng trước khi đăng nhập xong.', connected: false, profile_left: false, failure: null, hang: null, at}});
    }
    return [200, {state: s.state}];
  }
  /* POST routes: [status, body], {drop: 'cut' | 'reset'} (done, the answer lost on the way) or null (not ours). */
  function handlePost(path, body) {
    if (io.accounts === 'old') return null;
    try {
      let m = path.match(/^\/api\/downloads\/(\d+)\/episodes\/(draft|confirm)$/);
      if (m && m[2] === 'draft') return [200, draft(m[1], body)];
      if (m) {
        const answer = confirm(m[1], body);
        if (!control.dropNextConfirm) return [200, answer];
        const drop = control.dropNextConfirm;
        control.dropNextConfirm = null;
        return {drop};
      }
      m = path.match(/^\/api\/downloads\/groups\/(\d+)\/(stop|resume|cancel|retry|remove)$/);
      if (m) {
        const g = groups.get(Number(m[1]));
        return g ? [200, groupAction(g, m[2])] : [404, {error: 'Không thấy nhóm tập.', code: 'NOT_FOUND'}];
      }
      m = path.match(/^\/api\/download-accounts\/([a-z0-9][a-z0-9-]{0,39})\/(login|cancel-login|disconnect)$/);
      return m ? account(m[1], m[2]) : null;
    } catch (error) {
      if (error && error.status) return [error.status, error.body];
      throw error;
    }
  }
  /* A task action the account work changes: an episode of a cancelled group never resumes; a split page removes its group. */
  function guard(task, op) {
    const g = task.group_id ? groups.get(task.group_id) : null;
    if (g && g.state === 'CANCELLED' && ['resume', 'retry'].includes(op)) {
      return [409, {error: 'Tập này thuộc nhóm tập #' + g.id + ' đã hủy nên không tải tiếp được; dán lại trang phim rồi chọn tập để tải lại.'}];
    }
    if (task.state === 'EXPANDED') {
      const own = [...groups.values()].find(x => x.parent_task_id === task.id);
      if (op !== 'remove') return [409, {error: 'Trang phim đã tách thành nhóm tập; dùng các nút của nhóm.'}];
      if (!own) { io.removeTasks(new Set([task.id])); return [200, {id: task.id, removed: true, freed_bytes: 0}]; }
      try { return [200, groupAction(own, 'remove')]; } catch (e) { return [e.status, e.body]; }
    }
    if (op === 'choose' && task.choice_kind === 'episodes') return [409, {error: 'Trang phim nhiều tập: dùng "Chọn tập".', code: 'NOT_ENTRIES'}];
    return null;
  }
  /* /__fake/…: the browser check's hooks (another device, a changed list, a lost answer, the end of a sign-in). */
  function fakeControl(path, query) {
    let m = path.match(/^\/__fake\/bump-draft\/(\d+)$/);
    if (m && previews.has(Number(m[1]))) {
      const p = previews.get(Number(m[1]));
      p.draft = {mode: 'pick', episodes: [episodes(p.listing)[0].key], variant_kind: p.listing.variant_kinds[0].kind, variants: null};
      p.revision += 1;
      return [200, {revision: p.revision}];
    }
    m = path.match(/^\/__fake\/change-listing\/(\d+)$/);
    if (m && previews.has(Number(m[1]))) {
      const p = previews.get(Number(m[1]));
      p.listing = {...p.listing, fingerprint: hash([p.listing.fingerprint, Date.now()])};
      p.draft = null;
      p.revision += 1;
      return [200, {fingerprint: p.listing.fingerprint}];
    }
    if (path === '/__fake/drop-next-confirm') { control.dropNextConfirm = query.get('mode') === 'reset' ? 'reset' : 'cut'; return [200, {ok: true}]; }
    m = path.match(/^\/__fake\/login-result\/([a-z0-9-]+)$/);
    if (m) return finishLogin(m[1], query.get('ok') === '1');
    if (path === '/__fake/groups') return [200, {groups: [...groups.values()].map(g => ({id: g.id, total: g.total, parent: g.parent_task_id, state: g.state}))}];
    return null;
  }
  return {seed, decorate, snapshotExtras, handleGet, handlePost, guard, fakeControl};
}

module.exports = {create, plan, listing, BAIT};
