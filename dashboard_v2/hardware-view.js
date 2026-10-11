/* Cài đặt → Phần cứng on live.html (docs/HARDWARE_PROFILE.md 6.2), PC only: the hardware profile of this root,
 * "Đo lại", "Chỉ dùng CPU" and "Chế độ nhanh trên card chưa qua cổng". app.js asks it for the panel HTML and hands
 * it the clicks of data-action="hw". Every request goes through the live store (adapter.js: the GET and the POSTs
 * only once /api/phone-mode said this page is the PC, one POST per button, 403 → one new session); the server checks
 * every write again. Every server string goes through esc(). The demo page (index.html) never loads this file.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BFHardware = api;
})(typeof window === 'undefined' ? this : window, function () {
  'use strict';
  /* data-op → contract operation (contracts.js endpoints). */
  const OPS = {remeasure: 'hardwareRemeasure', 'cpu-only': 'hardwareCpuOnly', 'untested-fast': 'hardwareUntestedFast'};
  /* GET status → [badge text (short: a badge never wraps), tone, what it means when the server sends no message]. */
  const STATUS = {
    ready: ['Sẵn sàng', '', 'Máy khớp một cấu hình đã qua cổng.'],
    ready_last_good: ['Bản tốt gần nhất', 'amber', 'File hồ sơ chính thiếu hoặc hỏng; đang dùng bản tốt gần nhất. Nên Đo lại.'],
    ready_ungated: ['Root thử', 'amber', 'Root thử: cấu hình này chưa qua cổng.'],
    ready_untested: ['Card chưa qua cổng', 'amber', 'Card này chưa qua cổng: job mới chạy chế độ chuẩn (OCR batch 1, không fp16, TF32 tắt), có nhãn "chưa qua cổng".'],
    needs_measurement: ['Chưa đo', 'blue', 'Chưa có hồ sơ phần cứng. Tạm dừng hàng đợi, đợi stage đang chạy xong rồi bấm Đo lại.'],
    invalid: ['Hồ sơ hỏng', 'red', 'Hồ sơ phần cứng hỏng và không có bản tốt gần nhất dùng được. Đo lại.'],
    cuda_unavailable: ['Không dùng được CUDA', 'red', 'Không dùng được CUDA. Khởi động lại hoặc cài lại driver NVIDIA rồi Đo lại; hoặc bật Chỉ dùng CPU.'],
    stale_gpu: ['Card đã đổi', 'amber', 'Card khác với lúc đo. Đo lại.'],
    stale_torch: ['Torch đã đổi', 'amber', 'Bản torch khác với lúc đo. Đo lại.'],
    no_gated_row: ['Không có cấu hình', 'red', 'Không có cấu hình đã qua cổng cho máy này. Bật Chỉ dùng CPU nếu máy không có card NVIDIA, hoặc Đo lại.'],
    multi_gpu_unsupported: ['Nhiều GPU', 'red', 'Máy có hơn một GPU NVIDIA: chưa hỗ trợ nhiều GPU.'],
    override_invalid: ['Công tắc hỏng', 'red', 'File công tắc phần cứng hỏng. Bật hoặc tắt lại một công tắc để ghi lại file.'],
    cpu_row_not_ready: ['CPU chưa sẵn sàng', 'amber', 'Đường CPU chưa chạy được ở bản này.'],
    arch_unsupported: ['Torch thiếu mã card', 'red', 'Bản torch đã cài không có mã cho đời card này. Cần bản torch mới hơn.'],
    driver_too_old: ['Driver quá cũ', 'red', 'Driver NVIDIA thấp hơn mức tối thiểu của bản torch. Cập nhật driver rồi Đo lại.'],
    vram_too_small: ['VRAM quá nhỏ', 'red', 'VRAM nhỏ hơn 6144 MiB, mức nhỏ nhất từng chạy hết pipeline.'],
  };
  const FAMILY_ORDER = ['fast', 'standard_ocr8', 'standard'];
  const FAMILY_LABELS = {fast: 'Tăng tốc xử lý', standard_ocr8: 'Chuẩn, OCR 8', standard: 'Chuẩn'};
  const ROW_STATUS = {gated: 'đã qua cổng', ungated: 'chưa qua cổng (root thử)', untested: 'chưa qua cổng', retired: 'đã ngừng dùng'};
  const TRIAL = {ok: 'đạt', failed: 'lỗi', not_run: 'chưa thử', listed: 'có trong danh sách'};
  /* progress.json steps (hardware_measure.STEPS, with the detect steps of hardware_detect.DETECT_STEPS). */
  const STEP_TEXT = {starting: 'Đang khởi động', idle: 'Kiểm hàng đợi rảnh', locks: 'Lấy khóa GPU và khóa xuất', nvml: 'Đọc NVML',
    nvidia_smi: 'Đối chiếu nvidia-smi', adapters: 'Đọc danh sách card', system: 'Đọc CPU, RAM, nguồn điện', torch_probe: 'Thử torch và CUDA',
    ffmpeg: 'Đọc FFmpeg', clip: 'Tạo clip thử', encoders: 'Thử mã hóa', nvdec_codecs: 'Thử giải mã NVDEC', logo_routing: 'Thử chuỗi routing logo',
    write: 'Ghi hồ sơ'};
  const EXIT_TEXT = {0: 'Đo xong.', 10: 'Đo xong nhưng lần đo thất bại; xem lý do trong khung Phần cứng.',
    64: 'Sai tham số hoặc thư mục không được phép; chưa đo.', 70: 'Lỗi nội bộ khi đo; chưa ghi gì.',
    75: 'Bận, hàng đợi chưa rảnh hoặc đã hủy; chưa ghi gì.', 124: 'Quá giờ; chưa ghi gì.'};
  const UNTESTED_LABEL = 'Chưa qua cổng trên đời card này';
  /* hardware_profile.WARNING_TEXTS["untested_fast_other_card"] (a test pins it): shown once, by the server or here. */
  const OTHER_CARD = 'Công tắc đang bật cho card khác; bật lại cho card này.';
  /* The switch is on but this state never grants it (Chỉ dùng CPU, a refusal, no profile); the status label follows. */
  const IDLE_SWITCH = 'Công tắc đang bật nhưng không có tác dụng ở trạng thái hiện tại';
  /* profile.present is false: no file at all (main_state and last_good_state "missing", or NEEDS_MEASUREMENT), or a
     file that exists but could not be read or is broken (the "Hồ sơ hỏng" badge). */
  const NEVER_MEASURED = 'Chưa đo lần nào.';
  const PROFILE_UNREADABLE = 'File hồ sơ phần cứng hỏng hoặc không đọc được; lần Đo lại tới sẽ ghi hồ sơ mới.';
  /* A task of an earlier Control Center (previous_run) that came without its sentence: never claims it still runs. */
  const PREVIOUS_UNKNOWN = 'Không rõ lần đo từ lần chạy trước còn chạy hay không.';
  const NOT_APPLIED = 'Chưa áp dụng cho job – có hiệu lực sau Đợt 2';
  const TAKES_EFFECT = 'có hiệu lực cho job mới từ Đợt 2';
  /* The three risks of docs/HARDWARE_PROFILE.md 5.6, word for word hardware_table.UNTESTED_FAST_RISKS (a test pins it). */
  const UNTESTED_FAST_RISKS = [
    "Trên Ampere trở lên, tích chập 'fp32' mặc định chạy TF32 (BiliFlow tắt TF32 trong chế độ này), " +
      'và kernel fp16 cũng khác Turing, nên kết quả có thể lệch so với RTX 2060.',
    'Một ứng viên bị bỏ sót trên đường chưa qua cổng sẽ không bao giờ hiện trong danh sách duyệt, nên không ai thấy.',
    'Số worker và số làn chưa tăng (để sau B1 và A1b), nên chưa dùng hết sức card.',
  ];

  const filled = value => value !== null && value !== undefined && value !== '';
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  function when(value) {
    const date = new Date(value);
    return filled(value) && !Number.isNaN(date.getTime()) ? date.toLocaleString('vi-VN') : String(value ?? '');
  }
  function trialWord(value) {
    const detail = value && typeof value === 'object' ? value : null, trial = detail ? detail.trial : value;
    return (TRIAL[trial] || String(trial ?? '—')) + (detail && filled(detail.reason) ? ' (' + detail.reason + ')' : '');
  }
  function trials(map) {
    return map && typeof map === 'object' ? Object.entries(map).map(([name, value]) => name + ' ' + trialWord(value)).join(' · ') : '';
  }
  function texts(list) {
    return Array.isArray(list) ? list.map(item => item && typeof item === 'object' ? item.text || item.code : item).filter(filled) : [];
  }
  function gpuName(gpu) {
    if (!gpu || typeof gpu !== 'object') return '';
    return String(gpu.name || 'card') + (filled(gpu.compute_capability) ? ' (' + gpu.compute_capability + ')' : '');
  }
  function doneText(task) {
    if (filled(task.message)) return String(task.message);
    return EXIT_TEXT[task.exit] || 'Lỗi không rõ (mã ' + (number(task.exit) ?? '?') + ').';
  }
  /* A run left by an earlier Control Center ends without an exit code the page can know (outcome previous_done):
     its sentence is shown plainly, never as an error. */
  function taskFailed(task) {
    return task.outcome !== 'previous_done' && task.exit !== 0;
  }
  function familyText(option) {
    if (!option || typeof option !== 'object') return '—';
    if (option.refusal) return 'Không cấp: ' + ((STATUS[option.refusal] || [option.refusal])[0]);
    if (!option.family) return 'Không cấp';
    const parts = [(FAMILY_LABELS[option.family] || option.family) + (option.capped ? ' (bị hạ)' : '')];
    if (option.logo_decode) parts.push('giải mã logo ' + (option.logo_decode === 'nvdec' ? 'NVDEC' : option.logo_decode === 'cpu' ? 'CPU' : option.logo_decode));
    if (option.tf32 === 'off') parts.push('TF32 tắt');
    return parts.join(' · ');
  }
  function power(value) {
    if (!value || typeof value !== 'object') return 'Không rõ';
    const ac = value.ac_online === true ? 'Cắm điện' : value.ac_online === false ? 'Chạy pin' : 'Không rõ nguồn điện';
    return ac + (value.battery_present === true ? ' · có pin' : value.battery_present === false ? ' · không có pin' : '');
  }
  /* profile.failure: {code, step, text (the user's message), message (the sanitized error)}. */
  function failure(value) {
    if (!filled(value)) return '';
    if (typeof value !== 'object') return String(value);
    const parts = [value.text, value.message].filter(filled).map(String);
    return parts.length ? parts.join(' · ') : String(value.code || '');
  }

  function create(o) {
    const esc = o.esc;
    const snap = () => o.store.snapshot();
    let watching = null; // the last watch(on), so a repeated route() sends nothing new
    let follow = null; // {done}: a measurement started or seen running on this page; its end gets one toast

    const row = (label, text) => '<div class="key-value"><span>' + esc(label) + '</span><span>' + esc(text) + '</span></div>';
    const rowHtml = (label, html) => '<div class="key-value"><span>' + esc(label) + '</span><span>' + html + '</span></div>';
    const group = (title, body) => body ? '<div class="detail-section"><h3>' + esc(title) + '</h3>' + body + '</div>' : '';
    const bullets = (list, cls) => list.length ? '<ul class="' + cls + '" style="margin:6px 0;padding-left:18px">' + list.map(t => '<li>' + esc(t) + '</li>').join('') + '</ul>' : '';
    function section(body) {
      return '<section class="panel hardware-panel" id="hw-panel" style="margin-bottom:18px;overflow-wrap:anywhere" aria-label="Phần cứng">' +
        '<h2>Phần cứng và cấu hình xử lý</h2>' + body + '</section>';
    }

    function statusPart(hw) {
      const [label, tone, meaning] = STATUS[hw.status] || [hw.status, 'grey', ''];
      let html = rowHtml('Trạng thái', '<span class="badge' + (tone ? ' ' + tone : '') + ' hw-status">' + esc(label) + '</span>');
      const message = filled(hw.message) ? hw.message : meaning;
      if (message) html += '<p class="muted">' + esc(message) + '</p>';
      if (hw.applies_to_jobs !== true) html += '<p class="pc-only-note hw-not-applied">' + esc(NOT_APPLIED) + '</p>';
      if (hw.load_error) html += '<p class="notice">Lần đọc gần nhất lỗi; đang hiện trạng thái trước đó: ' + esc(hw.load_error) + '</p>';
      const warnings = texts(hw.warnings);
      if (warnings.length) html += '<div class="notice hw-warnings" role="status"><strong>Cảnh báo</strong>' + bullets(warnings, 'hw-warning-list') + '</div>';
      return html;
    }
    function taskPart(task) {
      if (!task || typeof task !== 'object') return '';
      /* A run left by an earlier Control Center, or one the server could not tell is still running (previous_run):
         the server's own sentence as it is, never the panel's "Đang đo". */
      if (task.running && task.previous_run === true) {
        return '<div class="ts-task hw-task" role="status">' + row('Đo phần cứng', filled(task.message) ? task.message : PREVIOUS_UNKNOWN) + '</div>';
      }
      if (task.running) {
        const index = number(task.index), total = number(task.total);
        const pct = index !== null && total ? Math.min(100, Math.max(0, Math.round(index * 100 / total))) : null;
        const step = filled(task.message) ? task.message : STEP_TEXT[task.step] || task.step || 'Đang đo';
        return '<div class="ts-task hw-task" role="status">' + row('Đang đo', step + (pct !== null ? ' (bước ' + index + '/' + total + ')' : '')) +
          (pct !== null ? '<div class="meter"><i style="width:' + pct + '%"></i></div>' : '') + '</div>';
      }
      return '<p class="' + (taskFailed(task) ? 'notice' : 'muted') + ' hw-task">Lần đo gần nhất trên trang: ' + esc(doneText(task)) +
        (filled(task.finished_at) ? ' · ' + esc(when(task.finished_at)) : '') + '</p>';
    }
    function fastShown(hw) {
      const fast = hw.untested_fast || {};
      return hw.status === 'ready_untested' || fast.value === true;
    }
    /* "Đo lại" follows can_remeasure as the server sends it, locked with its text (doc 6.2), also while a measurement
       runs or when the server could not tell whether one does; the panel's own words only when it sends no text. */
    function remeasureReason(hw, s) {
      const can = hw.can_remeasure || {};
      if (can.ok !== true) return filled(can.text) ? String(can.text) : 'Chưa đo lại được lúc này.';
      return s.offline ? 'Mất kết nối Control Center.' : '';
    }
    const warnedOtherCard = hw => Array.isArray(hw.warnings) && hw.warnings.some(w => w && w.code === 'untested_fast_other_card');
    /* The switch is bound to another card: the server warns (READY_UNTESTED), or the bound card is not the profile's.
       applies is also false for the same card in states that never grant it (Chỉ dùng CPU, a refusal, no profile). */
    function otherCard(hw) {
      const fast = hw.untested_fast || {}, gpu = fast.gpu, d = hw.detection;
      if (fast.value !== true || fast.applies !== false) return false;
      if (warnedOtherCard(hw)) return true;
      if (!gpu || typeof gpu !== 'object' || !d || typeof d !== 'object') return false;
      return String(gpu.name ?? '') !== String(d.gpu ?? '') || String(gpu.compute_capability ?? '') !== String(d.compute_capability ?? '');
    }
    /* reason: why the button is off (also its title); flag: data-enabled, the switch value the click asks for. */
    function button(op, label, cls, reason, flag) {
      return '<button class="' + cls + '" data-action="hw" data-op="' + op + '"' + (flag === undefined ? '' : ' data-enabled="' + (flag ? '1' : '0') + '"') +
        (reason ? ' disabled title="' + esc(reason) + '"' : '') + '>' + esc(label) + '</button>';
    }
    function switchPart(hw, s) {
      const cpu = hw.cpu_only || {}, fast = hw.untested_fast || {}, offline = s.offline ? 'Mất kết nối Control Center.' : '';
      let html = row('Chỉ dùng CPU', cpu.valid === false ? 'File công tắc hỏng' : cpu.value === true ? 'Đang bật (' + TAKES_EFFECT + ')' : 'Đang tắt');
      const on = fast.value === true, other = otherCard(hw);
      if (fastShown(hw)) {
        html += row('Chế độ nhanh trên card chưa qua cổng', on ? 'Đang bật' + (fast.gpu ? ' cho ' + gpuName(fast.gpu) : '') : 'Đang tắt (mặc định)');
        if (other && !warnedOtherCard(hw)) html += '<p class="notice hw-other-card">' + esc(OTHER_CARD) + '</p>';
        else if (on && fast.applies === false && !other) html += '<p class="muted hw-fast-idle">' + esc(IDLE_SWITCH + ' (' + (STATUS[hw.status] || [hw.status])[0] + ').') + '</p>';
      }
      const reason = remeasureReason(hw, s), buttons = [button('remeasure', 'Đo lại', 'primary', reason)];
      buttons.push(button('cpu-only', cpu.value === true ? 'Tắt Chỉ dùng CPU' : 'Chỉ dùng CPU', 'secondary', offline, cpu.value !== true));
      if (fastShown(hw)) {
        const here = hw.status === 'ready_untested' ? offline : 'Chỉ bật được khi card hiện tại chưa qua cổng.';
        if (!on) buttons.push(button('untested-fast', 'Bật chế độ nhanh', 'secondary', here, true));
        if (other) buttons.push(button('untested-fast', 'Bật lại cho card này', 'secondary', here, true));
        if (on) buttons.push(button('untested-fast', 'Tắt chế độ nhanh', 'secondary', offline, false));
      }
      html += '<div class="action-grid">' + buttons.join('') + '</div>';
      if (reason) html += '<p class="muted hw-remeasure-reason">' + esc(reason) + '</p>';
      return html;
    }
    function configPart(hw) {
      const r = hw.row && typeof hw.row === 'object' ? hw.row : null, families = hw.families && typeof hw.families === 'object' ? hw.families : {};
      let body = r && r.status === 'untested' ? '<p><span class="badge amber hw-untested" style="white-space:normal">' + esc(UNTESTED_LABEL) + '</span></p>' : '';
      if (r && filled(r.id)) body += row('Dòng', r.id + ' · ' + (ROW_STATUS[r.status] || r.status || '—') + (filled(r.table_version) ? ' · bảng ' + r.table_version : ''));
      FAMILY_ORDER.filter(name => families[name]).forEach(name => {
        const notes = texts(families[name].warnings);
        body += rowHtml(FAMILY_LABELS[name], esc(familyText(families[name])) + (notes.length ? '<br><small>' + esc(notes.join(' · ')) + '</small>' : ''));
      });
      const reasons = texts(hw.reasons);
      if (reasons.length) body += '<p class="muted" style="margin-bottom:0">Lý do:</p>' + bullets(reasons, 'muted hw-reasons');
      return group('Cấu hình xử lý', body);
    }
    function machinePart(hw) {
      const d = hw.detection;
      if (!d || typeof d !== 'object') return '';
      const vram = number(d.vram_mib), ram = number(d.ram_gib);
      const rows = [['GPU', filled(d.gpu) ? d.gpu : 'Không thấy GPU NVIDIA'], ['Đời card', d.compute_capability], ['VRAM', vram !== null ? vram + ' MiB' : null],
        ['Driver NVIDIA', d.driver], ['Torch', d.torch], ['CPU', d.cpu],
        ['RAM', ram !== null ? ram.toLocaleString('vi-VN', {maximumFractionDigits: 1}) + ' GiB' : null]];
      return group('Máy', rows.filter(([, value]) => filled(value)).map(([label, value]) => row(label, value)).join(''));
    }
    function ffmpegPart(hw) {
      const f = hw.detection && hw.detection.ffmpeg;
      if (!f || typeof f !== 'object') return '';
      let body = f.ok === false ? '<p class="notice">Lúc đo không dùng được FFmpeg: giải mã routing bằng CPU, xuất bằng x264.</p>' : '';
      if (filled(f.version)) body += row('Phiên bản', f.version);
      if (trials(f.encoders)) body += row('Mã hóa thử', trials(f.encoders));
      if (trials(f.nvdec_codecs)) body += row('Giải mã NVDEC', trials(f.nvdec_codecs));
      const chain = f.nvdec && typeof f.nvdec === 'object' ? f.nvdec : null;
      if (chain) body += row('Chuỗi routing logo', 'NVDEC ' + trialWord(chain.trial) + (chain.framemd5_equal_cpu === true ? ', khung hình giống giải mã CPU'
        : chain.framemd5_equal_cpu === false ? ', khung hình khác giải mã CPU' : ''));
      return group('Thử FFmpeg', body);
    }
    function profilePart(hw) {
      const p = hw.profile;
      if (!p || typeof p !== 'object') return '';
      if (p.present !== true) {
        const states = [p.main_state, p.last_good_state];
        // "Chưa đo lần nào." only when there is truly no file: both states "missing", or NEEDS_MEASUREMENT sent without them.
        const never = states.every(state => state === 'missing') || (!states.some(filled) && hw.status === 'needs_measurement');
        // A file that exists but is broken or unreadable: the same story as the "Hồ sơ hỏng" badge, never "never measured".
        const broken = !never && states.some(state => filled(state) && state !== 'missing' && state !== 'unknown');
        // Neither (the server's fallback answer, or states it could not read): its status message says what went wrong.
        if (!never && !broken) return '';
        return group('Lần đo gần nhất', '<p class="muted hw-profile-none">' + esc(never ? NEVER_MEASURED : PROFILE_UNREADABLE) + '</p>');
      }
      const result = p.result === 'ok' ? 'Thành công' : p.result === 'failed' ? 'Thất bại' : p.result;
      const why = failure(p.failure);
      return group('Lần đo gần nhất', row('Lúc', when(p.written_at) + (p.source === 'last_good' ? ' (bản tốt gần nhất)' : '')) +
        row('Kết quả', String(result ?? '—') + (why ? ': ' + why : '')) + row('Nguồn điện lúc đo', power(p.power)));
    }

    /* PC only: '' on the phone (its listener refuses the route) and while /api/phone-mode has not said which. */
    function panel(state) {
      const s = state || {};
      if (s.remote || s.device === 'phone') return '';
      if (s.device !== 'pc') return s.device_error ? section('<p class="muted">Chưa xác định được trang này mở trên PC nên chưa đọc hồ sơ phần cứng.</p>') : '';
      const hw = s.hardware;
      if (!hw) return section('<p class="muted">Đang đọc hồ sơ phần cứng…</p>');
      if (hw.unavailable) return section('<p class="notice">Control Center đang chạy chưa có hồ sơ phần cứng. Tắt bằng Stop-BiliFlow.cmd rồi mở lại.</p>');
      if (typeof hw.status !== 'string') return section('<p class="notice">Không đọc được hồ sơ phần cứng' + (hw.load_error ? ': ' + esc(hw.load_error) : '') + '</p>');
      return section(statusPart(hw) + taskPart(hw.task) + switchPart(hw, s) + configPart(hw) + machinePart(hw) + ffmpegPart(hw) + profilePart(hw));
    }

    function confirmRemeasure() {
      return ['Đo lại phần cứng', '<p>Đo lại nhận diện card, driver và torch, rồi thử mã hóa và giải mã NVDEC trên một clip tổng hợp. Việc này <strong>dùng GPU vài phút</strong>.</p>' +
        '<p>Hàng đợi phải đang tạm dừng, và stage đang chạy phải xong; BiliFlow không tự tạm dừng hàng đợi. Đừng tiếp tục hàng đợi cho tới khi đo xong.</p>' +
        '<p class="muted">' + esc(NOT_APPLIED) + '.</p>', 'Đo lại', 'Đã bắt đầu đo lại phần cứng; khung Phần cứng hiện tiến độ.'];
    }
    function confirmCpuOnly(enabled) {
      if (!enabled) return ['Tắt Chỉ dùng CPU', '<p>Job mới lại dùng GPU theo hồ sơ phần cứng. Công tắc ' + TAKES_EFFECT + ', chưa áp dụng trước đó; job đã xếp giữ thiết bị đã đóng băng.</p>',
        'Tắt Chỉ dùng CPU', 'Đã tắt Chỉ dùng CPU.'];
      return ['Chỉ dùng CPU', '<p>Job mới chạy bằng CPU thay cho GPU:</p><ol class="help-steps"><li>Chậm hơn nhiều.</li>' +
        '<li>Tắt luật C1 (luật máu anime chỉ hiệu chỉnh cho report fp16).</li><li>Đường CPU chưa qua cổng (E4).</li>' +
        '<li>Công tắc <strong>' + TAKES_EFFECT + '</strong>, chưa áp dụng trước đó.</li>' +
        '<li>Job đã xếp giữ thiết bị đã đóng băng; muốn chuyển thì Chạy lại.</li></ol>', 'Bật Chỉ dùng CPU', 'Đã bật Chỉ dùng CPU (' + TAKES_EFFECT + ').'];
    }
    function confirmUntestedFast(hw, enabled) {
      if (!enabled) return ['Tắt chế độ nhanh trên card chưa qua cổng', '<p>Job mới trên card chưa qua cổng chạy chế độ chuẩn: chỉ họ Chuẩn (OCR batch 1, không fp16), TF32 tắt; ' +
        'yêu cầu "Tăng tốc xử lý" hay OCR 8 bị hạ về Chuẩn. Công tắc ' + TAKES_EFFECT + '.</p>', 'Tắt chế độ nhanh', 'Đã tắt chế độ nhanh trên card chưa qua cổng.'];
      const d = hw.detection || {}, card = gpuName({name: d.gpu, compute_capability: d.compute_capability}) || 'card trong hồ sơ';
      return ['Chế độ nhanh trên card chưa qua cổng', '<p>Bật cho <strong>' + esc(card) + '</strong>: job mới có đủ ba họ cấu hình đã qua cổng trên Turing (gồm fp16 ở các stage đã duyệt), TF32 tắt. ' +
        'Đổi card thì phải bật lại.</p><p>Rủi ro:</p><ol class="help-steps">' + UNTESTED_FAST_RISKS.map(risk => '<li>' + esc(risk) + '</li>').join('') + '</ol>' +
        '<p>Công tắc <strong>' + TAKES_EFFECT + '</strong>, chưa áp dụng trước đó.</p>', 'Bật chế độ nhanh', 'Đã bật chế độ nhanh cho card này (' + TAKES_EFFECT + ').'];
    }
    const doneKey = hw => { const task = hw && hw.task; return task && typeof task === 'object' && !task.running ? JSON.stringify(task) : null; };
    /* The clicks of data-action="hw": every change asks first; the dialog shows the server's refusal (409, 400). */
    function onAction(el) {
      const s = snap(), hw = s.hardware || {}, op = el && el.dataset ? el.dataset.op : '';
      if (s.device !== 'pc' || s.remote || s.offline || !OPS[op] || typeof hw.status !== 'string') return;
      const enabled = el.dataset.enabled === '1';
      if (op === 'remeasure' && remeasureReason(hw, s)) { o.toast(remeasureReason(hw, s), true); return; }
      if (op === 'untested-fast' && enabled && hw.status !== 'ready_untested') return;
      const [title, body, label, done] = op === 'remeasure' ? confirmRemeasure() : op === 'cpu-only' ? confirmCpuOnly(enabled) : confirmUntestedFast(hw, enabled);
      o.showModal(title, body, async () => {
        const before = doneKey(snap().hardware);
        await o.store.hardwareAction(OPS[op], op === 'remeasure' ? undefined : enabled);
        o.toast(done);
        if (op === 'remeasure') { follow = {done: before}; onSnapshot(); }
        return true;
      }, label);
    }
    /* Settings open on the PC: the panel follows every poll; closed: only while a measurement runs (adapter.js). */
    function watch(on) {
      const next = !!on;
      if (next === watching) return;
      watching = next;
      o.store.watchHardware(next);
    }
    function onSnapshot() {
      const hw = snap().hardware, task = hw && hw.task;
      if (!task || typeof task !== 'object') return;
      if (task.running) { if (!follow) follow = {done: null}; return; }
      if (!follow || doneKey(hw) === follow.done) return;
      follow = null;
      o.toast('Đo phần cứng: ' + doneText(task), taskFailed(task));
    }
    return {panel, onAction, watch, onSnapshot};
  }

  return {create, OPS, STATUS, UNTESTED_FAST_RISKS, UNTESTED_LABEL, OTHER_CARD, IDLE_SWITCH, NEVER_MEASURED, PROFILE_UNREADABLE, PREVIOUS_UNKNOWN,
    NOT_APPLIED};
});
