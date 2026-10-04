/* Review dialog controller. Opens <dialog id="review-dialog"> for #review/<id>/<view>, loads the queue
 * through store.review(id) (adapter in live.html, demo store in index.html), renders cards in batches,
 * polls the queue every 3 s while the tab is visible (identity change → rebuild, version change → patch
 * in place, the playing <video> is never rebuilt) and pauses the dashboard's /api/status polling while
 * open (Q7). R1: the selected card loads its evidence; ▶ plays its range in the one shared <video>; ⤢ zooms
 * a card (Esc unzooms before it closes).
 * R2: the user decides (Giữ / Làm mờ / Cắt / Cần xem thêm / Xóa quyết định, region buttons, studio /
 * platform memory) with the classic confirms in a V2 dialog (S5), keys 1–4 / ←→ / Space / Z (P9), undo (P10),
 * "Tự chuyển cảnh" and sticky cards (P8). Each choice applies at once and goes to the job's serial write
 * chain (adapter, 6.7); the queue the server returns applies only for the last pending write, polling waits
 * while writes are pending, a failed write reloads the queue and reopens its card (P14). Closing the dialog
 * never cancels a write; its error then shows in the dashboard toast. Nothing here decides on its own.
 * Bulk actions and export stay in R3.
 */
(function (root) {
  'use strict';
  const R = root.BFReviewCore, Cards = root.BFReviewCards, Media = root.BFReviewMedia, D = root.BFReviewDetail;
  const HASH = /^review\/(\d{1,9})(?:\/(overview|downloads|videos|queue|logos|settings))?$/;
  const POLL_MS = 3000, PREFETCH_MS = 600;

  function parseHash(hash) {
    const m = HASH.exec(String(hash || ''));
    return m ? {id: Number(m[1]), view: m[2] || 'overview'} : null;
  }

  const AUTO_KEY = 'biliflow.review.autoNext'; // shared with the classic page (same origin)
  function readAutoNext() { try { return localStorage.getItem(AUTO_KEY) !== '0'; } catch (_) { return true; } }
  /* The classic isTyping() / spaceActivates(): shortcuts stay off while typing; Space keeps its native role on controls. */
  function isTyping(target) {
    if (!target || target === document.body) return false;
    if (target.isContentEditable || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT') return true;
    return target.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit', 'reset'].includes(String(target.type).toLowerCase());
  }
  function spaceActivates(target) { return !!(target && target !== document.body && target.closest && target.closest('button,summary,a[href],input,select,textarea,label,[role="button"],[contenteditable="true"]')); }

  /* deps: {dialog, store, getJob(id), oldUrl(id, view), requestClose(view), toast(text, error)} */
  function create(deps) {
    const dialog = deps.dialog, rootEl = dialog.querySelector('#review-root');
    const stats = root.BFReviewStats = {opens: 0, fullRenders: 0, cardRenders: 0, patches: 0, polls: 0, cardsRendered: 0, maxImages: 0, evidenceFetches: 0, sessionRefreshes: 0};
    const loader = Media.createImageLoader({max: 2, stats});
    const player = Media.createPlayer({stats, key: () => s && s.mediaKey, refreshKey: () => refreshKey(), videoUrl: k => s.api.videoUrl(k),
      probe: k => s.api.probeVideo(k), ui: playerEvent});
    let s = null, more = null, prefetchTimer = null, autoNext = readAutoNext();
    const writes = new Map(); // job id → {epoch, resync, reopen, failed}: outlives the dialog, like the adapter's chain
    const writeState = id => { if (!writes.has(id)) writes.set(id, {epoch: 0, resync: false, reopen: null, failed: false}); return writes.get(id); };

    /* Media of one item, as the cards render it. A cleaned or archived source shows report images only (R1.6). */
    function media(x) {
      const lockedSource = s.lock.cleaned || s.lock.archived, hasKey = !!s.mediaKey && !lockedSource;
      const ev = lockedSource ? null : s.evidence.has(x.id) ? s.evidence.get(x.id) : s.evidenceLoading.has(x.id) ? undefined : null;
      const ps = player.state(), info = ps.reason ? {reason: ps.reason} : ev && ev.video;
      const playable = hasKey && R.hasPlayer(x) && ps.available && !(ev && ev.video && ev.video.available === false);
      const reason = lockedSource ? R.videoReason({reason: s.lock.cleaned ? 'source_cleaned' : 'source_missing'}, true) : R.videoReason(info, hasKey);
      return {ev, hasKey, playable, reason, frameUrl: t => s.api.frameUrl(x.id, t, s.mediaKey), mediaUrl: p => s.api.mediaUrl(p)};
    }
    function ctx() {
      return {job: s.job, queue: s.queue, map: s.map, filter: s.filter, list: s.list, focusId: s.focusId, zoomId: s.zoomId, lock: s.lock, techOpen: s.techOpen,
        offline: s.offline, readonly: s.lock.readonly || s.offline, oldUrl: deps.oldUrl(s.id, s.view), media};
    }
    function shell(headHtml, bodyHtml) {
      rootEl.innerHTML = '<div class="rv-head">' + headHtml + '</div><div class="rv-body">' + bodyHtml + '</div>' +
        '<div class="rv-foot"><span class="rv-keys">Phím: 1–4 quyết định · ←/→ thẻ trước/sau · Space phát/dừng · Z hoàn tác · Esc đóng</span>' +
        '<label class="rv-auto" title="Tự sang mục chưa duyệt kế tiếp"><input type="checkbox" data-review="auto"' + (autoNext ? ' checked' : '') + '> Tự chuyển cảnh</label>' +
        '<button class="secondary" type="button" data-review="close">Đóng</button></div>';
    }
    function message(text) {
      const name = s.job ? s.job.name : '';
      shell('<div class="rv-title-row"><div class="rv-title"><h2 id="review-title">' + Cards.esc('Duyệt cảnh · #' + s.id + (name ? ' · ' + name : '')) + '</h2></div>' +
        '<button class="icon-button" type="button" data-review="close" aria-label="Đóng hộp duyệt">×</button></div>', '<div class="notice rv-message" role="status">' + Cards.esc(text) + '</div>');
    }
    const cardEl = id => [...rootEl.querySelectorAll('article.rv-card')].find(el => el.dataset.item === id) || null;

    /* Images of a card: its main image and its strip frames. A frame URL gets a new media key after a 403. */
    function frameRefresher(x, t) {
      return failedUrl => {
        // As the classic fetchFrame: a frame refused with an older key retries with the current one; only a
        // refusal of the current key asks for a new session.
        const used = new URL(failedUrl, location.href).searchParams.get('k');
        const rebuild = () => s && s.mediaKey ? s.api.frameUrl(x.id, t, s.mediaKey) : null;
        return s && s.mediaKey && used !== s.mediaKey ? Promise.resolve(rebuild()) : refreshKey().then(rebuild);
      };
    }
    function watchCard(el, priority) {
      const x = s.map.get(el.dataset.item);
      if (!x) return;
      const art = el.querySelector('.rv-art'), img = art.querySelector('img'), m = media(x), remote = m.ev && m.hasKey && m.ev.strongest && img && img.dataset.src.includes('/frame?');
      loader.watch(art, img ? img.dataset.src : '', priority, remote ? frameRefresher(x, m.ev.strongest.t) : null);
      for (const thumb of el.querySelectorAll('.rv-thumb:not(.ghost)')) {
        loader.watch(thumb, thumb.querySelector('img').dataset.src, priority ? 5 : 0, thumb.dataset.remote ? frameRefresher(x, Number(thumb.dataset.t)) : null);
      }
    }
    function watchCards(cards) { for (const el of cards) watchCard(el, el.classList.contains('on') ? 10 : 0); }

    /* Cards: the first batch (enough to include the focus), then +24 when the end comes near. */
    function renderCards() {
      const box = rootEl.querySelector('.rv-cards');
      if (!box) return;
      s.list = R.listItems(s.queue, s.filter, s.sticky);
      if (!s.list.some(x => x.id === s.focusId)) s.focusId = R.pickFocus(s.list);
      if (!s.list.some(x => x.id === s.zoomId)) s.zoomId = null;
      const at = s.list.findIndex(x => x.id === s.focusId), playing = player.current();
      s.rendered = Math.min(s.list.length, Math.max(Cards.BATCH, Math.ceil((at + 1) / Cards.BATCH) * Cards.BATCH));
      const c = ctx();
      box.innerHTML = s.list.length ? s.list.slice(0, s.rendered).map(x => Cards.card(c, x)).join('') : Cards.empty(c);
      // The shared <video> moves into the new card of the same item (it keeps playing); else it is released.
      const host = playing ? cardEl(playing) : null;
      if (host) player.rehost(host.querySelector('.rv-art')); else if (playing) player.release();
      loader.forget();
      watchCards(box.querySelectorAll('article.rv-card'));
      stats.cardRenders++; stats.cardsRendered = s.rendered;
      rootEl.querySelector('.rv-body').scrollTop = 0;
      const focus = box.querySelector('article.rv-card.on');
      if (focus && at >= Cards.BATCH) focus.scrollIntoView({block: 'nearest'});
      if (s.zoomId) drawCrop(cardEl(s.zoomId));
      focusEvidence();
    }
    function renderMore() {
      if (!s || s.rendered >= s.list.length) return;
      const box = rootEl.querySelector('.rv-cards'), c = ctx(), start = s.rendered;
      s.rendered = Math.min(s.list.length, start + Cards.BATCH);
      box.insertAdjacentHTML('beforeend', s.list.slice(start, s.rendered).map(x => Cards.card(c, x)).join(''));
      watchCards([...box.querySelectorAll('article.rv-card')].slice(start));
      stats.cardsRendered = s.rendered;
    }
    function renderAll() {
      const c = ctx();
      shell(Cards.header(c), '<div class="rv-cards review-scenes"></div><div class="rv-sentinel" aria-hidden="true"></div>');
      const body = rootEl.querySelector('.rv-body');
      loader.attach(body);
      if (more) more.disconnect();
      more = typeof IntersectionObserver === 'function' ? new IntersectionObserver(items => { if (items.some(i => i.isIntersecting)) renderMore(); }, {root: body, rootMargin: '900px 0px'}) : null;
      renderCards();
      if (more) more.observe(rootEl.querySelector('.rv-sentinel'));
      stats.fullRenders++;
      tools();
    }
    /* Version change: patch statuses and the header; rebuild the cards only if the visible list changed. */
    function patch() {
      const ids = R.listItems(s.queue, s.filter, s.sticky).map(x => x.id), shown = s.list.map(x => x.id);
      const progress = rootEl.querySelector('.rv-progress-box');
      if (progress) progress.innerHTML = Cards.progressHtml(ctx());
      const chips = rootEl.querySelector('.rv-chips');
      if (chips) chips.outerHTML = Cards.chips(ctx());
      if (ids.length !== shown.length || ids.some((id, i) => id !== shown[i])) { renderCards(); return; }
      s.list = R.listItems(s.queue, s.filter, s.sticky);
      stats.patches += Cards.patchCards(rootEl.querySelector('.rv-cards'), ctx()) ? 1 : 0;
      tools();
    }
    /* force: a write result or a resync always applies (the local, optimistic queue may differ from s.version). */
    function apply(queue, force) {
      const identity = R.queueIdentity(queue), version = R.queueVersion(queue);
      if (!force && s.queue && identity === s.identity && version === s.version) return;
      const rebuild = !s.queue || identity !== s.identity;
      s.queue = queue; s.map = R.itemMap(queue); s.version = version;
      if (rebuild) {
        if (!s.identity) s.filter = R.initialFilter(queue);
        else { player.release(); player.reset(); s.evidence.clear(); s.evidenceLoading.clear(); } // a new scan revision
        s.identity = identity; s.sticky.clear(); s.focusId = null; s.zoomId = null; s.undo.length = 0;
        renderAll();
      } else {
        // As the classic applyQueueUpdate: a card of "Chưa duyệt" decided meanwhile stays in view (sticky).
        if (s.filter === 'pending') for (const x of s.list) { const y = s.map.get(x.id); if (y && y.decision) s.sticky.add(x.id); }
        patch();
      }
    }
    async function load(first) {
      const current = s, w = writeState(s.id);
      try {
        // Reopened while writes of this job are still running: show the queue once they settled.
        if (first && current.api.pendingWrites && current.api.pendingWrites()) { message('Đang chờ lưu xong các lựa chọn trước…'); await current.api.idle(); if (current !== s) return; }
        const epoch = w.epoch;
        const queue = await current.api.queue();
        if (current !== s) return;
        // Fetched before a local change, or a write is pending: never apply (the clicked card must not flash back).
        if (!first && (epoch !== w.epoch || pending())) return;
        if (first || R.queueIdentity(queue) !== s.identity) {
          const [exp] = await Promise.all([current.api.exportState().catch(() => null), first ? refreshKey(true) : null]);
          if (current !== s) return;
          s.exp = exp; s.lock = R.lockState(s.job, exp);
        }
        s.failed = false;
        apply(queue);
        setOffline(false);
      } catch (error) {
        if (current !== s) return;
        if (error.status === 404) { player.release(); s.queue = null; s.identity = ''; message(R.TEXT.rescanning); return; }
        if (!s.queue) message(error.message || 'Không tải được danh sách cảnh.');
        else if (!error.status) setOffline(true);
        s.failed = true;
      }
    }
    function poll() {
      if (!s || document.visibilityState === 'hidden' || s.loading || s.confirming || pending()) return;
      stats.polls++; s.loading = true;
      load(false).finally(() => { if (s) s.loading = false; });
    }

    /* Media key (GET review/session): one request at a time for the whole dialog; true when it changed. */
    function refreshKey(first) {
      if (!s) return Promise.resolve(false);
      if (s.keyRequest) return s.keyRequest;
      const current = s, before = s.mediaKey;
      if (!first) stats.sessionRefreshes++;
      current.keyRequest = current.api.session().then(() => {
        if (current !== s) return false;
        s.mediaKey = s.api.mediaKey() || null;
        if (s.mediaKey !== before && !first) player.keyChanged();
        return s.mediaKey !== before;
      }, () => false).finally(() => { current.keyRequest = null; });
      return current.keyRequest;
    }
    /* Evidence: only for the selected card that can play (and the next undecided one, after 600 ms). */
    function wantsEvidence(x) { return x && R.hasPlayer(x) && s.mediaKey && !s.lock.cleaned && !s.lock.archived && !s.evidence.has(x.id) && !s.evidenceLoading.has(x.id); }
    function loadEvidence(x) {
      if (!wantsEvidence(x)) return;
      const current = s, id = x.id;
      stats.evidenceFetches++;
      const pending = s.api.evidence(id).catch(() => null).then(ev => {
        if (current !== s) return;
        s.evidenceLoading.delete(id); s.evidence.set(id, ev);
        if (ev && ev.video && ev.video.available === false) player.unavailable(ev.video.reason);
        updateCardMedia(id);
      });
      s.evidenceLoading.set(id, pending);
      updateCardMedia(id); // ghost frames while it loads
    }
    function focusEvidence() {
      clearTimeout(prefetchTimer);
      const x = s.map.get(s.focusId);
      loadEvidence(x);
      prefetchTimer = setTimeout(() => {
        if (!s || !s.focusId) return;
        const next = R.nextUndecided(s.list, s.focusId, s.map);
        if (next) loadEvidence(s.map.get(next));
      }, PREFETCH_MS);
    }
    function updateCardMedia(id) {
      const el = cardEl(id), x = s.map.get(id);
      if (!el || !x) return;
      const url = Cards.refreshMedia(el, ctx(), x), img = el.querySelector('.rv-art img');
      if (img && url && img.dataset.src !== url) img.dataset.src = url;
      watchCard(el, el.classList.contains('on') ? 10 : 0);
      if (el.classList.contains('zoom')) drawCrop(el);
      syncPlay(el, id);
    }
    /* The ▶ button of a rebuilt bar shows what the shared video is doing for this card. */
    function syncPlay(el, id) {
      const playing = player.current() === id && player.playing(), play = el.querySelector('[data-review="play"]');
      el.classList.toggle('playing', playing);
      if (play) play.textContent = playing ? '❚❚ Dừng' : '▶ Phát đoạn này';
    }

    /* Player events → the card that plays: time label and playhead, moment chips, notes, playing state. */
    function playerEvent(type, data) {
      if (!s) return;
      const el = cardEl(data.id), x = s.map.get(data.id);
      if (!el || !x) return;
      if (type === 'time') {
        const ms = R.isScene(x) ? R.momentsOf(x) : null, k = ms ? R.momentIndex(data.t, ms) : -1;
        el.querySelector('.rv-time').textContent = ms ? (k >= 0 ? `${R.mmss(data.t)} · khoảnh khắc ${k + 1}/${ms.length} (${R.mmss(ms[k].start)}–${R.mmss(ms[k].end)})` : `${R.mmss(data.t)} · giữa hai khoảnh khắc`)
          : `${R.mmss(data.t)} / đoạn ${R.mmss(x.start_seconds)}–${R.mmss(x.end_seconds)}`;
        const head = el.querySelector('.rv-timeline .head');
        if (head) { head.style.left = R.tlPos(data.t, Number(x.start_seconds), Number(x.end_seconds)).toFixed(2) + '%'; head.hidden = !data.reveal; }
      } else if (type === 'moment') {
        el.querySelectorAll('.rv-mchip,.rv-timeline .mo').forEach(b => b.classList.toggle('on', Number(b.dataset.i) === data.i));
      } else if (type === 'note') {
        const note = el.querySelector('.rv-note'); note.textContent = data.text; note.hidden = false;
      } else if (type === 'state') syncPlay(el, data.id);
      else if (type === 'unavailable') updateCardMedia(data.id);
    }
    function hideNote(el) { const note = el && el.querySelector('.rv-note'); if (note) note.hidden = true; }
    function attachPlayer(x) { const el = cardEl(x.id); select(x.id); hideNote(el); player.attach(el.querySelector('.rv-art'), x); return el; }

    /* ⤢: the card spans the dialog, with the 360 px crop of its red region or the AI boxes; Esc unzooms first. */
    function zoom(id) {
      const was = s.zoomId;
      if (was) { const old = cardEl(was); if (old) { old.classList.remove('zoom'); old.querySelector('.rv-zoom-btn').setAttribute('aria-pressed', 'false'); old.querySelector('.rv-zoom-slot').innerHTML = ''; } }
      s.zoomId = was === id ? null : id;
      if (!s.zoomId) return;
      const el = cardEl(id), x = s.map.get(id);
      select(id);
      el.classList.add('zoom'); el.querySelector('.rv-zoom-btn').setAttribute('aria-pressed', 'true');
      el.querySelector('.rv-zoom-slot').innerHTML = Cards.zoomExtra(ctx(), x);
      drawCrop(el);
      el.scrollIntoView({block: 'nearest'});
    }
    function drawCrop(el) {
      const canvas = el && el.querySelector('canvas.rv-crop'), x = el && s.map.get(el.dataset.item);
      if (!canvas || !x) return;
      const owner = R.regionOwner(s.queue, x), region = owner && (owner.suggested_region_source_pixels || owner.decision_region_source_pixels), img = el.querySelector('.rv-art img');
      if (!region || region === 'FULL_FRAME' || !img) return;
      const draw = () => {
        if (!canvas.isConnected || !img.naturalWidth) return;
        const c = D.cropRect(region, owner.source_frame_size, img.naturalWidth, img.naturalHeight), g = canvas.getContext('2d');
        canvas.width = c.width; canvas.height = c.height;
        g.drawImage(img, c.x, c.y, c.w, c.h, 0, 0, c.width, c.height);
        g.strokeStyle = '#ff304f'; g.lineWidth = 5; g.strokeRect(c.red.x, c.red.y, c.red.w, c.red.h);
        canvas.dataset.drawn = '1';
      };
      if (img.complete && img.naturalWidth) draw(); else img.addEventListener('load', draw, {once: true});
    }

    /* R2. S5: the classic confirm texts in a V2 dialog above the review dialog. Enter confirms; Esc only
     * cancels it (the review dialog stays open, a zoomed card stays zoomed); shortcuts and polling wait. */
    const confirmBox = document.createElement('dialog'), toastBox = document.createElement('div');
    confirmBox.className = 'rv-confirm'; confirmBox.setAttribute('aria-labelledby', 'rv-confirm-title'); confirmBox.setAttribute('aria-describedby', 'rv-confirm-text');
    confirmBox.innerHTML = '<div class="modal-head"><h2 id="rv-confirm-title">Xác nhận</h2></div><div class="modal-body"><p id="rv-confirm-text"></p></div>' +
      '<div class="modal-foot"><button class="secondary" type="button" data-confirm="no">Hủy</button><button class="primary" type="button" data-confirm="yes">Xác nhận</button></div>';
    toastBox.className = 'rv-toast'; toastBox.setAttribute('role', 'status'); toastBox.setAttribute('aria-live', 'polite'); toastBox.hidden = true;
    dialog.append(confirmBox, toastBox);
    let answer = null, toastTimer = null, before = null;
    function ask(message) {
      return new Promise(resolve => {
        if (answer) answer(false);
        answer = resolve;
        if (!confirmBox.open) before = document.activeElement;
        if (s) s.confirming = true;
        confirmBox.querySelector('#rv-confirm-text').textContent = message;
        if (!confirmBox.open) confirmBox.showModal();
        confirmBox.querySelector('[data-confirm="yes"]').focus();
      });
    }
    function answerConfirm(value) {
      const resolve = answer;
      answer = null;
      if (s) s.confirming = false;
      if (confirmBox.open) confirmBox.close();
      // Focus leaves the closed confirm at once (the next key must reach the shortcuts): back where it was, else nowhere.
      if (confirmBox.contains(document.activeElement)) {
        if (before && before.isConnected && dialog.contains(before) && !confirmBox.contains(before)) before.focus({preventScroll: true}); else document.activeElement.blur();
      }
      before = null;
      if (resolve) resolve(value);
    }
    confirmBox.addEventListener('click', event => { const b = event.target.closest('[data-confirm]'); if (b) answerConfirm(b.dataset.confirm === 'yes'); });
    confirmBox.addEventListener('cancel', event => { event.preventDefault(); answerConfirm(false); });
    confirmBox.addEventListener('close', () => { if (!confirmBox.open && answer) answerConfirm(false); }); // closed by the browser itself
    confirmBox.addEventListener('keydown', event => {
      if (!confirmBox.open) return;
      event.stopPropagation(); // never a review shortcut or a dashboard key while it is open
      if (event.key === 'Enter' && !event.target.closest('[data-confirm="no"]')) { event.preventDefault(); answerConfirm(true); }
    });
    /* Messages: inside the dialog while it is open, else the dashboard toast (a write that fails after closing). */
    function notify(text, error) {
      if (!dialog.open) { if (deps.toast) deps.toast(text, error); return; }
      clearTimeout(toastTimer);
      toastBox.textContent = text; toastBox.classList.toggle('error', !!error); toastBox.hidden = false;
      toastTimer = setTimeout(() => { toastBox.hidden = true; }, error ? 12000 : 5500);
    }
    toastBox.addEventListener('click', () => { toastBox.hidden = true; });

    const pending = () => !!(s && s.api && s.api.pendingWrites && s.api.pendingWrites());
    /* "Đang lưu…" while a write of this job runs, "Đã lưu" for 1.5 s after the last one succeeded. */
    function saveState() {
      const el = rootEl.querySelector('.rv-save');
      if (!s || !el) return;
      clearTimeout(s.saveTimer);
      if (pending()) { el.textContent = 'Đang lưu…'; return; }
      const w = writeState(s.id);
      el.textContent = s.wrote && !w.failed ? 'Đã lưu' : '';
      if (el.textContent) s.saveTimer = setTimeout(() => { if (el.isConnected) el.textContent = ''; }, 1500);
    }
    function tools() {
      if (!s) return;
      const button = rootEl.querySelector('.rv-undo');
      if (button) {
        const last = s.undo[s.undo.length - 1], x = last && s.map.get(last.id);
        button.disabled = !s.undo.length || s.lock.readonly || s.offline;
        button.title = R.undoTitle(last, x);
      }
      saveState();
    }
    function setOffline(value) {
      if (!s || s.offline === value) return;
      s.offline = value;
      const note = rootEl.querySelector('.rv-offline');
      if (note) note.hidden = !value;
      if (s.queue && rootEl.querySelector('.rv-cards')) patch();
    }
    function refuse() {
      if (!s || !s.queue) return true;
      if (s.lock.readonly) { notify(s.lock.reason, true); return true; }
      if (s.offline) { notify(R.TEXT.offline, true); return true; }
      return false;
    }
    /* The selected card moves (auto-advance, ←/→, undo, a failed write): render up to it, select, scroll. */
    function focusCard(id, pause) {
      const i = s.list.findIndex(x => x.id === id);
      if (i < 0) return;
      for (let guard = 0; i >= s.rendered && guard < 1000; guard++) renderMore();
      if (pause && player.current() && player.current() !== id) player.pause();
      select(id);
      const el = cardEl(id);
      if (el) el.scrollIntoView({block: 'nearest'});
    }
    function afterLocalChange(id, advance) {
      if (s.filter === 'pending' && s.list.some(x => x.id === id)) s.sticky.add(id);
      patch();
      if (!advance) return;
      const next = R.nextUndecided(s.list, id, s.map);
      if (next && next !== s.focusId) focusCard(next, true);
    }
    /* One write into the job's chain. The returned queue applies only for the last pending write; the last
     * failure reloads the queue and reopens its card (classic enqueueWrite / resync). */
    function enqueue(kind, body) {
      const current = s, id = s.id, api = s.api, w = writeState(id), item = s.map.get(body.id);
      w.epoch++; s.wrote = true;
      const done = api.write(kind, body);
      saveState();
      done.then(result => {
        if (s === current && result && result.last && result.body && Array.isArray(result.body.items)) apply(result.body, true);
      }, error => {
        w.resync = true; w.failed = true;
        if (body.id) w.reopen = body.id;
        const known = (s === current && s.map.get(body.id)) || item;
        notify((s && s.id === id ? '' : '#' + id + ' · ') + R.writeFailureMessage(kind, body, error, known), true);
        if (s === current && !error.status) setOffline(true);
      }).finally(() => {
        if (api.pendingWrites()) { if (s === current) saveState(); return; }
        if (s === current) saveState();
        w.failed = false;
        if (!w.resync) return;
        const reopen = w.reopen;
        w.resync = false; w.reopen = null;
        if (s && s.id === id) resync(reopen);
      });
    }
    async function resync(reopen) {
      const current = s;
      try {
        const queue = await current.api.queue();
        if (current !== s) return;
        apply(queue, true); setOffline(false);
      } catch (error) {
        if (current !== s) return;
        notify(error.message, true);
        if (!error.status) setOffline(true);
      }
      if (reopen && s.map.has(reopen)) { if (!s.list.some(x => x.id === reopen)) setFilter('all'); focusCard(reopen); }
    }
    /* decide / clear / undo: the classic steps (review-core.js; verify-review.cjs compares them). */
    async function decide(id, decision, fullFrame, note, studio, platform) {
      if (refuse()) return;
      const current = s;
      let item = s.map.get(id);
      if (!item) { notify(R.TEXT.missingItem, true); return; }
      for (const text of R.decisionConfirms(item, decision, fullFrame)) {
        if (!await ask(text)) return;
        if (s !== current || refuse()) return;
        // An earlier write may have applied the server queue meanwhile: decide on the current item.
        item = s.map.get(id);
        if (!item) { notify(R.TEXT.missingItem, true); return; }
      }
      const plan = R.decisionBody(item, decision, {fullFrame, note, studio, platform});
      if (plan.error) { notify(plan.error, true); return; }
      s.undo.push(R.undoEntry(item, !item.decision && R.isAdvisoryItem(s.queue, item)));
      if (s.undo.length > 100) s.undo.shift();
      R.applyDecision(s.queue, item, decision, plan.region, plan.note, plan.studio, plan.platform);
      afterLocalChange(id, autoNext && id === s.focusId);
      enqueue('decision', plan.body);
    }
    function clear(id) {
      if (refuse()) return;
      const item = s.map.get(id);
      if (!item || !item.decision) return;
      s.undo.push(R.undoEntry(item, false));
      if (s.undo.length > 100) s.undo.shift();
      R.applyClear(s.queue, item);
      afterLocalChange(id, false);
      enqueue('clear', {id});
    }
    function undo() {
      if (refuse()) return;
      const entry = s.undo.pop();
      if (!entry) { tools(); return; }
      const item = s.map.get(entry.id), inList = item && s.list.some(x => x.id === item.id);
      if (!item) { notify(R.TEXT.undoMissing, true); tools(); return; }
      if (entry.advisory) { if (item.id !== s.focusId && inList) focusCard(item.id); tools(); notify(R.advisoryUndoMessage(item)); return; }
      const plan = R.undoPlan(entry), prev = entry.prev;
      if (plan.kind === 'decision') R.applyDecision(s.queue, item, prev.decision, prev.region, prev.note, prev.studio, prev.platform);
      else R.applyClear(s.queue, item);
      enqueue(plan.kind, plan.body);
      if (s.filter === 'pending' && inList) s.sticky.add(item.id);
      patch();
      if (item.id !== s.focusId && inList) focusCard(item.id);
    }
    function togglePlay() {
      const x = s.map.get(s.focusId);
      if (!x || !R.hasPlayer(x)) return;
      if (player.current() === x.id && player.playing()) { player.pause(); return; }
      attachPlayer(x); player.toggle();
    }
    /* P9, as the classic onKeyDown: 1–4 decide the selected card, ←/→ select (stop at the ends), Space plays,
     * Z undoes. Off while typing, with Ctrl/Alt/Meta, while the confirm is open; held keys repeat only ←/→. */
    function onKey(event) {
      if (!s || !dialog.open || confirmBox.open || !s.queue) return;
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || isTyping(event.target)) return;
      const k = event.key;
      if (event.repeat && k !== 'ArrowLeft' && k !== 'ArrowRight') return;
      if (k >= '1' && k <= '4' && k.length === 1) {
        event.preventDefault();
        const x = s.map.get(s.focusId), choice = x && R.keyDecision(Number(k), x);
        if (choice) decide(x.id, choice.decision, choice.fullFrame);
      } else if (k === 'ArrowLeft' || k === 'ArrowRight') {
        event.preventDefault();
        const id = R.step(s.list, s.focusId, k === 'ArrowLeft' ? -1 : 1);
        if (id && id !== s.focusId) focusCard(id, true);
      } else if (k === ' ' || k === 'Spacebar') {
        if (spaceActivates(event.target)) return;
        event.preventDefault(); togglePlay();
      } else if (k === 'z' || k === 'Z') { event.preventDefault(); undo(); }
    }

    function open(id, view) {
      if (s && s.id === id && s.view === view && dialog.open) return;
      if (s) close();
      const job = deps.getJob(id);
      s = {id, view, job: job || {id, name: ''}, api: null, queue: null, map: new Map(), identity: '', version: '', list: [], rendered: 0, mediaKey: null,
        filter: 'pending', sticky: new Set(), focusId: null, zoomId: null, exp: null, lock: R.lockState(job, null), timer: null, loading: false,
        evidence: new Map(), evidenceLoading: new Map(), techOpen: new Set(), undo: [], confirming: false, offline: false, wrote: false, saveTimer: null};
      stats.opens++;
      player.reset();
      if (!dialog.open) dialog.showModal();
      deps.store.pause();
      message('Đang tải danh sách cảnh…');
      try { s.api = deps.store.review(id); } catch (error) { message(error.message); return; }
      load(true);
      s.timer = setInterval(poll, POLL_MS);
    }
    function close() {
      if (!s) return;
      answerConfirm(false);
      clearInterval(s.timer); clearTimeout(prefetchTimer); clearTimeout(s.saveTimer); s = null;
      clearTimeout(toastTimer); toastBox.hidden = true;
      player.release();
      if (more) more.disconnect(); more = null;
      loader.reset();
      rootEl.innerHTML = '';
      if (dialog.open) dialog.close();
      deps.store.resume();
    }
    /* A newer /api/status (the first one after a reload, or after an action): name and locks. */
    function updateJob() {
      if (!s) return;
      const job = deps.getJob(s.id);
      if (!job || job === s.job) return;
      const before = s.lock.reason, name = s.job.name;
      s.job = job; s.lock = R.lockState(job, s.exp);
      if (s.queue && (before !== s.lock.reason || name !== job.name)) { player.release(); renderAll(); }
    }

    rootEl.addEventListener('click', event => {
      const el = event.target.closest('[data-review]');
      if (!el || !s) return;
      const action = el.dataset.review, card = el.closest('article.rv-card'), x = card && s.map.get(card.dataset.item);
      if (['decide', 'clear', 'region', 'studio', 'platform', 'undo'].includes(action) && event.detail) el.blur(); // Space must not click it again
      if (action === 'close') deps.requestClose(s.view);
      else if (action === 'filter' && s.queue && el.dataset.filter !== s.filter) setFilter(el.dataset.filter);
      else if (action === 'undo') undo();
      else if (!x) return;
      else if (action === 'decide') { select(x.id); decide(x.id, el.dataset.decision, el.dataset.decision === 'BLUR' && R.needsFullFrame(x)); }
      else if (action === 'clear') { select(x.id); clear(x.id); }
      else if (action === 'region') decide(el.dataset.owner, el.dataset.decision, false, R.regionNote(el.dataset.decision));
      else if (action === 'studio') { select(x.id); if (!R.studioRemembered(x)) decide(x.id, 'KEEP', false, null, true); }
      else if (action === 'platform') { select(x.id); if (!R.platformRemembered(x)) decide(x.id, 'BLUR', false, null, false, true); }
      else if (action === 'select') { select(x.id); if (event.target.tagName === 'VIDEO') player.toggle(); }
      else if (action === 'play') { attachPlayer(x); player.toggle(); }
      else if (action === 'seq') { attachPlayer(x); player.playSequence(); }
      else if (action === 'moment') { attachPlayer(x); player.playMoment(Number(el.dataset.i), false); }
      else if (action === 'zoom') zoom(x.id);
      else if (action === 'seek') {
        const box = el.getBoundingClientRect();
        if (!box.width) return;
        const target = R.seekTarget(x, (event.clientX - box.left) / box.width);
        if (!player.allowed() || !media(x).playable) { select(x.id); return; }
        attachPlayer(x); player.seek(target.t, target.moment);
      } else if (action === 'thumb') {
        card.querySelectorAll('.rv-thumb.on').forEach(b => b.classList.remove('on'));
        el.classList.add('on'); select(x.id);
        const t = el.dataset.t === '' ? null : Number(el.dataset.t), img = el.querySelector('img');
        if (t != null && Number.isFinite(t) && media(x).playable) { attachPlayer(x); player.seek(t, R.isScene(x) ? R.momentIndex(t, R.momentsOf(x)) : -1); }
        else if (img && img.getAttribute('src')) { const main = card.querySelector('.rv-art img'); if (main) { main.src = img.getAttribute('src'); main.closest('.rv-art').classList.add('loaded'); } }
      }
    });
    rootEl.addEventListener('change', event => {
      const el = event.target;
      if (el.dataset.review === 'more' && s && s.queue && el.value) setFilter(el.value);
      if (el.dataset.review === 'auto') { autoNext = el.checked; try { localStorage.setItem(AUTO_KEY, autoNext ? '1' : '0'); } catch (_) { /* private window */ } }
    });
    rootEl.addEventListener('toggle', event => {
      const details = event.target, card = details.closest && details.closest('article.rv-card');
      if (!s || !card || !details.classList.contains('rv-tech')) return;
      if (details.open) s.techOpen.add(card.dataset.item); else s.techOpen.delete(card.dataset.item);
    }, true);
    dialog.addEventListener('cancel', event => {
      event.preventDefault();
      if (!s) return;
      if (s.zoomId) { zoom(s.zoomId); return; } // Esc: unzoom the card first, close on the next Esc
      deps.requestClose(s.view);
    });
    document.addEventListener('keydown', onKey);
    document.addEventListener('visibilitychange', () => { if (document.hidden) player.pause(); });
    window.addEventListener('pagehide', () => player.release());
    function setFilter(filter) {
      if (!R.FILTER_IDS.includes(filter)) return;
      s.filter = filter; s.sticky.clear();
      const chips = rootEl.querySelector('.rv-chips');
      if (chips) chips.outerHTML = Cards.chips(ctx());
      renderCards();
    }
    function select(id) {
      if (!s.map.has(id)) return;
      const changed = s.focusId !== id;
      s.focusId = id;
      for (const el of rootEl.querySelectorAll('article.rv-card')) el.classList.toggle('on', el.dataset.item === id);
      const el = cardEl(id);
      if (el) loader.prioritize(el.querySelector('.rv-art'));
      if (changed) focusEvidence();
    }

    return {open, close, updateJob, isOpen: () => !!s,
      current: () => s && {id: s.id, view: s.view, filter: s.filter, focusId: s.focusId, zoomId: s.zoomId, undo: s.undo.length, offline: s.offline, pending: pending(), autoNext}};
  }

  root.BFReview = {create, parseHash};
})(window);
