/* DemoStore: in-memory fixture state for the standalone demo (index.html).
 * Same interface as BFAdapter.createLiveStore, so app.js renders either one.
 * No network: every request descriptor is recorded in memory only.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BFDemoStore = api;
})(typeof window === 'undefined' ? this : window, function () {
  'use strict';

  function demoError(status, message, extra) {
    const error = new Error(message);
    error.status = status; error.code = extra && extra.code || null; error.preview = extra && extra.preview || null;
    return error;
  }

  function create(C, Mock) {
    let state = Mock.create(), conflictUsed = false, previewVersion = 1;
    const listeners = new Set();
    const emit = () => listeners.forEach(fn => fn(state));
    const getJob = id => state.jobs.find(j => j.id === Number(id));

    function conflict(message, code) {
      if (state.scenario === 'conflict' && !conflictUsed) { conflictUsed = true; throw demoError(409, message, {code}); }
    }
    function online() { if (state.offline) throw demoError(0, 'Mất kết nối. Chưa gửi thao tác.'); }
    function queueJob(j, kind, preserve) {
      const place = preserve && j.queue_position ? j.queue_position : Math.max(0, ...state.jobs.filter(x => x.state === 'QUEUED').map(x => x.queue_position || 0)) + 1;
      j.state = 'QUEUED'; j.queue_kind = kind; j.queue_position = place; delete j.error; j.render_request = kind === 'export';
    }
    function normalizeQueue() {
      state.jobs.filter(j => j.state === 'QUEUED').sort((a, b) => a.queue_position - b.queue_position || a.id - b.id).forEach((j, i) => j.queue_position = i + 1);
      state.queue.length = state.jobs.filter(j => j.state === 'QUEUED').length;
      if (!state.jobs.some(j => j.id === state.active?.job_id && ['scanning', 'export'].includes(C.tab(j)) && j.state !== 'QUEUED')) state.active = null;
    }
    function changed() { previewVersion++; normalizeQueue(); emit(); }

    /* Review dialog: the adapter's review(jobId) interface on a synthetic in-memory queue.
     * Images are the bundled poster SVGs; there is no video, frame or evidence in the demo.
     * R2 writes (decision / clear) change that queue in memory, one at a time per job, never over the network. */
    const reviews = new Map(), writers = new Map();
    const writer = id => { if (!writers.has(id)) writers.set(id, {chain: Promise.resolve(), pending: 0}); return writers.get(id); };
    function countQueue(q) {
      const counts = {total: q.items.length, pending: 0, decisions: {KEEP: 0, BLUR: 0, CUT: 0, NEEDS_MORE_CONTEXT: 0}};
      for (const x of q.items) { if (counts.decisions[x.decision] != null) counts.decisions[x.decision]++; else counts.pending++; }
      q.counts = counts; q.updated_at = new Date().toISOString();
      q.status = counts.decisions.NEEDS_MORE_CONTEXT ? 'NEEDS_MORE_CONTEXT' : counts.pending ? 'REVIEW_REQUIRED' : 'READY_FOR_EDIT_PLAN';
    }
    /* The record_review_decision / clear_review_decision rules that matter to the dialog (same refusals as 400). */
    function reviewWrite(j, operation, body) {
      online();
      const q = reviews.get(j.id);
      if (!q) throw demoError(404, 'Video chưa có danh sách duyệt (đang quét hoặc quét lại).');
      if (C.inFlight(j) || C.locked(j) || j.state === 'SKIPPED') throw demoError(409, 'Video đang chờ xuất, đang xuất, bị khóa hoặc đã bỏ qua; không sửa quyết định.');
      let item = q.items.find(x => x.id === body.id);
      if (!item && operation === 'decision') {
        const i = q.advisory_items.findIndex(x => x.id === body.id);
        if (i >= 0) { item = q.advisory_items.splice(i, 1)[0]; delete item.advisory; q.items.push(item); }
      }
      if (!item) throw demoError(400, 'Unknown or duplicate review item: ' + body.id);
      delete item.studio_logo_memory; delete item.platform_logo_memory;
      if (operation === 'clear') Object.assign(item, {decision: null, decision_note: null, decision_region_source_pixels: null, decided_at: null});
      else {
        const d = body.decision, studio = body.remember_studio_logo === true, platform = body.remember_platform_logo === true;
        if (studio && d !== 'KEEP') throw demoError(400, 'Chỉ có thể ghi nhớ logo hãng phim khi chọn Giữ nguyên');
        if (platform && d !== 'BLUR') throw demoError(400, 'Chỉ có thể ghi nhớ logo nền tảng khi chọn Làm mờ');
        let region = null;
        if (d === 'BLUR') region = body.full_frame ? 'FULL_FRAME' : item.suggested_region_source_pixels || (platform ? {x: 1600, y: 60, width: 240, height: 90} : null);
        if (d === 'BLUR' && !region) throw demoError(400, 'BLUR requires a region or explicit full-frame approval');
        Object.assign(item, {decision: d, decision_region_source_pixels: region, decided_at: new Date().toISOString(),
          decision_note: body.note ?? (studio ? 'Người duyệt xác nhận đây là logo hãng phim — giữ nguyên và ghi nhớ' : platform ? 'Người duyệt xác nhận đây là logo nền tảng video — làm mờ vùng logo và ghi nhớ' : null)});
        if (studio) item.studio_logo_memory = {remembered: true, frames: 12, frames_source: 'source_video'};
        if (platform) item.platform_logo_memory = {remembered: true, logo_frames: 4, platform: {key: 'demo', name: 'Nền tảng mẫu'}};
      }
      countQueue(q);
      state.requests.push(C.request(operation, j, body));
      // The dashboard row follows the queue once the dialog closes (resume()).
      const c = q.counts;
      j.review_summary = {...(j.review_summary || {}), status: q.status, main_items: c.total, pending: c.pending, decisions: {...c.decisions}};
      if (['WAITING_REVIEW', 'READY_TO_EXPORT'].includes(j.state)) j.state = q.status === 'READY_FOR_EDIT_PLAN' ? 'READY_TO_EXPORT' : 'WAITING_REVIEW';
      return structuredClone(q);
    }
    function review(jobId) {
      const j = getJob(jobId);
      if (!j) throw demoError(404, 'Không tìm thấy video.');
      const asset = p => { const m = /^demo\/(poster-[a-z]+\.svg)$/.exec(String(p)); return m ? 'assets/' + m[1] : ''; };
      return {
        jobId: j.id,
        async queue() {
          online();
          if (!j.active_queue_path) throw demoError(404, 'Video chưa có danh sách duyệt (đang quét hoặc quét lại).');
          if (!reviews.has(j.id)) reviews.set(j.id, Mock.reviewQueue(j));
          return structuredClone(reviews.get(j.id));
        },
        session: async () => ({media_key: null}), // no media key: the demo has no video or frames (report images only)
        mediaKey: () => null,
        resources: async () => ({source_bytes: j.source_size_bytes, report_bytes: 18e6, disk_free_bytes: 312e9, estimated_preview_seconds: 40, estimated_preview_megabytes_range: [180, 260]}),
        exportState: async () => ({status: j.state, output: j.output_path || null, error: j.error || null, render_progress: j.render_progress || null,
          source_cleaned: !!j.source_cleaned, source_archived: !!j.source_archived, source_name: j.name + '.mp4'}),
        evidence: async item => { const q = reviews.get(j.id), x = q && q.items.concat(q.advisory_items).find(i => i.id === item); return x ? Mock.reviewEvidence(x, {video: {available: false, reason: 'source_unknown'}}) : null; },
        probeVideo: async () => 404,
        frameUrl: () => '',
        videoUrl: () => '',
        mediaUrl: asset,
        write(operation, body) {
          if (operation !== 'decision' && operation !== 'clear') return Promise.reject(demoError(400, 'Thao tác ghi không hợp lệ.'));
          const w = writer(j.id);
          w.pending++;
          const run = async () => { try { return {body: reviewWrite(j, operation, body), last: w.pending === 1}; } finally { w.pending--; } };
          const done = w.chain.then(run, run);
          w.chain = done.then(() => {}, () => {});
          return done;
        },
        pendingWrites: () => writer(j.id).pending,
        idle: () => writer(j.id).chain,
      };
    }

    function apply(operation, j, body) {
      if (j) {
        if (operation === 'start' || operation === 'rerun') {
          j.detector_groups = [...body.detectors]; j.ocr_recognition_batch_size = body.ocr_recognition_batch_size; j.fast_scan = body.fast_scan;
          if (operation === 'start') { j.content_style = body.content_style; j.profile = body.profile; }
          else { j.active_revision++; j.active_queue_path = null; j.review_summary = null; }
          j.hidden_at = null; queueJob(j, 'scan', false);
        } else if (operation === 'resume' || operation === 'retry') queueJob(j, j.render_request ? 'export' : 'scan', true);
        else if (operation === 'pause') j.state = 'PAUSED';
        else if (operation === 'stopAfter') j.stop_after_stage = true;
        else if (operation === 'cancel') { j.state = 'CANCELLED'; j.render_request = false; j.queue_position = null; j.hidden_at = null; }
        else if (operation === 'skip') { j.state = 'SKIPPED'; j.cleanup = {eligible: true}; j.archive = {eligible: true}; j.render_request = false; }
        else if (operation === 'unskip') { j.state = 'READY_TO_EXPORT'; j.cleanup = {eligible: false}; j.archive = {eligible: false}; }
        else if (operation === 'hide') j.hidden_at = new Date().toISOString();
        else if (operation === 'unhide') j.hidden_at = null;
        else if (operation === 'finalize') queueJob(j, 'export', false);
        else if (operation === 'audit') j.ai_audit = {state: 'QUEUED', message: body.visual ? 'Visual AI Audit mẫu đã xếp hàng' : 'JSON audit mẫu đã xếp hàng'};
        else if (operation === 'restore') { const skipped = j.state === 'SKIPPED'; j.source_archived = false; j.source_present = true; j.source_archive = {...j.source_archive, state: 'RESTORED'}; j.state = skipped ? 'SKIPPED' : j.review_summary?.status === 'READY_FOR_EDIT_PLAN' ? 'READY_TO_EXPORT' : 'WAITING_REVIEW'; }
        else if (operation === 'recheck') { if (body.kind === 'source_cleanup') j.source_cleanup.verified = true; else j.source_archive.export_verified = true; }
        j.updated_at = new Date().toISOString();
      } else if (operation === 'scheduler') { state.scheduler_paused = body.paused; state.queue.paused = body.paused; }
      else if (operation === 'aiConfig') { state.ai.config = {...body}; state.ai.ready = body.enabled; }
      else if (operation === 'aiCheck') { state.ai.ready = state.ai.config.enabled; state.ai.message = state.ai.ready ? 'Kết nối mẫu sẵn sàng' : 'AI đang tắt'; }
      else if (operation === 'aiLogin') state.ai.message = 'Đăng nhập được mô phỏng; không mở tài khoản thật.';
      else if (operation === 'shutdown') { state.offline = true; state.active = null; }
    }

    function previewFor(kind, ids) {
      const chosen = ids.map(getJob).filter(Boolean).slice(0, 50);
      const eligible = chosen.filter(j => C.eligible(j, kind)), ineligible = chosen.filter(j => !C.eligible(j, kind));
      const used = state.scenario === 'bin_full' ? 49.99e9 : 7.2e9, max = 50e9;
      const total = eligible.reduce((n, j) => n + (kind === 'archive' && j.state === 'SKIPPED' ? 0 : j.source_size_bytes), 0);
      const busy = state.source_cleanup_running || state.offline;
      const full = eligible.length && used + total > max - 64 * 1024 * 1024;
      return {
        preview_id: 'demo-preview-' + previewVersion + '-' + kind,
        eligible: eligible.map(j => ({job_id: j.id, name: j.name, file_name: j.name + '.mp4', source_path: j.source_path, size_bytes: j.source_size_bytes,
          kind: j.state === 'SKIPPED' ? 'SKIPPED' : 'EXPORTED', output_name: j.output_path || null,
          archive_path: kind === 'archive' ? 'archive/sources/' + j.job_key + '/' : undefined})),
        ineligible: ineligible.map(j => ({job_id: j.id, name: j.name, reason: (j[kind] && j[kind].reason) || 'Video không đủ điều kiện (dữ liệu mẫu).'})),
        count: eligible.length, total_bytes: total,
        recycle_bin: {volume: 'E:\\', used_bytes: used, items: 12, max_bytes: max, after_bytes: used + total},
        blocked: busy ? 'Một thao tác với video gốc đang chạy hoặc mất kết nối.' : full ? 'Thùng rác không đủ chỗ (giới hạn trừ 64 MiB dự phòng).' : null,
      };
    }

    return {
      mode: 'demo',
      snapshot: () => state,
      subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
      refresh: () => Promise.resolve(state),
      loadAI: () => Promise.resolve(),
      loadMemory: () => Promise.resolve(state),
      loadPhone: () => Promise.resolve(state),
      start() {},
      stop() {},
      pause() {},
      resume() { emit(); }, // the review dialog closed: rows show the decisions made in it
      review,
      async dispatch(operation, job, body) {
        online();
        conflict('Dữ liệu vừa thay đổi. Đóng hộp thoại và mở lại để kiểm tra; không tự gửi lại thao tác.', 'state_changed');
        if (operation === 'start' || operation === 'rerun') C.validateScan(body, operation === 'start');
        if (operation === 'finalize') C.exportSelection(body.size_mode, body.max_output_gb);
        const j = job ? getJob(job.id) : null;
        const descriptor = C.request(operation, j || job, body);
        state.requests.push(descriptor);
        apply(operation, j, body || {});
        changed();
        return {descriptor, status: operation === 'shutdown' ? 202 : 200, body: {}};
      },
      async preview(kind, ids) {
        const descriptor = C.request(kind === 'cleanup' ? 'cleanupPreview' : 'archivePreview', null, {});
        const p = previewFor(kind, ids);
        descriptor.path += '?ids=' + ids.join(',');
        state.requests.push(descriptor);
        return p;
      },
      async fileAction(kind, ids, previewId) {
        online();
        if (state.source_cleanup_running) throw demoError(409, 'Hệ thống không sẵn sàng. Không thực hiện thao tác.', {code: 'busy'});
        const fresh = previewFor(kind, ids);
        if (fresh.preview_id !== previewId) throw demoError(409, 'Danh sách đã thay đổi. Xem lại danh sách mới rồi xác nhận lần nữa.', {code: 'preview_changed', preview: fresh});
        if (state.scenario === 'conflict' && !conflictUsed) { conflictUsed = true; previewVersion++; throw demoError(409, 'Danh sách đã thay đổi; không tự gửi lại.', {code: 'preview_changed', preview: previewFor(kind, ids)}); }
        if (fresh.blocked) throw demoError(409, fresh.blocked, {code: 'bin_capacity', preview: fresh});
        state.requests.push(C.request(kind, null, {job_ids: ids, preview_id: previewId}));
        const results = fresh.eligible.map(item => {
          const j = getJob(item.job_id);
          if (kind === 'cleanup') { j.source_cleaned = true; j.source_present = false; j.source_cleanup = {id: 1000 + j.id, state: 'RECYCLED', verified: true}; }
          else { j.source_archived = true; j.source_present = false; j.source_archive = {id: 2000 + j.id, state: 'ARCHIVED', kind: j.state === 'SKIPPED' ? 'SKIPPED' : 'EXPORTED', export_recycled: j.state !== 'SKIPPED', export_verified: true}; }
          return {job_id: j.id, name: j.name, status: kind === 'cleanup' ? 'RECYCLED' : 'ARCHIVED', message: 'Đã mô phỏng', size_bytes: j.source_size_bytes};
        });
        changed();
        return {results};
      },
      async logoAction(remove, body) {
        online();
        if (body.expected_sha256 !== state.memory_sha256) throw demoError(409, 'Bộ nhớ vừa thay đổi. Tải lại danh sách và xác nhận lần nữa.', {code: 'memory_changed'});
        conflict('Bộ nhớ vừa thay đổi. Tải lại danh sách và xác nhận lần nữa.', 'memory_changed');
        state.requests.push(C.request(remove ? 'logoDelete' : 'logoClass', null, body));
        if (remove) state.logos = state.logos.filter(x => x.key !== body.key);
        else { const l = state.logos.find(x => x.key === body.key); if (l) { l.memory_class = body.memory_class; l.platform = body.platform; } }
        state.memory_sha256 = (parseInt(state.memory_sha256.slice(-6), 16) + 1).toString(16).padStart(64, '0');
        emit();
        return {};
      },
      /* Demo-only: the illustrative review modal records decisions here. */
      recordReview(operation, job, body, patch) {
        if (state.offline) return false;
        const j = getJob(job.id);
        state.requests.push(C.request(operation, j, body));
        Object.assign(j, patch);
        changed();
        return true;
      },
      scenario(name) {
        state.scenario = name; state.offline = name === 'offline'; state.source_cleanup_running = name === 'busy'; conflictUsed = false;
        const j = getJob(102);
        if (name === 'rendering') { j.state = 'RENDERING'; j.render_progress = {state: 'VERIFYING', percent: 98}; state.active = {job_id: 102, stage: 'render', pid: 12345}; }
        else if (j.state === 'RENDERING') { j.state = 'SCANNING_LOGO'; delete j.render_progress; state.active = {job_id: 102, stage: 'visual_logo', pid: 12345}; }
        previewVersion++; emit();
      },
      reset() { state = Mock.create(); reviews.clear(); writers.clear(); conflictUsed = false; previewVersion++; emit(); },
    };
  }

  return {create};
});
