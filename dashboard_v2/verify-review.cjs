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
