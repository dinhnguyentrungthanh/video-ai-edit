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
  /* Groups of episodes and "Tài khoản nguồn phim" (docs/SOURCE_ACCOUNTS_PLAN.md 9.14). */
  const GROUP_OPERATIONS = {stop: 'downloadGroupStop', resume: 'downloadGroupResume', cancel: 'downloadGroupCancel', retry: 'downloadGroupRetry',
    remove: 'downloadGroupRemove'};
  const GROUP_DONE = {stop: 'Đã gửi lệnh dừng nhóm; phần đã tải được giữ.', resume: 'Đã xếp lại các tập đã dừng của nhóm.',
    retry: 'Đã xếp lại các tập lỗi để tải lại.', cancel: 'Đã hủy nhóm; các tập chưa xong đang được dọn.', remove: 'Đã xóa nhóm khỏi danh sách.'};
  const GROUP_CONFIRM_LABELS = {cancel: 'Hủy nhóm', remove: 'Xóa nhóm', retry: 'Thử lại tập lỗi'};
  const MEMBERS_REFRESH_MS = 4000;
  const DONE = {stop: 'Đã dừng; phần đã tải được giữ.', resume: 'Đã xếp lại vào hàng đợi; tải tiếp phần đã có.',
    cancel: 'Đã gửi lệnh hủy.', retry: 'Đã xếp lại để tải từ đầu.', remove: 'Đã xóa khỏi danh sách.'};
  const LOG_REFRESH_MS = 4000;
  // The dialog's own close button is "Hủy": the confirm button of a cancel needs another word.
  const CONFIRM_LABELS = {cancel: 'Hủy lượt tải'};

  function create(o) {
    const V = o.view, K = o.core, ui = V.createUi(), esc = V.esc;
    /* In-place DOM patch from app.js (U4, the same one the video list uses): the nodes stay, so focus, an IME
     * composition, a selection, an open log (the toggle listener records it), a rename draft and the scroll of a log
     * survive the 2 s refresh. Task rows match by data-download-id; add() clears the link box on purpose. */
    const {morph, parse, patch} = o.dom;
    const loading = new Set(), loadedAt = new Map(), loadedState = new Map();
    const membersLoading = new Set(), membersSeen = new Map(); // group id → the summary its members were loaded for
    let watching = false, reveal = null; // reveal: the first task of the last add, brought into view once
    let revealGroup = null; // a group to bring into view once its card is on the page (after "Tải N tập" or "Xem nhóm")
    let revealUntil = 0; // …for this long only
    const REVEAL_MS = 10000;
    const snap = () => o.store.snapshot();
    const data = () => snap().downloads;
    const ctx = () => {
      const s = snap();
      // device: 'pc' | 'phone' from an answer of /api/phone-mode only, null while unknown (adapter.js). The account
      // buttons need 'pc': an error or a missing answer is never taken for the PC.
      const device = s.device === 'pc' || s.device === 'phone' ? s.device : null;
      return {icon: o.icon, offline: !!s.offline, remote: !!s.remote || device === 'phone', device, deviceChecking: !!s.device_checking,
        deviceError: s.device_error || '', error: s.downloads_error || '', storageError: s.storage_error || '', data: s.downloads, now: Date.now()};
    };
    const task = id => ((data() || {}).tasks || []).find(t => t.id === Number(id));
    const groupById = id => V.groupOf(data(), id);
    /* The episode dialog (download-episodes.js): its own <dialog>, patched in place; the list poll only tells it what
     * changed elsewhere. Its writes skip the list refresh except "Tải N tập". */
    const EP = o.episodes ? o.episodes.create({
      store: o.store, dom: o.dom, toast: o.toast, offline: () => ctx().offline, task,
      groupOfPage: id => (V.groupOfPage(data(), id) || {}).id || null,
      sourceLabel: id => { const t = task(id); return t && t.media && t.media.source_label || ''; },
      showGroup: id => showGroup(id),
      random: n => window.crypto.getRandomValues(new Uint8Array(n)),
    }) : null;

    function html() { return V.page(data(), snap().storage_summary, ui, ctx()); }
    function watch(on) {
      if (on === watching) return;
      watching = on;
      o.store.watchDownloads(on, 2000);
      if (!on && EP) EP.close();
    }
    function forget() {
      if (!data()) return;
      const known = new Map((data().tasks || []).map(t => [t.id, t]));
      [ui.open, ui.details, ui.renames].forEach(m => [...m.keys()].forEach(id => { if (!known.has(id)) m.delete(id); }));
      [...ui.choices.keys()].forEach(id => { if (!known.has(id) || known.get(id).state !== 'NEEDS_CHOICE') ui.choices.delete(id); });
      const groups = new Set((data().groups || []).map(g => g.id));
      [ui.groupsOpen, ui.members, membersSeen].forEach(m => [...m.keys()].forEach(id => { if (!groups.has(id)) m.delete(id); }));
      const sources = data().accounts && Array.isArray(data().accounts.sources) ? data().accounts.sources : [];
      if (ui.account !== null && !sources.some(item => item.id === ui.account)) ui.account = null;
    }
    /* A new answer: every part is patched in place (see morph). */
    function refresh() {
      const progress = o.$('#dl-progress');
      if (!progress) return false;
      forget();
      const d = data(), c = ctx();
      patch(progress, d ? V.list(d, ui, c) : '<p class="muted">Đang tải danh sách…</p>');
      revealAdded(progress);
      revealGroupCard(progress);
      const accounts = o.$('#dl-accounts-root');
      if (accounts) patch(accounts, V.accountsPanel(d, ui, c));
      const notices = o.$('#dl-notices');
      if (notices) patch(notices, V.notices(d, c));
      const storage = o.$('#storage-root');
      if (storage) patch(storage, V.storage(snap().storage_summary, c));
      const form = o.$('.download-form');
      if (form) morph(form, parse(V.form(d, ui, c)).firstElementChild);
      reloadOpenLogs();
      reloadOpenGroups();
      if (EP) EP.sync();
      return true;
    }
    function revealGroupCard(progress) {
      const card = revealGroup === null ? null : progress.querySelector('#dl-group-' + Number(revealGroup));
      if (!card) {
        if (revealGroup !== null && Date.now() > revealUntil) revealGroup = null;
        return;
      }
      revealGroup = null;
      card.scrollIntoView({block: 'nearest'});
      if (!document.querySelector('dialog[open]')) card.focus({preventScroll: true});
    }
    function showGroup(id) {
      revealGroup = Number(id);
      revealUntil = Date.now() + REVEAL_MS;
      if (!refresh()) revealGroup = null;
    }
    /* The episodes of an open group: loaded when opened, again when its summary changes or every 4 s while it runs. */
    const signature = g => JSON.stringify([g.state, g.done, g.counts, g.percent]);
    function reloadOpenGroups() {
      ui.groupsOpen.forEach(id => {
        const g = groupById(id), seen = membersSeen.get(id), loaded = ui.members.get(id);
        if (!g) return;
        const old = !loaded || Date.now() - (loaded.at || 0) > MEMBERS_REFRESH_MS;
        if (!seen || seen !== signature(g) || (old && (!g.finished || (loaded && loaded.error)))) loadMembers(id);
      });
    }
    async function loadMembers(id) {
      if (membersLoading.has(id)) return;
      membersLoading.add(id);
      const g = groupById(id);
      try {
        const detail = await o.store.loadGroup(id);
        ui.members.set(id, {members: Array.isArray(detail.members) ? detail.members : [], at: Date.now()});
      } catch (error) {
        ui.members.set(id, {members: [], error: 'Không tải được danh sách tập: ' + error.message, at: Date.now()});
      } finally {
        membersLoading.delete(id);
        if (g) membersSeen.set(id, signature(g));
        if (ui.groupsOpen.has(id)) refresh();
      }
    }
    function revealAdded(progress) {
      const row = reveal === null ? null : progress.querySelector('[data-download-id="' + Number(reveal) + '"]');
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
    function scopeOf(t) {
      return t.group && t.group.group_id ? groupById(t.group.group_id) : t.state === 'EXPANDED' ? V.groupOfPage(data(), t.id) : null;
    }
    function op(id, operation) {
      const t = task(id), a = t && K.actions(t, {...ctx(), group: scopeOf(t)}).find(x => x.id === operation);
      if (!a || !a.enabled) return;
      if (operation === 'episodes') { if (EP) EP.open(t.id); return; }
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
    function groupOp(groupId, operation) {
      const g = groupById(groupId), a = g && K.groupActions(g, ctx()).find(x => x.id === operation);
      if (!a || !a.enabled) return;
      const prefer = '#dl-group-' + g.id + ' button:not([disabled])';
      const go = () => run('group-' + operation + g.id, GROUP_DONE[operation], async () => {
        const result = await o.store.downloadAction(GROUP_OPERATIONS[operation], g.id, {});
        if (ui.groupsOpen.has(g.id)) loadMembers(g.id);
        return result;
      }, prefer);
      if (!a.confirm || operation === 'stop' || operation === 'resume') { go(); return; }
      const label = GROUP_CONFIRM_LABELS[operation] || a.label;
      o.showModal(label + ' · nhóm N' + g.id, '<p><strong>' + esc(g.title || 'Nhóm tập') + '</strong></p><p>' + esc(a.confirm) + '</p>',
        async () => { await go(); return true; }, label);
    }
    /* "Tài khoản nguồn phim": only a press on the PC posts; the panel then follows the list poll (never the 202). */
    function accountOp(operation) {
      const source = V.selectedSource((data() || {}).accounts, ui), a = source && K.accountActions(source, ctx()).find(x => x.id === operation);
      if (!a || !a.enabled) return;
      const label = source.label || source.id;
      const done = {login: 'Đã mở cửa sổ đăng nhập ' + label + ' trên PC. Đăng nhập trên trang chính thức của nguồn; trạng thái tự cập nhật khi xong.',
        'cancel-login': 'Đã gửi lệnh hủy đăng nhập ' + label + '.', disconnect: 'Đã ngắt kết nối ' + label + '.'}[operation];
      const go = () => run('account-' + operation, done, () => o.store.accountAction(o.contracts.accountOps[operation], source.id),
        '#dl-accounts button:not([disabled])');
      if (operation !== 'disconnect') { go(); return; }
      const waiting = Number(source.waiting_tasks) || 0;
      o.showModal('Ngắt kết nối · ' + label, '<p>Xóa phiên đăng nhập đã lưu của <strong>' + esc(label) + '</strong> trên máy này?</p><p>Các lượt ' +
        'tải sau của nguồn này, và lượt nào cần lấy vé mới, sẽ chờ bạn đăng nhập lại' + (waiting ? ' (hiện đã có ' + waiting +
        ' lượt chờ đăng nhập)' : '') + '. File đang truyền không bị ngắt: việc truyền file không dùng phiên đăng nhập.</p>',
        async () => { await go(); return true; }, 'Ngắt kết nối');
    }
    /* "Kiểm tra lại" while the page's mode is unknown: GET /api/phone-mode only, never an account POST; the panel
     * follows the answer (one check at a time: the store shares a request in flight). */
    function checkMode() {
      if (ui.busy.has('mode-check')) return;
      ui.busy.add('mode-check');
      refresh();
      o.store.loadPhone().finally(() => { ui.busy.delete('mode-check'); refresh(); });
    }
    function showAccount(sourceId) {
      ui.account = String(sourceId);
      refresh();
      const panel = o.$('#dl-accounts');
      if (!panel) return;
      panel.scrollIntoView({block: 'nearest'});
      // The source box, not Đăng nhập: a held Enter or a double click on the row's button must never open a sign-in window.
      const target = panel.querySelector('#dl-account-source');
      if (target) target.focus({preventScroll: true});
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
      if (EP && EP.click(el, action)) return true;
      if (action === 'dl-add') add();
      else if (action === 'dl-group-op') groupOp(el.dataset.group, el.dataset.op);
      else if (action === 'dl-group-show') showGroup(el.dataset.group);
      else if (action === 'dl-account') accountOp(el.dataset.op);
      else if (action === 'dl-account-show') showAccount(el.dataset.source);
      else if (action === 'dl-mode-check') checkMode();
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
      if (EP && EP.change(el)) return true;
      if (el.id === 'dl-rights') ui.rights = el.checked;
      else if (el.id === 'dl-account-source') { ui.account = el.value; refresh(); }
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
      if (el.matches && el.matches('.download-group-members[data-group-members]')) {
        const id = Number(el.dataset.groupMembers);
        if (!el.open) { ui.groupsOpen.delete(id); return; }
        ui.groupsOpen.add(id);
        refresh();
        if (!ui.members.has(id) || ui.members.get(id).error) loadMembers(id);
        return;
      }
      if (!el.matches || !el.matches('.download-log[data-log-id]')) return;
      const id = Number(el.dataset.logId);
      if (!el.open) { ui.open.delete(id); return; }
      ui.open.add(id);
      const detail = ui.details.get(id);
      if (!detail || detail.error) loadLog(id);
    }, true);

    return {html, watch, refresh, click, change, input, key, ui, episodes: EP};
  }
  return {create};
});
