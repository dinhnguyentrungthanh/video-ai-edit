/* Review dialog browser check (R0/R1): Chromium (Playwright) on live.html with a fake Control Center API
 * on 127.0.0.1 (review-fake-server.cjs: synthetic queues from mock-data.js; no real backend, database or network).
 * At 1440, 1024 and 390 px, light and dark: the dialog, its address, Back/Esc/Đóng, 2 / 1 columns and
 * full screen, the old-page link, at most 2 images at once, polling that only patches, the paused
 * dashboard polling, 500 synthetic items, and no POST in these viewing flows (R2 writes have their own
 * check, browser-check-review-write.cjs), no blob:, no console or CSP error.
 * Run: node dashboard_v2/browser-check-review.cjs
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
if (!pw) { process.stdout.write('SKIP browser-check-review: Playwright is not installed\n'); process.exit(0); }

// The fake Control Center (shared with browser-check-review-write.cjs): synthetic queues, frames, a test clip.
const {server, Mock, jobs, queues, queueFor, counters, server_state, posts, requests, VIDEO} = require('./review-fake-server.cjs').create();

let passed = 0;
async function check(name, fn) { await fn(); passed++; process.stdout.write('OK ' + name + '\n'); }

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = 'http://127.0.0.1:' + server.address().port;
  if (!VIDEO) process.stdout.write('SKIP video playback checks: ffmpeg could not make the test clip\n');
  const browser = await pw.chromium.launch({});
  const problems = [];
  // Job 102 is scanning (no queue yet: the "quét lại" message); 403 on frame/video (old media key) and 415 are tested on purpose.
  const EXPECTED_FAILURES = [/\/api\/jobs\/102\/review\/queue$/, /\/review\/(frame|video)\?/];
  const newPage = async (width, height, theme) => {
    const page = await browser.newPage({viewport: {width, height}});
    await page.addInitScript(t => { try { localStorage.setItem('biliflow-v2-theme', t); } catch (_) { /* ignore */ } }, theme);
    page.on('pageerror', e => problems.push(width + ' ' + theme + ' pageerror: ' + e.message));
    // A failed response is checked by URL below (the console line has none); everything else is an error.
    page.on('console', m => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) problems.push(width + ' ' + theme + ' console: ' + m.text()); });
    page.on('response', r => { if (r.status() >= 400 && !EXPECTED_FAILURES.some(re => re.test(r.url()))) problems.push(width + ' ' + theme + ' HTTP ' + r.status() + ' ' + r.url()); });
    page.on('request', r => { const u = r.url(); if (u.startsWith('blob:') || !(u.startsWith(base) || u.startsWith('data:'))) problems.push('request ' + u); });
    return page;
  };
  const openFromDrawer = async (page, id) => { // the job's "Duyệt cảnh" button in the detail drawer
    await page.fill('#search', String(id));
    await page.locator('[data-action="detail"][data-id="' + id + '"]').first().click();
    await page.waitForSelector('.drawer');
    await page.locator('.drawer [data-op="review"]').first().click(); // R4.3: "Duyệt cảnh" opens the dialog
    await page.waitForSelector('#review-dialog[open] article.rv-card');
  };
  const dialogState = page => page.evaluate(() => {
    const d = document.getElementById('review-dialog'), r = d.getBoundingClientRect(), body = d.querySelector('.rv-body');
    return {open: d.open, hash: location.hash, w: Math.round(r.width), h: Math.round(r.height), cols: body ? getComputedStyle(d.querySelector('.rv-cards')).gridTemplateColumns.split(' ').length : 0,
      overflow: d.scrollWidth > d.clientWidth + 1 || (body && body.scrollWidth > body.clientWidth + 1) || document.documentElement.scrollWidth > window.innerWidth,
      theme: document.documentElement.dataset.theme, old: (d.querySelector('a.rv-old') || {}).getAttribute ? d.querySelector('a.rv-old').getAttribute('href') : null};
  });
  const card = (page, id) => page.locator(`article.rv-card[data-item="${id}"]`);
  const videoState = page => page.evaluate(() => {
    const v = document.querySelector('#review-dialog video'), host = v && v.closest('article.rv-card');
    return v ? {count: document.querySelectorAll('video').length, paused: v.paused, t: v.currentTime, host: host ? host.dataset.item : null, mark: v.dataset.mark || '', src: v.getAttribute('src') || ''}
      : {count: document.querySelectorAll('video').length};
  });
  const showAll = async page => { await page.locator('.rv-chips [data-filter="all"]').click(); await page.waitForSelector('article.rv-card[data-item="gore-101-0004"]'); };
  const waitVideo = (page, fn, arg, timeout) => page.waitForFunction(fn, arg, {timeout: timeout || 10000});
  // gore-101-0004 plays 8.7–20.7 s; adult-101-0000 is a scene of 3 moments; visual_logo-101-0001 has a red region;
  // visual_logo-101-0002 has 2 AI boxes (one covered by another card).
  const GORE = {id: 'gore-101-0004', start: 8.7, end: 20.7};
  try {
    for (const theme of ['light', 'dark']) for (const [width, height, cols] of [[1440, 900, 2], [1024, 800, 1], [390, 844, 1]]) {
      await check(`${width} px ${theme}: dialog, address, ${cols} column(s), Esc / Đóng / Back return to #videos`, async () => {
        const page = await newPage(width, height, theme), before = posts.length;
        await page.goto(base + '/dashboard-v2/#videos'); await page.waitForSelector('#search');
        await openFromDrawer(page, 101);
        let st = await dialogState(page);
        assert.equal(st.hash, '#review/101/videos'); assert.equal(st.theme, theme);
        assert.equal(st.cols, cols, 'columns'); assert.equal(st.overflow, false, 'no horizontal overflow');
        assert.equal(st.old, '/review/101?from=v2&view=videos', 'link to the old page with M4');
        if (width === 390) assert.deepEqual([st.w, st.h], [390, 844], 'full screen on a phone');
        else assert.ok(st.w < width && st.w >= Math.min(1280, width * 0.9), 'wide dialog: ' + st.w);
        assert.match(await page.locator('#review-title').textContent(), /^Duyệt cảnh · #101 · /);
        assert.equal(await page.locator('.rv-progress-text').textContent(), '5 / 30 cảnh cần quyết định cuối');
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.getElementById('review-dialog').open);
        st = await dialogState(page); assert.deepEqual([st.open, st.hash], [false, '#videos'], 'Esc');
        await openFromDrawer(page, 101);
        await page.locator('.rv-foot [data-review="close"]').click();
        await page.waitForFunction(() => !document.getElementById('review-dialog').open);
        assert.equal((await dialogState(page)).hash, '#videos', 'Đóng');
        await openFromDrawer(page, 101);
        await page.goBack();
        await page.waitForFunction(() => !document.getElementById('review-dialog').open);
        assert.equal((await dialogState(page)).hash, '#videos', 'Back');
        assert.equal(posts.length, before, 'no POST');
        await page.close();
      });
    }

    for (const theme of ['light', 'dark']) for (const [width, height, cols] of [[1440, 900, 2], [1024, 800, 1], [390, 844, 1]]) {
      await check(`${width} px ${theme}: ▶ plays the range in the one shared <video>, the timeline seeks, ⤢ zooms, Esc unzooms then closes and releases the video`, async () => {
        const page = await newPage(width, height, theme), before = posts.length;
        await page.goto(base + '/dashboard-v2/#videos'); await page.waitForSelector('#search');
        await openFromDrawer(page, 101); await showAll(page);
        const gore = card(page, GORE.id);
        await gore.scrollIntoViewIfNeeded();
        if (VIDEO) {
          await gore.locator('[data-review="play"]').click();
          await waitVideo(page, s => { const v = document.querySelector('#review-dialog video'); return v && !v.paused && v.currentTime > s; }, GORE.start);
          const v = await videoState(page);
          assert.deepEqual([v.count, v.host], [1, GORE.id], 'one <video>, in the card that plays');
          await page.waitForFunction(id => document.querySelectorAll(`article.rv-card[data-item="${id}"] .rv-thumb:not(.ghost)`).length > 0, GORE.id);
          const thumbs = await gore.locator('.rv-thumb').evaluateAll(list => list.map(b => b.className));
          assert.ok(thumbs.length > 0 && thumbs.length <= 8 && /peak/.test(thumbs[0]), thumbs.join('|'));
          const box = await gore.locator('.rv-timeline').boundingBox();
          await gore.locator('.rv-timeline').click({position: {x: box.width * 0.5, y: box.height / 2}});
          await waitVideo(page, t => { const v = document.querySelector('#review-dialog video'); return v.paused && Math.abs(v.currentTime - t) < 0.6; }, GORE.start + 0.5 * (GORE.end - GORE.start));
          assert.match(await gore.locator('.rv-time').textContent(), /^0:14 \/ đoạn 0:08–0:20$/);
        }
        await gore.locator('[data-review="zoom"]').click();
        await page.waitForFunction(id => document.querySelector(`article.rv-card[data-item="${id}"]`).classList.contains('zoom'), GORE.id);
        const sizes = await page.evaluate(id => { const c = document.querySelector(`article.rv-card[data-item="${id}"]`).getBoundingClientRect(); return [Math.round(c.width), Math.round(document.querySelector('.rv-cards').getBoundingClientRect().width)]; }, GORE.id);
        assert.ok(Math.abs(sizes[0] - sizes[1]) <= 2 || cols === 1, 'the zoomed card spans the dialog: ' + sizes);
        assert.equal((await dialogState(page)).overflow, false, 'no horizontal overflow while zoomed');
        await page.keyboard.press('Escape');
        await page.waitForFunction(id => !document.querySelector(`article.rv-card[data-item="${id}"]`).classList.contains('zoom'), GORE.id);
        assert.equal(await page.evaluate(() => document.getElementById('review-dialog').open), true, 'the first Esc only unzooms');
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.getElementById('review-dialog').open);
        assert.equal((await dialogState(page)).hash, '#videos');
        assert.equal((await videoState(page)).count, 0, 'the video is released and removed');
        assert.equal(posts.length, before, 'no POST');
        await page.close();
      });
    }

    const page = await newPage(1440, 900, 'light');
    await check('Direct address opens the view then the dialog; closing replaces the hash; a bad hash goes to #overview', async () => {
      await page.goto(base + '/dashboard-v2/#review/101/queue');
      await page.waitForSelector('#review-dialog[open] article.rv-card');
      assert.match(await page.locator('#main h1').first().textContent(), /Hàng đợi/);
      await page.locator('.rv-head [data-review="close"]').click();
      await page.waitForFunction(() => location.hash === '#queue' && !document.getElementById('review-dialog').open);
      await page.goto(base + '/dashboard-v2/#review/abc');
      await page.waitForFunction(() => location.hash === '#overview');
      assert.equal(await page.evaluate(() => document.getElementById('review-dialog').open), false);
    });
    await check('R4.3: "Duyệt cảnh" only for a job that can be reviewed; the temporary "Duyệt (bản mới, thử)" button is gone', async () => {
      await page.goto(base + '/dashboard-v2/#videos'); await page.waitForSelector('#search');
      for (const [id, shown] of [[101, true], [102, false], [106, false]]) {
        await page.fill('#search', String(id));
        await page.locator('[data-action="detail"][data-id="' + id + '"]').first().click();
        await page.waitForSelector('.drawer');
        assert.equal(await page.locator('.drawer [data-action="review-v2"]').count(), 0, 'job ' + id + ': no temporary button');
        assert.equal(await page.locator('.drawer [data-op="review"]').count(), shown ? 1 : 0, 'job ' + id);
        await page.keyboard.press('Escape');
      }
    });
    await check('Dashboard /api/status polling pauses while the dialog is open and refreshes at once on close', async () => {
      await openFromDrawer(page, 101);
      await page.waitForTimeout(300);
      const start = counters.status, queueStart = counters.queue;
      await page.waitForTimeout(6600);
      assert.equal(counters.status, start, 'no /api/status while open');
      assert.ok(counters.queue - queueStart >= 2, 'the dialog polls its queue: ' + (counters.queue - queueStart));
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('review-dialog').open);
      await page.waitForTimeout(400);
      assert.equal(counters.status, start + 1, 'one refresh right after closing');
    });
    await check('Polling: a version change patches the same card nodes; an identity change rebuilds', async () => {
      await openFromDrawer(page, 101);
      await page.locator('.rv-chips [data-filter="all"]').click();
      const q = queueFor(101), first = q.items.slice().sort((a, b) => a.start_seconds - b.start_seconds)[0];
      await page.evaluate(() => { document.querySelector('article.rv-card').dataset.mark = 'kept'; });
      const before = await page.locator('article.rv-card').first().locator('.rv-status').textContent();
      first.decision = first.decision === 'CUT' ? 'KEEP' : 'CUT'; q.updated_at = '2026-10-03T12:00:00Z';
      await page.waitForFunction(([id, prev]) => { const el = document.querySelector('article.rv-card'); return el && el.dataset.item === id && el.querySelector('.rv-status').textContent !== prev; }, [first.id, before], {timeout: 8000});
      assert.equal(await page.locator('article.rv-card[data-mark="kept"]').count(), 1, 'same node after a version change');
      assert.notEqual(await page.locator('article.rv-card').first().locator('.rv-status').textContent(), before);
      q.created_at = '2026-10-03T13:00:00Z';
      await page.waitForFunction(() => !document.querySelector('article.rv-card[data-mark="kept"]'), null, {timeout: 8000});
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    });
    await check('Chips, "Lọc khác", red region box, scope warning, S8 read-only and rescan message', async () => {
      await openFromDrawer(page, 101);
      await page.locator('.rv-chips [data-filter="adult"]').click();
      const metas = await page.locator('article.rv-card .rv-meta').allTextContents();
      assert.ok(metas.length && metas.every(m => m.includes('18+')), metas.join('|'));
      await page.selectOption('.rv-more', 'candidates');
      assert.equal(await page.locator('article.rv-card').count(), 3);
      assert.match(await page.locator('article.rv-card .rv-meta').first().textContent(), /Ứng viên kiểm tra thêm/);
      await page.locator('.rv-chips [data-filter="all"]').click();
      const box = await page.locator('article.rv-card .rv-region').first().getAttribute('style');
      assert.match(box, /left:[\d.]+%;top:[\d.]+%;width:[\d.]+%;height:[\d.]+%/);
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('review-dialog').open);
      jobs.find(j => j.id === 103).detector_groups = ['advertising', 'adult'];
      await page.goto(base + '/dashboard-v2/#review/103/videos'); await page.waitForSelector('#review-dialog[open] .rv-scope');
      assert.equal(await page.locator('.rv-scope').textContent(), 'Không quét trong lượt này: Máu me, Bạo lực. Ít mục hơn không có nghĩa các nhóm này đã an toàn.');
      await page.goto(base + '/dashboard-v2/#review/109/videos'); await page.waitForSelector('#review-dialog[open] .rv-lock');
      assert.match(await page.locator('.rv-lock').textContent(), /^Chỉ xem · Video đã được đánh dấu bỏ qua/);
      await page.goto(base + '/dashboard-v2/#review/102/videos'); await page.waitForSelector('#review-dialog[open] .rv-message');
      assert.match(await page.locator('.rv-message').textContent(), /đang được quét lại/);
    });
    await check('500 synthetic items: first cards fast, batches of 24, at most 2 images at once, only cards near the view', async () => {
      queues.set(101, Mock.reviewQueue({...jobs.find(j => j.id === 101), duration: '07:26'}, {count: 500, advisory: 20}));
      counters.mediaMax = 0; counters.media = 0;
      await page.goto(base + '/dashboard-v2/#videos'); await page.waitForSelector('#search');
      const started = Date.now();
      await openFromDrawer(page, 101);
      const firstMs = Date.now() - started;
      const t0 = Date.now(); await page.locator('.rv-chips [data-filter="all"]').click(); await page.waitForFunction(() => document.querySelectorAll('article.rv-card').length >= 24);
      const filterMs = Date.now() - t0;
      assert.equal(await page.locator('article.rv-card').count(), 24, 'first batch only');
      await page.waitForTimeout(1500);
      const loadedEarly = counters.media;
      assert.ok(loadedEarly < 24, 'only cards near the view load images: ' + loadedEarly);
      for (let i = 0; i < 4; i++) { await page.evaluate(() => { const b = document.querySelector('.rv-body'); b.scrollTop = b.scrollHeight; }); await page.waitForTimeout(250); }
      const rendered = await page.locator('article.rv-card').count();
      assert.ok(rendered > 24 && rendered % 24 === 0, 'more cards near the end: ' + rendered);
      await page.waitForTimeout(1500);
      const stats = await page.evaluate(() => window.BFReviewStats);
      assert.ok(counters.mediaMax <= 2 && stats.maxImages <= 2, 'images at once: server ' + counters.mediaMax + ', page ' + stats.maxImages);
      assert.ok(firstMs < 3000 && filterMs < 1000, `open ${firstMs} ms, filter ${filterMs} ms`);
      process.stdout.write(`   500 items: dialog with cards ${firstMs} ms, "Tất cả" ${filterMs} ms, images at once ${counters.mediaMax}, cards ${rendered}\n`);
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('review-dialog').open && location.hash === '#videos');
      queues.delete(101);
    });
    await check('Strip: 8 ghost cells while the evidence loads, then ≤8 frames ("Rõ nhất" first); a frame click shows that frame', async () => {
      server_state.evidenceDelay = 1500;
      await page.goto(base + '/dashboard-v2/#review/101/videos'); await page.waitForSelector('#review-dialog[open] article.rv-card');
      await showAll(page);
      const scene = card(page, 'violence-101-0005');
      await scene.locator('.rv-name').click();
      await page.waitForFunction(() => document.querySelectorAll('article.rv-card[data-item="violence-101-0005"] .rv-thumb.ghost').length === 8);
      await page.waitForFunction(() => document.querySelectorAll('article.rv-card[data-item="violence-101-0005"] .rv-thumb.ghost').length === 0, null, {timeout: 6000});
      const thumbs = await scene.locator('.rv-thumb').evaluateAll(list => list.map(b => [b.className, b.dataset.t, b.querySelector('b') ? b.querySelector('b').textContent : '']));
      assert.ok(thumbs.length >= 1 && thumbs.length <= 8 && /peak/.test(thumbs[0][0]) && thumbs[0][2] === 'Rõ nhất', JSON.stringify(thumbs));
      await page.waitForFunction(() => document.querySelectorAll('article.rv-card[data-item="violence-101-0005"] .rv-thumb.loaded').length > 0);
      await scene.locator('.rv-thumb').nth(Math.min(1, thumbs.length - 1)).click();
      assert.equal(await scene.locator('.rv-thumb.on').count(), 1);
      if (VIDEO) await waitVideo(page, t => { const v = document.querySelector('#review-dialog video'); return v && v.paused && Math.abs(v.currentTime - t) < 0.3; }, Number(thumbs[Math.min(1, thumbs.length - 1)][1]));
      server_state.evidenceDelay = 300;
    });
    if (VIDEO) await check('Scene: moment chips; "▶ Phát lần lượt" plays the moments in order and stops; a click in a gap goes to the next moment', async () => {
      const scene = card(page, 'adult-101-0000');
      await scene.scrollIntoViewIfNeeded();
      assert.equal(await scene.locator('.rv-mchip').count(), 3);
      assert.match(await scene.locator('[data-review="seq"]').textContent(), /▶ Phát lần lượt 3 khoảnh khắc/);
      await scene.locator('[data-review="seq"]').click();
      const seen = [];
      for (let i = 0; i < 80; i++) {
        const on = await scene.evaluate(el => [...el.querySelectorAll('.rv-mchip')].findIndex(b => b.classList.contains('on')));
        if (on >= 0 && seen[seen.length - 1] !== on) seen.push(on);
        const v = await videoState(page);
        if (seen.length && v.paused && i > 5) break;
        await page.waitForTimeout(100);
      }
      assert.deepEqual(seen, [0, 1, 2], 'moments in order');
      assert.equal((await videoState(page)).paused, true, 'stops after the last moment');
      const q = queueFor(101), x = q.items.find(i => i.id === 'adult-101-0000'), ms = x.detected_intervals.map(d => [d.start_seconds, d.end_seconds]);
      const box = await scene.locator('.rv-timeline').boundingBox(), gap = (ms[0][1] + ms[1][0]) / 2;
      await scene.locator('.rv-timeline').click({position: {x: box.width * (((gap - x.start_seconds) / (x.end_seconds - x.start_seconds)) * 96 + 2) / 100, y: box.height / 2}});
      await waitVideo(page, t => Math.abs(document.querySelector('#review-dialog video').currentTime - t) < 0.3, ms[1][0]);
      assert.equal(await scene.evaluate(el => [...el.querySelectorAll('.rv-mchip')].findIndex(b => b.classList.contains('on'))), 1);
    });
    if (VIDEO) await check('A range stops at its end; polling while it plays keeps the same <video> and card; "Chi tiết kỹ thuật" stays open', async () => {
      const gore = card(page, GORE.id);
      await gore.scrollIntoViewIfNeeded();
      await gore.locator('.rv-tech summary').click();
      const box = await gore.locator('.rv-timeline').boundingBox();
      await gore.locator('.rv-timeline').click({position: {x: box.width * 0.5, y: box.height / 2}});
      await gore.locator('[data-review="play"]').click();
      await waitVideo(page, () => !document.querySelector('#review-dialog video').paused);
      await page.evaluate(id => { document.querySelector('#review-dialog video').dataset.mark = 'kept'; document.querySelector(`article.rv-card[data-item="${id}"]`).dataset.mark = 'kept'; }, GORE.id);
      const q = queueFor(101), other = q.items.find(i => i.id === 'text-101-0003');
      const before = await card(page, 'text-101-0003').locator('.rv-status').textContent();
      other.decision = other.decision === 'CUT' ? 'KEEP' : 'CUT'; q.updated_at = '2026-10-03T14:00:00Z';
      await page.waitForFunction(prev => document.querySelector('article.rv-card[data-item="text-101-0003"] .rv-status').textContent !== prev, before, {timeout: 8000});
      const v = await videoState(page);
      assert.deepEqual([v.mark, v.host, v.paused], ['kept', GORE.id, false], 'the playing video was not rebuilt');
      assert.equal(await gore.locator('[data-review="play"]').textContent(), '❚❚ Dừng', 'the button follows the playing video');
      assert.equal(await page.locator(`article.rv-card[data-item="${GORE.id}"][data-mark="kept"]`).count(), 1);
      assert.equal(await gore.locator('.rv-tech').evaluate(d => d.open), true);
      assert.match(await gore.locator('.rv-tech .tech-body').textContent(), /Phạm vi áp dụng: CHỈ ĐOẠN HIỆN TẠI/);
      await waitVideo(page, end => { const v = document.querySelector('#review-dialog video'); return v.paused && v.currentTime >= end - 0.4; }, GORE.end, 15000);
    });
    await check('Zoomed evidence: the 360 px crop of the red region; yellow AI boxes and their legend', async () => {
      const logo = card(page, 'visual_logo-101-0001');
      await logo.scrollIntoViewIfNeeded();
      await logo.locator('[data-review="zoom"]').click();
      await page.waitForFunction(() => { const c = document.querySelector('article.rv-card[data-item="visual_logo-101-0001"] canvas.rv-crop'); return c && c.dataset.drawn === '1'; });
      assert.equal(await logo.locator('canvas.rv-crop').evaluate(c => c.width), 360);
      const boxes = card(page, 'visual_logo-101-0002');
      await boxes.locator('[data-review="zoom"]').click();
      assert.equal(await logo.evaluate(el => el.classList.contains('zoom')), false, 'one zoomed card at a time');
      await page.waitForFunction(() => [...document.querySelectorAll('article.rv-card[data-item="visual_logo-101-0002"] .rv-aibox')].every(b => b.offsetWidth > 0));
      assert.equal(await boxes.locator('.rv-aibox').count(), 2);
      assert.equal(await boxes.locator('.rv-legend').textContent(), 'Khung vàng: vùng AI định vị, chỉ để tham khảo — không phải vùng sẽ làm mờ · watermark đã có thẻ riêng');
      assert.equal(await boxes.locator('.rv-aibox em').textContent(), 'watermark — đã có thẻ riêng');
      await page.keyboard.press('Escape');
      assert.equal(await page.evaluate(() => document.getElementById('review-dialog').open), true);
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    });
    if (VIDEO) await check('Media key: after a restart (new key) the video and the frames get a new key once, then play and load', async () => {
      // A fresh page: the shared <video> of the other checks keeps the old-key clip in Chromium's media cache.
      const page = await newPage(1440, 900, 'light');
      await page.goto(base + '/dashboard-v2/#review/101/videos'); await page.waitForSelector('#review-dialog[open] article.rv-card');
      await showAll(page);
      server_state.key = 'mk2'; // the Control Center restarted: the page still has mk1
      const sessions = counters.session, v403 = counters.video403, f403 = counters.frame403;
      const gore = card(page, GORE.id);
      await gore.scrollIntoViewIfNeeded();
      await gore.locator('[data-review="play"]').click();
      await waitVideo(page, () => { const v = document.querySelector('#review-dialog video'); return v && !v.paused && /k=mk2/.test(v.getAttribute('src')); }, null, 15000);
      // Whichever asks first (the video or a strip frame) gets the 403 and the new key; the other uses it.
      assert.ok(counters.video403 + counters.frame403 > v403 + f403, 'the old key was refused');
      assert.equal(counters.session, sessions + 1, 'one new session (single-flight)');
      await page.waitForFunction(id => { const t = document.querySelectorAll(`article.rv-card[data-item="${id}"] .rv-thumb`); return t.length && [...t].every(b => b.classList.contains('loaded') || b.classList.contains('failed')); }, GORE.id, {timeout: 15000});
      assert.equal(await gore.locator('.rv-thumb.failed').count(), 0, 'frames load with the new key');
      const stats = await page.evaluate(() => window.BFReviewStats);
      assert.ok(stats.maxImages <= 2 && stats.sessionRefreshes === 1, JSON.stringify(stats));
      await page.keyboard.press('Escape'); await page.waitForFunction(() => !document.getElementById('review-dialog').open);
      assert.equal((await videoState(page)).count, 0, 'released on close');
      server_state.key = 'mk1';
      await page.close();
    });
    await check('Video errors and locks: 415 → reason instead of ▶; evidence "unavailable" → reason; a cleaned source shows report images only', async () => {
      if (VIDEO) {
        server_state.videoStatus = 415;
        const fresh = await newPage(1440, 900, 'dark'); // no clip in the media cache: the 415 answer is what plays
        await fresh.goto(base + '/dashboard-v2/#review/101/videos'); await fresh.waitForSelector('#review-dialog[open] article.rv-card');
        await showAll(fresh);
        const gore = card(fresh, GORE.id);
        await gore.scrollIntoViewIfNeeded();
        await gore.locator('[data-review="play"]').click();
        await fresh.waitForFunction(id => /định dạng video này/.test(document.querySelector(`article.rv-card[data-item="${id}"] .rv-note`).textContent), GORE.id);
        await fresh.waitForFunction(id => !document.querySelector(`article.rv-card[data-item="${id}"] [data-review="play"]`), GORE.id);
        assert.match(await gore.locator('.rv-reason').textContent(), /Trình duyệt không phát được định dạng video này/);
        assert.ok((await fresh.evaluate(() => window.BFReviewStats.videoErrors)) >= 1);
        server_state.videoStatus = null;
        await fresh.close();
      }
      const q = queueFor(101), x = q.items.find(i => i.id === 'violence-101-0005');
      server_state.evidence.set(x.id, Mock.reviewEvidence(x, {video: {available: false, reason: 'source_changed'}}));
      await page.goto(base + '/dashboard-v2/#review/101/videos'); await page.waitForSelector('#review-dialog[open] article.rv-card');
      await showAll(page);
      await card(page, x.id).locator('.rv-name').click();
      await page.waitForFunction(id => !document.querySelector(`article.rv-card[data-item="${id}"] [data-review="seq"]`), x.id);
      assert.equal(await card(page, x.id).locator('.rv-reason').textContent(), 'Video nguồn đã thay đổi sau khi quét; chỉ xem được khung hình.');
      server_state.evidence.delete(x.id);
      const media = counters.evidence + counters.frames + counters.video;
      await page.goto(base + '/dashboard-v2/#review/113/videos'); await page.waitForSelector('#review-dialog[open] article.rv-card');
      await page.waitForTimeout(1200);
      assert.equal(await page.locator('#review-dialog [data-review="play"], #review-dialog [data-review="seq"]').count(), 0, 'no ▶ for a cleaned source');
      assert.match(await page.locator('#review-dialog .rv-reason').first().textContent(), /Video gốc đã được dọn vào Thùng rác; chỉ xem được ảnh đã lưu trong report/);
      assert.equal(counters.evidence + counters.frames + counters.video, media, 'no evidence, frame or video request for a cleaned source');
      await page.keyboard.press('Escape'); await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    });
    await check('Demo page: the same dialog on synthetic posters, no API request', async () => {
      const demo = await newPage(1440, 900, 'dark'), apiBefore = requests.filter(r => r.includes('/api/')).length;
      await demo.goto(base + '/demo/#videos'); await demo.waitForSelector('#search');
      await openFromDrawer(demo, 101);
      assert.equal(await demo.locator('.rv-progress-text').textContent(), '5 / 30 cảnh cần quyết định cuối');
      await demo.waitForSelector('article.rv-card .rv-art.loaded');
      assert.equal(await demo.locator('a.rv-old').count(), 0, 'no old-page link without a Control Center');
      assert.equal(await demo.locator('[data-review="play"], [data-review="seq"]').count(), 0, 'the demo has no video');
      assert.match(await demo.locator('.rv-reason').first().textContent(), /Trang này chỉ có ảnh xem trước, không phát video\./);
      assert.equal(requests.filter(r => r.includes('/api/')).length, apiBefore);
      await demo.close();
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
