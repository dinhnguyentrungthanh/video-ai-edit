/* ControlCenterAdapter: the only Dashboard V2 code that talks HTTP.
 * Same origin as the Control Center (route /dashboard-v2). Loaded only by live.html;
 * the standalone demo (index.html) never loads this file.
 * Rules (docs/DASHBOARD_V2_UPDATE_GUIDE.md, section 8.3):
 *  - POST sends Content-Type: application/json and X-BiliFlow-Token.
 *  - A POST answered 403 refreshes the token (GET /api/session) and is sent again exactly once.
 *  - No other write is ever repeated (408, network error, 409, 400, 5xx surface to the user).
 *  - GET is retried only when the user asks; a GET 403 never refreshes the token.
 *  - Polling responses carry a sequence number; an older response never replaces a newer one.
 *  - The token lives only in this closure: never in a URL, localStorage or a log line.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BFAdapter = api;
})(typeof window === 'undefined' ? this : window, function () {
  'use strict';

  class AdapterError extends Error {
    constructor(status, message, extra) {
      super(message);
      this.name = 'AdapterError';
      this.status = status;          // 0 = no HTTP answer (network error / connection closed)
      this.code = extra && extra.code || null;      // 409 body.code (top level)
      this.preview = extra && extra.preview || null; // 409 body.preview when the list changed
      this.operation = extra && extra.operation || null;
    }
  }

  const MESSAGES = {
    0: 'Mất kết nối Control Center. Thao tác có thể chưa được gửi hoặc đã chạy; tải lại trạng thái trước khi thử lại.',
    403: 'Phiên Control Center không hợp lệ. Tải lại trang để lấy phiên mới.',
    408: 'Control Center đóng yêu cầu vì gửi quá chậm (408). Không tự gửi lại; tải lại trạng thái trước khi thử lại.',
    500: 'Control Center gặp lỗi khi xử lý (500).',
  };

  /* Default transport: window.fetch, same origin, no cookies sent elsewhere, no cache. */
  function fetchTransport(fetchImpl) {
    return async function transport(method, path, options) {
      const init = {method, cache: 'no-store', credentials: 'same-origin', redirect: 'error', headers: options.headers || {}};
      if (options.body !== undefined) init.body = options.body;
      const response = await fetchImpl(path, init);
      let body = null;
      const text = await response.text();
      if (text) { try { body = JSON.parse(text); } catch (_) { body = {error: text.slice(0, 300)}; } }
      return {status: response.status, body};
    };
  }

  function errorFrom(status, body, operation) {
    const reason = body && typeof body.error === 'string' && body.error ? body.error : (MESSAGES[status] || ('HTTP ' + status));
    const message = status === 409 ? reason + (body && body.code ? ' (' + body.code + ')' : '') : reason;
    return new AdapterError(status, message, {code: body && body.code, preview: body && body.preview, operation});
  }

  function create(options) {
    options = options || {};
    const C = options.contracts || (typeof window !== 'undefined' ? window.BFContracts : null);
    if (!C) throw new Error('BFContracts is required');
    const transport = options.transport || fetchTransport(options.fetch || (typeof fetch === 'function' ? fetch.bind(globalThis) : null));
    let token = null, tokenRequest = null;
    let statusSeq = 0, statusApplied = 0;
    const inflight = new Map();

    async function send(method, path, body, headers) {
      try {
        return await transport(method, path, {headers: headers || {}, body});
      } catch (error) {
        throw new AdapterError(0, MESSAGES[0], {operation: path});
      }
    }

    async function refreshToken() {
      if (!tokenRequest) {
        tokenRequest = (async () => {
          const response = await send('GET', C.endpoints.session[1]);
          if (response.status !== 200 || !response.body || typeof response.body.token !== 'string') {
            throw errorFrom(response.status === 200 ? 500 : response.status, response.body, 'session');
          }
          token = response.body.token;
          return token;
        })().finally(() => { tokenRequest = null; });
      }
      return tokenRequest;
    }

    async function get(path) {
      const response = await send('GET', path);
      if (response.status !== 200) throw errorFrom(response.status, response.body, path);
      return response.body;
    }

    async function post(path, body, operation) {
      const payload = JSON.stringify(body || {});
      if (!token) await refreshToken();
      const headers = () => ({'Content-Type': 'application/json', 'X-BiliFlow-Token': token});
      let response = await send('POST', path, payload, headers());
      // A pc_only refusal (phone listener) is not a session problem: no refresh, no resend.
      if (response.status === 403 && !(response.body && response.body.code === 'pc_only')) {
        // The Control Center restarted (new token) or the tab is stale: one refresh, one resend.
        await refreshToken();
        response = await send('POST', path, payload, headers());
      }
      if (response.status < 200 || response.status >= 300) throw errorFrom(response.status, response.body, operation);
      return {status: response.status, body: response.body};
    }

    /* One request at a time per operation + target: a double click never sends two POSTs. */
    function once(key, fn) {
      if (inflight.has(key)) return inflight.get(key);
      const promise = Promise.resolve().then(fn).finally(() => inflight.delete(key));
      inflight.set(key, promise);
      return promise;
    }

    function positiveIds(ids) {
      const list = (ids || []).map(Number);
      if (!list.length || list.length > 50 || list.some(id => !Number.isInteger(id) || id <= 0)) {
        throw new AdapterError(400, 'Chọn từ 1 đến 50 video hợp lệ.');
      }
      return list;
    }

    return {
      AdapterError,
      refreshToken,
      hasToken: () => !!token,
      /* GET /api/status, guarded by sequence: returns null when a newer answer was applied. */
      async loadStatus() {
        const seq = ++statusSeq;
        const body = await get(C.endpoints.status[1]);
        if (seq <= statusApplied) return null;
        statusApplied = seq;
        return body;
      },
      loadJob(id) {
        if (!Number.isInteger(id) || id <= 0) return Promise.reject(new AdapterError(400, 'Thiếu job id hợp lệ.'));
        return get(C.endpoints.detail[1].replace('{id}', id));
      },
      loadAI: () => get(C.endpoints.ai[1]),
      loadMemory: () => get(C.endpoints.logos[1]),
      /* Phone mode: on the PC the status (code included); on the phone only {remote: true}. */
      loadPhone: () => get(C.endpoints.phoneStatus[1]),
      health: () => get(C.endpoints.health[1]),
      /* Read-only preview right before a cleanup / archive. */
      async preview(kind, ids) {
        const list = positiveIds(ids);
        const endpoint = kind === 'cleanup' ? C.endpoints.cleanupPreview : kind === 'archive' ? C.endpoints.archivePreview : null;
        if (!endpoint) throw new AdapterError(400, 'Loại xem trước không hợp lệ.');
        return get(endpoint[1] + '?ids=' + encodeURIComponent(list.join(',')));
      },
      /* Every state change goes through here: path and body come from BFContracts.request. */
      async dispatch(operation, job, body) {
        const descriptor = C.request(operation, job, body);
        if (descriptor.method !== 'POST') throw new AdapterError(400, 'Thao tác ghi phải là POST.');
        const key = descriptor.method + ' ' + descriptor.path;
        return once(key, () => post(descriptor.path, descriptor.body, operation).then(result => ({descriptor, ...result})));
      },
    };
  }

  /* Field derivation for the presenter: the backend value stays, display fields are added. */
  const PALETTES = ['blue', 'rose', 'sage', 'amber', 'violet'];
  function baseName(path) { return String(path || '').split(/[\\/]/).pop() || ''; }
  function stem(name) { return name.replace(/\.[^.]+$/, ''); }
  function clock(seconds) {
    const n = Number(seconds);
    if (!Number.isFinite(n) || n <= 0) return '--:--';
    const h = Math.floor(n / 3600), m = Math.floor(n % 3600 / 60), s = Math.floor(n % 60);
    return (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(s).padStart(2, '0');
  }
  function normalizeJob(raw) {
    const j = {...raw};
    j.name = stem(baseName(j.source_path)) || ('Video #' + j.id);
    j.duration = clock(j.duration_seconds);
    j.palette = PALETTES[Math.abs(Number(j.id) || 0) % PALETTES.length];
    j.detector_groups = Array.isArray(j.detector_groups) ? j.detector_groups : [];
    j.source_size_bytes = Number(j.source_size_bytes) || 0;
    j.active_revision = j.active_revision ?? '—';
    // Only what the backend says about the export; the name is never derived here.
    j.output_path = (j.cleanup && j.cleanup.output_name) || (j.archive && j.archive.output_name) || null;
    // Computed by the backend (/api/status, store.render_request); never derived here.
    j.render_request = raw.render_request === true;
    return j;
  }
  function normalizeLogos(memory) {
    const records = memory && Array.isArray(memory.records) ? memory.records : [];
    return records.map((r, i) => ({
      ...r,
      name: (Array.isArray(r.labels) && r.labels.length ? r.labels.join(' · ') : r.key) +
        (r.episode && r.episode.name ? ' · ' + r.episode.name : ''),
      color: PALETTES[i % PALETTES.length],
    }));
  }
  /* Real /api/status (+ /api/ai, /api/logo-memory) → the snapshot the presenter renders. */
  function normalizeSnapshot(status, ai, memory, previous) {
    previous = previous || {};
    return {
      mode: 'live',
      version: status.version,
      jobs: (status.jobs || []).map(normalizeJob),
      active: status.active || null,
      queue: status.queue || {length: 0, paused: false},
      scheduler_paused: !!status.scheduler_paused,
      resources: {cpu_percent: 0, memory: {percent: 0}, disk: {}, gpu: null, ...(status.resources || {})},
      storage: status.storage || null,
      detector_options: status.detector_options || [],
      source_cleanup_running: !!status.source_cleanup_running,
      ai: ai || previous.ai || {ready: false, config: {enabled: false, model: '', reasoning_effort: ''}, message: 'Chưa tải trạng thái AI'},
      logos: memory ? normalizeLogos(memory) : (previous.logos || []),
      memory_sha256: memory ? memory.memory_sha256 : (previous.memory_sha256 || null),
      memory_loaded: !!memory || !!previous.memory_loaded,
      phone: previous.phone || null,
      remote: !!previous.remote,
      offline: false,
      requests: [],
    };
  }

  /* Live store: same interface as BFDemoStore, backed by the adapter. */
  function createLiveStore(adapter, options) {
    options = options || {};
    let snap = {mode: 'live', jobs: [], logos: [], active: null, queue: {length: 0, paused: false}, resources: {cpu_percent: 0, memory: {percent: 0}, disk: {}, gpu: null}, ai: {ready: false, config: {}, message: 'Đang tải…'}, offline: false, loading: true, requests: []};
    const listeners = new Set();
    let ai = null, memory = null, timer = null, phone = null;
    const emit = () => listeners.forEach(fn => fn(snap));

    async function refresh() {
      try {
        const status = await adapter.loadStatus();
        if (status === null) return snap; // an older poll answered late
        snap = normalizeSnapshot(status, ai, memory, snap);
      } catch (error) {
        snap = {...snap, offline: true, loading: false, error: error.message};
      }
      emit();
      return snap;
    }
    async function loadAI() {
      try { ai = await adapter.loadAI(); snap = {...snap, ai}; emit(); } catch (_) { /* the settings page shows the last state */ }
    }
    async function loadPhone() {
      try {
        phone = await adapter.loadPhone();
        snap = {...snap, phone, remote: phone.remote === true};
        emit();
      } catch (_) { /* an older Control Center has no phone mode: the panel says so */
        snap = {...snap, phone: {unavailable: true}};
        emit();
      }
      return snap;
    }
    async function loadMemory() {
      memory = await adapter.loadMemory();
      snap = {...snap, logos: normalizeLogos(memory), memory_sha256: memory.memory_sha256, memory_loaded: true};
      emit();
      return snap;
    }

    return {
      mode: 'live',
      snapshot: () => snap,
      subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
      refresh,
      loadAI,
      loadMemory,
      loadPhone,
      start(intervalMs) {
        refresh(); loadAI(); loadPhone();
        if (!timer && intervalMs) timer = setInterval(() => {
          refresh();
          if (ai && ai.login_running) loadAI();
          if (phone && phone.enabled && !phone.remote) loadPhone(); // failed attempts / lock on the PC panel
        }, intervalMs);
      },
      stop() { clearInterval(timer); timer = null; },
      async dispatch(operation, job, body) {
        const result = await adapter.dispatch(operation, job, body);
        if (['aiConfig', 'aiCheck', 'aiLogin'].includes(operation)) { ai = result.body; snap = {...snap, ai}; }
        if (operation === 'phoneMode') { phone = result.body; snap = {...snap, phone, remote: false}; emit(); return result; }
        if (operation === 'shutdown') { snap = {...snap, offline: true, stopping: true}; emit(); return result; }
        await refresh();
        return result;
      },
      preview: (kind, ids) => adapter.preview(kind, ids),
      async fileAction(kind, ids, previewId) {
        const operation = kind === 'cleanup' ? 'cleanup' : 'archive';
        const result = await adapter.dispatch(operation, null, {job_ids: ids, preview_id: previewId});
        await refresh();
        return result.body;
      },
      async logoAction(remove, body) {
        try {
          return (await adapter.dispatch(remove ? 'logoDelete' : 'logoClass', null, body)).body;
        } finally {
          await loadMemory().catch(() => {});
        }
      },
    };
  }

  return {AdapterError, create, fetchTransport, normalizeJob, normalizeLogos, normalizeSnapshot, createLiveStore, clock};
});
