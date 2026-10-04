/* Review dialog controller (R0/R1, read-only). Opens <dialog id="review-dialog"> for #review/<id>/<view>,
 * loads the queue through store.review(id) (adapter in live.html, demo store in index.html), renders
 * cards in batches, polls the queue every 3 s while the tab is visible (identity change → rebuild,
 * version change → patch in place, the playing <video> is never rebuilt) and pauses the dashboard's
 * /api/status polling while open (Q7). R1: the selected card loads its evidence (strip of ≤8 frames,
 * timeline ticks, "Rõ nhất"); ▶ plays its range in the one shared <video>; ⤢ zooms a card (Esc unzooms
 * before it closes). It never sends a write: decisions, bulk and export come in R2/R3.
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

  /* deps: {dialog, store, getJob(id), oldUrl(id, view), requestClose(view)} */
  function create(deps) {
    const dialog = deps.dialog, rootEl = dialog.querySelector('#review-root');
    const stats = root.BFReviewStats = {opens: 0, fullRenders: 0, cardRenders: 0, patches: 0, polls: 0, cardsRendered: 0, maxImages: 0, evidenceFetches: 0, sessionRefreshes: 0};
    const loader = Media.createImageLoader({max: 2, stats});
    const player = Media.createPlayer({stats, key: () => s && s.mediaKey, refreshKey: () => refreshKey(), videoUrl: k => s.api.videoUrl(k),
      probe: k => s.api.probeVideo(k), ui: playerEvent});
    let s = null, more = null, prefetchTimer = null;

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
        oldUrl: deps.oldUrl(s.id, s.view), media};
    }
    function shell(headHtml, bodyHtml) {
      rootEl.innerHTML = '<div class="rv-head">' + headHtml + '</div><div class="rv-body">' + bodyHtml + '</div>' +
        '<div class="rv-foot"><label class="rv-auto"><input type="checkbox" checked disabled> Tự chuyển cảnh</label><button class="secondary" type="button" data-review="close">Đóng</button></div>';
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
    }
    function apply(queue) {
      const identity = R.queueIdentity(queue), version = R.queueVersion(queue);
      if (s.queue && identity === s.identity && version === s.version) return;
      const rebuild = !s.queue || identity !== s.identity;
      s.queue = queue; s.map = R.itemMap(queue); s.version = version;
      if (rebuild) {
        if (!s.identity) s.filter = R.initialFilter(queue);
        else { player.release(); player.reset(); s.evidence.clear(); s.evidenceLoading.clear(); } // a new scan revision
        s.identity = identity; s.sticky.clear(); s.focusId = null; s.zoomId = null;
        renderAll();
      } else patch();
    }
    async function load(first) {
      const current = s;
      try {
        const queue = await current.api.queue();
        if (current !== s) return;
        if (first || R.queueIdentity(queue) !== s.identity) {
          const [exp] = await Promise.all([current.api.exportState().catch(() => null), first ? refreshKey(true) : null]);
          if (current !== s) return;
          s.exp = exp; s.lock = R.lockState(s.job, exp);
        }
        s.failed = false;
        apply(queue);
      } catch (error) {
        if (current !== s) return;
        if (error.status === 404) { player.release(); s.queue = null; s.identity = ''; message(R.TEXT.rescanning); return; }
        if (!s.queue) message(error.message || 'Không tải được danh sách cảnh.');
        s.failed = true;
      }
    }
    function poll() {
      if (!s || document.visibilityState === 'hidden' || s.loading) return;
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

    function open(id, view) {
      if (s && s.id === id && s.view === view && dialog.open) return;
      if (s) close();
      const job = deps.getJob(id);
      s = {id, view, job: job || {id, name: ''}, api: null, queue: null, map: new Map(), identity: '', version: '', list: [], rendered: 0, mediaKey: null,
        filter: 'pending', sticky: new Set(), focusId: null, zoomId: null, exp: null, lock: R.lockState(job, null), timer: null, loading: false,
        evidence: new Map(), evidenceLoading: new Map(), techOpen: new Set()};
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
      clearInterval(s.timer); clearTimeout(prefetchTimer); s = null;
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
      if (action === 'close') deps.requestClose(s.view);
      else if (action === 'filter' && s.queue && el.dataset.filter !== s.filter) setFilter(el.dataset.filter);
      else if (!x) return;
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

    return {open, close, updateJob, isOpen: () => !!s, current: () => s && {id: s.id, view: s.view, filter: s.filter, focusId: s.focusId, zoomId: s.zoomId}};
  }

  root.BFReview = {create, parseHash};
})(window);
