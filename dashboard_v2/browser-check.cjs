'use strict';
/* Optional browser gate (headless Chromium through Playwright, if installed).
 * Serves dashboard_v2/ from memory together with a FAKE Control Center API built from the
 * synthetic fixtures (mock-data.js). No real backend, database, video or network.
 * Run: node dashboard_v2/browser-check.cjs   (prints SKIP when Playwright is missing)
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const vm = require('node:vm');
const {execSync} = require('node:child_process');

function loadPlaywright() {
  try { return require('playwright'); } catch (_) { /* fall through */ }
  try { return require(path.join(execSync('npm root -g', {encoding: 'utf8'}).trim(), 'playwright')); } catch (_) { return null; }
}
const pw = loadPlaywright();
if (!pw) { process.stdout.write('SKIP browser-check: Playwright is not installed\n'); process.exit(0); }

const ROOT = __dirname;
const TOKEN = 'browser-token';
const ASSETS = new Set(['styles.css', 'theme.css', 'contracts.js', 'adapter.js', 'download-demo.js', 'app.js', 'mock-data.js', 'demo-store.js',
  ...fs.readdirSync(path.join(ROOT, 'assets')).map(f => 'assets/' + f)]);
const TYPES = {'.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.html': 'text/html'};

/* Backend-shaped status from the fixtures: demo-only display fields are dropped. */
const ctx = {window: {BFContracts: require('./contracts.js')}, structuredClone};
vm.runInNewContext(fs.readFileSync(path.join(ROOT, 'mock-data.js'), 'utf8'), ctx);
const demo = ctx.window.BFMock.create();
const jobs = demo.jobs.map(({name, duration, palette, render_request, output_path, ...j}) => ({...j, duration_seconds: 1500}));
const r103 = jobs.find(j => j.id === 103);
r103.review_summary = {...r103.review_summary, main_items: 2, pending: 0, decisions: {KEEP: 1, NEEDS_MORE_CONTEXT: 1}};
// G1/G2 fixtures: a ready job whose source left input, and a completed job whose last cleanup failed.
jobs.push({...jobs.find(j => j.id === 107), id: 116, job_key: 'demo-video-116', source_path: 'E:\\DungChung\\BiliFlow\\input\\Mất nguồn.mp4', source_present: false});
jobs.push({...jobs.find(j => j.id === 105), id: 117, job_key: 'demo-video-117', source_path: 'E:\\DungChung\\BiliFlow\\input\\Dọn lỗi.mp4', source_cleanup: {id: 917, state: 'FAILED', error: 'Thùng rác không phản hồi'}});
let polls = 0;
const posts = [];
let refuseNextCancel = false;
// Fake phone mode: the PC sees the code; with remoteMode the page behaves as on the phone listener.
let remoteMode = false, phoneOn = false, phoneCode = 'abcd2345';
const phoneStatus = () => remoteMode ? {remote: true, enabled: true} : {remote: false, enabled: phoneOn,
  url: phoneOn ? 'http://192.168.1.23:8767/' : null, code: phoneOn ? phoneCode : null,
  locked: false, failed_attempts: 0, max_failed_attempts: 10};
const PC_ONLY = ['/api/source-cleanup', '/api/source-archive', '/api/source-archive/restore', '/api/source-recycle-check',
  '/api/shutdown', '/api/ai/config', '/api/ai/login', '/api/logo-memory/class', '/api/logo-memory/delete', '/api/phone-mode'];
const status = () => ({version: '0.7.24', started: true, scheduler_paused: false, queue: {length: 2, paused: false},
  active: {job_id: 102, stage: 'visual_logo', pid: 1}, jobs, source_cleanup_running: false, detector_options: [],
  resources: {cpu_percent: 10 + (polls % 5), memory: {percent: 40, used_bytes: 1, total_bytes: 2}, disk: {percent: 60, free_bytes: 3e11, total_bytes: 1e12}, gpu: null}});

function send(res, code, body, type) {
  const data = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
  res.writeHead(code, {'Content-Type': type || 'application/json; charset=utf-8', 'Cache-Control': 'no-store'});
  res.end(data);
}
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const p = url.pathname;
  if (req.method === 'GET') {
    if (p === '/dashboard-v2/') return send(res, 200, fs.readFileSync(path.join(ROOT, 'live.html')), 'text/html');
    if (p === '/demo/') return send(res, 200, fs.readFileSync(path.join(ROOT, 'index.html')), 'text/html');
    const m = p.match(/^\/(?:dashboard-v2|demo)\/(.+)$/);
    if (m && ASSETS.has(m[1])) return send(res, 200, fs.readFileSync(path.join(ROOT, m[1])), TYPES[path.extname(m[1])]);
    if (p === '/api/session') return send(res, 200, {token: TOKEN});
    if (p === '/api/status') { polls++; return send(res, 200, status()); }
    if (p === '/api/phone-mode') return send(res, 200, phoneStatus());
    if (p === '/api/ai') return send(res, 200, {ready: true, config: {enabled: true, model: 'gpt-5.6-luna', reasoning_effort: 'medium'}, models: ['gpt-5.6-luna'], efforts: ['low', 'medium'], message: 'Đã kết nối (giả)'});
    if (p === '/api/logo-memory') return send(res, 200, {memory_sha256: 'c'.repeat(64), records: [{key: 'k1', labels: ['iQIYI'], memory_class: 'platform_logo', platform: 'iqiyi', frames: 1, frame_urls: []}], backups: []});
    if (/^\/review\/\d+$/.test(p)) return send(res, 200, '<!doctype html><title>review</title><h1>Trang duyệt cũ</h1>', 'text/html');
    if (p.endsWith('/preview')) return send(res, 200, {preview_id: 'p1', eligible: [{job_id: 105, name: 'demo', file_name: 'demo.mp4', size_bytes: 1, kind: 'EXPORTED'}], ineligible: [{job_id: 109, name: 'x', reason: 'Lý do thật từ backend'}], recycle_bin: {volume: 'E:\\', used_bytes: 1, max_bytes: 1e10, after_bytes: 2}, blocked: null});
    return send(res, 404, {error: 'Không tìm thấy'});
  }
  let body = '';
  req.on('data', c => { body += c; });
  req.on('end', () => {
    if (req.headers['x-biliflow-token'] !== TOKEN) return send(res, 403, {error: 'Phiên Control Center không hợp lệ'});
    posts.push({path: p, body: JSON.parse(body || '{}'), type: req.headers['content-type']});
    if (remoteMode && PC_ONLY.includes(p)) return send(res, 403, {error: 'Chỉ làm trên PC: (giả)', code: 'pc_only'});
    if (p === '/api/phone-mode') { phoneOn = JSON.parse(body).enabled; if (phoneOn) phoneCode = phoneCode === 'abcd2345' ? 'wxyz6789' : 'abcd2345'; return send(res, 200, phoneStatus()); }
    setTimeout(() => {
      if (p.endsWith('/cancel') && refuseNextCancel) { refuseNextCancel = false; return send(res, 409, {error: 'Trạng thái vừa đổi', code: 'state_changed'}); }
      send(res, 200, p.endsWith('/finalize') ? {status: 'QUEUED'} : {});
    }, 300);
  });
});

let passed = 0;
const results = [];
async function check(name, fn) { await fn(); passed++; results.push(name); process.stdout.write('OK ' + name + '\n'); }

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = 'http://127.0.0.1:' + server.address().port;
  const browser = await pw.chromium.launch({});
  const external = [];
  const page = await browser.newPage({viewport: {width: 1280, height: 900}});
  page.on('request', r => { if (!r.url().startsWith(base)) external.push(r.url()); });
  const errors = [];
  page.on('pageerror', e => { errors.push(e.message); process.stderr.write('pageerror: ' + e.message + '\n'); });
  const noOverflow = async label => {
    const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    assert.ok(sw <= iw, label + ': scrollWidth ' + sw + ' > ' + iw);
  };
  const waitPoll = async () => { const start = polls; await page.waitForFunction(() => true); while (polls < start + 1) await page.waitForTimeout(200); await page.waitForTimeout(150); };
  const openJob = async id => {
    if (await page.locator('.drawer').count()) await page.keyboard.press('Escape');
    if (!(await page.locator('#search').count())) { await page.goto(base + '/dashboard-v2/#videos'); await page.waitForSelector('#search'); }
    await page.locator('[data-action="filter"][data-filter="all"]').first().click();
    await page.fill('#search', String(id));
    await page.locator('[data-action="detail"][data-id="' + id + '"]').first().click();
    await page.waitForSelector('.drawer');
    await page.evaluate(() => document.querySelector('.drawer details.more-actions')?.setAttribute('open', ''));
  };
  const jobButton = (id, op) => page.locator('[data-action="job"][data-id="' + id + '"][data-op="' + op + '"]').first();

  try {
    await check('Live route renders real-shaped status at 1280 px without horizontal overflow', async () => {
      await page.goto(base + '/dashboard-v2/#videos');
      await page.waitForSelector('[data-job="101"]');
      assert.equal(await page.locator('.demo-pill').textContent(), 'CONTROL CENTER');
      assert.equal(await page.locator('[data-action="reset"]').count(), 0);
      await noOverflow('videos 1280');
    });

    await check('Unresolved NEEDS_MORE_CONTEXT keeps Xuất video disabled', async () => {
      const btn = jobButton(103, 'finalize');
      assert.equal(await btn.count(), 1);
      assert.equal(await btn.isDisabled(), true);
    });

    await check('Cancelling a dialog sends no POST; a double click on confirm sends one POST', async () => {
      await openJob(115);
      await page.locator('.drawer [data-op="cancel"]').click();
      await page.locator('#modal [data-action="close-modal"]').last().click();
      assert.equal(posts.length, 0);
      await page.locator('.drawer [data-op="cancel"]').click();
      await page.locator('#confirm-action').dblclick();
      await page.waitForFunction(() => !document.querySelector('#modal').open);
      assert.equal(posts.length, 1);
      assert.equal(posts[0].path, '/api/jobs/115/cancel');
      assert.match(posts[0].type, /application\/json/);
    });

    await check('409 shows the reason in the dialog and is not replayed', async () => {
      refuseNextCancel = true;
      const before = posts.length;
      await page.locator('.drawer [data-op="cancel"]').click();
      await page.locator('#confirm-action').click();
      await page.waitForSelector('#modal-error:not([hidden])');
      assert.match(await page.locator('#modal-error').textContent(), /Trạng thái vừa đổi \(state_changed\)/);
      await page.waitForTimeout(800);
      assert.equal(posts.length, before + 1);
      await page.locator('#modal [data-action="close-modal"]').last().click();
    });

    await check('Drawer survives polling: open sections and focus are kept; Escape closes it', async () => {
      await page.evaluate(() => { const d = document.querySelector('.drawer details.technical'); d.open = true; d.querySelector('summary').focus(); });
      await waitPoll();
      assert.equal(await page.evaluate(() => document.querySelector('.drawer details.technical').open), true);
      assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.closest('details.technical') !== null), true);
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.drawer').count(), 0);
    });

    await check('Scan and export drafts survive polling and closing/reopening the dialog', async () => {
      await openJob(106);
      await page.locator('.drawer [data-op="start"]').click();
      await page.locator('input[name="detector"][value="gore"]').uncheck();
      await page.selectOption('#scan-ocr', '8');
      await waitPoll();
      assert.equal(await page.locator('input[name="detector"][value="gore"]').isChecked(), false);
      await page.locator('#modal [data-action="close-modal"]').last().click();
      await page.locator('.drawer [data-op="start"]').click();
      assert.equal(await page.locator('input[name="detector"][value="gore"]').isChecked(), false);
      assert.equal(await page.inputValue('#scan-ocr'), '8');
      await page.locator('#modal [data-action="close-modal"]').last().click();
      await page.keyboard.press('Escape');
      await openJob(107);
      await page.locator('.drawer [data-op="finalize"]').click();
      await page.selectOption('#export-mode', 'custom');
      await page.fill('#export-gb', '2.5');
      await page.locator('#export-gb').dispatchEvent('change');
      await waitPoll();
      await page.locator('#modal [data-action="close-modal"]').last().click();
      await page.locator('.drawer [data-op="finalize"]').click();
      assert.equal(await page.inputValue('#export-mode'), 'custom');
      assert.equal(await page.inputValue('#export-gb'), '2.5');
      const before = posts.length;
      await page.locator('#confirm-action').click();
      await page.waitForFunction(() => !document.querySelector('#modal').open);
      assert.deepEqual(posts[before], {path: '/api/jobs/107/review/finalize', body: {size_mode: 'custom', max_output_gb: 2.5}, type: 'application/json'});
      await page.keyboard.press('Escape');
    });

    await check('Tab stays inside the drawer; Escape closes the modal before the drawer', async () => {
      await openJob(101);
      for (let i = 0; i < 25; i++) await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => !!document.activeElement.closest('.drawer')), true);
      await page.locator('.drawer [data-op="cancel"]').click().catch(() => {});
      if (await page.evaluate(() => document.querySelector('#modal').open)) {
        await page.keyboard.press('Escape');
        assert.equal(await page.evaluate(() => document.querySelector('#modal').open), false);
        assert.equal(await page.locator('.drawer').count(), 1);
      }
      await page.keyboard.press('Escape');
    });

    await check('Cleanup preview shows backend reasons for excluded videos', async () => {
      await openJob(105);
      const before = posts.length;
      await page.locator('.drawer [data-op="cleanup"]').click();
      await page.waitForSelector('#modal[open]');
      assert.match(await page.locator('#modal').textContent(), /Lý do thật từ backend/);
      await page.locator('#modal [data-action="close-modal"]').last().click();
      assert.equal(posts.length, before, 'preview is GET only');
      await page.keyboard.press('Escape');
    });

    await check('Duyệt cảnh opens the existing review page of the same job', async () => {
      await openJob(101);
      await Promise.all([page.waitForURL(/\/review\/101$/), page.locator('.drawer [data-op="review"]').first().click()]);
      await page.goto(base + '/dashboard-v2/#overview');
      await page.waitForSelector('.kpi');
    });

    await check('Unsaved AI settings survive polling', async () => {
      await page.goto(base + '/dashboard-v2/#settings');
      await page.waitForSelector('#ai-effort');
      await page.selectOption('#ai-effort', 'low');
      await page.locator('h1').click();
      await waitPoll(); await waitPoll();
      assert.equal(await page.inputValue('#ai-effort'), 'low');
    });

    await check('Downloads stay a labelled simulation with no request', async () => {
      const before = posts.length, ext = external.length;
      await page.goto(base + '/dashboard-v2/#downloads');
      await page.waitForSelector('#download-simulation');
      assert.match(await page.locator('#download-simulation').textContent(), /mô phỏng/);
      await page.locator('[data-action="download-sample"]').click();
      await page.waitForTimeout(1200);
      assert.equal(posts.length, before); assert.equal(external.length, ext);
    });

    await check('375 px: overview, videos, downloads, settings and drawer do not overflow', async () => {
      await page.setViewportSize({width: 375, height: 800});
      for (const view of ['overview', 'videos', 'queue', 'downloads', 'logos', 'settings']) {
        await page.goto(base + '/dashboard-v2/#' + view);
        await page.waitForTimeout(500);
        await noOverflow(view + ' 375');
      }
      await page.goto(base + '/dashboard-v2/#videos');
      await page.waitForSelector('#search');
      await openJob(101);
      await noOverflow('drawer 375');
      await page.setViewportSize({width: 1280, height: 900});
    });

    await check('Demo page still runs on fixtures without any API request', async () => {
      const apiBefore = polls, before = posts.length;
      const demoPage = await browser.newPage();
      const demoRequests = [];
      demoPage.on('request', r => { if (r.url().includes('/api/')) demoRequests.push(r.url()); });
      await demoPage.goto(base + '/demo/#videos');
      await demoPage.waitForSelector('[data-job="101"]');
      assert.equal(await demoPage.locator('.demo-pill').textContent(), 'DỮ LIỆU MẪU');
      assert.deepEqual(demoRequests, []); assert.equal(polls, apiBefore); assert.equal(posts.length, before);
      await demoPage.close();
    });

    await check('G1/G2: missing source and a failed cleanup read like the classic dashboard', async () => {
      if (await page.locator('.drawer').count()) await page.keyboard.press('Escape');
      await page.goto(base + '/dashboard-v2/#videos');
      await page.waitForSelector('#search');
      await page.locator('[data-action="filter"][data-filter="all"]').first().click();
      await page.fill('#search', '116');
      assert.match(await page.locator('[data-job="116"] .cell-sub').textContent(), /^Không còn video gốc trong input$/);
      await openJob(116);
      const drawer = await page.locator('.drawer').textContent();
      assert.match(drawer, /Không còn video gốc trong input/);
      assert.match(drawer, /Video gốc không còn trong input; không thể xuất\./);
      assert.ok(!/Có trong input/.test(drawer), 'never claims the source is in input');
      assert.equal(await page.locator('.drawer [data-op="finalize"]').isDisabled(), true);
      await page.keyboard.press('Escape');
      await page.fill('#search', '117');
      assert.match(await page.locator('[data-job="117"] .cell-sub').textContent(), /^Lần dọn trước không thành công: Thùng rác không phản hồi$/);
      await openJob(117);
      assert.match(await page.locator('.drawer .source-line').textContent(), /Lần dọn trước không thành công: Thùng rác không phản hồi/);
      await page.keyboard.press('Escape');
    });

    await check('Phone panel on the PC: turn on shows link and a new code, turn off clears it', async () => {
      if (await page.locator('.drawer').count()) await page.keyboard.press('Escape');
      await page.goto(base + '/dashboard-v2/#settings');
      await page.waitForSelector('[data-action="phone-toggle"][data-enabled="1"]');
      assert.match(await page.locator('.phone-panel').textContent(), /Private networks/);
      assert.match(await page.locator('.phone-panel').textContent(), /Wi-Fi nhà/);
      await page.locator('[data-action="phone-toggle"][data-enabled="1"]').click();
      await page.waitForSelector('.phone-code');
      const first = await page.locator('.phone-code').textContent();
      assert.match(await page.locator('.phone-panel').textContent(), /http:\/\/192\.168\.1\.23:8767\//);
      await page.locator('[data-action="phone-toggle"][data-enabled="0"]').click();
      await page.waitForSelector('[data-action="phone-toggle"][data-enabled="1"]');
      assert.equal(await page.locator('.phone-code').count(), 0);
      await page.locator('[data-action="phone-toggle"][data-enabled="1"]').click();
      await page.waitForSelector('.phone-code');
      assert.notEqual(await page.locator('.phone-code').textContent(), first, 'a new code each time');
      assert.deepEqual(posts.slice(-3).map(x => [x.path, x.body.enabled]), [['/api/phone-mode', true], ['/api/phone-mode', false], ['/api/phone-mode', true]]);
    });

    await check('Opened through the phone: PC-only actions are disabled with the reason, 375 px fits', async () => {
      remoteMode = true;
      await page.setViewportSize({width: 375, height: 800});
      await page.goto(base + '/dashboard-v2/?remote#settings');
      await page.waitForSelector('.phone-panel h2');
      assert.match(await page.locator('.phone-panel h2').textContent(), /Đang mở qua điện thoại/);
      assert.equal(await page.locator('[data-action="phone-toggle"]').count(), 0, 'no switch on the phone');
      assert.ok(!/abcd2345|wxyz6789/.test(await page.content()), 'the phone never sees the code');
      for (const sel of ['[data-action="shutdown"]', '[data-action="ai-save"]', '[data-action="ai-login"]']) {
        const button = page.locator(sel).first();
        assert.equal(await button.isDisabled(), true, sel);
        assert.match(await button.getAttribute('title'), /Chỉ làm trên PC/);
      }
      const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
      assert.ok(sw <= iw, 'settings 375: ' + sw);
      await page.goto(base + '/dashboard-v2/?remote1#overview');
      await page.waitForSelector('[data-action="scheduler"]');
      assert.equal(await page.locator('[data-action="scheduler"]').first().isDisabled(), false, 'queue pause stays available');
      await page.goto(base + '/dashboard-v2/?remote2#videos');
      await page.waitForSelector('#search');
      await openJob(105);
      const cleanup = page.locator('.drawer [data-op="cleanup"]');
      assert.equal(await cleanup.isDisabled(), true);
      assert.match(await cleanup.getAttribute('title'), /Chỉ làm trên PC/);
      const [sw2, iw2] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
      assert.ok(sw2 <= iw2, 'drawer 375: ' + sw2);
      await page.keyboard.press('Escape');
      await page.goto(base + '/dashboard-v2/?remote3#logos');
      await page.waitForSelector('[data-action="logo-delete"]');
      assert.equal(await page.locator('[data-action="logo-delete"]').first().isDisabled(), true);
      remoteMode = false;
      await page.setViewportSize({width: 1280, height: 900});
    });

    await check('No page error and no request outside the origin', async () => {
      assert.deepEqual(errors, []);
      assert.deepEqual(external, []);
    });
  } finally {
    await browser.close();
    server.close();
  }
  process.stdout.write(JSON.stringify({passed, failed: 0}) + '\n');
})().catch(error => { console.error(error); process.exitCode = 1; server.close(); });
