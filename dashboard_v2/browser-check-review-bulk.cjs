/* Review dialog browser check, R3 (bulk and export): Chromium (Playwright) on live.html and index.html with the fake
 * Control Center of review-fake-server.cjs (synthetic queues; bulk-keep / bulk-accept / finalize change its memory).
 * - the bulk and finalize bodies V2 sends equal what the real classic page sends on the same fake server
 *   (finalize without the classic "description", B4);
 * - at 1440, 1024 and 390 px, light and dark: "Giữ tất cả" / "Dùng đề xuất" with the S5 confirm and the S1 count, the
 *   dialog locked while it runs, no undo, S2 on the disabled "Xuất video", the V2 export dialog over the review dialog
 *   (Esc only closes it, a bad limit is refused), finalize only after "Xác nhận xuất video", then the dialog closes;
 * - bulk after the pending writes, Quảng cáo = 2 commands, unsupported filters, a refused bulk, the export gate after
 *   a failed write, S3 (export state and resources 1.5 s after the last write), the export state line, the demo page;
 * - R3-N1: Chrome's grouped close request replayed (one Esc never closes the review dialog under the export dialog);
 * - no finalize the user did not confirm, no blob:, no console or CSP error.
 * Run: node dashboard_v2/browser-check-review-bulk.cjs
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
if (!pw) { process.stdout.write('SKIP browser-check-review-bulk: Playwright is not installed\n'); process.exit(0); }

const F = require('./review-fake-server.cjs').create();
const {server, jobs, queues, queueFor, counters, server_state, posts} = F;
let passed = 0;
async function check(name, fn) { await fn(); passed++; process.stdout.write('OK ' + name + '\n'); }
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, label, timeout = 8000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await fn()) return; await sleep(25); }
  throw new Error('timeout: ' + label);
}
const finalizes = () => posts.filter(p => p.path.endsWith('/finalize'));
let confirmed = 0; // finalize POSTs the user confirmed (the classic page's included)
const job101 = jobs.find(j => j.id === 101), saved101 = JSON.stringify({state: job101.state, queue_kind: job101.queue_kind, review_summary: job101.review_summary});

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = 'http://127.0.0.1:' + server.address().port;
  const browser = await pw.chromium.launch({});
  const problems = [];
  const EXPECTED = [/\/review\/(decision|clear|bulk-keep|bulk-accept|finalize)$/, /\/review\/(frame|video)\?/];
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
  const state = page => page.evaluate(() => ({open: document.getElementById('review-dialog').open, modal: document.getElementById('modal').open, hash: location.hash,
    save: (document.querySelector('#review-dialog .rv-save') || {}).textContent, filter: (document.querySelector('#review-dialog .rv-chips .filter-tab.active') || {dataset: {}}).dataset.filter,
    toast: (() => { const t = document.querySelector('#review-dialog .rv-toast'); return t && !t.hidden ? t.textContent : ''; })(),
    progress: (document.querySelector('#review-dialog .rv-progress-text') || {}).textContent}));
  async function openJob(page, id, filter, demo) {
    if (await page.evaluate(() => !!document.getElementById('review-dialog') && document.getElementById('review-dialog').open).catch(() => false)) {
      await page.evaluate(() => { location.hash = '#videos'; });
      await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    }
    await page.goto(base + (demo ? '/demo/' : '/dashboard-v2/') + `#review/${id}/videos`);
    await page.waitForSelector('#review-dialog[open] article.rv-card');
    if (filter && filter !== 'pending') await chip(page, filter);
  }
  async function chip(page, filter) {
    if (['high', 'visual_ai', 'visual_logo', 'text', 'candidates'].includes(filter)) await page.selectOption('#review-dialog .rv-more', filter);
    else await page.locator(`#review-dialog .rv-chips [data-filter="${filter}"]`).click();
    await page.waitForFunction(f => { const a = document.querySelector('#review-dialog .rv-chips .filter-tab.active'), m = document.querySelector('#review-dialog .rv-more'); return (a && a.dataset.filter === f) || (m && m.value === f); }, filter);
  }
  const confirmText = page => page.locator('#review-dialog .rv-confirm[open] #rv-confirm-text').textContent();
  const fresh = () => {
    queues.delete(101); server_state.failNext = []; server_state.postDelay = 0; server_state.token = F.TOKEN;
    Object.assign(job101, JSON.parse(saved101));
  };
  const exportCount = () => counters.exportAt.length;

  try {
    await check('Payloads: bulk and finalize bodies equal the real classic page\'s on the same fake server (finalize without the classic "description", B4)', async () => {
      fresh();
      const classic = await browser.newPage({viewport: {width: 1440, height: 900}});
      classic.on('pageerror', e => problems.push('classic pageerror: ' + e.message));
      await classic.goto(base + '/classic/101');
      await classic.waitForFunction(() => typeof queue !== 'undefined' && queue && queue.items && queue.items.length === 30);
      await classic.evaluate(() => { window.confirm = () => true; window.alert = m => { (window.__alerts = window.__alerts || []).push(m); }; });
      const start = posts.length;
      const steps = ["filter='ads';bulkAccept()", "filter='pending';bulkKeep()", "decide('violence-101-0025','KEEP',false)",
        "document.getElementById('output-size-mode').value='custom';document.getElementById('custom-output-gb').value='2.5';finalizeExport()"];
      const expected = [2, 3, 4, 5];
      for (const [i, step] of steps.entries()) {
        await classic.evaluate(code => { (0, eval)(code); }, step);
        await until(() => posts.length >= start + expected[i], 'classic ' + step);
        await classic.waitForFunction(() => !busy && !pendingWrites && !exportRequestInFlight); // the classic page ignores clicks while busy
      }
      assert.deepEqual(await classic.evaluate(() => window.__alerts || []), [], 'no classic error');
      const old = posts.slice(start).map(p => [p.path, p.body]);
      await classic.close();
      fresh();
      const page = await newPage(1440, 900, 'light', 'payload');
      await openJob(page, 101, 'ads');
      const mark = posts.length;
      await page.locator('#review-dialog .rv-bulk[data-kind="bulkAccept"]').click(); await page.keyboard.press('Enter');
      await until(() => posts.length === mark + 2, 'ads accept');
      await chip(page, 'pending');
      await page.locator('#review-dialog .rv-bulk[data-kind="bulkKeep"]').click(); await page.keyboard.press('Enter');
      await until(() => posts.length === mark + 3, 'pending keep');
      await chip(page, 'violence');
      await card(page, 'violence-101-0025').locator('[data-review="decide"][data-decision="KEEP"]').click();
      await until(() => posts.length === mark + 4, 'decision');
      await until(async () => !(await page.locator('#review-dialog .rv-export').isDisabled()), 'export enabled');
      await page.locator('#review-dialog .rv-export').click();
      await page.waitForSelector('#modal[open] #export-mode');
      await page.selectOption('#export-mode', 'custom'); await page.fill('#export-gb', '2.5');
      await page.locator('#confirm-action').click();
      await until(() => posts.length === mark + 5, 'finalize');
      const v2 = posts.slice(mark).map(p => [p.path, p.body]);
      assert.deepEqual(v2.slice(0, 4), old.slice(0, 4), 'bulk and decision bodies identical');
      const {description, ...classicFinalize} = old[4][1];
      assert.equal(description, 'tối đa 2,5 GB', 'the classic page also sends its description');
      assert.deepEqual(v2[4], [old[4][0], classicFinalize], 'finalize: {size_mode, max_output_gb}, no extra field');
      confirmed += 2;
      process.stdout.write('   ' + v2.map(p => p[0].split('/').pop() + ' ' + JSON.stringify(p[1])).join(' · ') + '\n');
      await page.close();
      fresh();
    });

    for (const theme of ['light', 'dark']) for (const [width, height] of [[1440, 900], [1024, 800], [390, 844]]) {
      await check(`${width} px ${theme}: "Giữ tất cả" (S1 count, Esc / Enter, locked while it runs, no undo), S2 on "Xuất video", the export dialog over the review dialog, finalize only on confirm`, async () => {
        fresh(); server_state.postDelay = 600;
        const p = await newPage(width, height, theme);
        await openJob(p, 101, 'pending');
        assert.equal(await p.locator('#review-dialog .rv-export').isDisabled(), true, 'not READY_FOR_EDIT_PLAN yet');
        assert.equal(await p.locator('#review-dialog .rv-export').getAttribute('title'), 'Còn 4 mục chưa duyệt.');
        const start = posts.length;
        await p.locator('#review-dialog .rv-bulk[data-kind="bulkKeep"]').click();
        assert.equal(await confirmText(p), 'Giữ nguyên 4 mục chưa duyệt đang hiển thị? Thao tác này không blur hoặc cắt video.');
        await p.keyboard.press('Escape');
        await p.waitForFunction(() => !document.querySelector('#review-dialog .rv-confirm').open);
        assert.equal((await state(p)).open, true, 'Esc only cancels the confirm');
        await sleep(200); assert.equal(posts.length, start, 'cancelled: nothing sent');
        await p.locator('#review-dialog .rv-bulk[data-kind="bulkKeep"]').click();
        await p.keyboard.press('Enter');
        await until(async () => (await state(p)).save === 'Đang áp dụng…', 'running');
        const locked = await p.evaluate(() => ({buttons: [...document.querySelectorAll('#review-dialog .rv-actions button[data-review="decide"], #review-dialog .rv-bulk, #review-dialog .rv-undo, #review-dialog .rv-export')].every(b => b.disabled)}));
        assert.equal(locked.buttons, true, 'the dialog is locked while it runs');
        await p.keyboard.press('1');
        await until(() => posts.length === start + 1, 'bulk sent');
        assert.deepEqual(posts[start].body, {filter: 'pending'});
        await until(async () => (await state(p)).save !== 'Đang áp dụng…', 'done', 6000);
        await sleep(300);
        assert.equal(posts.length, start + 1, 'the key pressed while locked sent nothing');
        let st = await state(p);
        assert.equal(st.progress, '1 / 30 cảnh cần quyết định cuối');
        assert.equal(await p.locator('#review-dialog article.rv-card').count(), 4, 'the kept cards stay in "Chưa duyệt" (sticky)');
        assert.equal(await p.locator('#review-dialog .rv-undo').isDisabled(), true, 'a bulk action cannot be undone');
        assert.equal(await p.locator('#review-dialog .rv-export').getAttribute('title'), 'Còn 1 mục Cần xem thêm — chọn quyết định cuối trước khi xuất.', 'S2');
        assert.equal(await p.locator('#review-dialog .rv-next').textContent(), 'Còn 1 mục Cần xem thêm — chọn quyết định cuối trước khi xuất.');
        // The last "Cần xem thêm" gets a final decision: "Xuất video" opens the V2 export dialog over the review dialog.
        await chip(p, 'violence');
        await card(p, 'violence-101-0025').locator('[data-review="decide"][data-decision="CUT"]').click();
        await until(async () => !(await p.locator('#review-dialog .rv-export').isDisabled()), 'export enabled');
        await p.locator('#review-dialog .rv-export').click();
        await p.waitForSelector('#modal[open] #export-mode');
        st = await state(p);
        assert.deepEqual([st.open, st.modal], [true, true], 'the export dialog sits over the review dialog');
        assert.match(await p.locator('#modal .export-resources').textContent(), /Video nguồn 233\.7 MB.*Ảnh và report 17\.2 MB.*Ổ đĩa còn trống 290\.6 GB.*Preview dự kiến 40 giây · khoảng 180–260 MB/);
        assert.equal(await p.locator('#export-confirm-text').textContent(), 'Khóa các lựa chọn hiện tại và bắt đầu xuất video hoàn chỉnh (tối đa 3,5 GB)?');
        assert.deepEqual(await p.locator('#export-mode option').allTextContents(), ['Tối đa 3,5 GB (mặc định)', 'Giới hạn tùy chỉnh', 'Không giới hạn dung lượng']);
        await p.keyboard.press('Escape');
        await p.waitForFunction(() => !document.getElementById('modal').open);
        assert.equal((await state(p)).open, true, 'Esc closes only the export dialog');
        assert.equal(finalizes().length, confirmed, 'no finalize without the confirm');
        await p.locator('#review-dialog .rv-export').click();
        await p.waitForSelector('#modal[open] #export-mode');
        await p.selectOption('#export-mode', 'custom'); await p.fill('#export-gb', '0.01');
        assert.equal(await p.locator('#export-confirm-text').textContent(), 'Giới hạn tùy chỉnh phải từ 0,05 đến 1.000 GB.');
        await p.locator('#confirm-action').click();
        await p.waitForFunction(() => !document.getElementById('modal-error').hidden);
        assert.equal(await p.locator('#modal-error').textContent(), 'Giới hạn tùy chỉnh phải từ 0,05 đến 1.000 GB.');
        assert.equal(finalizes().length, confirmed, 'a bad limit is never sent');
        await p.fill('#export-gb', '2.5');
        assert.equal(await p.locator('#export-confirm-text').textContent(), 'Khóa các lựa chọn hiện tại và bắt đầu xuất video hoàn chỉnh (tối đa 2,5 GB)?');
        const layout = await p.evaluate(() => { const d = document.getElementById('review-dialog'), body = d.querySelector('.rv-body'); return d.scrollWidth > d.clientWidth + 1 || body.scrollWidth > body.clientWidth + 1 || document.documentElement.scrollWidth > window.innerWidth; });
        assert.equal(layout, false, 'no horizontal overflow');
        await p.locator('#confirm-action').click();
        await until(() => finalizes().length === confirmed + 1, 'finalize');
        confirmed++;
        assert.deepEqual(finalizes()[confirmed - 1].body, {size_mode: 'custom', max_output_gb: 2.5});
        await p.waitForFunction(() => !document.getElementById('review-dialog').open && !document.getElementById('modal').open && location.hash === '#videos', null, {timeout: 6000});
        assert.equal(await p.locator('#toast').textContent(), 'Đã xếp lệnh xuất #101. Hoàn tất chỉ hiện sau khi xuất và kiểm tra xong.');
        await p.close();
      });
    }

    const page = await newPage(1440, 900, 'light', 'main');
    await check('Bulk waits for the pending writes (count without the card just decided); Quảng cáo = visual_logo then text; Visual AI / Ứng viên phụ / nothing left: no POST; a refused bulk unlocks', async () => {
      fresh(); server_state.postDelay = 1200;
      await openJob(page, 101, 'pending');
      const start = posts.length;
      await card(page, 'adult-101-0009').locator('[data-review="decide"][data-decision="NEEDS_MORE_CONTEXT"]').click();
      await page.locator('#review-dialog .rv-bulk[data-kind="bulkKeep"]').click();
      assert.equal(await confirmText(page), 'Giữ nguyên 3 mục chưa duyệt đang hiển thị? Thao tác này không blur hoặc cắt video.', 'the card just decided is not counted');
      await page.keyboard.press('Enter');
      await until(() => posts.length === start + 2, 'decision then bulk', 8000);
      assert.deepEqual(posts.slice(start).map(p => p.path.split('/').pop()), ['decision', 'bulk-keep']);
      assert.ok(posts[start + 1].at - posts[start].at >= 1150, 'the bulk waited for the decision: ' + (posts[start + 1].at - posts[start].at) + ' ms');
      await until(async () => (await state(page)).save !== 'Đang áp dụng…', 'done', 8000);
      // Quảng cáo: two commands in order.
      fresh();
      await openJob(page, 101, 'ads');
      let mark = posts.length;
      await page.locator('#review-dialog .rv-bulk[data-kind="bulkKeep"]').click();
      assert.match(await confirmText(page), /^Giữ nguyên 3 mục chưa duyệt đang hiển thị\?/);
      await page.keyboard.press('Enter');
      await until(() => posts.length === mark + 2, 'two commands');
      assert.deepEqual(posts.slice(mark).map(p => p.body), [{filter: 'visual_logo'}, {filter: 'text'}]);
      // Unsupported filters and a filter with nothing left: the classic words, nothing sent.
      mark = posts.length;
      for (const [filter, kind, text] of [['visual_ai', 'bulkKeep', 'Bộ lọc này không hỗ trợ thao tác hàng loạt.'], ['candidates', 'bulkAccept', 'Bộ lọc này không hỗ trợ thao tác hàng loạt.'],
        ['gore', 'bulkKeep', 'Không có mục chưa duyệt trong bộ lọc này.'], ['gore', 'bulkAccept', 'Không có đề xuất chưa duyệt trong bộ lọc này.']]) {
        await chip(page, filter);
        await page.evaluate(() => { const t = document.querySelector('#review-dialog .rv-toast'); t.hidden = true; t.textContent = ''; });
        await page.locator(`#review-dialog .rv-bulk[data-kind="${kind}"]`).click();
        await until(async () => (await state(page)).toast === text, filter + ' ' + kind);
        assert.equal(await page.evaluate(() => document.querySelector('#review-dialog .rv-confirm').open), false);
      }
      await sleep(300); assert.equal(posts.length, mark, 'nothing sent');
      // A refused bulk (400): the server text, nothing applied, the dialog unlocked.
      fresh();
      await openJob(page, 101, 'pending');
      server_state.failNext = [{status: 400, body: {error: 'Bộ lọc hàng loạt không hợp lệ'}}];
      await page.locator('#review-dialog .rv-bulk[data-kind="bulkAccept"]').click(); await page.keyboard.press('Enter');
      await until(async () => (await state(page)).toast === 'Bộ lọc hàng loạt không hợp lệ', 'refused');
      assert.equal((await state(page)).progress, '5 / 30 cảnh cần quyết định cuối');
      assert.equal(await page.locator('#review-dialog .rv-bulk[data-kind="bulkKeep"]').isDisabled(), false, 'unlocked');
    });

    await check('Export gate: a write refused right before "Xuất video" → the saved queue is checked again, "Vẫn còn mục chưa có quyết định cuối cùng.", no export dialog, no finalize', async () => {
      fresh();
      const q = queueFor(101);
      for (const x of q.items) if (x.id !== 'violence-101-0025' && !x.decision) x.decision = 'KEEP';
      F.syncJob(101, (() => { const c = {total: q.items.length, pending: 0, decisions: {KEEP: 0, BLUR: 0, CUT: 0, NEEDS_MORE_CONTEXT: 0}}; for (const x of q.items) if (x.decision) c.decisions[x.decision]++; else c.pending++; q.counts = c; q.status = 'NEEDS_MORE_CONTEXT'; return q; })());
      await openJob(page, 101, 'violence');
      server_state.postDelay = 800; server_state.failNext = [{status: 400, body: {error: 'Lỗi giả: không ghi được'}}];
      await card(page, 'violence-101-0025').locator('[data-review="decide"][data-decision="KEEP"]').click();
      assert.equal(await page.locator('#review-dialog .rv-export').isDisabled(), false, 'ready locally (optimistic)');
      await page.locator('#review-dialog .rv-export').click();
      await until(async () => (await state(page)).toast === 'Vẫn còn mục chưa có quyết định cuối cùng.' || /Lỗi giả/.test((await state(page)).toast), 'gate', 8000);
      await until(async () => (await state(page)).toast === 'Vẫn còn mục chưa có quyết định cuối cùng.', 'gate message', 8000);
      assert.equal((await state(page)).modal, false, 'no export dialog');
      assert.equal(await page.locator('#review-dialog .rv-export').isDisabled(), true);
      assert.equal(finalizes().length, confirmed);
    });

    await check('S3 / 6.8: the export state and the resources reload once, 1.5 s after the last write; the export state line follows a queued export', async () => {
      fresh(); server_state.postDelay = 200;
      await openJob(page, 101, 'pending');
      await sleep(300);
      const e0 = exportCount(), r0 = counters.resourcesAt.length, start = posts.length;
      await card(page, 'text-101-0016').locator('[data-review="decide"][data-decision="KEEP"]').click();
      await card(page, 'text-101-0023').locator('[data-review="decide"][data-decision="KEEP"]').click();
      await until(() => posts.length === start + 2, 'two writes');
      const last = Date.now();
      await sleep(2600);
      const reloads = counters.exportAt.slice(e0), resources = counters.resourcesAt.slice(r0);
      assert.equal(reloads.length, 1, 'one export state reload for both writes: ' + reloads.length);
      assert.ok(reloads[0] - last >= 1300 && reloads[0] - last < 2400, 'about 1.5 s after the last write: ' + (reloads[0] - last));
      assert.equal(resources.length, 1, 'one resources reload');
      // A queued export: the line says so, "Xuất video" is off, the export state follows each poll.
      Object.assign(job101, {state: 'QUEUED', queue_kind: 'export'});
      await openJob(page, 101, 'pending');
      assert.equal(await page.locator('#review-dialog .rv-export-line').textContent(), 'Đã xếp hàng xuất video.');
      assert.equal(await page.locator('#review-dialog .rv-export').isDisabled(), true);
      const before = exportCount();
      await sleep(6500);
      assert.ok(exportCount() - before >= 2, 'the export state follows a queued export: ' + (exportCount() - before));
      Object.assign(job101, {state: 'COMPLETED', queue_kind: null});
      await until(async () => (await page.locator('#review-dialog .rv-export-line').count()) && /^Hoàn tất: output\/demo-101-reviewed\.mp4$/.test(await page.locator('#review-dialog .rv-export-line').textContent()), 'completed line', 8000);
      fresh();
      await page.keyboard.press('Escape'); await page.waitForFunction(() => !document.getElementById('review-dialog').open);
    });

    await check('R3-N1: one Esc closes only the dialog on top: a "cancel" grouped after the export dialog\'s is ignored, a review dialog the browser closes with it reopens, one closed alone returns to #videos', async () => {
      fresh();
      const q = queueFor(101);
      for (const x of q.items) if (!x.decision) x.decision = 'KEEP';
      const c = {total: q.items.length, pending: 0, decisions: {KEEP: 0, BLUR: 0, CUT: 0, NEEDS_MORE_CONTEXT: 0}};
      for (const x of q.items) c.decisions[x.decision]++;
      q.counts = c; q.status = 'READY_FOR_EDIT_PLAN'; F.syncJob(101, q);
      await openJob(page, 101, 'pending');
      const openExport = async () => {
        await page.locator('#review-dialog .rv-export').click();
        await page.waitForSelector('#modal[open] #export-mode');
      };
      // Chrome's grouped close request, replayed: "cancel" then close on #modal, then "cancel" on the review dialog.
      await openExport();
      const prevented = await page.evaluate(() => {
        const m = document.getElementById('modal'), d = document.getElementById('review-dialog');
        m.dispatchEvent(new Event('cancel', {cancelable: true})); m.close();
        const e = new Event('cancel', {cancelable: true}); d.dispatchEvent(e); return e.defaultPrevented;
      });
      await sleep(300);
      let st = await state(page);
      assert.deepEqual([prevented, st.open, st.modal, st.hash], [true, true, false, '#review/101/videos'], 'the grouped cancel is ignored');
      // The same, when the browser also closes the review dialog: it reopens at once with its cards and focus on "Xuất video".
      await sleep(500); await openExport();
      await page.evaluate(() => {
        const m = document.getElementById('modal'), d = document.getElementById('review-dialog');
        m.dispatchEvent(new Event('cancel', {cancelable: true})); m.close();
        d.dispatchEvent(new Event('cancel')); d.close();
      });
      await page.waitForFunction(() => document.getElementById('review-dialog').open);
      st = await state(page);
      assert.deepEqual([st.open, st.modal, st.hash], [true, false, '#review/101/videos'], 'reopened over the same job');
      assert.ok(await page.locator('#review-dialog article.rv-card').count() > 0, 'cards kept');
      assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.classList.contains('rv-export')), true, 'focus back on "Xuất video"');
      // Closed by the browser while the export dialog stays open: it comes back once that dialog closes (Hủy).
      await sleep(500); await openExport();
      await page.evaluate(() => document.getElementById('review-dialog').close());
      await sleep(200);
      assert.equal((await state(page)).open, false, 'not reopened over the export dialog');
      await page.locator('#modal [data-action="close-modal"]').last().click();
      await page.waitForFunction(() => document.getElementById('review-dialog').open);
      assert.equal((await state(page)).hash, '#review/101/videos');
      // A real Esc on the export dialog closes only it; the next Esc closes the review dialog.
      await openExport();
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('modal').open);
      await sleep(300);
      assert.equal((await state(page)).open, true, 'Esc closes only the export dialog');
      await sleep(300);
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('review-dialog').open && location.hash === '#videos');
      // Closed by the browser with nothing above it: the hash returns to #videos and the dashboard poll resumes.
      await page.evaluate(() => { location.hash = '#review/101/videos'; });
      await page.waitForSelector('#review-dialog[open] article.rv-card');
      await sleep(1800);
      const polls = counters.status;
      await page.evaluate(() => document.getElementById('review-dialog').close());
      await page.waitForFunction(() => location.hash === '#videos');
      await until(() => counters.status > polls, 'dashboard poll resumed', 9000);
      // "Duyệt" for the same job opens it again (the hash had followed the close).
      await page.evaluate(() => { location.hash = '#review/101/videos'; });
      await page.waitForSelector('#review-dialog[open] article.rv-card');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.getElementById('review-dialog').open);
      assert.equal(finalizes().length, confirmed, 'no finalize');
      fresh();
    });

    await check('Demo page: bulk and export in memory, recorded in "Lịch sử thao tác mẫu", no request; the dialog closes after the export is queued', async () => {
      await page.goto('about:blank');
      const demo = await newPage(1440, 900, 'dark', 'demo'), api = [];
      demo.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/') || r.method() !== 'GET') api.push(r.method() + ' ' + r.url()); });
      await openJob(demo, 101, 'pending', true);
      await demo.locator('#review-dialog .rv-bulk[data-kind="bulkKeep"]').click(); await demo.keyboard.press('Enter');
      await demo.waitForFunction(() => document.querySelector('#review-dialog .rv-progress-text').textContent === '1 / 30 cảnh cần quyết định cuối');
      await chip(demo, 'violence');
      await demo.locator('#review-dialog article.rv-card[data-item="violence-101-0025"] [data-review="decide"][data-decision="KEEP"]').click();
      await demo.waitForFunction(() => !document.querySelector('#review-dialog .rv-export').disabled);
      await demo.locator('#review-dialog .rv-export').click();
      await demo.waitForSelector('#modal[open] #export-mode');
      assert.match(await demo.locator('#modal .export-resources').textContent(), /Ổ đĩa còn trống/);
      await demo.locator('#confirm-action').click();
      await demo.waitForFunction(() => !document.getElementById('review-dialog').open && location.hash === '#videos');
      await demo.goto(base + '/demo/#settings'); await demo.waitForSelector('.log-list');
      const log = await demo.locator('.log-list').textContent();
      for (const line of ['POST /api/jobs/101/review/bulk-keep', 'POST /api/jobs/101/review/decision', 'POST /api/jobs/101/review/finalize']) assert.ok(log.includes(line), line);
      assert.deepEqual(api, [], 'no API request and no POST from the demo');
      await demo.close();
    });

    await check('No finalize the user did not confirm; no blob:, no console or CSP error, no request outside the origin', async () => {
      assert.equal(finalizes().length, confirmed, 'every finalize followed a "Xác nhận xuất video"');
      assert.deepEqual(problems, []);
    });
    await page.close();
  } finally {
    await browser.close();
    server.close();
  }
  process.stdout.write(JSON.stringify({passed, failed: 0}) + '\n');
})().catch(error => { console.error(error); process.exitCode = 1; server.close(); });
