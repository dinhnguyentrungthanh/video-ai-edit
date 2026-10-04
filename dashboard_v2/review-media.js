/* Review dialog media (R0): the image loader. At most 2 images load at once (the phone listener
 * answers HTTP/1.0, one connection per request, with a per-device connection limit), and only for
 * cards near the visible part of the dialog; the selected card goes first. Images are plain
 * same-origin <img src> (the V2 CSP has no blob:). The shared <video>, strip and timeline come in R1.
 */
(function (root) {
  'use strict';

  function createImageLoader(options) {
    options = options || {};
    const max = options.max || 2, stats = options.stats || {};
    let active = 0, order = 0, observer = null;
    const waiting = [], entries = new Map();
    stats.maxImages = stats.maxImages || 0; stats.activeImages = 0; stats.imagesLoaded = stats.imagesLoaded || 0; stats.imagesFailed = stats.imagesFailed || 0;

    function settle(entry, ok) {
      const art = entry.art;
      active--; stats.activeImages = active;
      if (ok) { stats.imagesLoaded++; art.classList.add('loaded'); art.classList.remove('loading'); }
      else if (entry.tries < 1 && art.isConnected) { entry.tries++; waiting.push(entry); }
      else { stats.imagesFailed++; art.classList.add('failed'); art.classList.remove('loading'); }
      pump();
    }
    function pump() {
      while (active < max && waiting.length) {
        waiting.sort((a, b) => b.priority - a.priority || a.order - b.order);
        const entry = waiting.shift();
        if (entry.cancelled || !entry.art.isConnected) continue;
        active++; stats.activeImages = active; stats.maxImages = Math.max(stats.maxImages, active);
        const img = entry.img;
        img.onload = () => { img.onload = img.onerror = null; settle(entry, true); };
        img.onerror = () => { img.onload = img.onerror = null; settle(entry, false); };
        entry.art.classList.add('loading');
        img.src = entry.url;
      }
    }
    function enqueue(entry) {
      if (entry.queued) return;
      entry.queued = true;
      if (observer) observer.unobserve(entry.art);
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
      /* art: the card's image box; its <img> gets url when the card comes near the view. */
      watch(art, url, priority) {
        const img = art.querySelector('img');
        if (!img || !url) { art.classList.add('failed'); return; }
        const entry = {art, img, url, priority: priority || 0, order: order++, tries: 0, queued: false, cancelled: false};
        entries.set(art, entry);
        if (observer) observer.observe(art); else enqueue(entry);
      },
      /* The selected card loads before the others. */
      prioritize(art) {
        const entry = entries.get(art);
        if (!entry) return;
        entry.priority = 10;
        if (!entry.queued) enqueue(entry); else pump();
      },
      /* Cards removed by a new list: waiting images are dropped (an image already loading finishes detached). */
      forget(container) {
        for (const [art, entry] of entries) {
          if (container && container.contains(art) && art.isConnected) continue;
          entry.cancelled = true; entries.delete(art); if (observer) observer.unobserve(art);
        }
      },
      reset() { for (const entry of entries.values()) entry.cancelled = true; entries.clear(); waiting.length = 0; if (observer) observer.disconnect(); observer = null; },
      active: () => active,
    };
  }

  root.BFReviewMedia = {createImageLoader};
})(window);
