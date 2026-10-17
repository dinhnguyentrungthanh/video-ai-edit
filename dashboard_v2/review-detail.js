/* Review dialog (R1): texts of a card's evidence and "Chi tiết kỹ thuật", ported from the classic review
 * page (viText, labelsReasonsHtml, visualAiHtml, evidenceHtml, regionDetailHtml, decisionScope, scopeBlock,
 * aiModelLine, techDetails, evidenceMediaHtml) and, since session D (S12), what other cards blur over a card's image
 * (blurCovers, coversAt, coverageNote instead of the classic overlapCoverage). Pure: no DOM, no fetch. Every queue
 * string goes through esc(). verify-review.cjs compares the output with the classic functions.
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
  /* Session D (S12, docs/handoff/D-preview-blurs.md): the regions OTHER cards blur over this card's picture, as the
   * export does (review_workflow.approved_operations): a decided BLUR card's region from its start to its end, a
   * discrete card's in each of its moments. A main card that only suggests BLUR (no decision yet) is 'pending'. Not
   * covers: KEEP and CUT cards, a whole-frame BLUR (coverageNote counts it), candidates (outside the plan until
   * decided), the card itself and the logo card whose red box it borrows (regionHtml draws that one). The box is in
   * fractions of the source frame; scope and short say when, for the note and for the label on the image. */
  const COVER = {eps: .05, list: 6, cells: 3, cellMin: 1 / 160};
  const COVER_KINDS = {repeated_watermark:'watermark',scrolling_text:'dòng chữ chạy'};
  const clamp01 = v => Math.min(1, Math.max(0, v));
  const momentsOrSpan = x => { const ms = R.momentsOf(x); return ms.length ? ms : [{start: Number(x.start_seconds), end: Number(x.end_seconds)}]; };
  const imageTime = t => t == null || t === '' ? NaN : Number(t);
  function coverKind(o) {
    if (COVER_KINDS[o.candidate_type]) return COVER_KINDS[o.candidate_type];
    if (o.candidate_type === 'persistent_overlay') return o.category === 'visual_logo' ? 'logo cố định' : 'chữ cố định';
    return R.isLogoItem(o) ? 'logo' : o.category === 'text' ? 'chữ' : 'vùng';
  }
  function blurCovers(queue, x) {
    const box = R.regionBox(queue, x), borrowed = box && box.owner !== x.id ? box.owner : null, own = momentsOrSpan(x), covers = [];
    for (const o of queue?.items || []) {
      if (!o || o.id === x.id || o.id === borrowed || o.advisory) continue;
      const pending = !o.decision && o.suggested_decision === 'BLUR', r = pending ? o.suggested_region_source_pixels : o.decision === 'BLUR' ? o.decision_region_source_pixels : null;
      if (!r || typeof r !== 'object') continue;
      const ms = momentsOrSpan(o);
      if (!ms.some(m => own.some(n => m.start < Math.max(n.end, n.start + COVER.eps) && m.end > n.start))) continue;
      const sw = Number(o.source_frame_size?.[0]), sh = Number(o.source_frame_size?.[1]);
      if (!(sw > 0 && sh > 0)) continue;
      const region = {x: Number(r.x), y: Number(r.y), width: Number(r.width), height: Number(r.height)};
      // A region that starts left of or above the frame keeps only its part inside it.
      const left = clamp01(region.x / sw), top = clamp01(region.y / sh), width = Math.min(1 - left, (Math.min(region.x, 0) + region.width) / sw), height = Math.min(1 - top, (Math.min(region.y, 0) + region.height) / sh);
      if (!Object.values(region).every(Number.isFinite) || !(width > 0 && height > 0)) continue;
      const full = ms.length === 1 && trackCoversFullVideo(queue, o), last = ms[ms.length - 1];
      covers.push({id: String(o.id), state: pending ? 'pending' : 'applied', region, size: [sw, sh], box: {left, top, width, height}, kind: coverKind(o), reading: readingLabel(o, ''), moments: ms,
        scope: full ? 'toàn phim' : ms.length > 1 ? `${ms.length} khoảnh khắc ${clock(ms[0].start)}–${clock(last.end)}` : `${clock(ms[0].start)}–${clock(ms[0].end)}`,
        short: full ? 'toàn phim' : ms.length > 1 ? `${ms.length} khoảnh khắc` : `${R.mmss(ms[0].start)}–${R.mmss(ms[0].end)}`});
    }
    return covers;
  }
  /* The covers over the image at time t (±COVER.eps): one of the cover's moments holds t. An image without a time (a
   * text preview) gets the covers one of whose moments holds the whole card (each moment of a discrete card). */
  function coversAt(covers, x, t) {
    const at = imageTime(t), spans = Number.isFinite(at) ? [{start: at, end: at}] : momentsOrSpan(x);
    return covers.filter(c => spans.every(s => c.moments.some(m => m.start <= s.start + COVER.eps && m.end >= s.end - COVER.eps)));
  }
  /* The label of a cover on the image: full, shorter, shortest (review-cards.js takes the first that has room). */
  function coverWords(c) {
    return c.state === 'applied' ? [`đã làm mờ ở thẻ ${c.kind} (${c.short})`, `đã làm mờ · ${c.kind}`, 'đã làm mờ']
      : [`đề xuất làm mờ ở thẻ ${c.kind}, chưa duyệt`, `đề xuất · ${c.kind}, chưa duyệt`, 'đề xuất, chưa duyệt'];
  }
  /* The line under the image (plain text): what the drawing at time t shows and what it leaves out. */
  function coverLegend(queue, x, t) {
    const covers = blurCovers(queue, x), shown = coversAt(covers, x, t), at = imageTime(t), known = Number.isFinite(at);
    const applied = shown.filter(c => c.state === 'applied').length, pending = shown.length - applied, rest = covers.length - shown.length, parts = [];
    if (applied) parts.push(`Vùng mờ viền xanh: ${applied} vùng thẻ khác đã làm mờ ${known ? `lúc ${mmssTenth(at)}` : 'suốt đoạn này'} — bản xuất cũng làm mờ.`);
    if (pending) parts.push(`Viền tím chấm: ${pending} vùng thẻ khác đề xuất làm mờ, chưa duyệt.`);
    if (rest) parts.push(`Còn ${rest} vùng của thẻ khác ${known ? 'làm mờ lúc khác trong đoạn này' : 'chỉ trùng một phần đoạn này'}, không vẽ trên ảnh này (xem Chi tiết kỹ thuật).`);
    return parts.join(' ');
  }
  /* "Chi tiết kỹ thuật", every card: which region other cards blur (or only suggest), where, for how long and when it
   * meets this card; whole-frame BLURs over the same time are counted (the classic sentence). */
  function coverageNote(queue, x) {
    const covers = blurCovers(queue, x), own = momentsOrSpan(x), from = Number(x.start_seconds), to = Number(x.end_seconds);
    const whole = (queue?.items || []).filter(o => o.id !== x.id && o.decision === 'BLUR' && o.decision_region_source_pixels === 'FULL_FRAME' && Number(o.start_seconds) < to && Number(o.end_seconds) > from);
    if (!covers.length && !whole.length) return '';
    const mine = R.regionOwner(queue, x), red = mine && mine.id === x.id ? x.suggested_region_source_pixels || x.decision_region_source_pixels : null;
    const during = c => {
      if (coversAt([c], x, null).length) return 'suốt đoạn của thẻ này';
      const meet = c.moments.flatMap(m => own.map(n => [Math.max(m.start, n.start), Math.min(m.end, n.end)])).filter(([a, b]) => b > a).map(([a, b]) => `${clock(a)}–${clock(b)}`);
      return `trùng thẻ này ${meet.slice(0, 3).join('; ')}${meet.length > 3 ? '; …' : ''}`;
    };
    const line = c => `${c.kind}${c.reading ? ` “${esc(c.reading)}”` : ''} (x=${c.region.x}, y=${c.region.y}, rộng=${c.region.width}, cao=${c.region.height})${red && regionOverlap(c.region, red) >= .6 ? ', cùng vùng khung đỏ của thẻ này' : ''} — ${c.state === 'applied' ? 'đã làm mờ' : 'đề xuất làm mờ'} ở thẻ riêng, ${c.scope}, ${during(c)}`;
    const list = cs => cs.slice(0, COVER.list).map(line).join('; ') + (cs.length > COVER.list ? ` và ${cs.length - COVER.list} vùng khác` : '');
    const applied = covers.filter(c => c.state === 'applied'), pending = covers.filter(c => c.state === 'pending'), parts = [];
    if (applied.length) parts.push(`Vùng thẻ khác đã làm mờ (bản xuất làm mờ đúng vùng và lúc này; trên ảnh: viền xanh nét đứt): ${list(applied)}.`);
    if (pending.length) parts.push(`Vùng thẻ khác chỉ đề xuất làm mờ, chưa duyệt (trên ảnh: viền tím chấm): ${list(pending)}.`);
    if (covers.length && !red) parts.push(`Thẻ này không có vùng riêng — quyết định bên dưới áp dụng cho cả đoạn ${R.span(x)}.`);
    if (whole.length) parts.push(`${whole.length} đoạn trùng thời gian đã được duyệt làm mờ toàn cảnh.`);
    return `<div class="coverage">${parts.join(' ')}</div>`;
  }
  /* The cover's region in the loaded image's pixels (the source frame may be letterboxed in it, as for the magnifier),
   * cut at the image edges, and the gw × gh cells it is shrunk to (a third of its smaller side, at least 1/160 of the
   * image width) before review-media.js stretches it back: the blur. null when nothing of it is in the image. */
  function coverCrop(region, sourceSize, imageW, imageH) {
    const sw = Number(sourceSize && sourceSize[0]), sh = Number(sourceSize && sourceSize[1]);
    if (!(sw > 0 && sh > 0 && imageW > 0 && imageH > 0)) return null;
    const scale = Math.min(imageW / sw, imageH / sh), ox = (imageW - sw * scale) / 2, oy = (imageH - sh * scale) / 2;
    const x = Math.max(0, Number(region.x) * scale + ox), y = Math.max(0, Number(region.y) * scale + oy);
    const right = Math.min(imageW, (Number(region.x) + Number(region.width)) * scale + ox), bottom = Math.min(imageH, (Number(region.y) + Number(region.height)) * scale + oy);
    if (!(right > x && bottom > y)) return null;
    const w = right - x, h = bottom - y, cell = Math.max(Math.min(w, h) / COVER.cells, imageW * COVER.cellMin);
    return {sx: x, sy: y, sw: w, sh: h, gw: Math.max(1, Math.round(w / cell)), gh: Math.max(1, Math.round(h / cell))};
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
  /* The classic techDetails body; controls: the region buttons of a safety card (classic techDetails(x, ev, true)). */
  function techBody(queue, x, ev, controls) {
    const owner = R.regionOwner(queue, x), r = owner && (owner.suggested_region_source_pixels || owner.decision_region_source_pixels);
    const score = x.max_score == null ? '—' : Number(x.max_score).toFixed(3);
    const parts = [`<div class="meta">${esc(x.review_kind || x.category)} · ưu tiên ${esc(x.priority || '—')} · điểm ${score} · ${clock(x.start_seconds)}–${clock(x.end_seconds)} · ${esc(x.id)}</div>`, scopeBlock(queue, x)];
    parts.push(coverageNote(queue, x));
    if (!R.isSafety(x) || (owner && r && r !== 'FULL_FRAME')) parts.push(regionDetailHtml(x, owner, r));
    if (controls) parts.push(controls);
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
  /* Session R (job 134 on a phone): the magnifier under a card's image, always shown for a card with a red region
   * (evidenceView mode 'region'). The region with room around it (text just outside a tight OCR box stays in view)
   * at 3× its size on the card; when 3× is wider than `avail`, the largest zoom down to 2× that fits; a region wider
   * than `avail` even at 2× (a ticker) stays at 3× and the box scrolls sideways (it scrolls anyway, and its small
   * letters read best at 3×); a region taller than MAG.maxHeight at that zoom is fitted to it; one shown at under
   * 1.5× is left out (null). The source
   * frame may be letterboxed in the image. In: the region and frame size in source pixels, the image's natural size,
   * the image box on the card and the width the magnifier may use (CSS px). Out: the crop in image pixels, its CSS
   * size and where the region falls in it (CSS px). */
  /* Room around the region: 12 % of its size, at least its smaller side across and 0.6 of it up and down (a text line
   * keeps a line height of context; that side counts up to sideMax of the image), never under padMin image pixels.
   * A region already filling `large` of the card image both ways needs no magnifier. */
  const MAG = {zoom: 3, min: 2, skip: 1.5, maxHeight: 300, pad: .12, padAcross: 1, padUpDown: .6, sideMax: .15, padMin: 6, large: .5};
  function magnifier(region, sourceSize, imageW, imageH, boxW, boxH, avail) {
    const sw = Number(sourceSize && sourceSize[0]), sh = Number(sourceSize && sourceSize[1]);
    if (!(sw > 0 && sh > 0 && imageW > 0 && imageH > 0 && boxW > 0 && boxH > 0 && avail > 0)) return null;
    const scale = Math.min(imageW / sw, imageH / sh), offsetX = (imageW - sw * scale) / 2, offsetY = (imageH - sh * scale) / 2;
    const rx = Number(region.x) * scale + offsetX, ry = Number(region.y) * scale + offsetY, rw = Number(region.width) * scale, rh = Number(region.height) * scale;
    if (!(rw > 0 && rh > 0) || (rw >= MAG.large * sw * scale && rh >= MAG.large * sh * scale)) return null;
    const side = Math.min(rw, rh, MAG.sideMax * Math.min(imageW, imageH)), padX = Math.max(rw * MAG.pad, side * MAG.padAcross, MAG.padMin), padY = Math.max(rh * MAG.pad, side * MAG.padUpDown, MAG.padMin);
    const x = Math.max(0, rx - padX), y = Math.max(0, ry - padY), w = Math.min(imageW, rx + rw + padX) - x, h = Math.min(imageH, ry + rh + padY) - y;
    if (!(w > 0 && h > 0)) return null;
    const shown = Math.min(boxW / imageW, boxH / imageH), cardW = w * shown, cardH = h * shown;
    let zoom = MAG.zoom;
    if (cardW * zoom > avail && cardW * MAG.min <= avail) zoom = avail / cardW;
    if (cardH * zoom > MAG.maxHeight) zoom = MAG.maxHeight / cardH;
    if (zoom < MAG.skip) return null;
    const cssW = cardW * zoom, cssH = cardH * zoom, k = cssW / w;
    return {sx: x, sy: y, sw: w, sh: h, cssW, cssH, zoom, scroll: cssW > avail + .5, red: {x: (rx - x) * k, y: (ry - y) * k, w: rw * k, h: rh * k}};
  }

  /* Session R: "Máy đọc: …" on a card. The OCR readings (text cards: every label; logo cards: the readings among
   * their labels, as readingLabel) grouped when one is nearly inside another (character pairs; short ones as
   * substrings), the group read in most forms first, then the longer; each group shows its longest reading, cut at
   * `chars`. Out: {lines: [{text, forms}], more: groups not shown} or null (safety cards, no reading). */
  const READ = {lines: 3, chars: 70, overlap: .6, tiny: 3, shortPairs: 5, long: 20};
  /* Labels review_workflow.py writes around a reading ("OCR nội dung phim: X", "Có khả năng là tiêu đề phim: X")
   * give X; a platform card's name ("Logo nền tảng iQIYI", platform_cards.py) is not a reading. */
  const READ_PREFIX = /^(?:OCR nội dung phim|Có khả năng là tiêu đề phim):\s*/, NOT_READ = /^Logo nền tảng\b/;
  function readings(x, limit, chars) {
    if (!x || R.isSafety(x)) return null;
    limit = limit || READ.lines; chars = chars || READ.chars;
    const clean = (x.labels || []).map(v => String(v ?? '').replace(/<\/?s>/g, '').replace(/\s+/g, ' ').trim()).filter(l => l && !NOT_READ.test(l)).map(l => l.replace(READ_PREFIX, '')).filter(Boolean);
    const texts = [...new Set(clean.filter(l => !SCANNER_LABELS.includes(l) && !VI[l] && (x.category === 'text' || !(l === l.toLowerCase() && /^[\p{L} ]+$/u.test(l)))))];
    const read = texts.map((text, i) => {
      const c = text.toLowerCase().normalize('NFC').replace(/[^\p{L}\p{N}]+/gu, ''), g = new Set();
      for (let k = 0; k + 1 < c.length; k++) g.add(c.slice(k, k + 2));
      return {text, i, c, g};
    }).filter(r => r.c).sort((a, b) => b.c.length - a.c.length || a.i - b.i);
    const overlap = (a, b) => {
      if (Math.min(a.c.length, b.c.length) < READ.tiny) return a.c === b.c ? 1 : 0;
      if (Math.min(a.g.size, b.g.size) < READ.shortPairs) return a.c.includes(b.c) || b.c.includes(a.c) ? 1 : 0;
      let shared = 0;
      for (const pair of b.g) if (a.g.has(pair)) shared++;
      return shared / Math.min(a.g.size, b.g.size);
    };
    const groups = [];
    for (const r of read) { const group = groups.find(g => overlap(g[0], r) >= READ.overlap); if (group) group.push(r); else groups.push([r]); }
    if (!groups.length) return null;
    groups.sort((a, b) => b.length - a.length || b[0].c.length - a[0].c.length || a[0].i - b[0].i);
    // The group read in most forms, then each time the line least like those shown (a line under READ.long letters
    // counts less), so a card mixing a watermark with subtitle lines shows both.
    const shown = [groups.shift()];
    while (shown.length < limit && groups.length) {
      const score = g => (1 - Math.max(...shown.map(s => overlap(s[0], g[0])))) * Math.min(1, g[0].c.length / READ.long);
      let best = 0;
      groups.forEach((g, k) => { if (score(g) > score(groups[best])) best = k; });
      shown.push(groups.splice(best, 1)[0]);
    }
    const cut = text => text.length > chars ? text.slice(0, chars - 1).trimEnd() + '…' : text;
    return {lines: shown.map(g => ({text: cut(g[0].text), forms: g.length})), more: groups.length};
  }

  /* Session R: the text scanner prints "REVIEW <track> | <zone>" on its previews (textscan._annotate); the card
   * says that the zone is a place in the frame, not a verdict. The zone comes from the queue (text_preview_zones,
   * queues built since session R); an older queue gets the general sentence. Plain text (escape it); '' when
   * `path` is not a text preview. */
  const ZONE_NAMES = {subtitle:'vùng phụ đề','top-left':'góc trên bên trái','top-center':'giữa cạnh trên','top-right':'góc trên bên phải','middle-left':'giữa cạnh trái',
    'middle-center':'giữa khung hình','middle-right':'giữa cạnh phải','bottom-left':'góc dưới bên trái','bottom-center':'giữa cạnh dưới','bottom-right':'góc dưới bên phải'};
  const TEXT_PREVIEW = /(?:^|\/)text-previews\/track-0*(\d+)\.(?:jpe?g|png)$/i;
  function zoneNote(x, path) {
    const m = TEXT_PREVIEW.exec(String(path || ''));
    if (!m) return '';
    const zones = x && x.text_preview_zones && typeof x.text_preview_zones === 'object' ? x.text_preview_zones : {};
    const zone = Object.prototype.hasOwnProperty.call(zones, path) ? String(zones[path] || '') : '', track = Number(m[1]);
    if (!zone) return `Nhãn “REVIEW ${track} | …” trên ảnh do máy quét chữ in: phần sau dấu | chỉ là tên vùng trong khung (subtitle = vùng phụ đề, top-right = góc trên bên phải…), không phải kết luận.`;
    const name = Object.prototype.hasOwnProperty.call(ZONE_NAMES, zone) ? ZONE_NAMES[zone] : '';
    return `Nhãn “REVIEW ${track} | ${zone}” trên ảnh do máy quét chữ in: “${zone}” chỉ là tên vùng${name ? ` (${name})` : ' trong khung'}${zone === 'subtitle' ? ', không có nghĩa đây là phụ đề' : ', không phải kết luận'}.`;
  }

  /* R2: notes of the region buttons and of "Đây là logo hãng phim — giữ & nhớ" / "Đây là logo nền tảng — làm mờ & nhớ",
   * word for word from the classic regionControlsHtml, studioHtml and platformHtml (verify-review.cjs compares). */
  const mmssTenth = s => { const v = Math.max(0, Number(s) || 0), m = Math.floor(v / 60); return `${m}:${(v - m * 60).toFixed(1).padStart(4, '0')}`; };
  function regionControls(x, owner, r) {
    if (!owner || !r || r === 'FULL_FRAME') return null;
    const borrowed = owner.id !== x.id;
    return {owner: owner.id, decision: owner.decision || null, title: borrowed ? 'Xử lý riêng vùng logo khoanh đỏ' : 'Phân loại vùng khoanh đỏ',
      note: borrowed ? `Vùng logo áp dụng ${clock(owner.start_seconds)}–${clock(owner.end_seconds)}. Quyết định toàn cảnh bên dưới chỉ áp dụng ${clock(x.start_seconds)}–${clock(x.end_seconds)}; nếu chọn Cắt cả cảnh, đoạn bị cắt không cần làm mờ.`
        : 'Chỉ lựa chọn theo phần nằm trong khung đỏ, không theo logo hoặc chữ ở vị trí khác trong ảnh.'};
  }
  /* S9 (R2-B2): a card that only borrows the red box of another logo card gets this line and "Đi tới thẻ logo"
   * instead of the region buttons (both cards' buttons would change the same owner card). */
  function borrowedRegion(x, owner) {
    if (!owner || owner.id === x.id) return null;
    const applies = `${clock(owner.start_seconds)}–${clock(owner.end_seconds)}`;
    return {owner: owner.id, applies, text: `Khung đỏ là vùng logo của thẻ “${R.catName(owner)}” (${applies}) — đang: ${owner.decision ? R.decisionLabel(owner) : 'Chưa duyệt'}`};
  }
  function studioCompareLine(x) {
    const c = x.studio_logo_compared;
    if (!c || x.decision || x.studio_logo_match) return '';
    const pct = Math.floor(100 * Number(c.best_similarity || 0)), need = Math.round(100 * Number(c.minimum_similarity ?? .95)), cells = c.best_cell_difference, limit = Number(c.maximum_cell_difference ?? 20);
    const grid = pct >= need && cells != null && Number(cells) > limit ? ` nhưng lệch màu ${Number(cells)} (cần ≤ ${limit})` : '';
    return `<small class="studio-note">Đã so với ${Number(c.records) || 0} logo hãng phim bạn đã nhớ: giống nhất ${pct}% (cần ≥ ${need}%)${grid} — chưa khớp${Number(c.masked_regions) > 0 ? ' (đã bỏ qua vùng watermark đã làm mờ)' : ''}</small>`;
  }
  function studioTextsNote(x) {
    const texts = x.studio_logo_memory?.remembered ? [] : (x.suggestion_withheld?.window_texts || []);
    return texts.length ? ` (sẽ nhớ cả chữ: ${esc(texts.slice(0, 6).join(', '))}${texts.length > 6 ? ', …' : ''} — nếu trong đó có tên web/thương hiệu lạ, đừng bấm nút này mà hãy Cắt)` : '';
  }
  function studioFramesNote(x) {
    const m = R.studioRemembered(x);
    if (m) {
      if (m.frames == null) return '';
      if (m.frames_missing) return ' Không còn ảnh khung hình đã nhớ (state/studio-logo-frames) nên không cập nhật được vùng watermark — logo này tạm thời không khớp thẻ nào cho tới khi bạn nhớ lại nó.';
      if (m.frames_source === 'source_video') return ` Đã nhớ ${Number(m.frames)} khung trong đoạn ${R.span(x)}.`;
      if (m.frames_source) return ` Chỉ nhớ ${Number(m.frames)} ảnh xem trước (không đọc được video gốc), không phải cả đoạn ${R.span(x)} — tập khác có thể không khớp.`;
      return ` Chỉ nhớ ${Number(m.frames)} ảnh xem trước của thẻ này, không phải cả đoạn ${R.span(x)}.`;
    }
    const shots = (x.preview_images || []).slice(0, 8), times = shots.map(R.thumbTime).filter(t => t != null).map(mmssTenth);
    return ` Sẽ nhớ mọi khung hình trong đoạn ${R.span(x)} (giải mã lại từ video gốc đúng như lúc quét, tối đa 250 khung); nếu không đọc được video gốc thì chỉ nhớ ${shots.length} ảnh xem trước${times.length ? ` (lúc ${times.join(', ')})` : ''}.`;
  }
  function studioMaskNote(queue, x) {
    const m = R.studioRemembered(x);
    if (m && m.frames == null) return '';
    if (m && m.frames_source) {
      const parts = [];
      if (m.mask_refused) parts.push('Vùng watermark đã làm mờ quá lớn (trên 20% khung hình) nên không bỏ qua — logo được nhớ nguyên ảnh.');
      else if ((m.ignored_regions || []).length) parts.push(`Đang bỏ qua ${m.ignored_regions.length} vùng watermark đã làm mờ.`);
      if (m.mask_updated_at) parts.push('Đã cập nhật logo hãng phim đã nhớ theo vùng watermark bạn vừa chọn.');
      return parts.length ? `<small class="studio-note">${parts.join(' ')}</small>` : '';
    }
    const overlays = (queue?.items || []).filter(o => o.id !== x.id && o.candidate_type === 'persistent_overlay' && Number(o.start_seconds) < Number(x.end_seconds) && Number(o.end_seconds) > Number(x.start_seconds));
    const blurred = overlays.filter(o => o.decision === 'BLUR' && o.decision_region_source_pixels !== 'FULL_FRAME' && (o.category === 'text' || o.category === 'visual_logo'));
    const pending = overlays.length > blurred.length || (x.evidence_regions || []).some(b => b && b.covered_by && !blurred.some(o => o.id === b.covered_by));
    if (m) return pending || blurred.length ? '<small class="studio-note">Ảnh đã nhớ đang có watermark/lớp phủ — tập không có lớp phủ này sẽ không khớp.</small>' : '';
    const notes = [];
    if (blurred.length) notes.push(`<small class="studio-note">Sẽ bỏ qua ${blurred.length} vùng watermark bạn đã chọn làm mờ (${blurred.map(o => esc(readingLabel(o, 'watermark'))).join(', ')}) khi so khớp — tập không có watermark hoặc có watermark ở đúng chỗ đó vẫn khớp; chữ, website, banner hay lớp phủ ở chỗ khác vẫn giữ thẻ ở danh sách chính.</small>`);
    if (pending) notes.push('<small class="studio-note">Ảnh đang có watermark/lớp phủ chưa được chọn Làm mờ. Nếu bạn chọn Làm mờ thẻ watermark đó (trước hay sau khi bấm nút này), BiliFlow sẽ tự bỏ qua vùng đó trong logo đã nhớ; nếu không, tập không có lớp phủ này sẽ không khớp.</small>');
    return notes.join('');
  }
  /* The <small> under the studio button (classic studioHtml). */
  function studioNote(x) {
    return `Giữ nguyên đoạn này và nhớ hình logo cùng chữ trên đó${studioTextsNote(x)}. Lần quét sau, thẻ có ảnh trùng khớp từ 95% với một khung bất kỳ của logo này sẽ nằm ở Ứng viên phụ — trừ khi có chữ lạ, website hay lớp phủ (banner) nằm ngoài vùng watermark đã làm mờ; khi đó thẻ vẫn ở danh sách chính.${studioFramesNote(x)}`;
  }
  function platformNote(x, m) {
    if (m) {
      if (m.logo_frames == null) return '';
      const name = m.platform && m.platform.key !== 'unknown' && m.platform.name ? ` ${esc(m.platform.name)}` : '';
      return `Đã nhớ logo nền tảng${name}: ${Number(m.logo_frames)} khung có logo trên nền tối. Lần quét sau, đầu hoặc cuối tập khác có hình trùng khớp sẽ thành thẻ Làm mờ vùng logo — vẫn chờ bạn duyệt.`;
    }
    const where = x.suggested_region_source_pixels ? 'vùng khoanh đỏ' : 'vùng logo BiliFlow tìm thấy trên nền tối trong đoạn này (không làm mờ cả khung; khoảng thời gian thu lại đúng lúc logo hiện)';
    return `Làm mờ ${where} và nhớ hình logo. Lần quét sau, logo nền tảng trùng khớp ở đầu hoặc cuối tập khác sẽ thành thẻ Làm mờ — vẫn chờ bạn duyệt. Dùng cho logo nền tảng phát hành (iQIYI, WeTV…), không dùng cho logo hãng phim hay giấy phép.`;
  }

  return {esc, clock, viText, readingLabel, memoryMatch, memoryBrandName, boxesFromMemory, trackCoversFullVideo, regionOverlap, regionName, decisionScope, scopeBlock,
    blurCovers, coversAt, coverWords, coverLegend, coverageNote, coverCrop, regionDetailHtml, labelsReasonsHtml, visualAiHtml, evidenceHtml, aiModelLine, techBody, evidenceView, magnifier, readings, zoneNote, ZONE_NAMES,
    regionControls, borrowedRegion, studioCompareLine, studioTextsNote, studioFramesNote, studioMaskNote, studioNote, platformNote};
});
