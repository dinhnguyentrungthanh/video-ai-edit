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
  'review.css', 'review-core.js', 'review-detail.js', 'review-media.js', 'review-cards.js', 'review.js',
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
let remoteMode = false, phoneOn = false, phoneCode = 'abcd2345', phoneExtended = false;
const phoneStatus = () => remoteMode ? {remote: true, enabled: true} : {remote: false, enabled: phoneOn,
  url: phoneOn ? 'http://192.168.1.23:8767/' : null, code: phoneOn ? phoneCode : null,
  locked: false, failed_attempts: 0, max_failed_attempts: 10, expires_at: phoneOn ? (phoneExtended ? 1790028800 : 1790000000) : null,
  last_disabled_reason_text: phoneOn ? null : 'hết 8 giờ',
  events: phoneOn ? [{type: 'PHONE_CODE_WRONG', message: 'Thiết bị 192.168.1.50 nhập sai mã', at: 1789990000, ip: '192.168.1.50'}] : []};
const PC_ONLY = ['/api/source-cleanup', '/api/job-delete', '/api/source-archive', '/api/source-archive/restore', '/api/source-recycle-check',
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
    if (p === '/api/phone-mode' && JSON.parse(body).extend) { phoneExtended = true; return send(res, 200, phoneStatus()); }
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

    await check('R4.3: "Duyệt cảnh" opens the review dialog of the same job over the view (live and demo); no "thử" button; Esc returns', async () => {
      await openJob(101);
      assert.equal(await page.locator('.drawer [data-action="review-v2"], .drawer .review-v2-try').count(), 0, 'the temporary button is gone');
      await page.locator('.drawer [data-op="review"]').first().click();
      await page.waitForFunction(() => document.getElementById('review-dialog').open && location.hash === '#review/101/videos');
      assert.equal(new URL(page.url()).pathname, '/dashboard-v2/', 'no navigation to the classic page');
      assert.match(await page.locator('#review-title').textContent(), /^Duyệt cảnh · #101/);
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('review-dialog').open && location.hash === '#videos');
      const demo = await browser.newPage({viewport: {width: 1440, height: 900}});
      demo.on('pageerror', e => errors.push('demo: ' + e.message));
      await demo.goto(base + '/demo/#videos'); await demo.waitForSelector('#search');
      await demo.fill('#search', '101');
      await demo.locator('[data-action="detail"][data-id="101"]').first().click();
      await demo.waitForSelector('.drawer');
      await demo.locator('.drawer [data-op="review"]').first().click();
      await demo.waitForSelector('#review-dialog[open] article.rv-card');
      assert.equal(await demo.locator('#modal[open]').count(), 0, 'the prototype review modal is gone');
      assert.equal(await demo.locator('.rv-progress-text').textContent(), '5 / 30 cảnh cần quyết định cuối');
      await demo.close();
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
      assert.match(await page.locator('.phone-panel').textContent(), /Tự tắt lúc/);
      assert.match(await page.locator('.phone-events').textContent(), /192\.168\.1\.50 nhập sai mã/);
      const before = await page.locator('.phone-panel').textContent();
      await page.locator('[data-action="phone-extend"]').click();
      await page.waitForFunction(b => document.querySelector('.phone-panel').textContent !== b, before);
      assert.equal(await page.locator('.phone-code').textContent(), first, 'extending keeps the code');
      assert.deepEqual(posts[posts.length - 1].body, {extend: true});
      await page.locator('[data-action="phone-toggle"][data-enabled="0"]').click();
      await page.waitForSelector('[data-action="phone-toggle"][data-enabled="1"]');
      assert.equal(await page.locator('.phone-code').count(), 0);
      assert.match(await page.locator('.phone-panel').textContent(), /lần trước tắt vì hết 8 giờ/);
      await page.locator('[data-action="phone-toggle"][data-enabled="1"]').click();
      await page.waitForSelector('.phone-code');
      assert.notEqual(await page.locator('.phone-code').textContent(), first, 'a new code each time');
      assert.deepEqual(posts.slice(-4).map(x => [x.path, x.body.enabled ?? (x.body.extend ? 'extend' : null)]),
        [['/api/phone-mode', true], ['/api/phone-mode', 'extend'], ['/api/phone-mode', false], ['/api/phone-mode', true]]);
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
      // H2: the visual audit option is locked on the phone; the JSON audit stays.
      await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/phone-mode')),
        page.goto(base + '/dashboard-v2/?remote4#videos')]);
      await page.waitForSelector('#search');
      await openJob(101);
      await page.locator('.drawer [data-op="audit"]').click();
      await page.waitForSelector('#audit-kind');
      assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#audit-kind option')].map(o => [o.value, o.disabled])),
        [['json', false], ['visual', true]]);
      assert.match(await page.locator('#modal').textContent(), /Visual AI Audit gửi ảnh ra ngoài máy/);
      await page.locator('#modal [data-action="close-modal"]').last().click();
      await page.keyboard.press('Escape');
      remoteMode = false;
      await page.setViewportSize({width: 1280, height: 900});
    });

    await check('U1: polling keeps open folds, the scroll position and a focused select', async () => {
      if (await page.locator('.drawer').count()) await page.keyboard.press('Escape');
      await page.setViewportSize({width: 1280, height: 640});
      await page.goto(base + '/dashboard-v2/?u1#videos');
      await page.waitForSelector('#search');
      await page.locator('[data-action="filter"][data-filter="all"]').first().click();
      await page.fill('#search', '');
      await page.locator('details.fold[data-fold="cancelled"] summary').click();
      assert.equal(await page.locator('details.fold[data-fold="cancelled"]').getAttribute('open'), '');
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      const y = await page.evaluate(() => window.scrollY);
      assert.ok(y > 100, 'the page scrolls: ' + y);
      // Identical snapshot: #main keeps its nodes.
      await page.evaluate(() => { document.querySelector('#main .list-section').dataset.mark = 'kept'; });
      await waitPoll(); await waitPoll();
      assert.equal(await page.locator('#main .list-section[data-mark="kept"]').count(), 1, 'no rebuild without a change');
      // Changed snapshot: rebuilt, the fold stays open and the page does not jump.
      jobs.find(j => j.id === 104).progress = 0.5;
      jobs.find(j => j.id === 102).progress = (jobs.find(j => j.id === 102).progress + 0.01) % 1;
      await waitPoll(); await waitPoll();
      assert.equal(await page.locator('#main .list-section[data-mark="kept"]').count(), 0, 'rebuilt after a change');
      assert.equal(await page.locator('details.fold[data-fold="cancelled"]').evaluate(d => d.open), true, 'the fold stays open');
      assert.equal(await page.evaluate(() => window.scrollY), y, 'scrollY unchanged');
      // A focused select is not rebuilt.
      await page.locator('#sort').focus();
      await page.evaluate(() => { document.getElementById('sort').dataset.mark = 'same'; });
      jobs.find(j => j.id === 104).progress = 0.25;
      await waitPoll(); await waitPoll();
      assert.equal(await page.locator('#sort[data-mark="same"]').count(), 1, 'the open select is not replaced');
      assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.id), 'sort');
      await page.locator('h1').click();
      await waitPoll(); await waitPoll();
      assert.equal(await page.locator('#sort[data-mark="same"]').count(), 0, 'rebuilt once the select lost focus');
      assert.equal(await page.locator('details.fold[data-fold="cancelled"]').evaluate(d => d.open), true);
      await page.setViewportSize({width: 1280, height: 900});
    });

    await check('U2: the detail drawer keeps its scroll, open sections and focus across polling (1440 px and 390 px)', async () => {
      const drawerState = () => page.evaluate(() => {
        const d = document.querySelector('.drawer'), a = document.activeElement;
        return {scroll: d.scrollTop, max: d.scrollHeight - d.clientHeight, kept: d.dataset.mark === 'u2',
          open: [...d.querySelectorAll('details')].map(x => x.open),
          focus: a && d.contains(a) ? [a.tagName, a.dataset.action || '', a.dataset.op || '', a.textContent.trim()] : null,
          percent: (d.querySelector('.progress-summary .hero-status strong') || {}).textContent || null};
      });
      // Opens the drawer, opens every section, focuses a button in it and scrolls the drawer (the real scroller) down.
      const prepare = async id => {
        await openJob(id);
        await page.evaluate(() => {
          const d = document.querySelector('.drawer');
          d.querySelectorAll('details').forEach(x => { x.open = true; });
          const b = d.querySelector('.drawer-body button:not(:disabled)'); if (b) b.focus({preventScroll: true});
          d.dataset.mark = 'u2';
        });
        const max = await page.evaluate(() => { const d = document.querySelector('.drawer'); return d.scrollHeight - d.clientHeight; });
        const target = Math.min(400, max - 20);
        assert.ok(target > 100, 'the drawer scrolls: max ' + max);
        await page.evaluate(t => { document.querySelector('.drawer').scrollTop = t; }, target);
        const before = await drawerState();
        assert.ok(Math.abs(before.scroll - target) <= 2 && before.focus, JSON.stringify(before));
        return before;
      };
      for (const [width, height] of [[1440, 900], [390, 844]]) {
        await page.setViewportSize({width, height});
        await page.goto(base + '/dashboard-v2/?u2-' + width + '#videos');
        await page.waitForSelector('#search');
        // A job that does not change between polls: the same node, nothing moves.
        const still = await prepare(106);
        await waitPoll(); await waitPoll();
        const after = await drawerState();
        assert.equal(after.kept, true, width + ': an unchanged drawer is not rebuilt');
        assert.ok(Math.abs(after.scroll - still.scroll) <= 2, width + ': scrollTop ' + still.scroll + ' → ' + after.scroll);
        assert.deepEqual(after.open, still.open, width + ': open sections');
        assert.deepEqual(after.focus, still.focus, width + ': focus');
        // A scanning job whose progress changes between polls: rebuilt, the place in the drawer is kept, the new % shows.
        const job = jobs.find(j => j.id === 102);
        job.progress = 0.41;
        await waitPoll();
        const running = await prepare(102);
        assert.equal(running.percent, '41%', JSON.stringify(running));
        job.progress = 0.77;
        await waitPoll(); await waitPoll();
        const moved = await drawerState();
        assert.equal(moved.kept, false, width + ': a changed drawer is rebuilt');
        assert.equal(moved.percent, '77%', width + ': the new progress shows');
        assert.ok(Math.abs(moved.scroll - running.scroll) <= 2, width + ': scrollTop ' + running.scroll + ' → ' + moved.scroll);
        assert.deepEqual(moved.open, running.open, width + ': open sections after a rebuild');
        assert.deepEqual(moved.focus, running.focus, width + ': focus after a rebuild');
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.drawer').count(), 0);
        job.progress = 0.64;
      }
      await page.setViewportSize({width: 1280, height: 900});
    });

    await check('U3: "Hoàn tất" shows a box on every row, the reason when it cannot be ticked, and PC-only on the phone', async () => {
      const NONE = 'Không có video nào xóa video gốc hoặc lưu trữ được';
      const MOVED = 'Không thấy bản xuất trong thư mục output (đã bị dời hoặc đổi tên?)';
      const MISSING = 'Video gốc không còn trong thư mục input';
      const OTHER_VOLUME = 'Kho lưu trữ không cùng ổ đĩa với thư mục input; không lưu trữ';
      const done = () => jobs.filter(j => ['COMPLETED', 'SKIPPED'].includes(j.state));
      const saved = new Map(jobs.map(j => [j.id, {cleanup: j.cleanup, archive: j.archive}]));
      const restore = () => jobs.forEach(j => { if (saved.has(j.id)) Object.assign(j, saved.get(j.id)); });
      const openCompleted = async (tag, width) => {
        await page.setViewportSize({width, height: 900});
        await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/phone-mode')), page.goto(base + '/dashboard-v2/?u3-' + tag + '#videos')]);
        await page.waitForSelector('#search');
        await page.locator('[data-action="filter"][data-filter="completed"]').first().click();
        await page.waitForSelector('.bulk-toolbar');
      };
      const boxes = () => page.evaluate(() => [...document.querySelectorAll('#list-body .job-row')].map(r => {
        const box = r.querySelector('.video-cell input.select-job');
        return {id: Number(r.dataset.job), box: !!box, disabled: !!box && box.disabled, checked: !!box && box.checked,
          title: box ? box.title : '', label: box ? box.getAttribute('aria-label') : '',
          status: r.querySelector('.status-cell').textContent, why: (r.querySelector('.select-reason') || {}).textContent || ''};
      }));
      const toolbar = () => page.evaluate(() => {
        const t = document.querySelector('.bulk-toolbar'), b = a => t.querySelector('[data-action="' + a + '"]');
        return {label: t.querySelector('.bulk-label').textContent, pick: b('select-all').disabled, clear: b('deselect').disabled,
          clean: b('bulk-cleanup').disabled, archive: b('bulk-archive').disabled, cleanTitle: b('bulk-cleanup').title, archiveTitle: b('bulk-archive').title};
      });
      try {
        // 1. Nothing can be cleaned or archived: every row has a disabled box with the server's reason.
        for (const j of done()) {
          if (!j.cleanup && !j.archive) continue; // already cleaned/archived fixtures: the reason comes from their state
          j.cleanup = {eligible: false, reason: MOVED}; j.archive = {eligible: false, reason: MOVED};
        }
        jobs.find(j => j.id === 109).cleanup = {eligible: false, reason: MISSING};
        jobs.find(j => j.id === 109).archive = {eligible: false, reason: OTHER_VOLUME};
        await openCompleted('none', 1440);
        const rows = await boxes();
        assert.ok(rows.length >= 4, JSON.stringify(rows.map(r => r.id)));
        for (const r of rows) {
          assert.ok(r.box && r.disabled && !r.checked, 'row ' + r.id + ' has a disabled box');
          assert.ok(r.title && r.label.includes(r.title), 'row ' + r.id + ' reason in title and aria-label: ' + JSON.stringify(r));
          assert.ok(r.status.includes(r.title), 'row ' + r.id + ' shows the reason without a tooltip: ' + r.status);
        }
        const r109 = rows.find(r => r.id === 109), r105 = rows.find(r => r.id === 105);
        assert.equal(r109.title, 'Xóa video gốc: ' + MISSING + ' · Lưu trữ: ' + OTHER_VOLUME, 'archive.reason is added when it differs');
        assert.equal(r105.title, MOVED, 'one reason when cleanup and archive agree');
        assert.equal(r105.why, 'Không chọn được: ' + MOVED);
        const bar = await toolbar();
        assert.ok(bar.label.startsWith(NONE), bar.label);
        assert.match(bar.label, /\d+ thiếu bản xuất/);
        assert.match(bar.label, /1 không còn video gốc/);
        assert.deepEqual([bar.pick, bar.clear, bar.clean, bar.archive], [true, true, true, true], JSON.stringify(bar));
        // Clicking a disabled box changes nothing.
        const before = posts.length;
        await page.locator('#list-body .job-row[data-job="105"] input.select-job').click({force: true});
        assert.deepEqual(await boxes(), rows, 'a disabled box does not change');
        assert.equal((await toolbar()).label, bar.label);
        assert.equal(posts.length, before);
        await noOverflow('U3 none 1440');

        // 2. Mixed list: only selectable videos have an active box; "Chọn tối đa 50" picks cleanable OR archivable ones.
        restore();
        const template = jobs.find(j => j.id === 105);
        for (let i = 0; i < 55; i++) jobs.push({...template, id: 300 + i, job_key: 'demo-video-' + (300 + i),
          source_path: 'E:\\DungChung\\BiliFlow\\input\\Lưu trữ ' + i + '.mp4', updated_at: template.updated_at,
          cleanup: {eligible: false, reason: 'Bản ghi bỏ qua không ứng với lần duyệt hiện tại'}, archive: {eligible: true}});
        await openCompleted('mixed', 1440);
        const total = done().filter(j => (j.cleanup?.eligible || j.archive?.eligible) && !j.source_cleaned && !j.source_archived && j.source_present !== false
          && !['PENDING', 'RECYCLED'].includes(j.source_cleanup?.state)).length;
        let bar2 = await toolbar();
        assert.equal(bar2.label, '0 video đã chọn · ' + total + ' video chọn được');
        assert.equal(bar2.pick, false);
        await page.locator('[data-action="select-all"]').click();
        bar2 = await toolbar();
        assert.equal(bar2.label, '50 video đã chọn · ' + total + ' video chọn được', 'at most 50, archive-only videos included');
        assert.equal(bar2.archive, false);
        const picked = await page.evaluate(() => [...document.querySelectorAll('#list-body input.select-job:checked')].map(b => b.dataset.select));
        assert.ok(picked.length > 0 && picked.every(Boolean), 'only active boxes are ticked');
        for (const r of await boxes()) {
          const j = jobs.find(x => x.id === r.id), eligible = (j.cleanup?.eligible || j.archive?.eligible) && !j.source_cleaned && !j.source_archived && j.source_present !== false
            && !['PENDING', 'RECYCLED'].includes(j.source_cleanup?.state);
          assert.equal(r.disabled, !eligible, 'row ' + r.id + ' box follows C.eligible');
        }
        await page.locator('[data-action="deselect"]').click();
        assert.equal((await toolbar()).label, '0 video đã chọn · ' + total + ' video chọn được');
        jobs.splice(jobs.findIndex(j => j.id === 300), 55);

        // 3. Through the phone, even with selectable videos: PC-only, no preview.
        remoteMode = true;
        await openCompleted('remote', 390);
        await page.waitForFunction(() => document.querySelector('.bulk-toolbar .bulk-label').textContent === 'Xóa video gốc và lưu trữ chỉ làm trên PC');
        const bar3 = await toolbar();
        assert.deepEqual([bar3.pick, bar3.clear, bar3.clean, bar3.archive], [true, true, true, true], JSON.stringify(bar3));
        assert.match(bar3.cleanTitle, /^Chỉ làm trên PC: xóa video và video gốc, lưu trữ/);
        assert.equal(bar3.archiveTitle, bar3.cleanTitle);
        for (const r of await boxes()) {
          assert.ok(r.box && r.disabled && !r.checked, 'phone row ' + r.id);
          assert.equal(r.title, bar3.cleanTitle);
          assert.equal(r.why, '', 'no per-row reason on the phone: the toolbar says PC only');
        }
        const previews = [];
        const onRequest = r => { if (/source-(cleanup|archive)|job-delete/.test(r.url())) previews.push(r.url()); };
        page.on('request', onRequest);
        await page.locator('[data-action="bulk-cleanup"]').click({force: true});
        await page.locator('[data-action="bulk-archive"]').click({force: true});
        await page.locator('#list-body .job-row[data-job="105"] input.select-job').click({force: true});
        await page.waitForTimeout(300);
        page.off('request', onRequest);
        assert.deepEqual(previews, [], 'no preview is opened from the phone');
        assert.equal(await page.evaluate(() => document.getElementById('modal').open), false);
        await noOverflow('U3 remote 390');
      } finally {
        remoteMode = false;
        restore();
        const extra = jobs.findIndex(j => j.id === 300);
        if (extra >= 0) jobs.splice(extra, 55);
        await page.setViewportSize({width: 1280, height: 900});
      }
    });

    await check('R4-U1/R4-U2/R4-B4: "Chờ / đang xuất" rows show only ⋯; "Hoàn tất" offers "Xuất lại" with the old choices (info only while the export is in output, size choices once it left); the page behind a dialog does not scroll', async () => {
      const MOVED = 'Không thấy bản xuất trong thư mục output (đã bị dời hoặc đổi tên?)';
      const j105 = jobs.find(j => j.id === 105), saved = j105.cleanup;
      jobs.push({...jobs.find(j => j.id === 114), id: 118, job_key: 'demo-video-118', source_path: 'E:\\DungChung\\BiliFlow\\input\\Đang xuất.mp4',
        state: 'RENDERING', queue_kind: null, queue_position: null, render_progress: {state: 'RENDERING', percent: 42}});
      const rowButtons = () => page.evaluate(() => [...document.querySelectorAll('#list-body .job-row')].map(r => ({
        id: Number(r.dataset.job), buttons: [...r.querySelectorAll('.row-actions button')].map(b => b.textContent.trim())})));
      const rootOverflow = () => page.evaluate(() => getComputedStyle(document.documentElement).overflowY);
      const openTab = async (tag, tab) => {
        await page.goto(base + '/dashboard-v2/?r4u-' + tag + '#videos'); await page.waitForSelector('#search');
        await page.locator('[data-action="filter"][data-filter="' + tab + '"]').first().click();
      };
      try {
        await openTab('export', 'export');
        const rows = await rowButtons();
        assert.deepEqual(rows.map(r => r.id).sort(), [114, 118], JSON.stringify(rows));
        for (const r of rows) assert.deepEqual(r.buttons, ['⋯'], 'row ' + r.id);
        j105.cleanup = {eligible: true, kind: 'EXPORTED', reason: null, size_bytes: 1, output_name: 'demo-105-reviewed.mp4',
          output_bytes: 734003200, exported_at: '2026-10-03T10:40:00Z', skipped_at: null};
        await openTab('present', 'completed');
        const done = await rowButtons();
        assert.deepEqual(done.find(r => r.id === 105).buttons, ['Xuất lại', '⋯']);
        assert.deepEqual(done.find(r => r.id === 109).buttons, ['Duyệt cảnh', '⋯'], 'a skipped video keeps "Duyệt cảnh"');
        const before = posts.length;
        await jobButton(105, 'reexport').click();
        await page.waitForSelector('#modal[open]');
        const text = await page.locator('#modal .modal-body').innerText();
        for (const part of ['Xuất lại dùng các lựa chọn cũ:', 'output\\demo-105-reviewed.mp4', 'demo-105-reviewed.mp4.manifest.json',
          'Muốn duyệt lại và xuất với lựa chọn mới', 'Bấm ⋯ ở video này rồi bấm Duyệt cảnh']) assert.ok(text.includes(part), part);
        assert.equal(await page.locator('#modal #confirm-action').count(), 0, 'no confirm while the export is in output');
        assert.equal(await rootOverflow(), 'hidden', 'R4-B4: the page behind the dialog does not scroll');
        await page.locator('#modal .modal-foot [data-action="close-modal"]').click();
        await page.waitForFunction(() => !document.getElementById('modal').open);
        assert.notEqual(await rootOverflow(), 'hidden');
        assert.equal(posts.length, before, 'no POST');
        j105.cleanup = {eligible: false, kind: 'EXPORTED', reason: MOVED, size_bytes: 1, output_name: null, output_bytes: null, exported_at: null, skipped_at: null};
        await openTab('moved', 'completed');
        await jobButton(105, 'reexport').click();
        await page.waitForSelector('#modal[open] #export-mode');
        assert.ok((await page.locator('#modal .modal-body').innerText()).includes(MOVED + '. Xuất lại tạo bản xuất mới từ các lựa chọn cũ.'));
        assert.equal(await page.locator('#confirm-action').textContent(), 'Xuất lại');
        const sent = posts.length;
        await page.locator('#confirm-action').click();
        await page.waitForFunction(() => !document.getElementById('modal').open);
        assert.deepEqual(posts.slice(sent).filter(p => p.path.endsWith('/finalize')).map(p => [p.path, p.body]),
          [['/api/jobs/105/review/finalize', {size_mode: 'default'}]]);
      } finally {
        j105.cleanup = saved;
        jobs.splice(jobs.findIndex(j => j.id === 118), 1);
      }
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
