/* Review dialog browser check, R2 (decisions): Chromium (Playwright) on live.html and index.html with the fake
 * Control Center of review-fake-server.cjs (synthetic queues; POST decision/clear change its in-memory queue).
 * - the bodies V2 sends equal, field by field, what the real classic page (_interactive_html) sends for the
 *   same choices on the same fake server;
 * - at 1440, 1024 and 390 px, light and dark: buttons, .selected, the blur / cut illustration, "Đang lưu… /
 *   Đã lưu", the S5 confirm (Enter confirms, Esc only cancels it), keys 1–4 / ←→ / Z, no horizontal overflow;
 * - the write chain: two quick decisions = two POSTs in order, 300/900 ms retries, 400 not resent, 403 → new
 *   session then one resend, the last failure reloads the queue and reopens the card (switching to "Tất cả"),
 *   polling waits while saving, closing never cancels a write (its error in the dashboard toast), reopening
 *   shows the server queue; offline, locks (S6, S8), undo, "Tự chuyển cảnh", sticky, region / memory buttons;
 * - R1-B1: the image loader gives a slot back when a box is watched again while loading;
 * - the demo page decides in memory without any request; no blob:, no console or CSP error.
 * Run: node dashboard_v2/browser-check-review-write.cjs
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
if (!pw) { process.stdout.write('SKIP browser-check-review-write: Playwright is not installed\n'); process.exit(0); }

const F = require('./review-fake-server.cjs').create();
const {server, jobs, queues, queueFor, counters, server_state, posts} = F;
let passed = 0;
async function check(name, fn) { await fn(); passed++; process.stdout.write('OK ' + name + '\n'); }
const bodies = from => posts.slice(from).map(p => p.body);
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, label, timeout = 8000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await fn()) return; await sleep(25); }
  throw new Error('timeout: ' + label);
}

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = 'http://127.0.0.1:' + server.address().port;
  const browser = await pw.chromium.launch({});
  const problems = [];
  // Scripted on purpose: failed writes (500/400/403), the scanning job 102, frames of the R1 fake (403 on an old key).
  const EXPECTED = [/\/review\/(decision|clear)$/, /\/api\/jobs\/102\/review\/queue$/, /\/review\/(frame|video)\?/];
  const newPage = async (width, height, theme, label) => {
    const page = await browser.newPage({viewport: {width, height}});
    await page.addInitScript(t => { try { localStorage.setItem('biliflow-v2-theme', t); } catch (_) { /* ignore */ } }, theme);
    const tag = label || width + ' ' + theme;
    page.on('pageerror', e => problems.push(tag + ' pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) problems.push(tag + ' console: ' + m.text()); });
    page.on('response', r => { if (r.status() >= 400 && !EXPECTED.some(re => re.test(r.url()))) problems.push(tag + ' HTTP ' + r.status() + ' ' + r.url()); });
    page.on('request', r => { const u = r.url(); if (u.startsWith('blob:') || !(u.startsWith(base) || u.startsWith('data:'))) problems.push('request ' + u); });
    return page;
  };
  const card = (page, id) => page.locator(`#review-dialog article.rv-card[data-item="${id}"]`);
  const button = (page, id, decision) => card(page, id).locator(`.rv-actions [data-review="decide"][data-decision="${decision}"]`);
  const confirmOpen = page => page.evaluate(() => document.querySelector('#review-dialog .rv-confirm').open);
  const state = page => page.evaluate(() => ({open: document.getElementById('review-dialog').open, hash: location.hash, focus: (document.querySelector('#review-dialog article.rv-card.on') || {dataset: {}}).dataset.item,
    save: (document.querySelector('#review-dialog .rv-save') || {}).textContent, toast: (() => { const t = document.querySelector('#review-dialog .rv-toast'); return t && !t.hidden ? t.textContent : ''; })(),
    filter: (document.querySelector('#review-dialog .rv-chips .filter-tab.active') || {dataset: {}}).dataset.filter}));
  async function openJob(page, id, filter, view) {
    // Always a fresh dialog: the view first (closes an open one), then the review address.
    if (await page.evaluate(() => !!document.getElementById('review-dialog'), null).catch(() => false)) {
      await page.evaluate(v => { location.hash = '#' + v; }, view || 'videos');
      await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    }
    await page.goto(base + `/dashboard-v2/#review/${id}/${view || 'videos'}`);
    await page.waitForSelector('#review-dialog[open] article.rv-card');
    if (filter && filter !== 'pending') {
      await page.locator(`#review-dialog .rv-chips [data-filter="${filter}"]`).click();
      await page.waitForFunction(f => document.querySelector(`#review-dialog .rv-chips [data-filter="${f}"]`).classList.contains('active'), filter);
    }
  }
  const fresh = () => { queues.delete(101); queues.delete(103); server_state.failNext = []; server_state.postDelay = 0; server_state.token = F.TOKEN; };

  try {
    const page = await newPage(1440, 900, 'light', 'main');
    await check('R1-B1: a box watched again while its image loads gives the slot back (active() returns to 0); at most 2 images at once', async () => {
      await page.goto(base + '/dashboard-v2/#overview'); await page.waitForSelector('#main h1');
      counters.mediaMax = 0;
      const result = await page.evaluate(async () => {
        const L = window.BFReviewMedia.createImageLoader({max: 2, stats: {}}), url = n => '/media/' + encodeURIComponent('demo/poster-blue.svg') + '?n=' + n;
        const box = () => { const b = document.createElement('div'); b.innerHTML = '<img alt="">'; document.body.appendChild(b); return b; };
        const wait = ms => new Promise(r => setTimeout(r, ms)), out = {};
        const a = box();
        L.watch(a, url(1), 10); L.watch(a, url(1), 10);
        out.twice = L.active(); await wait(1200); out.twiceAfter = L.active(); out.loaded = a.classList.contains('loaded');
        const b = box();
        L.watch(b, url(2), 10); L.watch(b, url(3), 10);
        await wait(1200); out.swapAfter = L.active(); out.swapSrc = b.querySelector('img').getAttribute('src');
        const many = Array.from({length: 7}, box);
        let peak = 0;
        many.forEach((m, i) => { L.watch(m, url(10 + i), 10); if (i % 2) L.watch(m, url(10 + i), 10); peak = Math.max(peak, L.active()); });
        for (let i = 0; i < 40 && L.active(); i++) { peak = Math.max(peak, L.active()); await wait(100); }
        out.manyAfter = L.active(); out.peak = peak; out.allLoaded = many.every(m => m.classList.contains('loaded'));
        [a, b, ...many].forEach(n => n.remove());
        return out;
      });
      assert.deepEqual(result, {twice: 1, twiceAfter: 0, loaded: true, swapAfter: 0, swapSrc: result.swapSrc, manyAfter: 0, peak: 2, allLoaded: true}, JSON.stringify(result));
      assert.match(result.swapSrc, /n=3$/);
      assert.ok(counters.mediaMax <= 2, 'server saw at most 2 images at once: ' + counters.mediaMax);
    });

    await check('Payloads: for the same choices V2 sends, field by field, the bodies the real classic page sends (R2-K listed)', async () => {
      // The classic page: the same functions its buttons call (confirm() answered "OK").
      fresh();
      // The classic page loads its frames as blob: URLs (fetch + createObjectURL): only its page errors are watched.
      const classic = await browser.newPage({viewport: {width: 1440, height: 900}});
      classic.on('pageerror', e => problems.push('classic pageerror: ' + e.message));
      await classic.goto(base + '/classic/101');
      await classic.waitForFunction(() => typeof queue !== 'undefined' && queue && queue.items && queue.items.length === 30);
      await classic.evaluate(() => { window.confirm = () => true; window.alert = m => { (window.__alerts = window.__alerts || []).push(m); }; });
      const start = posts.length;
      const steps = [
        "decide('gore-101-0004','KEEP',false)", "decide('adult-101-0000','CUT',false)", "decide('text-101-0003','NEEDS_MORE_CONTEXT',false)",
        "decide('gore-101-0004','BLUR',true)", "decide('visual_logo-101-0001','KEEP',false,'Đã xác nhận vùng khoanh đỏ là tiêu đề hoặc nội dung hợp lệ của phim')",
        "decide('visual_logo-101-0001','BLUR',false,'Đã xác nhận vùng khoanh đỏ là logo thương hiệu')", "decide('visual_logo-101-0002','KEEP',false,null,true)",
        "decide('visual_logo-101-0002','BLUR',false,null,false,true)", "clearDecision('text-101-0003')", 'undo()', "decide('adult-101-0009','KEEP',false)",
        "focusId='violence-101-0005';keyDecision(4)", "decide('text-101-0016','BLUR',false)", "decide('advisory-101-0001','CUT',false)",
      ];
      for (const [i, step] of steps.entries()) {
        await classic.evaluate(code => { (0, eval)(code); }, step);
        await until(() => posts.length >= start + i + 1, 'classic POST ' + step);
      }
      const old = bodies(start);
      assert.deepEqual(await classic.evaluate(() => window.__alerts || []), [], 'no classic error');
      await classic.close();
      // V2: the real buttons, the S5 confirm and the keys.
      fresh();
      await openJob(page, 101, 'all');
      const v2start = posts.length, ok = async n => until(() => posts.length >= v2start + n, 'V2 POST ' + n);
      const yes = async () => { await page.waitForSelector('#review-dialog .rv-confirm[open]'); await page.locator('#review-dialog .rv-confirm [data-confirm="yes"]').click(); };
      await button(page, 'gore-101-0004', 'KEEP').click(); await ok(1);
      await button(page, 'adult-101-0000', 'CUT').click(); await ok(2);
      await button(page, 'text-101-0003', 'NEEDS_MORE_CONTEXT').click(); await ok(3);
      await button(page, 'gore-101-0004', 'BLUR').click(); await yes(); await ok(4);
      await card(page, 'visual_logo-101-0001').locator('[data-review="region"][data-decision="KEEP"]').click(); await ok(5);
      await card(page, 'visual_logo-101-0001').locator('[data-review="region"][data-decision="BLUR"]').click(); await ok(6);
      await card(page, 'visual_logo-101-0002').locator('[data-review="studio"]').click(); await ok(7);
      await card(page, 'visual_logo-101-0002').locator('[data-review="platform"]').click(); await ok(8);
      await card(page, 'text-101-0003').locator('[data-review="clear"]').click(); await ok(9);
      await page.locator('#review-dialog .rv-undo').click(); await ok(10);
      await button(page, 'adult-101-0009', 'KEEP').click(); await yes(); await ok(11);
      await card(page, 'violence-101-0005').locator('.rv-name').click(); await page.keyboard.press('4'); await ok(12);
      await button(page, 'text-101-0016', 'BLUR').click(); await ok(13); // R2-K: "Làm mờ" of a region item = classic decide(id, 'BLUR', false)
      await page.selectOption('#review-dialog .rv-more', 'candidates');
      await button(page, 'advisory-101-0001', 'CUT').click(); await ok(14);
      const v2 = bodies(v2start);
      assert.deepEqual(v2, old, 'the same bodies, in the same order');
      assert.ok(posts.slice(v2start).every(p => p.token === F.TOKEN && /^application\/json/.test(p.type)), 'JSON with the token');
      assert.ok(v2.some(b => b.remember_studio_logo === true) && v2.some(b => b.remember_platform_logo === true) && !v2.some(b => 'remember_studio_logo' in b && b.remember_studio_logo !== true));
      process.stdout.write('   ' + v2.length + ' bodies identical: ' + v2.map(b => b.decision || 'clear').join(', ') + '\n');
      await page.keyboard.press('Escape'); await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    });

    for (const theme of ['light', 'dark']) for (const [width, height] of [[1440, 900], [1024, 800], [390, 844]]) {
      await check(`${width} px ${theme}: buttons, .selected, illustration, "Đang lưu… / Đã lưu", S5 confirm (Esc / Enter), keys 1 / → / Z, sticky, no overflow`, async () => {
        fresh();
        server_state.postDelay = 350;
        const p = await newPage(width, height, theme);
        await openJob(p, 101, 'pending');
        let st = await state(p);
        assert.equal(st.focus, 'visual_logo-101-0002', 'the first undecided card is selected');
        assert.equal(await card(p, 'visual_logo-101-0002').locator('.rv-actions [data-review="decide"]').count(), 4);
        assert.equal(await button(p, 'visual_logo-101-0002', 'BLUR').textContent(), 'Làm mờ cả cảnh', 'no region: whole frame');
        assert.equal(await button(p, 'text-101-0016', 'BLUR').textContent(), 'Làm mờ', 'a red region: the region');
        const start = posts.length;
        // Cắt on the selected card: chosen at once, darkened with "Đã chọn cắt", saving, auto-advance, sticky.
        await button(p, 'visual_logo-101-0002', 'CUT').click();
        st = await state(p);
        assert.equal(st.save, 'Đang lưu…');
        const c = card(p, 'visual_logo-101-0002');
        assert.equal(await c.evaluate(el => el.classList.contains('d-cut') && getComputedStyle(el.querySelector('.rv-cut-label')).display !== 'none'), true, 'cut illustration');
        assert.equal(await button(p, 'visual_logo-101-0002', 'CUT').evaluate(b => b.classList.contains('selected') && b.getAttribute('aria-pressed') === 'true'), true);
        assert.equal(await c.locator('.rv-status').textContent(), 'Cắt');
        assert.equal(st.focus, 'adult-101-0009', 'auto-advance to the next undecided card');
        await until(async () => (await state(p)).save === 'Đã lưu', 'Đã lưu');
        assert.deepEqual(bodies(start), [{id: 'visual_logo-101-0002', decision: 'CUT', full_frame: false, note: null}]);
        assert.equal(await card(p, 'visual_logo-101-0002').count(), 1, 'sticky: still in "Chưa duyệt"');
        // Make the next card clickable on a phone: it is selected and scrolled into view.
        assert.ok(await card(p, 'adult-101-0009').evaluate(el => { const r = el.getBoundingClientRect(), b = el.closest('.rv-body').getBoundingClientRect(); return r.bottom > b.top && r.top < b.bottom; }), 'scrolled into view');
        // S5: Visual AI disagrees (93 %) with "Giữ": Esc cancels only the confirm; no POST, no close.
        await button(p, 'adult-101-0009', 'KEEP').click();
        await p.waitForSelector('#review-dialog .rv-confirm[open]');
        assert.match(await p.locator('#rv-confirm-text').textContent(), /^Visual AI tin cậy 93% đề xuất “Làm mờ toàn cảnh”/);
        await p.keyboard.press('3'); // shortcuts are off while it is open
        await p.keyboard.press('Escape');
        await p.waitForFunction(() => !document.querySelector('#review-dialog .rv-confirm').open);
        st = await state(p);
        assert.deepEqual([st.open, st.hash], [true, '#review/101/videos'], 'the review dialog stays open');
        await sleep(450);
        assert.equal(posts.length, start + 1, 'cancel and the key while confirming sent nothing');
        // Key 1 on the selected card asks the same confirm; Enter confirms.
        await p.keyboard.press('1');
        await p.waitForSelector('#review-dialog .rv-confirm[open]');
        await p.keyboard.press('Enter');
        await until(() => posts.length === start + 2, 'key 1 + Enter');
        assert.deepEqual(posts[start + 1].body, {id: 'adult-101-0009', decision: 'KEEP', full_frame: false, note: null});
        st = await state(p);
        assert.equal(st.focus, 'text-101-0016');
        // "Làm mờ" on a red region: no whole-frame confirm, the region is blurred on the image.
        await p.keyboard.press('2');
        await until(() => posts.length === start + 3, 'key 2');
        assert.deepEqual(posts[start + 2].body, {id: 'text-101-0016', decision: 'BLUR', full_frame: false, note: null});
        // R4-B3: a white veil, no backdrop-filter (it made Chrome blink on the user's PC).
        assert.equal(await card(p, 'text-101-0016').locator('.rv-region').evaluate(r => { const cs = getComputedStyle(r); return r.classList.contains('blurred') && cs.backdropFilter === 'none' && /^rgba\(255, 255, 255, 0\.[67]/.test(cs.backgroundColor); }), true, 'region veiled');
        // → then ← move the selection (stop at the ends); Z undoes the last choice (BLUR → back to undecided = clear).
        assert.equal((await state(p)).focus, 'text-101-0023');
        await p.keyboard.press('ArrowLeft'); assert.equal((await state(p)).focus, 'text-101-0016');
        await p.keyboard.press('ArrowRight'); await p.keyboard.press('ArrowRight'); assert.equal((await state(p)).focus, 'text-101-0023', 'stops at the end');
        await p.keyboard.press('z');
        await until(() => posts.length === start + 4, 'undo');
        assert.deepEqual(posts[start + 3].body, {id: 'text-101-0016'}, 'undo of a first choice clears it');
        assert.equal((await state(p)).focus, 'text-101-0016', 'undo selects the card it changed');
        // "Làm mờ cả cảnh" with its confirm, then Enter: the whole image is blurred.
        await openJob(p, 101, 'all');
        await button(p, 'gore-101-0004', 'BLUR').click();
        await p.waitForSelector('#review-dialog .rv-confirm[open]');
        assert.equal(await p.locator('#rv-confirm-text').textContent(), 'Bạn có xác nhận làm mờ toàn bộ khung hình trong đoạn này?');
        await p.keyboard.press('Enter');
        await until(() => posts.length === start + 5, 'full frame');
        assert.deepEqual(posts[start + 4].body, {id: 'gore-101-0004', decision: 'BLUR', full_frame: true, note: null});
        assert.equal(await card(p, 'gore-101-0004').evaluate(el => el.classList.contains('d-blur-full') && /blur/.test(getComputedStyle(el.querySelector('.rv-art img')).filter)), true);
        // Layout: no horizontal overflow; touch targets on the phone.
        const layout = await p.evaluate(() => {
          const d = document.getElementById('review-dialog'), body = d.querySelector('.rv-body');
          const gore = d.querySelector('article.rv-card[data-item="gore-101-0004"]');
          const heights = [...gore.querySelectorAll('.rv-actions button'), ...d.querySelectorAll('.rv-tools button')].filter(b => !b.closest('details:not([open])')).map(b => b.getBoundingClientRect().height);
          return {overflow: d.scrollWidth > d.clientWidth + 1 || body.scrollWidth > body.clientWidth + 1 || document.documentElement.scrollWidth > window.innerWidth, min: Math.min(...heights)};
        });
        assert.equal(layout.overflow, false, 'no horizontal overflow');
        if (width === 390) assert.ok(layout.min >= 44, 'buttons ≥ 44 px on a phone: ' + layout.min);
        await p.keyboard.press('Escape'); await p.waitForFunction(() => !document.getElementById('review-dialog').open);
        await p.close();
      });
    }

    await check('Write chain: two quick decisions on two cards are two POSTs in order, the second after the first answered; polling waits; no /api/status', async () => {
      fresh(); server_state.postDelay = 1800; // two writes span more than one 3 s poll tick
      await openJob(page, 101, 'all');
      const start = posts.length, status = counters.status;
      await button(page, 'text-101-0023', 'CUT').click();
      await button(page, 'text-101-0016', 'KEEP').click();
      await until(() => posts.length === start + 2, 'two POSTs', 6000);
      assert.deepEqual(bodies(start).map(b => b.id), ['text-101-0023', 'text-101-0016']);
      assert.ok(posts[start + 1].at - posts[start].at >= 1750, 'serial: ' + (posts[start + 1].at - posts[start].at) + ' ms');
      // While saving, polling neither runs nor puts the old state back on the clicked card.
      assert.equal(await card(page, 'text-101-0016').locator('.rv-status').textContent(), 'Giữ');
      await until(async () => (await state(page)).save === 'Đã lưu', 'saved', 8000);
      const busy = [posts[start].at, posts[start + 1].at + server_state.postDelay];
      assert.deepEqual(counters.queueAt.filter(t => t > busy[0] && t < busy[1]), [], 'no queue poll while writes were pending');
      assert.equal(counters.status, status, 'no /api/status after a decision');
      assert.deepEqual([queueFor(101).items.find(x => x.id === 'text-101-0023').decision, queueFor(101).items.find(x => x.id === 'text-101-0016').decision], ['CUT', 'KEEP']);
      const after = counters.queue;
      await sleep(3300);
      assert.ok(counters.queue > after, 'polling resumes after the writes');
      // A confirm still open when an earlier write returns its queue: the confirmed choice is still sent.
      server_state.postDelay = 500;
      const mark = posts.length;
      await button(page, 'text-101-0006', 'CUT').click();
      await button(page, 'gore-101-0004', 'BLUR').click();
      await page.waitForSelector('#review-dialog .rv-confirm[open]');
      await until(async () => (await state(page)).save === 'Đã lưu', 'the first write applied while confirming', 6000);
      await page.keyboard.press('Enter');
      await until(() => posts.length === mark + 2, 'the confirmed choice is sent', 6000);
      assert.deepEqual(posts[mark + 1].body, {id: 'gore-101-0004', decision: 'BLUR', full_frame: true, note: null});
    });

    await check('Retries: a network error or ≥ 500 is sent again after 300 ms then 900 ms; 400 is never resent and shows the server text', async () => {
      fresh();
      await openJob(page, 101, 'all');
      let start = posts.length;
      server_state.failNext = [{status: 500, body: {error: 'bận'}}, 'drop'];
      await button(page, 'text-101-0023', 'CUT').click();
      await until(() => posts.length === start + 3, 'three attempts', 6000);
      const gaps = [posts[start + 1].at - posts[start].at, posts[start + 2].at - posts[start + 1].at];
      assert.ok(gaps[0] >= 280 && gaps[0] < 800 && gaps[1] >= 870 && gaps[1] < 1600, 'gaps ' + gaps);
      await until(async () => (await state(page)).save === 'Đã lưu', 'saved after retries');
      assert.equal(queueFor(101).items.find(x => x.id === 'text-101-0023').decision, 'CUT');
      start = posts.length;
      server_state.failNext = [{status: 400, body: {error: 'Chỉ có thể ghi nhớ logo hãng phim khi chọn Giữ nguyên'}}];
      await button(page, 'text-101-0016', 'CUT').click();
      await until(async () => (await state(page)).toast !== '', 'toast');
      await sleep(1500);
      assert.equal(posts.length, start + 1, '400 is not resent');
      const toast = (await state(page)).toast;
      assert.ok(toast.startsWith('Chưa lưu được lựa chọn “Cắt cả cảnh” cho mục Logo 0:31–0:37.') && toast.endsWith('Chi tiết: Chỉ có thể ghi nhớ logo hãng phim khi chọn Giữ nguyên'), toast);
      await until(async () => (await card(page, 'text-101-0016').locator('.rv-status').textContent()) === 'Chưa duyệt', 'resync puts the saved state back');
      assert.equal((await state(page)).focus, 'text-101-0016', 'the failed card is selected again');
    });

    await check('Last failure after the retries: the queue reloads and the card reopens, switching to "Tất cả" when the filter hides it; 403 → new session, one resend', async () => {
      fresh(); server_state.postDelay = 900;
      await openJob(page, 101, 'pending');
      const start = posts.length;
      server_state.failNext = [1, 2, 3].map(() => ({status: 503, body: {error: 'WinError 32: E:\\x'}}));
      await button(page, 'text-101-0023', 'CUT').click();
      await page.locator('#review-dialog .rv-chips [data-filter="adult"]').click(); // the user moves on meanwhile
      await until(async () => (await state(page)).toast !== '', 'final error', 12000);
      const st = await state(page);
      assert.match(st.toast, /^Chưa lưu được lựa chọn “Cắt cả cảnh” cho mục Chữ trong phim .* \(đã thử 3 lần\)\. .*Chi tiết: máy chủ chưa ghi được file hàng đợi \(file đang bị đọc hoặc khóa\)$/);
      await until(async () => (await state(page)).filter === 'all' && (await state(page)).focus === 'text-101-0023', 'reopened in Tất cả');
      assert.equal(posts.length, start + 3);
      assert.equal(await card(page, 'text-101-0023').locator('.rv-status').textContent(), 'Chưa duyệt');
      // 403: the Control Center restarted (new token): GET /api/session, then the same body once more.
      server_state.postDelay = 0; server_state.token = 'browser-token-2';
      const before = posts.length;
      await button(page, 'text-101-0023', 'KEEP').click();
      await until(async () => (await state(page)).save === 'Đã lưu', 'saved after 403');
      assert.deepEqual(posts.slice(before).map(p => [p.token, p.body.id]), [[F.TOKEN, 'text-101-0023'], ['browser-token-2', 'text-101-0023']]);
      server_state.token = F.TOKEN;
    });

    await check('Closing while saving: the write still runs; its error shows in the dashboard toast; reopening waits for it and shows the server queue', async () => {
      fresh(); server_state.postDelay = 1200;
      await openJob(page, 101, 'all');
      server_state.failNext = [{status: 400, body: {error: 'Lỗi giả: hàng đợi đã đổi'}}];
      await button(page, 'text-101-0023', 'CUT').click();
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('review-dialog').open && location.hash === '#videos');
      await page.waitForFunction(() => { const t = document.getElementById('toast'); return !t.hidden && /Lỗi giả: hàng đợi đã đổi/.test(t.textContent) && t.classList.contains('error'); }, null, {timeout: 6000});
      // Success after closing (× this time), reopened before it finished: the dialog waits, then shows it saved.
      const start = posts.length;
      await openJob(page, 101, 'all');
      await button(page, 'text-101-0016', 'CUT').click();
      await page.locator('#review-dialog .rv-head [data-review="close"]').click();
      await page.waitForFunction(() => !document.getElementById('review-dialog').open);
      await page.goto(base + '/dashboard-v2/#review/101/videos');
      await page.waitForSelector('#review-dialog[open] .rv-message');
      assert.match(await page.locator('#review-dialog .rv-message').textContent(), /Đang chờ lưu xong|Đang tải/);
      await page.waitForSelector('#review-dialog[open] article.rv-card', {timeout: 6000});
      await page.locator('#review-dialog .rv-chips [data-filter="all"]').click();
      assert.equal(await card(page, 'text-101-0016').locator('.rv-status').textContent(), 'Cắt', 'the server queue, after the write');
      assert.equal(posts.length, start + 1);
      // Opening another job while a write fails: the dialog toast names the job.
      server_state.failNext = [{status: 400, body: {error: 'Lỗi giả khác'}}];
      await button(page, 'text-101-0023', 'KEEP').click();
      await page.evaluate(() => { location.hash = '#review/103/videos'; });
      await page.waitForFunction(() => /^#101 · Chưa lưu được/.test(document.querySelector('#review-dialog .rv-toast').textContent), null, {timeout: 6000});
      await page.keyboard.press('Escape'); await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    });

    await check('Offline: decision buttons and undo are disabled with a notice; they come back when the queue answers again', async () => {
      fresh();
      await openJob(page, 101, 'all');
      await page.route('**/api/jobs/101/review/queue', route => route.abort());
      await page.waitForFunction(() => !document.querySelector('#review-dialog .rv-offline').hidden, null, {timeout: 8000});
      assert.equal(await page.locator('#review-dialog .rv-actions [data-review="decide"]:not([disabled])').count(), 0, 'every decision button is disabled');
      const start = posts.length;
      await page.keyboard.press('1');
      await until(async () => (await state(page)).toast.startsWith('Mất kết nối'), 'offline message');
      assert.equal(posts.length, start, 'nothing sent while offline');
      await page.unroute('**/api/jobs/101/review/queue');
      await page.waitForFunction(() => document.querySelector('#review-dialog .rv-offline').hidden, null, {timeout: 8000});
      assert.ok(await page.locator('#review-dialog .rv-actions [data-review="decide"]:not([disabled])').count() > 0);
      await page.keyboard.press('Escape'); await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    });

    await check('Locks: export queued/running (S6), cleaned, archived, skipped (S8) → "Chỉ xem" and no write; a scan in the queue does not lock (S6)', async () => {
      const j103 = jobs.find(j => j.id === 103), saved = {state: j103.state, queue_kind: j103.queue_kind};
      for (const [id, reason] of [[114, /^Chỉ xem · Video đang chờ xuất hoặc đang xuất/], [113, /^Chỉ xem · Video gốc đã được dọn vào Thùng rác/], [110, /^Chỉ xem · Video gốc đang ở kho lưu trữ/], [109, /^Chỉ xem · Video đã được đánh dấu bỏ qua/]]) {
        await openJob(page, id, 'all');
        assert.match(await page.locator('#review-dialog .rv-lock').textContent(), reason, 'job ' + id);
        assert.equal(await page.locator('#review-dialog .rv-actions button:not([disabled])').count(), 0, 'job ' + id + ': every button disabled');
        const start = posts.length;
        await page.keyboard.press('3');
        await until(async () => reason.test('Chỉ xem · ' + (await state(page)).toast), 'lock reason toast ' + id);
        assert.equal(posts.length, start, 'no POST for job ' + id);
      }
      Object.assign(j103, {state: 'QUEUED', queue_kind: 'scan'});
      await openJob(page, 103, 'all');
      assert.equal(await page.locator('#review-dialog .rv-lock').count(), 0, 'S6: waiting for a scan is not an export');
      assert.ok(await page.locator('#review-dialog .rv-actions [data-review="decide"]:not([disabled])').count() > 0);
      Object.assign(j103, {queue_kind: 'export'});
      await openJob(page, 103, 'all');
      assert.match(await page.locator('#review-dialog .rv-lock').textContent(), /đang chờ xuất/);
      Object.assign(j103, saved);
      await page.keyboard.press('Escape'); await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    });

    await check('Undo, advisory, "Tự chuyển cảnh" (saved as biliflow.review.autoNext), memory buttons, S9 in "Chi tiết kỹ thuật"', async () => {
      fresh();
      await page.goto(base + '/dashboard-v2/#overview');
      await page.evaluate(() => localStorage.removeItem('biliflow.review.autoNext'));
      await openJob(page, 101, 'pending');
      // "Tự chuyển cảnh" off: the selection stays; saved for the next page (and the classic page).
      await page.locator('#review-dialog [data-review="auto"]').uncheck();
      assert.equal(await page.evaluate(() => localStorage.getItem('biliflow.review.autoNext')), '0');
      let start = posts.length;
      await button(page, 'visual_logo-101-0002', 'NEEDS_MORE_CONTEXT').click();
      await until(() => posts.length === start + 1, 'NMC');
      assert.equal((await state(page)).focus, 'visual_logo-101-0002', 'no auto-advance');
      const undo = page.locator('#review-dialog .rv-undo');
      assert.match(await undo.getAttribute('title'), /^Hoàn tác lựa chọn cho Logo toàn khung \(chưa khoanh vùng\) 0:04–0:/);
      await page.reload(); await page.waitForSelector('#review-dialog[open] article.rv-card');
      assert.equal(await page.locator('#review-dialog [data-review="auto"]').isChecked(), false, 'still off after a reload');
      await page.locator('#review-dialog [data-review="auto"]').check();
      await page.locator('#review-dialog .rv-chips [data-filter="all"]').click();
      // Studio memory: the button becomes "✓ Đã nhớ…"; pressing it again sends nothing.
      start = posts.length;
      const studio = card(page, 'visual_logo-101-0002').locator('[data-review="studio"]');
      await studio.click();
      await until(() => posts.length === start + 1, 'studio');
      assert.deepEqual(posts[start].body, {id: 'visual_logo-101-0002', decision: 'KEEP', full_frame: false, note: null, remember_studio_logo: true});
      await until(async () => (await card(page, 'visual_logo-101-0002').locator('[data-review="studio"]').textContent()) === '✓ Đã nhớ là logo hãng phim (giữ nguyên)', 'remembered');
      await card(page, 'visual_logo-101-0002').locator('[data-review="studio"]').click();
      await sleep(400);
      assert.equal(posts.length, start + 1, 'already remembered: skipped');
      // Undo: the studio choice goes back to NEEDS_MORE_CONTEXT (as saved before the reload); a first choice is cleared.
      await page.keyboard.press('z'); await until(() => posts.length === start + 2, 'undo 1');
      assert.deepEqual(posts[start + 1].body, {id: 'visual_logo-101-0002', decision: 'NEEDS_MORE_CONTEXT', full_frame: false, note: null});
      await button(page, 'text-101-0023', 'CUT').click(); await until(() => posts.length === start + 3, 'cut');
      await page.keyboard.press('z'); await until(() => posts.length === start + 4, 'undo 2');
      assert.deepEqual(posts[start + 3].body, {id: 'text-101-0023'});
      assert.equal(await undo.isDisabled(), true, 'nothing left to undo');
      start += 1;
      // An advisory card decided for the first time cannot be undone: the classic message, nothing sent.
      await page.selectOption('#review-dialog .rv-more', 'candidates');
      await button(page, 'advisory-101-0000', 'KEEP').click();
      await until(() => posts.length === start + 4, 'advisory');
      await page.keyboard.press('z');
      await until(async () => (await state(page)).toast.startsWith('Không hoàn tác được lựa chọn cho ứng viên phụ'), 'advisory undo message');
      await sleep(300);
      assert.equal(posts.length, start + 4);
      // S9 in "Chi tiết kỹ thuật" of a safety card: the borrowed box has no region buttons (the R2 path to the classic
      // "chưa có vùng" error went with them; that error stays checked against the classic page in verify-review).
      fresh();
      const q = queueFor(101), owner = q.items.find(x => x.id === 'visual_logo-101-0011'), other = q.items.find(x => x.id === 'adult-101-0010');
      owner.decision = 'BLUR'; owner.decision_region_source_pixels = owner.suggested_region_source_pixels; owner.suggested_region_source_pixels = null;
      owner.start_seconds = other.start_seconds - 1; owner.end_seconds = other.end_seconds + 1;
      await openJob(page, 101, 'all');
      const tech = card(page, 'adult-101-0010').locator('.rv-tech');
      await tech.locator('summary').click();
      assert.equal(await tech.locator('[data-review="region"]').count(), 0, 'no region buttons in the technical details');
      assert.match(await tech.locator('.rv-region-block.borrowed small').textContent(), /^Khung đỏ là vùng logo của thẻ “Logo toàn khung \(chưa khoanh vùng\)” \(\d\d:\d\d\.\d–\d\d:\d\d\.\d\) — đang: Làm mờ logo$/);
      start = posts.length;
      await tech.locator('[data-review="goto"]').click();
      await until(async () => (await state(page)).focus === owner.id, 'owner selected');
      await sleep(300);
      assert.equal(posts.length, start, 'navigation only, no POST');
      await page.keyboard.press('Escape'); await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    });

    await check('R2-B1: a write lost while offline, its reload lost too: once the server answers, the next poll puts the card and the counts back', async () => {
      fresh();
      await openJob(page, 101, 'pending');
      const before = await page.locator('#review-dialog .rv-progress-text').textContent();
      assert.equal(before, '5 / 30 cảnh cần quyết định cuối');
      // The server stops answering: the write (and its 300/900 ms retries) and the reload of the queue fail.
      await page.route('**/api/jobs/101/review/**', route => route.abort());
      await page.keyboard.press('3'); // CUT on visual_logo-101-0002, applied at once
      assert.equal(await page.locator('#review-dialog .rv-progress-text').textContent(), '4 / 30 cảnh cần quyết định cuối', 'optimistic');
      // The write gives up after its retries (its message is then replaced by the failed reload's), offline shows.
      await page.waitForFunction(() => !document.querySelector('#review-dialog .rv-offline').hidden, null, {timeout: 8000});
      await until(async () => (await state(page)).save !== 'Đang lưu…', 'write settled', 8000);
      assert.equal(await card(page, 'visual_logo-101-0002').locator('.rv-status').textContent(), 'Cắt', 'still the unsaved choice while offline');
      // The server answers again with the same queue version: the next poll must still apply it.
      await page.unroute('**/api/jobs/101/review/**');
      await page.waitForFunction(() => document.querySelector('#review-dialog .rv-offline').hidden, null, {timeout: 8000});
      await until(async () => (await card(page, 'visual_logo-101-0002').locator('.rv-status').textContent()) === 'Chưa duyệt', 'card back to the saved state', 8000);
      assert.equal(await page.locator('#review-dialog .rv-progress-text').textContent(), before, 'counts back to the server queue');
      assert.equal(queueFor(101).items.find(x => x.id === 'visual_logo-101-0002').decision, null, 'nothing was written');
      await page.keyboard.press('Escape'); await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    });

    await check('R2-B2 (S9): "Kiểm tra đoạn kết" beside the platform logo card it borrows the red box from: no region buttons there, a dashed box, "Đi tới thẻ logo"', async () => {
      fresh();
      const q = queueFor(101), logo = q.items.find(x => x.id === 'visual_logo-101-0007'), end = q.items.find(x => x.id === 'visual_logo-101-0008');
      logo.decision = 'BLUR'; logo.decision_region_source_pixels = logo.suggested_region_source_pixels;
      Object.assign(end, {candidate_type: 'ending_boundary', decision: null, start_seconds: logo.start_seconds + 0.5, end_seconds: logo.end_seconds + 1});
      for (const [width, theme] of [[1440, 'light'], [390, 'dark']]) {
        const p = width === 1440 ? page : await newPage(390, 844, 'dark');
        await openJob(p, 101, 'all');
        const cards = await p.locator('#review-dialog article.rv-card').evaluateAll(list => list.map(el => el.dataset.item));
        assert.equal(cards.indexOf(end.id), cards.indexOf(logo.id) + 1, 'side by side');
        const e = card(p, end.id), l = card(p, logo.id);
        assert.equal(await e.locator('[data-review="region"]').count(), 0, 'no region buttons on the borrowing card');
        assert.equal(await l.locator('[data-review="region"]').count(), 2, 'the owner card keeps them');
        assert.match(await e.locator('.rv-region-block.borrowed small').textContent(), /^Khung đỏ là vùng logo của thẻ “Logo nền tảng Nền tảng mẫu” \(\d\d:\d\d\.\d–\d\d:\d\d\.\d\) — đang: Làm mờ logo$/);
        const box = await e.locator('.rv-region').evaluate(r => ({style: getComputedStyle(r).borderTopStyle, label: r.querySelector('em') && r.querySelector('em').textContent}));
        assert.equal(box.style, 'dashed'); assert.match(box.label, /^áp dụng \d\d:\d\d\.\d–\d\d:\d\d\.\d$/);
        assert.equal(await l.locator('.rv-region').evaluate(r => getComputedStyle(r).borderTopStyle), 'solid');
        // The owner card's buttons change only the owner card; the borrowing card follows with its line only.
        const start = posts.length;
        await l.locator('[data-review="region"][data-decision="KEEP"]').click();
        await until(() => posts.length === start + 1, 'owner KEEP');
        assert.deepEqual(posts[start].body, {id: logo.id, decision: 'KEEP', full_frame: false, note: 'Đã xác nhận vùng khoanh đỏ là tiêu đề hoặc nội dung hợp lệ của phim'});
        await until(async () => (await e.locator('.rv-region').count()) === 0 && (await e.locator('.rv-region-block').count()) === 0, 'no longer borrowed: box and line go');
        await l.locator('[data-review="region"][data-decision="BLUR"]').click();
        await until(() => posts.length === start + 2, 'owner BLUR');
        await until(async () => (await e.locator('.rv-region.borrowed').count()) === 1, 'borrowed again');
        // "Đi tới thẻ logo" from "Chưa duyệt", where the decided logo card is hidden: switches to "Tất cả" and selects it.
        await p.locator('#review-dialog .rv-chips [data-filter="pending"]').click();
        await card(p, end.id).locator('[data-review="goto"]').click();
        await until(async () => (await state(p)).filter === 'all' && (await state(p)).focus === logo.id, 'owner card selected in Tất cả');
        assert.ok(await card(p, logo.id).evaluate(el => { const r = el.getBoundingClientRect(), b = el.closest('.rv-body').getBoundingClientRect(); return r.bottom > b.top && r.top < b.bottom; }), 'scrolled into view');
        if (p !== page) await p.close();
        else { await p.keyboard.press('Escape'); await p.waitForFunction(() => !document.getElementById('review-dialog').open); }
      }
    });

    await check('Demo page: decisions change the synthetic queue in memory and show in "Lịch sử thao tác mẫu"; no request', async () => {
      await page.goto('about:blank'); // the live page would keep polling /api/status
      const demo = await newPage(1440, 900, 'dark', 'demo'), api = [];
      demo.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/') || r.method() !== 'GET') api.push(r.method() + ' ' + r.url()); });
      await demo.goto(base + '/demo/#review/101/videos');
      await demo.waitForSelector('#review-dialog[open] article.rv-card');
      assert.equal(await demo.locator('.rv-progress-text').textContent(), '5 / 30 cảnh cần quyết định cuối');
      await button(demo, 'visual_logo-101-0002', 'CUT').click();
      await demo.waitForFunction(() => document.querySelector('#review-dialog .rv-save').textContent === 'Đã lưu');
      assert.equal(await demo.locator('.rv-progress-text').textContent(), '4 / 30 cảnh cần quyết định cuối');
      await demo.keyboard.press('z');
      await demo.waitForFunction(() => document.querySelector('#review-dialog .rv-progress-text').textContent === '5 / 30 cảnh cần quyết định cuối');
      await button(demo, 'text-101-0016', 'CUT').click();
      await demo.waitForFunction(() => document.querySelector('#review-dialog .rv-progress-text').textContent === '4 / 30 cảnh cần quyết định cuối');
      await demo.keyboard.press('Escape'); await demo.waitForFunction(() => !document.getElementById('review-dialog').open);
      await demo.goto(base + '/demo/#settings'); await demo.waitForSelector('.log-list');
      const log = await demo.locator('.log-list').textContent();
      assert.match(log, /POST \/api\/jobs\/101\/review\/decision/); assert.match(log, /POST \/api\/jobs\/101\/review\/clear/);
      assert.deepEqual(api, [], 'no API request and no POST from the demo');
      await demo.close();
    });

    await check('No blob:, no console or CSP error, no request outside the origin', async () => {
      assert.deepEqual(problems, []);
    });
    await page.close();
  } finally {
    await browser.close();
    server.close();
  }
  process.stdout.write(JSON.stringify({passed, failed: 0}) + '\n');
})().catch(error => { console.error(error); process.exitCode = 1; server.close(); });
