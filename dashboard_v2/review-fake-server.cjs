/* Fake Control Center for the review browser checks (R1 media, R2 writes): synthetic queues from mock-data.js,
 * a synthetic VP8 clip made by ffmpeg in temp/, no real backend, database, video or network.
 * R2: POST /api/jobs/<id>/review/decision|clear change the in-memory queue like record_review_decision /
 * clear_review_decision (token checked, same 400 texts); server_state.failNext scripts failures
 * ({status, body} or 'drop' = connection cut mid-answer), server_state.postDelay holds every answer.
 * /classic/<id> serves the real classic page (_interactive_html, as the Control Center rewrites it) so a
 * check can compare the bodies both pages send.
 */
'use strict';
const fs = require('fs');
const http = require('http');
const path = require('path');
const vm = require('vm');
const {execFileSync, spawnSync} = require('child_process');

const ROOT = __dirname;
const TOKEN = 'browser-token';
const TYPES = {'.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.html': 'text/html'};
const ASSETS = new Set(['styles.css', 'theme.css', 'review.css', 'contracts.js', 'adapter.js', 'download-demo.js', 'mock-data.js', 'demo-store.js',
  'review-core.js', 'review-detail.js', 'review-media.js', 'review-cards.js', 'review.js', 'app.js', ...fs.readdirSync(path.join(ROOT, 'assets')).map(f => 'assets/' + f)]);
const DECISIONS = ['KEEP', 'BLUR', 'CUT', 'NEEDS_MORE_CONTEXT'];

/* The classic page exactly as the Control Center serves /review/<id> (token, API prefix). */
function classicPage(token, jobId) {
  const code = 'import sys\nfrom biliflow.review_workflow import _interactive_html\nsys.stdout.buffer.write(_interactive_html(sys.argv[1]).encode("utf-8"))';
  const venv = process.platform === 'win32' ? path.join(ROOT, '..', '.venv', 'Scripts', 'python.exe') : path.join(ROOT, '..', '.venv', 'bin', 'python');
  for (const python of [process.env.BILIFLOW_PYTHON, fs.existsSync(venv) ? venv : null, 'python3', 'python'].filter(Boolean)) {
    const run = spawnSync(python, ['-c', code, token], {cwd: path.join(ROOT, '..'), encoding: 'utf-8', env: {...process.env, PYTHONPATH: path.join(ROOT, '..', 'src')}, timeout: 60000});
    if (run.status === 0 && run.stdout.includes('<script>')) return run.stdout.split("'/api/").join(`'/api/jobs/${jobId}/review/`);
  }
  return null;
}

function create() {
  const ctx = {window: {BFContracts: require('./contracts.js')}, structuredClone};
  vm.runInNewContext(fs.readFileSync(path.join(ROOT, 'mock-data.js'), 'utf8'), ctx);
  const Mock = ctx.window.BFMock;
  const jobs = Mock.create().jobs.map(({name, duration, palette, render_request, output_path, ...j}) => ({...j, duration_seconds: 446}));
  const queues = new Map(); // job id → queue override (else generated from the job)
  const queueFor = id => {
    const j = jobs.find(x => x.id === id);
    if (!j || !j.active_queue_path) return null;
    if (!queues.has(id)) queues.set(id, Mock.reviewQueue({...j, duration: '01:00'}));
    return queues.get(id);
  };
  const counters = {queueAt: [], status: 0, queue: 0, media: 0, mediaActive: 0, mediaMax: 0, frames: 0, frame403: 0, video: 0, video403: 0, evidence: 0, session: 0};
  /* R1: a synthetic 70 s VP8 clip (ffmpeg test pattern, 1 key frame per second) in the repo's temp/ folder. */
  const CLIP = path.join(ROOT, '..', 'temp', 'review-check', 'clip.webm');
  let VIDEO = fs.existsSync(CLIP);
  if (!VIDEO) {
    try {
      fs.mkdirSync(path.dirname(CLIP), {recursive: true});
      execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc=duration=70:size=320x180:rate=10', '-c:v', 'libvpx', '-b:v', '150k', '-g', '10', CLIP], {timeout: 120000});
      VIDEO = fs.existsSync(CLIP);
    } catch (_) { VIDEO = false; }
  }
  const server_state = {key: 'mk1', token: TOKEN, videoStatus: null, evidenceDelay: 300, evidence: new Map(), failNext: [], postDelay: 0, classic: new Map()};
  const posts = [], requests = [];

  function send(res, code, body, type) {
    const data = typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body);
    res.writeHead(code, {'Content-Type': type || 'application/json; charset=utf-8', 'Cache-Control': 'no-store'});
    res.end(data);
  }
  function countQueue(q) {
    const counts = {total: q.items.length, pending: 0, decisions: {KEEP: 0, BLUR: 0, CUT: 0, NEEDS_MORE_CONTEXT: 0}};
    for (const x of q.items) { if (counts.decisions[x.decision] != null) counts.decisions[x.decision]++; else counts.pending++; }
    q.counts = counts; q.updated_at = new Date().toISOString();
    q.status = counts.decisions.NEEDS_MORE_CONTEXT ? 'NEEDS_MORE_CONTEXT' : counts.pending ? 'REVIEW_REQUIRED' : 'READY_FOR_EDIT_PLAN';
  }
  /* record_review_decision / clear_review_decision, the parts the dialog can reach (same refusal texts). */
  function applyWrite(q, kind, body) {
    const bad = error => ({status: 400, body: {error}});
    if (!body || typeof body.id !== 'string') return bad('Thiếu id');
    let item = q.items.find(x => x.id === body.id);
    if (kind === 'clear') {
      if (!item) return bad('Unknown or duplicate review item: ' + body.id);
      Object.assign(item, {decision: null, decision_note: null, decision_region_source_pixels: null, decided_at: null});
      delete item.studio_logo_memory; delete item.platform_logo_memory;
    } else {
      const d = String(body.decision || '').toUpperCase(), studio = body.remember_studio_logo === true, platform = body.remember_platform_logo === true;
      if (!DECISIONS.includes(d)) return bad('Decision must be one of ' + DECISIONS.join(', '));
      if (studio && platform) return bad('Chỉ chọn một: logo hãng phim (giữ & nhớ) hoặc logo nền tảng (làm mờ & nhớ)');
      if (studio && d !== 'KEEP') return bad('Chỉ có thể ghi nhớ logo hãng phim khi chọn Giữ nguyên');
      if (platform && d !== 'BLUR') return bad('Chỉ có thể ghi nhớ logo nền tảng khi chọn Làm mờ');
      if (!item) {
        const i = q.advisory_items.findIndex(x => x.id === body.id);
        if (i >= 0) { item = q.advisory_items.splice(i, 1)[0]; delete item.advisory; q.items.push(item); }
      }
      if (!item) return bad('Unknown or duplicate review item: ' + body.id);
      if (studio && !(item.category === 'visual_logo' && !item.suggested_region_source_pixels && item.candidate_type !== 'persistent_overlay')) return bad('Chỉ ghi nhớ được logo hãng phim cho thẻ logo toàn khung hình');
      let region = null;
      if (d === 'BLUR') region = body.full_frame ? 'FULL_FRAME' : item.suggested_region_source_pixels || (platform ? {x: 1600, y: 60, width: 240, height: 90} : null);
      if (d === 'BLUR' && !region) return bad('BLUR requires a region or explicit full-frame approval');
      Object.assign(item, {decision: d, decision_region_source_pixels: region, decided_at: new Date().toISOString(),
        decision_note: body.note ?? (studio ? 'Người duyệt xác nhận đây là logo hãng phim — giữ nguyên và ghi nhớ' : platform ? 'Người duyệt xác nhận đây là logo nền tảng video — làm mờ vùng logo và ghi nhớ' : null)});
      delete item.studio_logo_memory; delete item.platform_logo_memory;
      if (studio) item.studio_logo_memory = {remembered: true, frames: 12, frames_source: 'source_video'};
      if (platform) item.platform_logo_memory = {remembered: true, logo_frames: 4, platform: {key: 'demo', name: 'Nền tảng mẫu'}};
    }
    countQueue(q);
    return {status: 200, body: q};
  }
  function post(req, res, p) {
    let raw = '';
    req.setEncoding('utf8');
    req.on('data', chunk => { raw += chunk; });
    req.on('end', () => {
      let body = null;
      try { body = JSON.parse(raw); } catch (_) { /* recorded as null */ }
      const m = p.match(/^\/api\/jobs\/(\d+)\/review\/(decision|clear)$/);
      posts.push({path: p, body, token: req.headers['x-biliflow-token'] || null, type: req.headers['content-type'] || '', at: Date.now()});
      if (!m) return send(res, 405, {error: 'Không hỗ trợ'});
      const fail = server_state.failNext.shift();
      setTimeout(() => {
        // A cut connection the browser cannot retry by itself (it resends a request whose reused socket
        // closes before any answer): headers, part of the body, then the socket closes.
        if (fail === 'drop') { res.writeHead(200, {'Content-Type': 'application/json', 'Content-Length': 100}); res.write('{"items":'); setTimeout(() => req.socket.destroy(), 20); return; }
        if (fail) return send(res, fail.status, fail.body || {error: 'Lỗi giả ' + fail.status});
        if (req.headers['x-biliflow-token'] !== server_state.token) return send(res, 403, {error: 'Phiên không hợp lệ'});
        if (!/^application\/json/.test(req.headers['content-type'] || '')) return send(res, 415, {error: 'JSON'});
        const q = queueFor(Number(m[1]));
        if (!q) return send(res, 404, {error: 'Video chưa có danh sách duyệt'});
        const result = applyWrite(q, m[2], body);
        send(res, result.status, result.body);
      }, server_state.postDelay);
    });
  }

  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1'), p = url.pathname;
    requests.push(req.method + ' ' + req.url);
    if (req.method === 'POST') return post(req, res, p);
    if (req.method !== 'GET') { req.resume(); return send(res, 405, {error: 'GET/POST only'}); }
    if (p === '/dashboard-v2/') return send(res, 200, fs.readFileSync(path.join(ROOT, 'live.html')), 'text/html');
    if (p === '/demo/') return send(res, 200, fs.readFileSync(path.join(ROOT, 'index.html')), 'text/html');
    const asset = p.match(/^\/(?:dashboard-v2|demo)\/(.+)$/);
    if (asset && ASSETS.has(asset[1])) return send(res, 200, fs.readFileSync(path.join(ROOT, asset[1])), TYPES[path.extname(asset[1])]);
    if (p === '/api/session') return send(res, 200, {token: server_state.token});
    if (p === '/api/status') { counters.status++; return send(res, 200, {version: 't', jobs, queue: {length: 0, paused: false}, active: null, resources: {disk: {}}}); }
    if (p === '/api/phone-mode') return send(res, 200, {remote: false, enabled: false});
    if (p === '/api/ai') return send(res, 200, {ready: false, config: {enabled: false}, message: 'tắt'});
    const review = p.match(/^\/api\/jobs\/(\d+)\/review\/(queue|export|session|resources)$/);
    if (review) {
      const id = Number(review[1]), j = jobs.find(x => x.id === id);
      if (review[2] === 'queue') { counters.queue++; counters.queueAt.push(Date.now()); const q = queueFor(id); return q ? send(res, 200, q) : send(res, 404, {error: 'Video chưa có danh sách duyệt'}); }
      if (review[2] === 'export') return send(res, 200, {status: j.state, source_cleaned: !!j.source_cleaned, source_archived: !!j.source_archived});
      if (review[2] === 'session') { counters.session++; return send(res, 200, {token: server_state.token, media_key: server_state.key}); }
      return send(res, 200, {source_bytes: 1, report_bytes: 1, disk_free_bytes: 1});
    }
    const evidence = p.match(/^\/api\/jobs\/(\d+)\/review\/(evidence|frame|video)$/);
    if (evidence) {
      const q = queueFor(Number(evidence[1])), item = url.searchParams.get('item'), x = q && q.items.concat(q.advisory_items).find(i => i.id === item);
      if (evidence[2] === 'evidence') {
        counters.evidence++;
        return setTimeout(() => x ? send(res, 200, server_state.evidence.get(item) || Mock.reviewEvidence(x)) : send(res, 404, {error: 'Không có mục này'}), server_state.evidenceDelay);
      }
      const key = url.searchParams.get('k');
      if (evidence[2] === 'frame') {
        counters.frames++;
        if (key !== server_state.key) { counters.frame403++; return send(res, 403, {error: 'Khóa media không hợp lệ'}); }
        counters.mediaActive++; counters.mediaMax = Math.max(counters.mediaMax, counters.mediaActive);
        return setTimeout(() => { counters.mediaActive--; send(res, 200, fs.readFileSync(path.join(ROOT, 'assets', 'poster-blue.svg')), 'image/svg+xml'); }, 220);
      }
      counters.video++;
      if (key !== server_state.key) { counters.video403++; return send(res, 403, {error: 'Khóa media không hợp lệ'}); }
      if (server_state.videoStatus) return send(res, server_state.videoStatus, {error: 'video'});
      if (!VIDEO) return send(res, 404, {error: 'no clip'});
      const size = fs.statSync(CLIP).size, range = /bytes=(\d*)-(\d*)/.exec(req.headers.range || '');
      // As review_evidence.stream_file: Accept-Ranges, Content-Range and Cache-Control: no-store.
      if (!range) { res.writeHead(200, {'Content-Type': 'video/webm', 'Content-Length': size, 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store'}); return fs.createReadStream(CLIP).pipe(res); }
      const a = range[1] === '' ? 0 : Number(range[1]), b = range[2] === '' ? size - 1 : Math.min(size - 1, Number(range[2]));
      if (a >= size) { res.writeHead(416, {'Content-Range': 'bytes */' + size, 'Cache-Control': 'no-store'}); return res.end(); }
      res.writeHead(206, {'Content-Type': 'video/webm', 'Content-Length': b - a + 1, 'Content-Range': `bytes ${a}-${b}/${size}`, 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store'});
      return fs.createReadStream(CLIP, {start: a, end: b}).pipe(res);
    }
    const media = p.match(/^\/media\/(.+)$/);
    if (media) {
      const file = decodeURIComponent(media[1]).match(/^demo\/(poster-[a-z]+\.svg)$/);
      counters.media++; counters.mediaActive++; counters.mediaMax = Math.max(counters.mediaMax, counters.mediaActive);
      return setTimeout(() => { counters.mediaActive--; file ? send(res, 200, fs.readFileSync(path.join(ROOT, 'assets', file[1])), 'image/svg+xml') : send(res, 404, {error: 'x'}); }, 220);
    }
    const classic = p.match(/^\/classic\/(\d+)$/);
    if (classic) {
      const id = Number(classic[1]);
      if (!server_state.classic.has(id)) server_state.classic.set(id, classicPage(server_state.token, id));
      const html = server_state.classic.get(id);
      return html ? send(res, 200, html, 'text/html; charset=utf-8') : send(res, 503, {error: 'Python with BiliFlow is required for the classic page'});
    }
    if (/^\/review\/\d+$/.test(p)) return send(res, 200, '<!doctype html><title>review</title><h1>Trang duyệt cũ</h1>', 'text/html');
    return send(res, 404, {error: 'Không tìm thấy'});
  });
  return {server, Mock, jobs, queues, queueFor, counters, server_state, posts, requests, VIDEO, TOKEN, applyWrite};
}

module.exports = {create, classicPage};
