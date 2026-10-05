/* Review dialog through the real phone listener, R4.2. Started by tests/test_dashboard_v2_review.py
 * (ReviewR4PhoneListener): the real Control Center handlers on a temporary root with a synthetic queue and a
 * synthetic VP8 clip, the phone listener on 127.0.0.1 (never the Wi-Fi address). Chromium as a phone (390 × 844, touch):
 * - the code form gives the cookie and V2 opens; "Duyệt cảnh" in the detail drawer opens the review dialog;
 * - frames load through the listener (the dialog counts at most 2 at once; the test counts on the server too);
 *   the clip plays (the test checks the Range requests and the 206 answers on the server);
 * - a decision, its undo and "Giữ tất cả" are sent, each POST path is one of PHONE_ALLOWED_POSTS (given by the test);
 *   "Xuất video" is never pressed; the dialog shows no PC-only action and keeps its 44 px targets;
 * - no blob:, no console or CSP error, no request outside the listener.
 * Prints one JSON line for the test. Alone (without the test's environment) it prints SKIP.
 */
'use strict';
const path = require('path');
const assert = require('assert/strict');
const {execSync} = require('child_process');

const base = process.env.BILIFLOW_PHONE_BASE, code = process.env.BILIFLOW_PHONE_CODE, job = Number(process.env.BILIFLOW_PHONE_JOB);
if (!base || !code || !job) { process.stdout.write('SKIP browser-check-review-phone: run it through tests.test_dashboard_v2_review.ReviewR4PhoneListener\n'); process.exit(0); }
function loadPlaywright() {
  try { return require('playwright'); } catch (_) { /* fall through */ }
  try { return require(path.join(execSync('npm root -g', {encoding: 'utf8'}).trim(), 'playwright')); } catch (_) { return null; }
}
const pw = loadPlaywright();
if (!pw) { process.stdout.write('SKIP browser-check-review-phone: Playwright is not installed\n'); process.exit(0); }
const allowed = JSON.parse(process.env.BILIFLOW_PHONE_POSTS || '[]').map(p => new RegExp('^' + p + '$'));
const {audit} = require('./review-layout-audit.cjs');
const PC_ONLY_WORDS = ['Xóa video gốc', 'Xóa video', 'Dọn video mất gốc', 'Dọn video gốc', 'Lưu trữ', 'Khôi phục bản xuất', 'Kiểm tra lại Thùng rác', 'Thùng rác'];

let passed = 0;
async function check(name, fn) { await fn(); passed++; process.stderr.write('OK ' + name + '\n'); }

(async () => {
  const browser = await pw.chromium.launch({});
  const problems = [], posts = [], media = [];
  const page = await browser.newPage({viewport: {width: 390, height: 844}, hasTouch: true, isMobile: true});
  page.on('pageerror', e => problems.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) problems.push('console: ' + m.text()); });
  page.on('request', r => {
    const u = r.url();
    if (u.startsWith('blob:') || !(u.startsWith(base) || u.startsWith('data:'))) problems.push('request ' + u);
    if (r.method() === 'POST') posts.push(new URL(u).pathname);
    if (/\/review\/(frame|video)\?/.test(u)) media.push(new URL(u).pathname.split('/').pop());
  });
  const dialog = () => page.locator('#review-dialog');
  const saved = () => page.waitForFunction(() => { const s = document.querySelector('#review-dialog .rv-save'); return s && s.textContent === 'Đã lưu'; }, null, {timeout: 15000});
  try {
    await check('The code form gives the cookie; V2 opens through the listener', async () => {
      const first = await page.goto(base + '/');
      assert.equal(first.status(), 401);
      await page.fill('input[name="code"]', code);
      await Promise.all([page.waitForURL(base + '/dashboard-v2/**'), page.locator('button[type="submit"]').tap()]);
      await page.waitForSelector('#main h1');
      const cookies = await page.context().cookies(base);
      assert.equal(cookies.length, 1);
      assert.deepEqual([cookies[0].httpOnly, cookies[0].sameSite], [true, 'Strict']);
      assert.deepEqual(posts, ['/phone-login']);
      posts.length = 0; // from here on: the dialog's writes
    });

    await check('"Duyệt cảnh" in the detail drawer opens the review dialog over Video; frames load at most 2 at once', async () => {
      await page.evaluate(() => { location.hash = '#videos'; });
      await page.waitForSelector('#search');
      await page.locator(`[data-action="detail"][data-id="${job}"]`).first().tap();
      await page.waitForSelector('.drawer [data-op="review"]');
      await page.locator('.drawer [data-op="review"]').first().tap();
      await page.waitForSelector('#review-dialog[open] article.rv-card');
      assert.equal(await page.evaluate(() => location.hash), `#review/${job}/videos`);
      await page.waitForFunction(() => document.querySelectorAll('#review-dialog .rv-art.loaded, #review-dialog .rv-thumb.loaded').length >= 6, null, {timeout: 30000});
      for (let i = 0; i < 4; i++) {
        await page.evaluate(() => { const b = document.querySelector('#review-dialog .rv-body'); b.scrollTop = b.scrollHeight; });
        await page.waitForTimeout(700);
      }
      const stats = await page.evaluate(() => window.BFReviewStats);
      assert.ok(stats.maxImages >= 1 && stats.maxImages <= 2, 'images at once: ' + stats.maxImages);
      assert.ok(media.filter(x => x === 'frame').length >= 3, 'frames through the listener');
    });

    await check('The dialog shows no PC-only action, keeps 44 px targets and text ≥ 12 px, and links the classic page of the listener', async () => {
      await page.evaluate(() => { const b = document.querySelector('#review-dialog .rv-body'); b.scrollTop = 0; });
      const text = await dialog().textContent();
      for (const word of PC_ONLY_WORDS) assert.ok(!text.includes(word), 'no PC-only action: ' + word);
      assert.equal(await dialog().locator('[data-op]').count(), 0);
      const result = await page.evaluate(audit, {touch: true});
      assert.deepEqual([result.outside, result.small, result.tiny, result.clipped, result.overlap, result.page], [[], [], [], [], [], false], JSON.stringify(result));
      assert.equal(await dialog().locator('a.rv-old').getAttribute('href'), `/review/${job}?from=v2&view=videos`);
    });

    await check('▶ plays the clip in the card through the listener', async () => {
      await dialog().locator('article.rv-card [data-review="play"]').first().tap();
      // A short range may already have stopped at its end: played means frames were decoded past its start.
      await page.waitForFunction(() => { const v = document.querySelector('#review-dialog video'); return v && v.readyState >= 2 && window.BFReviewStats.plays >= 1 && v.currentTime > 0.2; }, null, {timeout: 20000});
      if (!(await page.evaluate(() => document.querySelector('#review-dialog video').paused))) {
        await dialog().locator('article.rv-card [data-review="play"]').first().tap();
        await page.waitForFunction(() => document.querySelector('#review-dialog video').paused);
      }
      assert.ok(media.includes('video'));
    });

    await check('A decision, its undo and "Giữ tất cả" go through the listener; every POST is in PHONE_ALLOWED_POSTS', async () => {
      const first = dialog().locator('article.rv-card').first(), id = await first.getAttribute('data-item');
      await first.locator('[data-review="decide"][data-decision="KEEP"]').tap();
      if (await page.locator('#review-dialog .rv-confirm[open]').count()) await page.locator('#review-dialog .rv-confirm [data-confirm="yes"]').tap();
      await saved();
      await dialog().locator('.rv-undo').tap();
      await saved();
      assert.equal(await page.evaluate(i => document.querySelector(`#review-dialog article.rv-card[data-item="${i}"] .rv-status`).textContent, id), 'Chưa duyệt');
      await dialog().locator('.rv-bulk[data-kind="bulkKeep"]').tap();
      await page.waitForSelector('#review-dialog .rv-confirm[open]');
      await page.locator('#review-dialog .rv-confirm [data-confirm="yes"]').tap();
      await page.waitForFunction(() => /^0 \//.test(document.querySelector('#review-dialog .rv-progress-text').textContent) || !document.querySelector('#review-dialog .rv-bulk').disabled, null, {timeout: 15000});
      await page.waitForFunction(() => document.querySelector('#review-dialog .rv-save').textContent !== 'Đang áp dụng…', null, {timeout: 15000});
      assert.ok(posts.length >= 3, 'three writes: ' + posts.join(', '));
      for (const p of posts) assert.ok(allowed.some(re => re.test(p)), 'in PHONE_ALLOWED_POSTS: ' + p);
      assert.ok(!posts.some(p => p.endsWith('/finalize')), 'never finalize');
    });

    await check('× closes the dialog back to Video; no blob:, no console or CSP error, no request outside the listener', async () => {
      await dialog().locator('.rv-head [data-review="close"]').tap();
      await page.waitForFunction(() => !document.getElementById('review-dialog').open && location.hash === '#videos');
      assert.deepEqual(problems, []);
    });
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify({passed, failed: 0, posts, frames: media.filter(x => x === 'frame').length, videos: media.filter(x => x === 'video').length}) + '\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
