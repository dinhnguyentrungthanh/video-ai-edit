/* Review dialog browser check, R4.1 (6.2, P17): Chromium (Playwright) on live.html with the fake Control Center of
 * review-fake-server.cjs (synthetic queues, no real backend, database or network).
 * - phone 375 × 740 and 390 × 844, phone held sideways 844 × 390 (touch), laptop 1024 × 800, PC 1440 × 900; light and dark:
 *   full screen and 1 column on the phone, almost full screen and 1 column on the laptop, 2 columns on the PC;
 *   nothing past the window edges and no sideways page scroll; on the phone only the chip row scrolls sideways and the
 *   tools row wraps; text ≥ 12 px on the phone (with "Chi tiết kỹ thuật" open); every button, link, select, summary,
 *   checkbox and timeline ≥ 44 × 44 px on a touch screen; no key hints on a touch screen; the cards keep half of a phone
 *   screen (40 % held sideways, where the dialog is full screen and the header scrolls in its band); the save state
 *   never changes the header height;
 * - the chip row fades at the side with more chips and keeps its place when a chip is chosen;
 * - the zoomed card, the confirm and the export dialog over the review dialog fit and keep 44 px targets on the phone;
 * - R4-B1: no visible text is cut by a box with overflow hidden; the "áp dụng …" label of a borrowed red box (S9) stays
 *   inside the image when the box touches the right, left, top or bottom edge, or fills the height (375 and 1440 px);
 * - no POST, no blob:, no console or CSP error.
 * Run: node dashboard_v2/browser-check-review-layout.cjs
 */
'use strict';
const path = require('path');
const assert = require('assert/strict');
const {execSync} = require('child_process');

function loadPlaywright() {
  try { return require('playwright'); } catch (_) { /* fall through */ }
  try { return require(path.join(execSync('npm root -g', {encoding: 'utf8'}).trim(), 'playwright')); } catch (_) { return null; }
}
const pw = loadPlaywright();
if (!pw) { process.stdout.write('SKIP browser-check-review-layout: Playwright is not installed\n'); process.exit(0); }

const F = require('./review-fake-server.cjs').create();
const {server, jobs, queues, queueFor, posts} = F;
const {audit} = require('./review-layout-audit.cjs');
let passed = 0;
async function check(name, fn) { await fn(); passed++; process.stdout.write('OK ' + name + '\n'); }
const job101 = jobs.find(j => j.id === 101), saved101 = JSON.stringify({state: job101.state, queue_kind: job101.queue_kind, review_summary: job101.review_summary});

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = 'http://127.0.0.1:' + server.address().port;
  const browser = await pw.chromium.launch({});
  const problems = [];
  const EXPECTED = [/\/review\/(frame|video)\?/];
  const newPage = async (size, theme) => {
    const page = await browser.newPage({viewport: {width: size.w, height: size.h}, hasTouch: size.touch, isMobile: size.touch});
    await page.addInitScript(t => { try { localStorage.setItem('biliflow-v2-theme', t); } catch (_) { /* ignore */ } }, theme);
    const tag = size.w + '×' + size.h + ' ' + theme;
    if (size.touch) touchPages.add(page);
    page.on('pageerror', e => problems.push(tag + ' pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) problems.push(tag + ' console: ' + m.text()); });
    page.on('response', r => { if (r.status() >= 400 && !EXPECTED.some(re => re.test(r.url()))) problems.push(tag + ' HTTP ' + r.status() + ' ' + r.url()); });
    page.on('request', r => { const u = r.url(); if (u.startsWith('blob:') || !(u.startsWith(base) || u.startsWith('data:'))) problems.push('request ' + u); });
    return page;
  };
  const open = async (page, id) => {
    await page.goto(base + `/dashboard-v2/#review/${id}/videos`);
    await page.waitForSelector('#review-dialog[open] article.rv-card');
    await page.waitForFunction(() => document.querySelectorAll('#review-dialog .rv-art.loaded').length >= 2, null, {timeout: 8000}).catch(() => {});
  };
  const touchPages = new WeakSet();
  const tap = (page, selector) => touchPages.has(page) ? page.locator(selector).first().tap() : page.locator(selector).first().click();
  const expectClean = (result, size, label) => {
    assert.deepEqual(result.outside, [], label + ': past the window edge');
    assert.deepEqual(result.clipped, [], label + ': text cut by a box with overflow hidden (R4-B1)');
    assert.equal(result.page, false, label + ': the page scrolls sideways');
    if (size.w <= 820) assert.deepEqual(result.scrolling.filter(x => !x.includes('rv-chips')), [], label + ': only the chip row scrolls sideways');
    else assert.deepEqual(result.scrolling, [], label + ': nothing scrolls sideways');
    if (size.w <= 820) assert.deepEqual(result.small, [], label + ': text under 12 px');
    if (size.touch) assert.deepEqual(result.tiny, [], label + ': touch targets under 44 px');
  };
  const SIZES = [{w: 375, h: 740, touch: true}, {w: 390, h: 844, touch: true}, {w: 844, h: 390, touch: true}, {w: 1024, h: 800, touch: false}, {w: 1440, h: 900, touch: false}];

  try {
    for (const theme of ['light', 'dark']) for (const size of SIZES) {
      await check(`${size.w}×${size.h} ${theme}${size.touch ? ' touch' : ''}: dialog size and columns, no overflow, text ≥ 12 px and 44 px targets where they apply, key hints, chip row, zoomed card and confirm`, async () => {
        queues.delete(101);
        const page = await newPage(size, theme);
        await open(page, 101);
        await page.evaluate(() => document.querySelectorAll('#review-dialog details.rv-tech').forEach(d => { d.open = true; }));
        const geo = await page.evaluate(() => {
          const d = document.getElementById('review-dialog'), r = d.getBoundingClientRect(), keys = d.querySelector('.rv-keys');
          return {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height), theme: document.documentElement.dataset.theme,
            body: Math.round(d.querySelector('.rv-body').getBoundingClientRect().height),
            cols: getComputedStyle(d.querySelector('.rv-cards')).gridTemplateColumns.split(' ').length, keys: !!keys && keys.getBoundingClientRect().width > 0};
        });
        assert.equal(geo.theme, theme);
        if (size.w <= 820 || size.h <= 560) assert.deepEqual([geo.x, geo.y, geo.w, geo.h, geo.cols], [0, 0, size.w, size.h, 1], 'full screen, 1 column');
        else if (size.w < 1100) assert.deepEqual([geo.w, geo.cols], [size.w - 24, 1], 'almost full screen, 1 column');
        else assert.equal(geo.cols, 2, '2 columns');
        assert.equal(geo.keys, !size.touch && size.w > 820, 'key hints only with a mouse on a wide screen');
        // The cards keep most of a phone screen; held sideways, the header scrolls in its band and they keep 40 %.
        if (size.touch) assert.ok(geo.body >= (size.h <= 560 ? 0.4 : 0.5) * size.h, 'cards area ' + geo.body + ' of ' + size.h);
        // "Đang lưu…" / "Đã lưu" never changes the header height (the cards do not jump).
        const heads = await page.evaluate(() => { const box = document.querySelector('#review-dialog .rv-head'), save = box.querySelector('.rv-save'), h = [box.offsetHeight];
          for (const text of ['Đang áp dụng…', 'Đang lưu…', 'Đã lưu', '']) { save.textContent = text; h.push(box.offsetHeight); } return h; });
        assert.equal(new Set(heads).size, 1, 'header height with each save state: ' + heads);
        expectClean(await page.evaluate(audit, {touch: size.touch}), size, 'dialog');
        const chips = () => page.evaluate(() => { const c = document.querySelector('#review-dialog .rv-chips'), a = c.querySelector('.filter-tab.active'), cr = c.getBoundingClientRect(), ar = a.getBoundingClientRect();
          return {left: c.classList.contains('more-left'), right: c.classList.contains('more-right'), scroll: Math.round(c.scrollLeft), max: c.scrollWidth - c.clientWidth,
            active: a.dataset.filter, visible: ar.left >= cr.left - 1 && ar.right <= cr.right + 1}; });
        let c = await chips();
        if (size.w <= 820) {
          assert.deepEqual([c.left, c.right, c.scroll], [false, true, 0], 'more chips to the right: the right edge fades');
          await page.evaluate(() => { const el = document.querySelector('#review-dialog .rv-chips'); el.scrollLeft = el.scrollWidth; });
          await page.waitForFunction(() => document.querySelector('#review-dialog .rv-chips').classList.contains('more-left'));
          c = await chips();
          assert.deepEqual([c.left, c.right], [true, false], 'at the end: the left edge fades');
          await tap(page, '#review-dialog .rv-chips [data-filter="all"]');
          await page.waitForFunction(() => document.querySelector('#review-dialog .rv-chips .filter-tab.active').dataset.filter === 'all');
          c = await chips();
          assert.ok(c.visible && c.scroll > 0 && c.left, 'redrawn, the row keeps its place and shows the chosen chip: ' + JSON.stringify(c));
        } else assert.deepEqual([c.left, c.right, c.max <= 1], [false, false, true], 'the chips fit: no fade');
        // The zoomed card and the confirm over the review dialog.
        await tap(page, '#review-dialog article.rv-card [data-review="zoom"]');
        await page.waitForSelector('#review-dialog article.rv-card.zoom');
        expectClean(await page.evaluate(audit, {touch: size.touch}), size, 'zoomed card');
        await tap(page, '#review-dialog article.rv-card.zoom [data-review="zoom"]');
        await page.waitForFunction(() => !document.querySelector('#review-dialog article.rv-card.zoom'));
        await tap(page, '#review-dialog .rv-bulk[data-kind="bulkKeep"]');
        await page.waitForSelector('#review-dialog .rv-confirm[open]');
        expectClean(await page.evaluate(audit, {touch: size.touch}), size, 'confirm');
        await tap(page, '#review-dialog .rv-confirm [data-confirm="no"]');
        await page.waitForFunction(() => !document.querySelector('#review-dialog .rv-confirm').open);
        assert.equal(posts.length, 0, 'nothing sent');
        await page.close();
      });
    }

    await check('R4-B1: the "áp dụng …" label of a borrowed red box stays whole inside the image at every edge, also zoomed (375 × 740 touch and 1440 × 900, light and dark)', async () => {
      // [left, top, width, height] of the owner's red box, as shares of the frame.
      const EDGES = {right: [0.86, 0.35, 0.13, 0.12], left: [0.01, 0.35, 0.13, 0.12], 'top right': [0.84, 0, 0.16, 0.1], 'bottom left': [0, 0.9, 0.16, 0.1],
        'bottom right': [0.85, 0.88, 0.15, 0.12], 'full height, right half': [0.7, 0, 0.3, 1], 'wide, top': [0.05, 0.02, 0.9, 0.08]};
      const card = (page, id) => page.locator(`#review-dialog article.rv-card[data-item="${id}"]`);
      for (const theme of ['light', 'dark']) for (const size of [SIZES[0], SIZES[4]]) {
        const page = await newPage(size, theme);
        for (const [name, [l, t, w, h]] of Object.entries(EDGES)) {
          queues.delete(101);
          const q = queueFor(101), logo = q.items.find(x => x.id === 'visual_logo-101-0007'), end = q.items.find(x => x.id === 'visual_logo-101-0008');
          const [sw, sh] = logo.source_frame_size.map(Number);
          logo.decision = 'BLUR';
          logo.suggested_region_source_pixels = logo.decision_region_source_pixels = {x: Math.round(l * sw), y: Math.round(t * sh), width: Math.round(w * sw), height: Math.round(h * sh)};
          Object.assign(end, {candidate_type: 'ending_boundary', decision: null, start_seconds: logo.start_seconds + 0.5, end_seconds: logo.end_seconds + 1});
          await page.goto('about:blank'); // the same address again would not reload the page
          await page.goto(base + '/dashboard-v2/#review/101/videos');
          await page.waitForSelector(`#review-dialog article.rv-card[data-item="${end.id}"] .rv-region.borrowed em`);
          for (const zoomed of [false, true]) {
            const tag = size.w + ' ' + theme + ' ' + name + (zoomed ? ' zoomed' : '');
            if (zoomed) { await card(page, end.id).locator('[data-review="zoom"]').click(); await page.waitForSelector(`#review-dialog article.rv-card.zoom[data-item="${end.id}"]`); }
            await card(page, end.id).locator('.rv-art').scrollIntoViewIfNeeded();
            const m = await card(page, end.id).locator('.rv-region em').evaluate(em => {
              const a = em.closest('.rv-art').getBoundingClientRect(), r = em.getBoundingClientRect();
              return {inside: r.left >= a.left - 0.5 && r.right <= a.right + 0.5 && r.top >= a.top - 0.5 && r.bottom <= a.bottom + 0.5, font: parseFloat(getComputedStyle(em).fontSize),
                text: em.textContent, box: [r.left - a.left, r.right - a.left, r.top - a.top, r.bottom - a.top].map(Math.round), art: [Math.round(a.width), Math.round(a.height)]};
            });
            assert.match(m.text, /^áp dụng \d\d:\d\d\.\d–\d\d:\d\d\.\d$/, tag);
            assert.ok(m.inside, tag + ': label inside the image ' + JSON.stringify(m));
            if (size.w <= 820) assert.ok(m.font >= 12, tag + ': 12 px text');
            assert.deepEqual((await page.evaluate(audit, {touch: size.touch})).clipped, [], tag + ': no text cut');
          }
        }
        await page.close();
      }
      queues.delete(101);
      assert.equal(posts.length, 0, 'nothing sent');
    });

    await check('Phone 390 and 375 px, light and dark: the export dialog over the review dialog fits with 44 px targets; Esc closes only it', async () => {
      const q = queueFor(101);
      for (const x of q.items) if (!x.decision) x.decision = 'KEEP';
      const counts = {total: q.items.length, pending: 0, decisions: {KEEP: 0, BLUR: 0, CUT: 0, NEEDS_MORE_CONTEXT: 0}};
      for (const x of q.items) counts.decisions[x.decision]++;
      q.counts = counts; q.status = 'READY_FOR_EDIT_PLAN'; F.syncJob(101, q);
      for (const theme of ['light', 'dark']) for (const size of [SIZES[0], SIZES[1]]) {
        const page = await newPage(size, theme);
        await page.goto(base + '/dashboard-v2/#review/101/videos');
        await page.waitForSelector('#review-dialog[open] article.rv-card');
        await tap(page, '#review-dialog .rv-export');
        await page.waitForSelector('#modal[open] #export-mode');
        const label = size.w + ' ' + theme + ' export dialog';
        expectClean(await page.evaluate(audit, {touch: true}), size, label);
        await page.selectOption('#export-mode', 'custom');
        expectClean(await page.evaluate(audit, {touch: true}), size, label + ' (custom limit)');
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.getElementById('modal').open);
        assert.equal(await page.evaluate(() => document.getElementById('review-dialog').open), true);
        await page.close();
      }
      queues.delete(101); Object.assign(job101, JSON.parse(saved101));
      assert.equal(posts.length, 0, 'no finalize');
    });

    await check('No POST, no blob:, no console or CSP error, no request outside the origin', async () => {
      assert.deepEqual(posts, []);
      assert.deepEqual(problems, []);
    });
  } finally {
    await browser.close();
    server.close();
  }
  process.stdout.write(JSON.stringify({passed, failed: 0}) + '\n');
})().catch(error => { console.error(error); process.exitCode = 1; server.close(); });
