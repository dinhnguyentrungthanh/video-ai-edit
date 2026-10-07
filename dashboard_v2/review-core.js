/* Review logic of the V2 "Duyệt cảnh" dialog: pure functions, no DOM, no fetch.
 * It mirrors the classic review page (_interactive_html in review_workflow.py); verify-review.cjs
 * extracts the classic functions and compares both on sample queues. Deliberate differences:
 *   S1 bulk counts follow the server (category, main undecided items; accept skips BLUR without region);
 *   S2 only NEEDS_MORE_CONTEXT left → "Còn N mục Cần xem thêm …" instead of "Đã duyệt đủ";
 *   S6 "export active" is contracts.inFlight (render_request, RENDERING/VERIFYING, QUEUED export),
 *      not every QUEUED job;
 *   S7 the queue identity reads source.sha256 (the classic page reads source.input_sha256, always empty);
 *   S8 a SKIPPED video opens read-only;
 *   R2-K the BLUR button (and key 2) of an item with a red region blurs that region (P7, 6.3); the classic
 *      main button and key 2 always blur the whole frame (the classic page blurs a region with its region buttons);
 *   S9 a card that only borrows another logo card's red box has no region buttons: a line about the owner card
 *      and "Đi tới thẻ logo" (the classic page shows the same buttons on both cards; the bodies are unchanged);
 *   S3 the export state reloads 1.5 s after a decision too (the classic page: only while QUEUED/RENDERING);
 *   S4 the resources line says "Ổ đĩa còn trống" (the classic page: "Ổ E còn trống", contracts.resourceItems);
 *   S10 a gore card's hint line adds what the tagger saw (gore_hint, docs/ANIME_GORE_PLAN.md step 1); the classic
 *      page stays byte-identical (D2) and has no such line.
 * R3 bulk and export follow the classic bulkKeep(), bulkAccept(), runBlocking() and finalizeExport(): same filter map,
 * same confirm words (count as the server selects, S1), the export gate and the export_dialog.py sentence.
 * R2 decisions follow the classic decide(), undo() and writeFailureMessage(): same confirms (S5 shows them in
 * a V2 dialog), same errors and, field for field, the same POST bodies.
 */
(function (root, factory) {
  const contracts = typeof module === 'object' && module.exports ? require('./contracts.js') : root.BFContracts;
  const api = factory(contracts);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BFReviewCore = api;
})(typeof window === 'undefined' ? this : window, function (C) {
  'use strict';
  const SAFETY = {adult:'18+',gore:'Máu me',violence:'Bạo lực'};
  const KIND_NAMES = {logo_overlay:'Logo',logo_candidate:'Ứng viên logo',opening_promotion:'Quảng cáo mở đầu',text_candidate:'Ứng viên chữ',title_overlay:'Tiêu đề phim',in_film_text:'Chữ trong phim',platform_logo:'Logo nền tảng'};
  const SCENE_WORDS = {violence:'Trận đánh',gore:'Cảnh máu',adult:'Nhóm 18+'};
  const STATUS = {KEEP:['Giữ','st-keep'],BLUR:['Làm mờ','st-blur'],CUT:['Cắt','st-cut'],NEEDS_MORE_CONTEXT:['Cần xem','st-more']};
  const SCOPE_NAMES = {advertising:'Quảng cáo / logo',adult:'18+',gore:'Máu me',violence:'Bạo lực'};
  /* Chips of the classic page (#chips) and its "Lọc khác…" select (#more-filter), same order and labels. */
  const FILTERS = [['pending','Chưa duyệt'],['adult','18+'],['gore','Máu me'],['violence','Bạo lực'],['ads','Quảng cáo'],['all','Tất cả']];
  const MORE_FILTERS = [['high','Ưu tiên cao'],['visual_ai','Visual AI'],['visual_logo','Logo'],['text','Chữ'],['candidates','Ứng viên phụ']];
  const FILTER_IDS = FILTERS.concat(MORE_FILTERS).map(f => f[0]);
  const FINAL = ['KEEP','BLUR','CUT'];
  const DECISIONS = ['KEEP','BLUR','CUT','NEEDS_MORE_CONTEXT'];
  /* Texts that carry rules (A5), word for word from the classic page. */
  const TEXT = {
    exportLock:'Video đang chờ xuất hoặc đang xuất; hủy lệnh xuất trước khi đổi quyết định.',
    cleanedLock:'Video gốc đã được dọn vào Thùng rác; trang duyệt chỉ để xem. Chép lại video gốc vào input để sửa quyết định hoặc xuất lại.',
    archivedLock:'Video gốc đang ở kho lưu trữ; trang duyệt chỉ để xem. Bấm “Khôi phục bản xuất” trên Dashboard để sửa quyết định hoặc xuất lại.',
    skippedLock:'Video đã được đánh dấu bỏ qua (không xuất). Bấm “Mở lại để xuất” ở Dashboard để sửa.',
    bulkUnsupported:'Bộ lọc này không hỗ trợ thao tác hàng loạt.',
    advisory:'Ứng viên kiểm tra thêm — chưa thuộc quyết định chính',
    rescanning:'Video đang được quét lại. Đóng hộp và mở lại khi quét xong.',
    missingItem:'Không tìm thấy mục này trong hàng đợi hiện tại.',
    needsRegion:'Mục này chưa có vùng được định vị; hãy chọn Làm mờ cả cảnh.',
    fullFrame:'Bạn có xác nhận làm mờ toàn bộ khung hình trong đoạn này?',
    undoMissing:'Mục cần hoàn tác không còn trong hàng đợi hiện tại.',
    studio:'Đây là logo hãng phim — giữ & nhớ',studioDone:'✓ Đã nhớ là logo hãng phim (giữ nguyên)',
    platform:'Đây là logo nền tảng — làm mờ & nhớ',platformDone:'✓ Đã nhớ là logo nền tảng (làm mờ)',
    regionKeep:'Đây là tiêu đề/nội dung phim — giữ lại',regionBlur:'Đây là logo thương hiệu — làm mờ',
    bulkKeepNone:'Không có mục chưa duyệt trong bộ lọc này.',bulkAcceptNone:'Không có đề xuất chưa duyệt trong bộ lọc này.',
    offline:'Mất kết nối với Control Center. Các nút quyết định tạm khóa đến khi kết nối lại; lựa chọn đã lưu vẫn được giữ.'
  };
  /* Notes of the region buttons ("Đây là tiêu đề/nội dung phim — giữ lại" / "Đây là logo thương hiệu — làm mờ"). */
  const REGION_NOTES = {KEEP:'Đã xác nhận vùng khoanh đỏ là tiêu đề hoặc nội dung hợp lệ của phim',BLUR:'Đã xác nhận vùng khoanh đỏ là logo thương hiệu'};
  const WRITE_RETRY_MS = [300, 900];

  function isSafety(x) { return !!x && Object.prototype.hasOwnProperty.call(SAFETY, x.category); }
  function momentsOf(x) {
    if (!x || x.temporal_policy !== 'discrete_detected_intervals') return [];
    return (x.detected_intervals || []).map(d => ({start:Number(d.start_seconds),end:Number(d.end_seconds)}))
      .filter(m => Number.isFinite(m.start) && Number.isFinite(m.end) && m.end >= m.start).sort((a, b) => a.start - b.start);
  }
  function isScene(x) { return isSafety(x) && momentsOf(x).length > 1; }
  function studioEligible(x) { return !!x && x.category === 'visual_logo' && !x.suggested_region_source_pixels && x.candidate_type !== 'persistent_overlay'; }
  function platformEligible(x) { return !!x && x.category === 'visual_logo' && (x.candidate_type === 'platform_logo' || studioEligible(x)); }
  function sceneLogo(x) {
    return !!x && x.category === 'visual_logo' && !x.suggested_region_source_pixels
      && (x.candidate_type == null || ['opening_boundary','ending_boundary','opening_promotion','branded_end_card'].includes(x.candidate_type));
  }
  function hasPlayer(x) { return isSafety(x) || sceneLogo(x); }
  function isLogoItem(x) { return x.category === 'visual_logo' || x.review_kind === 'logo_overlay' || x.review_kind === 'logo_candidate'; }
  function isAdItem(x) { return x.category === 'visual_logo' || x.category === 'text' || isLogoItem(x); }
  /* BLUR on an item without a located region can only blur the whole frame (server: full_frame or a region). */
  function needsFullFrame(x) { return !!x && !x.suggested_region_source_pixels; }
  function catName(x) {
    if (isSafety(x)) return SAFETY[x.category];
    if (x.review_kind === 'opening_promotion' && x.opening_ident) return 'Logo mở đầu';
    if (x.category === 'visual_logo' && x.candidate_type === 'opening_boundary') return 'Kiểm tra đoạn mở đầu';
    if (x.category === 'visual_logo' && x.candidate_type === 'ending_boundary') return 'Kiểm tra đoạn kết';
    if (x.category === 'visual_logo' && x.candidate_type === 'platform_logo') return `Logo nền tảng ${x.platform_logo?.name || ''}`.trim();
    if (x.category === 'visual_logo' && x.candidate_type == null && !x.suggested_region_source_pixels) return 'Logo toàn khung (chưa khoanh vùng)';
    return KIND_NAMES[x.review_kind] || (x.category === 'text' ? 'Chữ' : x.category === 'visual_logo' ? 'Logo / quảng cáo' : String(x.category || 'Khác'));
  }
  function actionName(x, decision) {
    if (decision === 'KEEP') return 'Giữ nguyên';
    if (decision === 'CUT') return 'Cắt cả cảnh';
    if (decision === 'BLUR') return x.suggested_region_source_pixels ? (isLogoItem(x) ? 'Làm mờ logo' : 'Làm mờ vùng chữ/logo') : 'Làm mờ toàn cảnh';
    if (decision === 'NEEDS_MORE_CONTEXT') return 'Cần xem thêm';
    return decision;
  }
  function sceneName(x) { return isScene(x) ? `${SCENE_WORDS[x.category] || catName(x)} (${momentsOf(x).length})` : catName(x); }
  function statusOf(x) { return STATUS[x.decision] || ['Chưa duyệt','st-pending']; }
  function mmss(s) { const v = Math.max(0, Math.floor(Number(s) || 0)); return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, '0')}`; }
  function span(x) { return `${mmss(x.start_seconds)}–${mmss(x.end_seconds)}`; }

  /* Lists: the classic visible(), byTime() and filteredItems(), with filter and sticky passed in. */
  function visible(x, filter, sticky) {
    if (filter === 'pending') return !x.decision || !!(sticky && sticky.has(x.id));
    if (filter === 'high') return x.priority === 'high';
    if (filter === 'all') return true;
    if (filter === 'visual_ai') return !!x.ai_visual_audit;
    if (filter === 'visual_logo') return isLogoItem(x);
    if (filter === 'ads') return isAdItem(x);
    if (filter === 'text') return x.category === 'text' && x.review_kind !== 'logo_overlay';
    return x.category === filter;
  }
  function byTime(a, b) { return (Number(a.start_seconds) - Number(b.start_seconds)) || (Number(a.end_seconds) - Number(b.end_seconds)) || String(a.id).localeCompare(String(b.id)); }
  function listItems(queue, filter, sticky) {
    if (!queue) return [];
    const data = filter === 'candidates' ? (queue.advisory_items || []).slice() : (queue.items || []).filter(x => visible(x, filter, sticky));
    return data.sort(byTime);
  }
  function itemMap(queue) {
    const map = new Map();
    for (const x of queue?.items || []) map.set(x.id, x);
    for (const x of queue?.advisory_items || []) if (!map.has(x.id)) map.set(x.id, x);
    return map;
  }
  function countsFrom(items) {
    const decisions = {KEEP:0,BLUR:0,CUT:0,NEEDS_MORE_CONTEXT:0}; let pending = 0;
    for (const x of items) { if (Object.prototype.hasOwnProperty.call(decisions, x.decision)) decisions[x.decision]++; else pending++; }
    return {total:items.length,pending,decisions};
  }
  function statusFrom(items) {
    if (items.some(x => x.decision === 'NEEDS_MORE_CONTEXT')) return 'NEEDS_MORE_CONTEXT';
    if (items.some(x => !x.decision)) return 'REVIEW_REQUIRED';
    return 'READY_FOR_EDIT_PLAN';
  }
  /* Header of the V2 dialog: "N / M cảnh cần quyết định cuối" (N = main items without KEEP/BLUR/CUT). */
  function progress(queue) {
    const items = queue?.items || [], c = countsFrom(items), remaining = items.filter(x => !FINAL.includes(x.decision)).length;
    return {total:c.total,pending:c.pending,more:c.decisions.NEEDS_MORE_CONTEXT,remaining,done:c.total - remaining,advisory:(queue?.advisory_items || []).length};
  }
  function progressText(queue) { const p = progress(queue); return `${p.remaining} / ${p.total} cảnh cần quyết định cuối`; }
  /* S2: what is left before export, in the classic wording where the classic page is right. */
  function nextNote(queue) {
    const p = progress(queue);
    if (p.pending) return `Còn ${p.pending} mục chưa duyệt.`;
    if (p.more) return `Còn ${p.more} mục Cần xem thêm — chọn quyết định cuối trước khi xuất.`;
    return 'Đã duyệt đủ mọi mục chính.';
  }
  function initialFilter(queue) { return !countsFrom(queue?.items || []).pending && (queue?.items || []).length ? 'all' : 'pending'; }

  /* Focus: the classic pickFocus(), nextUndecided() (cyclic, skips decided) and step() (clamped). */
  function pickFocus(list) { for (const x of list) if (x && !x.decision) return x.id; return list[0] ? list[0].id : null; }
  function nextUndecided(list, fromId, map) {
    const n = list.length; if (!n) return null;
    let start = list.findIndex(x => x.id === fromId);
    if (start < 0) { const current = map && map.get(fromId); start = -1; if (current) for (let i = 0; i < n; i++) if (byTime(list[i], current) < 0) start = i; }
    for (let k = 1; k <= n; k++) { const x = list[(start + k + n) % n]; if (x.id !== fromId && !x.decision) return x.id; }
    return null;
  }
  function step(list, focusId, delta) {
    if (!list.length) return null;
    const i = list.findIndex(x => x.id === focusId), j = i < 0 ? (delta > 0 ? 0 : list.length - 1) : Math.min(list.length - 1, Math.max(0, i + delta));
    return list[j].id;
  }

  /* Sync: identity changes with a new scan revision (S7: source.sha256); version with any edit. */
  function queueIdentity(q) {
    if (!q) return '';
    const reports = (q.reports || []).map(x => typeof x === 'string' ? x : (x.path || x.report || JSON.stringify(x))).join('|');
    return [q.created_at || '', q.source?.sha256 || '', reports].join('::');
  }
  function queueVersion(q) { if (!q) return ''; const c = q.counts || {}; return [queueIdentity(q), q.updated_at || '', q.status || '', c.total || 0, c.pending || 0].join('::'); }

  /* Bulk: the classic filter map; S1 counts as the server selects (bulk_keep / bulk_accept in review_workflow.py). */
  function bulkFilters(filter) { return {pending:['pending'],all:['all'],high:['high'],adult:['adult'],gore:['gore'],violence:['violence'],text:['text'],visual_logo:['visual_logo'],ads:['visual_logo','text']}[filter] || null; }
  function serverSelects(x, f) { return f === 'pending' || f === 'all' ? true : f === 'high' ? x.priority === 'high' : x.category === f; }
  function bulkCount(queue, filter, kind) {
    const filters = bulkFilters(filter); if (!filters) return null;
    const ids = new Set();
    for (const f of filters) for (const x of queue?.items || []) {
      if (x.decision != null || !serverSelects(x, f)) continue;
      if (kind === 'accept' && (!DECISIONS.includes(x.suggested_decision) || (x.suggested_decision === 'BLUR' && x.suggested_region_source_pixels == null))) continue;
      ids.add(x.id);
    }
    return ids.size;
  }

  /* Locks of the dialog (client mirror of ensure_review_editable and the finalize guards).
   * job: the V2 /api/status job; exp: GET review/export (may be null). */
  function lockState(job, exp) {
    const e = exp || {}, j = job || {};
    const active = C.inFlight(j), cleaned = !!e.source_cleaned || C.cleaned(j), archived = !!e.source_archived || C.archived(j);
    const skipped = j.state === 'SKIPPED' || e.status === 'SKIPPED';
    const reason = active ? TEXT.exportLock : cleaned ? TEXT.cleanedLock : archived ? TEXT.archivedLock : skipped ? TEXT.skippedLock : '';
    return {readonly:!!reason,reason,active,cleaned,archived,skipped};
  }
  function canExport(queue, lock) { return !!queue && queue.status === 'READY_FOR_EDIT_PLAN' && !lock.readonly; }

  /* Red region of a card: the item's own region, or the BLUR logo card that overlaps it (classic regionOwner). */
  function regionOwner(queue, x) {
    if (x.suggested_region_source_pixels && Array.isArray(x.source_frame_size)) return x;
    if (x.advisory) return null;
    return (queue?.items || []).find(o => o.id !== x.id && isLogoItem(o) && o.decision === 'BLUR' && (o.suggested_region_source_pixels || o.decision_region_source_pixels)
      && Array.isArray(o.source_frame_size) && o.start_seconds < x.end_seconds && o.end_seconds > x.start_seconds) || null;
  }
  /* The region as fractions of the source frame (for a CSS box over the image), or null. */
  function regionBox(queue, x) {
    const owner = regionOwner(queue, x), r = owner && (owner.suggested_region_source_pixels || owner.decision_region_source_pixels);
    if (!r || r === 'FULL_FRAME' || !Array.isArray(owner.source_frame_size)) return null;
    const sw = Number(owner.source_frame_size[0]), sh = Number(owner.source_frame_size[1]);
    const box = [Number(r.x) / sw, Number(r.y) / sh, Number(r.width) / sw, Number(r.height) / sh];
    if (!(sw > 0 && sh > 0) || box.some(v => !Number.isFinite(v))) return null;
    const clamp = v => Math.min(1, Math.max(0, v));
    return {left:clamp(box[0]),top:clamp(box[1]),width:clamp(box[2]),height:clamp(box[3]),owner:owner.id};
  }
  function frameAspect(x) { const s = x && x.source_frame_size; return Array.isArray(s) && Number(s[0]) > 0 && Number(s[1]) > 0 ? `${Number(s[0])} / ${Number(s[1])}` : '16 / 9'; }
  function scopeWarning(queue) {
    const skipped = ((queue?.detection_scope || {}).skipped || []).map(x => SCOPE_NAMES[x] || x);
    return skipped.length ? `Không quét trong lượt này: ${skipped.join(', ')}. Ít mục hơn không có nghĩa các nhóm này đã an toàn.` : '';
  }
  function filterLabel(filter, queue) {
    if (filter === 'pending') return `Chưa duyệt (${countsFrom(queue?.items || []).pending})`;
    if (filter === 'candidates') return `Ứng viên phụ (${(queue?.advisory_items || []).length})`;
    const f = FILTERS.concat(MORE_FILTERS).find(x => x[0] === filter); return f ? f[1] : '';
  }

  /* R1, media of a card (classic thumbTime, momentIndex, pickStrip, pickSceneStrip, stripFrames, tlPos,
   * renderTimeline, videoReason; verify-review.cjs compares them). */
  function thumbTime(p) { const m = /-(\d+(?:\.\d+)?)s\.(?:jpg|jpeg|png)$/i.exec(String(p || '')); return m ? Number(m[1]) : null; }
  function momentIndex(t, ms) { return ms.findIndex(m => t >= m.start - .05 && t <= m.end + .05); }
  function nextMomentAfter(t, ms) { return (ms || []).findIndex(m => m.start > t + .01); }
  function pickStrip(frames, n = 8) {
    const list = (frames || []).slice().sort((a, b) => a.t - b.t);
    if (list.length <= n) return list;
    const chosen = new Set(), strongest = list.findIndex(f => f.kind === 'strongest');
    if (strongest >= 0) chosen.add(strongest);
    const take = (indexes, slots) => {
      if (slots <= 0 || !indexes.length) return;
      if (indexes.length <= slots) { indexes.forEach(i => chosen.add(i)); return; }
      for (let s = 0; s < slots; s++) chosen.add(indexes[Math.min(indexes.length - 1, Math.floor((s + .5) * indexes.length / slots))]);
    };
    const seeds = list.map((f, i) => f.kind === 'seed' && !chosen.has(i) ? i : -1).filter(i => i >= 0);
    take(seeds, Math.ceil((n - chosen.size) / 2));
    take(list.map((_f, i) => chosen.has(i) ? -1 : i).filter(i => i >= 0), n - chosen.size);
    return [...chosen].sort((a, b) => a - b).slice(0, n).map(i => list[i]);
  }
  function pickSceneStrip(frames, ms, n = 8) {
    const list = (frames || []).filter(f => momentIndex(f.t, ms) >= 0).sort((a, b) => a.t - b.t);
    if (list.length <= n) return list;
    const rank = f => f.kind === 'strongest' ? 3 : f.kind === 'seed' ? 2 : 1, groups = ms.map(() => []);
    list.forEach(f => groups[momentIndex(f.t, ms)].push(f));
    let order = ms.map((_m, i) => i).filter(i => groups[i].length);
    if (order.length > n) {
      const strong = list.find(f => f.kind === 'strongest'), picked = new Set(strong ? [momentIndex(strong.t, ms)] : []);
      for (let s = 0; picked.size < n && s < order.length; s++) picked.add(order[Math.min(order.length - 1, Math.floor((s + .5) * order.length / n))]);
      order = [...picked];
    }
    const chosen = new Set();
    for (const i of order) {
      if (chosen.size >= n) break;
      const mid = (ms[i].start + ms[i].end) / 2;
      chosen.add(groups[i].slice().sort((a, b) => rank(b) - rank(a) || (Number(b.score) || 0) - (Number(a.score) || 0) || Math.abs(a.t - mid) - Math.abs(b.t - mid))[0]);
    }
    if (chosen.size < n) for (const f of pickStrip(list.filter(f => !chosen.has(f)), n - chosen.size)) chosen.add(f);
    return [...chosen].sort((a, b) => a.t - b.t);
  }
  function pickFor(x, frames) { return isScene(x) ? pickSceneStrip(frames, momentsOf(x), 8) : pickStrip(frames, 8); }
  /* Evidence frames when the video can be read (a media key, not cleaned or missing), else ≤8 report previews.
   * R1 puts the "Rõ nhất" frame first in the strip; the frames keep their time order otherwise. */
  function stripFrames(x, ev, hasKey) {
    if (ev && hasKey && (ev.frames || []).length && !['source_cleaned', 'source_missing'].includes(ev?.video?.reason)) return pickFor(x, ev.frames).map(f => ({t: f.t, kind: f.kind, remote: true}));
    const ms = momentsOf(x), inside = p => { const t = thumbTime(p); return !isScene(x) || t == null || momentIndex(t, ms) >= 0; };
    return (x.preview_images || []).filter(inside).slice(0, 8).map(p => ({t: thumbTime(p), kind: 'preview', path: p}));
  }
  function peakFirst(frames) { const i = frames.findIndex(f => f.kind === 'strongest'); return i > 0 ? [frames[i]].concat(frames.slice(0, i), frames.slice(i + 1)) : frames; }
  function tlPos(t, start, end) { const len = Math.max(.001, end - start); return 2 + 96 * Math.min(1, Math.max(0, (Number(t) - start) / len)); }
  function thin(values, limit) { if (values.length <= limit) return values; const out = []; for (let s = 0; s < limit; s++) out.push(values[Math.floor((s + .5) * values.length / limit)]); return out; }
  /* The classic renderTimeline markup (without its single playhead): bars, detector ticks and the peak. */
  function timelineHtml(x, ev) {
    const start = Number(x.start_seconds), end = Number(x.end_seconds), pos = t => tlPos(t, start, end);
    const bar = (a, b, cls, extra = '') => { const left = pos(a), right = pos(b); return `<div class="${cls}"${extra} style="left:${left.toFixed(2)}%;width:${Math.max(.6, right - left).toFixed(2)}%"></div>`; };
    const segments = ev?.detected_intervals?.length ? ev.detected_intervals : (x.detected_intervals || []).map(d => ({start: d.start_seconds, end: d.end_seconds}));
    let html;
    if (isScene(x)) html = '<div class="gapline" style="left:2%;width:96%"></div>' + momentsOf(x).map((m, i) => bar(m.start, m.end, 'mo', ` data-i="${i}" title="Khoảnh khắc ${i + 1}: ${mmss(m.start)}–${mmss(m.end)}"`)).join('');
    else html = (segments.length ? segments : [{start: x.start_seconds, end: x.end_seconds}]).map(s => bar(s.start, s.end, 'seg')).join('');
    const seeds = ev?.seeds;
    if (seeds && !seeds.known) html += (seeds.windows || []).map(w => bar(w.start, w.end, 'win')).join('');
    const ticks = seeds?.known ? thin((seeds.samples || []).map(s => s.t), 120) : (ev?.frames || []).filter(f => f.kind === 'seed').map(f => f.t);
    html += ticks.map(t => `<div class="hit" style="left:${pos(t).toFixed(2)}%"></div>`).join('');
    const peak = ev?.strongest?.t ?? thumbTime((x.preview_images || [])[0]);
    if (peak != null) html += `<div class="peak" style="left:${pos(peak).toFixed(2)}%" title="Rõ nhất lúc ${mmss(peak)}"></div>`;
    return html;
  }
  /* A click at `ratio` of the timeline width (the classic handler): a time in the range; a scene snaps
   * a gap to the start of the next moment (or the last one). */
  function seekTarget(x, ratio) {
    const start = Number(x.start_seconds), end = Number(x.end_seconds), ms = isScene(x) ? momentsOf(x) : null;
    let t = start + Math.min(1, Math.max(0, (ratio * 100 - 2) / 96)) * (end - start), moment = -1;
    if (ms) { moment = momentIndex(t, ms); if (moment < 0) { moment = nextMomentAfter(t, ms); if (moment < 0) moment = ms.length - 1; t = ms[moment].start; } }
    return {t, moment};
  }
  const VIDEO_REASONS = {unsupported_container:'Trình duyệt không phát được định dạng video này; hãy xem dải khung hình.',source_changed:'Video nguồn đã thay đổi sau khi quét; chỉ xem được khung hình.',source_missing:'Không tìm thấy video nguồn.',source_cleaned:'Video gốc đã được dọn vào Thùng rác; chỉ xem được ảnh đã lưu trong report.',source_unknown:'Không rõ video nguồn.',decode_error:'Trình duyệt không giải mã được video này; hãy xem dải khung hình.'};
  function videoReason(info, hasKey) { if (!hasKey) return 'Trang này chỉ có ảnh xem trước, không phát video.'; return VIDEO_REASONS[info?.reason] || 'Không phát được video trong trình duyệt; hãy xem dải khung hình.'; }
  /* Media status codes of a probe → reason (classic videoFailed): 2xx with a media error 3/4 = decode error. */
  function probeReason(status, mediaErrorCode) {
    return {404:'source_missing',409:'source_changed',410:'source_cleaned',415:'unsupported_container'}[status] || (status >= 200 && status < 300 && (mediaErrorCode === 3 || mediaErrorCode === 4) ? 'decode_error' : null);
  }


  /* R2: decisions, as the classic decide() / clearDecision() / undo() in pure steps. The dialog asks every
   * message of decisionConfirms() in order (any "Hủy" stops), then decisionBody() gives the POST body or the
   * classic error, applyDecision() changes the item locally and the write is queued. */
  function momentTotal(ms) { return ms.reduce((sum, m) => sum + (m.end - m.start), 0); }
  function sceneBlurMessage(x) { const ms = momentsOf(x); return `Làm mờ toàn bộ khung hình trong ${ms.length} khoảnh khắc (tổng ${mmss(momentTotal(ms))})? Khoảng trống giữa các khoảnh khắc giữ nguyên.`; }
  function aiConfirm(item, decision) {
    const ai = item.ai_visual_audit, aiDecision = ai?.suggested_decision, confidence = Number(ai?.confidence || 0);
    if (!(aiDecision && confidence >= .9 && decision !== aiDecision && FINAL.includes(aiDecision) && FINAL.includes(decision))) return '';
    return `Visual AI tin cậy ${Math.round(confidence * 100)}% đề xuất “${actionName(item, aiDecision)}” vì vùng đỏ được nhận là ${ai.classification || 'nội dung phim'}. Bạn vẫn muốn chọn “${actionName(item, decision)}” cho đúng vùng đỏ này?`;
  }
  function decisionConfirms(item, decision, fullFrame) {
    const out = [], ai = aiConfirm(item, decision);
    if (ai) out.push(ai);
    if (decision === 'BLUR' && fullFrame) out.push(isScene(item) ? sceneBlurMessage(item) : TEXT.fullFrame);
    return out;
  }
  /* options: {fullFrame (the whole-frame confirm was accepted), note, studio, platform}. */
  function decisionBody(item, decision, options) {
    const o = options || {}, id = item.id, note = o.note == null ? null : o.note, full_frame = decision === 'BLUR' && !!o.fullFrame;
    const platform = !!o.platform && decision === 'BLUR' && !full_frame && platformEligible(item);
    if (decision === 'BLUR' && !full_frame && !platform && !item.suggested_region_source_pixels) return {error: TEXT.needsRegion};
    const studio = !!o.studio && decision === 'KEEP' && studioEligible(item);
    const body = platform ? {id, decision, full_frame, note, remember_platform_logo: true} : studio ? {id, decision, full_frame, note, remember_studio_logo: true} : {id, decision, full_frame, note};
    return {body, region: decision === 'BLUR' ? (full_frame ? 'FULL_FRAME' : item.suggested_region_source_pixels) : null, note, studio, platform};
  }
  function regionNote(decision) { return REGION_NOTES[decision] || null; }
  /* Keys 1–4 on the selected card: the card's own buttons (R2-K: key 2 is the card's BLUR button). */
  function keyDecision(n, x) { const d = {1: 'KEEP', 2: 'BLUR', 3: 'CUT', 4: 'NEEDS_MORE_CONTEXT'}[n]; return d ? {decision: d, fullFrame: d === 'BLUR' && needsFullFrame(x)} : null; }
  function blurLabel(x) { return needsFullFrame(x) ? 'Làm mờ cả cảnh' : 'Làm mờ'; }
  /* The main button shown as chosen (.selected): BLUR only when it matches the card's BLUR button. */
  function chosenButton(x) { if (x.decision !== 'BLUR') return DECISIONS.includes(x.decision) ? x.decision : null; return needsFullFrame(x) === (x.decision_region_source_pixels === 'FULL_FRAME') ? 'BLUR' : null; }
  function studioRemembered(x) { return x.decision === 'KEEP' && x.studio_logo_memory?.remembered ? x.studio_logo_memory : null; }
  function platformRemembered(x) { return x.decision === 'BLUR' && x.platform_logo_memory?.remembered ? x.platform_logo_memory : null; }
  function isAdvisoryItem(queue, x) { return !!x && (!!x.advisory || (queue?.advisory_items || []).includes(x)); }
  function syncCounts(queue) { queue.counts = countsFrom(queue.items); queue.status = statusFrom(queue.items); }
  function applyDecision(queue, item, decision, region, note, studio, platform) {
    item.decision = decision; item.decision_region_source_pixels = region; item.decision_note = note; item.decided_at = new Date().toISOString();
    if (studio) item.studio_logo_memory = {remembered: true}; else delete item.studio_logo_memory;
    if (platform) item.platform_logo_memory = {remembered: true}; else delete item.platform_logo_memory;
    syncCounts(queue);
  }
  function applyClear(queue, item) {
    item.decision = null; item.decision_region_source_pixels = null; item.decision_note = null; item.decided_at = null;
    delete item.studio_logo_memory; delete item.platform_logo_memory;
    syncCounts(queue);
  }
  /* Undo (≤100 steps, P10): the state before a change; an advisory item decided for the first time cannot go back. */
  function undoEntry(item, advisory) {
    return {id: item.id, advisory: !!advisory, prev: {decision: item.decision || null, region: item.decision_region_source_pixels ?? null, note: item.decision_note ?? null,
      studio: !!item.studio_logo_memory?.remembered, platform: !!item.platform_logo_memory?.remembered}};
  }
  function undoPlan(entry) {
    const prev = entry.prev;
    if (!prev.decision) return {kind: 'clear', body: {id: entry.id}};
    const body = {id: entry.id, decision: prev.decision, full_frame: prev.region === 'FULL_FRAME', note: prev.note};
    if (prev.studio) body.remember_studio_logo = true;
    if (prev.platform) body.remember_platform_logo = true;
    return {kind: 'decision', body};
  }
  /* Title of "↶ Hoàn tác" (classic updateNavState). */
  function undoTitle(entry, x) {
    if (!x) return 'Chưa có lựa chọn nào trong phiên này để hoàn tác';
    return entry.advisory ? `Lựa chọn cho ứng viên phụ ${catName(x)} ${span(x)} không hoàn tác được (phím Z để xem lý do)` : `Hoàn tác lựa chọn cho ${catName(x)} ${span(x)} (phím Z)`;
  }
  function decisionLabel(x) {
    if (x.decision === 'BLUR' && x.platform_logo_memory?.remembered) { const m = x.platform_logo_memory, name = m.platform && m.platform.key !== 'unknown' && m.platform.name ? ` ${m.platform.name}` : ''; return `Làm mờ logo · đã nhớ là logo nền tảng${name}`; }
    if (x.decision === 'KEEP' && x.studio_logo_memory?.remembered) { const m = x.studio_logo_memory; return m.frames != null ? `Giữ nguyên · đã nhớ là logo hãng phim (${Number(m.frames)} khung${m.ignored_regions?.length ? ', bỏ qua watermark đã làm mờ' : ''})` : 'Giữ nguyên · đã nhớ là logo hãng phim'; }
    if (x.decision === 'BLUR' && x.decision_region_source_pixels === 'FULL_FRAME') return isScene(x) ? `Làm mờ toàn cảnh trong ${momentsOf(x).length} khoảnh khắc` : 'Làm mờ toàn cảnh';
    if (x.decision === 'BLUR') return isLogoItem(x) ? 'Làm mờ logo' : 'Làm mờ vùng chữ/logo';
    if (x.decision === 'NEEDS_MORE_CONTEXT') return 'Cần xem thêm';
    return actionName(x, x.decision);
  }
  function advisoryUndoMessage(x) { return `Không hoàn tác được lựa chọn cho ứng viên phụ ${catName(x)} ${span(x)}: khi bạn chọn, mục này đã được chuyển vào danh sách chính. Bỏ chọn lúc này sẽ biến nó thành mục bắt buộc chưa duyệt và chặn xuất video, nên lựa chọn “${decisionLabel(x)}” được giữ nguyên. Nếu muốn đổi, hãy chọn lại Giữ nguyên, Làm mờ, Cắt hoặc Cần xem thêm cho mục này.`; }
  /* The last write failed (after its retries): the classic message; x = the item of body.id, if still known. */
  function writeFailureMessage(kind, body, error, x) {
    const where = x ? `${catName(x)} ${span(x)}` : String(body?.id || '');
    const what = kind === 'clear' ? 'bỏ chọn' : `“${body?.remember_platform_logo ? 'Đây là logo nền tảng — làm mờ & nhớ' : body?.decision === 'BLUR' && body?.full_frame ? 'Làm mờ cả cảnh' : x ? actionName(x, body?.decision) : String(body?.decision || '')}”`;
    const raw = String(error?.message || ''), detail = error?.status >= 500 && /WinError|Errno|denied|[\\/]/i.test(raw) ? 'máy chủ chưa ghi được file hàng đợi (file đang bị đọc hoặc khóa)' : raw;
    return `Chưa lưu được lựa chọn ${what} cho mục ${where}${error?.attempts > 1 ? ` (đã thử ${error.attempts} lần)` : ''}. Mục này sẽ trở về trạng thái đã lưu trên máy và được mở lại để bạn chọn lại. Chi tiết: ${detail}`;
  }
  /* A write error worth a retry (network error or status ≥ 500); 400/403/409 are not (403 is handled by the adapter). */
  function transientWrite(error) { return !error || !error.status || error.status >= 500; }

  /* R3. Bulk (classic bulkKeep / bulkAccept): the filters to send, in order, and the confirm text with the S1 count.
   * kind: 'bulkKeep' | 'bulkAccept'. */
  function bulkPlan(queue, filter, kind) {
    const filters = bulkFilters(filter);
    if (!filters) return {error: TEXT.bulkUnsupported};
    const count = bulkCount(queue, filter, kind === 'bulkAccept' ? 'accept' : 'keep');
    if (!count) return {error: kind === 'bulkAccept' ? TEXT.bulkAcceptNone : TEXT.bulkKeepNone};
    return {filters, count, confirm: kind === 'bulkAccept' ? `Áp dụng ${count} đề xuất đang hiển thị? Bạn vẫn có thể bỏ chọn từng mục trước khi xuất.`
      : `Giữ nguyên ${count} mục chưa duyệt đang hiển thị? Thao tác này không blur hoặc cắt video.`};
  }
  /* The export state under the progress (classic renderExport exportText), for what "N / M" and S2 do not say:
   * an export queued, running, done or failed, a skipped video, a cleaned or archived source. */
  function exportLine(exp) {
    const e = exp || {}, done = e.status === 'COMPLETED' ? `Hoàn tất: ${e.output || ''}. ` : '';
    if (e.source_cleaned) { const when = C.formatStamp(e.source_cleanup?.finished_at); return `${done}Video gốc đã được dọn vào Thùng rác${when ? ` lúc ${when}` : ''}. Chép lại đúng tên “${e.source_name || ''}” vào input để xuất lại hoặc sửa quyết định.`; }
    if (e.source_archived) { const when = C.formatStamp(e.source_archive?.archived_at); return `${done}Video gốc đang ở kho lưu trữ${when ? ` từ ${when}` : ''}. Bấm “Khôi phục bản xuất” trên Dashboard để xuất lại hoặc sửa quyết định.`; }
    return {QUEUED: 'Đã xếp hàng xuất video.', RENDERING: 'Đang render và kiểm tra video…', COMPLETED: `Hoàn tất: ${e.output || ''}`, FAILED: `Xuất thất bại: ${e.error || 'không rõ lỗi'}`,
      SKIPPED: 'Video đã được đánh dấu bỏ qua (không xuất). Bấm “Mở lại để xuất” ở Dashboard nếu muốn xuất video.'}[e.status] || '';
  }
  const exportActive = exp => !!exp && ['QUEUED', 'RENDERING'].includes(exp.status);

  return {SAFETY,KIND_NAMES,STATUS,FILTERS,MORE_FILTERS,FILTER_IDS,TEXT,isSafety,momentsOf,isScene,studioEligible,platformEligible,sceneLogo,hasPlayer,
    isLogoItem,isAdItem,needsFullFrame,catName,actionName,sceneName,statusOf,mmss,span,visible,byTime,listItems,itemMap,countsFrom,statusFrom,progress,progressText,
    nextNote,initialFilter,pickFocus,nextUndecided,step,queueIdentity,queueVersion,bulkFilters,bulkCount,lockState,canExport,regionOwner,regionBox,
    frameAspect,scopeWarning,filterLabel,thumbTime,momentIndex,nextMomentAfter,pickStrip,pickSceneStrip,pickFor,stripFrames,peakFirst,tlPos,thin,timelineHtml,
    seekTarget,VIDEO_REASONS,videoReason,probeReason,REGION_NOTES,WRITE_RETRY_MS,momentTotal,sceneBlurMessage,aiConfirm,decisionConfirms,decisionBody,regionNote,
    keyDecision,blurLabel,chosenButton,studioRemembered,platformRemembered,isAdvisoryItem,syncCounts,applyDecision,applyClear,undoEntry,undoPlan,undoTitle,decisionLabel,
    advisoryUndoMessage,writeFailureMessage,transientWrite,bulkPlan,exportLine,exportActive};
});
