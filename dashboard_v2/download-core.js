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
  const STATES = ['QUEUED', 'PROBING', 'NEEDS_CHOICE', 'WAITING_SPACE', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING',
    'COMPLETED', 'STOPPED', 'FAILED', 'CANCELLING', 'CANCELLED', 'INTERRUPTED', 'EXPIRED'];
  const LABELS = {QUEUED: 'Chờ tải', PROBING: 'Đang thăm dò', NEEDS_CHOICE: 'Cần chọn video', WAITING_SPACE: 'Chờ chỗ trống',
    DOWNLOADING: 'Đang tải', VERIFYING: 'Đang kiểm tra file', PUBLISHING: 'Đang chuyển vào input', COMPLETED: 'Đã vào input',
    STOPPED: 'Đã dừng', FAILED: 'Lỗi', CANCELLING: 'Đang hủy', CANCELLED: 'Đã hủy', INTERRUPTED: 'Bị ngắt',
    EXPIRED: 'Đã dọn file tạm'};
  const TONES = {DOWNLOADING: 'blue', PROBING: 'blue', VERIFYING: 'blue', PUBLISHING: 'blue', CANCELLING: 'grey',
    NEEDS_CHOICE: 'amber', WAITING_SPACE: 'amber', INTERRUPTED: 'amber', FAILED: 'red', COMPLETED: '', QUEUED: 'grey',
    STOPPED: 'grey', CANCELLED: 'grey', EXPIRED: 'grey'};
  const without = out => STATES.filter(s => !out.includes(s));
  /* download_worker.py: STOPPABLE, RESUMABLE, CANCELLABLE, RETRYABLE, RENAMABLE; download_store: FINAL_STATES and
   * SLOT_STATES (RUNNING here, the "Đang chạy" group). */
  const STOPPABLE = ['QUEUED', 'PROBING', 'WAITING_SPACE', 'DOWNLOADING'];
  const RESUMABLE = ['STOPPED', 'INTERRUPTED'];
  const CANCELLABLE = without(['PUBLISHING', 'COMPLETED', 'EXPIRED', 'CANCELLED']);
  const RETRYABLE = ['FAILED', 'INTERRUPTED', 'STOPPED', 'CANCELLED', 'EXPIRED'];
  const RENAMABLE = without(['PUBLISHING', 'COMPLETED']);
  const FINAL = ['COMPLETED', 'CANCELLED', 'FAILED', 'STOPPED', 'INTERRUPTED', 'EXPIRED'];
  const RUNNING = ['PROBING', 'WAITING_SPACE', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING', 'CANCELLING'];
  const FILTERS = [['all', 'Tất cả'], ['running', 'Đang chạy'], ['queued', 'Chờ tải'], ['attention', 'Cần xử lý'],
    ['completed', 'Đã vào input'], ['ended', 'Đã dừng / hủy']];
  const MAX_LINKS = 20;
  /* download_upkeep.TEMP_CLEANABLE: the states whose downloaded part a retry or a remove drops. */
  const KEEPS_PART = ['FAILED', 'STOPPED', 'INTERRUPTED'];

  function group(task) {
    const s = task && task.state;
    if (RUNNING.includes(s)) return 'running';
    if (s === 'QUEUED') return 'queued';
    if (['NEEDS_CHOICE', 'FAILED', 'INTERRUPTED'].includes(s)) return 'attention';
    if (s === 'COMPLETED') return 'completed';
    return 'ended';
  }
  function matches(task, filter) { return filter === 'all' || group(task) === filter; }
  function counts(tasks) {
    const out = {all: 0, running: 0, queued: 0, attention: 0, completed: 0, ended: 0};
    (tasks || []).forEach(t => { out.all++; out[group(t)]++; });
    return out;
  }

  /* Buttons of one task: {id, label, enabled, reason, confirm}. ctx.offline locks every write. */
  function actions(task, ctx) {
    const s = task.state, out = [], offline = !!(ctx && ctx.offline);
    const add = (id, label, when, confirm) => {
      if (when) out.push({id, label, enabled: !offline, reason: offline ? 'Mất kết nối Control Center.' : '', confirm: confirm || null});
    };
    add('stop', 'Dừng', STOPPABLE.includes(s));
    add('resume', 'Tiếp tục', RESUMABLE.includes(s));
    add('retry', 'Thử lại từ đầu', RETRYABLE.includes(s),
      KEEPS_PART.includes(s) ? 'Thử lại sẽ xóa phần đã tải của lượt này và tải lại từ đầu.' : null);
    add('cancel', 'Hủy', CANCELLABLE.includes(s) && s !== 'CANCELLING',
      'Hủy lượt tải và xóa file tạm của lượt này? File đã có trong input không bị đụng tới.');
    add('remove', 'Xóa khỏi danh sách', FINAL.includes(s), KEEPS_PART.includes(s)
      ? 'Xóa lượt này khỏi danh sách và xóa file tạm của nó? File đã có trong input không bị đụng tới.' : null);
    return out;
  }
  /* The backend also renames a task being cancelled; the page does not offer it (the file never reaches input). */
  const canRename = task => RENAMABLE.includes(task.state) && task.state !== 'CANCELLING' && !task.name_locked;

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
  /* Percent only with a known total (never invented); 100 % only once the file is in input. */
  function progress(task) {
    const done = Math.max(0, Number(task.downloaded_bytes) || 0), total = Number(task.total_bytes) || 0, s = task.state;
    let percent = null;
    if (s === 'COMPLETED') percent = 100;
    else if (total > 0 && ['DOWNLOADING', 'STOPPED', 'INTERRUPTED', 'FAILED', 'CANCELLING'].includes(s)) {
      percent = Math.min(99, Math.floor(100 * done / total));
    }
    const sizeText = total > 0 ? formatBytes(done) + ' / ' + formatBytes(total) : done ? formatBytes(done) + ' đã tải' : '';
    const speed = s === 'DOWNLOADING' && Number(task.speed) > 0 ? formatBytes(task.speed) + '/s' : '';
    const eta = s === 'DOWNLOADING' && Number(task.eta) > 0 ? 'còn ' + clock(task.eta) : '';
    return {percent, indeterminate: percent === null && ['PROBING', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING'].includes(s),
      sizeText, speed, eta};
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
    MAX_LINKS, group, matches, counts, actions, canRename, formatBytes, clock, progress, links, checkBatch, batchErrors,
    entryLine};
});
