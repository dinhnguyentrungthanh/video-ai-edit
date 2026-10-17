/* "Dựng lại danh sách duyệt (giữ quyết định)" browser check (docs/handoff/D-rebuild-keep.md): Chromium (Playwright) on
 * live.html with the fake Control Center (review-fake-server.cjs: synthetic jobs and queues, no real backend):
 * - the job drawer shows the button right after "Chạy lại kiểm tra" (1440 × 900 light on a video to review, 390 × 844
 *   dark on one ready to export), no sideways scroll;
 * - it opens a confirmation that lists the four points of contracts.js REBUILD_CONFIRM; "Hủy" sends nothing;
 * - "Xác nhận" sends one POST /api/jobs/101/rebuild-review with the session token and an empty JSON body, the toast
 *   says how many decisions were kept, the video leaves review for the scan queue;
 * - a SKIPPED video shows it disabled with the reason;
 * - the review dialog of a rebuilt list shows the carry note (review-core.js carryNote), also at 390 px.
 * No blob:, no console error, no request outside the origin.
 * Run: node dashboard_v2/browser-check-rebuild.cjs
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
if (!pw) { process.stdout.write('SKIP browser-check-rebuild: Playwright is not installed\n'); process.exit(0); }

const C = require('./contracts.js');
const R = require('./review-core.js');
const {server, jobs, queueFor, posts, server_state} = require('./review-fake-server.cjs').create();
const LABEL = 'Dựng lại danh sách duyệt (giữ quyết định)';

let passed = 0;
async function check(name, fn) { await fn(); passed++; process.stdout.write('OK ' + name + '\n'); }

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = 'http://127.0.0.1:' + server.address().port, browser = await pw.chromium.launch({}), problems = [];
  const newPage = async (width, height, theme) => {
    const page = await browser.newPage({viewport: {width, height}});
    await page.addInitScript(t => { try { localStorage.setItem('biliflow-v2-theme', t); } catch (_) { /* ignore */ } }, theme);
    page.on('pageerror', e => problems.push(width + ' pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) problems.push(width + ' console: ' + m.text()); });
    page.on('request', r => { const u = r.url(); if (u.startsWith('blob:') || !(u.startsWith(base) || u.startsWith('data:'))) problems.push('request ' + u); });
    return page;
  };
  const noOverflow = async (page, label) => {
    const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    assert.ok(sw <= iw, label + ': scrollWidth ' + sw + ' > ' + iw);
  };
  const openJob = async (page, id) => {
    if (await page.locator('.drawer').count()) { await page.keyboard.press('Escape'); await page.waitForFunction(() => !document.querySelector('.drawer')); }
    await page.goto(base + '/dashboard-v2/#videos');
    await page.waitForSelector('#search');
    await page.locator('[data-action="filter"][data-filter="all"]').first().click();
    await page.fill('#search', String(id));
    await page.locator('[data-action="detail"][data-id="' + id + '"]').first().click();
    await page.waitForSelector('.drawer');
    await page.evaluate(() => document.querySelector('.drawer details.more-actions')?.setAttribute('open', ''));
  };
  const drawerOps = page => page.$$eval('.drawer [data-action="job"]', els => els.map(el => ({op: el.dataset.op, label: el.textContent, disabled: el.disabled, title: el.title})));
  try {
    for (const [width, height, theme] of [[1440, 900, 'light'], [390, 844, 'dark']]) {
      const page = await newPage(width, height, theme), id = width === 1440 ? 101 : 107; // 101 is queued below
      await check(`${width} px ${theme}: the button sits right after "Chạy lại kiểm tra"; its confirmation lists the four points; "Hủy" sends nothing`, async () => {
        await openJob(page, id);
        const ops = await drawerOps(page), at = ops.findIndex(x => x.op === 'rebuild');
        assert.ok(at > 0, JSON.stringify(ops.map(x => x.op)));
        assert.equal(ops[at - 1].op, 'rerun');
        assert.deepEqual([ops[at].label, ops[at].disabled], [LABEL, false]);
        await noOverflow(page, width + ' drawer');
        const before = posts.length;
        await page.locator('.drawer [data-op="rebuild"]').click();
        await page.waitForSelector('#modal[open]');
        assert.equal(await page.locator('#modal-title').textContent(), LABEL);
        assert.deepEqual(await page.$$eval('#modal li', els => els.map(el => el.textContent)), C.REBUILD_CONFIRM);
        assert.equal(await page.locator('#confirm-action').textContent(), LABEL);
        await noOverflow(page, width + ' dialog');
        await page.locator('#modal .modal-foot [data-action="close-modal"]').click();
        await page.waitForFunction(() => !document.getElementById('modal').open);
        assert.equal(posts.length, before, 'Hủy sends nothing');
      });
      if (width === 1440) {
        await check('1440 px: "Xác nhận" sends one POST with the token and {}; the toast gives the kept decisions; the video goes to the scan queue', async () => {
          const kept = queueFor(101).items.filter(x => x.decision).length, revision = jobs.find(x => x.id === 101).active_revision;
          const before = posts.length;
          await page.locator('.drawer [data-op="rebuild"]').click();
          await page.waitForSelector('#modal[open]');
          await page.locator('#confirm-action').click();
          await page.waitForFunction(() => !document.getElementById('modal').open);
          const sent = posts.slice(before);
          assert.equal(sent.length, 1);
          assert.deepEqual([sent[0].path, sent[0].body, sent[0].token, /^application\/json/.test(sent[0].type)], ['/api/jobs/101/rebuild-review', {}, server_state.token, true]);
          await page.waitForFunction(() => !document.getElementById('toast').hidden);
          assert.equal(await page.locator('#toast').textContent(), C.rebuildSent({rebuild: {decisions: kept, from_revision: revision}}));
          await page.waitForFunction(() => !document.querySelector('.drawer [data-op="rebuild"]'), null, {timeout: 15000});
          assert.equal(jobs.find(x => x.id === 101).state, 'QUEUED');
        });
        await check('1440 px: a skipped video shows the button disabled with "Mở lại để xuất" first', async () => {
          await openJob(page, 109);
          const op = (await drawerOps(page)).find(x => x.op === 'rebuild');
          assert.deepEqual([op.disabled, op.title], [true, 'Video đã bỏ qua: bấm “Mở lại để xuất” trước.']);
        });
      }
      await check(`${width} px ${theme}: the review dialog of a rebuilt list says what was kept, what waits and why`, async () => {
        const q = queueFor(103);
        q.decision_carry = {previous_decisions: 4, carried: 3, restored_unchanged: 1, conflicts: 0, from_revision: 2,
          from_queue: 'reports/jobs/demo/review-queue.json', restored_reasons: {conflict: 0, not_wider: 1, moments_outside: 0, no_match: 0}};
        await page.goto(base + '/dashboard-v2/#review/103/videos');
        await page.waitForSelector('#review-dialog[open] .rv-carry');
        assert.equal(await page.locator('#review-dialog .rv-carry').textContent(), R.carryNote(q));
        assert.match(R.carryNote(q), /^Đã mang 3 \/ 4 quyết định của revision 2 sang thẻ mới\. 1 quyết định .* \(1 vì thẻ mới cho thấy nhiều hơn đoạn đã duyệt\)\./);
        await noOverflow(page, width + ' review');
        delete q.decision_carry;
      });
      await page.close();
    }
    assert.deepEqual(problems, []);
    process.stdout.write(JSON.stringify({passed, failed: 0}) + '\n');
  } finally {
    await browser.close();
    server.close();
  }
})().catch(error => { process.stderr.write((error && error.stack || String(error)) + '\n'); process.exit(1); });
