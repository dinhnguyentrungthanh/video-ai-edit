/* "Tải video" (live) HTML: the form, "Tài khoản nguồn phim", the groups of episodes, the task list and the
 * "Dung lượng" panel, built from the GET /api/downloads and /api/storage-summary answers. No HTTP and no state of
 * its own: download-live.js keeps the page state (ui) and wires the data-action buttons. Titles, entries, labels,
 * messages and log lines come from remote sites or the sources: they are only ever written through esc() as text.
 */
(function (root, factory) {
  const api = factory(typeof module === 'object' && module.exports ? require('./download-core.js') : root.BFDownloadCore);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BFDownloadView = api;
})(typeof window === 'undefined' ? this : window, function (K) {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const attr = (on, name) => on ? ' ' + name : '';

  function createUi() {
    return {text: '', rights: false, error: '', lineErrors: [], filter: 'all', open: new Set(),
      details: new Map(), renames: new Map(), choices: new Map(), busy: new Set(),
      account: null, groupsOpen: new Set(), members: new Map(), expiredSessions: new Set()};
  }

  function notices(data, ctx) {
    const out = [];
    if (ctx.error) out.push('<div class="notice" role="alert">Không tải được danh sách tải video: ' + esc(ctx.error) + '</div>');
    if (data && data.worker_error) out.push('<div class="notice" role="alert">Hàng tải video báo lỗi' +
      (data.worker_error_at ? ' lúc ' + esc(new Date(data.worker_error_at).toLocaleString('vi-VN')) : '') + ': ' + esc(data.worker_error) + '</div>');
    return out.join('');
  }

  function form(data, ui, ctx) {
    const locked = ctx.offline;
    const lines = ui.lineErrors.length ? '<ul class="dl-line-errors">' + ui.lineErrors.map(l => '<li>' + esc(l) + '</li>').join('') + '</ul>' : '';
    return '<section class="panel download-form" aria-labelledby="dl-form-title"><div class="download-panel-title">' + ctx.icon('downloads') +
      '<h2 id="dl-form-title">Thêm video</h2></div><p class="muted" id="dl-hint">Mỗi dòng một link, tối đa ' + K.MAX_LINKS +
      ' link mỗi lần, từ trang nào cũng được. BiliFlow thăm dò từng trang trước khi tải; trang không có video đọc được sẽ báo ' +
      '"Chưa hỗ trợ" ngay trong danh sách. Có thể thêm lượt mới trong khi các video khác đang tải.</p>' +
      '<label class="field"><span>Link video</span><textarea id="dl-urls" rows="5" autocomplete="off" spellcheck="false" ' +
      'placeholder="Mỗi dòng một link https://…" maxlength="41000" aria-describedby="dl-hint dl-error"' +
      attr(ui.error, 'aria-invalid="true"') + attr(ui.busy.has('add'), 'readonly') + '>' + esc(ui.text) + '</textarea></label>' +
      '<label class="dl-rights"><input type="checkbox" id="dl-rights"' + attr(ui.rights, 'checked') + '> ' +
      '<span>Tôi có quyền tải và chỉnh sửa các video này.</span></label>' +
      '<div id="dl-error" class="modal-error" role="alert"' + attr(!ui.error, 'hidden') + '>' + esc(ui.error) + lines + '</div>' +
      '<button class="primary download-start" data-action="dl-add"' + attr(locked || ui.busy.has('add'), 'disabled') + '>' +
      ctx.icon('downloads') + (ui.busy.has('add') ? ' Đang kiểm tra link…' : ' Thêm vào danh sách tải') + '</button>' +
      '<div class="download-destination"><span>File tải xong được chuyển vào</span><strong>Thư mục input của BiliFlow</strong>' +
      '<p class="muted dl-note">BiliFlow nhận video như file bạn tự chép vào; chỉ quét khi bạn bấm Thiết lập &amp; bắt đầu.</p></div>' +
      '<div class="download-demo-note"><strong>Giới hạn</strong><p>BiliFlow không dùng cookie hay tài khoản của trình duyệt bạn. ' +
      'Nguồn phim cần đăng nhập chỉ dùng phiên bạn tự đăng nhập ở khung Tài khoản nguồn phim (trên PC). Trang cần đăng nhập ' +
      'khác, có DRM hoặc đang phát trực tiếp sẽ báo lý do. Link danh sách phát hay kênh sẽ bị từ chối; hãy dán link của từng video. Trang yt-dlp không có ' +
      'bộ đọc riêng: chỉ lấy video từ 10 phút để bỏ quảng cáo; có nhiều video dài thì hỏi bạn.</p>' +
      '<p>Link thẳng tới file video (.mp4, .mkv, .webm, .mov, .ts) hoặc playlist HLS (.m3u8) BiliFlow tự tải: HLS tải nhiều đoạn ' +
      'cùng lúc rồi ghép đúng thứ tự, không mã hóa lại; HLS mã hóa AES-128 hay dạng fMP4 chuyển sang yt-dlp. Chỉ dán link từ nguồn ' +
      'bạn được phép dùng.</p></div></section>';
  }

  function tools(data, ui, ctx) {
    const settings = data && data.settings || {slots: 2, max_slots: 3}, temp = data && data.temp || {}, space = data && data.space || {};
    const options = Array.from({length: settings.max_slots || 3}, (_, i) => i + 1)
      .map(n => '<option value="' + n + '"' + attr(settings.slots === n, 'selected') + '>' + n + ' video</option>').join('');
    const tempText = temp.tasks ? 'Dọn file tạm (' + Number(temp.tasks) + ' lượt · ' + K.formatBytes(temp.bytes) + ')' : 'Dọn file tạm';
    const spaceText = Number.isFinite(space.free_bytes) ? 'Ổ còn ' + K.formatBytes(space.free_bytes) + ' trống · giữ lại ' +
      K.formatBytes(space.reserve_bytes) : space.error ? 'Không đọc được chỗ trống: ' + space.error : '';
    return '<div class="download-queue-tools"><label>Tải đồng thời <select id="dl-slots" aria-label="Số lượt tải đồng thời"' +
      attr(ctx.offline || ui.busy.has('slots'), 'disabled') + '>' + options + '</select></label><button class="secondary small" data-action="dl-cleanup"' +
      attr(ctx.offline || !temp.tasks, 'disabled') + ' title="Xóa file tạm của các lượt đã dừng, lỗi hoặc bị ngắt">' + esc(tempText) +
      '</button></div>' + (spaceText ? '<p class="dl-space muted">' + esc(spaceText) + '</p>' : '');
  }

  function counters(tasks) {
    const c = K.counts(tasks);
    return '<div class="download-counts">' + [['running', 'Đang chạy'], ['queued', 'Chờ tải'], ['attention', 'Cần xử lý'],
      ['completed', 'Đã vào input']].map(([key, label]) => '<div><strong>' + c[key] + '</strong><span>' + label + '</span></div>').join('') + '</div>';
  }

  function filters(tasks, ui) {
    const c = K.counts(tasks);
    return '<div class="download-filters" role="group" aria-label="Lọc lượt tải">' + K.FILTERS.map(([key, label]) =>
      '<button class="filter-tab ' + (ui.filter === key ? 'active' : '') + '" data-action="dl-filter" data-filter="' + key +
      '" aria-pressed="' + (ui.filter === key) + '">' + label + ' <span>' + c[key] + '</span></button>').join('') + '</div>';
  }


  /* "Tài khoản nguồn phim" (accounts of GET /api/downloads, docs/SOURCE_ACCOUNTS_PLAN.md 9.14). Read only: the page
   * never signs in by itself (only Đăng nhập on the PC posts); the state is the server's. */
  const when = value => {
    if (!value) return '';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('vi-VN');
  };
  function selectedSource(accounts, ui) {
    const sources = accounts && Array.isArray(accounts.sources) ? accounts.sources : [];
    return sources.find(item => item.id === ui.account) || sources[0] || null;
  }
  function lastLogin(source, passed) {
    const last = source.last_login;
    if (!last || typeof last !== 'object') return '';
    const at = when(last.at);
    const unusable = ['NOT_CONNECTED', 'NEEDS_LOGIN'].includes(source.state) || passed;
    const line = last.connected ? 'Lần đăng nhập gần nhất: thành công' + (at ? ' lúc ' + at : '') + (unusable
      ? '; phiên đó hiện không dùng được (xem trạng thái ở trên).' : '.')
      : 'Lần đăng nhập gần nhất không thành công' + (at ? ' (' + at + ')' : '') + ': ' + (last.message || last.code || 'không rõ lý do') + '.';
    const left = last.profile_left ? ' Hồ sơ trình duyệt tạm của lần đó chưa dọn được; BiliFlow sẽ dọn lại khi khởi động.' : '';
    return '<p class="dl-account-line' + (last.connected ? '' : ' dl-account-warn') + '">' + esc(line + left) + '</p>';
  }
  /* `passed`: download-core.ttlPassed with the page's memory of expired sessions (ui.expiredSessions). */
  function sessionLines(source, now, passed) {
    const lines = [], signed = when(source.authenticated_at), checked = when(source.checked_at);
    if (signed) lines.push(['Đăng nhập lúc', signed]);
    if (passed && ['CONNECTED', 'NEEDS_LOGIN'].includes(source.state)) {
      lines.push(['Phiên hết hạn lúc', when(source.recheck_at)]);
    } else if (source.session_check === 'ttl' && source.state === 'CONNECTED' && source.recheck_at) {
      const left = Math.round((new Date(source.recheck_at).getTime() - now) / 60000);
      lines.push(['Phiên dùng tới', when(source.recheck_at) + (left > 0 ? ' (còn khoảng ' + left + ' phút)' : '')]);
    }
    if (source.session_check === 'live' && checked) lines.push(['Kiểm tra gần nhất', checked]);
    return lines.map(([k, v]) => '<div class="key-value"><span>' + esc(k) + '</span><span>' + esc(v) + '</span></div>').join('');
  }
  /* The server has not confirmed that this page is the PC (pending, an error, a timeout, no `remote`): no account
   * button (download-core.accountActions), a short note and a re-check that only reads /api/phone-mode. */
  function modeUnknown(ui, ctx) {
    const checking = !!ctx.deviceChecking || ui.busy.has('mode-check');
    const why = checking ? 'Đang kiểm tra trang này mở trên PC hay qua điện thoại…'
      : 'Chưa xác định được trang này mở trên PC hay qua điện thoại' + (ctx.deviceError ? ' (' + ctx.deviceError + ')' : '') + '.';
    return '<div class="dl-mode-unknown" role="status"><p class="pc-only-note">' + esc(why) + ' Đăng nhập, hủy đăng nhập và ngắt kết ' +
      'nối chỉ làm được khi Control Center xác nhận đây là PC.</p><button class="secondary small" data-action="dl-mode-check"' + attr(checking, 'disabled') +
      '>Kiểm tra lại</button></div>';
  }
  function accountButtons(source, ui, ctx) {
    if (ctx.remote) return '<p class="pc-only-note">Đăng nhập, hủy đăng nhập và ngắt kết nối chỉ làm trên PC: mở trang Tải video ' +
      'trên PC rồi bấm Đăng nhập. Ở đây chỉ xem trạng thái.</p>';
    if (ctx.device !== 'pc') return modeUnknown(ui, ctx);
    const buttons = K.accountActions(source, ctx).map(a => '<button class="' + (a.primary ? 'primary' : a.id === 'disconnect' ? 'danger' : 'secondary') +
      ' small" data-action="dl-account" data-op="' + a.id + '"' + attr(!a.enabled || ui.busy.has('account-' + a.id), 'disabled') +
      (a.reason ? ' title="' + esc(a.reason) + '"' : '') + '>' + esc(a.label) + '</button>').join('');
    return buttons ? '<div class="download-item-actions dl-account-actions">' + buttons + '</div>' : '';
  }
  function accountsPanel(data, ui, ctx) {
    const accounts = data ? data.accounts : undefined;
    const head = '<div class="download-panel-title">' + ctx.icon('settings') + '<h2 id="dl-acc-title">Tài khoản nguồn phim</h2></div>';
    const wrap = inner => '<section class="panel download-accounts" id="dl-accounts" aria-labelledby="dl-acc-title">' + head + inner + '</section>';
    if (!data) return wrap('<p class="muted">Đang tải…</p>');
    if (accounts === undefined || accounts === null) return wrap('<p class="muted dl-note">Control Center này chưa có phần tài khoản nguồn ' +
      'phim. Link thường vẫn tải như bên trên.</p>');
    const sources = Array.isArray(accounts.sources) ? accounts.sources : [];
    const problems = (accounts.error ? '<p class="download-item-error" role="alert">' + esc(accounts.error) + '</p>' : '') +
      (accounts.problem_text ? '<p class="download-item-error" role="alert">Cấu hình nguồn có lỗi: ' + esc(accounts.problem_text) + '</p>' : '');
    if (!sources.length) return wrap(problems + '<p class="muted dl-note">Chưa có nguồn phim cần đăng nhập nào được bật trên máy này ' +
      '(nguồn được khai trong <span class="mono">config/download_accounts.local.json</span> trên PC, theo mẫu ' +
      '<span class="mono">config/download_accounts.example.json</span>). Link thường vẫn tải như bên trên, không cần đăng nhập.</p>');
    const source = selectedSource(accounts, ui), now = ctx.now || Date.now();
    const passed = K.ttlPassed(source, now, ui.expiredSessions), state = K.accountState(source, now, ui.expiredSessions);
    const options = sources.map(item => '<option value="' + esc(item.id) + '"' + attr(item === source, 'selected') + '>' + esc(item.label || item.id) +
      '</option>').join('');
    const waiting = Number(source.waiting_tasks) || 0;
    const notes = [];
    if (source.message) notes.push('<p class="dl-account-line' + (['NEEDS_LOGIN', 'CHECK_FAILED', 'UNAVAILABLE'].includes(source.state) ?
      ' dl-account-warn' : '') + '">' + esc(source.message) + '</p>');
    if (source.state === 'CHECK_FAILED') notes.push('<p class="muted dl-note">Lỗi mạng khi kiểm tra không có nghĩa là hết phiên; BiliFlow thử lại sau.</p>');
    if (source.login_supported === false) notes.push('<p class="muted dl-note">BiliFlow chưa biết cách xác nhận đăng nhập của nguồn này nên ' +
      'không mở cửa sổ đăng nhập.</p>');
    if (source.reader_supported === false) notes.push('<p class="muted dl-note">BiliFlow chưa đọc được trang phim của nguồn này: link của nó ' +
      'sẽ báo "Chưa hỗ trợ", kể cả khi đã đăng nhập.</p>');
    (Array.isArray(source.warnings) ? source.warnings : []).forEach(w => notes.push('<p class="dl-account-line dl-account-warn">' +
      esc(w && (w.message || w.code)) + (w && w.at ? ' (' + esc(when(w.at)) + ')' : '') + '</p>'));
    return wrap(problems + '<label class="field dl-account-pick"><span>Nguồn</span><select id="dl-account-source" aria-describedby="dl-account-state">' +
      options + '</select></label><div class="dl-account-state" id="dl-account-state"><span class="badge ' + esc(state.tone) + '">' + esc(state.label) +
      '</span>' + (waiting ? '<span class="dl-account-waiting">' + waiting + ' lượt chờ đăng nhập</span>' : '') + '</div>' +
      sessionLines(source, now, passed) + notes.join('') + lastLogin(source, passed) + accountButtons(source, ui, ctx) +
      '<p class="muted dl-note">Đăng nhập mở cửa sổ trang chính thức của nguồn trên PC; bạn tự đăng nhập ở đó, BiliFlow không hỏi hay lưu ' +
      'mật khẩu. Chỉ khi nguồn xác nhận, BiliFlow mới coi là đã đăng nhập (đóng cửa sổ không tính).' + (source.session_check === 'ttl'
        ? ' Nguồn này không kiểm tra phiên trực tiếp được: BiliFlow dùng phiên tối đa 1 giờ từ lần đăng nhập rồi hỏi đăng nhập lại; mở trang ' +
          'này không gia hạn. Giờ ở trên chỉ để tham khảo.' : '') + '</p>');
  }

  /* Groups of episodes (GET /api/downloads groups[]; members from GET /api/downloads/groups/<id> when opened). */
  const groupOf = (data, id) => (data && Array.isArray(data.groups) ? data.groups : []).find(g => g.id === Number(id)) || null;
  const groupOfPage = (data, taskId) => (data && Array.isArray(data.groups) ? data.groups : []).find(g => g.parent_task_id === Number(taskId)) || null;
  function members(group, ui) {
    const loaded = ui.members.get(group.id);
    if (!loaded) return '<p class="muted">Đang tải danh sách tập…</p>';
    if (loaded.error) return '<p class="download-item-error">' + esc(loaded.error) + '</p>';
    const rows = loaded.members.map(m => {
      const st = K.memberState(m);
      const size = m.task_state === 'COMPLETED' ? K.formatBytes(m.output_size) : m.total_bytes ? K.formatBytes(m.downloaded_bytes) + ' / ' +
        K.formatBytes(m.total_bytes) : '';
      const name = [m.code, m.episode_label, m.season_label && !m.code ? m.season_label : '', m.variant_label].filter(Boolean).join(' · ');
      return '<li id="dl-member-' + Number(m.id) + '"><span class="download-item-number">' + Number(m.ordinal) + '</span><span class="dl-member-name">' +
        esc(name || 'Tập ' + Number(m.ordinal)) + (m.task_id ? ' <small>lượt #' + Number(m.task_id) + '</small>' : '') + '</span>' +
        (size ? '<small>' + esc(size) + '</small>' : '') + '<span class="badge ' + esc(st.tone) + '">' + esc(st.label) + '</span>' +
        (m.error_message && ['FAILED', 'WAITING_LOGIN', 'INTERRUPTED'].includes(m.task_state) ? '<small class="dl-member-error">' + esc(m.error_message) +
        '</small>' : '') + '</li>';
    }).join('');
    return '<ol class="dl-members">' + rows + '</ol>';
  }
  function groupCard(group, ui, ctx) {
    const id = Number(group.id), st = K.groupState(group);
    const chips = K.BUCKET_LABELS.filter(([key]) => Number(group.counts && group.counts[key]) > 0)
      .map(([key, label]) => '<span class="dl-chip dl-chip-' + key + '">' + Number(group.counts[key]) + ' ' + label + '</span>').join('');
    const percent = typeof group.percent === 'number' && Number.isFinite(group.percent) ? Math.max(0, Math.min(100, Math.round(group.percent))) : null;
    const meter = percent === null ? '' : '<div class="meter download-meter" role="progressbar" aria-label="Tiến độ nhóm ' + id +
      '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + percent + '"><i style="width:' + percent + '%"></i></div>';
    const notes = [];
    if (!group.complete) notes.push('Danh sách lúc chọn chưa đầy đủ' + (Array.isArray(group.reasons) && group.reasons.length ? ': ' +
      group.reasons.map(String).join('; ').replace(/[.\s]+$/, '') : '') + '. Nhóm chỉ có các tập đã thấy.');
    if (group.note) notes.push(group.note);
    if (Number(group.existing_count) > 0) notes.push(Number(group.existing_count) + ' tập đã có trong danh sách khi chọn nên không thêm lại.');
    if (Number(group.counts && group.counts.pending) > 0) notes.push('Tập "chờ chỗ trong danh sách" chưa chạy: danh sách tải giữ tối đa 100 ' +
      'lượt chưa xong, BiliFlow thêm dần theo thứ tự khi có chỗ. Không mất tập nào.');
    const buttons = K.groupActions(group, ctx).map(a => '<button class="' + (a.danger ? 'danger' : 'secondary') + ' small" data-action="dl-group-op" ' +
      'data-group="' + id + '" data-op="' + a.id + '"' + attr(!a.enabled || ui.busy.has('group-' + a.id + id), 'disabled') + ' title="' +
      esc(a.reason || a.label) + '">' + esc(a.label) + '</button>').join('');
    return '<article class="download-group" id="dl-group-' + id + '" tabindex="-1" aria-labelledby="dl-group-title-' + id + '"><div class="download-item-top">' +
      '<div class="download-item-name"><span class="download-item-number">N' + id + '</span><h3 id="dl-group-title-' + id + '">' +
      esc(group.title || 'Nhóm tập ' + id) + '</h3></div><span class="badge ' + esc(st.tone) + '">' + esc(st.label) + '</span></div>' +
      '<p class="download-source">' + esc([group.source_label, 'Đã xong ' + Number(group.done || 0) + '/' + Number(group.total || 0) + ' tập']
        .filter(Boolean).join(' · ')) + '</p>' +
      '<div class="download-item-progress"><span class="dl-chips">' + chips + '</span><strong>' + (percent === null ? '' : percent + '%') + '</strong></div>' +
      meter + notes.map(n => '<p class="muted dl-message">' + esc(n) + '</p>').join('') +
      (buttons ? '<div class="download-item-actions">' + buttons + '</div>' : '') +
      '<details class="download-log download-group-members" data-fold="group-' + id + '" data-group-members="' + id + '"' +
      attr(ui.groupsOpen.has(id), 'open') + '><summary>Các tập theo thứ tự (' + Number(group.total || 0) + ')</summary>' +
      (ui.groupsOpen.has(id) ? members(group, ui) : '') + '</details></article>';
  }
  function groups(data, ui, ctx) {
    const list = data && Array.isArray(data.groups) ? data.groups : [];
    if (!list.length) return '';
    return '<section class="download-groups" aria-labelledby="dl-groups-title"><h3 id="dl-groups-title" class="dl-section-title">Nhóm tập (' +
      list.length + ')</h3>' + list.map(g => groupCard(g, ui, ctx)).join('') + '</section>';
  }

  function rename(task, ui, ctx) {
    const id = Number(task.id);
    if (!K.canRename(task)) {
      return task.output_name ? '<p class="dl-output">Đã vào input: <strong>' + esc(task.output_name) + '</strong></p>' : '';
    }
    const place = task.group && typeof task.group === 'object' ? task.group : null;
    const film = place ? task.desired_name || (groupOf(ctx.data, place.group_id) || {}).title || '' : task.title || '';
    const draft = ui.renames.has(id) ? ui.renames.get(id) : film;
    return '<div class="dl-rename"><input id="dl-name-' + id + '" data-rename="' + id + '" value="' + esc(draft) +
      '" maxlength="150" autocomplete="off" aria-label="' + (place ? 'Tên phim trong tên file của lượt ' : 'Tên file của lượt ') + id +
      '" placeholder="' + (place ? 'Tên phim (số thứ tự và mã tập giữ nguyên)' : 'Tên file khi vào input') + '"' +
      attr(ctx.offline, 'disabled') + '><button class="secondary small" data-action="dl-rename" data-id="' + id + '"' +
      attr(ctx.offline || ui.busy.has('rename' + id), 'disabled') + '>Đổi tên</button></div>';
  }

  function choice(task, ui, ctx) {
    const id = Number(task.id);
    if (task.state !== 'NEEDS_CHOICE' || !Array.isArray(task.entries) || K.choosesEpisodes(task)) return '';
    const picked = ui.choices.get(id);
    return '<fieldset class="dl-choice"><legend>Trang có nhiều video. Chọn video cần tải:</legend>' + task.entries.map(entry =>
      '<label><input type="radio" name="dl-choice-' + id + '" data-choice="' + id + '" value="' + esc(entry.index) + '"' +
      attr(picked === entry.index, 'checked') + '> <span>' + esc(K.entryLine(entry)) + '</span></label>').join('') +
      '<button class="primary small" data-action="dl-choose" data-id="' + id + '"' +
      attr(ctx.offline || picked === undefined || ui.busy.has('choose' + id), 'disabled') + '>Tải video đã chọn</button></fieldset>';
  }


  /* Source accounts on a task row: where an episode sits in its group, why a task waits for a sign-in, what a series
   * page waits for, and which group a split page became. */
  function accountLine(task, ui, ctx) {
    const id = Number(task.id), place = task.group && typeof task.group === 'object' ? task.group : null;
    if (place) {
      const name = place.planned_name && !['COMPLETED', 'PUBLISHING'].includes(task.state) ? ' · Tên file dự kiến: ' + place.planned_name : '';
      return '<p class="muted dl-media dl-place">' + esc('Nhóm N' + Number(place.group_id) + ' · tập ' + Number(place.ordinal) + '/' +
        Number(place.total) + (place.code ? ' · ' + place.code : '') + name) + ' <button class="secondary small dl-link" data-action="dl-group-show" ' +
        'data-group="' + Number(place.group_id) + '">Xem nhóm</button></p>' + loginLine(task, ctx);
    }
    if (task.state === 'EXPANDED') {
      const group = groupOfPage(ctx.data, id);
      return '<p class="muted dl-media">' + (group ? 'Đã tách thành nhóm N' + Number(group.id) + ' (' + Number(group.total) + ' tập). ' +
        '<button class="secondary small dl-link" data-action="dl-group-show" data-group="' + Number(group.id) + '">Xem nhóm</button>'
        : 'Đã tách thành nhóm tập (nhóm không còn trong danh sách).') + '</p>';
    }
    if (K.choosesEpisodes(task)) {
      const e = task.episodes && typeof task.episodes === 'object' ? task.episodes : {};
      const parts = [e.title ? 'Phim: ' + e.title : 'Trang phim nhiều tập', Number(e.episode_count) ? Number(e.episode_count) + ' tập' : '',
        e.complete === false ? 'danh sách chưa đầy đủ' + (e.message ? ' (' + e.message + ')' : '') : '', e.has_draft ? 'đã lưu lựa chọn nháp' : '']
        .filter(Boolean);
      return '<p class="dl-message">' + esc(parts.join(' · ')) + '. Bấm Chọn tập để chọn phạm vi và bản tải; chỉ khi bấm "Tải N tập" mới có lượt tải.</p>';
    }
    return loginLine(task, ctx);
  }
  /* WAITING_LOGIN (an episode of a group too): which source, and what to do on this device. */
  function loginLine(task, ctx) {
    if (task.state === 'WAITING_LOGIN' && task.login_source) {
      const sources = ctx.data && ctx.data.accounts && Array.isArray(ctx.data.accounts.sources) ? ctx.data.accounts.sources : [];
      const source = sources.find(item => item.id === task.login_source);
      const label = source ? source.label || source.id : task.login_source;
      if (task.login_reason === 'OTHER_ACCOUNT') return '<p class="muted dl-note">Đăng nhập bằng tài khoản Windows đang chạy BiliFlow không ' +
        'đánh thức lượt này. Mở BiliFlow trên PC bằng tài khoản Windows đã thêm nó, hoặc Hủy rồi dán lại link.</p>';
      if (ctx.remote) return '<p class="pc-only-note">Đăng nhập ' + esc(label) + ' trên PC (trang Tải video → Tài khoản nguồn phim); lượt này ' +
        'tự tiếp tục sau khi đăng nhập xong.</p>';
      if (!source) return '<p class="muted dl-note">Nguồn của lượt này không còn trong cấu hình.</p>';
      // The page's mode is unknown: no button, only what holds on either device (as the server's own hint says).
      if (ctx.device !== 'pc') return '<p class="muted dl-note">Lượt này chờ phiên đăng nhập của ' + esc(label) + ': đăng nhập trên PC ' +
        '(trang Tải video → Tài khoản nguồn phim), lượt này tự tiếp tục sau đó.</p>';
      return '<p class="dl-message"><button class="secondary small" data-action="dl-account-show" data-source="' + esc(source.id) + '">Đăng nhập ' +
        esc(label) + '</button> Lượt này tự tiếp tục sau khi đăng nhập xong; BiliFlow không tự mở cửa sổ đăng nhập.</p>';
    }
    return '';
  }

  function message(task) {
    if (!task.error_message) return '';
    const tone = task.state === 'FAILED' ? 'download-item-error' : 'muted dl-message';
    return '<p class="' + tone + '">' + esc(task.error_message) + (task.state === 'FAILED' && task.error_code ? ' (' + esc(task.error_code) + ')' : '') + '</p>';
  }

  function log(task, ui) {
    const id = Number(task.id);
    const detail = ui.details.get(id), open = ui.open.has(id);
    let body = '<p class="muted">Đang tải nhật ký…</p>';
    if (detail && detail.error) body = '<p class="download-item-error">' + esc(detail.error) + '</p>';
    else if (detail && detail.events) {
      const events = detail.events.map(e => '<li><strong>' + esc(e.message || e.kind) + '</strong></li>').join('');
      const lines = (detail.log || []).map(line => '<li class="mono">' + esc(line) + '</li>').join('');
      body = '<ol>' + (events || '<li>Chưa có sự kiện.</li>') + '</ol>' + (lines ? '<ol class="dl-log-lines">' + lines + '</ol>' : '');
    }
    return '<details class="download-log" data-log-id="' + id + '"' + attr(open, 'open') + '><summary>Nhật ký lượt tải</summary>' + body + '</details>';
  }

  function row(task, ui, ctx) {
    const id = Number(task.id);
    const p = K.progress(task);
    const percent = p.percent === null ? '' : p.percent + '%';
    // Sizes, speed and time left (or the joining stage) only: the badge already names the state.
    const meta = [p.stage, p.sizeText, p.speed, p.eta].filter(Boolean).join(' · ');
    const media = task.state === 'COMPLETED' && task.media && task.media.height ? '<p class="muted dl-media">' + esc(task.media.height + 'p · ' +
      [task.media.video_codec, task.media.audio_codec].filter(Boolean).join(' + ')) + '</p>' : '';
    const source = task.media && task.media.source_label ? '<p class="muted dl-media">Nguồn: ' + esc(task.media.source_label) + '</p>' : '';
    const scope = task.group && task.group.group_id ? groupOf(ctx.data, task.group.group_id) : task.state === 'EXPANDED' ? groupOfPage(ctx.data, id) : null;
    const buttons = K.actions(task, {...ctx, group: scope}).map(a => '<button class="' + (a.primary ? 'primary' : a.id === 'cancel' ||
      (a.id === 'remove' && task.state === 'EXPANDED') ? 'danger' : 'secondary') + ' small" data-action="dl-op" data-id="' +
      id + '" data-op="' + a.id + '"' + attr(!a.enabled || ui.busy.has(a.id + id), 'disabled') + ' title="' + esc(a.reason || a.label) + '">' +
      esc(a.label) + '</button>').join('');
    const width = p.percent === null ? (p.indeterminate ? 30 : 0) : p.percent;
    // No bar when there is nothing to show (waiting, a choice, cancelled): an empty bar reads as 0 %.
    const meter = p.percent === null && !p.indeterminate ? '' : '<div class="meter download-meter' + (p.indeterminate ? ' indeterminate' : '') +
      '" role="progressbar" aria-label="Tiến độ lượt ' + id + '" aria-valuemin="0" aria-valuemax="100"' +
      (p.percent === null ? '' : ' aria-valuenow="' + p.percent + '"') + '><i style="width:' + width + '%"></i></div>';
    const line = meta || percent ? '<div class="download-item-progress"><span>' + esc(meta) + '</span><strong>' + percent + '</strong></div>' : '';
    return '<article class="download-item" data-download-id="' + id + '"><div class="download-item-top"><div class="download-item-name">' +
      '<span class="download-item-number">' + id + '</span><h3>' + esc(task.title || 'Lượt tải ' + id) + '</h3></div><span class="badge ' +
      esc(K.own(K.TONES, task.state) || 'grey') + '">' + esc(K.label(task)) + '</span></div><p class="download-source">' +
      esc(task.url) + '</p>' + source + accountLine(task, ui, ctx) + rename(task, ui, ctx) + line + meter + message(task) + choice(task, ui, ctx) + media +
      (buttons ? '<div class="download-item-actions">' + buttons + '</div>' : '') + log(task, ui) + '</article>';
  }

  /* Filters, rows and the foot line: the part redrawn on every refresh. */
  function items(data, ui, ctx) {
    const tasks = data && Array.isArray(data.tasks) ? data.tasks : [];
    const shown = tasks.filter(t => K.matches(t, ui.filter));
    const empty = '<div class="download-empty">' + ctx.icon('downloads') + '<h3>' + (tasks.length ? 'Không có lượt tải trong nhóm này' :
      'Chưa có lượt tải') + '</h3><p>Dán link ở khung Thêm video.<br>Mỗi video có tiến độ riêng.</p></div>';
    const running = data && Array.isArray(data.running) ? data.running.length : 0;
    return filters(tasks, ui) + '<div class="download-list">' +
      (shown.length ? shown.map(t => row(t, ui, ctx)).join('') : empty) + '</div><p class="download-list-foot">' + tasks.length +
      ' lượt · ' + running + ' đang chạy' + (data && data.settings ? ' / ' + data.settings.slots + ' luồng' : '') + '</p>';
  }
  /* Three parts: download-live.js redraws the tools bar (its select) only when it changed and has no focus. */
  function list(data, ui, ctx) {
    const tasks = data && Array.isArray(data.tasks) ? data.tasks : [];
    return '<div id="dl-counts">' + counters(tasks) + '</div><div id="dl-tools">' + tools(data, ui, ctx) + '</div><div id="dl-groups">' +
      groups(data, ui, ctx) + '</div><div id="dl-items">' + items(data, ui, ctx) + '</div>';
  }

  function storage(answer, ctx) {
    const computing = !!(answer && answer.computing);
    const head = '<div class="section-top"><h2 id="storage-title">Dung lượng</h2><button class="secondary small" data-action="storage-refresh"' +
      attr(ctx.offline || computing, 'disabled') + '>' + (computing ? 'Đang tính…' : 'Tính lại') + '</button></div>';
    const s = answer && answer.summary;
    let body;
    if (ctx.storageError) body = '<p class="download-item-error">' + esc(ctx.storageError) + '</p>';
    else if (!s) body = '<p class="muted">' + (answer && answer.error ? esc(answer.error) : 'Đang tính dung lượng các thư mục…') + '</p>';
    else {
      const f = s.folders || {}, d = s.drive || {}, c = s.cleanable || {}, bin = s.recycle_bin || {};
      const line = (label, value) => '<div class="key-value"><span>' + label + '</span><span>' + value + '</span></div>';
      const size = n => n == null ? '—' : esc(K.formatBytes(n) || '0 KB');
      const link = '<a href="#videos" data-action="storage-cleanable">' +
        (ctx.remote ? 'mở danh sách để Xóa video gốc (Lưu trữ chỉ làm trên PC)' : 'mở danh sách để Xóa video gốc / Lưu trữ') + '</a>';
      body = line('Ổ chứa BiliFlow', size(d.free_bytes) + ' trống / ' + size(d.total_bytes) + ' · giữ lại ' + size(d.reserve_bytes)) +
        ['input', 'output', 'reports', 'cache', 'temp'].map(name => line('<span class="mono">' + name + '</span>', size(f[name]))).join('') +
        line('Video gốc xóa được', c.error ? esc(c.error) : c.jobs ? Number(c.jobs) + ' video đã xuất xong · ' + size(c.bytes) + ' · ' + link :
          'Chưa có video nào xóa được') +
        line('Thùng rác ' + esc(bin.volume || ''), bin.error ? esc(bin.error) : size(bin.used_bytes) + ' · ' + esc(bin.items) + ' mục' +
          (bin.max_bytes ? ' · tối đa ' + size(bin.max_bytes) : '')) +
        '<p class="muted dl-note">Chỉ hiển thị. BiliFlow không tự xóa video gốc, bản xuất hay làm trống Thùng rác.' +
        (s.computed_at ? ' Tính lúc ' + esc(new Date(s.computed_at).toLocaleString('vi-VN')) + '.' : '') + '</p>';
    }
    return '<section class="panel storage-panel" id="storage-panel" aria-labelledby="storage-title">' + head + body + '</section>';
  }

  function page(data, storageAnswer, ui, ctx) {
    return '<div id="dl-notices">' + notices(data, ctx) + '</div><div class="download-layout"><div class="download-side">' + form(data, ui, ctx) +
      '<div id="dl-accounts-root">' + accountsPanel(data, ui, ctx) + '</div></div>' +
      '<section class="panel download-progress-panel" aria-labelledby="dl-progress-title"><div class="section-top"><h2 id="dl-progress-title">' +
      'Danh sách &amp; tiến độ tải</h2></div><div id="dl-progress">' + (data ? list(data, ui, ctx) : '<p class="muted">Đang tải danh sách…</p>') +
      '</div></section></div><div id="storage-root">' + storage(storageAnswer, ctx) + '</div>';
  }

  return {createUi, page, list, items, counters, tools, storage, form, row, notices, esc, accountsPanel, groups, groupCard, members,
    selectedSource, groupOf, groupOfPage};
});
