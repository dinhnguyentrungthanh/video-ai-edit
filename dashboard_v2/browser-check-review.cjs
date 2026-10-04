/* Review dialog browser check (R0): Chromium (Playwright) on live.html with a fake Control Center API
 * on 127.0.0.1 (synthetic queues from mock-data.js; no real backend, database, video or network).
 * At 1440, 1024 and 390 px, light and dark: the dialog, its address, Back/Esc/Đóng, 2 / 1 columns and
 * full screen, the old-page link, at most 2 images at once, polling that only patches, the paused
 * dashboard polling, 500 synthetic items, and no POST, no blob:, no console or CSP error.
 * Run: node dashboard_v2/browser-check-review.cjs
 */
'use strict';
const fs = require('fs');
const http = require('http');
const path = require('path');
const vm = require('vm');
const assert = require('assert/strict');
const {execSync} = require('child_process');

function loadPlaywright() {
  try { return require('playwright'); } catch (_) { /* fall through */ }
  try { return require(path.join(execSync('npm root -g', {encoding: 'utf8'}).trim(), 'playwright')); } catch (_) { return null; }
}
const pw = loadPlaywright();
if (!pw) { process.stdout.write('SKIP browser-check-review: Playwright is not installed\n'); process.exit(0); }

const ROOT = __dirname;
const TOKEN = 'browser-token';
const TYPES = {'.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.html': 'text/html'};
const ASSETS = new Set(['styles.css', 'theme.css', 'review.css', 'contracts.js', 'adapter.js', 'download-demo.js', 'mock-data.js', 'demo-store.js',
  'review-core.js', 'review-media.js', 'review-cards.js', 'review.js', 'app.js', ...fs.readdirSync(path.join(ROOT, 'assets')).map(f => 'assets/' + f)]);
const ctx = {window: {BFContracts: require('./contracts.js')}, structuredClone};
vm.runInNewContext(fs.readFileSync(path.join(ROOT, 'mock-data.js'), 'utf8'), ctx);
const Mock = ctx.window.BFMock;
const jobs = Mock.create().jobs.map(({name, duration, palette, render_request, output_path, ...j}) => ({...j, duration_seconds: 446}));
const queues = new Map(); // job id → queue override (else generated from the job)
const queueFor = id => {
  const j = jobs.find(x => x.id === id);
  if (!j || !j.active_queue_path) return null;
  if (!queues.has(id)) queues.set(id, Mock.reviewQueue({...j, duration: '07:26'}));
  return queues.get(id);
};
const counters = {status: 0, queue: 0, media: 0, mediaActive: 0, mediaMax: 0};
const posts = [], requests = [];

function send(res, code, body, type) {
  const data = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(code, {'Content-Type': type || 'application/json; charset=utf-8', 'Cache-Control': 'no-store'});
  res.end(data);
}
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1'), p = url.pathname;
  requests.push(req.method + ' ' + req.url);
  if (req.method !== 'GET') { posts.push(p); req.resume(); return send(res, 405, {error: 'R0 is read-only'}); }
  if (p === '/dashboard-v2/') return send(res, 200, fs.readFileSync(path.join(ROOT, 'live.html')), 'text/html');
  if (p === '/demo/') return send(res, 200, fs.readFileSync(path.join(ROOT, 'index.html')), 'text/html');
  const asset = p.match(/^\/(?:dashboard-v2|demo)\/(.+)$/);
  if (asset && ASSETS.has(asset[1])) return send(res, 200, fs.readFileSync(path.join(ROOT, asset[1])), TYPES[path.extname(asset[1])]);
  if (p === '/api/session') return send(res, 200, {token: TOKEN});
  if (p === '/api/status') { counters.status++; return send(res, 200, {version: 't', jobs, queue: {length: 0, paused: false}, active: null, resources: {disk: {}}}); }
  if (p === '/api/phone-mode') return send(res, 200, {remote: false, enabled: false});
  if (p === '/api/ai') return send(res, 200, {ready: false, config: {enabled: false}, message: 'tắt'});
  const review = p.match(/^\/api\/jobs\/(\d+)\/review\/(queue|export|session|resources)$/);
  if (review) {
    const id = Number(review[1]), j = jobs.find(x => x.id === id);
    if (review[2] === 'queue') { counters.queue++; const q = queueFor(id); return q ? send(res, 200, q) : send(res, 404, {error: 'Video chưa có danh sách duyệt'}); }
    if (review[2] === 'export') return send(res, 200, {status: j.state, source_cleaned: !!j.source_cleaned, source_archived: !!j.source_archived});
    if (review[2] === 'session') return send(res, 200, {token: TOKEN, media_key: 'mk'});
    return send(res, 200, {source_bytes: 1, report_bytes: 1, disk_free_bytes: 1});
  }
  const media = p.match(/^\/media\/(.+)$/);
  if (media) {
    const file = decodeURIComponent(media[1]).match(/^demo\/(poster-[a-z]+\.svg)$/);
    counters.media++; counters.mediaActive++; counters.mediaMax = Math.max(counters.mediaMax, counters.mediaActive);
    return setTimeout(() => { counters.mediaActive--; file ? send(res, 200, fs.readFileSync(path.join(ROOT, 'assets', file[1])), 'image/svg+xml') : send(res, 404, {error: 'x'}); }, 220);
  }
  if (/^\/review\/\d+$/.test(p)) return send(res, 200, '<!doctype html><title>review</title><h1>Trang duyệt cũ</h1>', 'text/html');
  return send(res, 404, {error: 'Không tìm thấy'});
});

let passed = 0;
async function check(name, fn) { await fn(); passed++; process.stdout.write('OK ' + name + '\n'); }

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = 'http://127.0.0.1:' + server.address().port;
  const browser = await pw.chromium.launch({});
  const problems = [];
  const EXPECTED_FAILURES = [/\/api\/jobs\/102\/review\/queue$/]; // job 102 is scanning: no queue yet (the "quét lại" message)
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
  const openFromDrawer = async (page, id) => {
    await page.fill('#search', String(id));
    await page.locator('[data-action="detail"][data-id="' + id + '"]').first().click();
    await page.waitForSelector('.drawer');
    await page.locator('.drawer [data-action="review-v2"]').click();
    await page.waitForSelector('#review-dialog[open] article.rv-card');
  };
  const dialogState = page => page.evaluate(() => {
    const d = document.getElementById('review-dialog'), r = d.getBoundingClientRect(), body = d.querySelector('.rv-body');
    return {open: d.open, hash: location.hash, w: Math.round(r.width), h: Math.round(r.height), cols: body ? getComputedStyle(d.querySelector('.rv-cards')).gridTemplateColumns.split(' ').length : 0,
      overflow: d.scrollWidth > d.clientWidth + 1 || (body && body.scrollWidth > body.clientWidth + 1) || document.documentElement.scrollWidth > window.innerWidth,
      theme: document.documentElement.dataset.theme, old: (d.querySelector('a.rv-old') || {}).getAttribute ? d.querySelector('a.rv-old').getAttribute('href') : null};
  });
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
    await check('The temporary button only shows for a job that can be reviewed; the old "Duyệt" stays', async () => {
      await page.goto(base + '/dashboard-v2/#videos'); await page.waitForSelector('#search');
      for (const [id, shown] of [[101, true], [102, false], [106, false]]) {
        await page.fill('#search', String(id));
        await page.locator('[data-action="detail"][data-id="' + id + '"]').first().click();
        await page.waitForSelector('.drawer');
        assert.equal(await page.locator('.drawer [data-action="review-v2"]').count(), shown ? 1 : 0, 'job ' + id);
        if (shown) assert.equal(await page.locator('.drawer [data-op="review"]').count(), 1, 'old Duyệt button kept');
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
      queues.delete(101);
    });
    await check('Demo page: the same dialog on synthetic posters, no API request', async () => {
      const demo = await newPage(1440, 900, 'dark'), apiBefore = requests.filter(r => r.includes('/api/')).length;
      await demo.goto(base + '/demo/#videos'); await demo.waitForSelector('#search');
      await openFromDrawer(demo, 101);
      assert.equal(await demo.locator('.rv-progress-text').textContent(), '5 / 30 cảnh cần quyết định cuối');
      await demo.waitForSelector('article.rv-card .rv-art.loaded');
      assert.equal(await demo.locator('a.rv-old').count(), 0, 'no old-page link without a Control Center');
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
