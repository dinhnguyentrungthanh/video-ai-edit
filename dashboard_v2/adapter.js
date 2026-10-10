/* ControlCenterAdapter: the only Dashboard V2 code that talks HTTP.
 * Same origin as the Control Center (route /dashboard-v2). Loaded only by live.html;
 * the standalone demo (index.html) never loads this file.
 * Rules (docs/DASHBOARD_V2_UPDATE_GUIDE.md, section 8.3):
 *  - POST sends Content-Type: application/json and X-BiliFlow-Token.
 *  - A POST answered 403 refreshes the token (GET /api/session) and is sent again exactly once.
 *  - No other write is ever repeated (408, network error, 409, 400, 5xx surface to the user), except the
 *    review writes of one job (plan 6.7: decision, clear, bulk-keep, bulk-accept): a serial chain like the
 *    classic writeChain, where a network error or a status >= 500 is sent again after 300 ms, then 900 ms
 *    (the same body: it sets one decision, or keeps / accepts the still undecided items of a filter).
 *    finalize is never repeated (dispatch).
 *  - GET is retried only when the user asks; a GET 403 never refreshes the token.
 *  - A GET with no answer after 15 s is cut and fails like a dropped connection; a POST is never cut.
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
      this.errors = extra && Array.isArray(extra.errors) ? extra.errors : []; // 400 BATCH_REJECTED: one per link
      this.detail = extra && extra.detail || {}; // the episode and group refusals: the fields of ERROR_DETAIL only
    }
  }
  /* Fields of a refused episode or group request the page may show or act on (docs/SOURCE_ACCOUNTS_PLAN.md 9.14):
   * NOT_WAITING {group_id, state}, STALE_DRAFT {revision}, ITEMS_EXIST {existing, existing_count}, VARIANT_* {episodes,
   * episode_count}, SCOPE_NOT_CONFIRMED {count, confirm_label}, GROUP_TOO_LARGE {count}, BAD_SELECTION {unknown}. */
  const ERROR_DETAIL = ['group_id', 'state', 'revision', 'existing', 'existing_count', 'episodes', 'episode_count', 'count',
    'confirm_label', 'unknown'];
  function errorDetail(body) {
    const out = {};
    if (body && typeof body === 'object') ERROR_DETAIL.forEach(name => { if (body[name] !== undefined) out[name] = body[name]; });
    return out;
  }

  const MESSAGES = {
    0: 'Mất kết nối Control Center. Thao tác có thể chưa được gửi hoặc đã chạy; tải lại trạng thái trước khi thử lại.',
    403: 'Phiên Control Center không hợp lệ. Tải lại trang để lấy phiên mới.',
    408: 'Control Center đóng yêu cầu vì gửi quá chậm (408). Không tự gửi lại; tải lại trạng thái trước khi thử lại.',
    500: 'Control Center gặp lỗi khi xử lý (500).',
  };

  /* Default transport: window.fetch, same origin, no cookies sent elsewhere, no cache. A GET with no full answer after
   * GET_TIMEOUT_MS is cut (2026-10-06: a phone waking up on a dead connection would hang a poll for minutes): it fails
   * like a dropped connection, so the page says it is offline and the next poll tries again. A POST is never cut: the
   * Control Center may still be running it. */
  const GET_TIMEOUT_MS = 15000;
  function fetchTransport(fetchImpl, getTimeoutMs = GET_TIMEOUT_MS) {
    return async function transport(method, path, options) {
      const init = {method, cache: 'no-store', credentials: 'same-origin', redirect: 'error', headers: options.headers || {}};
      if (options.body !== undefined) init.body = options.body;
      const limit = method === 'GET' && typeof AbortController === 'function' ? new AbortController() : null;
      const timer = limit ? setTimeout(() => limit.abort(), getTimeoutMs) : null;
      if (limit) init.signal = limit.signal;
      try {
        const response = await fetchImpl(path, init);
        // raw: only the status matters (video probe); the body is never read or parsed.
        if (options.raw) { try { if (response.body && response.body.cancel) await response.body.cancel(); } catch (_) { /* nothing to release */ } return {status: response.status, body: null}; }
        let body = null;
        const text = await response.text();
        if (text) { try { body = JSON.parse(text); } catch (_) { body = {error: text.slice(0, 300)}; } }
        return {status: response.status, body};
      } finally {
        clearTimeout(timer);
      }
    };
  }

  function errorFrom(status, body, operation) {
    const reason = body && typeof body.error === 'string' && body.error ? body.error : (MESSAGES[status] || ('HTTP ' + status));
    const message = status === 409 ? reason + (body && body.code ? ' (' + body.code + ')' : '') : reason;
    return new AdapterError(status, message, {code: body && body.code, preview: body && body.preview, operation,
      errors: body && body.errors, detail: errorDetail(body)});
  }

  function create(options) {
    options = options || {};
    const C = options.contracts || (typeof window !== 'undefined' ? window.BFContracts : null);
    if (!C) throw new Error('BFContracts is required');
    const transport = options.transport || fetchTransport(options.fetch || (typeof fetch === 'function' ? fetch.bind(globalThis) : null));
    let token = null, tokenRequest = null;
    let statusSeq = 0, statusApplied = 0, downloadsSeq = 0, downloadsApplied = 0;
    const inflight = new Map(), writers = new Map();
    const sleep = options.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
    const WRITE_RETRY_MS = [300, 900];

    async function send(method, path, body, headers, raw) {
      try {
        return await transport(method, path, raw ? {headers: headers || {}, body, raw: true} : {headers: headers || {}, body});
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

    /* Review writes (R2): one chain per job that outlives the dialog (closing it never cancels a write).
     * Never once(): two quick decisions on two cards are two POSTs, in order. 403 → session, one resend
     * (post()); 400/409 are never resent; no /api/status after a decision. */
    function writer(id) { if (!writers.has(id)) writers.set(id, {chain: Promise.resolve(), pending: 0}); return writers.get(id); }
    async function postWrite(path, body, operation) {
      for (let attempt = 1; ; attempt++) {
        try { return await post(path, body, operation); } catch (error) {
          error.attempts = attempt;
          if ((error.status && error.status < 500) || attempt > WRITE_RETRY_MS.length) throw error;
          await sleep(WRITE_RETRY_MS[attempt - 1]);
        }
      }
    }

    /* Review dialog: GETs, URL builders and the decision writes of one job. URLs are strings only (no fetch):
     * frames and video load as same-origin <img>/<video> under the V2 CSP (no blob:). */
    function review(jobId) {
      const job = {id: Number(jobId)};
      if (!/^\d{1,9}$/.test(String(jobId)) || !Number.isInteger(job.id) || job.id <= 0) throw new AdapterError(400, 'Thiếu job id hợp lệ.');
      const path = (operation, query) => C.request(operation, job, null, query).path;
      let mediaKey = null, sessionRequest = null;
      function session() {
        if (!sessionRequest) {
          sessionRequest = get(path('reviewSession')).then(body => {
            if (body && typeof body.token === 'string') token = body.token; // same Control Center token, kept in this closure
            mediaKey = body && typeof body.media_key === 'string' ? body.media_key : null;
            return {media_key: mediaKey};
          }).finally(() => { sessionRequest = null; });
        }
        return sessionRequest;
      }
      return {
        jobId: job.id,
        queue: () => get(path('queue')),
        session,
        mediaKey: () => mediaKey,
        resources: () => get(path('resources')),
        exportState: () => get(path('reviewExport')),
        evidence: item => get(path('evidence', {item: String(item)})),
        /* Range bytes=0-0, status only (404/409/410/415 give the reason a video does not play). */
        async probeVideo(key) { return (await send('GET', path('video', {k: key}), undefined, {Range: 'bytes=0-0'}, true)).status; },
        frameUrl: (item, t, key) => path('frame', {item: String(item), t: String(t), k: key}),
        videoUrl: key => path('video', {k: key}),
        mediaUrl: p => C.endpoints.media[1].replace('{path}', encodeURIComponent(String(p))),
        /* operation 'decision' {id, decision, full_frame, note, remember_*?}, 'clear' {id}, 'bulkKeep' / 'bulkAccept'
         * {filter}, body as given. Resolves {body: server queue, last: no other write of this job waits}; rejects
         * after the retries. A bulk write queued after decisions waits for them (the classic runBlocking). */
        write(operation, body) {
          if (!['decision', 'clear', 'bulkKeep', 'bulkAccept'].includes(operation)) return Promise.reject(new AdapterError(400, 'Thao tác ghi không hợp lệ.'));
          const descriptor = C.request(operation, job, body), w = writer(job.id);
          w.pending++;
          const run = async () => {
            try { const result = await postWrite(descriptor.path, descriptor.body, operation); return {body: result.body, last: w.pending === 1}; }
            finally { w.pending--; }
          };
          const done = w.chain.then(run, run);
          w.chain = done.then(() => {}, () => {});
          return done;
        },
        pendingWrites: () => writer(job.id).pending,
        /* Resolves once every write already queued for this job has settled (reopening waits for it). */
        idle: () => writer(job.id).chain,
      };
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
      /* "Tải video": GET /api/downloads, guarded like loadStatus (null = a newer answer was applied). */
      async loadDownloads() {
        const seq = ++downloadsSeq;
        const body = await get(C.endpoints.downloads[1]);
        if (seq <= downloadsApplied) return null;
        downloadsApplied = seq;
        return body;
      },
      loadDownload: id => get(C.request('downloadTask', {id: Number(id)}).path),
      /* A series page's stored episode list, its draft and plan (no browser, no ticket); a group and its members. */
      loadEpisodes: id => get(C.request('downloadEpisodes', {id: Number(id)}).path),
      loadGroup: id => get(C.request('downloadGroup', {id: Number(id)}).path),
      /* "Dung lượng": read-only; refresh asks the server to compute again (it answers at once with the last value). */
      loadStorage: refresh => get(C.endpoints.storageSummary[1] + (refresh ? '?refresh=1' : '')),
      loadAI: () => get(C.endpoints.ai[1]),
      loadMemory: () => get(C.endpoints.logos[1]),
      /* Phone mode: on the PC the status (code included); on the phone only {remote: true}. */
      loadPhone: () => get(C.endpoints.phoneStatus[1]),
      /* Cài đặt → Tailscale: PC only (the phone listener answers 403 pc_only). */
      loadTailscale: () => get(C.endpoints.tailscale[1]),
      health: () => get(C.endpoints.health[1]),
      review,
      /* Read-only preview right before a cleanup ("Xóa video gốc"), a delete ("Xóa video") or an archive. */
      async preview(kind, ids) {
        const list = positiveIds(ids);
        const endpoint = {cleanup: C.endpoints.cleanupPreview, delete: C.endpoints.deletePreview, archive: C.endpoints.archivePreview}[kind] || null;
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
      tailscale: previous.tailscale || null, // Cài đặt → Tailscale (GET /api/tailscale, PC only)
      remote: !!previous.remote,
      // The page's listener (plan 9.18): 'pc' or 'phone' only from an answer of /api/phone-mode with a boolean `remote`;
      // null while unknown (pending, an error, a timeout, an answer without it). The PC-only account buttons need 'pc'.
      // An error never changes what an earlier answer said: a page never moves to another listener.
      device: previous.device === 'pc' || previous.device === 'phone' ? previous.device : null,
      device_checking: !!previous.device_checking,
      device_error: previous.device_error || null,
      // "Tải video" polls on its own (only while #downloads is open); /api/status keeps its last answer.
      downloads: previous.downloads || null,
      downloads_error: previous.downloads_error || null,
      storage_summary: previous.storage_summary || null, // "Dung lượng" (/api/storage-summary); `storage` above is /api/status
      storage_error: previous.storage_error || null,
      offline: false,
      requests: [],
    };
  }

  /* /api/phone-mode (and the phone-mode writes) answer {remote: false} on the PC listener and {remote: true} on the
   * phone listener. Anything else keeps what an earlier answer said (null when none did). */
  const deviceOf = (body, known) => body && typeof body.remote === 'boolean' ? (body.remote ? 'phone' : 'pc') : known || null;
  const deviceProblem = error => error.status === 0 ? 'mất kết nối hoặc quá thời gian chờ'
    : error.status === 404 ? 'Control Center không có mục này (404), có thể là bản cũ' : 'Control Center trả lỗi ' + error.status;
  const PC_UNCONFIRMED = 'Chưa xác định được trang này mở trên PC nên chưa gửi. Bấm Kiểm tra lại trong khung Tài khoản nguồn phim.';
  const PHONE_RETRIES = 5; // the poll asks /api/phone-mode again at most this often while the mode is unknown

  /* Live store: same interface as BFDemoStore, backed by the adapter. */
  function createLiveStore(adapter, options) {
    options = options || {};
    let snap = {mode: 'live', jobs: [], logos: [], active: null, queue: {length: 0, paused: false}, resources: {cpu_percent: 0, memory: {percent: 0}, disk: {}, gpu: null}, ai: {ready: false, config: {}, message: 'Đang tải…'}, offline: false, loading: true, requests: [], device: null, device_checking: false, device_error: null};
    const listeners = new Set();
    let ai = null, memory = null, timer = null, phone = null, paused = false, downloadTimer = null, statusPolling = null;
    let phoneRequest = null, phoneRetries = 0;
    let tailscale = null, tailscaleWatch = false, tailscalePolling = null;
    // A listener that throws (a render bug) must not turn a finished request into a failed one: reported apart.
    const emit = () => listeners.forEach(fn => { try { fn(snap); } catch (error) { setTimeout(() => { throw error; }); } });

    async function loadDownloads() {
      try {
        const body = await adapter.loadDownloads();
        if (body === null) return snap; // an older poll answered late
        snap = {...snap, downloads: body, downloads_error: null};
      } catch (error) {
        snap = {...snap, downloads_error: error.message};
      }
      emit();
      return snap;
    }
    async function loadStorage(refresh) {
      try {
        // Await first: {...snap, x: await …} would copy snap before the wait and drop what changed meanwhile.
        const summary = await adapter.loadStorage(refresh);
        snap = {...snap, storage_summary: summary, storage_error: null};
      } catch (error) {
        snap = {...snap, storage_error: error.message};
      }
      emit();
      return snap;
    }

    async function refresh() {
      try {
        const status = await adapter.loadStatus();
        if (status === null) return snap; // an older poll answered late
        const wasOffline = snap.offline;
        snap = normalizeSnapshot(status, ai, memory, snap);
        if (wasOffline && !snap.device) phoneRetries = 0; // back online: an unknown mode is asked again a few times
      } catch (error) {
        snap = {...snap, offline: true, loading: false, error: error.message};
      }
      emit();
      return snap;
    }
    /* /api/status polling (2026-10-06): one poll at a time, so a slow phone link never piles polls up, and none while
     * the tab is hidden (screen off, another tab). Showing the tab again sends a fresh request at once instead of
     * waiting on one that may hang on a dead connection (the transport cuts it after 15 s). Writes, resume() and the
     * export dialog still refresh at once: loadStatus drops whichever answer is older. */
    function pollStatus() {
      if (statusPolling) return statusPolling;
      const request = refresh().finally(() => { if (statusPolling === request) statusPolling = null; });
      statusPolling = request;
      return request;
    }
    function tick() {
      if (paused || (typeof document !== 'undefined' && document.hidden)) return; // paused: the review dialog polls its own queue (Q7)
      pollStatus();
      if (ai && ai.login_running) loadAI();
      if (snap.device === 'pc' && phone && phone.enabled) loadPhone(); // failed attempts / lock on the PC panel
      // The page's mode is still unknown (an error or a timeout): asked again a few times, GET only.
      if (!snap.device && !phoneRequest && phoneRetries < PHONE_RETRIES) { phoneRetries++; loadPhone(); }
      // Cài đặt open on the PC, or a Tailscale task still running (its end may have turned the phone mode on).
      if (!snap.remote && (tailscaleWatch || (tailscale && tailscale.task && tailscale.task.running))) loadTailscale();
    }
    function onVisible() { if (!timer || document.hidden || paused) return; statusPolling = null; tick(); }
    async function loadAI() {
      try { ai = await adapter.loadAI(); snap = {...snap, ai}; emit(); } catch (_) { /* the settings page shows the last state */ }
    }
    /* One request at a time. Only an answer with a boolean `remote` sets the page's mode (deviceOf); an error, a
     * timeout or an answer without it leaves the mode as it was (unknown at first: no PC-only account button). Only the
     * first check shows "Đang kiểm tra"; a retry after an error keeps that error on the page until it has an answer
     * (Kiểm tra lại shows its own progress). `fresh`: a request already on its way may have left before what the caller
     * waits for (a Tailscale task's end), so it is asked again once that one ends. */
    function loadPhone(options) {
      if (phoneRequest) return options && options.fresh ? phoneRequest.then(() => loadPhone()) : phoneRequest;
      if (!snap.device && !snap.device_error) { snap = {...snap, device_checking: true}; emit(); }
      const request = (async () => {
        try {
          phone = await adapter.loadPhone();
          const device = deviceOf(phone, snap.device);
          snap = {...snap, phone, device, remote: device === 'phone', device_checking: false,
            device_error: device ? null : 'câu trả lời của Control Center thiếu thông tin này'};
        } catch (error) { /* an older Control Center has no phone mode: the panel says so */
          snap = {...snap, phone: {unavailable: true}, device_checking: false, device_error: deviceProblem(error)};
        }
        emit();
        return snap;
      })().finally(() => { if (phoneRequest === request) phoneRequest = null; });
      phoneRequest = request;
      return request;
    }
    /* One request at a time (the server runs sc.exe and `tailscale status` for it). An older Control Center
     * (404) shows the panel's restart note; another error keeps the last state. */
    function loadTailscale() {
      if (tailscalePolling) return tailscalePolling;
      const request = (async () => {
        const wasRunning = !!(tailscale && tailscale.task && tailscale.task.running);
        try {
          tailscale = await adapter.loadTailscale();
        } catch (error) {
          tailscale = error.status === 404 ? {unavailable: true} : {...(tailscale || {}), load_error: error.message};
        }
        snap = {...snap, tailscale};
        emit();
        // "Mở cho điện thoại ngoài nhà" turns the phone mode on at the end of its task.
        if (wasRunning && !(tailscale.task && tailscale.task.running)) await loadPhone({fresh: true});
        return snap;
      })().finally(() => { if (tailscalePolling === request) tailscalePolling = null; });
      tailscalePolling = request;
      return request;
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
      loadTailscale,
      /* Cài đặt open on the PC: the Tailscale panel follows every poll; closed: only while a task runs. */
      watchTailscale(on) {
        tailscaleWatch = !!on;
        if (tailscaleWatch && !snap.remote) loadTailscale();
      },
      start(intervalMs) {
        pollStatus(); loadAI(); loadPhone().then(s => { if (!s.remote) loadTailscale(); });
        if (timer || !intervalMs) return;
        timer = setInterval(tick, intervalMs);
        if (typeof document !== 'undefined' && document.addEventListener) document.addEventListener('visibilitychange', onVisible);
      },
      stop() {
        clearInterval(timer); timer = null; clearInterval(downloadTimer); downloadTimer = null;
        if (typeof document !== 'undefined' && document.removeEventListener) document.removeEventListener('visibilitychange', onVisible);
      },
      loadDownloads,
      loadStorage,
      loadDownload: id => adapter.loadDownload(id),
      loadEpisodes: id => adapter.loadEpisodes(id),
      loadGroup: id => adapter.loadGroup(id),
      /* #downloads open: its list every intervalMs (and "Dung lượng" while the server computes it); closed: nothing.
       * One poll at a time (a slow link never piles requests up), none while the tab is hidden; after a
       * "Dung lượng" error it waits for "Tính lại". */
      watchDownloads(on, intervalMs) {
        clearInterval(downloadTimer); downloadTimer = null;
        if (!on) return;
        let polling = null;
        const poll = opening => {
          if (polling || (!opening && typeof document !== 'undefined' && document.hidden)) return;
          const summary = snap.storage_summary, again = opening || (summary ? summary.computing : !snap.storage_error);
          polling = Promise.all([loadDownloads(), again ? loadStorage(false) : null]).finally(() => { polling = null; });
        };
        poll(true); // opening the page: the list and "Dung lượng" (the server recomputes it when older than 5 minutes)
        downloadTimer = setInterval(() => poll(false), intervalMs || 2000);
      },
      /* Download writes refresh the download list only (never /api/status), also after a refusal (409: it changed). */
      async downloadAction(operation, id, body) {
        try {
          return (await adapter.dispatch(operation, id ? {id: Number(id)} : null, body)).body;
        } finally {
          await loadDownloads();
        }
      },
      /* The episode dialog's draft: no list refresh (the dialog keeps its own plan); one at a time (download-episodes.js). */
      async episodeDraft(id, body) {
        return (await adapter.dispatch('downloadEpisodesDraft', {id: Number(id)}, body)).body;
      },
      /* "Tải N tập": the list is refreshed after it (the new group), also after a refusal. */
      async episodeConfirm(id, body) {
        try {
          return (await adapter.dispatch('downloadEpisodesConfirm', {id: Number(id)}, body)).body;
        } finally {
          await loadDownloads();
        }
      },
      /* "Tài khoản nguồn phim" (PC only): login answers 202 at once; the result is read from the next list
       * (accounts.sources[].state / last_login), never from the answer. The list is refreshed also after a refusal. */
      async accountAction(operation, source) {
        // Never sent before /api/phone-mode said this page is the PC (download-core.accountActions shows no button then).
        if (snap.device !== 'pc') throw new Error(PC_UNCONFIRMED);
        try {
          return (await adapter.dispatch(operation, {source: String(source)}, {})).body;
        } finally {
          await loadDownloads();
        }
      },
      /* Review dialog open: /api/status polling pauses; on close it refreshes at once and resumes. */
      pause() { paused = true; },
      resume() { if (!paused) return; paused = false; refresh(); },
      review: jobId => adapter.review(jobId),
      async dispatch(operation, job, body) {
        const result = await adapter.dispatch(operation, job, body);
        if (['aiConfig', 'aiCheck', 'aiLogin'].includes(operation)) { ai = result.body; snap = {...snap, ai}; }
        if (operation === 'phoneMode') {
          phone = result.body;
          const device = deviceOf(result.body, snap.device);
          snap = {...snap, phone, device, remote: device === 'phone'};
          emit();
          return result;
        }
        // "Gia hạn thêm 8 giờ": the PC gets its full status back, the phone (over Tailscale) only the new time.
        if (operation === 'phoneExtend') {
          phone = {...(phone || {}), ...result.body};
          const device = deviceOf(result.body, snap.device);
          snap = {...snap, phone, device, remote: device === 'phone'};
          emit();
          return result;
        }
        // tailscaleInstall … tailscaleRemoteOn: the answer is the panel state with the task just started.
        if (/^tailscale[A-Z]/.test(operation)) { tailscale = result.body; snap = {...snap, tailscale}; emit(); return result; }
        if (operation === 'shutdown') { snap = {...snap, offline: true, stopping: true}; emit(); return result; }
        await refresh();
        return result;
      },
      preview: (kind, ids) => adapter.preview(kind, ids),
      async fileAction(kind, ids, previewId) {
        const operation = ['cleanup', 'delete'].includes(kind) ? kind : 'archive';
        const body = {job_ids: ids, preview_id: previewId};
        // The dialog's "Tôi hiểu" box was ticked: the server refuses a permanent delete without this (400).
        if (operation !== 'archive') body.confirm_permanent = true;
        const result = await adapter.dispatch(operation, null, body);
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
