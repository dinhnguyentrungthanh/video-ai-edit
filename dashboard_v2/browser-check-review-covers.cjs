/* Session D browser check (docs/handoff/D-preview-blurs.md): other cards' blurs over a card's image, in Chromium
 * (Playwright) on live.html with the fake Control Center (review-fake-server.cjs) and a synthetic queue for job 101:
 * - the dialogue card's report preview (no time) shows the film-long fixed text blurred (a canvas painted from the
 *   loaded image) in a dashed sky-blue box, and the watermark card that only suggests BLUR in a dotted violet box;
 * - the labels stay inside the image and never cover each other, at 390 × 844 (phone) and 1440 × 900, light and dark;
 * - "Ẩn khung" hides the drawing and its note, "Hiện khung" brings them back;
 * - on a safety card whose video cannot play, a report preview (time in its name) inside a moment of a discrete logo
 *   card shows that logo blurred, one outside does not (a card that can play seeks its video instead);
 * - BLUR on the watermark card draws it blurred on the dialogue card in place (the same card node);
 * - while the video of a safety card shows, its drawing is hidden;
 * - the only POST is that decision; no blob:, no console or CSP error, no request outside the origin.
 * Run: node dashboard_v2/browser-check-review-covers.cjs
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
if (!pw) { process.stdout.write('SKIP browser-check-review-covers: Playwright is not installed\n'); process.exit(0); }

const {server, Mock, queues, queueFor, posts, server_state, VIDEO} = require('./review-fake-server.cjs').create();
const POSTER = path.join(__dirname, 'assets', 'poster-rose.svg'), SHOTS = ['demo/shot-100.500s.png', 'demo/shot-105.000s.png', 'demo/shot-108.500s.png'];
const FRAME = [1280, 720], box = (x, y, width, height) => ({x, y, width, height});
function coverQueue() {
  const q = structuredClone(queueFor(101));
  const card = (id, extra) => ({id, category: 'text', review_kind: 'text', candidate_type: null, priority: 'medium', start_seconds: 0, end_seconds: 600, labels: [], reasons: [], max_score: .7,
    preview_images: ['demo/poster-blue.svg'], source_frame_size: FRAME, temporal_policy: null, detected_intervals: [], decision: null, decision_region_source_pixels: null, suggested_decision: 'KEEP', ...extra});
  q.items = [
    card('wm', {candidate_type: 'repeated_watermark', temporal_policy: 'discrete_detected_intervals', detected_intervals: [{start_seconds: 0, end_seconds: 600}], labels: ['WWW.EXAMPLE.COM'],
      suggested_decision: 'BLUR', suggested_region_source_pixels: box(552, 643, 176, 31)}),
    card('top', {review_kind: 'logo_overlay', candidate_type: 'persistent_overlay', labels: ['EXAMPLE.COM'], suggested_decision: 'BLUR', suggested_region_source_pixels: box(837, 44, 319, 47),
      decision: 'BLUR', decision_region_source_pixels: box(837, 44, 319, 47)}),
    card('dlg', {start_seconds: 100, end_seconds: 110, labels: ['Ngày mai chúng ta đi sớm'], suggested_region_source_pixels: box(300, 560, 680, 60)}),
    card('mom', {category: 'visual_logo', review_kind: 'logo_overlay', start_seconds: 100, end_seconds: 110, temporal_policy: 'discrete_detected_intervals',
      detected_intervals: [{start_seconds: 100, end_seconds: 103}, {start_seconds: 108, end_seconds: 110}], suggested_decision: 'BLUR', suggested_region_source_pixels: box(60, 300, 200, 80),
      decision: 'BLUR', decision_region_source_pixels: box(60, 300, 200, 80)}),
    card('fight', {category: 'violence', review_kind: 'violence', start_seconds: 100, end_seconds: 110, preview_images: SHOTS.slice(), suggested_decision: 'KEEP'}),
    card('brawl', {category: 'violence', review_kind: 'violence', start_seconds: 120, end_seconds: 130, preview_images: ['demo/poster-rose.svg'], suggested_decision: 'KEEP'}),
  ];
  q.advisory_items = [];
  q.source = {...q.source, duration_seconds: 600};
  q.status = 'REVIEW_REQUIRED';
  return q;
}

let passed = 0;
async function check(name, fn) { await fn(); passed++; process.stdout.write('OK ' + name + '\n'); }

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = 'http://127.0.0.1:' + server.address().port, browser = await pw.chromium.launch({}), problems = [];
  const EXPECTED_FAILURES = [/\/review\/(frame|video)\?/];
  const newPage = async (width, height, theme) => {
    const page = await browser.newPage({viewport: {width, height}});
    await page.addInitScript(t => { try { localStorage.setItem('biliflow-v2-theme', t); } catch (_) { /* ignore */ } }, theme);
    page.on('pageerror', e => problems.push(width + ' ' + theme + ' pageerror: ' + e.message));
    page.on('console', m => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) problems.push(width + ' ' + theme + ' console: ' + m.text()); });
    page.on('response', r => { if (r.status() >= 400 && !EXPECTED_FAILURES.some(re => re.test(r.url()))) problems.push(width + ' ' + theme + ' HTTP ' + r.status() + ' ' + r.url()); });
    page.on('request', r => { const u = r.url(); if (u.startsWith('blob:') || !(u.startsWith(base) || u.startsWith('data:'))) problems.push('request ' + u); });
    // The report previews of the violence card are named with their time (as review_workflow writes them); the poster stands in.
    await page.route(/\/media\/demo%2Fshot-[\d.]+s\.png$/, route => route.fulfill({path: POSTER, contentType: 'image/svg+xml'}));
    return page;
  };
  const open = async page => {
    const q = coverQueue();
    queues.set(101, q);
    server_state.evidence.delete('fight');
    await page.goto(base + '/dashboard-v2/#review/101/videos');
    await page.waitForSelector('#review-dialog[open] article.rv-card[data-item="dlg"]');
  };
  // The drawing on one card: its layer, every box (owner, look, label, painted canvas) and the image box.
  const drawing = (page, id) => page.evaluate(id => {
    const el = document.querySelector(`#review-dialog article.rv-card[data-item="${id}"]`), art = el.querySelector('.rv-art'), layer = el.querySelector('.rv-covers'), note = el.querySelector('.rv-cover-note');
    const painted = c => { if (!c || c.dataset.drawn !== '1' || !c.width) return false; const d = c.getContext('2d').getImageData(Math.floor(c.width / 2), Math.floor(c.height / 2), 1, 1).data; return d[3] === 255; };
    const rect = n => { const r = n.getBoundingClientRect(); return {left: r.left, top: r.top, right: r.right, bottom: r.bottom}; };
    return {t: layer ? layer.dataset.t : null, shown: !!layer && getComputedStyle(layer).display !== 'none', note: note && !note.hidden && getComputedStyle(note).display !== 'none' ? note.textContent : '', art: rect(art),
      covers: [...el.querySelectorAll('.rv-cover')].map(c => { const s = getComputedStyle(c), em = c.querySelector('em');
        return {owner: c.dataset.owner, state: c.classList.contains('applied') ? 'applied' : 'pending', line: s.outlineStyle + ' ' + s.outlineColor, label: em ? em.textContent : '', em: em ? rect(em) : null, painted: painted(c.querySelector('canvas'))}; })};
  }, id);
  const meets = (a, b) => a.left < b.right - .5 && b.left < a.right - .5 && a.top < b.bottom - .5 && b.top < a.bottom - .5;
  const labelsFit = (d, label) => {
    const ems = d.covers.filter(c => c.em).map(c => c.em);
    for (const r of ems) assert.ok(r.left >= d.art.left - 1 && r.right <= d.art.right + 1 && r.top >= d.art.top - 1 && r.bottom <= d.art.bottom + 1, label + ': label inside the image ' + JSON.stringify(r));
    for (let i = 0; i < ems.length; i++) for (let k = i + 1; k < ems.length; k++) assert.ok(!meets(ems[i], ems[k]), label + ': labels apart');
  };
  const SKY = 'dashed rgb(56, 189, 248)', VIOLET = 'dotted rgb(192, 132, 252)';
  // A source the evidence says is missing makes the job's one player unavailable for every card: the video check runs
  // on a page that never asked for that card.
  const videoCheck = page => check('1440 px: while the video of a violence card shows, its drawing is hidden', async () => {
    const at = '#review-dialog article.rv-card[data-item="brawl"]';
    await page.locator(at + ' .rv-name').click();
    await page.waitForSelector(at + ' [data-review="play"]');
    await page.waitForFunction(at => document.querySelector(at + ' .rv-cover[data-owner="wm"]'), at);
    assert.equal((await drawing(page, 'brawl')).shown, true);
    await page.locator(at + ' [data-review="play"]').click();
    await page.waitForSelector(at + ' .rv-art.revealed');
    assert.equal((await drawing(page, 'brawl')).shown, false);
    await page.locator(at + ' [data-review="play"]').click();
  });
  try {
    for (const [width, height, theme] of [[390, 844, 'light'], [390, 844, 'dark'], [1440, 900, 'light']]) {
      const page = await newPage(width, height, theme);
      await check(`${width} px ${theme}: the dialogue card shows the film-long fixed text blurred (sky-blue, dashed) and the suggested watermark (violet, dotted); labels inside the image, apart`, async () => {
        await open(page);
        await page.waitForFunction(() => { const c = document.querySelector('#review-dialog article.rv-card[data-item="dlg"] .rv-cover.applied canvas'); return c && c.dataset.drawn === '1'; });
        const d = await drawing(page, 'dlg');
        assert.equal(d.t, '', 'the report preview has no time');
        assert.deepEqual(d.covers.map(c => [c.owner, c.state]), [['wm', 'pending'], ['top', 'applied']], 'the moments of the logo card do not hold the whole card');
        assert.equal(d.covers[1].line, SKY); assert.equal(d.covers[0].line, VIOLET);
        assert.ok(d.covers[1].painted, 'the blurred copy is painted'); assert.ok(d.covers.every(c => c.label), 'every box has its label here');
        assert.match(d.covers[0].label, /^đề xuất.*chưa duyệt$/); assert.match(d.covers[1].label, /^đã làm mờ/);
        assert.match(d.note, /^Vùng mờ viền xanh: 1 vùng thẻ khác đã làm mờ suốt đoạn này — bản xuất cũng làm mờ\. Viền tím chấm: 1 vùng thẻ khác đề xuất làm mờ, chưa duyệt\. Còn 1 vùng/);
        labelsFit(d, width + ' ' + theme);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'no sideways scroll');
      });
      if (width === 1440) { if (VIDEO) await videoCheck(page); else process.stdout.write('SKIP video check: ffmpeg could not make the test clip\n'); }
      if (width !== 390 || theme !== 'light') { await page.close(); continue; }
      await check('390 px: "Ẩn khung" hides the drawing and its note, "Hiện khung" brings them back', async () => {
        const frames = page.locator('#review-dialog article.rv-card[data-item="dlg"] [data-review="frames"]');
        await frames.click();
        let d = await drawing(page, 'dlg');
        assert.equal(d.shown, false); assert.equal(d.note, '');
        await frames.click();
        d = await drawing(page, 'dlg');
        assert.equal(d.shown, true); assert.ok(d.note);
      });
      await check('390 px: on a card whose video cannot play, a report preview inside a moment of the discrete logo card shows it blurred; one outside does not', async () => {
        const at = '#review-dialog article.rv-card[data-item="fight"]';
        await page.waitForFunction(at => { const c = document.querySelector(at + ' .rv-cover[data-owner="mom"] canvas'); return c && c.dataset.drawn === '1'; }, at);
        let d = await drawing(page, 'fight');
        assert.equal(d.t, '100.5', 'the first preview is at 1:40.5');
        assert.deepEqual(d.covers.map(c => [c.owner, c.state]), [['wm', 'pending'], ['mom', 'applied']], 'the red box it borrows (the fixed text) is not a cover');
        // Its source cannot be read: no evidence frames, no video; the strip keeps the report previews.
        server_state.evidence.set('fight', {...Mock.reviewEvidence(queues.get(101).items.find(x => x.id === 'fight')), frames: [], video: {available: false, mime: null, reason: 'source_missing'}});
        await page.locator(at + ' .rv-name').click(); // select: its evidence rebuilds the strip
        await page.waitForFunction(at => document.querySelectorAll(at + ' .rv-thumb[data-path]').length === 3, at);
        for (const [t, want] of [['105', false], ['108.5', true], ['100.5', true]]) {
          await page.waitForFunction(([at, t]) => { const i = document.querySelector(`${at} .rv-thumb[data-t="${t}"] img`); return i && i.getAttribute('src'); }, [at, t]);
          await page.locator(`${at} .rv-thumb[data-t="${t}"]`).click();
          await page.waitForFunction(([at, t]) => document.querySelector(at + ' .rv-covers').dataset.t === t, [at, t]);
          if (want) await page.waitForFunction(at => { const c = document.querySelector(at + ' .rv-cover[data-owner="mom"] canvas'); return c && c.dataset.drawn === '1'; }, at);
          d = await drawing(page, 'fight');
          assert.equal(d.covers.some(c => c.owner === 'mom'), want, 'logo at ' + t);
          assert.equal(await page.evaluate(at => document.querySelector(at + ' .rv-art img').getAttribute('src').includes(encodeURIComponent('shot-')), at), true);
          labelsFit(d, 'preview ' + t);
        }
        assert.match(d.note, /lúc 1:40\.5/);
      });
      await check('390 px: BLUR on the watermark card draws it blurred on the dialogue card in place (same card node); the only POST is that decision', async () => {
        const before = posts.length;
        await page.evaluate(() => { document.querySelector('#review-dialog article.rv-card[data-item="dlg"]').__marker = 1; });
        await page.locator('#review-dialog article.rv-card[data-item="wm"] .rv-actions [data-review="decide"][data-decision="BLUR"]').click();
        if (await page.evaluate(() => document.querySelector('#review-dialog .rv-confirm').open)) await page.locator('#review-dialog .rv-confirm [data-confirm="yes"]').click();
        await page.waitForFunction(() => { const c = document.querySelector('#review-dialog article.rv-card[data-item="dlg"] .rv-cover.applied[data-owner="wm"] canvas'); return c && c.dataset.drawn === '1'; });
        const d = await drawing(page, 'dlg'), wm = d.covers.find(c => c.owner === 'wm');
        assert.equal(wm.line, SKY); assert.ok(wm.painted);
        assert.equal(await page.evaluate(() => document.querySelector('#review-dialog article.rv-card[data-item="dlg"]').__marker), 1, 'patched, not rebuilt');
        assert.deepEqual(posts.slice(before).map(p => [p.path, p.body.id, p.body.decision]), [['/api/jobs/101/review/decision', 'wm', 'BLUR']]);
      });
      await page.close();
    }
    await check('No POST but the one decision, no blob:, no console or CSP error, no request outside the origin', async () => {
      assert.deepEqual(problems, []);
      assert.equal(posts.length, 1);
    });
  } finally {
    await browser.close();
    server.close();
  }
  process.stdout.write(JSON.stringify({passed, failed: 0}) + '\n');
})().catch(error => { process.stderr.write((error && error.stack || String(error)) + '\n'); process.exit(1); });
