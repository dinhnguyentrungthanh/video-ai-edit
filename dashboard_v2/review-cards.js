/* Review dialog markup (R0, read-only): header, filter chips, scene cards and the in-place patch used
 * by polling. Every queue string goes through esc(); ids only reach data-* attributes escaped.
 * Card design follows the prototype dialog (article.scene, .scene-content); decisions come in R2.
 */
(function (root) {
  'use strict';
  const R = root.BFReviewCore;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const GROUPS = {adult:'18+',gore:'Máu me',violence:'Bạo lực',visual_logo:'Logo / quảng cáo',text:'Chữ'};
  const pct = v => (Math.round(v * 10000) / 100) + '%';
  const BATCH = 24;

  function groupOf(x) { return GROUPS[x.category] || String(x.category || 'Khác'); }
  function regionLine(queue, x) {
    if (x.advisory) return R.TEXT.advisory;
    const box = R.regionBox(queue, x);
    if (box) return box.owner === x.id ? 'Vùng khoanh đỏ' : 'Vùng của thẻ logo đã làm mờ';
    if (R.isScene(x)) return R.momentsOf(x).length + ' khoảnh khắc';
    return R.isSafety(x) ? 'Cả khung hình' : 'Chưa khoanh vùng (cả khung)';
  }
  function suggestion(x) {
    if (x.ai_visual_audit && x.ai_visual_audit.suggested_decision) {
      return 'Visual AI ' + Math.round(100 * Number(x.ai_visual_audit.confidence || 0)) + '%: ' + R.actionName(x, x.ai_visual_audit.suggested_decision);
    }
    return x.suggested_decision ? 'Đề xuất: ' + R.actionName(x, x.suggested_decision) : '';
  }
  function pill(x) { const [label, cls] = R.statusOf(x); return '<span class="rv-status ' + cls + '">' + esc(label) + '</span>'; }

  function card(ctx, x) {
    const box = R.regionBox(ctx.queue, x), src = (x.preview_images || [])[0], tip = suggestion(x);
    return '<article class="scene rv-card' + (x.id === ctx.focusId ? ' on' : '') + (x.decision ? ' decided' : '') + '" data-item="' + esc(x.id) + '">' +
      '<button class="rv-art" type="button" data-review="select" data-item="' + esc(x.id) + '" style="aspect-ratio:' + esc(R.frameAspect(x)) + '" aria-label="' + esc('Chọn ' + R.sceneName(x) + ' ' + R.span(x)) + '">' +
        (src ? '<img alt="" decoding="async" data-src="' + esc(ctx.mediaUrl(src)) + '">' : '') +
        (box ? '<span class="rv-region" style="left:' + pct(box.left) + ';top:' + pct(box.top) + ';width:' + pct(box.width) + ';height:' + pct(box.height) + '"></span>' : '') +
        '<span class="rv-art-note">' + (src ? 'Không tải được ảnh' : 'Không có ảnh xem trước cho mục này.') + '</span>' +
      '</button>' +
      '<div class="scene-content"><div class="rv-card-top"><h3>' + esc(R.sceneName(x)) + '</h3>' + pill(x) + '</div>' +
        '<small class="rv-meta">' + esc(R.span(x) + ' · ' + groupOf(x) + ' · ' + regionLine(ctx.queue, x)) + '</small>' +
        (tip ? '<p class="rv-hint">' + esc(tip) + '</p>' : '') +
      '</div></article>';
  }

  function chips(ctx) {
    const extra = R.MORE_FILTERS.some(f => f[0] === ctx.filter);
    return '<div class="filter-tabs rv-chips" role="group" aria-label="Bộ lọc cảnh">' +
      R.FILTERS.map(([id]) => '<button class="filter-tab' + (ctx.filter === id ? ' active' : '') + '" type="button" data-review="filter" data-filter="' + id + '" aria-pressed="' + (ctx.filter === id) + '">' + esc(R.filterLabel(id, ctx.queue)) + '</button>').join('') +
      '<select class="rv-more' + (extra ? ' active' : '') + '" data-review="more" aria-label="Lọc khác"><option value="">Lọc khác…</option>' +
      R.MORE_FILTERS.map(([id]) => '<option value="' + id + '"' + (ctx.filter === id ? ' selected' : '') + '>' + esc(R.filterLabel(id, ctx.queue)) + '</option>').join('') + '</select></div>';
  }

  /* The header part that changes with the queue version (patched in place by polling). */
  function progressHtml(ctx) {
    const p = R.progress(ctx.queue), width = p.total ? Math.round(p.done / p.total * 100) : 100;
    return '<div class="rv-progress"><span class="rv-progress-text">' + esc(R.progressText(ctx.queue)) + '</span>' +
      '<div class="meter" role="progressbar" aria-label="Cảnh đã có quyết định cuối" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + width + '"><i style="width:' + width + '%"></i></div>' +
      '<button class="small" type="button" disabled title="Xuất video trong hộp này có ở bản sau. Bây giờ dùng nút “Xuất video” ở Dashboard.">Xuất video</button></div>' +
      '<p class="rv-next muted">' + esc(R.nextNote(ctx.queue)) + '</p>';
  }

  function header(ctx) {
    const scope = R.scopeWarning(ctx.queue);
    return '<div class="rv-title-row"><div class="rv-title"><h2 id="review-title">' + esc('Duyệt cảnh · #' + ctx.job.id + ' · ' + ctx.job.name) + '</h2>' +
        (ctx.oldUrl ? '<a class="rv-old" href="' + esc(ctx.oldUrl) + '">Mở trang duyệt cũ</a>' : '<span class="rv-old muted">Trang duyệt cũ chỉ có khi chạy cùng Control Center</span>') + '</div>' +
        '<button class="icon-button" type="button" data-review="close" aria-label="Đóng hộp duyệt">×</button></div>' +
      '<div class="rv-progress-box">' + progressHtml(ctx) + '</div>' +
      chips(ctx) +
      (scope ? '<div class="notice rv-scope" role="note">' + esc(scope) + '</div>' : '') +
      '<div class="rv-tools"><button class="small secondary" type="button" disabled>Giữ tất cả</button><button class="small secondary" type="button" disabled>Dùng đề xuất</button>' +
        '<button class="small secondary" type="button" disabled>↶ Hoàn tác</button><span class="rv-tools-note">Bản thử: chỉ xem. Duyệt bằng nút “Duyệt” hoặc trang duyệt cũ.</span></div>' +
      (ctx.lock.readonly ? '<div class="notice rv-lock" role="status">Chỉ xem · ' + esc(ctx.lock.reason) + '</div>' : '');
  }

  function empty(ctx) {
    return '<div class="empty rv-empty"><strong>' + (ctx.filter === 'pending' ? 'Không còn cảnh chưa duyệt' : 'Không có cảnh trong bộ lọc này') + '</strong>' +
      (ctx.filter === 'pending' ? 'Chọn “Tất cả” để xem lại các cảnh đã duyệt.' : 'Thử bộ lọc khác.') + '</div>';
  }

  /* Polling: same list → only statuses, classes and header text change; the card nodes stay. */
  function patchCards(container, ctx) {
    let patched = 0;
    for (const el of container.querySelectorAll('article.rv-card')) {
      const x = ctx.map.get(el.dataset.item);
      if (!x) continue;
      const html = pill(x), status = el.querySelector('.rv-status');
      if (status && status.outerHTML !== html) { status.outerHTML = html; patched++; }
      el.classList.toggle('decided', !!x.decision);
      el.classList.toggle('on', x.id === ctx.focusId);
    }
    return patched;
  }

  root.BFReviewCards = {BATCH, esc, card, header, progressHtml, chips, empty, patchCards};
})(window);
