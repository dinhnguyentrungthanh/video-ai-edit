/* Review dialog markup: header, filter chips, scene cards with their media (▶, timeline, strip of ≤8 frames,
 * moment chips, AI boxes, zoom, "Chi tiết kỹ thuật"), R2 decision buttons (Giữ / Làm mờ / Cắt / Cần xem thêm /
 * Xóa quyết định, region buttons, studio / platform memory, the blur or cut illustration on the image) and the
 * in-place patch used after a decision and by polling. Every queue string goes through esc(); ids only reach
 * data-* attributes escaped. Card design follows the prototype dialog (article.scene, .scene-content, .selected).
 */
(function (root) {
  'use strict';
  const R = root.BFReviewCore, D = root.BFReviewDetail;
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
  const mmssTenth = s => { const v = Math.max(0, Number(s) || 0), m = Math.floor(v / 60); return `${m}:${(v - m * 60).toFixed(1).padStart(4, '0')}`; };

  /* Media of a card. m: {ev (undefined = loading, null = none), hasKey, playable, reason, frameUrl(t), mediaUrl(path)}. */
  function mainImage(m, x) {
    if (m.ev && m.hasKey && m.ev.strongest && R.stripFrames(x, m.ev, true).some(f => f.remote)) return m.frameUrl(m.ev.strongest.t);
    const src = (x.preview_images || [])[0];
    return src ? m.mediaUrl(src) : '';
  }
  function strip(m, x) {
    if (m.ev === undefined) return '<span class="rv-thumb ghost"></span>'.repeat(8);
    const frames = R.peakFirst(R.stripFrames(x, m.ev, m.hasKey)), label = (x.end_seconds - x.start_seconds) < 30 ? mmssTenth : R.mmss;
    const ms = R.isScene(x) ? R.momentsOf(x) : null;
    return frames.map((f, i) => {
      const k = ms && f.t != null ? R.momentIndex(f.t, ms) : -1, url = f.remote ? m.frameUrl(f.t) : m.mediaUrl(f.path);
      return '<button class="rv-thumb' + (f.kind === 'strongest' ? ' peak' : f.kind === 'seed' ? ' hit' : '') + '" type="button" data-review="thumb" data-i="' + i + '" data-t="' + (f.t == null ? '' : Number(f.t)) + '"' +
        (f.remote ? ' data-remote="1"' : '') + ' aria-label="' + esc('Khung ' + (f.t == null ? i + 1 : label(f.t))) + '"><img alt="" decoding="async" data-src="' + esc(url) + '">' +
        (f.kind === 'strongest' ? '<b>Rõ nhất</b>' : '') + (f.t == null ? '' : '<span>' + esc(label(f.t)) + '</span>') + (k >= 0 ? '<i>' + (k + 1) + '</i>' : '') + '</button>';
    }).join('');
  }
  function bar(m, x) {
    const scene = R.isScene(x), n = scene ? R.momentsOf(x).length : 0;
    const play = !R.hasPlayer(x) ? '' : m.playable
      ? (scene ? '<button class="small secondary" type="button" data-review="seq" title="Phát từng khoảnh khắc theo thứ tự, bỏ qua khoảng trống giữa chúng">▶ Phát lần lượt ' + n + ' khoảnh khắc</button>'
        : '<button class="small secondary" type="button" data-review="play">▶ Phát đoạn này</button>')
      : '<small class="rv-reason">' + esc(m.reason) + '</small>';
    return play + '<span class="rv-time"></span><button class="icon-button rv-zoom-btn" type="button" data-review="zoom" aria-pressed="false" aria-label="Phóng to thẻ" title="Phóng to thẻ (Esc để thu nhỏ)">⤢</button>';
  }
  function moments(x) {
    if (!R.isScene(x)) return '';
    return '<div class="rv-moments" role="group" aria-label="Các khoảnh khắc">' + R.momentsOf(x).map((mo, i) => '<button class="rv-mchip" type="button" data-review="moment" data-i="' + i + '" title="' + esc('Phát riêng khoảnh khắc ' + (i + 1)) + '"><b>' + (i + 1) + '</b>' + esc(R.mmss(mo.start) + '–' + R.mmss(mo.end)) + '<span>▶</span></button>').join('') + '</div>';
  }
  /* Yellow AI boxes (reference only) and red approved boxes, shown while the card is zoomed. */
  function aiBoxes(ctx, x) {
    const view = D.evidenceView(ctx.queue, x);
    if (view.mode !== 'boxes') return '';
    const sw = Number(view.size[0]), sh = Number(view.size[1]), tagged = new Set(view.marks.filter(b => b.a).map(b => b.o));
    return '<span class="rv-boxes">' + view.marks.map(b => {
      const tag = b.a ? 'đã duyệt làm mờ ở thẻ riêng' : b.c && !tagged.has(b.o) ? (tagged.add(b.o), 'watermark — đã có thẻ riêng') : '';
      return '<span class="rv-aibox' + (b.a ? ' approved' : '') + '" style="left:' + pct(b.x / sw) + ';top:' + pct(b.y / sh) + ';width:' + pct(b.w / sw) + ';height:' + pct(b.h / sh) + '">' + (tag ? '<em>' + esc(tag) + '</em>' : '') + '</span>';
    }).join('') + '</span>';
  }
  function zoomExtra(ctx, x) {
    const view = D.evidenceView(ctx.queue, x);
    return (view.legend ? '<p class="rv-legend">' + esc(view.legend) + '</p>' : '') +
      (view.mode === 'region' ? '<figure class="rv-crop-box"><canvas class="rv-crop" width="360" height="120" aria-label="Ảnh cắt quanh vùng khoanh đỏ"></canvas><figcaption>Vùng khoanh đỏ, phóng to</figcaption></figure>' : '');
  }
  function tech(ctx, x, ev) { return '<details class="rv-tech"' + (ctx.techOpen && ctx.techOpen.has(x.id) ? ' open' : '') + '><summary>Chi tiết kỹ thuật</summary><div class="tech-body">' + techHtml(ctx, x, ev) + '</div></details>'; }

  function pill(x) { const [label, cls] = R.statusOf(x); return '<span class="rv-status ' + cls + '">' + esc(label) + '</span>'; }

  /* R2. Illustration only (not the export): BLUR on a region blurs the red box, BLUR on the whole frame blurs the
   * image, CUT darkens it with "Đã chọn cắt". */
  function regionBlurred(ctx, x) {
    const owner = R.regionOwner(ctx.queue, x);
    return !!owner && owner.decision === 'BLUR' && owner.decision_region_source_pixels !== 'FULL_FRAME';
  }
  const fullBlur = x => x.decision === 'BLUR' && x.decision_region_source_pixels === 'FULL_FRAME';
  function regionBlock(ctx, x) {
    const owner = R.regionOwner(ctx.queue, x), r = owner && (owner.suggested_region_source_pixels || owner.decision_region_source_pixels), c = D.regionControls(x, owner, r);
    if (!c) return '';
    const b = (d, label) => '<button class="small secondary' + (c.decision === d ? ' selected' : '') + '" type="button" data-review="region" data-owner="' + esc(c.owner) + '" data-decision="' + d + '" aria-pressed="' + (c.decision === d) + '"' + (ctx.readonly ? ' disabled' : '') + '>' + esc(label) + '</button>';
    return '<div class="rv-region-block"><strong>' + esc(c.title) + '</strong><small>' + esc(c.note) + '</small><div class="scene-actions">' + b('KEEP', R.TEXT.regionKeep) + b('BLUR', R.TEXT.regionBlur) + '</div></div>';
  }
  function memoryBlock(ctx, x) {
    const off = ctx.readonly ? ' disabled' : '';
    let html = '';
    if (R.studioEligible(x)) {
      const done = !!R.studioRemembered(x);
      html += '<div class="rv-memory">' + D.studioCompareLine(x) + '<button class="small secondary' + (done ? ' selected' : '') + '" type="button" data-review="studio" aria-pressed="' + done + '"' + off + '>' + esc(done ? R.TEXT.studioDone : R.TEXT.studio) + '</button><small>' + D.studioNote(x) + '</small>' + D.studioMaskNote(ctx.queue, x) + '</div>';
    }
    if (R.platformEligible(x)) {
      const m = R.platformRemembered(x), note = D.platformNote(x, m);
      html += '<div class="rv-memory"><button class="small secondary' + (m ? ' selected' : '') + '" type="button" data-review="platform" aria-pressed="' + !!m + '"' + off + '>' + esc(m ? R.TEXT.platformDone : R.TEXT.platform) + '</button>' + (note ? '<small>' + note + '</small>' : '') + '</div>';
    }
    return html;
  }
  /* The buttons of one card; ctx.readonly (lock or offline) disables every one of them. */
  function actions(ctx, x) {
    const chosen = R.chosenButton(x), off = ctx.readonly ? ' disabled' : '', ad = !R.isSafety(x), region = ad ? regionBlock(ctx, x) : '';
    const b = (d, label, key) => '<button class="rv-decide' + (chosen === d ? ' selected' : '') + '" type="button" data-review="decide" data-decision="' + d + '" aria-pressed="' + (chosen === d) + '" title="' + esc(label + ' (phím ' + key + ')') + '"' + off + '>' + esc(label) + '</button>';
    return region + (region ? '<p class="rv-decide-head">' + esc('Quyết định cho toàn cảnh ' + R.catName(x) + ' · ' + R.span(x)) + '</p>' : '') +
      '<div class="scene-actions" role="group" aria-label="Quyết định">' + b('KEEP', 'Giữ', 1) + b('BLUR', R.blurLabel(x), 2) + b('CUT', 'Cắt', 3) + b('NEEDS_MORE_CONTEXT', 'Cần xem thêm', 4) +
      '<button class="rv-clear" type="button" data-review="clear"' + (ctx.readonly || !x.decision ? ' disabled' : '') + '>Xóa quyết định</button></div>' +
      (x.decision ? '<p class="rv-chosen">Đã chọn: <b>' + esc(R.decisionLabel(x)) + '</b></p>' : '') + memoryBlock(ctx, x);
  }
  function techHtml(ctx, x, ev) { return D.techBody(ctx.queue, x, ev || null, R.isSafety(x) ? regionBlock(ctx, x) : ''); }

  function card(ctx, x) {
    const box = R.regionBox(ctx.queue, x), m = ctx.media(x), src = mainImage(m, x), tip = suggestion(x), zoomed = ctx.zoomId === x.id;
    return '<article class="scene rv-card' + (x.id === ctx.focusId ? ' on' : '') + (x.decision ? ' decided' : '') + (zoomed ? ' zoom' : '') + (x.decision === 'CUT' ? ' d-cut' : '') + (fullBlur(x) ? ' d-blur-full' : '') + '" data-item="' + esc(x.id) + '">' +
      '<div class="rv-art" data-review="select" style="aspect-ratio:' + esc(R.frameAspect(x)) + '">' +
        (src ? '<img alt="" decoding="async" data-src="' + esc(src) + '">' : '') +
        (box ? '<span class="rv-region' + (regionBlurred(ctx, x) ? ' blurred' : '') + '" style="left:' + pct(box.left) + ';top:' + pct(box.top) + ';width:' + pct(box.width) + ';height:' + pct(box.height) + '"></span>' : '') +
        aiBoxes(ctx, x) + '<span class="rv-cut-label">Đã chọn cắt</span>' +
        '<span class="rv-art-note">' + (src ? 'Không tải được ảnh' : 'Không có ảnh xem trước cho mục này.') + '</span>' +
      '</div>' +
      '<div class="rv-bar">' + bar(m, x) + '</div>' +
      '<div class="rv-timeline" data-review="seek" title="Bấm để tua">' + R.timelineHtml(x, m.ev || null) + '<div class="head" hidden></div></div>' +
      '<div class="rv-strip">' + strip(m, x) + '</div>' + moments(x) +
      '<p class="rv-note" role="status" hidden></p><div class="rv-zoom-slot">' + (zoomed ? zoomExtra(ctx, x) : '') + '</div>' +
      '<div class="scene-content"><div class="rv-card-top"><h3><button class="rv-name" type="button" data-review="select" aria-label="' + esc('Chọn ' + R.sceneName(x) + ' ' + R.span(x)) + '">' + esc(R.sceneName(x)) + '</button></h3>' + pill(x) + '</div>' +
        '<small class="rv-meta">' + esc(R.span(x) + ' · ' + groupOf(x) + ' · ' + regionLine(ctx.queue, x)) + '</small>' +
        (tip ? '<p class="rv-hint">' + esc(tip) + '</p>' : '') + '<div class="rv-actions">' + actions(ctx, x) + '</div>' + tech(ctx, x, m.ev) +
      '</div></article>';
  }
  /* Evidence arrived (or the video became unavailable): media parts of one card, without touching its
   * image box (which may hold the playing <video>). Returns the new main image URL. */
  function refreshMedia(el, ctx, x) {
    const m = ctx.media(x), head = el.querySelector('.rv-timeline .head');
    el.querySelector('.rv-bar').innerHTML = bar(m, x);
    const timeline = el.querySelector('.rv-timeline');
    timeline.innerHTML = R.timelineHtml(x, m.ev || null);
    if (head) timeline.appendChild(head);
    el.querySelector('.rv-strip').innerHTML = strip(m, x);
    const body = techHtml(ctx, x, m.ev);
    el.querySelector('.tech-body').innerHTML = body; el.querySelector('.tech-body').__html = body;
    el.querySelector('.rv-zoom-slot').innerHTML = el.classList.contains('zoom') ? zoomExtra(ctx, x) : '';
    return mainImage(m, x);
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
      '<div class="rv-tools"><button class="small secondary" type="button" disabled title="Có ở bản sau; bây giờ dùng trang duyệt cũ.">Giữ tất cả</button><button class="small secondary" type="button" disabled title="Có ở bản sau; bây giờ dùng trang duyệt cũ.">Dùng đề xuất</button>' +
        '<button class="small secondary rv-undo" type="button" data-review="undo" disabled title="Chưa có lựa chọn nào trong phiên này để hoàn tác">↶ Hoàn tác</button><span class="rv-save" role="status" aria-live="polite"></span></div>' +
      (ctx.lock.readonly ? '<div class="notice rv-lock" role="status">Chỉ xem · ' + esc(ctx.lock.reason) + '</div>' : '') +
      '<div class="notice rv-offline" role="alert"' + (ctx.offline ? '' : ' hidden') + '>' + esc(R.TEXT.offline) + '</div>';
  }

  function empty(ctx) {
    return '<div class="empty rv-empty"><strong>' + (ctx.filter === 'pending' ? 'Không còn cảnh chưa duyệt' : 'Không có cảnh trong bộ lọc này') + '</strong>' +
      (ctx.filter === 'pending' ? 'Chọn “Tất cả” để xem lại các cảnh đã duyệt.' : 'Thử bộ lọc khác.') + '</div>';
  }

  /* After a decision and on polling (same list): statuses, classes, the illustration, the buttons and the
   * technical text change in place; the card nodes and the image box (maybe the playing <video>) stay. */
  function setHtml(el, html) {
    if (!el || el.__html === html) return false;
    const active = document.activeElement, key = active && el.contains(active) ? [active.dataset.review, active.dataset.decision || '', active.dataset.owner || ''] : null;
    el.innerHTML = html; el.__html = html;
    if (key) {
      const again = [...el.querySelectorAll('[data-review]')].find(b => b.dataset.review === key[0] && (b.dataset.decision || '') === key[1] && (b.dataset.owner || '') === key[2]);
      if (again && !again.disabled) again.focus({preventScroll: true});
    }
    return true;
  }
  function patchCard(el, ctx, x) {
    let patched = 0;
    const html = pill(x), status = el.querySelector('.rv-status');
    if (status && status.outerHTML !== html) { status.outerHTML = html; patched++; }
    el.classList.toggle('decided', !!x.decision);
    el.classList.toggle('on', x.id === ctx.focusId);
    el.classList.toggle('d-cut', x.decision === 'CUT');
    el.classList.toggle('d-blur-full', fullBlur(x));
    const region = el.querySelector('.rv-region');
    if (region) region.classList.toggle('blurred', regionBlurred(ctx, x));
    if (setHtml(el.querySelector('.rv-actions'), actions(ctx, x))) patched++;
    setHtml(el.querySelector('.tech-body'), techHtml(ctx, x, ctx.media(x).ev));
    return patched;
  }
  function patchCards(container, ctx) {
    let patched = 0;
    for (const el of container.querySelectorAll('article.rv-card')) {
      const x = ctx.map.get(el.dataset.item);
      if (x) patched += patchCard(el, ctx, x) ? 1 : 0;
    }
    return patched;
  }

  root.BFReviewCards = {BATCH, esc, card, refreshMedia, zoomExtra, mainImage, header, progressHtml, chips, empty, patchCard, patchCards, actions};
})(window);
