/* Review dialog controller (R0, read-only). Opens <dialog id="review-dialog"> for #review/<id>/<view>,
 * loads the queue through store.review(id) (adapter in live.html, demo store in index.html), renders
 * cards in batches, polls the queue every 3 s while the tab is visible (identity change → rebuild,
 * version change → patch in place) and pauses the dashboard's /api/status polling while open (Q7).
 * It never sends a write: decisions, bulk and export come in R2/R3.
 */
(function (root) {
  'use strict';
  const R = root.BFReviewCore, Cards = root.BFReviewCards, Media = root.BFReviewMedia;
  const HASH = /^review\/(\d{1,9})(?:\/(overview|downloads|videos|queue|logos|settings))?$/;
  const POLL_MS = 3000;

  function parseHash(hash) {
    const m = HASH.exec(String(hash || ''));
    return m ? {id: Number(m[1]), view: m[2] || 'overview'} : null;
  }

  /* deps: {dialog, store, getJob(id), oldUrl(id, view), requestClose(view)} */
  function create(deps) {
    const dialog = deps.dialog, rootEl = dialog.querySelector('#review-root');
    const stats = root.BFReviewStats = {opens: 0, fullRenders: 0, cardRenders: 0, patches: 0, polls: 0, cardsRendered: 0, maxImages: 0};
    const loader = Media.createImageLoader({max: 2, stats});
    let s = null, more = null;

    function ctx() {
      return {job: s.job, queue: s.queue, map: s.map, filter: s.filter, list: s.list, focusId: s.focusId, lock: s.lock,
        oldUrl: deps.oldUrl(s.id, s.view), mediaUrl: p => s.api.mediaUrl(p)};
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

    /* Cards: the first batch (enough to include the focus), then +24 when the end comes near. */
    function renderCards() {
      const box = rootEl.querySelector('.rv-cards');
      if (!box) return;
      s.list = R.listItems(s.queue, s.filter, s.sticky);
      if (!s.list.some(x => x.id === s.focusId)) s.focusId = R.pickFocus(s.list);
      const at = s.list.findIndex(x => x.id === s.focusId);
      s.rendered = Math.min(s.list.length, Math.max(Cards.BATCH, Math.ceil((at + 1) / Cards.BATCH) * Cards.BATCH));
      const c = ctx();
      box.innerHTML = s.list.length ? s.list.slice(0, s.rendered).map(x => Cards.card(c, x)).join('') : Cards.empty(c);
      loader.forget(box);
      watchImages(box.querySelectorAll('article.rv-card'));
      stats.cardRenders++; stats.cardsRendered = s.rendered;
      rootEl.querySelector('.rv-body').scrollTop = 0;
      const focus = box.querySelector('article.rv-card.on');
      if (focus && at >= Cards.BATCH) focus.scrollIntoView({block: 'nearest'});
    }
    function renderMore() {
      if (!s || s.rendered >= s.list.length) return;
      const box = rootEl.querySelector('.rv-cards'), c = ctx(), start = s.rendered;
      s.rendered = Math.min(s.list.length, start + Cards.BATCH);
      box.insertAdjacentHTML('beforeend', s.list.slice(start, s.rendered).map(x => Cards.card(c, x)).join(''));
      watchImages([...box.querySelectorAll('article.rv-card')].slice(start));
      stats.cardsRendered = s.rendered;
    }
    function watchImages(cards) {
      for (const el of cards) {
        const art = el.querySelector('.rv-art'), img = art && art.querySelector('img');
        loader.watch(art, img ? img.dataset.src : '', el.classList.contains('on') ? 10 : 0);
      }
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
        s.identity = identity; s.sticky.clear(); s.focusId = null;
        renderAll();
      } else patch();
    }
    async function load(first) {
      const current = s;
      try {
        const queue = await current.api.queue();
        if (current !== s) return;
        if (first || R.queueIdentity(queue) !== s.identity) {
          const exp = await current.api.exportState().catch(() => null);
          if (current !== s) return;
          s.exp = exp; s.lock = R.lockState(s.job, exp);
        }
        s.failed = false;
        apply(queue);
      } catch (error) {
        if (current !== s) return;
        if (error.status === 404) { s.queue = null; s.identity = ''; message(R.TEXT.rescanning); return; }
        if (!s.queue) message(error.message || 'Không tải được danh sách cảnh.');
        s.failed = true;
      }
    }
    function poll() {
      if (!s || document.visibilityState === 'hidden' || s.loading) return;
      stats.polls++; s.loading = true;
      load(false).finally(() => { if (s) s.loading = false; });
    }

    function open(id, view) {
      if (s && s.id === id && s.view === view && dialog.open) return;
      if (s) close();
      const job = deps.getJob(id);
      s = {id, view, job: job || {id, name: ''}, api: null, queue: null, map: new Map(), identity: '', version: '', list: [], rendered: 0,
        filter: 'pending', sticky: new Set(), focusId: null, exp: null, lock: R.lockState(job, null), timer: null, loading: false};
      stats.opens++;
      if (!dialog.open) dialog.showModal();
      deps.store.pause();
      message('Đang tải danh sách cảnh…');
      try { s.api = deps.store.review(id); } catch (error) { message(error.message); return; }
      load(true);
      s.timer = setInterval(poll, POLL_MS);
    }
    function close() {
      if (!s) return;
      clearInterval(s.timer); s = null;
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
      if (s.queue && (before !== s.lock.reason || name !== job.name)) renderAll();
    }

    rootEl.addEventListener('click', event => {
      const el = event.target.closest('[data-review]');
      if (!el || !s) return;
      const action = el.dataset.review;
      if (action === 'close') deps.requestClose(s.view);
      else if (action === 'filter' && s.queue && el.dataset.filter !== s.filter) setFilter(el.dataset.filter);
      else if (action === 'select' && s.queue) select(el.dataset.item);
    });
    rootEl.addEventListener('change', event => {
      const el = event.target;
      if (el.dataset.review === 'more' && s && s.queue && el.value) setFilter(el.value);
    });
    dialog.addEventListener('cancel', event => { event.preventDefault(); if (s) deps.requestClose(s.view); });
    function setFilter(filter) {
      if (!R.FILTER_IDS.includes(filter)) return;
      s.filter = filter; s.sticky.clear();
      const chips = rootEl.querySelector('.rv-chips');
      if (chips) chips.outerHTML = Cards.chips(ctx());
      renderCards();
    }
    function select(id) {
      if (!s.map.has(id)) return;
      s.focusId = id;
      for (const el of rootEl.querySelectorAll('article.rv-card')) el.classList.toggle('on', el.dataset.item === id);
      const art = [...rootEl.querySelectorAll('article.rv-card')].find(el => el.dataset.item === id);
      if (art) loader.prioritize(art.querySelector('.rv-art'));
    }

    return {open, close, updateJob, isOpen: () => !!s, current: () => s && {id: s.id, view: s.view, filter: s.filter, focusId: s.focusId}};
  }

  root.BFReview = {create, parseHash};
})(window);
