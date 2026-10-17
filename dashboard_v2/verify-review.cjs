/* Review dialog gate (R0): dashboard_v2/review-core.js against the classic review page.
 * The classic functions are taken verbatim from _interactive_html("test-token") (review_workflow.py,
 * run through Python once) and compared with review-core.js on synthetic queues (mock-data.js).
 * Deliberate differences S1, S2, S6, S7, S8 are asserted explicitly, never silently.
 * No server, network, database or video.
 */
'use strict';
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {spawnSync} = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const C = require('./contracts.js');
const R = require('./review-core.js');
let passed = 0;
const results = [];
const asyncChecks = [];
function check(name, fn) {
  const done = () => { passed++; results.push(name); process.stdout.write('OK ' + name + '\n'); };
  const result = fn();
  if (result && typeof result.then === 'function') asyncChecks.push(result.then(done)); else done();
}

/* The classic page, exactly as the Control Center serves it (token "test-token", as the D2 fixture). */
function classicHtml() {
  const code = 'import sys\nfrom biliflow.review_workflow import _interactive_html\nsys.stdout.buffer.write(_interactive_html("test-token").encode("utf-8"))';
  const venv = process.platform === 'win32' ? path.join(ROOT, '.venv', 'Scripts', 'python.exe') : path.join(ROOT, '.venv', 'bin', 'python');
  const candidates = [process.env.BILIFLOW_PYTHON, fs.existsSync(venv) ? venv : null, 'python3', 'python'].filter(Boolean);
  for (const python of candidates) {
    const run = spawnSync(python, ['-c', code], {cwd: ROOT, encoding: 'utf-8', env: {...process.env, PYTHONPATH: path.join(ROOT, 'src')}, timeout: 60000});
    if (run.status === 0 && run.stdout.includes('<script>')) return run.stdout;
  }
  throw new Error('Python with BiliFlow is required (set BILIFLOW_PYTHON)');
}
const html = classicHtml();
const script = html.slice(html.indexOf('<script>') + 8, html.indexOf('</script>'));
const lines = script.split('\n');
const line = re => { const found = lines.find(l => re.test(l)); assert.ok(found, 'classic line ' + re); return found; };
const CONSTS = ['SAFETY', 'KIND_NAMES', 'STATUS', 'SCENE_WORDS', 'EXPORT_LOCK_MESSAGE', 'SOURCE_CLEANED_LOCK_MESSAGE', 'SOURCE_ARCHIVED_LOCK_MESSAGE'];
const FUNCS = ['isSafety', 'momentsOf', 'isScene', 'studioEligible', 'catName', 'sceneLogo', 'hasPlayer', 'statusOf', 'isLogoItem', 'isAdItem', 'visible',
  'byTime', 'filteredItems', 'indexQueue', 'countsFrom', 'statusFrom', 'queueIdentity', 'queueVersion', 'pickFocus', 'nextUndecided', 'step',
  'bulkFilters', 'platformEligible', 'decisionsLocked', 'regionOwner', 'reviewDoneHint', 'nextNote', 'actionName'];
const source = CONSTS.map(n => line(new RegExp('^const ' + n + '='))).concat(FUNCS.map(n => line(new RegExp('^function ' + n + '\\(')))).join('\n');
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(`let filter='pending',queue=null,itemMap=new Map(),listIds=[],exportJob={status:'IDLE'},focusId=null,autoNext=true;const sticky=new Set();
function selectItem(id){focusId=id;}
${source}
globalThis.classic={
  load(q){queue=q;indexQueue();},
  filter(f,stickyIds){filter=f;sticky.clear();for(const id of stickyIds||[])sticky.add(id);listIds=filteredItems().map(x=>x.id);return listIds.slice();},
  pickFocus:()=>pickFocus(), nextUndecided:id=>nextUndecided(id), step(from,delta){focusId=from;step(delta);return focusId;},
  exportJob(value){exportJob=value;}, locked:()=>decisionsLocked(), nextNote:()=>nextNote(countsFrom(queue.items)),
  bulkKeepCount:()=>queue.items.filter(visible).filter(x=>!x.decision).length,
  bulkAcceptCount:()=>queue.items.filter(visible).filter(x=>!x.decision&&x.suggested_decision).length,
  regionOwner:x=>regionOwner(x), bulkFilters(f){filter=f;return bulkFilters();},
  fn:{actionName,isSafety,momentsOf,isScene,studioEligible,catName,sceneLogo,hasPlayer,statusOf,isLogoItem,isAdItem,countsFrom,statusFrom,queueIdentity,queueVersion,bulkFilters,platformEligible},
  consts:{SAFETY,KIND_NAMES,STATUS,SCENE_WORDS,EXPORT_LOCK_MESSAGE,SOURCE_CLEANED_LOCK_MESSAGE,SOURCE_ARCHIVED_LOCK_MESSAGE}
};`, sandbox);
const classic = sandbox.classic;
const same = (a, b, label) => assert.deepEqual(JSON.parse(JSON.stringify(a ?? null)), JSON.parse(JSON.stringify(b ?? null)), label);

/* Synthetic queues: the demo generator (every kind) plus hand-made edge cases. */
const mockContext = {window: {BFContracts: C}, structuredClone};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'mock-data.js'), 'utf8'), mockContext);
const Mock = mockContext.window.BFMock, demo = Mock.create(), job101 = demo.jobs.find(j => j.id === 101);
const edge = (() => {
  const q = Mock.reviewQueue(job101, {count: 12, advisory: 2});
  q.items[1].start_seconds = q.items[0].start_seconds; q.items[1].end_seconds = q.items[0].end_seconds; // tie → id order
  q.items[2].start_seconds = q.items[0].start_seconds; q.items[2].end_seconds = q.items[0].end_seconds + 1; // tie → end order
  q.items[3].priority = 'high'; q.items[3].decision = null;
  q.items[4].category = 'text'; q.items[4].review_kind = 'logo_overlay'; q.items[4].decision = null; // logo text: Logo filter, not Chữ
  q.items[5].category = 'visual_logo'; q.items[5].candidate_type = 'persistent_overlay'; q.items[5].suggested_region_source_pixels = null; q.items[5].decision = null;
  q.items[6].category = 'visual_logo'; q.items[6].candidate_type = 'ending_boundary'; q.items[6].suggested_region_source_pixels = null;
  q.items[7].review_kind = 'opening_promotion'; q.items[7].opening_ident = true;
  return q;
})();
const QUEUES = {
  demo101: Mock.reviewQueue(job101),
  mixed30: Mock.reviewQueue(job101, {count: 30}),
  big500: Mock.reviewQueue(job101, {count: 500, advisory: 20}),
  edge,
  allDecided: (() => { const q = Mock.reviewQueue(job101, {count: 10}); q.items.forEach(x => { x.decision = 'KEEP'; }); return q; })(),
  empty: Mock.reviewQueue(job101, {count: 0, advisory: 0}),
};

check('Chips and "Lọc khác" have the classic order, values and labels', () => {
  const chips = [...html.matchAll(/<button class="chip[^"]*" type="button" data-filter="([a-z_]+)"[^>]*>([^<]+)<\/button>/g)].map(m => [m[1], m[2]]);
  const select = html.slice(html.indexOf('<select id="more-filter"'), html.indexOf('</select>', html.indexOf('<select id="more-filter"')));
  const more = [...select.matchAll(/<option value="([a-z_]+)"[^>]*>([^<]+)<\/option>/g)].map(m => [m[1], m[2]]);
  same(R.FILTERS, chips, 'chips'); same(R.MORE_FILTERS, more, 'more filters');
});
check('Labels, statuses and rule texts are the classic ones', () => {
  const k = classic.consts;
  same(R.KIND_NAMES, k.KIND_NAMES); same(R.STATUS, k.STATUS);
  same(R.SAFETY, Object.fromEntries(Object.entries(k.SAFETY).map(([id, v]) => [id, v[0]])));
  assert.equal(R.TEXT.exportLock, k.EXPORT_LOCK_MESSAGE); assert.equal(R.TEXT.cleanedLock, k.SOURCE_CLEANED_LOCK_MESSAGE); assert.equal(R.TEXT.archivedLock, k.SOURCE_ARCHIVED_LOCK_MESSAGE);
  assert.ok(script.includes(R.TEXT.bulkUnsupported)); assert.ok(script.includes(R.TEXT.advisory));
  assert.ok(script.includes('Ít mục hơn không có nghĩa các nhóm này đã an toàn.'));
  assert.equal(R.scopeWarning({detection_scope: {skipped: ['adult', 'advertising']}}), 'Không quét trong lượt này: 18+, Quảng cáo / logo. Ít mục hơn không có nghĩa các nhóm này đã an toàn.');
});
check('Per-item rules match on every sample item (kind, name, action, status, player, logo eligibility)', () => {
  let n = 0;
  for (const [name, q] of Object.entries(QUEUES)) {
    for (const x of q.items.concat(q.advisory_items)) {
      for (const fn of ['isSafety', 'momentsOf', 'isScene', 'studioEligible', 'catName', 'sceneLogo', 'hasPlayer', 'statusOf', 'isLogoItem', 'isAdItem', 'platformEligible']) {
        same(R[fn](x), classic.fn[fn](x), name + ' ' + x.id + ' ' + fn);
      }
      for (const d of ['KEEP', 'BLUR', 'CUT', 'NEEDS_MORE_CONTEXT', x.suggested_decision]) assert.equal(R.actionName(x, d), classic.fn.actionName(x, d), x.id + ' actionName ' + d);
      n++;
    }
  }
  assert.ok(n > 580, 'items compared: ' + n);
});
check('Lists, focus, next undecided and step match for every filter (sticky included)', () => {
  for (const [name, q] of Object.entries(QUEUES)) {
    classic.load(q); const map = R.itemMap(q);
    for (const f of R.FILTER_IDS) {
      const decided = q.items.filter(x => x.decision).slice(0, 2).map(x => x.id);
      for (const stickyIds of f === 'pending' ? [[], decided] : [[]]) {
        const ids = classic.filter(f, stickyIds), list = R.listItems(q, f, new Set(stickyIds));
        same(list.map(x => x.id), ids, `${name} ${f} list`);
        assert.equal(R.pickFocus(list), classic.pickFocus(), `${name} ${f} focus`);
        const probes = list.slice(0, 40).map(x => x.id).concat(q.items.filter(x => x.decision).slice(0, 3).map(x => x.id), ['missing-id']);
        for (const id of probes) assert.equal(R.nextUndecided(list, id, map), classic.nextUndecided(id), `${name} ${f} next ${id}`);
        for (const id of [null].concat(list.slice(0, 6).map(x => x.id), list.slice(-2).map(x => x.id))) for (const d of [-1, 1]) {
          assert.equal(R.step(list, id, d), classic.step(id, d), `${name} ${f} step ${id} ${d}`);
        }
      }
    }
  }
});
check('Counts, status and the initial filter match; "N / M cảnh cần quyết định cuối" counts NEEDS_MORE_CONTEXT as not final', () => {
  for (const [name, q] of Object.entries(QUEUES)) {
    same(R.countsFrom(q.items), classic.fn.countsFrom(q.items), name);
    assert.equal(R.statusFrom(q.items), classic.fn.statusFrom(q.items), name);
    assert.equal(R.initialFilter(q), !classic.fn.countsFrom(q.items).pending && q.items.length ? 'all' : 'pending', name);
  }
  const p = R.progress(QUEUES.demo101);
  assert.deepEqual([p.remaining, p.total, p.pending, p.more], [5, 30, 4, 1]);
  assert.equal(R.progressText(QUEUES.demo101), '5 / 30 cảnh cần quyết định cuối');
  assert.equal(C.reviewStats(job101).remaining, p.remaining, 'the dialog and the dashboard row agree');
});
check('Bulk filter map matches; S1: counts follow the server selection, the classic count can differ', () => {
  for (const f of R.FILTER_IDS) same(R.bulkFilters(f), classic.bulkFilters(f), f);
  // Independent copy of bulk_keep / bulk_accept selection (review_workflow.py): category, main items, undecided.
  const server = (q, f, accept) => new Set(R.bulkFilters(f).flatMap(v => q.items.filter(x => x.decision == null
    && (v === 'pending' || v === 'all' || (v === 'high' ? x.priority === 'high' : x.category === v))
    && (!accept || (['KEEP', 'BLUR', 'CUT', 'NEEDS_MORE_CONTEXT'].includes(x.suggested_decision) && !(x.suggested_decision === 'BLUR' && x.suggested_region_source_pixels == null))))
    .map(x => x.id))).size;
  const differs = [];
  for (const [name, q] of Object.entries(QUEUES)) {
    classic.load(q);
    for (const f of R.FILTER_IDS) {
      if (!R.bulkFilters(f)) { assert.equal(R.bulkCount(q, f, 'keep'), null); continue; }
      classic.filter(f, []);
      for (const kind of ['keep', 'accept']) {
        const ours = R.bulkCount(q, f, kind), old = kind === 'keep' ? classic.bulkKeepCount() : classic.bulkAcceptCount();
        assert.equal(ours, server(q, f, kind === 'accept'), `${name} ${f} ${kind}`);
        if (ours !== old) differs.push(`${name}:${f}:${kind}`);
      }
    }
  }
  assert.ok(differs.some(d => d.startsWith('edge:visual_logo')), 'S1 shows on the Logo filter: ' + differs.join(','));
  assert.ok(differs.some(d => d.endsWith(':accept')), 'S1: accept skips BLUR without a region');
});
check('Identity and version: S7 reads source.sha256 (the classic page reads source.input_sha256)', () => {
  const q = structuredClone(QUEUES.demo101), r = structuredClone(q);
  r.source.sha256 = 'f'.repeat(64);
  assert.notEqual(R.queueIdentity(q), R.queueIdentity(r), 'a new source sha is a new identity');
  assert.equal(classic.fn.queueIdentity(q), classic.fn.queueIdentity(r), 'the classic identity misses it (A7)');
  const noSha = structuredClone(q); noSha.source.sha256 = '';
  assert.equal(R.queueIdentity(noSha), classic.fn.queueIdentity(noSha), 'otherwise the same identity');
  assert.equal(R.queueVersion(noSha), classic.fn.queueVersion(noSha));
  const edited = structuredClone(noSha); edited.updated_at = '2026-10-03T12:00:00Z'; edited.counts.pending--;
  assert.equal(R.queueIdentity(edited), R.queueIdentity(noSha)); assert.notEqual(R.queueVersion(edited), R.queueVersion(noSha));
});
check('Locks match the classic decisionsLocked except S6 (only an export is in flight) and S8 (SKIPPED is read-only)', () => {
  const cases = [
    ['ready', {state: 'READY_TO_EXPORT'}, {status: 'READY_TO_EXPORT'}, false, false],
    ['completed', {state: 'COMPLETED'}, {status: 'COMPLETED'}, false, false],
    ['failed', {state: 'FAILED'}, {status: 'FAILED'}, false, false],
    ['queued export', {state: 'QUEUED', queue_kind: 'export'}, {status: 'QUEUED'}, true, true],
    ['rendering', {state: 'RENDERING'}, {status: 'RENDERING'}, true, true],
    ['render request', {state: 'READY_TO_EXPORT', render_request: true}, {status: 'READY_TO_EXPORT'}, true, false],
    ['S6 queued scan', {state: 'QUEUED', queue_kind: 'scan'}, {status: 'QUEUED'}, false, true],
    ['S6 verifying', {state: 'VERIFYING'}, {status: 'VERIFYING'}, true, false],
    ['cleaned', {state: 'COMPLETED', source_cleaned: true}, {status: 'COMPLETED', source_cleaned: true}, true, true],
    ['archived', {state: 'COMPLETED', source_archived: true}, {status: 'COMPLETED', source_archived: true}, true, true],
    ['S8 skipped', {state: 'SKIPPED'}, {status: 'SKIPPED'}, true, false],
  ];
  for (const [name, job, exp, ours, old] of cases) {
    classic.exportJob(exp);
    assert.equal(classic.locked(), old, name + ' classic');
    const lock = R.lockState(job, exp);
    assert.equal(lock.readonly, ours, name);
    assert.equal(lock.active, C.inFlight(job), name + ' uses contracts.inFlight');
  }
  assert.equal(R.lockState({state: 'RENDERING'}, null).reason, R.TEXT.exportLock);
  assert.equal(R.lockState({state: 'COMPLETED', source_cleaned: true}, null).reason, R.TEXT.cleanedLock);
  assert.equal(R.lockState({state: 'COMPLETED', source_archived: true}, null).reason, R.TEXT.archivedLock);
  assert.match(R.lockState({state: 'SKIPPED'}, null).reason, /Mở lại để xuất/);
  assert.equal(R.canExport({status: 'READY_FOR_EDIT_PLAN'}, R.lockState({state: 'READY_TO_EXPORT'})), true);
  assert.equal(R.canExport({status: 'NEEDS_MORE_CONTEXT'}, R.lockState({state: 'READY_TO_EXPORT'})), false);
});
check('S2: only NEEDS_MORE_CONTEXT left says so (the classic page says "Đã duyệt đủ")', () => {
  const q = structuredClone(QUEUES.allDecided); q.items[0].decision = 'NEEDS_MORE_CONTEXT';
  classic.load(q); classic.filter('all', []); classic.exportJob({status: 'READY_TO_EXPORT'});
  assert.match(classic.nextNote(), /^Đã duyệt đủ mọi mục chính/);
  assert.equal(R.nextNote(q), 'Còn 1 mục Cần xem thêm — chọn quyết định cuối trước khi xuất.');
  assert.equal(R.nextNote(QUEUES.allDecided), 'Đã duyệt đủ mọi mục chính.');
  assert.equal(R.nextNote(QUEUES.demo101), 'Còn 4 mục chưa duyệt.');
});
check('Red region: the same owner card as the classic regionOwner, as fractions of the source frame', () => {
  for (const [name, q] of Object.entries(QUEUES)) {
    classic.load(q);
    for (const x of q.items.concat(q.advisory_items).slice(0, 120)) {
      const old = classic.regionOwner(x), ours = R.regionOwner(q, x);
      assert.equal(ours ? ours.id : null, old ? old.id : null, name + ' ' + x.id);
    }
  }
  const logo = QUEUES.mixed30.items.find(x => x.review_kind === 'logo_overlay' && x.category === 'visual_logo');
  const box = R.regionBox(QUEUES.mixed30, logo);
  assert.deepEqual([box.left, box.top, box.width, box.height].map(v => Math.round(v * 1000) / 1000), [0.031, 0.037, 0.135, 0.089]);
  assert.equal(R.regionBox(QUEUES.mixed30, QUEUES.mixed30.items.find(x => x.category === 'gore')), null);
  assert.equal(R.frameAspect(logo), '1920 / 1080');
});
check('Pure: no DOM, no fetch, no timers in review-core.js', () => {
  const text = fs.readFileSync(path.join(__dirname, 'review-core.js'), 'utf8');
  assert.ok(!/document\.|fetch\(|XMLHttpRequest|setTimeout|setInterval|innerHTML/.test(text));
});
check('500 synthetic items: every filter list is built in well under a frame', () => {
  const q = QUEUES.big500, started = process.hrtime.bigint();
  for (let i = 0; i < 20; i++) for (const f of R.FILTER_IDS) R.listItems(q, f, new Set());
  const ms = Number(process.hrtime.bigint() - started) / 1e6 / 20;
  assert.ok(ms < 16, 'all filters for 500 items: ' + ms.toFixed(2) + ' ms');
  process.stdout.write('   500 items, 11 filters: ' + ms.toFixed(2) + ' ms per pass\n');
});

/* R1: media and evidence texts of a card against the classic functions (a second sandbox). */
const D = require('./review-detail.js');
const MEDIA_CONSTS = ['esc', 'clock', 'mmss', 'span', 'SAFETY', 'KIND_NAMES', 'SCENE_WORDS'];
const MEDIA_FUNCS = ['isSafety', 'momentsOf', 'isScene', 'catName', 'sceneLogo', 'studioEligible', 'isLogoItem', 'actionName', 'regionOwner', 'thumbTime',
  'momentIndex', 'pickStrip', 'pickSceneStrip', 'pickFor', 'stripFrames', 'tlPos', 'thin', 'renderTimeline', 'nextMomentAfter', 'videoReason', 'viText',
  'labelsReasonsHtml', 'visualAiHtml', 'evidenceHtml', 'regionName', 'regionDetailHtml', 'decisionScope', 'scopeBlock', 'overlapCoverage', 'regionOverlap',
  'trackCoversFullVideo', 'aiModelLine', 'memoryMatch', 'memoryBrandName', 'readingLabel', 'boxesFromMemory', 'evidenceMediaHtml', 'techDetails'];
const mediaSource = MEDIA_CONSTS.map(n => line(new RegExp('^const ' + n + '='))).concat(MEDIA_FUNCS.map(n => line(new RegExp('^function ' + n + '\\(')))).join('\n');
const media = {};
vm.createContext(media);
vm.runInContext(`let queue=null,mediaKey=null,techOpen=false;const timelineBox={innerHTML:''};const pstate={start:0,end:0,moments:null};
const $=s=>s==='#timeline'?timelineBox:null;function markMoment(){}
${mediaSource}
globalThis.classic={load(q){queue=q;},key(k){mediaKey=k;},
  timeline(x,ev){pstate.start=Number(x.start_seconds);pstate.end=Number(x.end_seconds);renderTimeline(x,ev);return timelineBox.innerHTML;},
  next(t,ms){pstate.moments=ms;return nextMomentAfter(t);},
  fn:{thumbTime,momentIndex,pickStrip,pickSceneStrip,pickFor,stripFrames,thin,videoReason,viText,labelsReasonsHtml,visualAiHtml,evidenceHtml,regionDetailHtml,regionOwner,
    decisionScope,scopeBlock,overlapCoverage,aiModelLine,readingLabel,evidenceMediaHtml,techDetails}};`, media);
const old = media.classic;
const evidenceCases = (x, i) => [Mock.reviewEvidence(x), Mock.reviewEvidence(x, {known: false, count: 24, ignored: 2}), Mock.reviewEvidence(x, {count: 5}),
  Mock.reviewEvidence(x, {frames: false, video: {available: false, reason: ['source_cleaned', 'source_missing', 'unsupported_container'][i % 3]}}), null];

check('R1 strip: the same ≤8 frames as the classic stripFrames (evidence or report previews), "Rõ nhất" first', () => {
  let n = 0;
  for (const q of [QUEUES.mixed30, QUEUES.edge, QUEUES.big500]) {
    q.items.concat(q.advisory_items).slice(0, 80).forEach((x, i) => {
      for (const ev of evidenceCases(x, i)) for (const key of [null, 'mk']) {
        old.key(key);
        const classicStrip = old.fn.stripFrames(x, ev), ours = R.stripFrames(x, ev, !!key);
        same(ours.map(f => f.path ? {t: f.t, kind: f.kind, src: '/media/' + encodeURIComponent(f.path)} : f), classicStrip, x.id + ' strip');
        assert.ok(ours.length <= 8);
        const first = R.peakFirst(ours);
        if (ours.some(f => f.kind === 'strongest')) assert.equal(first[0].kind, 'strongest');
        same(first.slice().sort((a, b) => (a.t ?? 0) - (b.t ?? 0)).map(f => f.t), ours.map(f => f.t), x.id + ' only the order changes');
        n++;
      }
    });
  }
  const frames = Array.from({length: 30}, (_, i) => ({t: i, kind: i === 17 ? 'strongest' : i % 4 ? 'context' : 'seed', score: i / 30}));
  same(R.pickStrip(frames), old.fn.pickStrip(frames));
  const ms = [{start: 2, end: 4}, {start: 10, end: 11}, {start: 20, end: 26}];
  same(R.pickSceneStrip(frames, ms), old.fn.pickSceneStrip(frames, ms));
  for (const p of ['a/b-12.5s.jpg', 'x-3s.PNG', 'y.jpg', '', null]) assert.equal(R.thumbTime(p), old.fn.thumbTime(p));
  assert.ok(n >= 1270, 'strips compared: ' + n);
});
check('R1 timeline: the classic markup (bars, moments, windows, ticks, peak) for every sample item', () => {
  for (const q of [QUEUES.mixed30, QUEUES.edge]) {
    q.items.concat(q.advisory_items).forEach((x, i) => {
      for (const ev of evidenceCases(x, i)) assert.equal(R.timelineHtml(x, ev) + '<div class="head" id="thead" hidden></div>', old.timeline(x, ev), x.id);
    });
  }
  const ms = [{start: 2, end: 4}, {start: 10, end: 11}];
  for (const t of [0, 2, 4.2, 9.99, 11, 30]) { assert.equal(R.nextMomentAfter(t, ms), old.next(t, ms)); assert.equal(R.momentIndex(t, ms), old.fn.momentIndex(t, ms)); }
  const scene = QUEUES.mixed30.items.find(x => R.isScene(x)), m = R.momentsOf(scene);
  const gap = (m[0].end + m[1].start) / 2, ratio = ((gap - scene.start_seconds) / (scene.end_seconds - scene.start_seconds) * 96 + 2) / 100;
  same(R.seekTarget(scene, ratio), {t: m[1].start, moment: 1}, 'a click in a gap goes to the start of the next moment');
  same(R.seekTarget(scene, 1), {t: m[m.length - 1].start, moment: m.length - 1});
  const single = QUEUES.mixed30.items.find(x => x.category === 'gore');
  same(R.seekTarget(single, 0.5), {t: single.start_seconds + (single.end_seconds - single.start_seconds) * 0.5, moment: -1});
});
check('R1 video reasons and probe statuses are the classic ones', () => {
  for (const key of [null, 'mk']) for (const reason of [undefined, 'unsupported_container', 'source_changed', 'source_missing', 'source_cleaned', 'source_unknown', 'decode_error', 'demo']) {
    old.key(key); assert.equal(R.videoReason(reason ? {reason} : null, !!key), old.fn.videoReason(reason ? {reason} : null));
  }
  same([404, 409, 410, 415, 206, 206, 403, 0].map((s, i) => R.probeReason(s, i === 5 ? 3 : 0)), ['source_missing', 'source_changed', 'source_cleaned', 'unsupported_container', null, 'decode_error', null, null]);
});
check('R1 "Chi tiết kỹ thuật" and evidence texts equal the classic markup (but the note on the blurs of other cards, S12)', () => {
  for (const q of [QUEUES.mixed30, QUEUES.edge, QUEUES.demo101]) {
    old.load(q);
    q.items.concat(q.advisory_items).forEach((x, i) => {
      const ev = evidenceCases(x, i)[i % 5];
      // S12 (session D): the note on other cards' blurs is V2's own (every card, every kind; checked below).
      const noCoverage = h => h.replace(/<div class="coverage">[\s\S]*?<\/div>/, '');
      assert.equal(noCoverage('<details class="tech"><summary>Chi tiết kỹ thuật</summary><div class="tech-body">' + D.techBody(q, x, ev) + '</div></details>'), noCoverage(old.fn.techDetails(x, ev, false)), x.id);
      same(D.decisionScope(q, x), old.fn.decisionScope(x));
      assert.equal(D.labelsReasonsHtml(x), old.fn.labelsReasonsHtml(x)); assert.equal(D.visualAiHtml(x), old.fn.visualAiHtml(x));
      assert.equal(D.evidenceHtml(ev), old.fn.evidenceHtml(ev)); assert.equal(D.aiModelLine(x), old.fn.aiModelLine(x));
    });
  }
  const memory = {category: 'visual_logo', max_score: 0.97, model_evidence: {vlm_source: 'approved_brand_memory', vlm_answer: 'MEMORY_MATCH | Kênh <b>A</b>', vlm_confirmation: 'MATCHED'}};
  assert.equal(D.aiModelLine(memory), old.fn.aiModelLine(memory));
  assert.match(D.aiModelLine(memory), /&lt;b&gt;A&lt;\/b&gt;/, 'queue strings are escaped');
});
check('R1 zoomed evidence: yellow AI boxes, red approved boxes and the legend match evidenceMediaHtml (session R: no crop in the zoomed card)', () => {
  const unescape = t => t.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  let boxes = 0;
  for (const q of [QUEUES.mixed30, QUEUES.edge]) {
    old.load(q);
    const blurred = q.items.find(x => x.review_kind === 'logo_overlay' && x.category === 'visual_logo');
    blurred.decision = 'BLUR'; blurred.decision_region_source_pixels = blurred.suggested_region_source_pixels;
    for (const x of q.items.concat(q.advisory_items)) {
      const view = D.evidenceView(q, x), owner = old.fn.regionOwner(x), r = owner && (owner.suggested_region_source_pixels || owner.decision_region_source_pixels);
      if (view.mode === 'region') { assert.ok(owner && r && r !== 'FULL_FRAME'); continue; }
      if (x.category !== 'visual_logo') { assert.equal(view.mode, 'none'); continue; }
      const images = ['a.jpg'], approved = R.sceneLogo(x) && owner && owner.id !== x.id && r && r !== 'FULL_FRAME' ? owner : null;
      const html = old.fn.evidenceMediaHtml(x, images, p => '/media/' + p, approved, approved ? r : undefined);
      const data = /data-boxes="([^"]*)"/.exec(html), legend = /<div class="evidence-legend">(.*?)<\/div>/.exec(html);
      same(view.marks, data ? JSON.parse(unescape(data[1])) : [], x.id + ' marks');
      assert.equal(view.legend, legend ? unescape(legend[1]) : '', x.id + ' legend');
      boxes += view.marks.length;
    }
  }
  assert.ok(boxes >= 4, 'boxes compared: ' + boxes);
  // Session R: the 360 px crop of the zoomed card became the magnifier under every card's image (checked below).
  assert.equal(D.cropRect, undefined);
  assert.ok(!/document\.|fetch\(|innerHTML|setTimeout/.test(fs.readFileSync(path.join(__dirname, 'review-detail.js'), 'utf8')), 'review-detail.js is pure');
});

/* R2: decisions against the classic decide(), clearDecision(), undo(), keyDecision() and writeFailureMessage()
 * (a third sandbox; confirm/alert/enqueueWrite are recorders). */
const DECIDE_CONSTS = ['esc', 'clock', 'mmss', 'mmssTenth', 'span', 'SAFETY', 'KIND_NAMES', 'SCENE_WORDS', 'EXPORT_LOCK_MESSAGE', 'SOURCE_CLEANED_LOCK_MESSAGE', 'SOURCE_ARCHIVED_LOCK_MESSAGE', 'WRITE_RETRY_MS'];
const DECIDE_FUNCS = ['isSafety', 'momentsOf', 'isScene', 'momentTotal', 'studioEligible', 'catName', 'sceneLogo', 'isLogoItem', 'actionName', 'platformEligible', 'countsFrom',
  'statusFrom', 'isAdvisoryItem', 'pushUndo', 'syncLocalCounts', 'applyLocalDecision', 'applyLocalClear', 'writeFailureMessage', 'sceneBlurMessage', 'decisionsLocked',
  'refuseWhileExporting', 'decide', 'clearDecision', 'undo', 'advisoryUndoMessage', 'decisionLabel', 'keyDecision', 'thumbTime', 'readingLabel', 'regionOwner',
  'studioCompareLine', 'studioTextsNote', 'studioFramesNote', 'studioMaskNote', 'studioRemembered', 'platformRemembered', 'platformNote', 'regionControlsHtml', 'studioHtml', 'platformHtml',
  'studioWithheldLine', 'studioBlockedLine', 'suggestionLine'];
const decideSource = DECIDE_CONSTS.map(n => line(new RegExp('^const ' + n + '='))).concat(DECIDE_FUNCS.map(n => line(new RegExp('^(async )?function ' + n + '\\(')))).join('\n');
const decideBox = {};
vm.createContext(decideBox);
vm.runInContext(`let queue=null,itemMap=new Map(),busy=false,exportJob={status:'IDLE'},focusId=null,previousFocusId=null,autoNext=true,filter='all',listIds=[];
const sticky=new Set(),undoStack=[];let answers=[],asked=[],alerts=[],writes=[];
function confirm(m){asked.push(m);return answers.length?answers.shift():true;}
function alert(m){alerts.push(m);}
function enqueueWrite(kind,body){writes.push({kind,body:JSON.parse(JSON.stringify(body))});return Promise.resolve();}
function afterLocalChange(){} function updateNavState(){} function renderFocus(){} function renderSide(){} function updateListStatuses(){} function updateHeader(){}
function studioHtmlNote(x){return studioHtml(x);}
${decideSource}
globalThis.classic={
  load(q){queue=q;itemMap=new Map();for(const x of q.items||[])itemMap.set(x.id,x);for(const x of q.advisory_items||[])if(!itemMap.has(x.id))itemMap.set(x.id,x);undoStack.length=0;listIds=[];focusId=null;},
  run(fn,script){answers=(script||[]).slice();asked=[];alerts=[];writes=[];fn();return {asked:asked.slice(),alerts:alerts.slice(),writes:writes.slice()};},
  decide:(id,d,full,note,studio,platform)=>decide(id,d,full,note,studio,platform), clear:id=>clearDecision(id), undo:()=>undo(), key(id,n){focusId=id;keyDecision(n);},
  undoStack:()=>JSON.parse(JSON.stringify(undoStack)), item:id=>itemMap.get(id), drop:id=>itemMap.delete(id), lock(value){exportJob=value;},
  fn:{writeFailureMessage,decisionLabel,advisoryUndoMessage,sceneBlurMessage,studioCompareLine,studioTextsNote,studioFramesNote,studioMaskNote,studioRemembered,platformRemembered,platformNote,regionControlsHtml,regionOwner,studioHtml,platformHtml,suggestionLine},
  consts:{WRITE_RETRY_MS}
};`, decideBox);
const cl = decideBox.classic, esc = D.esc;
/* The V2 dialog's decide()/clear()/undo() on the same steps as review.js (S5 confirms answered by `script`). */
function v2Session(q) {
  const map = R.itemMap(q), undo = [];
  const run = (fn, script) => { const answers = (script || []).slice(), out = {asked: [], alerts: [], writes: []}; fn(answers, out); return out; };
  const ask = (answers, out, message) => { out.asked.push(message); return answers.length ? answers.shift() : true; };
  return {
    map, undoStack: () => JSON.parse(JSON.stringify(undo)),
    decide: (id, d, full, note, studio, platform) => (answers, out) => {
      const item = map.get(id);
      if (!item) { out.alerts.push(R.TEXT.missingItem); return; }
      for (const message of R.decisionConfirms(item, d, full)) if (!ask(answers, out, message)) return;
      const plan = R.decisionBody(item, d, {fullFrame: full, note, studio, platform});
      if (plan.error) { out.alerts.push(plan.error); return; }
      undo.push(R.undoEntry(item, !item.decision && R.isAdvisoryItem(q, item))); if (undo.length > 100) undo.shift();
      R.applyDecision(q, item, d, plan.region, plan.note, plan.studio, plan.platform);
      out.writes.push({kind: 'decision', body: JSON.parse(JSON.stringify(plan.body))});
    },
    clear: id => (answers, out) => { const item = map.get(id); if (!item || !item.decision) return; undo.push(R.undoEntry(item, false)); if (undo.length > 100) undo.shift(); R.applyClear(q, item); out.writes.push({kind: 'clear', body: {id}}); },
    undo: () => (answers, out) => {
      const entry = undo.pop(); if (!entry) return;
      const item = map.get(entry.id);
      if (!item) { out.alerts.push(R.TEXT.undoMissing); return; }
      if (entry.advisory) { out.alerts.push(R.advisoryUndoMessage(item)); return; }
      const plan = R.undoPlan(entry), prev = entry.prev;
      if (plan.kind === 'decision') R.applyDecision(q, item, prev.decision, prev.region, prev.note, prev.studio, prev.platform); else R.applyClear(q, item);
      out.writes.push({kind: plan.kind, body: plan.body});
    },
    run,
  };
}
const itemState = x => x && ({decision: x.decision ?? null, region: x.decision_region_source_pixels ?? null, note: x.decision_note ?? null, studio: x.studio_logo_memory || null, platform: x.platform_logo_memory || null});
function decideQueues() {
  const a = Mock.reviewQueue(job101, {count: 30}), b = structuredClone(QUEUES.edge);
  // Visual AI on items with and without a region, a scene with AI, a platform logo without a region, remembered memories.
  a.items[3].ai_visual_audit = {suggested_decision: 'KEEP', confidence: 0.95, classification: 'tiêu đề phim'};
  a.items[0].ai_visual_audit = {suggested_decision: 'CUT', confidence: 0.91};
  a.items[2].ai_visual_audit = {suggested_decision: 'BLUR', confidence: 0.97, classification: 'logo <b>thương hiệu</b>'};
  a.items[12].ai_visual_audit = {suggested_decision: 'BLUR', confidence: 0.89};
  a.items[17].suggested_region_source_pixels = null; // platform logo without a region
  a.items[22].decision = 'KEEP'; a.items[22].studio_logo_memory = {remembered: true, frames: 40, frames_source: 'source_video'};
  a.items[27].decision = 'BLUR'; a.items[27].decision_region_source_pixels = null; a.items[27].platform_logo_memory = {remembered: true, logo_frames: 6, platform: {key: 'demo', name: 'Nền tảng <mẫu>'}};
  return [a, b];
}
check('R2 decisions: confirms, errors, POST bodies, local state and undo entries equal the classic decide() / clearDecision()', () => {
  let cases = 0, bodies = 0;
  const SCRIPTS = [[], [false], [true, false]];
  for (const base of decideQueues()) {
    for (const x of base.items.concat(base.advisory_items)) {
      const variants = [[false, null, false, false], [true, null, false, false], [false, 'ghi chú <i>', false, false], [false, null, true, false], [false, null, false, true]];
      for (const decision of ['KEEP', 'BLUR', 'CUT', 'NEEDS_MORE_CONTEXT']) for (const [full, note, studio, platform] of variants) for (const script of SCRIPTS) {
        const q1 = structuredClone(base), q2 = structuredClone(base);
        cl.load(q1); const v2 = v2Session(q2);
        const a = cl.run(() => cl.decide(x.id, decision, full, note, studio, platform), script);
        const b = v2.run(v2.decide(x.id, decision, full, note, studio, platform), script);
        const label = `${x.id} ${decision} full=${full} note=${note} studio=${studio} platform=${platform} script=${JSON.stringify(script)}`;
        same(b, a, label);
        same(itemState(v2.map.get(x.id)), itemState(cl.item(x.id)), label + ' item');
        same([q2.counts, q2.status], [q1.counts, q1.status], label + ' counts');
        same(v2.undoStack(), cl.undoStack(), label + ' undo');
        cases++; bodies += a.writes.length;
        // Then clear it again: same {id} body and undo entry.
        const c1 = cl.run(() => cl.clear(x.id)), c2 = v2.run(v2.clear(x.id));
        same(c2, c1, label + ' clear'); same(itemState(v2.map.get(x.id)), itemState(cl.item(x.id)), label + ' cleared');
      }
    }
  }
  const missing = (() => { const q = structuredClone(QUEUES.edge), v2 = v2Session(structuredClone(QUEUES.edge)); cl.load(q); return [cl.run(() => cl.decide('missing-id', 'KEEP')), v2.run(v2.decide('missing-id', 'KEEP'))]; })();
  same(missing[1], missing[0], 'unknown id');
  assert.ok(cases > 2500 && bodies > 2000, `cases ${cases}, bodies ${bodies}`);
  process.stdout.write(`   ${cases} decision cases, ${bodies} POST bodies identical\n`);
});
check('R2 undo: up to 100 steps, advisory items cannot go back, the same bodies as the classic undo()', () => {
  for (const base of decideQueues()) {
    const q1 = structuredClone(base), q2 = structuredClone(base);
    cl.load(q1); const v2 = v2Session(q2);
    const all = base.items.concat(base.advisory_items), D4 = ['KEEP', 'BLUR', 'CUT', 'NEEDS_MORE_CONTEXT'];
    let seed = 7;
    const rnd = n => (seed = (seed * 1103515245 + 12345) % 2147483648) % n;
    for (let i = 0; i < 130; i++) {
      const x = all[rnd(all.length)], d = D4[rnd(4)], full = rnd(2) === 1, studio = rnd(5) === 0, platform = rnd(5) === 0;
      if (rnd(6) === 0) { same(v2.run(v2.clear(x.id)), cl.run(() => cl.clear(x.id)), 'clear ' + i); continue; }
      same(v2.run(v2.decide(x.id, d, full, null, studio, platform)), cl.run(() => cl.decide(x.id, d, full, null, studio, platform)), 'step ' + i);
    }
    assert.equal(cl.undoStack().length, 100); same(v2.undoStack(), cl.undoStack(), 'stack capped at 100');
    let advisory = 0;
    for (let i = 0; i < 105; i++) {
      const a = cl.run(() => cl.undo()), b = v2.run(v2.undo());
      same(b, a, 'undo ' + i);
      if (a.alerts.length) advisory++;
    }
    for (const x of all) same(itemState(v2.map.get(x.id)), itemState(cl.item(x.id)), 'after undo ' + x.id);
    same([q2.counts, q2.status], [q1.counts, q1.status]);
    assert.ok(advisory >= 1, 'advisory undo message seen');
  }
  // An item no longer in the queue: the classic message, nothing sent.
  const q = structuredClone(QUEUES.edge), v2 = v2Session(structuredClone(QUEUES.edge)), id = q.items[0].id;
  cl.load(q); cl.run(() => cl.decide(id, 'KEEP')); v2.run(v2.decide(id, 'KEEP'));
  cl.drop(id); v2.map.delete(id);
  same(v2.run(v2.undo()), cl.run(() => cl.undo()), 'undo of a missing item');
  assert.equal(R.TEXT.undoMissing, 'Mục cần hoàn tác không còn trong hàng đợi hiện tại.');
  assert.ok(script.includes(R.TEXT.undoMissing) && script.includes(R.TEXT.missingItem) && script.includes(R.TEXT.needsRegion) && script.includes(R.TEXT.fullFrame));
});
check('R2 keys 1–4 and the card buttons: the classic keyDecision except R2-K (BLUR on an item with a red region blurs the region)', () => {
  let regionItems = 0;
  for (const base of decideQueues()) for (const x of base.items) for (const n of [1, 2, 3, 4]) {
    const q1 = structuredClone(base), q2 = structuredClone(base); cl.load(q1); const v2 = v2Session(q2);
    const a = cl.run(() => cl.key(x.id, n)), k = R.keyDecision(n, x), b = v2.run(v2.decide(x.id, k.decision, k.fullFrame, null, false, false));
    if (n === 2 && !R.needsFullFrame(x)) {
      regionItems++;
      // R2-K: the classic key 2 asks to blur the whole frame; the V2 key 2 is the card's "Làm mờ" button: the red region, no whole-frame confirm.
      assert.ok(a.asked.includes(R.TEXT.fullFrame) || a.asked.some(m => /Làm mờ toàn bộ khung hình/.test(m)), x.id);
      assert.equal(b.writes[0].body.full_frame, false); assert.equal(R.blurLabel(x), 'Làm mờ');
      same(b, cl.run(() => { cl.load(structuredClone(base)); cl.decide(x.id, 'BLUR', false); }), x.id + ' = classic decide(id, BLUR, false)');
    } else same(b, a, x.id + ' key ' + n);
  }
  assert.ok(regionItems > 5, 'region items: ' + regionItems);
  const x = QUEUES.edge.items.find(i => R.needsFullFrame(i));
  assert.equal(R.blurLabel(x), 'Làm mờ cả cảnh'); assert.ok(script.includes('Làm mờ cả cảnh'));
  // The chosen main button: the classic marks "Làm mờ cả cảnh" for BLUR on FULL_FRAME; V2 marks its BLUR button the same way, or BLUR on a region for a region item.
  assert.equal(R.chosenButton({decision: 'BLUR', decision_region_source_pixels: 'FULL_FRAME'}), 'BLUR');
  assert.equal(R.chosenButton({decision: 'BLUR', decision_region_source_pixels: null, platform_logo_memory: {remembered: true}}), null);
  assert.equal(R.chosenButton({decision: 'BLUR', suggested_region_source_pixels: {x: 1}, decision_region_source_pixels: {x: 1}}), 'BLUR');
  assert.equal(R.chosenButton({decision: 'CUT'}), 'CUT'); assert.equal(R.chosenButton({decision: null}), null);
});
check('R2 region buttons, studio / platform memory texts and labels equal the classic regionControlsHtml, studioHtml and platformHtml', () => {
  const q = structuredClone(decideQueues()[0]);
  const studio = q.items.find(x => R.studioEligible(x)), overlay = q.items.find(x => x.category === 'visual_logo' && x.suggested_region_source_pixels);
  overlay.candidate_type = 'persistent_overlay'; overlay.start_seconds = studio.start_seconds - 1; overlay.end_seconds = studio.end_seconds + 1;
  const variants = [
    x => x,
    x => ({...x, studio_logo_compared: {records: 3, best_similarity: 0.962, minimum_similarity: 0.95, best_cell_difference: 31, maximum_cell_difference: 20, masked_regions: 1}}),
    x => ({...x, studio_logo_compared: {records: 2, best_similarity: 0.81}, suggestion_withheld: {window_texts: ['A', 'B<c>', 'C', 'D', 'E', 'F', 'G']}, preview_images: ['p-12.5s.jpg', 'p-61.25s.jpg']}),
    x => ({...x, decision: 'KEEP', studio_logo_memory: {remembered: true, frames: 40, frames_source: 'source_video', ignored_regions: [{}], mask_updated_at: 'now'}}),
    x => ({...x, decision: 'KEEP', studio_logo_memory: {remembered: true, frames: 6, frames_source: 'previews', mask_refused: true}}),
    x => ({...x, decision: 'KEEP', studio_logo_memory: {remembered: true, frames: 6, frames_missing: true}}),
    x => ({...x, decision: 'KEEP', studio_logo_memory: {remembered: true, frames: 6}}),
    x => ({...x, decision: 'KEEP', studio_logo_memory: {remembered: true}}),
    x => ({...x, decision: 'BLUR', platform_logo_memory: {remembered: true, logo_frames: 5, platform: {key: 'demo', name: 'Nền <tảng>'}}}),
    x => ({...x, decision: 'BLUR', platform_logo_memory: {remembered: true}}),
    x => ({...x, evidence_regions: [{covered_by: 'other-id'}]}),
  ];
  let n = 0;
  for (const blurOverlay of [false, true]) {
    overlay.decision = blurOverlay ? 'BLUR' : null; overlay.decision_region_source_pixels = blurOverlay ? overlay.suggested_region_source_pixels : null;
    for (const make of variants) for (const base of [studio].concat(q.items.filter(x => R.platformEligible(x)))) {
      const x = make(structuredClone(base)), qq = {...q, items: q.items.map(i => i.id === x.id ? x : i)};
      cl.load(qq);
      assert.equal(D.studioCompareLine(x), cl.fn.studioCompareLine(x)); assert.equal(D.studioTextsNote(x), cl.fn.studioTextsNote(x));
      assert.equal(D.studioFramesNote(x), cl.fn.studioFramesNote(x)); assert.equal(D.studioMaskNote(qq, x), cl.fn.studioMaskNote(x));
      same(R.studioRemembered(x), cl.fn.studioRemembered(x)); same(R.platformRemembered(x), cl.fn.platformRemembered(x));
      assert.equal(D.platformNote(x, R.platformRemembered(x)), cl.fn.platformNote(x, cl.fn.platformRemembered(x)));
      if (R.studioEligible(x)) {
        const remembered = !!R.studioRemembered(x);
        assert.equal(`<div class="studio-wrap">${D.studioCompareLine(x)}<button type="button" class="studio${remembered ? ' sel' : ''}" data-act="studio" aria-pressed="${remembered}">${remembered ? R.TEXT.studioDone : esc(R.TEXT.studio)}</button><small>${D.studioNote(x)}</small>${D.studioMaskNote(qq, x)}</div>`, cl.fn.studioHtml(x));
      }
      if (R.platformEligible(x)) {
        const m = R.platformRemembered(x);
        assert.equal(`<div class="studio-wrap"><button type="button" class="studio platform${m ? ' sel' : ''}" data-act="platform" aria-pressed="${!!m}">${m ? R.TEXT.platformDone : esc(R.TEXT.platform)}</button><small>${D.platformNote(x, m)}</small></div>`, cl.fn.platformHtml(x));
      }
      n++;
    }
  }
  // Region buttons: owner, title, note and the chosen button of every sample item.
  let regions = 0;
  for (const base of decideQueues()) {
    cl.load(base);
    for (const x of base.items.concat(base.advisory_items)) {
      const owner = R.regionOwner(base, x), r = owner && (owner.suggested_region_source_pixels || owner.decision_region_source_pixels), c = D.regionControls(x, owner, r);
      const html = c ? `<div class="decision-block"><strong>${c.title}</strong><small>${c.note}</small><div class="region-decide"><button type="button" class="rk${c.decision === 'KEEP' ? ' sel' : ''}" data-act="region" data-owner="${esc(c.owner)}" data-decision="KEEP">${R.TEXT.regionKeep}</button><button type="button" class="rb${c.decision === 'BLUR' ? ' sel' : ''}" data-act="region" data-owner="${esc(c.owner)}" data-decision="BLUR">${R.TEXT.regionBlur}</button></div></div>` : '';
      const ownerOld = cl.fn.regionOwner(x), rOld = ownerOld && (ownerOld.suggested_region_source_pixels || ownerOld.decision_region_source_pixels);
      assert.equal(html, cl.fn.regionControlsHtml(x, ownerOld, rOld), x.id);
      if (c) regions++;
    }
  }
  assert.equal(R.regionNote('KEEP'), 'Đã xác nhận vùng khoanh đỏ là tiêu đề hoặc nội dung hợp lệ của phim'); assert.equal(R.regionNote('BLUR'), 'Đã xác nhận vùng khoanh đỏ là logo thương hiệu');
  assert.ok(script.includes(`b.dataset.decision==='KEEP'?'${R.regionNote('KEEP')}':'${R.regionNote('BLUR')}'`), 'region notes are the classic ones');
  assert.ok(n > 30 && regions > 5, `memory cases ${n}, region cards ${regions}`);
});
check('R2 write failures, retries and texts: writeFailureMessage, WRITE_RETRY_MS, decisionLabel, advisoryUndoMessage, sceneBlurMessage', () => {
  same(R.WRITE_RETRY_MS, cl.consts.WRITE_RETRY_MS);
  assert.ok(script.includes('const transient=!error.status||error.status>=500;'), 'classic retry rule');
  for (const [status, transient] of [[0, true], [500, true], [503, true], [400, false], [403, false], [409, false]]) assert.equal(R.transientWrite({status}), transient, String(status));
  const q = decideQueues()[0]; cl.load(q);
  const map = R.itemMap(q), errors = [{status: 0, message: 'Mất kết nối', attempts: 3}, {status: 400, message: 'Mục này chưa có vùng <x>', attempts: 1}, {status: 500, message: 'WinError 32: E:\\x', attempts: 3}, {status: 503, message: 'bận', attempts: 2}];
  let n = 0;
  for (const x of q.items.slice(0, 12).concat(q.advisory_items)) {
    const bodies = [['clear', {id: x.id}], ['decision', {id: x.id, decision: 'BLUR', full_frame: true, note: null}], ['decision', {id: x.id, decision: 'KEEP', full_frame: false, note: null}],
      ['decision', {id: x.id, decision: 'BLUR', full_frame: false, note: null, remember_platform_logo: true}], ['decision', {id: 'gone-id', decision: 'CUT', full_frame: false, note: null}]];
    for (const [kind, body] of bodies) for (const error of errors) { assert.equal(R.writeFailureMessage(kind, body, error, map.get(body.id)), cl.fn.writeFailureMessage(kind, body, error)); n++; }
    for (const variant of [{}, {decision: 'BLUR', decision_region_source_pixels: 'FULL_FRAME'}, {decision: 'BLUR', decision_region_source_pixels: {x: 1}}, {decision: 'NEEDS_MORE_CONTEXT'}, {decision: 'CUT'},
      {decision: 'KEEP', studio_logo_memory: {remembered: true, frames: 3, ignored_regions: [1]}}, {decision: 'BLUR', platform_logo_memory: {remembered: true, platform: {key: 'unknown', name: 'X'}}}]) {
      const y = {...x, ...variant};
      assert.equal(R.decisionLabel(y), cl.fn.decisionLabel(y)); assert.equal(R.advisoryUndoMessage(y), cl.fn.advisoryUndoMessage(y));
    }
    if (R.isScene(x)) assert.equal(R.sceneBlurMessage(x), cl.fn.sceneBlurMessage(x));
  }
  assert.ok(n >= 300, 'messages ' + n);
});

check('R2 deliberate differences are explicit: S5 (V2 confirm dialog, classic words), S6, S8, R2-K; undo titles are the classic ones', () => {
  // S5: the classic page asks with window.confirm()/alert(); the dialog asks the same strings (compared above) in its own <dialog>.
  assert.ok(/(^|[^\w.])confirm\(/.test(script) && /(^|[^\w.])alert\(/.test(script), 'the classic page uses confirm()/alert()');
  for (const file of ['review.js', 'review-cards.js', 'review-core.js', 'review-detail.js', 'review-media.js']) {
    assert.ok(!/(^|[^\w.])(confirm|alert|prompt)\(/.test(fs.readFileSync(path.join(__dirname, file), 'utf8')), file + ': no native dialog');
  }
  const header = fs.readFileSync(path.join(__dirname, 'review-core.js'), 'utf8').slice(0, 3200);
  for (const tag of ['S1', 'S2', 'S6', 'S7', 'S8', 'R2-K', 'S5']) assert.ok(header.includes(tag), 'review-core.js lists ' + tag);
  // Undo button titles (classic updateNavState).
  assert.ok(script.includes('`Lựa chọn cho ứng viên phụ ${catName(item)} ${span(item)} không hoàn tác được (phím Z để xem lý do)`'));
  assert.ok(script.includes('`Hoàn tác lựa chọn cho ${catName(item)} ${span(item)} (phím Z)`') && script.includes("'Chưa có lựa chọn nào trong phiên này để hoàn tác'"));
  const x = QUEUES.edge.items[0];
  assert.equal(R.undoTitle({advisory: false}, x), `Hoàn tác lựa chọn cho ${R.catName(x)} ${R.span(x)} (phím Z)`);
  assert.equal(R.undoTitle({advisory: true}, x), `Lựa chọn cho ứng viên phụ ${R.catName(x)} ${R.span(x)} không hoàn tác được (phím Z để xem lý do)`);
  assert.equal(R.undoTitle(undefined, undefined), 'Chưa có lựa chọn nào trong phiên này để hoàn tác');
  // The autoNext key and its default (on unless "0"), shared with the classic page.
  assert.ok(script.includes("autoNext=localStorage.getItem('biliflow.review.autoNext')!=='0'"));
  assert.ok(fs.readFileSync(path.join(__dirname, 'review.js'), 'utf8').includes("localStorage.getItem(AUTO_KEY) !== '0'") && fs.readFileSync(path.join(__dirname, 'review.js'), 'utf8').includes("AUTO_KEY = 'biliflow.review.autoNext'"));
  process.stdout.write('   S5 confirm in a V2 dialog (same words) · S6 export in flight only · S8 SKIPPED read-only · R2-K BLUR of a region item = its region\n');
});

/* R3 (R2-B2): S9 on the real card markup (review-cards.js in a sandbox, no DOM needed to build a card). */
function cardsModule() {
  const box = {window: {BFReviewCore: R, BFReviewDetail: D}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'review-cards.js'), 'utf8'), box);
  return box.window.BFReviewCards;
}
function s9Queue() {
  // "Kiểm tra đoạn kết" (no region of its own) beside the BLUR platform logo card whose red box it borrows, and a
  // safety card that borrows the same box.
  const q = Mock.reviewQueue(job101, {count: 30});
  const logo = q.items.find(x => x.id === 'visual_logo-101-0007'), end = q.items.find(x => x.id === 'visual_logo-101-0008'), gore = q.items.find(x => x.id === 'gore-101-0014');
  logo.decision = 'BLUR'; logo.decision_region_source_pixels = logo.suggested_region_source_pixels;
  Object.assign(end, {candidate_type: 'ending_boundary', decision: null, start_seconds: logo.start_seconds - 0.5, end_seconds: logo.end_seconds + 0.5});
  Object.assign(gore, {start_seconds: logo.start_seconds + 0.2, end_seconds: logo.end_seconds - 0.2});
  return {q, logo, end, gore};
}
check('R3 S9 (R2-B2): a card borrowing another logo card\'s red box has no region buttons, a line about the owner card, "Đi tới thẻ logo" and a dashed box', () => {
  const Cards = cardsModule(), {q, logo, end, gore} = s9Queue(), map = R.itemMap(q);
  const ctx = {queue: q, map, focusId: null, zoomId: null, readonly: false, techOpen: new Set(), media: () => ({ev: null, hasKey: false, playable: false, reason: '', frameUrl: () => '', mediaUrl: p => '/media/' + p})};
  for (const x of [end, gore]) {
    assert.equal(R.regionOwner(q, x).id, logo.id, x.id + ' borrows the logo box (classic regionOwner)');
    const html = Cards.card(ctx, x);
    assert.ok(!/data-review="region"/.test(html), x.id + ': no region button, not even in "Chi tiết kỹ thuật"');
    const go = /<button[^>]*data-review="goto" data-owner="([^"]+)"[^>]*>Đi tới thẻ logo<\/button>/.exec(html);
    assert.ok(go && go[1] === logo.id, x.id + ': "Đi tới thẻ logo" points to the owner card');
    const text = D.borrowedRegion(x, logo).text;
    assert.equal(text, `Khung đỏ là vùng logo của thẻ “Logo nền tảng Nền tảng mẫu” (${D.clock(logo.start_seconds)}–${D.clock(logo.end_seconds)}) — đang: Làm mờ logo`);
    assert.ok(html.includes(D.esc(text)), x.id + ': the owner line');
    assert.match(html, /<span class="rv-region blurred borrowed[^"]*"[^>]*><em>áp dụng \d\d:\d\d\.\d–\d\d:\d\d\.\d<\/em><\/span>/, x.id + ': dashed box with the time it applies');
  }
  const own = Cards.card(ctx, logo);
  assert.equal((own.match(/data-review="region"/g) || []).length, 2, 'the owner card keeps its 2 region buttons');
  assert.ok(!/data-review="goto"/.test(own) && !/rv-region[^"]*borrowed/.test(own), 'the owner card: solid box, no link');
  // The classic page shows the buttons on the borrowing card too: listed as deliberate difference S9 (payload unchanged).
  cl.load(q);
  assert.match(cl.fn.regionControlsHtml(end, cl.fn.regionOwner(end), logo.decision_region_source_pixels), /Xử lý riêng vùng logo khoanh đỏ.*data-act="region" data-owner="visual_logo-101-0007"/);
  const header = fs.readFileSync(path.join(__dirname, 'review-core.js'), 'utf8').slice(0, 3200);
  assert.ok(header.includes('S9'), 'review-core.js lists S9');
  process.stdout.write('   S9: borrowing cards link to the owner card; the classic page shows region buttons on both\n');
});
/* S10, anime gore hint (docs/ANIME_GORE_PLAN.md step 1): the tagger line follows the suggestion on V2 cards only;
 * the classic suggestionLine is unchanged (D2). */
check('S10 gore hint: a gore card shows the tagger line (gore_hint) after the suggestion, escaped; no other category shows it; the classic page does not (D2)', () => {
  const Cards = cardsModule(), q = Mock.reviewQueue(job101, {count: 30}), map = R.itemMap(q);
  const ctx = {queue: q, map, focusId: null, zoomId: null, readonly: false, techOpen: new Set(), media: () => ({ev: null, hasKey: false, playable: false, reason: '', frameUrl: () => '', mediaUrl: p => '/media/' + p})};
  const gore = q.items.find(x => x.category === 'gore'), other = q.items.find(x => x.category === 'violence' || x.category === 'adult');
  assert.ok(gore && other, 'the mock queue has a gore card and another safety card');
  const hint = 'Tagger: không thấy máu · có dấu hiệu xác (yếu), nên xem kỹ <b>';
  const hintLine = h => (/<p class="rv-hint">([^<]*)<\/p>/.exec(h) || [])[1] ?? null;
  for (const suggested of ['BLUR', null]) {
    Object.assign(gore, {gore_hint: hint, suggested_decision: suggested, decision: null, ai_visual_audit: null, advisory: false});
    const tip = (suggested ? 'Đề xuất: ' + R.actionName(gore, suggested) + ' · ' : '') + hint;
    assert.equal(hintLine(Cards.card(ctx, gore)), esc(tip), 'V2 card, suggestion ' + suggested);
    // S10: the classic line keeps only its suggestion.
    assert.equal(cl.fn.suggestionLine(gore), suggested ? '<div class="hint">' + esc('Đề xuất: ' + R.actionName(gore, suggested)) + '</div>' : '', 'classic suggestionLine, suggestion ' + suggested);
  }
  Object.assign(other, {gore_hint: hint, suggested_decision: 'BLUR', decision: null, ai_visual_audit: null, advisory: false});
  assert.equal(hintLine(Cards.card(ctx, other)), esc('Đề xuất: ' + R.actionName(other, 'BLUR')), 'no tagger line outside gore');
  delete gore.gore_hint;
  assert.equal(hintLine(Cards.card(ctx, gore)), null, 'a gore card without a hint or suggestion has no hint line');
  assert.ok(!script.includes('gore_hint'), 'D2: the classic page has no tagger line');
  assert.ok(fs.readFileSync(path.join(__dirname, 'review-core.js'), 'utf8').slice(0, 3200).includes('S10'), 'review-core.js lists S10');
  process.stdout.write('   S10: the tagger line on V2 gore cards only; escaped; the classic page unchanged\n');
});
check('R4-B2: a zoomed card borrowing a logo card\'s red box has one label on that box ("áp dụng …"); the approved red box of the same logo card adds none', () => {
  const Cards = cardsModule(), {q, logo, end} = s9Queue(), map = R.itemMap(q);
  // A yellow AI box covered by another card keeps its own label; one covered by the logo card itself has none (the
  // red box of that card carries "áp dụng …").
  const own = logo.suggested_region_source_pixels;
  end.evidence_regions = [{x: 100, y: 900, width: 200, height: 100, sources: ['visual_ai'], covered_by: 'visual_logo-101-0001'},
    {x: own.x, y: own.y, width: own.width, height: own.height, sources: ['visual_ai'], covered_by: logo.id}];
  end.evidence_frame_size = [1920, 1080];
  const ctx = {queue: q, map, focusId: null, zoomId: end.id, readonly: false, techOpen: new Set(), media: () => ({ev: null, hasKey: false, playable: false, reason: '', frameUrl: () => '', mediaUrl: p => '/media/' + p})};
  const view = D.evidenceView(q, end);
  assert.equal(view.mode, 'boxes');
  assert.ok(view.marks.some(b => b.a && b.o === logo.id), 'the zoomed card still draws the approved red box of the logo card');
  const html = Cards.card(ctx, end), labels = [...html.matchAll(/<em>([^<]*)<\/em>/g)].map(m => m[1]);
  assert.match(html, /<span class="rv-aibox approved"[^>]*><\/span>/, 'the approved red box, without a label');
  assert.equal(labels.filter(t => t.startsWith('áp dụng ')).length, 1, 'one "áp dụng …" label');
  assert.ok(!labels.includes('đã duyệt làm mờ ở thẻ riêng'), 'no second label on the same box: ' + JSON.stringify(labels));
  assert.equal(labels.filter(t => t === 'watermark — đã có thẻ riêng').length, 1, 'only the yellow box covered by another card has a label: ' + JSON.stringify(labels));
  assert.equal((html.match(/class="rv-aibox(?: (?!approved)[^"]*)?"/g) || []).length, 2, 'both yellow boxes are drawn');
  assert.ok(view.legend.includes('Khung đỏ: vùng') && view.legend.includes('đã được duyệt làm mờ ở thẻ riêng'), 'the legend still says the red box is approved on its own card');
});
/* Session R (job 134 on a phone, docs/handoff/R-review-ui.md): the red box hid the faint text it marked; small text
 * was unreadable; nobody knew what the OCR read; "subtitle" on a preview read as a verdict. V2 only (S11): the
 * classic page stays byte-identical (D2). */
const cardCtx = (q, extra) => ({queue: q, map: R.itemMap(q), focusId: null, zoomId: null, readonly: false, techOpen: new Set(),
  media: () => ({ev: null, hasKey: false, playable: false, reason: '', frameUrl: () => '', mediaUrl: p => '/media/' + p}), ...(extra || {})});
const cssRules = () => {
  const css = fs.readFileSync(path.join(__dirname, 'review.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => ({selector: m[1].trim(), body: m[2]}));
};
check('Session R (1): the red box and the AI boxes are a line 2 px outside their region, never a fill or a border over it; dashed when borrowed; the veil only on "Làm mờ"', () => {
  const rules = cssRules(), rule = sel => rules.find(r => r.selector === sel);
  assert.match(rule('.rv-region').body, /outline: 2px solid #ff304f;.*outline-offset: 2px;/);
  assert.match(rule('.rv-aibox').body, /outline: 2px dashed #ffc233;.*outline-offset: 2px;/);
  assert.match(rule('.rv-aibox.approved').body, /^\s*outline: 3px solid #ff304f;\s*$/);
  assert.match(rule('.rv-region.borrowed').body, /^\s*outline-style: dashed;\s*$/);
  // Every rule that paints a box itself (not its label): no border and no background, except the "Làm mờ" veil.
  const boxRules = rules.filter(r => r.selector.split(',').some(s => /\.rv-(region|aibox)(?![\w-])(?![^,]*\bem\b)/.test(s)));
  assert.ok(boxRules.length >= 5, 'box rules found: ' + boxRules.length);
  for (const r of boxRules) {
    assert.ok(!/(^|[;\s])border(-(top|right|bottom|left|width|style|color))?\s*:/.test(r.body), r.selector + ' has no border');
    if (r.selector === '.rv-region.blurred') assert.match(r.body, /^\s*background: #ffffffb3;\s*$/);
    else assert.ok(!/background/.test(r.body), r.selector + ' has no fill');
  }
  // The labels hang off the line (5 px) and wrap within the image left on their side (R4-B1, --room box widths).
  assert.match(rule('.rv-region em').body, /left: 0; top: calc\(100% \+ 5px\);/);
  assert.match(rule('.rv-region em, .rv-aibox em').body, /max-width: calc\(var\(--room, 1\) \* 100% - 2px\);/);
});
check('Session R (1): "Ẩn khung / Hiện khung" shows a card\'s image without the red box, its label, the AI boxes, the veil and the cut illustration', () => {
  const Cards = cardsModule(), q = Mock.reviewQueue(job101, {count: 30}), logo = q.items.find(x => x.id === 'visual_logo-101-0001');
  Object.assign(logo, {decision: 'CUT', decision_region_source_pixels: null});
  const shown = Cards.card(cardCtx(q), logo), bare = Cards.card(cardCtx(q, {bare: new Set([logo.id])}), logo);
  assert.match(shown, /<button class="small secondary rv-frames-btn" type="button" data-review="frames" aria-pressed="false" title="Ẩn khung đỏ và phần minh họa của trang duyệt \(khung và nhãn do máy quét in sẵn trong ảnh vẫn còn\)">Ẩn khung<\/button>/);
  assert.match(bare, /<button class="small secondary rv-frames-btn" type="button" data-review="frames" aria-pressed="true" title="Vẽ lại khung đỏ và phần minh họa lên ảnh">Hiện khung<\/button>/);
  assert.match(bare, /^<article class="scene rv-card decided d-cut bare" /);
  assert.ok(!/^<article[^>]* bare/.test(shown), 'not bare by default');
  const noImage = q.items.find(x => !(x.preview_images || []).length);
  assert.ok(noImage && !Cards.card(cardCtx(q), noImage).includes('data-review="frames"'), 'no image, no button');
  // "Đã chọn cắt" shows through `.rv-card.d-cut .rv-cut-label` (3 classes): the bare rule needs 4 to win.
  const hidden = cssRules().find(r => r.selector === '.rv-card.bare .rv-region, .rv-card.bare .rv-boxes, .rv-card.bare.d-cut .rv-cut-label');
  assert.ok(hidden && /^\s*display: none;\s*$/.test(hidden.body), 'bare hides the boxes, their labels and "Đã chọn cắt"');
  assert.ok(cssRules().some(r => r.selector === '.rv-card.d-cut .rv-cut-label'), 'the rule it must beat');
  const only = cssRules().find(r => r.selector === '.rv-card:not(.d-cut):not(.d-blur-full):not(:has(.rv-region, .rv-aibox, .rv-cover)) .rv-frames-btn');
  assert.ok(only && /^\s*display: none;\s*$/.test(only.body), 'no "Ẩn khung" where nothing is drawn over the image');
  const plain = cssRules().find(r => r.selector === '.rv-card.bare.d-blur-full .rv-art img, .rv-card.bare.d-cut .rv-art img');
  assert.ok(plain && /^\s*filter: none;\s*$/.test(plain.body), 'bare shows the image without the blur or cut illustration');
});
check('Session R (2): the magnifier: 3× its size on the card, else the largest zoom down to 2× that fits, 3× scrolling sideways when not even 2× fits; tall regions fit 300 px; room around the region; letterboxed images', () => {
  const near = (a, b, label) => assert.ok(Math.abs(a - b) < 1e-6, label + ': ' + a + ' ≠ ' + b);
  // The faint watermark of job 134 on a 390 px phone: 171 × 23 px of a 1280 × 536 frame, its 960 × 402 preview in a 366 px card.
  const small = D.magnifier({x: 556, y: 448, width: 171, height: 23}, [1280, 536], 960, 402, 366, 153.3, 342);
  assert.equal(small.zoom, 3); assert.equal(small.scroll, false); assert.ok(small.cssW <= 342);
  const k = 366 / 960; // CSS px per image px on the card
  near(small.red.w, 171 * .75 * k * 3, 'region width at 3×'); near(small.red.h, 23 * .75 * k * 3, 'region height at 3×');
  assert.ok(small.sy <= 448 * .75 - 23 * .75 * .6 + 1e-9 && small.sy + small.sh >= (448 + 23 * 1.6) * .75 - 1e-9, 'text just above or below the box stays in view');
  assert.ok(small.red.x > 0 && small.red.y > 0 && small.red.x + small.red.w < small.cssW && small.red.y + small.red.h < small.cssH, 'the region is inside the crop');
  // A full-width line at the top: wider than the card even at 2×, so it stays at 3× and scrolls.
  const wide = D.magnifier({x: 0, y: 21, width: 1280, height: 32}, [1280, 536], 960, 402, 366, 153.3, 342);
  assert.equal(wide.zoom, 3); assert.equal(wide.scroll, true); near(wide.cssW, 960 * k * 3, 'three times the card width');
  // Wider than the card at 3× but not at 2×: the largest zoom that fits, no scrolling.
  const fits2 = D.magnifier({x: 300, y: 300, width: 470, height: 40}, [1280, 536], 1280, 536, 366, 153.3, 342);
  assert.ok(fits2.zoom >= 2 && fits2.zoom < 2.2 && fits2.scroll === false, JSON.stringify(fits2));
  // Between: the largest zoom (2–3×) that still fits.
  const mid = D.magnifier({x: 400, y: 300, width: 400, height: 40}, [1280, 536], 1280, 536, 366, 153.3, 342);
  assert.ok(mid.zoom > 2 && mid.zoom < 3 && mid.scroll === false && Math.abs(mid.cssW - 342) < 1e-6, JSON.stringify(mid));
  // A tall logo on a PC card: fitted to 300 px; a region already large on the card (under 1.5×): no magnifier.
  const tall = D.magnifier({x: 1700, y: 100, width: 80, height: 300}, [1920, 1080], 1920, 1080, 620, 348.75, 596);
  assert.ok(tall && Math.abs(tall.cssH - 300) < 1e-6 && tall.zoom > 2 && tall.zoom < 3 && tall.sx >= 1700 - 80 - 1e-9, JSON.stringify(tall));
  assert.equal(D.magnifier({x: 1700, y: 100, width: 120, height: 600}, [1920, 1080], 1920, 1080, 620, 348.75, 596), null, 'already ≥ 190 px tall on the card');
  assert.equal(D.magnifier({x: 200, y: 100, width: 800, height: 400}, [1280, 536], 1280, 536, 366, 153.3, 342), null);
  // A 16:9 frame letterboxed in a 4:3 image: the crop follows the picture, not the bars.
  const box = D.magnifier({x: 60, y: 40, width: 260, height: 96}, [1920, 1080], 640, 480, 600, 450, 576), offsetY = (480 - 1080 / 3) / 2;
  assert.ok(box.sy <= 40 / 3 + offsetY && box.sy + box.sh >= (40 + 96) / 3 + offsetY && box.sx <= 20, JSON.stringify(box));
  for (const bad of [[0, 0, 366, 153, 342], [960, 402, 0, 0, 342], [960, 402, 366, 153, 0]]) assert.equal(D.magnifier({x: 1, y: 1, width: 5, height: 5}, [1280, 536], ...bad), null);
  // Markup: every card with an image has one hidden magnifier (review.js draws it); none has the old crop.
  const Cards = cardsModule(), q = Mock.reviewQueue(job101, {count: 30});
  for (const x of q.items) {
    const html = Cards.card(cardCtx(q, {zoomId: x.id}), x), mags = (html.match(/<figure class="rv-mag" hidden>/g) || []).length;
    assert.equal(mags, (x.preview_images || []).length ? 1 : 0, x.id);
    assert.ok(!/rv-crop/.test(html), x.id + ': no zoom crop');
  }
  assert.ok(!/drawCrop|rv-crop/.test(fs.readFileSync(path.join(__dirname, 'review.js'), 'utf8') + fs.readFileSync(path.join(__dirname, 'review.css'), 'utf8')));
});
check('Session R (3): "Máy đọc": the group read in most forms, then the lines least like it; cut at 70; escaped; readings only', () => {
  const lines = x => { const r = D.readings(x); return r && r.lines.map(l => l.text + (l.forms > 1 ? ' ×' + l.forms : '')); };
  // A subtitle band holding a faint site name in many OCR forms: the site name, then two different subtitle lines.
  const band = {category: 'text', labels: ['TRANG.EXAMPLE', 'TRANGEXAMPLE', 'TRANG EXAMPLE', 'TRAN6.EXAMPLE', 'Anh có nghe thấy tôi nói không?',
    'Ngày mai chúng ta lên đường sớm.', 'Ngày mai chúng ta lên đường', 'Được.', 'Ừ.', 'Tôi biết rồi, đừng nhắc nữa.']};
  const shown = lines(band), subtitles = ['Ngày mai chúng ta lên đường sớm. ×2', 'Anh có nghe thấy tôi nói không?', 'Tôi biết rồi, đừng nhắc nữa.'];
  assert.equal(shown[0], 'TRANG.EXAMPLE ×4');
  assert.ok(shown.length === 3 && shown.slice(1).every(l => subtitles.includes(l)) && shown[1] !== shown[2], JSON.stringify(shown));
  assert.equal(D.readings(band).more, 3, 'one subtitle line, "Được." and "Ừ." are not shown');
  // A scrolling line read in pieces: the longest piece first, cut at 70 characters.
  const long = 'Phim cập nhật nhanh nhất tại TRANG.EXAMPLE, phim chỉ đăng trên trang này, trang khác đều là bản chép lại.';
  const ticker = {category: 'text', labels: ['Vie', 'cập nhật nhanh nhất tại TRANG.EXAMPLE', long, 'ủng hộ-']};
  const t = D.readings(ticker);
  assert.equal(t.lines[0].text, long.slice(0, 69).trimEnd() + '…'); assert.ok(t.lines[0].text.length <= 70); assert.equal(t.lines[0].forms, 2, 'the long line and the piece inside it');
  // Logo cards: scanner labels, lowercase prompts and platform names are not readings; wrapped readings are unwrapped.
  assert.deepEqual(lines({category: 'visual_logo', labels: ['Persistent external logo / watermark', 'a company logo', 'Logo nền tảng iQIYI', 'OCR nội dung phim: GREECE', 'Có khả năng là tiêu đề phim: TÊN PHIM']}), ['TÊN PHIM', 'GREECE']);
  assert.equal(D.readings({category: 'visual_logo', labels: ['Opening boundary review', 'Visual brand/logo candidate']}), null);
  assert.equal(D.readings({category: 'gore', labels: ['Cảnh máu']}), null);
  assert.deepEqual(lines({category: 'text', labels: ['dừng', 'quấn']}).sort(), ['dừng', 'quấn'], 'lowercase OCR words stay on text cards');
  // On the card, escaped, after the time line.
  const Cards = cardsModule(), q = Mock.reviewQueue(job101, {count: 30}), x = q.items.find(i => i.category === 'text');
  x.labels = ['<img src=x onerror=alert(1)>', 'TRANG.EXAMPLE'];
  const html = Cards.card(cardCtx(q), x);
  assert.match(html, /<\/small><div class="rv-ocr"><b>Máy đọc:<\/b><span class="rv-ocr-line">“&lt;img src=x onerror=alert\(1\)&gt;”<\/span><span class="rv-ocr-line">“TRANG.EXAMPLE”<\/span><\/div>/);
  assert.ok(!/<img src=x/.test(html));
  const safety = q.items.find(i => i.category === 'adult');
  assert.ok(!Cards.card(cardCtx(q), safety).includes('rv-ocr'), 'no line on a safety card');
});
check('Session R (3): the note under a text preview says "subtitle" / "top-right" on the image is a zone name; it follows the image shown', () => {
  const p = n => 'reports/jobs/demo/text/text-previews/track-' + String(n).padStart(5, '0') + '.jpg';
  const x = {category: 'text', text_preview_zones: {[p(58)]: 'subtitle', [p(689)]: 'top-right', [p(7)]: 'edge-strip'}};
  assert.equal(D.zoneNote(x, p(58)), 'Nhãn “REVIEW 58 | subtitle” trên ảnh do máy quét chữ in: “subtitle” chỉ là tên vùng (vùng phụ đề), không có nghĩa đây là phụ đề.');
  assert.equal(D.zoneNote(x, p(689)), 'Nhãn “REVIEW 689 | top-right” trên ảnh do máy quét chữ in: “top-right” chỉ là tên vùng (góc trên bên phải), không phải kết luận.');
  assert.equal(D.zoneNote(x, p(7)), 'Nhãn “REVIEW 7 | edge-strip” trên ảnh do máy quét chữ in: “edge-strip” chỉ là tên vùng trong khung, không phải kết luận.');
  assert.match(D.zoneNote({category: 'text'}, p(275)), /^Nhãn “REVIEW 275 \| …” trên ảnh do máy quét chữ in: phần sau dấu \| chỉ là tên vùng trong khung \(subtitle = vùng phụ đề, top-right = góc trên bên phải…\)/);
  for (const path of ['demo/poster-blue.svg', 'reports/jobs/demo/logo-0001-0.000s.jpg', '']) assert.equal(D.zoneNote(x, path), '');
  assert.deepEqual(Object.keys(D.ZONE_NAMES).sort(), ['bottom-center', 'bottom-left', 'bottom-right', 'middle-center', 'middle-left', 'middle-right', 'subtitle', 'top-center', 'top-left', 'top-right']);
  // The card: the note for its first preview, escaped; preview frames of the strip carry their path for review.js.
  const Cards = cardsModule(), q = Mock.reviewQueue(job101, {count: 30}), card = q.items.find(i => i.category === 'text');
  card.preview_images = [p(58), p(689)]; card.text_preview_zones = {[p(58)]: 'subtitle"><b>', [p(689)]: 'top-right'};
  const html = Cards.card(cardCtx(q), card);
  assert.ok(html.includes('<p class="rv-zone-note">Nhãn “REVIEW 58 | subtitle&quot;&gt;&lt;b&gt;” trên ảnh'), 'escaped note');
  assert.equal((html.match(/data-path="reports\/jobs\/demo\/text\/text-previews\/track-00\d{3}\.jpg"/g) || []).length, 2);
  const other = q.items.find(i => i.category === 'visual_logo' && (i.preview_images || []).length);
  assert.match(Cards.card(cardCtx(q), other), /<p class="rv-zone-note" hidden><\/p>/);
  assert.ok(fs.readFileSync(path.join(__dirname, 'review-core.js'), 'utf8').slice(0, 3600).includes('S11'), 'review-core.js lists S11');
  assert.ok(!/rv-zone-note|rv-ocr|rv-mag|text_preview_zones/.test(script), 'D2: the classic page is unchanged');
});
/* Session D (docs/handoff/D-preview-blurs.md): a card's image showed only its own red box while other cards blurred the
 * watermark under the subtitles and the text at the top for the whole film (the export was clean), and the note on
 * other cards' blurs was on safety cards only and counted fixed overlays only. Now the card draws the regions other
 * cards blur at the time of its image, and "Chi tiết kỹ thuật" names them on every card. V2 only (S12): the classic
 * page is unchanged (D2); decisions and POST bodies are unchanged. */
const COVER_FRAME = [1280, 720];
function coverQueue(frame) {
  const size = frame || COVER_FRAME, box = (x, y, width, height) => ({x, y, width, height});
  const card = (id, extra) => ({id, category: 'text', review_kind: 'text', priority: 'medium', start_seconds: 0, end_seconds: 600, source_frame_size: size,
    labels: [], reasons: [], preview_images: [], decision: null, suggested_decision: null, ...extra});
  const blur = (r, extra) => ({suggested_region_source_pixels: r, decision: 'BLUR', decision_region_source_pixels: r, ...extra});
  const items = [
    card('dlg', {start_seconds: 100, end_seconds: 110, suggested_region_source_pixels: box(300, 560, 680, 60), decision: 'KEEP', decision_region_source_pixels: box(300, 560, 680, 60),
      preview_images: ['reports/jobs/demo/text/text-previews/track-00012.jpg'], labels: ['Ngày mai chúng ta đi sớm']}),
    card('wm', blur(box(552, 643, 176, 31), {candidate_type: 'repeated_watermark', temporal_policy: 'discrete_detected_intervals', detected_intervals: [{start_seconds: 0, end_seconds: 600}], labels: ['WWW.EXAMPLE.COM <b>']})),
    card('top', blur(box(837, 44, 319, 47), {review_kind: 'logo_overlay', candidate_type: 'persistent_overlay', labels: ['EXAMPLE.COM']})),
    card('band', blur(box(0, 21, 1280, 32), {review_kind: 'logo_overlay', candidate_type: 'persistent_overlay'})),
    card('scroll', {candidate_type: 'scrolling_text', start_seconds: 105, end_seconds: 200, suggested_decision: 'BLUR', suggested_region_source_pixels: box(0, 680, 1280, 40)}),
    card('moments', blur(box(500, 300, 100, 50), {category: 'visual_logo', review_kind: 'logo_overlay', temporal_policy: 'discrete_detected_intervals', start_seconds: 95, end_seconds: 115,
      detected_intervals: [{start_seconds: 95, end_seconds: 102}, {start_seconds: 108, end_seconds: 115}]})),
    card('later', blur(box(20, 20, 100, 100), {category: 'visual_logo', review_kind: 'logo_overlay', start_seconds: 300, end_seconds: 400})),
    card('kept', {candidate_type: 'repeated_watermark', suggested_region_source_pixels: box(556, 609, 201, 58), suggested_decision: 'BLUR', decision: 'KEEP', decision_region_source_pixels: box(556, 609, 201, 58)}),
    card('whole', {category: 'gore', review_kind: 'gore', start_seconds: 104, end_seconds: 106, decision: 'BLUR', decision_region_source_pixels: 'FULL_FRAME'}),
    card('fight', {category: 'violence', review_kind: 'violence', start_seconds: 100, end_seconds: 110, preview_images: ['reports/jobs/demo/violence/frame-104.000s.jpg']}),
    card('scene', {category: 'gore', review_kind: 'gore', temporal_policy: 'discrete_detected_intervals', start_seconds: 96, end_seconds: 112,
      detected_intervals: [{start_seconds: 96, end_seconds: 101}, {start_seconds: 109, end_seconds: 112}]}),
  ];
  const advisory = [card('maybe', {advisory: true, start_seconds: 100, end_seconds: 110, suggested_decision: 'BLUR', suggested_region_source_pixels: box(10, 10, 50, 50)})];
  return {status: 'WAITING_REVIEW', source: {duration_seconds: 600}, items, advisory_items: advisory};
}
const coverItem = (q, id) => q.items.find(x => x.id === id);
/* review-media.js on fake canvases: every 2D call is logged as [canvas, call, ...arguments] (canvases and images by
 * name); alpha is what a cut from the image paints in its middle (0: nothing, as Chromium for a part of a sizeless SVG). */
function fakeCanvases(alpha) {
  const ctx = {window: {BFReviewCore: R}}, calls = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'review-media.js'), 'utf8'), ctx);
  const pen = name => ({drawImage: (...a) => calls.push([name, ...a.map(v => typeof v === 'object' ? v.name : v)]), getImageData: () => ({data: [0, 0, 0, alpha]}),
    save: () => calls.push([name, 'save']), restore: () => calls.push([name, 'restore']), setLineDash: d => calls.push([name, 'dash', ...d]), strokeRect: (...a) => calls.push([name, 'stroke', ...a]),
    set strokeStyle(v) { calls.push([name, 'color', v]); }, set lineWidth(v) { calls.push([name, 'width', v]); }, set imageSmoothingEnabled(v) { /* not logged */ }, set imageSmoothingQuality(v) { /* not logged */ }});
  let made = 0;
  const doc = {createElement: tag => { const c = {name: tag + ++made}; c.getContext = () => pen(c.name); calls.push(['create', c.name]); return c; }};
  const canvas = name => ({name, dataset: {}, style: {}, ownerDocument: doc, getContext: () => pen(name)});
  return {Media: ctx.window.BFReviewMedia, calls, canvas};
}
check('Session D (1): the regions other cards blur, as the export: a decided BLUR region over start–end, or over each moment of a discrete card; a suggested BLUR as pending; never KEEP, whole-frame BLUR, candidates, the card itself or the red box it borrows', () => {
  const q = coverQueue(), covers = D.blurCovers(q, coverItem(q, 'dlg'));
  same(covers.map(c => [c.id, c.state]), [['wm', 'applied'], ['top', 'applied'], ['band', 'applied'], ['scroll', 'pending'], ['moments', 'applied']]);
  const [wm] = covers, by = id => covers.find(c => c.id === id);
  same(wm.box, {left: 552 / 1280, top: 643 / 720, width: 176 / 1280, height: 31 / 720});
  same(wm.region, {x: 552, y: 643, width: 176, height: 31}); same(wm.size, [1280, 720]); same(wm.moments, [{start: 0, end: 600}]);
  same(covers.map(c => [c.kind, c.scope, c.short]), [['watermark', 'toàn phim', 'toàn phim'], ['chữ cố định', 'toàn phim', 'toàn phim'], ['chữ cố định', 'toàn phim', 'toàn phim'],
    ['dòng chữ chạy', '01:45.0–03:20.0', '1:45–3:20'], ['logo', '2 khoảnh khắc 01:35.0–01:55.0', '2 khoảnh khắc']]);
  same(by('moments').moments, [{start: 95, end: 102}, {start: 108, end: 115}]);
  assert.equal(wm.reading, 'WWW.EXAMPLE.COM <b>', 'plain text (escaped where it is shown)');
  // A violence card without a region borrows the red box of the fixed logo card: that one is its red box, not a cover.
  const fight = coverItem(q, 'fight');
  assert.equal(R.regionBox(q, fight).owner, 'top');
  same(D.blurCovers(q, fight).map(c => c.id), ['wm', 'band', 'scroll', 'moments']);
  // A logo card visual_logo blurs as 'logo'; a fixed region read by the logo scanner is a fixed logo.
  same(D.blurCovers(q, coverItem(q, 'later')).map(c => [c.id, c.kind]), [['wm', 'watermark'], ['top', 'chữ cố định'], ['band', 'chữ cố định']]);
  const logoQ = coverQueue(); coverItem(logoQ, 'top').category = 'visual_logo';
  assert.equal(D.blurCovers(logoQ, coverItem(logoQ, 'dlg'))[1].kind, 'logo cố định');
  // No frame size, no region or a broken one, a cleared decision: nothing to draw.
  const broken = coverQueue();
  coverItem(broken, 'wm').source_frame_size = null; coverItem(broken, 'top').decision_region_source_pixels = null; coverItem(broken, 'band').decision_region_source_pixels = {x: 0, y: 0, width: 0, height: 9};
  Object.assign(coverItem(broken, 'moments'), {decision: null, suggested_decision: 'KEEP'});
  same(D.blurCovers(broken, coverItem(broken, 'dlg')).map(c => c.id), ['scroll']);
  same(D.blurCovers({items: []}, coverItem(q, 'dlg')), []);
  // A region that starts left of or above the frame keeps only its part inside it; one wholly outside is not drawn.
  const edge = coverQueue();
  coverItem(edge, 'top').decision_region_source_pixels = {x: -40, y: -10, width: 100, height: 30};
  coverItem(edge, 'band').decision_region_source_pixels = {x: -200, y: 5, width: 100, height: 30};
  same(D.blurCovers(edge, coverItem(edge, 'dlg')).filter(c => c.id === 'top' || c.id === 'band').map(c => [c.id, c.box]), [['top', {left: 0, top: 0, width: 60 / 1280, height: 20 / 720}]]);
});
check('Session D (2): what is drawn follows the time of the image: a moment holds it (±0.05 s), a discrete card counts in its moments only; an image without a time (a text preview) needs one moment over the whole card, over each moment of a discrete card', () => {
  const q = coverQueue(), dlg = coverItem(q, 'dlg'), covers = D.blurCovers(q, dlg), at = t => D.coversAt(covers, dlg, t).map(c => c.id);
  same(at(105), ['wm', 'top', 'band', 'scroll'], 'in the gap between the two moments');
  same(at(100.5), ['wm', 'top', 'band', 'moments'], 'before the scrolling line');
  same(at(104.96), ['wm', 'top', 'band', 'scroll'], '0.05 s early still counts');
  same(at(104.9), ['wm', 'top', 'band']);
  same(at(102.04), ['wm', 'top', 'band', 'moments']); same(at(102.1), ['wm', 'top', 'band']);
  for (const t of [null, undefined, '', 'x']) same(at(t), ['wm', 'top', 'band'], 'no time: ' + t);
  const scene = coverItem(q, 'scene');
  same(D.coversAt(D.blurCovers(q, scene), scene, null).map(c => c.id), ['wm', 'band', 'moments'], 'each moment of the scene lies in a moment of the other card');
  same(D.coversAt(D.blurCovers(q, scene), scene, 105).map(c => c.id), ['wm', 'band', 'scroll']);
});
check('Session D (3): a card that nothing else blurs draws nothing (an empty layer, no note, no "Ẩn khung" for it alone)', () => {
  const Cards = cardsModule(), q = coverQueue();
  q.items = q.items.filter(x => ['dlg', 'later', 'kept', 'fight'].includes(x.id));
  const fight = coverItem(q, 'fight');
  same(D.blurCovers(q, fight), []); assert.equal(D.coverageNote(q, fight), ''); assert.equal(D.coverLegend(q, fight, 104), '');
  const html = Cards.card(cardCtx(q), fight);
  assert.ok(html.includes('<span class="rv-covers" data-t="104"></span>'), 'an empty layer that remembers the image time');
  assert.ok(!/class="rv-cover (applied|pending)/.test(html) && html.includes('<p class="rv-cover-note" hidden></p>'));
  assert.ok(!D.techBody(q, fight, null).includes('class="coverage"'));
  assert.ok(!Cards.card(cardCtx(q), coverItem(q, 'dlg')).includes('class="rv-cover applied'), 'KEEP and a later logo are not drawn');
  const noImage = coverQueue(), bare = coverItem(noImage, 'scene');
  assert.ok(!Cards.card(cardCtx(noImage), bare).includes('rv-covers'), 'no image, no layer');
  const only = cssRules().find(r => r.selector === '.rv-card:not(.d-cut):not(.d-blur-full):not(:has(.rv-region, .rv-aibox, .rv-cover)) .rv-frames-btn');
  assert.ok(only && /^\s*display: none;\s*$/.test(only.body), '"Ẩn khung" also where only other cards\' regions are drawn');
});
check('Session D (4): drawn like the export: the blurred image in a dashed sky-blue box, "đã làm mờ ở thẻ watermark (toàn phim)"; a suggestion: a dotted violet box, nothing blurred, "…, chưa duyệt"; "Ẩn khung", a cut or whole-frame card and the playing video hide them', () => {
  const Cards = cardsModule(), q = coverQueue(), dlg = coverItem(q, 'dlg'), owners = html => [...html.matchAll(/<span class="rv-cover (applied|pending)[^"]*" data-owner="([^"]+)"/g)].map(m => [m[2], m[1]]);
  const html = Cards.card(cardCtx(q), dlg);
  same(owners(html), [['wm', 'applied'], ['top', 'applied'], ['band', 'applied']], 'a text preview has no time: the film-long regions');
  assert.match(html, /<span class="rv-covers" data-t=""><span class="rv-cover applied[^"]*" data-owner="wm" data-region="552,643,176,31,1280,720" style="left:43\.13%;top:89\.31%;width:13\.75%;height:4\.31%;--room:[\d.]+"><canvas class="rv-cover-blur" aria-hidden="true"><\/canvas><em>đã làm mờ · watermark<\/em><\/span>/);
  // The full words where they fit on a phone card; the watermark at the bottom gets the shorter ones (D (4b)).
  assert.ok(html.includes('<canvas class="rv-cover-blur" aria-hidden="true"></canvas><em>đã làm mờ ở thẻ chữ cố định (toàn phim)</em>'), 'the band along the top');
  assert.ok(html.indexOf('rv-covers') > html.indexOf('<img') && html.indexOf('rv-covers') < html.indexOf('rv-region'), 'over the image, under the red box');
  assert.ok(html.includes('<p class="rv-cover-note">Vùng mờ viền xanh: 3 vùng thẻ khác đã làm mờ suốt đoạn này — bản xuất cũng làm mờ. Còn 2 vùng của thẻ khác chỉ trùng một phần đoạn này, không vẽ trên ảnh này (xem Chi tiết kỹ thuật).</p>'));
  const later = Cards.coversHtml(cardCtx(q), dlg, 105);
  same(owners(later), [['wm', 'applied'], ['top', 'applied'], ['band', 'applied'], ['scroll', 'pending']]);
  assert.match(later, /<span class="rv-cover pending[^"]*" data-owner="scroll" data-region="0,680,1280,40,1280,720" style="[^"]+"><em>đề xuất[^<]*, chưa duyệt<\/em><\/span>/, 'no blurred copy on a suggestion');
  assert.equal(D.coverLegend(q, dlg, 105), 'Vùng mờ viền xanh: 3 vùng thẻ khác đã làm mờ lúc 1:45.0 — bản xuất cũng làm mờ. Viền tím chấm: 1 vùng thẻ khác đề xuất làm mờ, chưa duyệt. Còn 1 vùng của thẻ khác làm mờ lúc khác trong đoạn này, không vẽ trên ảnh này (xem Chi tiết kỹ thuật).');
  // Ids and readings reach the markup escaped.
  const odd = coverQueue(); coverItem(odd, 'wm').id = 'wm"><b>';
  assert.ok(Cards.card(cardCtx(odd), coverItem(odd, 'dlg')).includes('data-owner="wm&quot;&gt;&lt;b&gt;"'));
  // "Ẩn khung" hides them with the red box; so do a cut or whole-frame card and the video while it shows.
  assert.match(Cards.card(cardCtx(q, {bare: new Set(['dlg'])}), dlg), /^<article class="scene rv-card decided bare" /);
  const rules = cssRules(), rule = sel => rules.find(r => r.selector === sel);
  for (const sel of ['.rv-card.bare .rv-covers, .rv-card.bare .rv-cover-note', '.rv-art.revealed .rv-covers',
    '.rv-card.d-cut .rv-covers, .rv-card.d-cut .rv-cover-note, .rv-card.d-blur-full .rv-covers, .rv-card.d-blur-full .rv-cover-note']) {
    assert.ok(rule(sel) && /^\s*display: none;\s*$/.test(rule(sel).body), sel);
  }
  assert.match(rule('.rv-cover').body, /outline: 2px dashed #38bdf8;.*outline-offset: 2px;.*pointer-events: none;/);
  assert.match(rule('.rv-cover.pending').body, /^\s*outline: 2px dotted #c084fc;\s*$/);
  assert.match(rule('.rv-cover-blur').body, /position: absolute; inset: 0; width: 100%; height: 100%;/);
  assert.ok(!/filter/.test(rule('.rv-cover-blur').body), 'the blur is painted into the canvas, no CSS filter (R4-B3)');
});
check('Session D (4b): on a 390 px phone (job 134: a band along the top, a fixed text under its right end, the watermark under the subtitles) no label covers another; a blurred region carries its label inside', () => {
  const Cards = cardsModule(), q = coverQueue([1280, 536]), dlg = coverItem(q, 'dlg');
  Object.assign(coverItem(q, 'wm'), {decision_region_source_pixels: {x: 552, y: 443, width: 176, height: 31}});
  Object.assign(dlg, {suggested_region_source_pixels: {x: 300, y: 380, width: 680, height: 50}});
  const labels = Cards.coverLabels(cardCtx(q), dlg, null), meets = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  same(labels.map(l => l.id), ['band', 'top', 'wm']);
  for (let i = 0; i < labels.length; i++) for (let k = i + 1; k < labels.length; k++) assert.ok(!meets(labels[i].rect, labels[k].rect), labels[i].id + ' / ' + labels[k].id);
  assert.match(labels[0].cls, / lin/); assert.equal(labels[0].text, 'đã làm mờ ở thẻ chữ cố định (toàn phim)');
  assert.equal(labels[1].text, 'đã làm mờ ở thẻ chữ cố định (toàn phim)', 'under its box: the band label is inside the band');
  assert.equal(labels[2].text, 'đã làm mờ · watermark', 'the full words would wrap below the image or cover the red box');
  assert.match(labels[2].cls, / lin/);
  assert.ok(labels.every(l => l.rect.top >= 0 && l.rect.bottom <= 1), 'inside the image height');
  // On the card of the text at the top, the labels keep off its red box and stay in their blurred boxes, in shorter
  // words, rather than over the picture (the watermark's full words took two lines over the subtitles above it).
  const onTop = Cards.coverLabels(cardCtx(q), coverItem(q, 'top'), null);
  same(onTop.map(l => [l.id, l.cls, l.text]), [['band', ' lin', 'đã làm mờ · chữ cố định'], ['wm', ' lin', 'đã làm mờ · watermark']]);
  // Too crowded for the full words: the short label, never two over each other.
  const crowd = coverQueue([1280, 536]);
  crowd.items.push(...[0, 1, 2].map(i => ({...coverItem(crowd, 'top'), id: 'top' + i, decision_region_source_pixels: {x: 700 + i * 20, y: 30 + i * 6, width: 300, height: 40}})));
  const many = Cards.coverLabels(cardCtx(crowd), coverItem(crowd, 'dlg'), null);
  const placed = many.filter(l => l.free);
  assert.ok(placed.length < many.length && many.filter(l => !l.free).every(l => l.text === '' && l.rect === null), 'no room: no label');
  for (let i = 0; i < placed.length; i++) for (let k = i + 1; k < placed.length; k++) assert.ok(!meets(placed[i].rect, placed[k].rect));
});
check('Session D (5): "Chi tiết kỹ thuật" on every card names each region other cards blur (watermark, scrolling text, fixed text or logo, logo, moments): which card, where and when; suggestions apart; whole-frame BLUR counted; escaped', () => {
  const q = coverQueue(), note = D.coverageNote(q, coverItem(q, 'dlg'));
  assert.ok(note.startsWith('<div class="coverage">') && note.endsWith('</div>'), note);
  for (const part of [
    'Vùng thẻ khác đã làm mờ (bản xuất làm mờ đúng vùng và lúc này; trên ảnh: viền xanh nét đứt): ',
    'watermark “WWW.EXAMPLE.COM &lt;b&gt;” (x=552, y=643, rộng=176, cao=31) — đã làm mờ ở thẻ riêng, toàn phim, suốt đoạn của thẻ này',
    'chữ cố định “EXAMPLE.COM” (x=837, y=44, rộng=319, cao=47) — đã làm mờ ở thẻ riêng, toàn phim, suốt đoạn của thẻ này',
    'chữ cố định (x=0, y=21, rộng=1280, cao=32) — đã làm mờ ở thẻ riêng, toàn phim',
    'logo (x=500, y=300, rộng=100, cao=50) — đã làm mờ ở thẻ riêng, 2 khoảnh khắc 01:35.0–01:55.0, trùng thẻ này 01:40.0–01:42.0; 01:48.0–01:50.0',
    'Vùng thẻ khác chỉ đề xuất làm mờ, chưa duyệt (trên ảnh: viền tím chấm): dòng chữ chạy (x=0, y=680, rộng=1280, cao=40) — đề xuất làm mờ ở thẻ riêng, 01:45.0–03:20.0, trùng thẻ này 01:45.0–01:50.0',
    '1 đoạn trùng thời gian đã được duyệt làm mờ toàn cảnh.']) assert.ok(note.includes(part), part + '\n' + note);
  assert.ok(!note.includes('không có vùng riêng'), 'the dialogue card has its own red box');
  // The same region as the card's own red box (the KEEP card of a second watermark reading).
  assert.ok(D.coverageNote(q, coverItem(q, 'kept')).includes('watermark “WWW.EXAMPLE.COM &lt;b&gt;” (x=552, y=643, rộng=176, cao=31), cùng vùng khung đỏ của thẻ này — đã làm mờ ở thẻ riêng'));
  // A safety card (no region of its own) gets the note too, as every card does.
  const fight = D.coverageNote(q, coverItem(q, 'fight'));
  assert.ok(fight.includes('Thẻ này không có vùng riêng — quyết định bên dưới áp dụng cho cả đoạn 1:40–1:50.') && fight.includes('watermark'), fight);
  for (const id of ['dlg', 'fight', 'later', 'scroll']) assert.ok(D.techBody(q, coverItem(q, id), null).includes('<div class="coverage">'), id);
  // More than six regions: the first six and how many more.
  const lots = coverQueue();
  lots.items.push(...[0, 1, 2, 3].map(i => ({...coverItem(lots, 'top'), id: 'extra' + i, labels: [], decision_region_source_pixels: {x: 10 * i, y: 200, width: 40, height: 20}})));
  assert.match(D.coverageNote(lots, coverItem(lots, 'dlg')), / và 2 vùng khác\./);
  assert.ok(fs.readFileSync(path.join(__dirname, 'review-core.js'), 'utf8').slice(0, 4400).includes('S12'), 'review-core.js lists S12');
  assert.ok(!/rv-cover|blurCovers|coverageNote/.test(script), 'D2: the classic page is unchanged');
});
check('Session D (6): the blurred copy is cut from the loaded image (a letterboxed frame too), shrunk to a few cells and stretched back into the canvas once; review.js draws it when an image loads', () => {
  same(D.coverCrop({x: 552, y: 443, width: 176, height: 31}, [1280, 536], 960, 402), {sx: 414, sy: 332.25, sw: 132, sh: 23.25, gw: 17, gh: 3});
  same(D.coverCrop({x: 0, y: 0, width: 1280, height: 72}, [1280, 720], 1280, 800), {sx: 0, sy: 40, sw: 1280, sh: 72, gw: 53, gh: 3}, 'source letterboxed in the image');
  same(D.coverCrop({x: 1200, y: 700, width: 200, height: 100}, [1280, 720], 1280, 720), {sx: 1200, sy: 700, sw: 80, sh: 20, gw: 10, gh: 3}, 'cut at the image edge');
  for (const args of [[[0, 0], 960, 540], [[1280, 720], 0, 540], [null, 960, 540]]) assert.equal(D.coverCrop({x: 1, y: 1, width: 5, height: 5}, ...args), null);
  assert.equal(D.coverCrop({x: 2000, y: 10, width: 5, height: 5}, [1280, 720], 1280, 720), null, 'outside the image');
  const {Media, calls, canvas} = fakeCanvases(255), img = {name: 'img', naturalWidth: 960, naturalHeight: 402}, cover = canvas('cover');
  Media.drawCover(cover, img, {sx: 414, sy: 332.25, sw: 132, sh: 23.25, gw: 17, gh: 3});
  same([cover.width, cover.height], [132, 23]);
  same(calls, [['create', 'canvas1'], ['canvas1', 'img', 414, 332.25, 132, 23.25, 0, 0, 17, 3], ['cover', 'canvas1', 0, 0, 17, 3, 0, 0, 132, 23]], 'cut from the image itself, no copy of the whole image');
  assert.equal(cover.dataset.drawn, '1');
  // A part of an SVG without a size of its own paints nothing in Chromium: the cells are cut again from a canvas copy
  // of the whole image (at most 1920 px a side).
  const svg = fakeCanvases(0), poster = svg.canvas('cover');
  svg.Media.drawCover(poster, {name: 'svg', naturalWidth: 3840, naturalHeight: 1608}, {sx: 1656, sy: 1329, sw: 528, sh: 93, gw: 17, gh: 3});
  same(svg.calls, [['create', 'canvas1'], ['canvas1', 'svg', 1656, 1329, 528, 93, 0, 0, 17, 3], ['create', 'canvas2'], ['canvas2', 'svg', 0, 0, 1920, 804],
    ['canvas1', 'canvas2', 828, 664.5, 264, 46.5, 0, 0, 17, 3], ['cover', 'canvas1', 0, 0, 17, 3, 0, 0, 528, 93]]);
  const review = fs.readFileSync(path.join(__dirname, 'review.js'), 'utf8');
  assert.match(review, /img\.addEventListener\('load', \(\) => \{ if \(s\) Cards\.syncCovers\(el, ctx\(\), s\.map\.get\(el\.dataset\.item\), img\); drawMag\(el\); \}\)/, 'every image a card shows repaints its covers');
  assert.ok(review.split('\n').length <= 800, 'review.js stays under the 800-line ceiling');
});
check('Session D (7): the magnifier shows the same covers (blurred with a dashed sky-blue line, a suggestion dotted violet) under the red line; none when bare, none on a cut or wholly blurred card', () => {
  const Cards = cardsModule(), view = {sx: 400, sy: 300, sw: 200, sh: 66.5, cssW: 300, cssH: 99.75, zoom: 3, scroll: false, red: {x: 30, y: 20, w: 150, h: 30}}, img = {name: 'img', naturalWidth: 960, naturalHeight: 402};
  const covers = [{state: 'applied', crop: {sx: 414, sy: 332.25, sw: 132, sh: 23.25, gw: 17, gh: 3}}, {state: 'pending', crop: {sx: 380, sy: 290, sw: 40, sh: 20, gw: 6, gh: 3}},
    {state: 'applied', crop: {sx: 700, sy: 300, sw: 50, sh: 20, gw: 7, gh: 3}}];
  const one = fakeCanvases(255), mag = one.canvas('mag');
  one.Media.drawMagnifier(mag, img, view, false, 1, covers);
  same([mag.width, mag.height, mag.style.width, mag.style.height], [300, 100, '300.0px', '99.8px']);
  same(one.calls, [['mag', 'img', 400, 300, 200, 66.5, 0, 0, 300, 100],
    ['create', 'canvas1'], ['canvas1', 'img', 414, 332.25, 132, 23.25, 0, 0, 17, 3], ['mag', 'canvas1', 0, 0, 17, 3, 21, 48.375, 198, 34.875],
    ['mag', 'save'], ['mag', 'color', '#38bdf8'], ['mag', 'width', 2], ['mag', 'dash', 6, 4], ['mag', 'stroke', 18, 45.375, 204, 40.875], ['mag', 'restore'],
    ['mag', 'save'], ['mag', 'color', '#c084fc'], ['mag', 'width', 2], ['mag', 'dash', 2, 3], ['mag', 'stroke', -33, -18, 66, 36], ['mag', 'restore'],
    ['mag', 'color', '#ff304f'], ['mag', 'width', 2], ['mag', 'stroke', 27, 17, 156, 36]], 'the third cover is outside the view');
  const sharp = fakeCanvases(255);
  sharp.Media.drawMagnifier(sharp.canvas('mag'), img, view, false, 2, covers.slice(0, 1));
  same(sharp.calls.filter(c => c[1] === 'dash' || c[1] === 'width'), [['mag', 'width', 4], ['mag', 'dash', 12, 8], ['mag', 'width', 4]], 'lines and dashes in CSS pixels');
  for (const [bare, list] of [[true, covers], [false, undefined]]) {
    const other = fakeCanvases(255);
    other.Media.drawMagnifier(other.canvas('mag'), img, view, bare, 1, list);
    same(other.calls, [['mag', 'img', 400, 300, 200, 66.5, 0, 0, 300, 100], ...(bare ? [] : [['mag', 'color', '#ff304f'], ['mag', 'width', 2], ['mag', 'stroke', 27, 17, 156, 36]])]);
  }
  // review-cards.js magCovers: the covers of the layer shown, cut in the loaded image's pixels.
  const node = (state, region) => ({classList: {contains: c => c === state}, dataset: {region}});
  const card = (cls, nodes) => ({matches: sel => sel.split(',').some(s => cls.split(' ').includes(s.trim().slice(1))), querySelectorAll: () => nodes});
  const nodes = [node('applied', '552,443,176,31,1280,536'), node('pending', '2000,10,5,5,1280,720')];
  same(Cards.magCovers(card('', nodes), img), [{state: 'applied', crop: {sx: 414, sy: 332.25, sw: 132, sh: 23.25, gw: 17, gh: 3}}], 'a cover outside the image is left out');
  for (const cls of ['d-cut', 'd-blur-full']) same(Cards.magCovers(card(cls, nodes), img), [], cls);
  same(Cards.magCovers(card('', nodes), {naturalWidth: 0}), [], 'image not loaded');
  const review = fs.readFileSync(path.join(__dirname, 'review.js'), 'utf8'), cards = fs.readFileSync(path.join(__dirname, 'review-cards.js'), 'utf8');
  assert.match(review, /Media\.drawMagnifier\(canvas, img, view, el\.classList\.contains\('bare'\), window\.devicePixelRatio, Cards\.magCovers\(el, img\)\);/);
  assert.match(cards, /if \(\(covers \|\| whole !== el\.matches\('\.d-cut, \.d-blur-full'\)\) && el\.__magKey !== undefined\) el\.__magKey = null;/, 'patch draws a magnifier again when its covers change');
});
check('R4-B3: no backdrop-filter in V2 (dialog backdrops, the top bar and the "Làm mờ" region made Chrome blink on the user\'s PC); no size container on card images', () => {
  const files = fs.readdirSync(__dirname).filter(f => /\.(css|js|html)$/.test(f));
  const found = files.flatMap(f => (fs.readFileSync(path.join(__dirname, f), 'utf8').match(/backdrop-?filter\s*[:=]\s*['"]?(?!none\b)[^;'"}\s]+/gi) || []).map(m => f + ': ' + m));
  assert.deepEqual(found, [], 'backdrop-filter found');
  const css = fs.readFileSync(path.join(__dirname, 'review.css'), 'utf8');
  assert.ok(!/container-type\s*:\s*(inline-size|size)/.test(css) && !/\d+cqw/.test(css.replace(/\/\*[\s\S]*?\*\//g, '')), 'no container query in review.css');
  assert.match(css, /\.rv-region\.blurred \{ background: #ffffffb3; \}/, 'the "Làm mờ" region is a white veil');
});
check('R4-B4: while a dialog is open the page behind does not scroll (in a narrow window its scrollbar lay over the review dialog\'s)', () => {
  const theme = fs.readFileSync(path.join(__dirname, 'theme.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.match(theme, /html:has\(dialog\[open\]\)\s*\{\s*overflow:\s*hidden;\s*\}/);
  for (const page of ['live.html', 'index.html']) assert.ok(fs.readFileSync(path.join(__dirname, page), 'utf8').includes('theme.css'), page + ' loads theme.css');
});

/* R3: bulk and export against the classic bulkKeep / bulkAccept (runBlocking, postJson recorded) and EXPORT_DIALOG_JS. */
const BULK_FUNCS = ['isSafety', 'isLogoItem', 'isAdItem', 'visible', 'bulkFilters', 'decisionsLocked', 'refuseWhileExporting', 'bulkKeep', 'bulkAccept', 'formatStamp'];
const EXPORT_LINES = lines.slice(0, 12).filter(l => /EXPORT_GATE_MESSAGE=|^function export(SizeSelection|ConfirmText|PolicyChoice|SizeOptionsHtml)\(/.test(l));
const bulkSource = ['EXPORT_LOCK_MESSAGE', 'SOURCE_CLEANED_LOCK_MESSAGE', 'SOURCE_ARCHIVED_LOCK_MESSAGE', 'size', 'SAFETY'].map(n => line(new RegExp('^const ' + n + '='))).concat(EXPORT_LINES, BULK_FUNCS.map(n => line(new RegExp('^(async )?function ' + n + '\\(')))).join('\n');
const bulkBox = {};
vm.createContext(bulkBox);
vm.runInContext(`let queue=null,filter='pending',exportJob={status:'IDLE'};const sticky=new Set();let asked=[],alerts=[],posted=[];
function confirm(m){asked.push(m);return true;}function alert(m){alerts.push(m);}
async function postJson(kind,body){posted.push({kind,body:JSON.parse(JSON.stringify(body))});return queue;}
async function runBlocking(task){await task();} async function applyQueueUpdate(){} function scheduleResources(){}
${bulkSource}
globalThis.classic={async run(q,f,kind){queue=q;filter=f;asked=[];alerts=[];posted=[];await (kind==='bulkAccept'?bulkAccept():bulkKeep());return {asked:asked.slice(),alerts:alerts.slice(),posted:posted.slice()};},
  fn:{exportSizeSelection,exportConfirmText,exportPolicyChoice,exportSizeOptionsHtml,formatStamp,size},consts:{EXPORT_GATE_MESSAGE,EXPORT_SIZE_OPTIONS}};`, bulkBox);
const bk = bulkBox.classic;
check('R3.1 bulk: the classic filter map, words and POSTs ({filter}; Quảng cáo = visual_logo then text; Visual AI / Ứng viên phụ unsupported); S1 counts', async () => {
  const KIND = {bulkKeep: 'bulk-keep', bulkAccept: 'bulk-accept'};
  let same1 = 0, s1 = 0, unsupported = 0, sends = 0;
  for (const [name, q] of Object.entries(QUEUES)) for (const f of R.FILTER_IDS) for (const kind of ['bulkKeep', 'bulkAccept']) {
    const a = await bk.run(structuredClone(q), f, kind), plan = R.bulkPlan(q, f, kind);
    const b = plan.error ? {asked: [], alerts: [plan.error], posted: []} : {asked: [plan.confirm], alerts: [], posted: plan.filters.map(filter => ({kind: KIND[kind], body: {filter}}))};
    const label = `${name} ${f} ${kind}`;
    if (plan.error === R.TEXT.bulkUnsupported) { unsupported++; same(b, a, label); continue; }
    // The count the classic page shows (what the list displays) and the one V2 shows (what the server will change).
    const classicCount = a.asked.length ? Number(/(\d+)/.exec(a.asked[0])[1]) : 0, v2Count = plan.error ? 0 : plan.count;
    if (classicCount === v2Count) { same(b, a, label); same1++; }
    else { // S1: same words and filters, the count follows the server
      s1++;
      assert.equal(v2Count, R.bulkCount(q, f, kind === 'bulkAccept' ? 'accept' : 'keep'), label);
      if (a.asked.length && b.asked.length) { assert.equal(b.asked[0], a.asked[0].replace(String(classicCount), String(v2Count)), label + ' words'); same(b.posted, a.posted, label + ' filters'); }
    }
    sends += b.posted.length;
  }
  const ads = R.bulkPlan(QUEUES.mixed30, 'ads', 'bulkKeep');
  same(ads.filters, ['visual_logo', 'text'], 'Quảng cáo: 2 commands, visual_logo then text');
  assert.ok(same1 > 60 && unsupported >= 14 && sends > 60, `identical ${same1}, S1 ${s1}, unsupported ${unsupported}, POSTs ${sends}`);
  process.stdout.write(`   bulk: ${same1} cases identical to the classic page, ${s1} with the S1 count, ${unsupported} unsupported\n`);
});
check('R3.3 export dialog: options, limits, gate and confirm sentence equal EXPORT_DIALOG_JS (export_dialog.py); the body stays {size_mode, max_output_gb?}', () => {
  const C = require('./contracts.js');
  same(C.EXPORT_SIZE_OPTIONS, bk.consts.EXPORT_SIZE_OPTIONS); assert.equal(C.EXPORT_GATE_MESSAGE, bk.consts.EXPORT_GATE_MESSAGE);
  let n = 0;
  for (const [mode, gb] of [['default', ''], ['unlimited', 'x'], ['custom', '2.5'], ['custom', 12.75], ['custom', '0.05'], ['custom', '1000'], ['custom', '0.04'], ['custom', '1000.1'], ['custom', ''], ['custom', 'abc'], ['custom', '3,5'], ['nope', '2']]) {
    let classicSel = null, classicErr = null, v2 = null, v2Err = null;
    try { classicSel = bk.fn.exportSizeSelection(mode, gb); } catch (e) { classicErr = e.message; }
    try { v2 = C.exportSelection(mode, gb); } catch (e) { v2Err = e.message; }
    if (mode === 'nope') { assert.ok(v2Err && classicSel && classicSel.size_mode === 'custom', 'an unknown mode: V2 refuses, the classic page reads it as custom'); n++; continue; }
    assert.equal(v2Err, classicErr, `${mode} ${gb} error`);
    if (!classicSel) { n++; continue; }
    const {description, ...body} = classicSel;
    same(v2, body, `${mode} ${gb}: the body without the classic "description"`);
    assert.equal(C.exportConfirmText(v2), bk.fn.exportConfirmText(classicSel), `${mode} ${gb} sentence`);
    n++;
  }
  for (const policy of [null, {}, {mode: 'custom', maximum_output_gb: 7.5}, {mode: 'custom', maximum_output_gb: 0}, {mode: 'unlimited', maximum_output_gb: 9}, {mode: 'default'}, {mode: 'bogus'}]) same(C.exportPolicyChoice(policy), bk.fn.exportPolicyChoice(policy), JSON.stringify(policy));
  assert.ok(script.includes('min="0.05" max="1000" step="0.1"') || html.includes('min="0.05" max="1000" step="0.1"'), 'classic custom field attributes');
  assert.equal(C.EXPORT_CUSTOM_GB.attributes, 'type="number" min="0.05" max="1000" step="0.1"');
  assert.ok(n === 12);
});
check('R3.2 / R3.4: resources line (S4 "Ổ đĩa còn trống"), export state line and S2 / S3 are explicit', () => {
  const C = require('./contracts.js');
  const r = {source_bytes: 245000000, report_bytes: 18000000, disk_free_bytes: 312000000000, estimated_preview_seconds: 40, estimated_preview_megabytes_range: [180, 260]};
  same(C.resourceItems(r).map(i => i[1]), [bk.fn.size(r.source_bytes), bk.fn.size(r.report_bytes), bk.fn.size(r.disk_free_bytes), '40 giây · khoảng 180–260 MB']);
  for (const [label] of C.resourceItems(r)) assert.ok(script.includes(`<div class="resource">${label === 'Ổ đĩa còn trống' ? 'Ổ E còn trống' : label}<strong>`), 'classic resource ' + label);
  assert.equal(C.resourceItems(r)[2][0], 'Ổ đĩa còn trống', 'S4');
  for (const stamp of ['2026-10-03T09:20:00Z', '', null, 'bad']) assert.equal(C.formatStamp(stamp), bk.fn.formatStamp(stamp));
  const texts = {QUEUED: 'Đã xếp hàng xuất video.', RENDERING: 'Đang render và kiểm tra video…', SKIPPED: 'Video đã được đánh dấu bỏ qua (không xuất). Bấm “Mở lại để xuất” ở Dashboard nếu muốn xuất video.'};
  for (const [status, text] of Object.entries(texts)) { assert.equal(R.exportLine({status}), text); assert.ok(script.includes(text), status); }
  assert.equal(R.exportLine({status: 'COMPLETED', output: 'out/a.mp4'}), 'Hoàn tất: out/a.mp4'); assert.ok(script.includes('COMPLETED:`Hoàn tất: ${exportJob.output||\'\'}`'));
  assert.equal(R.exportLine({status: 'FAILED', error: 'x'}), 'Xuất thất bại: x'); assert.ok(script.includes('FAILED:`Xuất thất bại: ${exportJob.error||\'không rõ lỗi\'}`'));
  assert.match(R.exportLine({status: 'COMPLETED', output: 'o.mp4', source_cleaned: true, source_name: 'v.mp4', source_cleanup: {finished_at: '2026-10-03T09:20:00Z'}}), /^Hoàn tất: o\.mp4\. Video gốc đã được dọn vào Thùng rác lúc .+\. Chép lại đúng tên “v\.mp4” vào input để xuất lại hoặc sửa quyết định\.$/);
  assert.match(R.exportLine({status: 'SKIPPED', source_archived: true}), /^Video gốc đang ở kho lưu trữ\. Bấm “Khôi phục bản xuất” trên Dashboard để xuất lại hoặc sửa quyết định\.$/);
  for (const status of ['IDLE', 'WAITING_REVIEW', 'READY_TO_EXPORT']) assert.equal(R.exportLine({status}), '', status + ': "N / M" and S2 already say it');
  // S2 (only "Cần xem thêm" left) and S3 (export state reloads after a decision) are deliberate differences.
  const q = Mock.reviewQueue(job101, {count: 4}); q.items.forEach(x => { x.decision = 'KEEP'; }); q.items[1].decision = 'NEEDS_MORE_CONTEXT';
  assert.equal(R.nextNote(q), 'Còn 1 mục Cần xem thêm — chọn quyết định cuối trước khi xuất.');
  const header = fs.readFileSync(path.join(__dirname, 'review-core.js'), 'utf8').slice(0, 3200);
  for (const tag of ['S2', 'S3', 'S4']) assert.ok(header.includes(tag), 'review-core.js lists ' + tag);
  assert.ok(fs.readFileSync(path.join(__dirname, 'review.js'), 'utf8').includes('}, 1500);'), 'S3: one reload 1.5 s after the last write');
});

/* R1-B1: the image loader on fake boxes (no DOM): each started load gives its slot back exactly once. */
function loaderHarness() {
  const ctx = {window: {BFReviewCore: R}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, 'review-media.js'), 'utf8'), ctx);
  const requests = [];
  const fakeImg = () => {
    const listeners = {load: new Set(), error: new Set()}, img = {requests: 0};
    img.addEventListener = (type, fn) => listeners[type].add(fn);
    img.removeEventListener = (type, fn) => listeners[type].delete(fn);
    img.listeners = () => listeners.load.size + listeners.error.size;
    img.fire = ok => { const handler = ok ? img.onload : img.onerror; for (const fn of [...listeners[ok ? 'load' : 'error']]) fn(); if (handler) handler(); };
    Object.defineProperty(img, 'src', {set(url) { img.current = url; img.requests++; requests.push({img, url}); }, get() { return img.current; }});
    return img;
  };
  const fakeBox = () => {
    const img = fakeImg(), classes = new Set();
    return {img, isConnected: true, classList: {add: (...c) => c.forEach(x => classes.add(x)), remove: (...c) => c.forEach(x => classes.delete(x)), has: c => classes.has(c)}, querySelector: () => img};
  };
  const stats = {};
  return {loader: ctx.window.BFReviewMedia.createImageLoader({max: 2, stats}), fakeBox, requests, stats};
}
check('R1-B1 image loader: a box watched again while loading gives its slot back; still at most 2 images at once', () => {
  // The reported case: L.watch(a,u,10); L.watch(a,u,10) → after the load, active() is 0 and the image loads once.
  let h = loaderHarness(), a = h.fakeBox();
  h.loader.watch(a, '/f/1', 10); h.loader.watch(a, '/f/1', 10);
  assert.equal(h.loader.active(), 1); assert.equal(a.img.requests, 1, 'same URL: no second load');
  a.img.fire(true);
  assert.equal(h.loader.active(), 0); assert.ok(a.classList.has('loaded')); assert.equal(a.img.listeners(), 0);
  // Another URL while loading: the first load is dropped by the browser without an event; its slot comes back once.
  h = loaderHarness(); a = h.fakeBox();
  h.loader.watch(a, '/f/1', 10); h.loader.watch(a, '/f/2', 10);
  assert.equal(h.loader.active(), 1); assert.equal(a.img.current, '/f/2');
  a.img.fire(true); a.img.fire(true);
  assert.equal(h.loader.active(), 0); assert.equal(h.stats.imagesLoaded, 1);
  // A cancelled entry (box gone, loader reset) still gives its slot back when its image settles.
  h = loaderHarness(); a = h.fakeBox(); const b = h.fakeBox();
  h.loader.watch(a, '/f/1', 10); h.loader.watch(b, '/f/2', 10);
  a.isConnected = false; h.loader.forget(); h.loader.reset();
  assert.equal(h.loader.active(), 2);
  a.img.fire(true); b.img.fire(false);
  assert.equal(h.loader.active(), 0); assert.equal(h.stats.imagesLoaded, 0, 'cancelled loads are not counted');
  // Many boxes, some watched twice or three times, errors with a key refresh: never more than 2, back to 0.
  h = loaderHarness();
  const boxes = Array.from({length: 9}, () => h.fakeBox());
  let refreshes = 0;
  boxes.forEach((box, i) => { h.loader.watch(box, '/f/' + i, 10, url => { refreshes++; return url + '?k=new'; }); if (i % 2) h.loader.watch(box, '/f/' + i, 10); if (i % 3 === 0) h.loader.watch(box, '/f/' + i + 'b', 10); });
  let max = 0, rounds = 0;
  const pending = () => boxes.filter(box => box.img.listeners());
  return (async () => {
    while (pending().length && rounds++ < 100) {
      max = Math.max(max, h.loader.active());
      assert.ok(h.loader.active() <= 2 && pending().length <= 2, 'at most 2 loads at once');
      const [box] = pending();
      box.img.fire(!(box.img.current === '/f/4' || box.img.current === '/f/7')); // these two fail once, then load with a new key
      await Promise.resolve(); await Promise.resolve();
    }
    assert.equal(h.loader.active(), 0); assert.equal(max, 2); assert.equal(refreshes, 2);
    assert.ok(boxes.every(box => box.classList.has('loaded')), 'every box loaded');
    assert.equal(h.stats.maxImages, 2);
  })();
});

Promise.all(asyncChecks).then(() => process.stdout.write(JSON.stringify({passed, failed: 0}) + '\n'), error => { process.stderr.write((error && error.stack || String(error)) + '\n'); process.exit(1); });
