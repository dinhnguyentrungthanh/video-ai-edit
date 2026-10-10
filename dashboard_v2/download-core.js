/* "Tải video" (live) without DOM or HTTP: state labels, the buttons a task offers, progress text,
 * filters and counts. The state sets mirror src/biliflow/download_worker.py; verify-download.cjs and
 * tests/test_dashboard_v2_downloads.py check that they stay equal. The backend decides; a button
 * here only avoids sending what the backend would refuse (409).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BFDownloadCore = api;
})(typeof window === 'undefined' ? this : window, function () {
  'use strict';
  /* WAITING_LOGIN and EXPANDED come from source accounts (docs/SOURCE_ACCOUNTS_PLAN.md 9.14): a task waiting for a
   * sign-in, and a series page split into a group of episode tasks (its Xóa removes the whole group). */
  const STATES = ['QUEUED', 'PROBING', 'NEEDS_CHOICE', 'WAITING_SPACE', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING',
    'COMPLETED', 'STOPPED', 'FAILED', 'CANCELLING', 'CANCELLED', 'INTERRUPTED', 'EXPIRED', 'WAITING_LOGIN', 'EXPANDED'];
  const LABELS = {QUEUED: 'Chờ tải', PROBING: 'Đang thăm dò', NEEDS_CHOICE: 'Cần chọn video', WAITING_SPACE: 'Chờ chỗ trống',
    DOWNLOADING: 'Đang tải', VERIFYING: 'Đang kiểm tra file', PUBLISHING: 'Đang chuyển vào input', COMPLETED: 'Đã vào input',
    STOPPED: 'Đã dừng', FAILED: 'Lỗi', CANCELLING: 'Đang hủy', CANCELLED: 'Đã hủy', INTERRUPTED: 'Bị ngắt',
    EXPIRED: 'Đã dọn file tạm', WAITING_LOGIN: 'Chờ đăng nhập', EXPANDED: 'Đã tách thành nhóm tập'};
  const TONES = {DOWNLOADING: 'blue', PROBING: 'blue', VERIFYING: 'blue', PUBLISHING: 'blue', CANCELLING: 'grey',
    NEEDS_CHOICE: 'amber', WAITING_SPACE: 'amber', INTERRUPTED: 'amber', FAILED: 'red', COMPLETED: '', QUEUED: 'grey',
    STOPPED: 'grey', CANCELLED: 'grey', EXPIRED: 'grey', WAITING_LOGIN: 'amber', EXPANDED: 'grey'};
  const without = out => STATES.filter(s => !out.includes(s));
  /* A state from the server is looked up only as an own key ("constructor" is not a state). */
  const own = (table, key) => typeof key === 'string' && Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
  /* download_worker.py: STOPPABLE, RESUMABLE, CANCELLABLE, RETRYABLE, RENAMABLE; download_store: FINAL_STATES and
   * SLOT_STATES (RUNNING here, the "Đang chạy" group). */
  const STOPPABLE = ['QUEUED', 'PROBING', 'WAITING_SPACE', 'DOWNLOADING', 'WAITING_LOGIN'];
  const RESUMABLE = ['STOPPED', 'INTERRUPTED'];
  const CANCELLABLE = without(['PUBLISHING', 'COMPLETED', 'EXPIRED', 'CANCELLED', 'EXPANDED']);
  const RETRYABLE = ['FAILED', 'INTERRUPTED', 'STOPPED', 'CANCELLED', 'EXPIRED'];
  const RENAMABLE = without(['PUBLISHING', 'COMPLETED', 'EXPANDED']);
  const FINAL = ['COMPLETED', 'CANCELLED', 'FAILED', 'STOPPED', 'INTERRUPTED', 'EXPIRED', 'EXPANDED'];
  const RUNNING = ['PROBING', 'WAITING_SPACE', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING', 'CANCELLING'];
  const FILTERS = [['all', 'Tất cả'], ['running', 'Đang chạy'], ['queued', 'Chờ tải'], ['attention', 'Cần xử lý'],
    ['completed', 'Đã vào input'], ['ended', 'Đã dừng / hủy']];
  const MAX_LINKS = 20;
  /* download_upkeep.TEMP_CLEANABLE: the states whose downloaded part a retry or a remove drops. */
  const KEEPS_PART = ['FAILED', 'STOPPED', 'INTERRUPTED'];
  /* download_probe: the codes of a page with no video yt-dlp can read ("Trang này chưa được hỗ trợ"). */
  const UNSUPPORTED = ['UNSUPPORTED', 'NO_ENTRIES'];

  /* The badge text: a page yt-dlp cannot read says so instead of a bare "Lỗi". */
  function label(task) {
    if (task.state === 'FAILED' && UNSUPPORTED.includes(task.error_code)) return 'Chưa hỗ trợ';
    return own(LABELS, task.state) || String(task.state || '');
  }

  function group(task) {
    const s = task && task.state;
    if (RUNNING.includes(s)) return 'running';
    if (s === 'QUEUED') return 'queued';
    if (['NEEDS_CHOICE', 'FAILED', 'INTERRUPTED', 'WAITING_LOGIN'].includes(s)) return 'attention';
    if (s === 'COMPLETED') return 'completed';
    return 'ended';
  }
  function matches(task, filter) { return filter === 'all' || group(task) === filter; }
  function counts(tasks) {
    const out = {all: 0, running: 0, queued: 0, attention: 0, completed: 0, ended: 0};
    (tasks || []).forEach(t => { out.all++; out[group(t)]++; });
    return out;
  }

  const OFFLINE = 'Mất kết nối Control Center.';
  /* NEEDS_CHOICE of a series page: the episode dialog ("Chọn tập"), not the entries of a single video. */
  const choosesEpisodes = task => !!task && task.state === 'NEEDS_CHOICE' && task.choice_kind === 'episodes';
  /* Buttons of one task: {id, label, enabled, reason, confirm, primary}. ctx.offline locks every write; ctx.group is the
   * summary of the task's group (an episode's, or the group a series page was split into). */
  function actions(task, ctx) {
    const s = task.state, out = [], offline = !!(ctx && ctx.offline), group = ctx && ctx.group || null;
    const add = (id, label, when, confirm, blocked, primary) => {
      if (!when) return;
      const reason = offline ? OFFLINE : blocked || '';
      out.push({id, label, enabled: !reason, reason, confirm: confirm || null, primary: !!primary});
    };
    if (s === 'EXPANDED') {
      // The page task's Xóa is the group's (download_account_tasks._remove_group): only once nothing waits or runs.
      const finished = !!group && group.finished === true;
      add('remove', 'Xóa cả nhóm', true, groupConfirm('remove', group || {}),
        finished ? '' : !group ? 'Không thấy nhóm của trang này.' : group.state === 'CANCELLED'
          ? 'Nhóm đang hủy; chờ các tập dừng hẳn rồi xóa.' : 'Nhóm còn tập chưa xong; bấm Hủy nhóm trước.');
      return out;
    }
    // An episode of a cancelled group never runs again (409 since 9.15): no Tiếp tục / Thử lại.
    const closed = !!task.group_id && !!group && group.state === 'CANCELLED';
    add('episodes', 'Chọn tập', choosesEpisodes(task), null, '', true);
    add('stop', 'Dừng', STOPPABLE.includes(s));
    add('resume', 'Tiếp tục', RESUMABLE.includes(s) && !closed);
    add('retry', 'Thử lại từ đầu', RETRYABLE.includes(s) && !closed,
      KEEPS_PART.includes(s) ? 'Thử lại sẽ xóa phần đã tải của lượt này và tải lại từ đầu.' : null);
    add('cancel', 'Hủy', CANCELLABLE.includes(s) && s !== 'CANCELLING',
      'Hủy lượt tải và xóa file tạm của lượt này? File đã có trong input không bị đụng tới.');
    add('remove', 'Xóa khỏi danh sách', FINAL.includes(s), KEEPS_PART.includes(s)
      ? 'Xóa lượt này khỏi danh sách và xóa file tạm của nó? File đã có trong input không bị đụng tới.' : null);
    return out;
  }

  /* "Tài khoản nguồn phim" (accounts.sources[] of GET /api/downloads). The state comes from the server only: a closed
   * window, a 202 or an old last_login never means "Đã kết nối". */
  const ACCOUNT_LABELS = {NOT_CONNECTED: 'Chưa đăng nhập', LOGGING_IN: 'Đang đăng nhập', CONNECTED: 'Đã kết nối',
    NEEDS_LOGIN: 'Cần đăng nhập lại', CHECK_FAILED: 'Chưa kiểm tra được kết nối', UNAVAILABLE: 'Chưa sẵn sàng'};
  const ACCOUNT_TONES = {NOT_CONNECTED: 'grey', LOGGING_IN: 'blue', CONNECTED: '', NEEDS_LOGIN: 'amber', CHECK_FAILED: 'amber',
    UNAVAILABLE: 'red'};
  const EXPIRED_CODES = ['SESSION_EXPIRED', 'CLOCK_CHANGED']; // download_accounts: the 3 600 s fallback, a clock set back
  const SESSION_STATES = ['CONNECTED', 'NEEDS_LOGIN', 'CHECK_FAILED']; // a saved session exists (Ngắt kết nối removes it)
  // A sign-in is offered only when the server can confirm it and read the source's pages with it.
  const supported = source => source.login_supported !== false && source.reader_supported !== false;
  /* A "ttl" session whose recheck_at has passed by this page's clock (`now`, ms): the server says NEEDS_LOGIN /
   * SESSION_EXPIRED at its next answer; until then (a poll not back yet, a page left offline) it is shown expired
   * too. `seen` (a Set the page keeps; optional) remembers each session found past its mark by source, sign-in time
   * and mark, so the same session stays expired on this page when its clock goes back; a new sign-in is another
   * session. Only ever towards "Hết phiên": the page never shows a sign-in the server has not confirmed. */
  const sessionKey = source => JSON.stringify([source.id, source.authenticated_at || '', source.recheck_at]);
  function ttlPassed(source, now, seen) {
    const until = source && source.session_check === 'ttl' && source.recheck_at ? Date.parse(source.recheck_at) : NaN;
    if (!Number.isFinite(until)) return false;
    if (seen && seen.has(sessionKey(source))) return true;
    const passed = Number.isFinite(now) && now >= until;
    if (passed && seen) seen.add(sessionKey(source));
    return passed;
  }
  function accountState(source, now, seen) {
    if (!source) return {label: '', tone: 'grey'};
    const s = source.state;
    if (s === 'UNAVAILABLE') return {label: ACCOUNT_LABELS.UNAVAILABLE, tone: 'red'};
    if (source.login_running || s === 'LOGGING_IN') return {label: ACCOUNT_LABELS.LOGGING_IN, tone: 'blue'};
    if (!supported(source) && !SESSION_STATES.includes(s)) return {label: 'Chưa hỗ trợ', tone: 'grey'};
    if (s === 'NEEDS_LOGIN' && EXPIRED_CODES.includes(source.error_code)) return {label: 'Hết phiên', tone: 'amber'};
    if (s === 'CONNECTED' && ttlPassed(source, now, seen)) return {label: 'Hết phiên', tone: 'amber'};
    const tone = own(ACCOUNT_TONES, s);
    return {label: own(ACCOUNT_LABELS, s) || String(s || ''), tone: tone === undefined ? 'grey' : tone};
  }
  /* Buttons of the selected source, only on a page the server confirmed as the PC (ctx.device 'pc': /api/phone-mode
   * answered remote false). None on the phone (the panel says so), none while that is unknown (pending, an error, a
   * timeout, an answer without remote: the panel offers Kiểm tra lại), none for a source the server cannot sign in (no
   * useless window). Đăng nhập only POSTs when the user presses it. */
  function accountActions(source, ctx) {
    const out = [], offline = !!(ctx && ctx.offline);
    if (!source || !ctx || ctx.remote || ctx.device !== 'pc' || source.state === 'UNAVAILABLE') return out;
    const add = (id, label, when, primary) => {
      if (when) out.push({id, label, enabled: !offline, reason: offline ? OFFLINE : '', primary: !!primary});
    };
    const running = !!source.login_running || source.state === 'LOGGING_IN';
    add('cancel-login', 'Hủy đăng nhập', running);
    add('login', source.state === 'NOT_CONNECTED' ? 'Đăng nhập' : 'Đăng nhập lại', !running && supported(source), true);
    add('disconnect', 'Ngắt kết nối', !running && SESSION_STATES.includes(source.state));
    return out;
  }

  /* Groups (GET /api/downloads groups[], download_groups.summarize): the buckets, the badge and the buttons. */
  const BUCKET_LABELS = [['running', 'đang chạy'], ['queued', 'chờ tải'], ['waiting_login', 'chờ đăng nhập'],
    ['pending', 'chờ chỗ trong danh sách'], ['held', 'đã dừng (chưa xếp hàng)'], ['attention', 'cần xử lý'],
    ['failed', 'lỗi'], ['stopped', 'đã dừng'], ['interrupted', 'bị ngắt'], ['completed', 'đã vào input'],
    ['cancelled', 'đã hủy'], ['expired', 'đã dọn file tạm'], ['removed', 'đã xóa khỏi danh sách']];
  const count = (g, key) => Math.max(0, Number(g && g.counts && g.counts[key]) || 0);
  const sum = (g, keys) => keys.reduce((n, key) => n + count(g, key), 0);
  const UNFINISHED_BUCKETS = ['pending', 'held', 'queued', 'waiting_login', 'running', 'attention'];
  function groupState(g) {
    if (!g) return {label: '', tone: 'grey'};
    if (g.state === 'CANCELLED') return count(g, 'running') ? {label: 'Đang hủy', tone: 'grey'} : {label: 'Đã hủy', tone: 'grey'};
    if (g.finished) {
      if (sum(g, ['failed', 'stopped', 'interrupted', 'expired'])) return {label: 'Cần xử lý', tone: 'amber'};
      return count(g, 'completed') < (Number(g.total) || 0) ? {label: 'Xong một phần', tone: 'grey'} : {label: 'Đã xong', tone: ''};
    }
    if (count(g, 'running')) return {label: 'Đang tải', tone: 'blue'};
    if (count(g, 'waiting_login')) return {label: 'Chờ đăng nhập', tone: 'amber'};
    if (sum(g, ['queued', 'pending'])) return {label: 'Chờ tải', tone: 'grey'};
    return count(g, 'held') ? {label: 'Đã dừng', tone: 'grey'} : {label: 'Cần xử lý', tone: 'amber'};
  }
  /* What still waits, runs or can be cancelled (a stopped, interrupted or failed episode is cancelled too, 9.15). */
  const groupLeft = g => sum(g, [...UNFINISHED_BUCKETS, 'failed', 'stopped', 'interrupted']);
  function groupConfirm(action, g) {
    const title = '"' + String(g.title || 'Nhóm tập') + '"', done = count(g, 'completed');
    if (action === 'cancel') return 'Hủy nhóm ' + title + '? ' + groupLeft(g) + ' tập chưa xong sẽ bị hủy và file tạm của chúng được ' +
      'dọn (tập đang chuyển vào input thì vẫn xong; tập có file đang bị giữ ở "Đang hủy" tới khi dọn được). ' + done + ' tập đã vào input được giữ nguyên. Nhóm đã hủy không ' +
      'tải tiếp được; muốn tải lại thì dán lại link trang phim.';
    if (action === 'remove') return 'Xóa nhóm ' + title + ' khỏi danh sách? Lượt trang phim và ' + (Number(g.total) || 0) + ' tập của ' +
      'nhóm biến mất khỏi danh sách tải, file tạm của các tập đã dừng hoặc lỗi bị xóa. ' + done + ' file đã vào input không bị đụng tới.';
    if (action === 'retry') return 'Thử lại ' + sum(g, ['failed', 'expired']) + ' tập lỗi hoặc đã dọn file tạm? Các tập đó tải lại từ ' +
      'đầu; tập đã vào input không tải lại. Tập dừng hay bị ngắt thì dùng Tiếp tục nhóm.';
    return null;
  }
  function groupActions(g, ctx) {
    const out = [], offline = !!(ctx && ctx.offline), active = !!g && g.state === 'ACTIVE';
    if (!g) return out;
    const add = (id, label, when, danger) => {
      if (when) out.push({id, label, enabled: !offline, reason: offline ? OFFLINE : '', confirm: groupConfirm(id, g), danger: !!danger});
    };
    add('stop', 'Dừng nhóm', active && sum(g, ['pending', 'queued', 'waiting_login', 'running']) > 0);
    add('resume', 'Tiếp tục nhóm', active && sum(g, ['held', 'stopped', 'interrupted']) > 0);
    add('retry', 'Thử lại tập lỗi', active && sum(g, ['failed', 'expired']) > 0);
    add('cancel', active ? 'Hủy nhóm' : 'Hủy các tập còn lại', groupLeft(g) > 0, true);
    add('remove', 'Xóa nhóm khỏi danh sách', g.finished === true);
    return out;
  }
  /* One member of GET /api/downloads/groups/<id> (members in ordinal order): where it is. */
  function memberState(m) {
    if (m.status === 'PENDING') return {label: 'Chờ chỗ trong danh sách', tone: 'grey'};
    if (m.status === 'HELD') return {label: 'Đã dừng (chưa xếp hàng)', tone: 'grey'};
    if (m.status === 'CANCELLED') return {label: 'Đã hủy', tone: 'grey'};
    if (m.task_state) return {label: own(LABELS, m.task_state) || String(m.task_state), tone: own(TONES, m.task_state) || 'grey'};
    return {label: 'Đã xóa khỏi danh sách' + (own(LABELS, m.last_state) ? ' (' + own(LABELS, m.last_state) + ')' : ''), tone: 'grey'};
  }
  /* The backend also renames a task being cancelled; the page does not offer it (the file never reaches input). */
  const canRename = task => RENAMABLE.includes(task.state) && task.state !== 'CANCELLING' && !task.name_locked && !choosesEpisodes(task);

  function formatBytes(n) {
    const v = Number(n);
    if (n == null || n === '' || !Number.isFinite(v) || v < 0) return '';
    if (v < 1048576) return Math.round(v / 1024).toLocaleString('vi-VN') + ' KB';
    if (v < 1073741824) return Math.round(v / 1048576).toLocaleString('vi-VN') + ' MB';
    return (v / 1073741824).toLocaleString('vi-VN', {minimumFractionDigits: 1, maximumFractionDigits: 1}) + ' GB';
  }
  function clock(seconds) {
    const n = Math.round(Number(seconds));
    if (seconds == null || !Number.isFinite(n) || n < 0) return '';
    const h = Math.floor(n / 3600), m = Math.floor(n % 3600 / 60), sec = n % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(sec).padStart(2, '0');
  }
  /* Percent only with a known total (never invented); 100 % only once the file is in input. An HLS link
     counts finished segments (fragments_done / fragments_total); downloaded_bytes stays real bytes. While
     the segments are joined into MP4 (transfer_stage "remuxing") there is no percent, only the stage. */
  function progress(task) {
    const done = Math.max(0, Number(task.downloaded_bytes) || 0), total = Number(task.total_bytes) || 0, s = task.state;
    const parts = Number(task.fragments_total) || 0, partsDone = Math.min(parts, Math.max(0, Number(task.fragments_done) || 0));
    const segmented = task.progress_basis === 'fragments' && parts > 0;
    const remuxing = s === 'DOWNLOADING' && task.transfer_stage === 'remuxing';
    // A fresh link is being fetched (a source account's takes a hidden browser run): no byte moves yet.
    const resolving = s === 'DOWNLOADING' && task.transfer_stage === 'resolving';
    // A kept part is compared with a fresh link's answer from byte 0 (a source account's file): those bytes are not new.
    const comparing = s === 'DOWNLOADING' && task.transfer_stage === 'comparing';
    let percent = null;
    if (s === 'COMPLETED') percent = 100;
    else if (!remuxing && ['DOWNLOADING', 'STOPPED', 'INTERRUPTED', 'FAILED', 'CANCELLING'].includes(s)) {
      if (segmented) percent = Math.min(99, Math.floor(100 * partsDone / parts));
      else if (total > 0) percent = Math.min(99, Math.floor(100 * done / total));
    }
    let sizeText = total > 0 ? formatBytes(done) + ' / ' + formatBytes(total) : done ? formatBytes(done) + ' đã tải' : '';
    if (segmented) sizeText = partsDone + '/' + parts + ' đoạn' + (done ? ' · ' + formatBytes(done) : '');
    const moving = s === 'DOWNLOADING' && !remuxing && !resolving && !comparing;
    const speed = moving && Number(task.speed) > 0 ? formatBytes(task.speed) + '/s' : '';
    const eta = moving && Number(task.eta) > 0 ? 'còn ' + clock(task.eta) : '';
    const stage = remuxing ? 'Đang ghép các đoạn thành MP4' : resolving ? 'Đang lấy link tải mới từ nguồn'
      : comparing ? 'Đang so phần đã tải với link mới' : '';
    return {percent, indeterminate: percent === null && ['PROBING', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING'].includes(s),
      sizeText, speed, eta, stage};
  }
  /* The textarea: one link per line, blanks dropped. The backend checks every link again. */
  function links(text) {
    return String(text || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  }
  function checkBatch(text, rights) {
    const list = links(text);
    if (!list.length) throw new Error('Nhập ít nhất một link video.');
    if (list.length > MAX_LINKS) throw new Error('Mỗi lần tối đa ' + MAX_LINKS + ' link (đang có ' + list.length + ').');
    if (rights !== true) throw new Error('Tick xác nhận bạn có quyền tải và chỉnh sửa các video này.');
    return list;
  }
  /* 400 BATCH_REJECTED: one line per refused link, as the backend wrote it. */
  function batchErrors(error) {
    const rows = error && Array.isArray(error.errors) ? error.errors : [];
    return rows.map(e => (e.line ? 'Dòng ' + e.line + ': ' : '') + String(e.message || e.code || 'Không hợp lệ'));
  }
  function entryLine(entry) {
    const parts = [entry.title || ('Mục ' + entry.index)];
    if (entry.duration_seconds) parts.push(clock(entry.duration_seconds));
    if (entry.estimated_bytes) parts.push('khoảng ' + formatBytes(entry.estimated_bytes));
    return parts.join(' · ');
  }
  return {STATES, LABELS, TONES, STOPPABLE, RESUMABLE, CANCELLABLE, RETRYABLE, RENAMABLE, FINAL, RUNNING, KEEPS_PART, FILTERS,
    MAX_LINKS, UNSUPPORTED, own, label, group, matches, counts, actions, canRename, formatBytes, clock, progress, links, checkBatch, batchErrors,
    entryLine, choosesEpisodes, ACCOUNT_LABELS, accountState, ttlPassed, accountActions, BUCKET_LABELS, groupState, groupActions, groupConfirm,
    groupLeft, memberState};
});
