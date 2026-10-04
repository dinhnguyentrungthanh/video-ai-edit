/* Review logic of the V2 "Duyệt cảnh" dialog: pure functions, no DOM, no fetch.
 * It mirrors the classic review page (_interactive_html in review_workflow.py); verify-review.cjs
 * extracts the classic functions and compares both on sample queues. Deliberate differences:
 *   S1 bulk counts follow the server (category, main undecided items; accept skips BLUR without region);
 *   S2 only NEEDS_MORE_CONTEXT left → "Còn N mục Cần xem thêm …" instead of "Đã duyệt đủ";
 *   S6 "export active" is contracts.inFlight (render_request, RENDERING/VERIFYING, QUEUED export),
 *      not every QUEUED job;
 *   S7 the queue identity reads source.sha256 (the classic page reads source.input_sha256, always empty);
 *   S8 a SKIPPED video opens read-only.
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
    rescanning:'Video đang được quét lại. Đóng hộp và mở lại khi quét xong.'
  };

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

  return {SAFETY,KIND_NAMES,STATUS,FILTERS,MORE_FILTERS,FILTER_IDS,TEXT,isSafety,momentsOf,isScene,studioEligible,platformEligible,sceneLogo,hasPlayer,
    isLogoItem,isAdItem,needsFullFrame,catName,actionName,sceneName,statusOf,mmss,span,visible,byTime,listItems,itemMap,countsFrom,statusFrom,progress,progressText,
    nextNote,initialFilter,pickFocus,nextUndecided,step,queueIdentity,queueVersion,bulkFilters,bulkCount,lockState,canExport,regionOwner,regionBox,
    frameAspect,scopeWarning,filterLabel};
});
