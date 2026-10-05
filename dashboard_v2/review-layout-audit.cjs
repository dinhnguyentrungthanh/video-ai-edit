/* R4.1 (P17): one measurement of the review dialog, run in the page by page.evaluate(audit, {touch}).
 * Shared by browser-check-review-layout.cjs (fake server) and browser-check-review-phone.cjs (the real phone listener).
 * - small: visible text under 12 px;
 * - tiny (touch only): a visible button, link, select, summary, checkbox label or timeline under 44 × 44 px;
 * - outside: an element past the left or right edge of the window that is not inside a sideways scroller;
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
  const small = new Set(), tiny = new Set(), outside = new Set(), scrolling = new Set();
  for (const root of roots) {
    for (const el of root.querySelectorAll('*')) {
      if (!shown(el)) continue;
      const cs = getComputedStyle(el);
      if ([...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && parseFloat(cs.fontSize) < 12) small.add(label(el) + ' ' + cs.fontSize);
      const r = el.getBoundingClientRect();
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
  return {small: [...small], tiny: [...tiny], outside: [...outside], scrolling: [...scrolling],
    page: document.documentElement.scrollWidth > innerWidth + 1};
}

module.exports = {audit};
