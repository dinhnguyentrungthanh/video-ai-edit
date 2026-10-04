/* Review dialog media. Images: at most 2 load at once (the phone listener answers HTTP/1.0, one connection
 * per request, with a per-device connection limit), only near the visible part of the dialog, the selected
 * card first; a frame whose media key expired gets a new key (≤2 tries). Video (R1): one shared <video>
 * (preload none, src set on the first play) moved into the card that plays, as the classic player:
 * range and moment playback, seek, error probe with a new key, release on close. Plain same-origin
 * <img src> / <video src> only (the V2 CSP has no blob:).
 */
(function (root) {
  'use strict';
  const R = root.BFReviewCore;

  function createImageLoader(options) {
    options = options || {};
    const max = options.max || 2, stats = options.stats || {};
    let active = 0, order = 0, observer = null;
    const waiting = [], entries = new Map(), running = new Map(); // running: <img> → the load it is doing now
    stats.maxImages = stats.maxImages || 0; stats.activeImages = 0; stats.imagesLoaded = stats.imagesLoaded || 0; stats.imagesFailed = stats.imagesFailed || 0; stats.imageRetries = stats.imageRetries || 0;

    function fail(entry) { entry.state = 'failed'; stats.imagesFailed++; entry.box.classList.add('failed'); entry.box.classList.remove('loading'); }
    function settle(entry, ok) {
      if (entry.cancelled) { pump(); return; }
      if (ok) { entry.state = 'loaded'; stats.imagesLoaded++; entry.box.classList.add('loaded'); entry.box.classList.remove('loading', 'failed'); }
      else if (entry.tries < (entry.refresh ? 2 : 1) && entry.box.isConnected) {
        entry.tries++; stats.imageRetries++; entry.state = 'waiting';
        // A frame (403: the media key changed when the Control Center restarted): a new URL with a new key.
        Promise.resolve(entry.refresh ? entry.refresh(entry.url) : entry.url).then(url => {
          if (entry.cancelled) return;
          if (!url) { fail(entry); return; }
          entry.url = url; waiting.push(entry); pump();
        }, () => fail(entry));
      } else fail(entry);
      pump();
    }
    /* Every started load gives its slot back exactly once: on load, on error, or when the same <img> starts another
     * load (the browser drops the old request without an event), whether its entry was cancelled or not (R1-B1). */
    function start(entry) {
      const img = entry.img, prev = running.get(img);
      if (prev) { prev.entry.cancelled = true; prev.finish(null); }
      active++; stats.activeImages = active; stats.maxImages = Math.max(stats.maxImages, active);
      const attempt = {entry, done: false};
      const onLoad = () => attempt.finish(true), onError = () => attempt.finish(false);
      attempt.finish = ok => {
        if (attempt.done) return;
        attempt.done = true;
        img.removeEventListener('load', onLoad); img.removeEventListener('error', onError);
        if (running.get(img) === attempt) running.delete(img);
        active--; stats.activeImages = active;
        if (ok !== null) settle(entry, ok);
      };
      running.set(img, attempt);
      img.addEventListener('load', onLoad); img.addEventListener('error', onError);
      entry.state = 'loading'; entry.box.classList.add('loading');
      img.src = entry.url;
    }
    function pump() {
      while (active < max && waiting.length) {
        waiting.sort((a, b) => b.priority - a.priority || a.order - b.order);
        const entry = waiting.shift();
        if (entry.cancelled || !entry.box.isConnected) continue;
        start(entry);
      }
    }
    function enqueue(entry) {
      if (entry.queued) return;
      entry.queued = true;
      if (observer) observer.unobserve(entry.box);
      waiting.push(entry);
      pump();
    }
    return {
      /* scroller: the element that scrolls the cards (the dialog body). */
      attach(scroller) {
        if (observer) observer.disconnect();
        observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(items => {
          for (const item of items) if (item.isIntersecting) { const entry = entries.get(item.target); if (entry) enqueue(entry); }
        }, {root: scroller, rootMargin: '600px 0px'}) : null;
      },
      /* box: an image box (card image, strip frame) with an <img>; url loads when the box comes near the view.
       * refresh(failedUrl): a new URL after an error (frames: new media key), or null. Watching a box again with
       * another URL replaces it; the same URL still waiting, loading or shown is kept (no second load). */
      watch(box, url, priority, refresh) {
        const old = entries.get(box), img = box.querySelector('img');
        priority = priority || 0;
        if (old && !old.cancelled && old.img === img && url && old.url === url && old.state !== 'failed') {
          if (refresh) old.refresh = refresh;
          if (priority > old.priority) { old.priority = priority; if (!old.queued) enqueue(old); else pump(); }
          return;
        }
        if (old) { old.cancelled = true; if (observer) observer.unobserve(box); }
        if (!img || !url) { entries.delete(box); box.classList.add('failed'); return; }
        box.classList.remove('failed');
        const entry = {box, img, url, refresh: refresh || null, priority, order: order++, tries: 0, queued: false, cancelled: false, state: 'waiting'};
        entries.set(box, entry);
        if (observer && !priority) observer.observe(box); else enqueue(entry);
      },
      /* The selected card loads before the others. */
      prioritize(box) {
        const entry = entries.get(box);
        if (!entry) return;
        entry.priority = 10;
        if (!entry.queued) enqueue(entry); else pump();
      },
      /* Boxes no longer in the page: waiting images are dropped (an image already loading finishes detached and
       * gives its slot back then). */
      forget() {
        for (const [box, entry] of entries) {
          if (box.isConnected) continue;
          entry.cancelled = true; entries.delete(box); if (observer) observer.unobserve(box);
        }
      },
      reset() { for (const entry of entries.values()) entry.cancelled = true; entries.clear(); waiting.length = 0; if (observer) observer.disconnect(); observer = null; },
      active: () => active,
    };
  }

  /* opts: {stats, key(), refreshKey() → Promise<bool changed>, videoUrl(key), probe(key) → Promise<status>, ui(event, data)}
   * ui events: 'time' {id, t, reveal}, 'moment' {id, i}, 'note' {id, text}, 'state' {id, playing}, 'unavailable' {id, reason}. */
  function createPlayer(opts) {
    const stats = opts.stats || {}, ui = opts.ui || (() => {});
    const video = document.createElement('video');
    video.className = 'rv-video'; video.preload = 'none'; video.playsInline = true; video.setAttribute('playsinline', '');
    stats.plays = 0; stats.videoErrors = 0; stats.videoReleases = 0;
    const st = {id: null, start: 0, end: 0, moments: null, mi: -1, seq: false, stopAt: null, loaded: false, srcKey: null, pending: null,
      seekFor: null, want: null, keyRetries: 0, available: true, reason: null, reveal: false, host: null};

    const allowed = () => !!opts.key() && st.available;
    function cover() { st.reveal = false; if (st.host) st.host.classList.remove('revealed'); }
    function reveal() { st.reveal = true; if (st.host) st.host.classList.add('revealed'); }
    function setMoment(i) { st.mi = i; ui('moment', {id: st.id, i}); }
    function setTime(t) { ui('time', {id: st.id, t, reveal: st.reveal}); }
    function note(text) { ui('note', {id: st.id, text}); }
    function ensure() {
      if (!allowed()) return false;
      if (!st.loaded) { st.loaded = true; st.srcKey = opts.key(); video.preload = 'metadata'; video.src = opts.videoUrl(st.srcKey); }
      return true;
    }
    function seek(t, play) {
      if (!ensure()) return false;
      const id = st.id;
      st.want = {id, t, play: !!play};
      const run = () => {
        if (st.id !== id) return;
        st.seekFor = id;
        try { video.currentTime = t; } catch (_) { /* metadata not ready */ }
        if (play) { stats.plays++; const p = video.play(); if (p && p.catch) p.catch(() => {}); }
      };
      if (video.readyState >= 1) run(); else st.pending = run;
      return true;
    }
    function unavailableNote() { note(R.videoReason(st.reason ? {reason: st.reason} : null, !!opts.key())); }
    function playMoment(i, sequence) {
      const ms = st.moments;
      if (!ms || !ms[i]) return;
      if (!allowed()) { unavailableNote(); return; }
      st.seq = !!sequence; st.stopAt = ms[i].end; setMoment(i); seek(ms[i].start, true);
    }
    /* ▶: resume inside the range or the current moment, else start (a scene plays its moments in order). */
    function playRange() {
      if (!allowed()) { unavailableNote(); return; }
      const ms = st.moments, t = video.currentTime;
      if (ms) {
        const k = st.reveal ? R.momentIndex(t, ms) : -1;
        if (k >= 0 && t < ms[k].end - .2) { st.seq = true; st.stopAt = ms[k].end; setMoment(k); st.want = {id: st.id, t, play: true}; stats.plays++; const p = video.play(); if (p && p.catch) p.catch(() => {}); return; }
        const next = st.reveal ? R.nextMomentAfter(t, ms) : -1;
        playMoment(next > 0 ? next : 0, true); return;
      }
      if (st.reveal && t >= st.start && t < st.end - .2) { st.want = {id: st.id, t, play: true}; stats.plays++; const p = video.play(); if (p && p.catch) p.catch(() => {}); return; }
      seek(st.start, true);
    }
    function momentGuard() {
      const ms = st.moments;
      if (!ms || video.paused || !st.reveal || st.seekFor !== st.id) return;
      const t = video.currentTime, k = R.momentIndex(t, ms);
      if (st.stopAt != null && t >= st.stopAt - .03) {
        const next = st.mi + 1;
        if (st.seq && next < ms.length) { playMoment(next, true); return; }
        video.pause(); st.stopAt = null; st.seq = false; return;
      }
      if (k < 0) { const next = R.nextMomentAfter(t, ms); if (st.seq && next >= 0) { playMoment(next, true); return; } video.pause(); st.stopAt = null; return; }
      if (k !== st.mi) setMoment(k);
    }
    function watchMoments() { if (!st.moments || video.paused) return; momentGuard(); requestAnimationFrame(watchMoments); }
    async function failed(used, want, code) {
      const id = st.id;
      stats.videoErrors++;
      const status = used && used !== opts.key() ? 403 : await opts.probe(used).catch(() => 0);
      if (status === 403 && st.keyRetries < 2) {
        if (used === opts.key()) await opts.refreshKey().catch(() => false);
        if (opts.key() && opts.key() !== used) { st.keyRetries++; if (st.id === id && !st.loaded && want) seek(want.t, want.play); return; }
      }
      const reason = R.probeReason(status, code);
      if (!reason) { if (st.id === id) note('Chưa tải được video lúc này (mất kết nối hoặc phiên Review vừa đổi). Bấm ▶ để thử lại; dải khung hình bên dưới vẫn xem được.'); return; }
      st.available = false; st.reason = reason;
      ui('unavailable', {id, reason});
      note(R.videoReason({reason}, true));
    }

    video.addEventListener('loadedmetadata', () => { st.keyRetries = 0; const run = st.pending; st.pending = null; if (run) run(); });
    video.addEventListener('seeked', () => { if (st.seekFor === st.id) { reveal(); setTime(video.currentTime); } });
    video.addEventListener('playing', () => { if (st.seekFor === st.id) reveal(); ui('state', {id: st.id, playing: true}); if (st.moments) requestAnimationFrame(watchMoments); });
    video.addEventListener('pause', () => ui('state', {id: st.id, playing: false}));
    video.addEventListener('timeupdate', () => {
      if (!st.reveal) return;
      const t = video.currentTime; setTime(t);
      if (st.moments) { momentGuard(); return; }
      if (!video.paused && t >= st.end) video.pause();
    });
    video.addEventListener('error', () => {
      if (!st.loaded) return;
      const used = st.srcKey, w = st.want && st.want.id === st.id ? st.want : null, now = video.currentTime, code = video.error ? video.error.code : 0;
      const want = w ? {t: st.reveal && Number.isFinite(now) && now >= st.start && now <= st.end ? now : w.t, play: w.play || !video.paused} : null;
      st.loaded = false; st.pending = null; cover(); ui('state', {id: st.id, playing: false});
      failed(used, want, code);
    });

    return {
      element: video,
      /* The card whose image box (host) shows the video and whose range plays. */
      attach(host, item) {
        const start = Number(item.start_seconds), end = Number(item.end_seconds);
        if (st.id !== item.id || st.start !== start || st.end !== end) {
          if (!video.paused) video.pause();
          st.id = item.id; st.start = start; st.end = end; st.seekFor = null; st.pending = null;
          st.moments = R.isScene(item) ? R.momentsOf(item) : null; st.mi = -1; st.seq = false; st.stopAt = null; cover();
        }
        if (st.host !== host) { if (st.host) st.host.classList.remove('revealed'); st.host = host; host.appendChild(video); if (st.reveal) host.classList.add('revealed'); }
      },
      /* A rebuilt card: move the same <video> (still playing) into its new image box. */
      rehost(host) { if (!host || host === st.host) return; st.host = host; host.appendChild(video); if (st.reveal) host.classList.add('revealed'); },
      play: playRange,
      playMoment,
      playSequence() { playMoment(0, true); },
      seek(t, moment) {
        if (!allowed()) { unavailableNote(); return false; }
        if (st.moments && moment != null && moment >= 0) { if (!video.paused) video.pause(); st.seq = false; st.stopAt = null; setMoment(moment); }
        else if (!video.paused) video.pause();
        setTime(t); return seek(t, false);
      },
      toggle() { if (!video.paused) { video.pause(); return; } playRange(); },
      pause() { if (!video.paused) video.pause(); },
      /* A new media key: an idle video reloads with it on the next play; a play still waiting for its
       * metadata (old key) starts again with the new key instead of being dropped. */
      keyChanged() {
        if (!st.loaded || !video.paused) return;
        const want = st.pending && st.want && st.want.id === st.id ? st.want : null;
        st.loaded = false; st.pending = null; video.removeAttribute('src'); video.load(); cover();
        if (want) seek(want.t, want.play);
      },
      /* Video gone (source cleaned/archived per evidence): no playback for this job. */
      unavailable(reason) { st.available = false; st.reason = reason || null; },
      release() {
        stats.videoReleases++;
        st.loaded = false; st.pending = null; st.id = null; st.want = null;
        video.pause(); video.removeAttribute('src'); video.load(); cover();
        if (video.parentNode) video.parentNode.removeChild(video);
        st.host = null;
      },
      reset() { st.available = true; st.reason = null; st.keyRetries = 0; },
      current: () => st.id,
      playing: () => !video.paused && !!st.id,
      allowed,
      state: () => ({id: st.id, loaded: st.loaded, reveal: st.reveal, mi: st.mi, available: st.available, reason: st.reason}),
    };
  }

  root.BFReviewMedia = {createImageLoader, createPlayer};
})(window);
