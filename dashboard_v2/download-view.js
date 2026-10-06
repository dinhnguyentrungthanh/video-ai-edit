/* "Tải video" (live) HTML: the form, the task list and the "Dung lượng" panel, built from the
 * GET /api/downloads and /api/storage-summary answers. No HTTP and no state of its own: app.js keeps
 * the page state (ui) and wires the data-action buttons. Titles, entries and log lines come from
 * remote sites: they are only ever written through esc() as text.
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
      details: new Map(), renames: new Map(), choices: new Map(), busy: new Set()};
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
      '<div class="download-demo-note"><strong>Giới hạn</strong><p>Không dùng cookie hay đăng nhập. Trang cần đăng nhập, có DRM ' +
      'hoặc đang phát trực tiếp sẽ báo lý do. Link danh sách phát hay kênh sẽ bị từ chối; hãy dán link của từng video. Trang yt-dlp không có ' +
      'bộ đọc riêng: chỉ lấy video từ 10 phút để bỏ quảng cáo; có nhiều video dài thì hỏi bạn.</p></div></section>';
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

  function rename(task, ui, ctx) {
    const id = Number(task.id);
    if (!K.canRename(task)) {
      return task.output_name ? '<p class="dl-output">Đã vào input: <strong>' + esc(task.output_name) + '</strong></p>' : '';
    }
    const draft = ui.renames.has(id) ? ui.renames.get(id) : (task.title || '');
    return '<div class="dl-rename"><input id="dl-name-' + id + '" data-rename="' + id + '" value="' + esc(draft) +
      '" maxlength="150" autocomplete="off" aria-label="Tên file của lượt ' + id + '" placeholder="Tên file khi vào input"' +
      attr(ctx.offline, 'disabled') + '><button class="secondary small" data-action="dl-rename" data-id="' + id + '"' +
      attr(ctx.offline || ui.busy.has('rename' + id), 'disabled') + '>Đổi tên</button></div>';
  }

  function choice(task, ui, ctx) {
    const id = Number(task.id);
    if (task.state !== 'NEEDS_CHOICE' || !Array.isArray(task.entries)) return '';
    const picked = ui.choices.get(id);
    return '<fieldset class="dl-choice"><legend>Trang có nhiều video. Chọn video cần tải:</legend>' + task.entries.map(entry =>
      '<label><input type="radio" name="dl-choice-' + id + '" data-choice="' + id + '" value="' + esc(entry.index) + '"' +
      attr(picked === entry.index, 'checked') + '> <span>' + esc(K.entryLine(entry)) + '</span></label>').join('') +
      '<button class="primary small" data-action="dl-choose" data-id="' + id + '"' +
      attr(ctx.offline || picked === undefined || ui.busy.has('choose' + id), 'disabled') + '>Tải video đã chọn</button></fieldset>';
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
    // Sizes, speed and time left only: the badge already names the state.
    const meta = [p.sizeText, p.speed, p.eta].filter(Boolean).join(' · ');
    const media = task.state === 'COMPLETED' && task.media && task.media.height ? '<p class="muted dl-media">' + esc(task.media.height + 'p · ' +
      [task.media.video_codec, task.media.audio_codec].filter(Boolean).join(' + ')) + '</p>' : '';
    const buttons = K.actions(task, ctx).map(a => '<button class="' + (a.id === 'cancel' ? 'danger' : 'secondary') + ' small" data-action="dl-op" data-id="' +
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
      (K.TONES[task.state] || 'grey') + '">' + esc(K.label(task)) + '</span></div><p class="download-source">' +
      esc(task.url) + '</p>' + rename(task, ui, ctx) + line + meter + message(task) + choice(task, ui, ctx) + media +
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
    return '<div id="dl-counts">' + counters(tasks) + '</div><div id="dl-tools">' + tools(data, ui, ctx) + '</div><div id="dl-items">' +
      items(data, ui, ctx) + '</div>';
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
        (ctx.remote ? 'xem danh sách (xóa chỉ làm trên PC)' : 'mở danh sách để Xóa video gốc / Lưu trữ') + '</a>';
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
    return '<div id="dl-notices">' + notices(data, ctx) + '</div><div class="download-layout">' + form(data, ui, ctx) +
      '<section class="panel download-progress-panel" aria-labelledby="dl-progress-title"><div class="section-top"><h2 id="dl-progress-title">' +
      'Danh sách &amp; tiến độ tải</h2></div><div id="dl-progress">' + (data ? list(data, ui, ctx) : '<p class="muted">Đang tải danh sách…</p>') +
      '</div></section></div><div id="storage-root">' + storage(storageAnswer, ctx) + '</div>';
  }

  return {createUi, page, list, items, counters, tools, storage, form, row, notices, esc};
});
