/* R4.1 (P17): one measurement of the review dialog, run in the page by page.evaluate(audit, {touch}).
 * Shared by browser-check-review-layout.cjs (fake server) and browser-check-review-phone.cjs (the real phone listener).
 * - small: visible text under 12 px;
 * - tiny (touch only): a visible button, link, select, summary, checkbox label or timeline under 44 × 44 px;
 * - outside: an element past the left or right edge of the window that is not inside a sideways scroller;
 * - clipped (R4-B1): visible text cut by an ancestor with overflow hidden or clip (the image box of a card, a frame
 *   thumbnail…), up to the nearest scroller (what lies past a scroller can be scrolled into view); only painted
 *   text counts (a card off screen keeps a placeholder size under content-visibility: auto);
 * - scrolling: the elements that scroll sideways (on a phone only the chip row may);
 * - page: the page itself scrolls sideways.
 * Inside the review dialog and, when open, the dialogs over it (the confirm is inside it; the export dialog is #modal).
 */
'use strict';

function audit(options) {
  const touch = !!(options && options.touch);
  const roots = [document.getElementById('review-dialog'), document.getElementById('modal')].filter(d => d && d.open);
  const shown = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const label = el => el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).join('.') : '') +
    (el.dataset && el.dataset.review ? '[' + el.dataset.review + ']' : '') + (el.id ? '#' + el.id : '');
  const scroller = (el, root) => {
    for (let p = el.parentElement; p && p !== root; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (/(auto|scroll)/.test(cs.overflowX) && p.scrollWidth > p.clientWidth + 1) return p;
    }
    return null;
  };
  // How much of r is cut by ancestors with overflow hidden/clip, per axis, until the first scroll container.
  const cut = (el, r, root) => {
    for (let p = el.parentElement; p && p !== root.parentElement; p = p.parentElement) {
      const cs = getComputedStyle(p), x = cs.overflowX, y = cs.overflowY;
      if (/(auto|scroll)/.test(x + ' ' + y)) return null;
      const b = p.getBoundingClientRect();
      const cutX = /(hidden|clip)/.test(x) && (r.left < b.left + p.clientLeft - 1 || r.right > b.left + p.clientLeft + p.clientWidth + 1);
      const cutY = /(hidden|clip)/.test(y) && (r.top < b.top + p.clientTop - 1 || r.bottom > b.top + p.clientTop + p.clientHeight + 1);
      if (cutX || cutY) {
        const left = Math.max(r.left, b.left + p.clientLeft), right = Math.min(r.right, b.left + p.clientLeft + p.clientWidth);
        return label(p) + ' shows ' + Math.max(0, Math.round(right - left)) + '/' + Math.round(r.width) + ' px wide' + (cutY ? ', cut top or bottom' : '');
      }
    }
    return null;
  };
  const small = new Set(), tiny = new Set(), outside = new Set(), scrolling = new Set(), clipped = new Set();
  for (const root of roots) {
    for (const el of root.querySelectorAll('*')) {
      if (!shown(el)) continue;
      const cs = getComputedStyle(el);
      const text = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()), r = el.getBoundingClientRect();
      if (text && parseFloat(cs.fontSize) < 12) small.add(label(el) + ' ' + cs.fontSize);
      // A card off screen is not painted (content-visibility: auto) and keeps a placeholder size: nothing to measure there.
      const painted = !el.checkVisibility || el.checkVisibility({contentVisibilityAuto: true});
      if (text && painted) { const c = cut(el, r, root); if (c) clipped.add(label(el) + ' "' + el.textContent.trim().slice(0, 40) + '": ' + c); }
      if ((r.right > innerWidth + 1 || r.left < -1) && !scroller(el, root)) outside.add(label(el) + ' ' + Math.round(r.left) + '…' + Math.round(r.right));
      if (/(auto|scroll)/.test(cs.overflowX) && el.scrollWidth > el.clientWidth + 1) scrolling.add(label(el));
    }
    if (!touch) continue;
    for (const el of root.querySelectorAll('button, a[href], select, summary, input, label, [data-review="seek"]')) {
      if (!shown(el) || el.matches('label input') || (el.tagName === 'LABEL' && !el.querySelector('input'))) continue;
      const r = el.getBoundingClientRect();
      if (r.height < 43.5 || r.width < 43.5) tiny.add(label(el) + ' ' + Math.round(r.width) + '×' + Math.round(r.height));
    }
  }
  return {small: [...small], tiny: [...tiny], outside: [...outside], scrolling: [...scrolling], clipped: [...clipped],
    page: document.documentElement.scrollWidth > innerWidth + 1};
}

module.exports = {audit};
