/* Session R evidence: screenshots of the self-made review job (make_fixture.py) before and after, and the gate checks.
 *
 *   node shoot.cjs <before base URL> <before root> <after base URL> <after root> <out folder>
 *
 * Each base is a test Control Center started by make_fixture.py serve (main tree for before, the branch for after).
 * The page only reads: no click sends a decision. Per side: the three cards at 390 × 844 light, the subtitle-band
 * card at 390 × 844 dark, the dialog at 1440 × 900 light, and (after) the subtitle-band card with "Ẩn khung"; at
 * 390 × 844 light also the text under the image of the subtitle-band and ticker cards ("Máy đọc" after).
 * Checks on the after page: boxes are a line outside the region (no fill), the magnifier is drawn at ≥ 2×, "Máy đọc"
 * shows the OCR lines, the zone note names "subtitle" as a zone, the ticker card starts with its full line, nothing
 * scrolls the page sideways. Both review-queue.json files keep their SHA-256 while the pages are open.
 */
'use strict';
const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const [beforeBase, beforeRoot, afterBase, afterRoot, out] = process.argv.slice(2);
if (!out) { console.error('usage: node shoot.cjs <before URL> <before root> <after URL> <after root> <out folder>'); process.exit(2); }
const QUEUE = 'reports/jobs/review-ui-demo/review-queue.json';
const sha = root => crypto.createHash('sha256').update(fs.readFileSync(path.join(root, QUEUE))).digest('hex');
const cardsOf = root => {
  const q = JSON.parse(fs.readFileSync(path.join(root, QUEUE), 'utf8'));
  const find = test => q.items.find(x => test(x.labels.join(' '))).id;
  return {logo: find(l => l.startsWith('LOGO.EXAMPLE')), band: find(l => l.startsWith('TRANG.EXAMPLE')), ticker: find(l => l.startsWith('Phim cập nhật'))};
};
const SIZES = [{w: 390, h: 844, touch: true, theme: 'light', cards: ['logo', 'band', 'ticker']}, {w: 390, h: 844, touch: true, theme: 'dark', cards: ['band']},
  {w: 1440, h: 900, touch: false, theme: 'light', cards: []}];

async function open(browser, base, size, problems) {
  const page = await browser.newPage({viewport: {width: size.w, height: size.h}, hasTouch: size.touch, isMobile: size.touch});
  await page.addInitScript(t => { try { localStorage.setItem('biliflow-v2-theme', t); } catch (_) { /* ignore */ } }, size.theme);
  page.on('pageerror', e => problems.push(size.w + ' ' + size.theme + ' pageerror: ' + e.message));
  page.on('request', r => { if (r.method() !== 'GET') problems.push('not a GET: ' + r.method() + ' ' + r.url()); });
  await page.goto(base + '/dashboard-v2/#review/1/videos');
  await page.waitForSelector('#review-dialog[open] article.rv-card');
  return page;
}

/* A card's image loads when the card comes near the view (at most 2 at once); then its strip and its magnifier. */
async function ready(page, id) {
  const card = page.locator(`article.rv-card[data-item="${id}"]`);
  await card.scrollIntoViewIfNeeded();
  await page.waitForFunction(id => { const el = document.querySelector(`article.rv-card[data-item="${id}"]`), img = el && el.querySelector('.rv-art img'), f = el && el.querySelector('.rv-mag');
    return img && img.complete && img.naturalWidth > 0 && (!f || f.hidden || f.querySelector('canvas').dataset.drawn === '1'); }, id, {timeout: 20000});
  await page.waitForFunction(id => !document.querySelector(`article.rv-card[data-item="${id}"] .rv-thumb.ghost`), id, {timeout: 20000}).catch(() => {});
  await page.waitForTimeout(250);
  return card;
}

/* What the phone shows: the window with the card's top just under the dialog's sticky rows (a card is taller than
 * the window, so an element shot would cut it at the scroller and keep the sticky rows over it). */
async function shot(page, file, id, part) {
  if (id) {
    await ready(page, id);
    await page.evaluate(([id, part]) => {
      const card = document.querySelector(`article.rv-card[data-item="${id}"]`), target = part ? card.querySelector(part) : card, above = part ? 48 : 0;
      let box = card.parentElement;
      while (box && !(/(auto|scroll)/.test(getComputedStyle(box).overflowY) && box.scrollHeight > box.clientHeight)) box = box.parentElement;
      if (!box) return;
      const top = box.getBoundingClientRect().top, sticky = [...box.querySelectorAll('*')].filter(e => getComputedStyle(e).position === 'sticky')
        .map(e => e.getBoundingClientRect()).filter(r => r.top <= top + 4 && r.height < 400).reduce((m, r) => Math.max(m, r.bottom), top);
      box.scrollTop += target.getBoundingClientRect().top - above - sticky - 6;
    }, [id, part]);
    await page.waitForTimeout(300);
  }
  await page.screenshot({path: path.join(out, file), type: 'jpeg', quality: 80});
  return file;
}

async function checkAfter(page, ids, size) {
  for (const id of Object.values(ids)) await ready(page, id);
  const state = await page.evaluate(ids => {
    const card = id => document.querySelector(`article.rv-card[data-item="${id}"]`);
    const region = el => { const r = el.querySelector('.rv-region'), cs = getComputedStyle(r); return {outline: cs.outlineStyle, offset: cs.outlineOffset, fill: cs.backgroundColor, border: cs.borderTopStyle}; };
    const mag = el => { const f = el.querySelector('.rv-mag'), c = f.querySelector('canvas'); return {hidden: f.hidden, zoom: Number(c.dataset.zoom), height: Math.round(c.getBoundingClientRect().height), cap: f.querySelector('figcaption').textContent}; };
    return {
      page: document.documentElement.scrollWidth > innerWidth + 1,
      regions: Object.fromEntries(Object.entries(ids).map(([k, id]) => [k, region(card(id))])),
      mags: Object.fromEntries(Object.entries(ids).map(([k, id]) => [k, mag(card(id))])),
      ocr: Object.fromEntries(Object.entries(ids).map(([k, id]) => [k, (card(id).querySelector('.rv-ocr') || {}).textContent || ''])),
      note: (card(ids.band).querySelector('.rv-zone-note') || {}).textContent || '',
      tickerImage: card(ids.ticker).querySelector('.rv-art img').getAttribute('src'),
    };
  }, ids);
  assert.equal(state.page, false, 'the page scrolls sideways');
  for (const [k, r] of Object.entries(state.regions)) assert.deepEqual(r, {outline: 'solid', offset: '2px', fill: 'rgba(0, 0, 0, 0)', border: 'none'}, k + ': a line outside the region, no fill');
  for (const [k, m] of Object.entries(state.mags)) assert.ok(!m.hidden && m.zoom >= 2 && m.height >= 40, k + ': magnifier ' + JSON.stringify(m));
  assert.match(state.ocr.band, /^Máy đọc:“TRANG\.EXAMPLE”/);
  assert.match(state.ocr.ticker, /^Máy đọc:“Phim cập nhật nhanh nhất tại TRANG\.EXAMPLE\. Phim chỉ đăng/);
  assert.match(state.ocr.logo, /^Máy đọc:“LOGO\.EXAMPLE”/);
  assert.match(state.note, /^Nhãn “REVIEW 58 \| subtitle” trên ảnh do máy quét chữ in: “subtitle” chỉ là tên vùng \(vùng phụ đề\)/);
  assert.match(state.tickerImage, /track-00022\.jpg/, 'the ticker card starts with its full line');
  return {size: size.w + '×' + size.h + ' ' + size.theme, mags: state.mags, ocr: state.ocr, note: state.note};
}

(async () => {
  let pw;
  try { pw = require('playwright'); } catch (_) { pw = require('playwright-core'); }
  fs.mkdirSync(out, {recursive: true});
  const roots = {before: beforeRoot, after: afterRoot}, bases = {before: beforeBase, after: afterBase};
  const hashes = {before: sha(beforeRoot), after: sha(afterRoot)}, problems = [], files = [], checks = [];
  const browser = await pw.chromium.launch({});
  try {
    for (const side of ['before', 'after']) {
      const ids = cardsOf(roots[side]);
      for (const size of SIZES) {
        const page = await open(browser, bases[side], size, problems), tag = side + '-' + size.w + '-' + size.theme;
        if (side === 'after') checks.push(await checkAfter(page, ids, size));
        if (!size.cards.length) { await ready(page, ids.band); await page.locator('#review-dialog .rv-cards').evaluate(c => c.scrollIntoView()); await page.waitForTimeout(250); files.push(await shot(page, tag + '-dialog.jpg')); }
        for (const name of size.cards) files.push(await shot(page, tag + '-' + name + '.jpg', ids[name]));
        // Below the image: the card's title, its time line and (after) "Máy đọc".
        if (size.w === 390 && size.theme === 'light') for (const name of ['band', 'ticker']) files.push(await shot(page, tag + '-' + name + '-read.jpg', ids[name], '.rv-meta'));
        if (side === 'after' && size.w === 390 && size.theme === 'light') {
          await (await ready(page, ids.band)).locator('[data-review="frames"]').click();
          await page.waitForFunction(id => document.querySelector(`article.rv-card[data-item="${id}"]`).classList.contains('bare'), ids.band);
          files.push(await shot(page, tag + '-band-bare.jpg', ids.band));
        }
        await page.close();
      }
    }
  } finally { await browser.close(); }
  assert.deepEqual(problems, []);
  assert.deepEqual({before: sha(beforeRoot), after: sha(afterRoot)}, hashes, 'review-queue.json unchanged while the pages were open');
  const result = {queue_sha256: hashes, files, checks};
  fs.writeFileSync(path.join(out, 'shoot-result.json'), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify({files: files.length, checks: checks.length, queue_sha256: hashes}));
})().catch(error => { console.error(error); process.exit(1); });
