/* "Tải video" on live.html: page state, in-place refresh and the dl-* actions. app.js asks it for the
 * page HTML and hands it the clicks, changes, inputs and keys it does not handle itself. Every write goes
 * through the live store (adapter.js: token, one request per button, 403 → one new session); the
 * backend checks every link and state again. The demo page (index.html) never loads this file.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BFDownloadLive = api;
})(typeof window === 'undefined' ? this : window, function () {
  'use strict';
  const OPERATIONS = {stop: 'downloadStop', resume: 'downloadResume', cancel: 'downloadCancel', retry: 'downloadRetry', remove: 'downloadRemove'};
  const DONE = {stop: 'Đã dừng; phần đã tải được giữ.', resume: 'Đã xếp lại vào hàng đợi; tải tiếp phần đã có.',
    cancel: 'Đã gửi lệnh hủy.', retry: 'Đã xếp lại để tải từ đầu.', remove: 'Đã xóa khỏi danh sách.'};
  const LOG_REFRESH_MS = 4000;
  // The dialog's own close button is "Hủy": the confirm button of a cancel needs another word.
  const CONFIRM_LABELS = {cancel: 'Hủy lượt tải'};

  /* In-place DOM patch: the nodes stay, so focus, an IME composition, a selection, an open <details>
   * and the scroll of a log survive the 2 s refresh; only attributes and text that changed are written.
   * Children match by key (a task row, an id) or by position with the same tag. */
  const keyOf = node => node.nodeType === 1 ? node.getAttribute('data-download-id') || node.id || '' : '';
  const same = (a, b) => a.nodeType === b.nodeType && a.nodeName === b.nodeName && keyOf(a) === keyOf(b);
  function morph(from, to) {
    if (from.nodeType !== 1) { if (from.nodeValue !== to.nodeValue) from.nodeValue = to.nodeValue; return; }
    const keepOpen = from.nodeName === 'DETAILS'; // the user opens and closes it; the toggle listener records it
    for (const {name} of [...from.attributes]) if (!to.hasAttribute(name) && !(keepOpen && name === 'open')) from.removeAttribute(name);
    for (const {name, value} of [...to.attributes]) if (from.getAttribute(name) !== value) from.setAttribute(name, value);
    if (from.nodeName === 'INPUT' && (from.type === 'checkbox' || from.type === 'radio')) from.checked = to.hasAttribute('checked');
    if (from.nodeName === 'TEXTAREA') return; // its text is the user's draft; add() clears it on purpose
    morphChildren(from, to);
    if (from.nodeName === 'SELECT' && from !== document.activeElement) from.value = to.value;
  }
  function morphChildren(from, to) {
    const next = [...to.childNodes];
    const keyed = new Map([...from.childNodes].filter(keyOf).map(node => [keyOf(node), node]));
    const wanted = new Set(next.map(keyOf).filter(Boolean));
    keyed.forEach((node, key) => { if (!wanted.has(key)) node.remove(); });
    next.forEach((node, i) => {
      const at = from.childNodes[i] || null, key = keyOf(node), old = key ? keyed.get(key) : at;
      if (old && old.parentNode === from && same(old, node)) {
        if (old !== at) from.insertBefore(old, at);
        morph(old, node);
      } else from.insertBefore(node, at);
    });
    while (from.childNodes.length > next.length) from.lastChild.remove();
  }
  function parse(html) { const box = document.createElement('div'); box.innerHTML = html; return box; }
  const patch = (el, html) => morphChildren(el, parse(html));

  function create(o) {
    const V = o.view, K = o.core, ui = V.createUi(), esc = V.esc;
    const loading = new Set(), loadedAt = new Map(), loadedState = new Map();
    let watching = false, reveal = null; // reveal: the first task of the last add, brought into view once
    const snap = () => o.store.snapshot();
    const data = () => snap().downloads;
    const ctx = () => {
      const s = snap();
      return {icon: o.icon, offline: !!s.offline, remote: !!s.remote, error: s.downloads_error || '', storageError: s.storage_error || ''};
    };
    const task = id => ((data() || {}).tasks || []).find(t => t.id === Number(id));

    function html() { return V.page(data(), snap().storage_summary, ui, ctx()); }
    function watch(on) {
      if (on === watching) return;
      watching = on;
      o.store.watchDownloads(on, 2000);
    }
    function forget() {
      if (!data()) return;
      const known = new Map((data().tasks || []).map(t => [t.id, t]));
      [ui.open, ui.details, ui.renames].forEach(m => [...m.keys()].forEach(id => { if (!known.has(id)) m.delete(id); }));
      [...ui.choices.keys()].forEach(id => { if (!known.has(id) || known.get(id).state !== 'NEEDS_CHOICE') ui.choices.delete(id); });
    }
    /* A new answer: every part is patched in place (see morph). */
    function refresh() {
      const progress = o.$('#dl-progress');
      if (!progress) return false;
      forget();
      const d = data(), c = ctx();
      patch(progress, d ? V.list(d, ui, c) : '<p class="muted">Đang tải danh sách…</p>');
      revealAdded(progress);
      const notices = o.$('#dl-notices');
      if (notices) patch(notices, V.notices(d, c));
      const storage = o.$('#storage-root');
      if (storage) patch(storage, V.storage(snap().storage_summary, c));
      const form = o.$('.download-form');
      if (form) morph(form, parse(V.form(d, ui, c)).firstElementChild);
      reloadOpenLogs();
      return true;
    }
    function revealAdded(progress) {
      const row = reveal === null ? null : progress.querySelector('[data-download-id="' + reveal + '"]');
      if (!row) return;
      reveal = null;
      const list = row.closest('.download-list');
      if (list && list.scrollHeight > list.clientHeight) list.scrollTop += row.getBoundingClientRect().top - list.getBoundingClientRect().top;
      else row.scrollIntoView({block: 'nearest'}); // the phone layout: the page scrolls, not the list
    }
    /* Open logs: loaded when opened, again on a state change or after an error, and every 4 s while the task
     * runs; only for rows the filter shows. */
    function reloadOpenLogs() {
      ui.open.forEach(id => {
        const t = task(id);
        if (!t || !K.matches(t, ui.filter)) return;
        const detail = ui.details.get(id), old = Date.now() - (loadedAt.get(id) || 0) > LOG_REFRESH_MS;
        const live = t.state === 'QUEUED' || K.RUNNING.includes(t.state);
        if (!detail || loadedState.get(id) !== t.state || (old && (live || detail.error))) loadLog(id);
      });
    }
    async function loadLog(id) {
      if (loading.has(id)) return;
      loading.add(id);
      const t = task(id);
      try {
        const detail = await o.store.loadDownload(id);
        ui.details.set(id, {events: detail.events || [], log: detail.log || []});
        loadedState.set(id, detail.task ? detail.task.state : t && t.state);
      } catch (error) {
        ui.details.set(id, {error: error.message});
        loadedState.set(id, t && t.state);
      } finally {
        loading.delete(id);
        loadedAt.set(id, Date.now());
        if (ui.open.has(id)) refresh();
      }
    }

    async function add() {
      if (ui.busy.has('add')) return;
      let urls;
      try {
        if (ctx().offline) throw new Error('Mất kết nối Control Center. Chưa gửi link.');
        urls = K.checkBatch(ui.text, ui.rights);
      } catch (error) {
        ui.error = error.message; ui.lineErrors = [];
        refresh();
        const area = o.$('#dl-urls');
        if (area) area.focus();
        return;
      }
      ui.busy.add('add'); ui.error = ''; ui.lineErrors = [];
      try {
        refresh();
        const result = await o.store.downloadAction('downloadAdd', null, {urls, rights_confirmed: true});
        ui.text = ''; ui.rights = false; ui.filter = 'all';
        const area = o.$('#dl-urls');
        if (area) area.value = '';
        reveal = result && result.tasks && result.tasks[0] ? result.tasks[0].id : null; // new tasks are at the end (oldest first)
        o.toast('Đã thêm ' + (result && result.tasks ? result.tasks.length : urls.length) + ' lượt tải.');
      } catch (error) {
        ui.lineErrors = K.batchErrors(error);
        ui.error = ui.lineErrors.length ? 'Cả lô bị từ chối, chưa thêm link nào:' : error.message;
      } finally {
        ui.busy.delete('add');
        refresh();
      }
    }
    /* A control that disappears or was disabled meanwhile (Dừng after a stop, a removed row) leaves focus on
     * the page body: once any dialog has closed it goes to `prefer` (a selector), else the active filter. */
    function keepFocus(prefer) {
      setTimeout(() => {
        const at = document.activeElement;
        if ((at && at !== document.body) || document.querySelector('dialog[open]')) return;
        const target = (prefer && o.$(prefer)) || o.$('.download-filters .filter-tab.active');
        if (target && !target.disabled) target.focus({preventScroll: true});
      });
    }
    const rowButton = id => '[data-download-id="' + Number(id) + '"] button:not([disabled])';
    async function run(key, label, fn, prefer) {
      ui.busy.add(key);
      try { refresh(); const result = await fn(); if (label) o.toast(label); return result; }
      catch (error) { o.toast(error.message, true); return null; }
      finally { ui.busy.delete(key); refresh(); keepFocus(prefer); }
    }
    function op(id, operation) {
      const t = task(id), a = t && K.actions(t, ctx()).find(x => x.id === operation);
      if (!a || !a.enabled) return;
      const go = () => run(operation + t.id, DONE[operation], () => o.store.downloadAction(OPERATIONS[operation], t.id, {}), rowButton(t.id));
      if (!a.confirm) { go(); return; }
      const label = CONFIRM_LABELS[operation] || a.label;
      o.showModal(label + ' · lượt ' + t.id, '<p><strong>' + esc(t.title || t.url) + '</strong></p><p>' + esc(a.confirm) + '</p>',
        async () => { await go(); return true; }, label);
    }
    function rename(id) {
      const t = task(id), field = o.$('[data-rename="' + Number(id) + '"]');
      if (!t || !field) return;
      const name = field.value.trim();
      if (!name) { o.toast('Tên không được để trống.', true); return; }
      run('rename' + t.id, 'Đã đổi tên; dùng khi file vào input.', async () => {
        const result = await o.store.downloadAction('downloadRename', t.id, {name});
        ui.renames.delete(t.id);
        return result;
      }, '[data-rename="' + t.id + '"]');
    }
    function choose(id) {
      const t = task(id), index = ui.choices.get(Number(id));
      if (!t || index === undefined) return;
      run('choose' + t.id, 'Đã chọn video; lượt tải xếp lại vào hàng đợi.', async () => {
        const result = await o.store.downloadAction('downloadChoose', t.id, {entry_index: index});
        ui.choices.delete(t.id);
        return result;
      }, rowButton(t.id));
    }
    /* The ids the dialog listed go with the request: a task stopped meanwhile keeps its part. */
    function cleanup() {
      const temp = (data() || {}).temp || {}, ids = Array.isArray(temp.ids) ? temp.ids.map(Number) : null;
      if (!temp.tasks) return;
      o.showModal('Dọn file tạm', '<p>Xóa file tạm của ' + Number(temp.tasks) + ' lượt đã dừng, lỗi hoặc bị ngắt (khoảng ' +
        esc(K.formatBytes(temp.bytes)) + ')?</p><p>Các lượt này chuyển sang "Đã dọn file tạm"; thử lại sẽ tải từ đầu. ' +
        'File trong input không bị đụng tới.</p>', async () => {
        const result = await o.store.downloadAction('downloadCleanup', null, ids ? {confirm: true, ids} : {confirm: true});
        o.toast('Đã dọn file tạm của ' + Number(result.tasks) + ' lượt · ' + (K.formatBytes(result.freed_bytes) || '0 KB') + '.');
        keepFocus('[data-action="dl-cleanup"]');
        return true;
      }, 'Dọn file tạm');
    }

    /* Returns true when the action was a download one (handled here). */
    function click(el, action, event) {
      if (action === 'dl-add') add();
      else if (action === 'dl-op') op(el.dataset.id, el.dataset.op);
      else if (action === 'dl-rename') rename(el.dataset.id);
      else if (action === 'dl-choose') choose(el.dataset.id);
      else if (action === 'dl-filter') { ui.filter = el.dataset.filter; refresh(); }
      else if (action === 'dl-cleanup') cleanup();
      else if (action === 'storage-refresh') o.store.loadStorage(true);
      else if (action === 'storage-cleanable') { if (event) event.preventDefault(); o.openCleanable(); }
      else return false;
      return true;
    }
    function change(el) {
      if (el.id === 'dl-rights') ui.rights = el.checked;
      else if (el.id === 'dl-slots') {
        const value = Number(el.value);
        run('slots', 'Tải đồng thời ' + value + ' video. Lượt đang chạy không bị ngắt.',
          () => o.store.downloadAction('downloadSettings', null, {slots: value}), '#dl-slots');
      }
      else if (el.dataset && el.dataset.choice) { ui.choices.set(Number(el.dataset.choice), Number(el.value)); refresh(); }
      else return false;
      return true;
    }
    function input(el) {
      if (el.id === 'dl-urls') {
        ui.text = el.value;
        if (ui.error) { ui.error = ''; ui.lineErrors = []; refresh(); }
      } else if (el.dataset && el.dataset.rename) ui.renames.set(Number(el.dataset.rename), el.value);
      else return false;
      return true;
    }
    /* Enter in a rename box renames (not while an IME composes). */
    function key(event) {
      const el = event.target;
      if (event.key !== 'Enter' || event.isComposing || !el.dataset || !el.dataset.rename) return false;
      event.preventDefault();
      rename(el.dataset.rename);
      return true;
    }
    /* <details> toggle does not bubble: listen in the capture phase. */
    document.addEventListener('toggle', event => {
      const el = event.target;
      if (!el.matches || !el.matches('.download-log[data-log-id]')) return;
      const id = Number(el.dataset.logId);
      if (!el.open) { ui.open.delete(id); return; }
      ui.open.add(id);
      const detail = ui.details.get(id);
      if (!detail || detail.error) loadLog(id);
    }, true);

    return {html, watch, refresh, click, change, input, key, ui};
  }
  return {create};
});
