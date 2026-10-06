'use strict';
/* "Tải video" layout fake: live.html with every download state, in memory (no yt-dlp, no network, no
 * Control Center). Run: node dashboard_v2/download-fake-server.cjs [port] [--phone]
 * then open http://127.0.0.1:<port>/dashboard-v2/#downloads (light and dark, 375 / 390 / 1440 px).
 * --phone answers /api/phone-mode like the phone listener ({remote: true}). Domains are .example only.
 * A link on a host starting with "chua-ho-tro." is added as a page yt-dlp cannot read (FAILED, UNSUPPORTED). */
const fs = require('fs');
const http = require('http');
const path = require('path');

const ROOT = __dirname;
const TOKEN = 'download-fake-token';
const TYPES = {'.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.html': 'text/html'};
const ASSETS = new Set(['styles.css', 'theme.css', 'review.css', 'contracts.js', 'adapter.js', 'download-demo.js', 'download-core.js',
  'download-view.js', 'download-live.js', 'review-core.js', 'review-detail.js', 'review-media.js', 'review-cards.js', 'review.js',
  'app.js', ...fs.readdirSync(path.join(ROOT, 'assets')).map(f => 'assets/' + f)]);
const UNSUPPORTED = url => 'Trang này chưa được hỗ trợ: yt-dlp không tìm thấy video nào đọc được trong trang. ' +
  '(ERROR: Unsupported URL: ' + url + ')';
const GB = 1073741824, MB = 1048576;
const LONG = 'Video mẫu có tên rất dài để thử xuống dòng trên điện thoại – tập 12 phần cuối (bản đầy đủ, phụ đề tiếng Việt)';

function seed() {
  const now = new Date().toISOString();
  const t = (id, state, extra = {}) => ({id, url: 'https://clips.example/watch/v' + id, state, attempt: 1,
    desired_name: null, original_title: 'Video mẫu ' + id, title: 'Video mẫu ' + id, duration_seconds: 1440, estimated_bytes: 900 * MB,
    downloaded_bytes: 0, total_bytes: null, speed: null, eta: null, error_code: null, error_message: null, chosen_entry: null,
    name_locked: false, created_at: now, state_since: now, finished_at: null, queued_at: now, output_name: null, entries: null,
    entry_count: 0, media: {extractor: 'generic', height: null, video_codec: null, audio_codec: null}, ...extra});
  return [
    t(1, 'DOWNLOADING', {downloaded_bytes: 700 * MB, total_bytes: 1.2 * GB, speed: 5.2 * MB, eta: 105}),
    t(2, 'DOWNLOADING', {url: 'https://media.example/show/index.m3u8', downloaded_bytes: 230 * MB, speed: 3.1 * MB, eta: 240,
      progress_basis: 'fragments', fragments_done: 120, fragments_total: 480, transfer_stage: 'downloading',
      media: {extractor: null, height: 1080, video_codec: 'h264', audio_codec: 'aac', provider: 'direct', transport: 'hls',
        source_label: 'Link HLS trực tiếp'}}),
    t(3, 'PROBING'),
    t(4, 'QUEUED', {title: LONG, original_title: LONG}),
    t(5, 'NEEDS_CHOICE', {url: 'https://movies.example/phim/tuyen-tap', entry_count: 3, entries: [
      {index: 1, title: 'Tập 1', duration_seconds: 1500, estimated_bytes: 700 * MB},
      {index: 2, title: LONG, duration_seconds: 1490, estimated_bytes: 690 * MB},
      {index: 3, title: 'Đoạn giới thiệu', duration_seconds: 95}]}),
    t(6, 'WAITING_SPACE', {error_message: 'Chờ chỗ trống: cần khoảng 7,2 GB, ổ còn 103 GB (giữ lại 100 GB).'}),
    t(7, 'VERIFYING', {downloaded_bytes: 820 * MB, total_bytes: 820 * MB}),
    t(8, 'COMPLETED', {downloaded_bytes: 1.1 * GB, total_bytes: 1.1 * GB, output_name: 'Video mẫu 8.mp4', name_locked: true,
      media: {extractor: 'generic', height: 1080, video_codec: 'h264', audio_codec: 'aac'}}),
    t(9, 'FAILED', {error_code: 'LOGIN_REQUIRED', error_message: 'Trang cần đăng nhập. BiliFlow không dùng cookie hay tài khoản.'}),
    t(10, 'STOPPED', {downloaded_bytes: 400 * MB, total_bytes: GB}),
    t(11, 'INTERRUPTED', {downloaded_bytes: 120 * MB, total_bytes: 900 * MB, error_message: 'Control Center tắt khi đang tải. Bấm Tiếp tục để tải nối.'}),
    t(12, 'CANCELLED'),
    t(13, 'EXPIRED', {error_message: 'Quá 7 ngày: đã dọn file tạm; thử lại sẽ tải từ đầu.'}),
    t(14, 'COMPLETED', {title: '<b>Không phải chữ đậm</b> ' + LONG, output_name: '_b_Không phải chữ đậm_b_.mp4', name_locked: true,
      media: {extractor: 'generic', height: 720, video_codec: 'vp9', audio_codec: 'opus'}}),
    t(15, 'PUBLISHING', {downloaded_bytes: 600 * MB, total_bytes: 600 * MB, name_locked: true}),
    t(16, 'CANCELLING'),
    t(17, 'FAILED', {url: 'https://phim.example/phim/tap-1', title: null, original_title: null, error_code: 'UNSUPPORTED',
      error_message: UNSUPPORTED('https://phim.example/phim/tap-1')}),
  ];
}

function create(options = {}) {
  let tasks = seed(), slots = 2, nextId = 18;
  const posts = [];
  const RUN = ['PROBING', 'WAITING_SPACE', 'DOWNLOADING', 'VERIFYING', 'PUBLISHING', 'CANCELLING'];
  const cleanable = () => tasks.filter(x => ['FAILED', 'STOPPED', 'INTERRUPTED'].includes(x.state));
  const snapshot = () => ({tasks, counts: tasks.reduce((c, x) => ({...c, [x.state]: (c[x.state] || 0) + 1}), {}),
    settings: {slots, max_slots: 3}, space: {free_bytes: 412 * GB, reserve_bytes: 100 * GB},
    temp: {tasks: cleanable().length, bytes: 520 * MB, ids: cleanable().map(x => x.id), total_temp_bytes: 1.4 * GB},
    running: tasks.filter(x => RUN.includes(x.state)).map(x => x.id), worker_error: null, worker_error_at: null});
  const storage = {computing: false, error: null, summary: {computed_at: new Date().toISOString(),
    drive: {total_bytes: 1863 * GB, free_bytes: 412 * GB, reserve_bytes: 100 * GB, state: 'OK'},
    folders: {input: 46 * GB, output: 31 * GB, reports: 2.4 * GB, cache: 18 * GB, temp: 1.4 * GB},
    cleanable: {jobs: 3, bytes: 9.6 * GB}, recycle_bin: {volume: 'E:', used_bytes: 3.2 * GB, items: 12, max_bytes: 186 * GB}}};

  function send(res, code, body, type) {
    res.writeHead(code, {'Content-Type': type || 'application/json; charset=utf-8', 'Cache-Control': 'no-store'});
    res.end(typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body));
  }
  const set = (task, state, extra = {}) => Object.assign(task, {state, state_since: new Date().toISOString()}, extra);
  const ACTIONS = {
    stop: [['QUEUED', 'PROBING', 'WAITING_SPACE', 'DOWNLOADING'], t => set(t, 'STOPPED', {speed: null, eta: null})],
    resume: [['STOPPED', 'INTERRUPTED'], t => set(t, 'QUEUED', {error_message: null})],
    cancel: [['QUEUED', 'PROBING', 'NEEDS_CHOICE', 'WAITING_SPACE', 'DOWNLOADING', 'VERIFYING', 'STOPPED', 'FAILED', 'INTERRUPTED', 'CANCELLING'],
      t => set(t, 'CANCELLED', {downloaded_bytes: 0})],
    retry: [['FAILED', 'INTERRUPTED', 'STOPPED', 'CANCELLED', 'EXPIRED'], t => set(t, 'QUEUED', {attempt: t.attempt + 1, downloaded_bytes: 0,
      total_bytes: null, error_code: null, error_message: null})],
  };

  function handlePost(p, body, res) {
    if (p === '/api/downloads') {
      const urls = Array.isArray(body.urls) ? body.urls : [];
      if (body.rights_confirmed !== true) return send(res, 400, {error: 'Cần xác nhận bạn có quyền tải và chỉnh sửa các video này.'});
      const errors = urls.map((u, i) => { // a few of the backend checks (download_links.py); any site is accepted
        let url;
        try { url = new URL(u); } catch (_) { return {line: i + 1, code: 'BAD_URL', message: 'Link không đúng dạng.'}; }
        if (!['http:', 'https:'].includes(url.protocol)) return {line: i + 1, code: 'BAD_SCHEME', message: 'Chỉ nhận link http:// hoặc https://.'};
        if (url.username || url.password) return {line: i + 1, code: 'USERINFO', message: 'Link không được chứa tên đăng nhập hay mật khẩu.'};
        if (url.port) return {line: i + 1, code: 'BAD_PORT', message: 'Link không được dùng cổng riêng.'};
        return null;
      }).filter(Boolean);
      if (!urls.length || errors.length) return send(res, 400, {error: 'Lô bị từ chối.', code: 'BATCH_REJECTED', errors});
      const added = urls.map(u => {
        const row = {...seed()[3], id: nextId++, url: u, title: null, original_title: null};
        return new URL(u).hostname.startsWith('chua-ho-tro.')
          ? {...row, state: 'FAILED', error_code: 'UNSUPPORTED', error_message: UNSUPPORTED(u)} : row;
      });
      tasks = [...tasks, ...added]; // the backend lists oldest first
      return send(res, 200, {tasks: added});
    }
    if (p === '/api/downloads/settings') {
      if (![1, 2, 3].includes(body.slots)) return send(res, 400, {error: 'Số luồng từ 1 đến 3.'});
      slots = body.slots;
      return send(res, 200, {slots});
    }
    if (p === '/api/downloads/cleanup-temp') {
      const ended = cleanable().filter(x => !Array.isArray(body.ids) || body.ids.includes(x.id)); // as the backend: only the listed ids
      ended.forEach(x => set(x, 'EXPIRED', {downloaded_bytes: 0}));
      return send(res, 200, {tasks: ended.length, freed_bytes: 520 * MB});
    }
    const m = p.match(/^\/api\/downloads\/(\d+)\/(rename|choose|stop|resume|cancel|retry|remove)$/);
    const t = m && tasks.find(x => x.id === Number(m[1]));
    if (!m) return send(res, 404, {error: 'Không tìm thấy'});
    if (!t) return send(res, 404, {error: 'Không thấy lượt tải.'});
    if (m[2] === 'remove') {
      if (!['COMPLETED', 'CANCELLED', 'FAILED', 'STOPPED', 'INTERRUPTED', 'EXPIRED'].includes(t.state)) return send(res, 409, {error: 'Lượt đang chạy.'});
      tasks = tasks.filter(x => x !== t);
      return send(res, 200, {id: t.id, removed: true, freed_bytes: 0});
    }
    if (m[2] === 'rename') {
      if (t.name_locked || ['PUBLISHING', 'COMPLETED'].includes(t.state)) return send(res, 409, {error: 'Không đổi tên được nữa.'});
      Object.assign(t, {desired_name: String(body.name || '').trim(), title: String(body.name || '').trim()});
      return send(res, 200, {task: t});
    }
    if (m[2] === 'choose') {
      if (t.state !== 'NEEDS_CHOICE') return send(res, 409, {error: 'Lượt không chờ chọn video.'});
      set(t, 'QUEUED', {chosen_entry: body.entry_index, entries: null});
      return send(res, 200, {task: t});
    }
    const [allowed, apply] = ACTIONS[m[2]];
    if (!allowed.includes(t.state)) return send(res, 409, {error: 'Trạng thái đã đổi: ' + t.state + '.'});
    apply(t);
    return send(res, 200, {task: t});
  }

  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1'), p = url.pathname;
    if (req.method === 'POST') {
      let raw = '';
      req.setEncoding('utf8');
      req.on('data', chunk => { raw += chunk; });
      req.on('end', () => {
        let body = {};
        try { body = JSON.parse(raw || '{}'); } catch (_) { return send(res, 400, {error: 'JSON'}); }
        posts.push({path: p, body});
        if (req.headers['x-biliflow-token'] !== TOKEN) return send(res, 403, {error: 'Phiên không hợp lệ'});
        setTimeout(() => handlePost(p, body, res), 250);
      });
      return;
    }
    if (p === '/dashboard-v2/') return send(res, 200, fs.readFileSync(path.join(ROOT, 'live.html')), 'text/html; charset=utf-8');
    const asset = p.match(/^\/dashboard-v2\/(.+)$/);
    if (asset && ASSETS.has(asset[1])) return send(res, 200, fs.readFileSync(path.join(ROOT, asset[1])), TYPES[path.extname(asset[1])]);
    if (p === '/api/session') return send(res, 200, {token: TOKEN});
    if (p === '/api/status') return send(res, 200, {version: 'fake', jobs: [], queue: {length: 0, paused: false}, active: null, resources: {disk: {}}});
    if (p === '/api/phone-mode') return send(res, 200, options.phone ? {remote: true} : {remote: false, enabled: false});
    if (p === '/api/ai') return send(res, 200, {ready: false, config: {enabled: false}, message: 'tắt'});
    if (p === '/api/downloads') return send(res, 200, snapshot());
    if (p === '/api/storage-summary') return send(res, 200, storage);
    const detail = p.match(/^\/api\/downloads\/(\d+)$/), t = detail && tasks.find(x => x.id === Number(detail[1]));
    if (detail) return t ? send(res, 200, {task: t, events: [{kind: 'QUEUED', message: 'Đã xếp hàng.', level: 'INFO'},
      {kind: t.state, message: 'Trạng thái: ' + t.state, level: 'INFO'}], log: ['[download] Destination: <BiliFlow>\\temp\\downloads\\' + t.id + '\\a.mp4',
      '[download]  42.0% of ~1.20GiB at 5.20MiB/s ETA 01:45']}) : send(res, 404, {error: 'Không thấy lượt tải.'});
    return send(res, 404, {error: 'Không tìm thấy'});
  });
  return {server, posts, tasks: () => tasks};
}

if (require.main === module) {
  const port = Number(process.argv.find(a => /^\d+$/.test(a))) || 8798;
  const fake = create({phone: process.argv.includes('--phone')});
  fake.server.listen(port, '127.0.0.1', () => process.stdout.write('download fake: http://127.0.0.1:' + port + '/dashboard-v2/#downloads\n'));
}
module.exports = {create};
