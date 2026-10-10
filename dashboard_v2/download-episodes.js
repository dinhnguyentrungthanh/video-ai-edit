/* "Tải video" (live): the episode dialog of a series page (docs/SOURCE_ACCOUNTS_PLAN.md 9.10, 9.14; M5).
 * The pure part (model, selection, what blocks "Tải N tập", the dialog HTML) runs in node for verify-download.cjs;
 * create() is the controller on live.html. Labels of seasons, episodes and variants come from the source's page: they
 * are only ever written through esc() as text, and the markup refers to them by position (data-ep-pick, data-season,
 * option values), never by the source's own ids. The server plans: the count on "Tải N tập", the missing and ambiguous
 * episodes and the scope note come from its draft and confirm answers, never from this file. Viewing, saving a draft
 * or confirming never opens a browser or asks for a ticket (the server's rule; nothing here could).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BFDownloadEpisodes = api;
})(typeof window === 'undefined' ? this : window, function () {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const attr = (on, name) => on ? ' ' + name : '';
  const text = value => typeof value === 'string' ? value : value == null ? '' : String(value);
  const SAVE_DELAY_MS = 300; // a burst of clicks is one draft
  const KEY_PATTERN = /^[A-Za-z0-9_-]{8,64}$/; // download_groups.REQUEST_KEY
  const OFFLINE = 'Mất kết nối Control Center.';

  function formatBytes(n) {
    const v = Number(n);
    if (n == null || !Number.isFinite(v) || v <= 0) return '';
    if (v < 1073741824) return Math.max(1, Math.round(v / 1048576)).toLocaleString('vi-VN') + ' MB';
    return (v / 1073741824).toLocaleString('vi-VN', {minimumFractionDigits: 1, maximumFractionDigits: 1}) + ' GB';
  }
  function seasonLabel(g) {
    if (text(g.label)) return text(g.label);
    if (g.special) return 'Tập đặc biệt';
    return Number.isInteger(g.number) ? 'Mùa ' + g.number : 'Các tập';
  }
  function episodeLabel(e) {
    if (text(e.label)) return text(e.label);
    return Number.isInteger(e.number) ? 'Tập ' + e.number : 'Tập #' + (Number(e.order) || '?');
  }
  /* The backend labels a variant without a name of its own "quality · audio": each part is shown once. */
  function variantLabel(v) {
    const parts = [...(text(v.label) || 'Bản').split(' · '), text(v.quality), text(v.audio), formatBytes(v.size)].filter(Boolean);
    return [...new Set(parts)].join(' · ');
  }

  /* GET …/episodes → the model: seasons and episodes in the server's order (never sorted here: "Tập 10" stays after
   * "Tập 2"), each episode with its position (`index`) in that order, the common variant kinds, and whether every
   * episode has one file (nothing to choose). */
  function model(answer) {
    const listing = answer && answer.listing && typeof answer.listing === 'object' ? answer.listing : {};
    const episodes = [];
    const seasons = (Array.isArray(listing.groups) ? listing.groups : []).map((g, gi) => {
      const items = (Array.isArray(g.episodes) ? g.episodes : []).map(e => {
        const variants = (Array.isArray(e.variants) ? e.variants : []).map(v => ({id: text(v.id), kind: text(v.kind), label: variantLabel(v)}));
        const item = {index: episodes.length, season: gi, key: text(e.key), label: episodeLabel(e), special: !!e.special, variants};
        episodes.push(item);
        return item;
      });
      return {index: gi, label: seasonLabel(g), special: !!g.special, episodes: items};
    });
    const kinds = (Array.isArray(listing.variant_kinds) ? listing.variant_kinds : [])
      .map(k => ({kind: text(k.kind), label: text(k.label) || text(k.kind), episodes: Number(k.episodes) || 0})).filter(k => k.kind);
    return {taskId: Number(answer && answer.task_id) || null, title: text(listing.title) || 'Phim', seasons, episodes, kinds,
      single: episodes.every(e => e.variants.length === 1), complete: listing.complete !== false, message: text(listing.message),
      count: episodes.length, fingerprint: text(answer && answer.fingerprint), max: Number(answer && answer.max_episodes) || 500};
  }

  /* The user's choice. mode: null | 'all' | 'pick'; picked: episode positions; how: 'none' (one file per episode),
   * 'kind' (one variant kind for every episode) or 'each' (a variant per episode), null until chosen. */
  function emptySelection(m) {
    return {mode: null, picked: new Set(), how: m.single ? 'none' : null, kind: null, each: new Map()};
  }
  /* A stored draft (this device's or another's) back into a selection; ids not in the list are dropped. */
  function fromDraft(draft, m) {
    const sel = emptySelection(m);
    if (!draft || typeof draft !== 'object') return sel;
    if (draft.mode === 'all' || draft.mode === 'pick') sel.mode = draft.mode;
    const byKey = new Map(m.episodes.map(e => [e.key, e]));
    if (Array.isArray(draft.episodes)) draft.episodes.forEach(key => { const e = byKey.get(text(key)); if (e) sel.picked.add(e.index); });
    if (typeof draft.variant_kind === 'string') {
      const at = m.kinds.findIndex(k => k.kind === draft.variant_kind);
      if (at >= 0) { sel.how = 'kind'; sel.kind = at; }
    } else if (draft.variants && typeof draft.variants === 'object') {
      sel.how = 'each';
      Object.entries(draft.variants).forEach(([key, id]) => {
        const e = byKey.get(key), at = e ? e.variants.findIndex(v => v.id === text(id)) : -1;
        if (at >= 0) sel.each.set(e.index, at);
      });
    }
    return sel;
  }
  const chosen = (sel, m) => sel.mode === 'all' ? m.episodes : sel.mode === 'pick' ? m.episodes.filter(e => sel.picked.has(e.index)) : [];
  /* The selection the server takes ({mode, episodes?, variant_kind? | variants?}); null before a mode is chosen. An
   * episode without a variant of its own stays out of `variants`: the server reports it as missing, never a guess. */
  function selection(sel, m) {
    if (sel.mode !== 'all' && sel.mode !== 'pick') return null;
    const items = chosen(sel, m), out = {mode: sel.mode};
    if (sel.mode === 'pick') out.episodes = items.map(e => e.key);
    if (sel.how === 'kind' && m.kinds[sel.kind]) out.variant_kind = m.kinds[sel.kind].kind;
    else if (sel.how === 'each') {
      out.variants = {};
      items.forEach(e => {
        const at = sel.each.has(e.index) ? sel.each.get(e.index) : e.variants.length === 1 ? 0 : -1;
        if (at >= 0 && e.variants[at]) out.variants[e.key] = e.variants[at].id;
      });
    }
    return out;
  }
  /* The same request is the same key (download_groups.request_hash: selection, fingerprint, skip_existing): a resend
   * after a lost answer replays the group instead of making a second one. */
  const requestText = (taskId, body) => JSON.stringify([taskId, body.selection, body.fingerprint, !!body.skip_existing]);
  function newKey(random) {
    const bytes = random(18), chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
    return 'ep-' + Array.from(bytes, b => chars[b & 63]).join('');
  }

  /* NOT_WAITING (409) detail: the group the page was split into, or the state it left NEEDS_CHOICE for. */
  const closedFrom = detail => ({group_id: detail && Number(detail.group_id) || null, state: detail && text(detail.state) || null});
  function emptyState() {
    return {taskId: null, session: 0, loading: true, model: null, sel: null, fingerprint: '', revision: null, plan: null,
      planError: null, loadError: '', saving: false, dirty: false, saveError: '', notice: '', error: '', errorList: [],
      existing: null, confirming: false, scopeOk: false, closedGroup: null, remote: null, source: ''};
  }
  /* Why "Tải N tập" cannot be pressed now ('' when it can). */
  function blocker(st, offline) {
    if (offline) return OFFLINE;
    if (st.closedGroup) return 'Trang này không còn chờ chọn tập.';
    if (st.loadError) return st.loadError;
    if (st.loading || !st.model) return 'Đang tải danh sách tập…';
    if (st.remote && st.remote.fingerprint && st.remote.fingerprint !== st.fingerprint) return 'Danh sách tập vừa đổi; bấm Tải lại danh sách.';
    if (!st.sel.mode) return 'Chọn "Tải tất cả các tập đang có" hoặc "Chọn tập".';
    if (st.saving || st.dirty) return st.saveError || 'Đang lưu lựa chọn và tính số tập…';
    if (st.saveError) return st.saveError;
    if (st.planError) return text(st.planError.error) || 'Lựa chọn chưa hợp lệ.';
    const p = st.plan;
    if (!p) return 'Chưa tính được số tập.';
    if (!(p.count > 0)) return 'Chưa có tập nào được chọn.';
    if (p.too_large) return 'Một nhóm tối đa ' + Number(p.max) + ' tập; lựa chọn này có ' + Number(p.count) + ' tập.';
    if (Array.isArray(p.missing) && p.missing.length) return 'Có tập không có bản đã chọn; chọn bản khác hoặc bỏ các tập đó.';
    if (Array.isArray(p.ambiguous) && p.ambiguous.length) return 'Có tập có hai file cùng bản; chọn bản riêng cho từng tập.';
    if (!p.complete && !st.scopeOk) return 'Đánh dấu ô xác nhận: danh sách chưa đầy đủ.';
    if (st.confirming) return 'Đang gửi…';
    return '';
  }

  /* ----- HTML (the dialog's content; morph patches it in place, so focus, scroll and checkboxes survive) ----- */
  const marks = list => new Set((Array.isArray(list) ? list : []).map(item => text(item && item.episode)));
  const currentPlan = st => st.plan && !st.dirty && !st.saving ? st.plan : null;
  function episodeRow(e, st, flags) {
    const sel = st.sel;
    const picked = sel.mode === 'all' || sel.picked.has(e.index);
    const box = sel.mode === 'pick' ? '<input type="checkbox" data-ep-pick="' + e.index + '"' + attr(picked, 'checked') + '>' : '';
    let variant = '';
    if (sel.how === 'each' && e.variants.length > 1) {
      const at = sel.each.has(e.index) ? sel.each.get(e.index) : -1;
      variant = '<select data-ep-variant="' + e.index + '" aria-label="Bản của ' + esc(e.label) + '"><option value=""' + attr(at < 0, 'selected') +
        '>Chọn bản…</option>' + e.variants.map((v, vi) => '<option value="' + vi + '"' + attr(at === vi, 'selected') + '>' + esc(v.label) +
        '</option>').join('') + '</select>';
    } else if (e.variants.length === 1) variant = '<small>' + esc(e.variants[0].label) + '</small>';
    else if (!e.variants.length) variant = '<small class="dl-ep-flag">Không có file</small>';
    const flag = (flags.missing.has(e.key) ? '<small class="dl-ep-flag">Thiếu bản đã chọn</small>' : '') +
      (flags.ambiguous.has(e.key) ? '<small class="dl-ep-flag">Hai file cùng bản</small>' : '');
    const name = '<span class="dl-ep-name">' + esc(e.label) + '</span>';
    return '<li id="dl-ep-row-' + e.index + '" class="dl-ep-row' + (picked && sel.mode ? ' picked' : '') + '">' +
      (box ? '<label>' + box + name + '</label>' : name) + variant + flag + '</li>';
  }
  function seasonBlock(season, st, flags) {
    const pick = st.sel.mode === 'pick';
    const picked = season.episodes.filter(e => st.sel.picked.has(e.index)).length;
    const tools = pick && season.episodes.length ? '<span class="dl-ep-season-tools"><button type="button" class="secondary small" ' +
      'data-action="dl-ep-season" data-season="' + season.index + '" data-on="1">Chọn cả mục này</button><button type="button" ' +
      'class="secondary small" data-action="dl-ep-season" data-season="' + season.index + '" data-on="0"' + attr(!picked, 'disabled') +
      '>Bỏ chọn mục này</button></span>' : '';
    return '<section id="dl-ep-season-' + season.index + '" class="dl-ep-season" aria-labelledby="dl-ep-season-title-' + season.index + '">' +
      '<div class="dl-ep-season-head"><h3 id="dl-ep-season-title-' + season.index + '">' + esc(season.label) + ' <small>' +
      season.episodes.length + ' tập' + (pick ? ' · đã chọn ' + picked : '') + '</small></h3>' + tools + '</div><ol class="dl-ep-list">' +
      season.episodes.map(e => episodeRow(e, st, flags)).join('') + '</ol></section>';
  }
  function variantChoice(st) {
    const m = st.model, sel = st.sel;
    if (m.single) return '<p class="muted dl-note">Mỗi tập có đúng một file; không cần chọn bản.</p>';
    const kinds = m.kinds.map((k, i) => '<option value="' + i + '"' + attr(sel.kind === i, 'selected') + '>' + esc(k.label) + ' (' +
      k.episodes + '/' + m.count + ' tập có bản này)</option>').join('');
    return '<fieldset class="dl-ep-how"><legend>Bản tải (chất lượng, âm thanh)</legend>' +
      '<label><input type="radio" name="dl-ep-how" value="kind"' + attr(sel.how === 'kind', 'checked') + '> <span>Một bản cho mọi tập</span></label>' +
      (sel.how === 'kind' ? '<select id="dl-ep-kind" aria-label="Bản cho mọi tập"><option value=""' + attr(sel.kind === null, 'selected') +
        '>Chọn bản…</option>' + kinds + '</select>' : '') +
      '<label><input type="radio" name="dl-ep-how" value="each"' + attr(sel.how === 'each', 'checked') + '> <span>Chọn bản riêng cho từng tập</span></label>' +
      '<p class="muted dl-note">Mỗi tập chỉ tải một bản. Tập không có bản đã chọn sẽ được báo; BiliFlow không tự đổi sang bản khác.</p></fieldset>';
  }
  function planLine(st, offline) {
    const p = currentPlan(st), why = blocker(st, offline), parts = [];
    if (st.sel && st.sel.mode === 'pick') parts.push('Đã đánh dấu ' + st.sel.picked.size + ' tập');
    if (p) {
      parts.push('Sẽ tải ' + Number(p.count) + ' tập');
      if (p.note) parts.push(text(p.note));
    }
    const missing = p ? [...(p.missing || []), ...(p.ambiguous || [])] : [];
    const list = missing.length ? '<ul class="dl-ep-problems">' + missing.slice(0, 20).map(x => '<li>' + esc(x && (x.label || x.episode)) + '</li>').join('') +
      (missing.length > 20 ? '<li>… và ' + (missing.length - 20) + ' tập khác</li>' : '') + '</ul>' : '';
    const again = st.saveError && !st.saving ? ' <button type="button" class="secondary small" data-action="dl-ep-save">Lưu lại lựa chọn</button>' : '';
    return '<p class="dl-ep-plan" id="dl-ep-plan" aria-live="polite">' + esc(parts.join(' · ')) + '</p>' + list +
      (why ? '<p class="muted dl-ep-why" id="dl-ep-why">' + esc(why) + again + '</p>' : '');
  }
  /* "Tải các tập còn lại" (skip_existing) is "Tải N tập" without the episodes already listed: it is off whenever that one
   * is (`why`, the blocker). */
  function existingBlock(st, why) {
    const x = st.existing;
    if (!x) return '';
    const items = (Array.isArray(x.existing) ? x.existing : []).filter(item => item && typeof item === 'object').slice(0, 50);
    const total = Math.max(Number(x.existing_count) || 0, items.length), more = total - items.length;
    const rows = items.map(item => '<li>' + esc(item.label || item.episode) + ' <small>' + (item.task_id ? 'lượt #' + Number(item.task_id)
      : item.group_id ? 'nhóm #' + Number(item.group_id) : '') + '</small></li>').join('') + (more > 0 ? '<li>… và ' + more + ' tập khác</li>' : '');
    return '<div class="dl-ep-existing" role="alert"><p><strong>' + total + ' tập đã có trong danh sách tải</strong> ' +
      '(đang tải, đang chờ hoặc chờ trong nhóm khác). BiliFlow không tạo trùng.' + (x.none ? ' Mọi tập đã chọn đều đã có: không còn tập nào ' +
      'để tải thêm.' : '') + '</p><ul>' + rows + '</ul>' + (x.none ? '' : '<button type="button" class="primary small" data-action="dl-ep-rest"' +
      attr(!!why, 'disabled') + (why ? ' aria-describedby="dl-ep-why"' : '') + '>Tải các tập còn lại</button> ') +
      '<button type="button" class="secondary small" data-action="dl-ep-rest-cancel">Để tôi chọn lại</button></div>';
  }
  function body(st, offline) {
    if (st.closedGroup) {
      const id = Number(st.closedGroup.group_id) || 0;
      if (!id) return '<p>Trang này không còn chờ chọn tập' + (st.closedGroup.state === 'CANCELLED' ? ' (đã hủy)' : !st.closedGroup.state
        ? ' (đã xóa khỏi danh sách)' : '') + '. Không tạo nhóm tập nào.</p>';
      return '<p>Trang phim này đã được tách thành nhóm tập #' + id + ' (có thể từ thiết bị khác). Không tạo thêm nhóm.</p>' +
        '<p><button type="button" class="primary small" data-action="dl-ep-group" data-group="' + id + '">Xem nhóm tập #' + id + '</button></p>';
    }
    if (st.loading || !st.model) return '<p class="muted">' + esc(st.loadError || 'Đang tải danh sách tập…') + '</p>' + (st.loadError
      ? '<button type="button" class="secondary small" data-action="dl-ep-reload">Thử tải lại danh sách</button>' : '');
    const m = st.model, sel = st.sel;
    const incomplete = m.complete ? '' : '<div class="notice dl-ep-incomplete">Danh sách chưa đầy đủ: ' + esc(m.message || 'BiliFlow chưa đọc hết các trang của phim.') +
      ' Chỉ tải được ' + m.count + ' tập đã thấy; đây không phải toàn bộ phim.</div>';
    const remote = st.remote && st.remote.fingerprint && st.remote.fingerprint !== st.fingerprint
      ? '<div class="notice" role="alert">Danh sách tập vừa đổi. <button type="button" class="secondary small" data-action="dl-ep-reload">Tải lại danh sách</button></div>'
      : st.remote && st.remote.revision > st.revision && !st.saving && !st.dirty
        ? '<div class="notice" role="status">Lựa chọn vừa được sửa ở thiết bị khác. <button type="button" class="secondary small" ' +
          'data-action="dl-ep-reload">Mở lựa chọn đã lưu</button></div>' : '';
    const mode = '<fieldset class="dl-ep-mode"><legend>Phạm vi tải</legend><label><input type="radio" name="dl-ep-mode" value="all"' +
      attr(sel.mode === 'all', 'checked') + '> <span>' + (m.complete ? 'Tải tất cả các tập đang có' : 'Tải tất cả ' + m.count + ' tập đã thấy') +
      ' (' + m.count + ' tập)</span></label><label><input type="radio" name="dl-ep-mode" value="pick"' + attr(sel.mode === 'pick', 'checked') +
      '> <span>Chọn tập</span></label></fieldset>';
    const pickTools = sel.mode === 'pick' ? '<div class="dl-ep-tools"><button type="button" class="secondary small" data-action="dl-ep-all">' +
      'Chọn tất cả</button><button type="button" class="secondary small" data-action="dl-ep-none"' + attr(!sel.picked.size, 'disabled') +
      '>Bỏ chọn tất cả</button></div>' : '';
    const p = currentPlan(st), flags = {missing: p ? marks(p.missing) : new Set(), ambiguous: p ? marks(p.ambiguous) : new Set()};
    const list = m.seasons.map(season => seasonBlock(season, st, flags)).join('');
    const reload = st.loadError ? '<div class="notice" role="alert">' + esc(st.loadError) + ' <button type="button" class="secondary small" ' +
      'data-action="dl-ep-reload">Thử tải lại danh sách</button></div>' : '';
    const notices = reload + (st.notice ? '<div class="notice" role="status">' + esc(st.notice) + '</div>' : '') + remote;
    const error = st.error ? '<div class="modal-error" role="alert">' + esc(st.error) + (st.errorList.length ? '<ul>' +
      st.errorList.slice(0, 20).map(x => '<li>' + esc(x) + '</li>').join('') + '</ul>' : '') + '</div>' : '';
    return notices + incomplete + mode + variantChoice(st) + pickTools + '<div class="dl-ep-seasons">' + list + '</div>' +
      existingBlock(st, blocker(st, offline)) + error;
  }
  function foot(st, offline) {
    const p = currentPlan(st);
    const scope = st.model && !st.model.complete && !st.closedGroup ? '<label class="check-line dl-ep-scope"><input type="checkbox" id="dl-ep-scope"' +
      attr(st.scopeOk, 'checked') + '><span>Tôi hiểu danh sách chưa đầy đủ: chỉ tải các tập đã thấy.</span></label>' : '';
    const why = blocker(st, offline);
    const label = st.confirming ? 'Đang gửi…' : p && p.confirm_label ? text(p.confirm_label) : 'Tải các tập đã chọn';
    return (st.closedGroup ? '' : planLine(st, offline) + scope) + '<div class="dl-ep-buttons"><button type="button" class="secondary" ' +
      'data-action="dl-ep-close">Đóng</button>' + (st.closedGroup ? '' : '<button type="button" class="primary" id="dl-ep-confirm" ' +
      'data-action="dl-ep-confirm"' + attr(!!why, 'disabled') + (why ? ' aria-describedby="dl-ep-why"' : '') + '>' + esc(label) + '</button>') + '</div>';
  }
  function html(st, offline) {
    const m = st.model;
    const sub = m ? m.count + ' tập' + (m.seasons.length > 1 ? ' · ' + m.seasons.length + ' mục' : '') + (st.source ? ' · ' + st.source : '') : '';
    return '<div class="modal-head"><div><h2 id="dl-ep-title">Chọn tập' + (m ? ': ' + esc(m.title) : '') + '</h2><small>' + esc(sub) +
      '</small></div><button type="button" class="icon-button" data-action="dl-ep-close" aria-label="Đóng hộp chọn tập">×</button></div>' +
      '<div class="modal-body dl-ep-body" id="dl-ep-body">' + body(st, offline) + '</div><div class="modal-foot dl-ep-foot">' + foot(st, offline) + '</div>';
  }

  /* ----- Controller (live.html). o: {store, dom: {patch}, toast, offline(), task(id), groupOfPage(id), sourceLabel(id),
   * showGroup(id), random(n)} ----- */
  function create(o) {
    let st = emptyState(), dialog = null, opener = null, timer = null, backToOpener = true, shownOffline = false, loads = 0;
    // The draft request in flight (also one of a dialog closed meanwhile): the list is read only after it, so a reopen
    // never shows an older choice than the one sent, and never shares that POST (the adapter's once()).
    let drafting = Promise.resolve();
    const keys = new Map(); // request text → idempotency key, for the life of the page
    const offline = () => !!o.offline();
    function element() {
      if (dialog) return dialog;
      dialog = document.createElement('dialog');
      dialog.id = 'dl-episodes';
      dialog.className = 'dl-episodes';
      dialog.setAttribute('aria-labelledby', 'dl-ep-title');
      dialog.innerHTML = '<div id="dl-ep-root"></div>';
      dialog.addEventListener('close', closed);
      document.body.appendChild(dialog);
      return dialog;
    }
    const isOpen = () => !!dialog && dialog.open;
    function render() {
      if (!isOpen()) return;
      shownOffline = offline();
      o.dom.patch(dialog.querySelector('#dl-ep-root'), html(st, shownOffline));
    }
    async function load(notice) {
      const session = st.session, seq = ++loads;
      clearTimeout(timer);
      st.loading = true; st.loadError = '';
      render();
      try {
        for (let wait = drafting; ; wait = drafting) { await wait; if (wait === drafting) break; }
        if (session !== st.session || seq !== loads) return;
        const answer = await o.store.loadEpisodes(st.taskId);
        if (session !== st.session || seq !== loads) return;
        const m = model(answer);
        Object.assign(st, {loading: false, model: m, fingerprint: m.fingerprint, revision: Number(answer.revision), sel: fromDraft(answer.draft, m),
          plan: answer.plan || null, planError: answer.plan_error || null, dirty: false, saveError: '', notice: notice || '', error: '',
          errorList: [], existing: null, remote: null, scopeOk: false});
      } catch (error) {
        if (session !== st.session || seq !== loads) return;
        if (error.code === 'NOT_WAITING') st.closedGroup = closedFrom(error.detail);
        else st.loadError = (st.model ? 'Không tải lại được danh sách tập: ' : 'Không tải được danh sách tập: ') + error.message;
        st.loading = false;
      }
      render();
    }
    function open(taskId) {
      opener = document.activeElement;
      clearTimeout(timer);
      st = {...emptyState(), taskId: Number(taskId), session: st.session + 1, source: o.sourceLabel(Number(taskId))};
      const d = element();
      if (!d.open) d.showModal();
      render();
      load('');
      const first = d.querySelector('[data-action="dl-ep-close"]');
      if (first) first.focus();
    }
    function closed() {
      // A choice not saved yet is sent now, so a refresh or another device opens it.
      clearTimeout(timer);
      if (st.dirty && !st.saving && st.model && !st.closedGroup) save();
      // Going to a group moves focus to its card: the opener's node may have become another button meanwhile.
      if (backToOpener && opener && opener.isConnected) opener.focus();
      backToOpener = true;
    }
    function close(toOpener) {
      if (!isOpen()) return;
      backToOpener = toOpener !== false;
      dialog.close();
    }
    /* One draft request at a time; changes made meanwhile go in the next one (never two drafts racing). */
    function changed() {
      st.dirty = true; st.existing = null; st.error = ''; st.errorList = []; st.saveError = '';
      render();
      clearTimeout(timer);
      timer = setTimeout(() => save(), SAVE_DELAY_MS);
    }
    async function save(s = st) {
      if (s.saving || !s.dirty || !s.model || s.closedGroup) return;
      const chosenNow = selection(s.sel, s.model);
      if (!chosenNow) { s.dirty = false; if (s === st) render(); return; }
      s.saving = true; s.dirty = false;
      if (s === st) render();
      let reload = '';
      const request = o.store.episodeDraft(s.taskId, {selection: chosenNow, fingerprint: s.fingerprint, revision: s.revision});
      drafting = request.catch(() => {});
      try {
        const answer = await request;
        s.revision = Number(answer.revision); s.plan = answer.plan || null; s.planError = answer.plan_error || null; s.saveError = '';
      } catch (error) {
        if (error.code === 'STALE_DRAFT') reload = 'Lựa chọn vừa được sửa ở thiết bị khác; đã mở lựa chọn đã lưu. Kiểm tra lại trước khi tải.';
        else if (error.code === 'STALE_PREVIEW') reload = 'Danh sách tập đã đổi; đã tải lại danh sách. Chọn lại trước khi tải.';
        else if (error.code === 'NOT_WAITING') s.closedGroup = closedFrom(error.detail);
        else {
          s.saveError = error.status === 0 ? 'Mất kết nối khi lưu lựa chọn.' : 'Chưa lưu được lựa chọn: ' + error.message;
          s.dirty = true;
        }
      } finally {
        s.saving = false;
      }
      if (s.dirty && !s.saveError && !s.closedGroup && !reload) { save(s); return; }
      if (s !== st) return;
      if (reload) { await load(reload); return; }
      render();
    }
    async function confirm(skipExisting) {
      if (st.confirming || !st.model) return;
      if (skipExisting && (!st.existing || st.existing.none)) { render(); return; } // "Tải các tập còn lại" only while some are new
      // Both buttons keep every check (scope box, a changed or unloaded list, an unsaved choice, variants), which the server
      // makes again; past it there is a plan, so the scope answer always goes along.
      if (blocker(st, offline())) { render(); return; }
      const body = {selection: selection(st.sel, st.model), fingerprint: st.fingerprint};
      if (st.plan && !st.plan.complete) body.confirm_scope = st.scopeOk === true;
      if (skipExisting) body.skip_existing = true;
      const request = requestText(st.taskId, body);
      try {
        if (!keys.has(request)) keys.set(request, newKey(o.random));
      } catch (_) { // no crypto.getRandomValues: nothing is sent without a key
        st.error = 'Trình duyệt này không tạo được khóa yêu cầu nên chưa gửi. Mở trang bằng trình duyệt khác.';
        render();
        return;
      }
      body.idempotency_key = keys.get(request);
      const s = st, taskId = st.taskId, plan = currentPlan(st);
      st.confirming = true; st.error = ''; st.errorList = [];
      render();
      let reload = '';
      try {
        const answer = await o.store.episodeConfirm(taskId, body);
        s.confirming = false;
        const group = answer && answer.group || {}, skipped = Array.isArray(answer && answer.existing) ? answer.existing.length : 0;
        if (s === st) close(!group.id);
        o.toast((answer && answer.replay ? 'Nhóm này đã được tạo trước đó: ' : 'Đã tạo nhóm ') + Number(group.total || 0) + ' tập' +
          (skipped ? ' (bỏ qua ' + skipped + ' tập đã có trong danh sách)' : '') + '.');
        if (group.id && s === st) o.showGroup(Number(group.id));
        return;
      } catch (error) {
        s.confirming = false;
        if (s !== st || !isOpen()) o.toast('Chưa tạo nhóm tập: ' + (error.status === 0 ? 'mất kết nối khi gửi.' : error.message), true);
        if (s !== st) return;
        const d = error.detail || {};
        if (error.code === 'ITEMS_EXIST') st.existing = {existing: d.existing, existing_count: d.existing_count,
          none: !!skipExisting || !!plan && Number(d.existing_count) >= Number(plan.count)};
        else if (error.code === 'NOT_WAITING') st.closedGroup = closedFrom(d);
        else if (error.code === 'STALE_PREVIEW') reload = 'Danh sách tập đã đổi trước khi gửi; đã tải lại. Chọn lại rồi bấm tải.';
        else {
          if (error.code === 'IDEMPOTENCY_CONFLICT') keys.delete(request);
          st.error = error.status === 0 ? 'Mất kết nối khi gửi. Bấm lại nút tải để gửi lại đúng yêu cầu này (không tạo nhóm trùng).' : error.message;
          st.errorList = Array.isArray(d.episodes) ? d.episodes.map(x => text(x && (x.label || x.episode))) : [];
        }
      }
      if (reload) { await load(reload); return; }
      render();
    }
    /* The list poll (every 2 s while the page is open): never rebuilds the dialog; it only notes that the page left
     * NEEDS_CHOICE (another device confirmed) or that the stored draft or list changed elsewhere. */
    function sync() {
      if (!isOpen() || !st.taskId) return;
      if (offline() !== shownOffline) render(); // the connection came back (or went): "Tải N tập" follows it
      const t = o.task(st.taskId);
      if (!t || t.state !== 'NEEDS_CHOICE') {
        if (!st.closedGroup && !st.confirming) { st.closedGroup = {group_id: t ? o.groupOfPage(t.id) : null, state: t ? t.state : null}; render(); }
        return;
      }
      const e = t.episodes || {}, remote = {fingerprint: text(e.fingerprint), revision: Number(e.revision)};
      if (!st.remote || st.remote.fingerprint !== remote.fingerprint || st.remote.revision !== remote.revision) { st.remote = remote; render(); }
    }
    const index = el => Number(el.dataset.epPick ?? el.dataset.epVariant);
    function click(el, action) {
      if (!action.startsWith('dl-ep-')) return false;
      const m = st.model;
      if (action === 'dl-ep-close') close();
      else if (action === 'dl-ep-reload') load('');
      else if (action === 'dl-ep-group') { const id = Number(el.dataset.group); close(!id); if (id) o.showGroup(id); }
      else if (action === 'dl-ep-confirm') confirm(false);
      else if (action === 'dl-ep-rest') confirm(true);
      else if (action === 'dl-ep-rest-cancel') { st.existing = null; render(); }
      else if (!m || !st.sel || st.closedGroup) return true;
      else if (action === 'dl-ep-save') { st.saveError = ''; st.dirty = true; save(); }
      else if (action === 'dl-ep-all') { m.episodes.forEach(e => st.sel.picked.add(e.index)); changed(); }
      else if (action === 'dl-ep-none') { st.sel.picked.clear(); changed(); }
      else if (action === 'dl-ep-season') {
        const season = m.seasons[Number(el.dataset.season)];
        if (season) { season.episodes.forEach(e => el.dataset.on === '1' ? st.sel.picked.add(e.index) : st.sel.picked.delete(e.index)); changed(); }
      }
      return true;
    }
    function change(el) {
      if (!isOpen() || !dialog.contains(el)) return false;
      const sel = st.sel;
      if (!sel || st.closedGroup) return true;
      if (el.name === 'dl-ep-mode') { sel.mode = el.value === 'pick' ? 'pick' : 'all'; changed(); }
      else if (el.name === 'dl-ep-how') { sel.how = el.value === 'each' ? 'each' : 'kind'; changed(); }
      else if (el.id === 'dl-ep-kind') { sel.kind = el.value === '' ? null : Number(el.value); changed(); }
      else if (el.dataset.epPick !== undefined) { if (el.checked) sel.picked.add(index(el)); else sel.picked.delete(index(el)); changed(); }
      else if (el.dataset.epVariant !== undefined) { if (el.value === '') sel.each.delete(index(el)); else sel.each.set(index(el), Number(el.value)); changed(); }
      else if (el.id === 'dl-ep-scope') { st.scopeOk = el.checked; render(); }
      return true;
    }
    return {open, close, sync, click, change, render, isOpen, state: () => st};
  }

  return {SAVE_DELAY_MS, KEY_PATTERN, model, emptySelection, fromDraft, selection, chosen, blocker, emptyState, html, requestText, newKey,
    create, esc};
});
