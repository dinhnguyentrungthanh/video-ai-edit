/* Session D: screenshots of the drawing of other cards' blurs on the test Control Center of copy_jobs.py (GET only).
 *   node shoot.cjs <base http://127.0.0.1:port> <test root> <screenshot folder>
 * For every copied job (job-<id>) it picks, from the copied queue, a dialogue card (a text card the user kept, its red
 * box in the lower half) that a blurred watermark card covers, and the fixed text card at the top that is blurred
 * (the narrowest), and shoots each card's picture with the lines under it at 390 × 844 (light, dark, "Ẩn khung") and
 * 1440 × 900, and the whole dialog at 1440 × 900 once a job. It checks that the page
 * sends no POST, that every blurred box is painted, that labels stay inside the image and apart, and that the queue
 * files keep their SHA-256; the result goes to shoot-result.json beside the pictures. The pictures show real frames:
 * keep them in <install>/reports/evidence (not in Git).
 */
'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const pw = require('playwright');

const [base, root, out] = process.argv.slice(2);
if (!base || !root || !out || /:8765\b/.test(base)) { process.stderr.write('usage: node shoot.cjs <test base, never :8765> <test root> <out folder>\n'); process.exit(2); }
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const queueFile = key => path.join(root, 'reports', 'jobs', key, 'review-queue.json');

function targets(queue) {
  const items = queue.items, region = x => x.decision_region_source_pixels || x.suggested_region_source_pixels;
  const blurred = items.filter(x => x.decision === 'BLUR' && region(x) && region(x) !== 'FULL_FRAME');
  const watermark = blurred.find(x => x.candidate_type === 'repeated_watermark');
  const tops = blurred.filter(x => x.candidate_type === 'persistent_overlay' && region(x).y < Number(x.source_frame_size[1]) / 4).sort((a, b) => region(a).width - region(b).width);
  const dialogue = items.filter(x => x.category === 'text' && x.candidate_type == null && x.decision === 'KEEP' && region(x) && region(x).y > Number(x.source_frame_size[1]) / 2
    && (x.preview_images || []).length && (!watermark || (x.start_seconds < watermark.end_seconds && x.end_seconds > watermark.start_seconds))).sort((a, b) => a.start_seconds - b.start_seconds);
  return [['dialogue', dialogue[0]], ['top', tops[0]]].filter(([, x]) => x);
}

(async () => {
  fs.mkdirSync(out, {recursive: true});
  const browser = await pw.chromium.launch({}), result = {base, shots: [], posts: [], problems: [], queues: {}};
  const status = await (await fetch(base + '/api/status')).json();
  const jobs = (status.jobs || []).filter(j => /^job-\d+$/.test(j.job_key));
  for (const job of jobs) result.queues[job.job_key] = {before: sha(queueFile(job.job_key))};
  const page = async (width, height, theme) => {
    const p = await browser.newPage({viewport: {width, height}});
    await p.addInitScript(t => { try { localStorage.setItem('biliflow-v2-theme', t); } catch (_) { /* ignore */ } }, theme);
    p.on('request', r => { if (r.method() !== 'GET') result.posts.push(r.method() + ' ' + r.url()); });
    p.on('pageerror', e => result.problems.push(width + ' ' + theme + ' pageerror: ' + e.message));
    p.on('console', m => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) result.problems.push(width + ' ' + theme + ' console: ' + m.text()); });
    return p;
  };
  const openCard = async (p, job, id) => {
    await p.goto(base + `/dashboard-v2/#review/${job.id}/videos`);
    await p.waitForSelector('#review-dialog[open] .rv-chips');
    await p.locator('#review-dialog .rv-chips [data-filter="all"]').click();
    const card = p.locator(`#review-dialog article.rv-card[data-item="${id}"]`);
    for (let i = 0; i < 20 && !(await card.count()); i++) { await p.evaluate(() => { const b = document.querySelector('#review-dialog .rv-body'); b.scrollTop = b.scrollHeight; }); await p.waitForTimeout(300); }
    await card.scrollIntoViewIfNeeded();
    await p.waitForFunction(id => { const el = document.querySelector(`#review-dialog article.rv-card[data-item="${id}"]`), art = el && el.querySelector('.rv-art');
      return art && art.classList.contains('loaded') && [...el.querySelectorAll('.rv-cover-blur')].every(c => c.dataset.drawn === '1'); }, id, {timeout: 20000});
    return card;
  };
  const describe = (p, id) => p.evaluate(id => {
    const el = document.querySelector(`#review-dialog article.rv-card[data-item="${id}"]`), art = el.querySelector('.rv-art').getBoundingClientRect(), layer = el.querySelector('.rv-covers');
    const ems = [...el.querySelectorAll('.rv-cover em')].map(e => e.getBoundingClientRect());
    const inside = ems.every(r => r.left >= art.left - 1 && r.right <= art.right + 1 && r.top >= art.top - 1 && r.bottom <= art.bottom + 1);
    const apart = ems.every((a, i) => ems.every((b, k) => k <= i || !(a.left < b.right - .5 && b.left < a.right - .5 && a.top < b.bottom - .5 && b.top < a.bottom - .5)));
    const painted = [...el.querySelectorAll('.rv-cover-blur')].every(c => c.getContext('2d').getImageData(Math.floor(c.width / 2), Math.floor(c.height / 2), 1, 1).data[3] === 255);
    // The magnifier: how many pixels have the line colours of the covers (sky-blue drawn, violet suggested).
    const fig = el.querySelector('.rv-mag'), mc = fig && !fig.hidden && fig.querySelector('canvas[data-drawn="1"]');
    let magnifier = null;
    if (mc) {
      const d = mc.getContext('2d').getImageData(0, 0, mc.width, mc.height).data, near = (i, [r, g, b]) => d[i + 3] > 200 && Math.abs(d[i] - r) + Math.abs(d[i + 1] - g) + Math.abs(d[i + 2] - b) < 40;
      magnifier = {sky: 0, violet: 0, covers: (window.BFReviewCards.magCovers(el, el.querySelector('.rv-art img')) || []).length};
      for (let i = 0; i < d.length; i += 4) { if (near(i, [56, 189, 248])) magnifier.sky++; else if (near(i, [192, 132, 252])) magnifier.violet++; }
    }
    return {shown: !!layer && getComputedStyle(layer).display !== 'none', time: layer ? layer.dataset.t : null, labels_inside: inside, labels_apart: apart, painted, magnifier,
      covers: [...el.querySelectorAll('.rv-cover')].map(c => ({owner: c.dataset.owner, state: c.classList.contains('applied') ? 'applied' : 'pending', label: (c.querySelector('em') || {}).textContent || ''})),
      note: (el.querySelector('.rv-cover-note:not([hidden])') || {}).textContent || '', coverage: (el.querySelector('.tech-body .coverage') || {}).textContent || ''};
  }, id);
  // The picture and the lines under it (the magnifier, the notes): a whole card is taller than a phone screen.
  const shoot = async (p, id, file) => {
    await p.evaluate(id => document.querySelector(`#review-dialog article.rv-card[data-item="${id}"] .rv-art`).scrollIntoView({block: 'start'}), id);
    const clip = await p.evaluate(id => {
      const el = document.querySelector(`#review-dialog article.rv-card[data-item="${id}"]`), card = el.getBoundingClientRect();
      const parts = [el.querySelector('.rv-art'), el.querySelector('.rv-mag:not([hidden])'), el.querySelector('.rv-cover-note:not([hidden])') || el.querySelector('.rv-zone-note:not([hidden])')].filter(Boolean).map(n => n.getBoundingClientRect());
      const top = Math.max(0, Math.min(...parts.map(r => r.top)) - 4), bottom = Math.min(innerHeight, Math.max(...parts.map(r => r.bottom)) + 8), left = Math.max(0, card.left);
      return {x: left, y: top, width: Math.min(innerWidth, card.right) - left, height: bottom - top};
    }, id);
    await p.screenshot({path: file, clip});
  };
  for (const job of jobs) {
    const queue = JSON.parse(fs.readFileSync(queueFile(job.job_key), 'utf8'));
    for (const [kind, x] of targets(queue)) {
      for (const [width, height, theme, bare] of [[390, 844, 'light', false], [390, 844, 'light', true], [390, 844, 'dark', false], [1440, 900, 'light', false]]) {
        const p = await page(width, height, theme), card = await openCard(p, job, x.id);
        if (bare) await card.locator('[data-review="frames"]').click();
        const name = `${job.job_key}-${kind}-${width}-${theme}${bare ? '-bare' : ''}.png`;
        await shoot(p, x.id, path.join(out, name));
        if (kind === 'dialogue' && width === 1440) await p.screenshot({path: path.join(out, `${job.job_key}-dialog-1440-light.png`)});
        result.shots.push({file: name, job: job.job_key, kind, card: x.id, width, theme, bare, ...(await describe(p, x.id))});
        await p.close();
      }
    }
  }
  for (const job of jobs) result.queues[job.job_key].after = sha(queueFile(job.job_key));
  await browser.close();
  result.queues_unchanged = Object.values(result.queues).every(q => q.before === q.after);
  fs.writeFileSync(path.join(out, 'shoot-result.json'), JSON.stringify(result, null, 2));
  const bad = result.shots.filter(s => !s.bare && (!s.shown || !s.painted || !s.labels_inside || !s.labels_apart || !s.covers.length)).map(s => s.file);
  process.stdout.write(JSON.stringify({shots: result.shots.length, posts: result.posts.length, problems: result.problems.length, queues_unchanged: result.queues_unchanged, bad}) + '\n');
  process.exit(result.posts.length || result.problems.length || !result.queues_unchanged || bad.length ? 1 : 0);
})().catch(error => { process.stderr.write((error && error.stack || String(error)) + '\n'); process.exit(1); });
