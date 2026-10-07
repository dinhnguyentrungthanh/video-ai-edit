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
check('R1 "Chi tiết kỹ thuật" and evidence texts equal the classic markup', () => {
  for (const q of [QUEUES.mixed30, QUEUES.edge, QUEUES.demo101]) {
    old.load(q);
    q.items.concat(q.advisory_items).forEach((x, i) => {
      const ev = evidenceCases(x, i)[i % 5];
      assert.equal('<details class="tech"><summary>Chi tiết kỹ thuật</summary><div class="tech-body">' + D.techBody(q, x, ev) + '</div></details>', old.fn.techDetails(x, ev, false), x.id);
      same(D.decisionScope(q, x), old.fn.decisionScope(x)); assert.equal(D.overlapCoverage(q, x), old.fn.overlapCoverage(x));
      assert.equal(D.labelsReasonsHtml(x), old.fn.labelsReasonsHtml(x)); assert.equal(D.visualAiHtml(x), old.fn.visualAiHtml(x));
      assert.equal(D.evidenceHtml(ev), old.fn.evidenceHtml(ev)); assert.equal(D.aiModelLine(x), old.fn.aiModelLine(x));
    });
  }
  const memory = {category: 'visual_logo', max_score: 0.97, model_evidence: {vlm_source: 'approved_brand_memory', vlm_answer: 'MEMORY_MATCH | Kênh <b>A</b>', vlm_confirmation: 'MATCHED'}};
  assert.equal(D.aiModelLine(memory), old.fn.aiModelLine(memory));
  assert.match(D.aiModelLine(memory), /&lt;b&gt;A&lt;\/b&gt;/, 'queue strings are escaped');
});
check('R1 zoomed evidence: yellow AI boxes, red approved boxes and the legend match evidenceMediaHtml; 360 px crop', () => {
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
  const crop = D.cropRect({x: 60, y: 40, width: 260, height: 96}, [1920, 1080], 640, 360);
  assert.equal(crop.width, 360); assert.ok(crop.height >= 100);
  assert.ok(crop.red.x > 0 && crop.red.y > 0 && crop.red.x + crop.red.w < 360, JSON.stringify(crop));
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
