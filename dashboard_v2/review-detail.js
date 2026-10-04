/* Review dialog (R1): texts of a card's evidence and "Chi tiết kỹ thuật", ported from the classic review
 * page (viText, labelsReasonsHtml, visualAiHtml, evidenceHtml, regionDetailHtml, decisionScope, scopeBlock,
 * overlapCoverage, aiModelLine, techDetails, evidenceMediaHtml). Pure: no DOM, no fetch. Every queue string
 * goes through esc(). verify-review.cjs compares the output with the classic functions.
 */
(function (root, factory) {
  const core = typeof module === 'object' && module.exports ? require('./review-core.js') : root.BFReviewCore;
  const api = factory(core);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BFReviewDetail = api;
})(typeof window === 'undefined' ? this : window, function (R) {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clock = s => { const m = Math.floor(s / 60), v = (s - m * 60).toFixed(1).padStart(4, '0'); return `${String(m).padStart(2, '0')}:${v}`; };
  const SCENE_WORDS = {violence:'Trận đánh',gore:'Cảnh máu',adult:'Nhóm 18+'};
  const VI = {'The first video window is retained once so external intros are not silently missed':'Luôn giữ 5 giây đầu video một lần để không bỏ sót intro ngoài phim','Opening boundary review':'Kiểm tra đoạn mở đầu','Ending boundary review':'Kiểm tra đoạn kết','Visual brand/logo candidate':'Ứng viên logo/thương hiệu','Local visual-language model confirmed branding/logo evidence':'AI hình ảnh cục bộ xác nhận có logo/thương hiệu trong khung','Visual-language answer was uncertain; human review required':'AI hình ảnh trả lời không chắc chắn; cần người duyệt','Visual-language model rejected this window; retained in exhaustive audit':'AI hình ảnh đã loại cửa sổ này; vẫn lưu trong audit đầy đủ','A visual signature from an earlier human-approved brand item matched; review is still required':'Khớp hình một logo thương hiệu bạn đã duyệt trước đó; vẫn cần duyệt lại','Full-frame opening promotion / branded intro':'Quảng bá / intro thương hiệu toàn khung ở đầu video','Local boundary semantics grouped consecutive full-frame promotional windows':'Các cửa sổ quảng bá toàn khung liên tiếp ở đầu video được gom thành một thẻ','Branded end card / channel promotion':'End card thương hiệu / quảng bá kênh','Temporal boundary evidence grouped branded final windows; one human decision covers the complete end card':'Các cửa sổ thương hiệu ở cuối video được gom; một quyết định áp dụng cho cả end card','Persistent external logo / watermark':'Logo / watermark bên ngoài cố định','Repeated regional visual-brand confirmations were grouped into one human review item':'Nhiều lần xác nhận logo ở cùng vùng được gom thành một thẻ duyệt','Full-frame promotional material':'Quảng cáo / intro toàn khung','Local visual-language model classified the boundary window as full-frame promotional material':'AI hình ảnh cục bộ nhận định đoạn đầu/cuối video là quảng cáo toàn khung','Known approved external brand':'Thương hiệu bên ngoài bạn đã duyệt'};
  const SCANNER_LABELS = ['Persistent external logo / watermark','Visual brand/logo candidate','Opening boundary review','Ending boundary review','Full-frame promotional material','Full-frame opening promotion / branded intro','Branded end card / channel promotion','Known approved external brand'];

  function viText(value) { const text = String(value ?? ''); return VI[text] || text; }
  function readingLabel(o, fallback) {
    return (o?.labels || []).map(v => String(v ?? '').replace(/<\/?s>/g, '').trim()).find(l => l && !SCANNER_LABELS.includes(l) && !(l === l.toLowerCase() && /^[\p{L} ]+$/u.test(l))) || fallback;
  }
  function memoryMatch(m) { return m?.vlm_source === 'approved_brand_memory' || /^MEMORY_MATCH\b/i.test(String(m?.vlm_answer || '')); }
  function memoryBrandName(m) { return readingLabel({labels: [String(m?.vlm_answer || '').replace(/^MEMORY_MATCH\s*\|?\s*/i, '')]}, ''); }
  function boxesFromMemory(boxes) { return boxes.length > 0 && boxes.every(b => Array.isArray(b.sources) && b.sources.length > 0 && b.sources.every(s => s === 'brand_memory')); }
  function trackCoversFullVideo(queue, x) {
    const duration = Number(queue?.source?.duration_seconds || 0), tolerance = Math.max(1.5, duration * .0005);
    return duration > 0 && Number(x.start_seconds) <= tolerance && Number(x.end_seconds) >= duration - tolerance;
  }
  function regionOverlap(a, b) {
    if (!a || !b || a === 'FULL_FRAME' || b === 'FULL_FRAME') return 0;
    const left = Math.max(a.x, b.x), top = Math.max(a.y, b.y), right = Math.min(a.x + a.width, b.x + b.width), bottom = Math.min(a.y + a.height, b.y + b.height);
    const intersection = Math.max(0, right - left) * Math.max(0, bottom - top), smaller = Math.min(a.width * a.height, b.width * b.height);
    return smaller ? intersection / smaller : 0;
  }
  function regionName(owner) {
    if (owner?.decision === 'BLUR') return 'logo thương hiệu đã xác nhận';
    if (owner?.decision === 'KEEP') return 'tiêu đề/nội dung phim đã xác nhận';
    const names = {movie_title:'tiêu đề phim',approved_non_brand:'nội dung phim đã xác nhận',external_brand:'logo thương hiệu',external_brand_candidate:'ứng viên logo thương hiệu',branded_end_card:'end-card thương hiệu',promotional_segment:'đoạn quảng bá',unknown:'chưa phân loại'};
    return names[owner?.region_classification] || 'vùng chưa phân loại';
  }
  function decisionScope(queue, x) {
    const intervals = Array.isArray(x.detected_intervals) ? x.detected_intervals : [], from = clock(x.start_seconds), to = clock(x.end_seconds);
    if (x.advisory) return {kind:'advisory',title:'Ứng viên kiểm tra thêm — chưa thuộc quyết định chính',detail:`Bằng chứng chưa đủ để ghép mục này vào track chính. Thẻ chính khác không tự xử lý mục này. Nếu bạn chọn một hành động, mục sẽ được đưa vào kế hoạch và chỉ áp dụng ${from}–${to}.`};
    if (x.candidate_type === 'persistent_overlay' || x.temporal_policy === 'continuous_persistent_overlay') {
      const full = trackCoversFullVideo(queue, x), support = Number(x.supporting_candidate_count || 0);
      return {kind:'track',title:full ? 'QUYẾT ĐỊNH TOÀN VIDEO' : 'QUYẾT ĐỊNH TOÀN KHOẢNG XUẤT HIỆN',detail:`Một lựa chọn cho vùng khoanh đỏ áp dụng từ ${from} đến ${to}${full ? ' — toàn bộ video' : ''}. ${support ? `Track này đại diện thêm ${support} lần phát hiện cùng vùng đã lưu trong Audit. ` : ''}Logo ở vị trí hoặc track khác vẫn cần quyết định riêng.`};
    }
    if (x.temporal_policy === 'discrete_detected_intervals' && intervals.length > 1) return {kind:'grouped',title:`${(SCENE_WORDS[x.category] || 'Nhóm sự kiện').toUpperCase()} — ${intervals.length} KHOẢNH KHẮC`,detail:`Một lựa chọn được áp dụng riêng cho ${intervals.length} khoảnh khắc phát hiện trong ${from}–${to} (${R.momentsOf(x).map(m => `${clock(m.start)}–${clock(m.end)}`).join('; ')}); các khoảng trống giữa chúng không bị cắt hoặc làm mờ.`};
    if (intervals.length > 1) return {kind:'grouped',title:`Đại diện cho ${intervals.length} lần phát hiện đã gom`,detail:`Các lần phát hiện gần nhau đã được gom thành cửa sổ ${from}–${to}; quyết định áp dụng toàn bộ cửa sổ này. Không tự lan sang cảnh khác.`};
    return {kind:'single',title:'CHỈ ĐOẠN HIỆN TẠI',detail:`Quyết định chỉ áp dụng ${from}–${to}. Đây không phải lựa chọn đại diện cho mọi quảng cáo hoặc logo cùng loại trong toàn phim.`};
  }
  function scopeBlock(queue, x) { const scope = decisionScope(queue, x); return `<div class="scope-detail ${scope.kind}"><strong>Phạm vi áp dụng: ${esc(scope.title)}</strong>${esc(scope.detail)}</div>`; }
  function overlapCoverage(queue, x) {
    const covered = (queue?.items || []).filter(o => o.id !== x.id && o.decision === 'BLUR' && o.start_seconds < x.end_seconds && o.end_seconds > x.start_seconds);
    if (!covered.length) return '';
    const persistent = covered.filter(o => o.candidate_type === 'persistent_overlay' && o.decision_region_source_pixels && o.decision_region_source_pixels !== 'FULL_FRAME');
    const full = covered.filter(o => o.decision_region_source_pixels === 'FULL_FRAME'), parts = [];
    if (persistent.length) {
      const owner = persistent[0], r = owner.decision_region_source_pixels, current = x.suggested_region_source_pixels || x.decision_region_source_pixels;
      const label = esc(readingLabel(owner, viText((owner.labels || [])[0] || '') || 'logo/watermark')), scope = trackCoversFullVideo(queue, owner) ? 'toàn video' : `${clock(owner.start_seconds)}–${clock(owner.end_seconds)}`;
      if (!current) {
        const drawn = !R.isSafety(x) && R.regionOwner(queue, x)?.id === owner.id;
        parts.push(`Track <strong>${label}</strong> đã được duyệt làm mờ ${scope} ở thẻ riêng${drawn ? ' (khung đỏ trong ảnh)' : ''}. Thẻ này không có vùng riêng — quyết định bên dưới áp dụng cho cả đoạn ${R.span(x)}.`);
      } else {
        const same = regionOverlap(r, current) >= .6;
        parts.push(same ? `Track <strong>${label}</strong> cùng vùng này đã được duyệt làm mờ ${scope}; thẻ hiện tại chỉ là bằng chứng hỗ trợ.` : `Track <strong>${label}</strong> ở vùng khác đã được duyệt làm mờ ${scope} (x=${Number(r.x)}, y=${Number(r.y)}, rộng=${Number(r.width)}, cao=${Number(r.height)}). Vùng đỏ hiện tại vẫn là ứng viên riêng.`);
      }
    }
    if (full.length) parts.push(`${full.length} đoạn trùng thời gian đã được duyệt làm mờ toàn cảnh.`);
    return parts.length ? `<div class="coverage">${parts.join(' ')}</div>` : '';
  }
  function regionDetailHtml(x, owner, r) {
    const borrowed = owner && owner.id !== x.id, status = regionName(owner);
    return owner && r && r !== 'FULL_FRAME' ? `<div class="region-detail">Chỉ nội dung nằm trong khung đỏ này đang được phân loại. Vùng khoanh đỏ: <strong>${esc(status)}</strong> · x=${Number(r.x)}, y=${Number(r.y)}, rộng=${Number(r.width)}, cao=${Number(r.height)}${borrowed ? ` · vùng liên kết áp dụng ${clock(owner.start_seconds)}–${clock(owner.end_seconds)}` : ''}</div>` : '<div class="region-detail">Chưa có vùng được định vị nên không thể phân loại logo hay tiêu đề một cách an toàn.</div>';
  }
  function labelsReasonsHtml(x) {
    const labels = (x.labels || []).map(viText), reasons = (x.reasons || []).map(viText);
    return `${labels.length ? `<div class="labels">Nhãn: ${esc(labels.join(', '))}</div>` : ''}${reasons.length ? `<div class="labels">Lý do: ${esc(reasons.join(' · '))}</div>` : ''}`;
  }
  function visualAiHtml(x) { const a = x.ai_visual_audit; return a ? `<div class="visual-ai"><strong>Visual AI:</strong> ${esc(a.classification)} · tin cậy ${Math.round(100 * Number(a.confidence || 0))}% · đề xuất ${esc(R.actionName(x, a.suggested_decision))} · vùng ${esc(a.region_assessment)}<br>${esc(a.reasoning)}</div>` : ''; }
  function evidenceHtml(ev) {
    if (!ev) return '';
    const seeds = ev.seeds || {}, windows = seeds.windows || [], context = ev.context || {};
    const rows = [`Ngưỡng máy dò ${seeds.threshold ?? '—'} · lấy mẫu ${ev.sample_fps ?? '—'} khung/giây · ${seeds.known ? 'đã lưu thời điểm từng khung nghi ngờ' : 'bản quét cũ: chưa lưu thời điểm từng khung nghi ngờ'}`];
    if (ev.strongest) rows.push(`Điểm cao nhất ${Number(ev.strongest.score ?? 0).toFixed(3)} lúc ${clock(ev.strongest.t)}`);
    if (windows.length) rows.push(`Cửa sổ máy dò: ${windows.map(w => `${clock(w.start)}–${clock(w.end)} (${w.count} khung)`).join('; ')}`);
    if ((context.extended || []).length) rows.push(`Mở rộng theo ngữ cảnh: ${context.extended.map(w => `${clock(w.start)}–${clock(w.end)}`).join('; ')}${context.threshold != null ? ` (ngưỡng ${context.threshold})` : ''}`);
    if (ev.ignored_ref_count) rows.push(`${ev.ignored_ref_count} tham chiếu không đọc được đã bỏ qua`);
    return `<div class="evidence"><strong>Bằng chứng máy dò</strong>${rows.map(v => `<div>${esc(v)}</div>`).join('')}</div>`;
  }
  function aiModelLine(x) {
    const m = x.model_evidence || {};
    if (x.category !== 'visual_logo' || !('vlm_confirmation' in m || m.vlm_answer)) return '';
    const score = x.max_score == null ? '—' : Number(x.max_score).toFixed(3), scenes = {PROMO_FULL_FRAME:'quảng cáo / intro toàn khung',MOVIE_CONTENT:'nội dung phim',UNCERTAIN:'chưa chắc'};
    const scene = m.vlm_scene ? `; AI (Qwen) về cả cảnh: ${esc(scenes[m.vlm_scene] || m.vlm_scene)} (${esc(m.vlm_scene)})` : '';
    if (memoryMatch(m)) { const name = memoryBrandName(m); return `<div class="labels">Bộ nhớ thương hiệu: khớp hình logo bạn đã duyệt trước đó${name ? ` (${esc(name)})` : ''}, AI không được hỏi về logo; trạng thái ${esc(m.vlm_confirmation || '—')}${scene}; điểm ${score} là điểm xếp hạng (độ giống với bộ nhớ hoặc điểm hình học), không phải độ tin cậy AI</div>`; }
    return `<div class="labels">AI (Qwen): trả lời ${m.vlm_answer ? `“${esc(m.vlm_answer)}”` : '— (thẻ cũ, chưa lưu câu trả lời gốc)'}; trạng thái ${esc(m.vlm_confirmation || '—')}${m.promoted_from_rejected_boundary ? ' (đổi từ câu trả lời KHÔNG để giữ 5 giây đầu)' : ''}${scene}; điểm ${score} là điểm hình học, không phải độ tin cậy AI</div>`;
  }
  /* The classic techDetails body without the region buttons (decisions come in R2). */
  function techBody(queue, x, ev) {
    const owner = R.regionOwner(queue, x), r = owner && (owner.suggested_region_source_pixels || owner.decision_region_source_pixels);
    const score = x.max_score == null ? '—' : Number(x.max_score).toFixed(3);
    const parts = [`<div class="meta">${esc(x.review_kind || x.category)} · ưu tiên ${esc(x.priority || '—')} · điểm ${score} · ${clock(x.start_seconds)}–${clock(x.end_seconds)} · ${esc(x.id)}</div>`, scopeBlock(queue, x)];
    if (R.isSafety(x)) parts.push(overlapCoverage(queue, x));
    if (!R.isSafety(x) || (owner && r && r !== 'FULL_FRAME')) parts.push(regionDetailHtml(x, owner, r));
    parts.push(labelsReasonsHtml(x), visualAiHtml(x), evidenceHtml(ev));
    if (x.model_evidence) parts.push(aiModelLine(x), `<div class="labels">AI cục bộ: ${esc(JSON.stringify(x.model_evidence))}</div>`);
    return parts.join('');
  }
  /* What the zoomed card shows over its image (classic regionMediaHtml / evidenceMediaHtml):
   *  'region' → its red region and a 360 px crop; 'boxes' → yellow AI boxes (reference only) and red approved
   *  boxes, as fractions of `size`, with the legend; 'none' → nothing to draw (a legend may still say why). */
  function evidenceView(queue, x) {
    const owner = R.regionOwner(queue, x), r = owner && (owner.suggested_region_source_pixels || owner.decision_region_source_pixels);
    const ownRegion = owner && r && r !== 'FULL_FRAME' && Array.isArray(owner.source_frame_size);
    if (ownRegion && !(R.sceneLogo(x) && owner.id !== x.id)) return {mode: 'region', marks: [], size: owner.source_frame_size, legend: ''};
    if (x.category !== 'visual_logo') return {mode: 'none', marks: [], size: null, legend: ''};
    const approved = ownRegion ? owner : null;
    const boxes = (Array.isArray(x.evidence_regions) ? x.evidence_regions : []).filter(b => b && Number(b.width) > 0 && Number(b.height) > 0);
    const size = x.evidence_frame_size || x.source_frame_size || approved?.source_frame_size;
    const marks = boxes.map(b => ({x: Number(b.x), y: Number(b.y), w: Number(b.width), h: Number(b.height), c: b.covered_by ? 1 : 0, o: String(b.covered_by || '')}));
    if (approved && Array.isArray(size)) {
      const fx = Number(size[0]) / Number(approved.source_frame_size[0]), fy = Number(size[1]) / Number(approved.source_frame_size[1]);
      marks.push({x: Number(r.x) * fx, y: Number(r.y) * fy, w: Number(r.width) * fx, h: Number(r.height) * fy, a: 1, o: String(approved.id)});
    }
    if (!marks.length || !Array.isArray(size)) return {mode: 'none', marks: [], size: null, legend: 'Không có khung: AI không định vị vùng logo nào trong ảnh này; thẻ hỏi về cả cảnh.'};
    const legend = [];
    if (boxes.length) legend.push(`Khung vàng: ${boxesFromMemory(boxes) ? 'vùng được định vị (bộ nhớ thương hiệu)' : 'vùng AI định vị'}, chỉ để tham khảo — không phải vùng sẽ làm mờ${boxes.some(b => b.covered_by) ? ' · watermark đã có thẻ riêng' : ''}`);
    if (approved) legend.push(`Khung đỏ: vùng ${readingLabel(approved, 'logo/watermark')} đã được duyệt làm mờ ở thẻ riêng`);
    return {mode: 'boxes', marks, size, legend: legend.join(' · ')};
  }
  /* Crop rectangle of the 360 px crop, in image pixels (classic drawRegionPreviews: letterboxed source frame,
   * 12 % / 18 % padding around the region), and where the red rectangle falls inside it. */
  function cropRect(region, sourceSize, imageW, imageH) {
    const sw = Number(sourceSize[0]), sh = Number(sourceSize[1]), scale = Math.min(imageW / sw, imageH / sh);
    const offsetX = (imageW - sw * scale) / 2, offsetY = (imageH - sh * scale) / 2;
    const rx = Number(region.x) * scale + offsetX, ry = Number(region.y) * scale + offsetY, rw = Number(region.width) * scale, rh = Number(region.height) * scale;
    const padX = rw * .12, padY = rh * .18, x = Math.max(0, rx - padX), y = Math.max(0, ry - padY), w = Math.min(imageW - x, rw + padX * 2), h = Math.min(imageH - y, rh + padY * 2);
    const width = 360, height = Math.max(100, Math.round(360 * h / w));
    return {x, y, w, h, width, height, red: {x: (rx - x) / w * width, y: (ry - y) / h * height, w: rw / w * width, h: rh / h * height}};
  }

  return {esc, clock, viText, readingLabel, memoryMatch, memoryBrandName, boxesFromMemory, trackCoversFullVideo, regionOverlap, regionName, decisionScope, scopeBlock,
    overlapCoverage, regionDetailHtml, labelsReasonsHtml, visualAiHtml, evidenceHtml, aiModelLine, techBody, evidenceView, cropRect};
});
