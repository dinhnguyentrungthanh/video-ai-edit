/* Frontend contract inventory. No network or production imports. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.BFContracts = api;
})(typeof window === 'undefined' ? this : window, function () {
  'use strict';
  const detectors = {advertising:'Quảng cáo / logo',adult:'18+',gore:'Máu me',violence:'Bạo lực'};
  const tabs = [['all','Tất cả'],['waiting','Chờ xử lý'],['scan_queue','Chờ quét'],['scanning','Đang quét'],['review','Cần duyệt / đã duyệt'],['export','Chờ / đang xuất'],['completed','Hoàn tất']];
  const labels = {DISCOVERED:'Chưa thiết lập',NEEDS_METADATA:'Chờ thiết lập',QUEUED:'Đang chờ',PREFLIGHT:'Chuẩn bị',SCANNING_SAFETY:'Quét an toàn',SCANNING_TEXT:'Quét chữ',SCANNING_LOGO:'Quét logo',LOCALIZING_REGIONS:'Khoanh vùng',BUILDING_REVIEW:'Tạo cảnh duyệt',AI_AUDITING:'Kiểm tra AI',WAITING_REVIEW:'Chờ duyệt',READY_TO_EXPORT:'Sẵn sàng xuất',RENDERING:'Đang xuất',VERIFYING:'Kiểm tra bản xuất',PAUSED:'Tạm dừng',FAILED:'Có lỗi',INTERRUPTED_RECOVERABLE:'Có thể tiếp tục',CANCELLED:'Đã hủy',COMPLETED:'Đã xuất',SKIPPED:'Đã bỏ qua'};
  const scanning = ['PREFLIGHT','SCANNING_SAFETY','SCANNING_TEXT','SCANNING_LOGO','LOCALIZING_REGIONS','BUILDING_REVIEW','AI_AUDITING'];
  const pausable = ['QUEUED','PREFLIGHT','SCANNING_SAFETY','SCANNING_TEXT','SCANNING_LOGO','LOCALIZING_REGIONS','BUILDING_REVIEW','RENDERING'];
  const rerunnable = ['WAITING_REVIEW','READY_TO_EXPORT','COMPLETED','SKIPPED','CANCELLED','FAILED','PAUSED','INTERRUPTED_RECOVERABLE'];
  const endpoints = {
    session:['GET','/api/session'],status:['GET','/api/status'],jobs:['GET','/api/jobs'],detail:['GET','/api/jobs/{id}'],health:['GET','/healthz'],
    review:['GET','/review/{id}'],queue:['GET','/api/jobs/{id}/review/queue'],reviewSession:['GET','/api/jobs/{id}/review/session'],resources:['GET','/api/jobs/{id}/review/resources'],reviewExport:['GET','/api/jobs/{id}/review/export'],
    evidence:['GET','/api/jobs/{id}/review/evidence'],frame:['GET','/api/jobs/{id}/review/frame'],video:['GET','/api/jobs/{id}/review/video'],media:['GET','/media/{path}'],
    scheduler:['POST','/api/scheduler'],shutdown:['POST','/api/shutdown'],
    start:['POST','/api/jobs/{id}/start'],resume:['POST','/api/jobs/{id}/resume'],pause:['POST','/api/jobs/{id}/pause'],stopAfter:['POST','/api/jobs/{id}/stop-after-stage'],cancel:['POST','/api/jobs/{id}/cancel'],retry:['POST','/api/jobs/{id}/retry'],rerun:['POST','/api/jobs/{id}/rerun'],skip:['POST','/api/jobs/{id}/skip'],unskip:['POST','/api/jobs/{id}/unskip'],hide:['POST','/api/jobs/{id}/hide'],unhide:['POST','/api/jobs/{id}/unhide'],audit:['POST','/api/jobs/{id}/ai-audit'],
    finalize:['POST','/api/jobs/{id}/review/finalize'],decision:['POST','/api/jobs/{id}/review/decision'],clear:['POST','/api/jobs/{id}/review/clear'],bulkKeep:['POST','/api/jobs/{id}/review/bulk-keep'],bulkAccept:['POST','/api/jobs/{id}/review/bulk-accept'],
    ai:['GET','/api/ai'],aiConfig:['POST','/api/ai/config'],aiCheck:['POST','/api/ai/check'],aiLogin:['POST','/api/ai/login'],
    cleanupPreview:['GET','/api/source-cleanup/preview'],cleanup:['POST','/api/source-cleanup'],deletePreview:['GET','/api/job-delete/preview'],delete:['POST','/api/job-delete'],archivePreview:['GET','/api/source-archive/preview'],archive:['POST','/api/source-archive'],restore:['POST','/api/source-archive/restore'],recheck:['POST','/api/source-recycle-check'],
    logoPage:['GET','/logo-memory'],logos:['GET','/api/logo-memory'],logoFrame:['GET','/api/logo-memory/frame'],logoClass:['POST','/api/logo-memory/class'],logoDelete:['POST','/api/logo-memory/delete'],
    phoneStatus:['GET','/api/phone-mode'],phoneMode:['POST','/api/phone-mode'],phoneExtend:['POST','/api/phone-mode/extend'],
    /* Cài đặt → Tailscale (docs/TAILSCALE_PLAN.md): PC only; one background task at a time. */
    tailscale:['GET','/api/tailscale'],tailscaleInstall:['POST','/api/tailscale/install'],tailscaleService:['POST','/api/tailscale/start-service'],
    tailscaleFirewall:['POST','/api/tailscale/firewall'],tailscaleLogin:['POST','/api/tailscale/login'],tailscaleUp:['POST','/api/tailscale/up'],
    tailscaleDown:['POST','/api/tailscale/down'],tailscaleLogout:['POST','/api/tailscale/logout'],tailscaleRemoteOn:['POST','/api/tailscale/remote-on'],
    /* "Tải video" (docs/VIDEO_DOWNLOAD_PLAN.md §5): {id} is the download task id, not a job id. */
    downloads:['GET','/api/downloads'],downloadTask:['GET','/api/downloads/{id}'],storageSummary:['GET','/api/storage-summary'],
    downloadAdd:['POST','/api/downloads'],downloadSettings:['POST','/api/downloads/settings'],downloadCleanup:['POST','/api/downloads/cleanup-temp'],
    downloadRename:['POST','/api/downloads/{id}/rename'],downloadChoose:['POST','/api/downloads/{id}/choose'],downloadStop:['POST','/api/downloads/{id}/stop'],
    downloadResume:['POST','/api/downloads/{id}/resume'],downloadCancel:['POST','/api/downloads/{id}/cancel'],downloadRetry:['POST','/api/downloads/{id}/retry'],
    downloadRemove:['POST','/api/downloads/{id}/remove'],
    /* Source accounts (docs/SOURCE_ACCOUNTS_PLAN.md 9.14): {id} is the series page task or the group id; {source} is a
     * configured source id. The episode and group routes work on the phone like the other download actions. */
    downloadEpisodes:['GET','/api/downloads/{id}/episodes'],downloadEpisodesDraft:['POST','/api/downloads/{id}/episodes/draft'],
    downloadEpisodesConfirm:['POST','/api/downloads/{id}/episodes/confirm'],downloadGroup:['GET','/api/downloads/groups/{id}'],
    downloadGroupStop:['POST','/api/downloads/groups/{id}/stop'],downloadGroupResume:['POST','/api/downloads/groups/{id}/resume'],
    downloadGroupCancel:['POST','/api/downloads/groups/{id}/cancel'],downloadGroupRetry:['POST','/api/downloads/groups/{id}/retry'],
    downloadGroupRemove:['POST','/api/downloads/groups/{id}/remove'],
    /* PC only (127.0.0.1 + token; the phone listener answers 403 pc_only): the page never offers them on the phone. */
    accountLogin:['POST','/api/download-accounts/{source}/login'],accountCancelLogin:['POST','/api/download-accounts/{source}/cancel-login'],
    accountDisconnect:['POST','/api/download-accounts/{source}/disconnect']
  };
  /* A configured source id (download_account_api.ACCOUNT_ACTION): the only value a {source} placeholder takes. */
  const SOURCE_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;
  /* "Tài khoản nguồn phim": action → contract operation; PC only (phone_access.PC_ONLY_SOURCE_ACCOUNTS is the reason). */
  const accountOps = {login:'accountLogin','cancel-login':'accountCancelLogin',disconnect:'accountDisconnect'};
  const PC_ONLY_ACCOUNT_REASON = 'Chỉ làm trên PC: đăng nhập, hủy đăng nhập và ngắt kết nối tài khoản nguồn phim chỉ làm trên PC.';
  const cleaned = j => !!j && (!!j.source_cleaned || ['PENDING','RECYCLED'].includes(j.source_cleanup?.state));
  const archived = j => !!j && (!!j.source_archived || ['PENDING','ARCHIVED','RESTORING'].includes(j.source_archive?.state));
  const hidden = j => j.state === 'CANCELLED' && !!j.hidden_at;
  const locked = j => cleaned(j) || archived(j) || j.source_present === false;
  const inFlight = j => !!j.render_request || ['RENDERING','VERIFYING'].includes(j.state) || (j.state === 'QUEUED' && j.queue_kind === 'export');
  const eligible = (j,kind) => !!j && ['COMPLETED','SKIPPED'].includes(j.state) && j[kind]?.eligible === true && !locked(j) && !inFlight(j);
  /* Source line of the classic dashboard (sourceLineInfo/archiveLineInfo), copied verbatim
   * so V2 shows the same text: [text, tone] or null. tests/test_dashboard_v2_contract.py
   * compares it with the classic functions extracted from _dashboard_html(). */
  function formatStamp(value){if(!value)return '';const date=new Date(value);if(Number.isNaN(date.getTime()))return '';return new Intl.DateTimeFormat('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit',day:'2-digit',month:'2-digit',year:'numeric'}).format(date)}
  function formatBytes(n){const v=Number(n);if(n==null||n===''||!Number.isFinite(v)||v<0)return '';if(v<1073741824)return `${Math.round(v/1048576).toLocaleString('vi-VN')} MB`;return `${(v/1073741824).toLocaleString('vi-VN',{minimumFractionDigits:1,maximumFractionDigits:1})} GB`}
  function videoName(j){const parts=String(j.source_path||j.job_key||'Video').split(/[\\/]/);return parts[parts.length-1]||j.job_key||'Video'}
  function isArchived(j){return !!j&&(j.source_archived===true||['PENDING','ARCHIVED','RESTORING'].includes(j.source_archive?.state))}
  function recheckNote(c){if(!c)return '';if(c.verified_later_at){const s=formatStamp(c.verified_later_at);return ` · Đã thấy trong Thùng rác khi kiểm tra lại${s?` lúc ${s}`:''}.`}if(c.rechecked_at&&c.verified===false){const s=formatStamp(c.rechecked_at);return ` Lần kiểm tra lại gần nhất${s?` (${s})`:''} vẫn chưa thấy.`}return ''}
  function archiveRecheckNote(a){if(!a)return '';if(a.export_verified_later_at){const s=formatStamp(a.export_verified_later_at);return ` · Đã thấy bản xuất trong Thùng rác khi kiểm tra lại${s?` lúc ${s}`:''}.`}if(a.export_rechecked_at&&a.export_verified===false){const s=formatStamp(a.export_rechecked_at);return ` Lần kiểm tra lại gần nhất${s?` (${s})`:''} vẫn chưa thấy.`}return ''}
  function archiveLineInfo(j){const a=j.source_archive;if(!a)return null;if(a.state==='ARCHIVED'){const stamp=formatStamp(a.archived_at),exported=a.kind==='EXPORTED',unverified=exported&&a.export_verified===false;return[`Đã lưu trữ · video gốc ${a.file_name||videoName(j)} (${formatBytes(a.size_bytes)}) trong kho lưu trữ${exported?` · bản xuất ${a.output_name||''} đã vào Thùng rác`:''}${stamp?` lúc ${stamp}`:''}${unverified?' · Windows chưa xác nhận bản ghi của bản xuất trong Thùng rác; hãy kiểm tra Thùng rác.':''}${archiveRecheckNote(a)}${a.warning?` · ${a.warning}`:''}`,unverified||a.warning?'waiting':'complete']}if(a.state==='PENDING')return['Đang lưu trữ video gốc…','running'];if(a.state==='RESTORING')return['Đang đưa video gốc từ kho lưu trữ về input…','running'];if(a.state==='RESTORED'&&j.source_present!==false){const stamp=formatStamp(a.restored_at);return[`Đã đưa video gốc từ kho lưu trữ về input (SHA-256 khớp)${stamp?` lúc ${stamp}`:''}`,'complete']}if(a.state==='FAILED'&&j.source_present!==false)return[`Lần lưu trữ trước không thành công: ${a.error||''}`,'error'];return null}
  function sourceLineInfo(j){const c=j.source_cleanup,a=j.source_archive;if(a&&(isArchived(j)||!c||String(a.created_at||'')>String(c.created_at||''))){const line=archiveLineInfo(j);if(line)return line}if(c?.state==='RECYCLED'){const stamp=formatStamp(c.finished_at);return[`Đã dọn video gốc · ${formatBytes(c.size_bytes)}${stamp?` · lúc ${stamp}`:''} (đang ở Thùng rác)${c.verified===false?' · Windows chưa xác nhận bản ghi trong Thùng rác; hãy kiểm tra Thùng rác.':''}${recheckNote(c)}`,c.verified===false?'waiting':'complete']}if(c?.state==='PENDING')return['Đang dọn video gốc…','running'];if(c?.state==='RESTORED'){const stamp=formatStamp(c.restored_at);return[`Đã khôi phục video gốc (SHA-256 khớp)${stamp?` lúc ${stamp}`:''}`,'complete']}if(c?.state==='FAILED'&&j.source_present!==false)return[`Lần dọn trước không thành công: ${c.error||''}`,'error'];if(j.source_present===false)return['Không còn video gốc trong input','error'];if(['COMPLETED','SKIPPED'].includes(j.state)&&j.cleanup&&!j.cleanup.eligible&&j.cleanup.reason)return[`Chưa dọn được: ${j.cleanup.reason}`,'waiting'];if(['COMPLETED','SKIPPED'].includes(j.state)&&j.archive&&!j.archive.eligible&&j.archive.reason&&!isArchived(j))return[`Chưa lưu trữ được: ${j.archive.reason}`,'waiting'];return null}
  const sourceLine = sourceLineInfo;
  /* Same text as export_guards.SOURCE_MISSING_MESSAGE (checked by tests/test_dashboard_v2_contract.py). */
  const SOURCE_MISSING_MESSAGE = 'Video gốc không còn trong input; không thể xuất.';
  /* Phone mode (batch 2): these actions are refused by the phone listener (403 pc_only).
     "Xóa video gốc" and "Xóa video" work on the phone too (the user's choice, 2026-10-06). */
  const pcOnlyOps = ['archive','restore','recheck'];
  /* "Xóa video gốc" and "Xóa video" delete for good (2026-10-05): the POST must carry confirm_permanent:true. */
  const permanentOps = ['cleanup','delete'];
  /* A DELETED "Xóa video gốc" that left the export's manifest says so after this marker (source_cleanup.MANIFEST_WARNING). */
  const DELETE_NOTE = '. Lưu ý: ';
  /* "Dọn video mất gốc": every video the server marks as lost and deletable (status delete hint), never a golden one. */
  function lostIds(jobs){return (jobs||[]).filter(j=>!!j&&!!j.delete&&j.delete.kind==='LOST'&&j.delete.eligible===true&&!j.protected).map(j=>j.id).sort((a,b)=>a-b);}
  const PC_ONLY_REASON = 'Chỉ làm trên PC: lưu trữ, khôi phục bản xuất và kiểm tra lại Thùng rác không làm qua điện thoại.';
  /* H3: while the phone mode is on, the PC says so (the classic "/" page did until V2 replaced it on 2026-10-06).
   * Only on the PC (the phone listener answers {remote: true}), never with the code. */
  function phoneNotice(phone,{live,remote}={}){return live&&!remote&&!!phone&&phone.enabled===true?'Đang mở cho điện thoại'+(phone.network==='tailscale'?' qua Tailscale':'')+': '+(phone.url||''):'';}
  /* Cài đặt → Tailscale: action → contract operation; labels are tailscale_manager.ACTIONS (checked by the tests). */
  const tailscaleOps = {install:'tailscaleInstall','start-service':'tailscaleService',firewall:'tailscaleFirewall',login:'tailscaleLogin',
    up:'tailscaleUp',down:'tailscaleDown',logout:'tailscaleLogout','remote-on':'tailscaleRemoteOn'};
  const TAILSCALE_LABELS = {install:'Cài và cấu hình Tailscale','start-service':'Khởi động dịch vụ Tailscale',firewall:'Tạo rule tường lửa',
    login:'Đăng nhập Tailscale',up:'Kết nối Tailscale',down:'Ngắt Tailscale',logout:'Đăng xuất Tailscale','remote-on':'Mở cho điện thoại ngoài nhà'};
  /* The step the PC is at, from GET /api/tailscale: what the panel explains and which button comes first. */
  function tailscaleStep(ts){
    if(!ts)return 'loading';
    if(ts.unavailable)return 'unavailable';
    if(typeof ts.installed!=='boolean')return 'error'; // the first read failed (load_error)
    if(!ts.installed)return 'install';
    if(ts.service!=='running')return 'service';
    if(ts.backend==='NeedsLogin'||ts.backend==='NeedsMachineAuth')return 'login';
    return ts.backend==='Running'?'ready':'connect';
  }
  const TAILSCALE_STEP_ACTIONS = {install:['install'],service:['start-service'],login:['login'],connect:['up','logout'],ready:['remote-on','down','logout']};
  /* [[action, label, primary?]]: none while a task runs (one at a time); the firewall rule whenever it is missing or off. */
  function tailscaleActions(ts){
    if(!ts||ts.task&&ts.task.running)return [];
    const list=(TAILSCALE_STEP_ACTIONS[tailscaleStep(ts)]||[]).slice();
    if(ts.installed&&['missing','disabled'].includes(ts.firewall_rule))list.push('firewall');
    return list.map((a,i)=>[a,TAILSCALE_LABELS[a],i===0]);
  }
  /* Only a Tailscale sign-in page is ever offered as a link (the server checks it too). */
  const tailscaleLoginUrl = ts => !!ts&&typeof ts.auth_url==='string'&&/^https:\/\/login\.tailscale\.com\/[A-Za-z0-9/_-]+$/.test(ts.auth_url)?ts.auth_url:null;
  function reviewStats(j) {
    const r=j.review_summary||{},d=r.decisions||{};
    const total=Number(r.main_items)||0,resolved=['KEEP','BLUR','CUT'].reduce((n,k)=>n+(Number(d[k])||0),0);
    return {total,resolved,remaining:Math.max(0,total-resolved),needsMore:Number(d.NEEDS_MORE_CONTEXT)||0};
  }
  function tab(j) {
    const s = String(j && j.state || '');
    if (['COMPLETED','SKIPPED'].includes(s)) return 'completed';
    if (['RENDERING','VERIFYING'].includes(s)) return 'export';
    if (s === 'QUEUED') return j.queue_kind === 'export' ? 'export' : j.queue_kind === 'scan' ? 'scan_queue' : 'waiting';
    if (['WAITING_REVIEW','READY_TO_EXPORT'].includes(s)) return 'review';
    if (scanning.includes(s) || s.startsWith('SCANNING_')) return 'scanning';
    return 'waiting';
  }
  function phase(j) {
    if (hidden(j)) return 'Đã ẩn';
    if (j.state === 'CANCELLED') return 'Đã hủy';
    if (archived(j)) return 'Đã lưu trữ';
    if (j.state === 'SKIPPED') return 'Đã bỏ qua';
    if (j.state === 'READY_TO_EXPORT') return 'Đã duyệt xong';
    if (j.state === 'COMPLETED') return 'Đã xuất video';
    return ({waiting:'Cần xử lý',scan_queue:'Chờ quét',scanning:'Đang quét',review:'Cần duyệt cảnh',export:'Đang xuất video'})[tab(j)];
  }
  const overviewLabels={scan_active:'Đang quét cảnh',review_pending:'Chờ bạn duyệt',review_ready:'Sẵn sàng xuất',export_active:'Đang xuất video'};
  function overviewMatch(j,group) {
    if(hidden(j))return false;
    if(group==='scan_active')return tab(j)==='scanning';
    if(group==='export_active')return ['RENDERING','VERIFYING'].includes(j.state);
    if(group==='review_pending')return j.state==='WAITING_REVIEW';
    if(group==='review_ready')return j.state==='READY_TO_EXPORT';
    return false;
  }
  function operations(j, ctx) {
    ctx = ctx || {};
    const actions = [];
    function add(id,label,enabled,reason) {
      if (ctx.remote && pcOnlyOps.includes(id)) { enabled = false; reason = PC_ONLY_REASON; }
      actions.push({id,label,enabled:!!enabled,reason:enabled?'':reason||'Thao tác chưa sẵn sàng.'});
    }
    const sourceReason = archived(j) ? 'Video đang được lưu trữ. Khôi phục trước khi xử lý.' : cleaned(j) ? 'Khôi phục đúng video từ Thùng rác về input trước.' : 'Không tìm thấy video gốc.';
    const sourceOK = !locked(j);
    if (['DISCOVERED','NEEDS_METADATA'].includes(j.state)) add('start','Thiết lập & bắt đầu',sourceOK,sourceReason);
    if (['PAUSED','FAILED','INTERRUPTED_RECOVERABLE'].includes(j.state)) add('resume','Tiếp tục',sourceOK,sourceReason);
    if (pausable.includes(j.state)) { add('stopAfter','Dừng sau bước',true); add('pause','Dừng ngay',true); }
    if (j.active_queue_path) {
      add('review','Duyệt cảnh',true);
      add('audit','Visual AI Audit',ctx.aiReady && !['RUNNING','QUEUED'].includes(j.ai_audit?.state) && sourceOK && !inFlight(j), 'AI cần kết nối, có queue và không có bước xuất đang chờ/chạy.');
    }
    if (j.state === 'FAILED') add('retry','Thử lại bước lỗi',sourceOK,sourceReason);
    if (j.state === 'READY_TO_EXPORT') {
      add('finalize','Xuất video',sourceOK && !inFlight(j) && j.review_summary?.status === 'READY_FOR_EDIT_PLAN' && reviewStats(j).remaining===0,'Cần quyết định cuối cùng cho mọi cảnh, có video gốc và không có lệnh xuất đang chờ/chạy.');
      if (j.review_summary?.skip_eligible) add('skip','Bỏ qua (không xuất)',sourceOK && !inFlight(j),sourceReason);
    }
    /* R4-U2: "Xuất lại" sends finalize with the decisions of the last review (the backend reuses an export its manifest proves). */
    if (j.state === 'COMPLETED') add('reexport','Xuất lại',sourceOK && !inFlight(j),sourceOK?'Lệnh xuất đang chờ/chạy.':sourceReason);
    if (j.state === 'SKIPPED') add('unskip','Mở lại để xuất',sourceOK,sourceReason);
    if (!['COMPLETED','SKIPPED','CANCELLED'].includes(j.state)) add('cancel','Hủy xử lý',true);
    if (j.state === 'CANCELLED') add(hidden(j)?'unhide':'hide',hidden(j)?'Hiện lại':'Ẩn khỏi danh sách',true);
    if (rerunnable.includes(j.state)) add('rerun','Chạy lại kiểm tra',sourceOK && !inFlight(j),sourceOK?'Lệnh xuất đang chờ/chạy.':sourceReason);
    /* A golden-set video (j.protected, from /api/status) keeps every delete action off, with that reason. */
    if (eligible(j,'cleanup')) add('cleanup','Xóa video gốc',!ctx.fileBusy && !j.protected,j.protected||'Một thao tác với video gốc đang chạy.');
    if (j.delete) add('delete','Xóa video',j.delete.eligible===true && !ctx.fileBusy && !j.protected,j.protected||(j.delete.eligible===true?'Một thao tác với video gốc đang chạy.':j.delete.reason||'Chưa xóa được video này.'));
    if (eligible(j,'archive')) add('archive','Lưu trữ',!ctx.fileBusy,'Một thao tác với video gốc đang chạy.');
    if (archived(j)) add('restore','Khôi phục bản xuất',j.source_archive?.state === 'ARCHIVED' && !ctx.fileBusy,'Đang lưu trữ hoặc khôi phục video.');
    const c=j.source_cleanup,a=j.source_archive;
    if ((c?.state==='RECYCLED' && c.verified===false && Number.isInteger(c.id)) ||
        (a?.state==='ARCHIVED' && a.kind==='EXPORTED' && a.export_recycled && a.export_verified===false && Number.isInteger(a.id))) add('recheck','Kiểm tra lại Thùng rác',!ctx.fileBusy,'Một thao tác với video gốc đang chạy.');
    return actions;
  }
  function primary(j) {
    if (archived(j)) return 'restore';
    if (j.state === 'COMPLETED') return 'reexport';
    if (locked(j)) return 'review';
    if (['DISCOVERED','NEEDS_METADATA'].includes(j.state)) return 'start';
    if (['PAUSED','FAILED','INTERRUPTED_RECOVERABLE'].includes(j.state)) return 'resume';
    if (j.state === 'READY_TO_EXPORT') return 'finalize';
    if (j.active_queue_path) return 'review';
    return 'detail';
  }
  function validateScan(data,start) {
    if (!Array.isArray(data.detectors) || !data.detectors.length || data.detectors.some(x=>!detectors[x])) throw new Error('Chọn ít nhất một nhóm kiểm tra hợp lệ.');
    if (![1,8].includes(data.ocr_recognition_batch_size)) throw new Error('OCR chỉ nhận 1 hoặc 8.');
    if (typeof data.fast_scan!=='boolean') throw new Error('fast_scan phải là boolean.');
    if (start && (!['animation','live_action','mixed'].includes(data.content_style) || !['careful','fast'].includes(data.profile))) throw new Error('Chọn loại nội dung và chế độ quét hợp lệ.');
    return data;
  }
  /* Export dialog text and limits, as export_dialog.py (EXPORT_DIALOG_JS); the body stays {size_mode, max_output_gb?}. */
  const EXPORT_GATE_MESSAGE = 'Vẫn còn mục chưa có quyết định cuối cùng.';
  const EXPORT_SIZE_OPTIONS = [['default','Tối đa 3,5 GB (mặc định)'],['custom','Giới hạn tùy chỉnh'],['unlimited','Không giới hạn dung lượng']];
  const EXPORT_CUSTOM_GB = {attributes:'type="number" min="0.05" max="1000" step="0.1"',value:'3.5'};
  function exportDescription(selection) {
    if (selection.size_mode==='unlimited') return 'không giới hạn dung lượng';
    if (selection.size_mode==='default') return 'tối đa 3,5 GB';
    return `tối đa ${Number(selection.max_output_gb).toLocaleString('vi-VN')} GB`;
  }
  function exportConfirmText(selection) { return `Khóa các lựa chọn hiện tại và bắt đầu xuất video hoàn chỉnh (${exportDescription(selection)})?`; }
  /* Same text as source_cleanup.REASON_OUTPUT_MOVED (checked by tests/test_dashboard_v2_contract.py). */
  const OUTPUT_MOVED_REASON = 'Không thấy bản xuất trong thư mục output (đã bị dời hoặc đổi tên?)';
  /* R4-U2: what the cleanup hint of a COMPLETED video says about its export. present: the export of this review is in
   * output and its manifest proves it, so finalize reuses it (no render); otherwise finalize renders unless the
   * backend still finds a proven export. */
  function reexportState(j) {
    const c = j && j.cleanup;
    if (c && c.kind === 'EXPORTED' && c.eligible === true && c.output_name) {
      const name = String(c.output_name);
      return {present:true,moved:false,name,manifest:name+'.manifest.json',bytes:c.output_bytes,exportedAt:c.exported_at||'',reason:''};
    }
    const reason = String(c && c.reason || '');
    return {present:false,moved:reason===OUTPUT_MOVED_REASON,name:'',manifest:'',bytes:null,exportedAt:'',reason};
  }
  function exportPolicyChoice(policy) {
    const value=policy||{}, mode=EXPORT_SIZE_OPTIONS.some(o => o[0]===value.mode)?value.mode:'default', gb=Number(value.maximum_output_gb);
    return {mode,gb:mode==='custom'&&gb>0?gb:Number(EXPORT_CUSTOM_GB.value)};
  }
  /* The resources line of the export dialog (classic renderExport; S4: "Ổ đĩa còn trống", not "Ổ E còn trống"). */
  function resourceItems(r) {
    if (!r) return [];
    const size = n => n>1073741824 ? `${(n/1073741824).toFixed(1)} GB` : `${(n/1048576).toFixed(1)} MB`, range = r.estimated_preview_megabytes_range||[0,0];
    return [['Video nguồn',size(r.source_bytes)],['Ảnh và report',size(r.report_bytes)],['Ổ đĩa còn trống',size(r.disk_free_bytes)],['Preview dự kiến',`${r.estimated_preview_seconds} giây · khoảng ${range[0]}–${range[1]} MB`]];
  }
  function exportSelection(mode,gb) {
    if (mode==='default' || mode==='unlimited') return {size_mode:mode};
    const n=Number(gb);
    if (mode!=='custom' || !Number.isFinite(n) || n<0.05 || n>1000) throw new Error('Giới hạn tùy chỉnh phải từ 0,05 đến 1.000 GB.');
    return {size_mode:'custom',max_output_gb:n};
  }
  /* query: optional {name: value} appended as an encoded query string (review GETs: item, t, k). */
  function request(id,job,body,query) {
    const ep = endpoints[id];
    if (!ep) throw new Error('Thao tác không có trong hợp đồng.');
    if (ep[1].includes('{id}') && (!Number.isInteger(job?.id) || job.id<=0)) throw new Error('Thiếu job id hợp lệ.');
    if (ep[1].includes('{source}') && !(typeof job?.source === 'string' && SOURCE_ID.test(job.source))) throw new Error('Thiếu mã nguồn hợp lệ.');
    let path = ep[1].replace('{id}',job?.id).replace('{source}',job?.source);
    const pairs = Object.entries(query||{}).filter(([,v]) => v!==undefined && v!==null).map(([k,v]) => encodeURIComponent(k)+'='+encodeURIComponent(String(v)));
    if (pairs.length) path += '?'+pairs.join('&');
    return {operation:id,method:ep[0],path,body:body||{}};
  }
  return {accountOps,PC_ONLY_ACCOUNT_REASON,SOURCE_ID,pcOnlyOps,permanentOps,DELETE_NOTE,lostIds,PC_ONLY_REASON,phoneNotice,tailscaleOps,TAILSCALE_LABELS,tailscaleStep,tailscaleActions,tailscaleLoginUrl,SOURCE_MISSING_MESSAGE,sourceLine,formatStamp,formatBytes,detectors,tabs,labels,scanning,pausable,rerunnable,endpoints,cleaned,archived,hidden,locked,inFlight,eligible,reviewStats,tab,phase,overviewLabels,overviewMatch,operations,primary,validateScan,exportSelection,EXPORT_GATE_MESSAGE,EXPORT_SIZE_OPTIONS,EXPORT_CUSTOM_GB,exportDescription,exportConfirmText,exportPolicyChoice,OUTPUT_MOVED_REASON,reexportState,resourceItems,request};
});
